from __future__ import annotations

from copy import deepcopy

from app.technical_writing_agent import (
    _apply_validated_fresh_patches,
    _build_fresh_repair_context,
    _build_fresh_repair_task_card,
    _candidate_sha256,
    _group_fresh_repair_task_cards,
)
from tests import test_technical_writing_structured_patch as base


def _same_sentence_context() -> dict:
    return base._related_issue_context()


def _group_payload(context: dict, candidates: list[dict]) -> dict:
    card = context["repair_task_cards"][0]
    return {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": [
            {
                "repair_target_id": card["repair_target_id"],
                "section_id": card["section_id"],
                "candidates": candidates,
            }
        ],
    }


def _apply_group(
    context: dict,
    candidates: list[dict],
    *,
    validator=base.snapshot,
):
    initial = context["initial_candidate"]
    return _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=_group_payload(context, candidates),
        repair_context=context,
        fact_ledger=base.ledger(),
        initial_validation=validator(initial),
        validate_candidate=validator,
    )


def test_same_sentence_issues_form_one_repair_target_group():
    context = _same_sentence_context()
    assert len(context["repair_task_cards"]) == 1
    assert context["fresh_edit_scope"]["editable_issue_count"] == 1
    assert set(context["repair_task_cards"][0]["member_issue_codes"]) == {
        "schedule_frequency",
        "unconfirmed_bidder_commitment",
    }


def test_different_sentences_remain_separate_groups():
    context = base._related_issue_context(second_sentence=True)
    assert len(context["repair_task_cards"]) == 2
    assert len({card["repair_target_id"] for card in context["repair_task_cards"]}) == 2


def test_span_issue_is_grouped_by_containing_sentence():
    item = {
        "section_id": base.SECTION_A,
        "section_title": "实施计划",
        "issue_code": "schedule_frequency",
        "offending_sentence": "### 进度控制",
        "offending_span": "每周",
    }
    card = _build_fresh_repair_task_card(
        item,
        section_content="### 进度控制\n\n项目经理每周组织进度评估。",
        section_permissions=base.permissions(),
    )
    assert card is not None
    assert card["target"]["offending_sentence"] == "项目经理每周组织进度评估。"


def test_group_preserves_all_member_issue_fingerprints():
    card = _same_sentence_context()["repair_task_cards"][0]
    assert len(card["member_issue_fingerprints"]) == 2
    assert card["issue_fingerprint"] in card["member_issue_fingerprints"]


def test_shared_operation_resolves_multiple_member_issues():
    context = _same_sentence_context()
    _, report = _apply_group(context, [base.candidate(context, base.SAFE_A)])
    assert report["applied_operation_count"] == 1
    assert report["unresolved_repair_task_count"] == 0
    assert {item["status"] for item in report["issue_results"]} == {
        "resolved_by_shared_operation",
    }


def test_resolved_sibling_issue_is_not_reported_as_overlap():
    context = _same_sentence_context()
    _, report = _apply_group(context, [base.candidate(context, base.SAFE_A)])
    sibling = report["issue_results"][1]
    assert sibling["candidate_attempts"] == []
    assert report["operation_overlap_avoided_count"] == 1


def test_remaining_sibling_issue_is_reported_after_shared_operation():
    context = _same_sentence_context()

    def validator(value: dict) -> dict:
        result = base.snapshot(value)
        if base.BAD_A not in value["sections"][0]["content"]:
            result["raw_error_count"] = 1
            result["blocking_error_categories"] = [
                ("unconfirmed_bidder_commitment", "unconfirmed_bidder_commitment")
            ]
        return result

    _, report = _apply_group(
        context, [base.candidate(context, base.SAFE_A)], validator=validator
    )
    assert report["applied_operation_count"] == 1
    assert any(
        item["status"] == "remaining_after_shared_operation"
        for item in report["issue_results"]
    )
    assert report["unresolved_repair_task_count"] == 1


def _multi_issue_card() -> dict:
    context = _same_sentence_context()
    return context["repair_task_cards"][0]


def test_multi_span_sentence_disallows_replace_span():
    card = deepcopy(_multi_issue_card())
    card["offending_spans"] = ["每周", "提交进度报告"]
    grouped = _group_fresh_repair_task_cards([card])[0]
    assert "replace_span" not in grouped["allowed_operation_types"]


def test_multi_issue_sentence_disallows_replace_span():
    assert "replace_span" not in _multi_issue_card()["allowed_operation_types"]


def test_single_isolated_span_can_use_replace_span():
    card = base.repair_context()["repair_task_cards"][0]
    assert "replace_span" in card["allowed_operation_types"]


def test_narrow_operation_is_rejected_task_locally():
    context = _same_sentence_context()
    narrow = base.candidate(
        context,
        "定期",
        candidate_id="C1",
        operation_type="replace_span",
    )
    _, report = _apply_group(context, [narrow])
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_operation_scope_too_narrow" in reasons


def test_conservative_candidate_is_tried_after_narrow_operation_rejected():
    context = _same_sentence_context()
    candidates = [
        base.candidate(context, "定期", candidate_id="C1", operation_type="replace_span"),
        base.candidate(context, base.SAFE_A, candidate_id="C2"),
    ]
    _, report = _apply_group(context, candidates)
    attempts = report["issue_results"][0]["candidate_attempts"]
    assert [item["status"] for item in attempts] == ["rejected", "applied"]


def test_fact_issue_cannot_target_markdown_heading():
    card = _build_fresh_repair_task_card(
        {
            "section_id": base.SECTION_A,
            "section_title": "与现有系统的集成",
            "issue_code": "unconfirmed_product_capability",
            "offending_sentence": "### 与现有系统的集成",
            "offending_span": "",
        },
        section_content="### 与现有系统的集成",
        section_permissions=base.permissions(),
    )
    assert card is None


def test_fact_target_prefers_violation_sentence():
    sentence = "本系统支持未经确认的自动切换能力。"
    card = _build_fresh_repair_task_card(
        {
            "section_id": base.SECTION_A,
            "section_title": "技术方案",
            "issue_code": "unconfirmed_product_capability",
            "offending_sentence": sentence,
            "offending_span": "自动切换",
        },
        section_content=f"### 技术方案\n\n{sentence}",
        section_permissions=base.permissions(),
    )
    assert card and card["target"]["offending_sentence"] == sentence


def test_fact_target_can_resolve_sentence_from_span():
    sentence = "本系统支持未经确认的自动切换能力。"
    card = _build_fresh_repair_task_card(
        {
            "section_id": base.SECTION_A,
            "section_title": "技术方案",
            "issue_code": "unconfirmed_product_capability",
            "offending_sentence": "### 技术方案",
            "offending_span": "自动切换",
        },
        section_content=f"### 技术方案\n\n{sentence}",
        section_permissions=base.permissions(),
    )
    assert card and card["target"]["offending_sentence"] == sentence


def test_unresolved_fact_target_is_not_auto_repaired():
    context = base.repair_context()
    value = context["auto_repair_items"][0]
    value["offending_sentence"] = "### 不存在的标题"
    value["offending_span"] = "不存在的能力"
    assert _build_fresh_repair_task_card(
        value,
        section_content=context["initial_candidate"]["sections"][0]["content"],
        section_permissions=base.permissions(),
    ) is None


def _quality_context(*, trailing: str) -> dict:
    sentence = "项目实施期间每周向采购人提交书面进度报告，并在报告中说明阶段工作、偏差情况和处理安排。"
    initial = base.draft(sentence + trailing)
    message = f"fact_boundary:实施计划:schedule_frequency:{sentence}"
    return _build_fresh_repair_context(
        validation={
            "errors": [message],
            "issues": [{
                "severity": "blocking", "category": "fact_boundary",
                "code": "schedule_frequency", "section_id": base.SECTION_A,
                "section_title": "实施计划", "message": message,
            }],
        },
        draft=initial,
        fact_ledger=base.ledger(),
        pending_confirmations=[],
        target=base.target(),
    )


LONG_PARAGRAPH = (
    "\n\n项目实施过程依据确认后的计划开展进度跟踪，对输入条件、执行状态和阶段偏差进行分析，"
    "相关处理以合同约定和采购人确认结果为边界，确保过程记录能够支持后续复核。"
    "\n\n实施过程中同步核对工作条件和接口状态，对发现的问题进行分类、跟踪和闭环，"
    "并根据确认后的安排衔接后续工作，避免引入未经确认的履约范围；同时保留必要的过程依据，"
    "使各阶段输入、处理边界和复核结果能够形成连续且可追踪的实施记录。"
)


def test_safe_shorter_replacement_is_allowed():
    context = _quality_context(trailing=LONG_PARAGRAPH)
    replacement = "项目实施过程按照确认后的计划开展进度跟踪和阶段偏差分析。"
    merged, report = _apply_group(context, [base.candidate(context, replacement)])
    assert report["applied_operation_count"] == 1
    audit = report["issue_results"][0]["candidate_attempts"][0]["quality_audit"]
    assert audit["after_prose_characters"] < audit["before_prose_characters"]
    assert audit["quality_floor_crossed"] is False
    assert replacement in merged["sections"][0]["content"]


def test_safe_delete_is_allowed_when_quality_floor_remains():
    context = _quality_context(trailing=LONG_PARAGRAPH)
    delete = base.candidate(context, "", candidate_id="C3", operation_type="delete_sentence")
    _, report = _apply_group(context, [delete])
    assert report["applied_operation_count"] == 1


def test_shorter_replacement_does_not_equal_density_regression():
    context = _quality_context(trailing=LONG_PARAGRAPH)
    replacement = "项目实施过程按照确认后的计划开展进度跟踪和阶段偏差分析。"
    _, report = _apply_group(context, [base.candidate(context, replacement)])
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_prose_density_regressed" not in reasons


def test_new_insufficient_prose_is_rejected():
    context = _quality_context(trailing="\n\n项目输入和执行边界在实施前完成核对，并按确认结果推进后续工作。")
    replacement = "按确认后的计划跟踪进度。"
    _, report = _apply_group(context, [base.candidate(context, replacement)])
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_prose_density_regressed" in reasons


def test_quality_floor_crossing_is_rejected():
    test_new_insufficient_prose_is_rejected()


def test_coverage_regression_is_still_rejected():
    context = base.repair_context()

    def validator(value: dict) -> dict:
        result = base.snapshot(value)
        result["coverage"]["covered_response_point_count"] = (
            0 if base.SAFE_A in value["sections"][0]["content"] else 1
        )
        return result

    _, report = _apply_group(
        context, [base.candidate(context, base.SAFE_A)], validator=validator
    )
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_covered_response_point_count_decreased" in reasons


def test_placeholder_loss_is_still_rejected():
    base.test_patch_candidate_cannot_remove_registered_placeholder()


def test_missing_candidate_role_is_normalized_by_order():
    context = base.repair_context()
    first = base.candidate(context, base.SAFE_A)
    first.pop("candidate_role")
    _, report = _apply_group(context, [first])
    assert report["issue_results"][0]["candidate_attempts"][0]["candidate_role"] == "primary"


def test_candidate_role_mismatch_is_task_local():
    context = base.repair_context()
    first = base.candidate(context, base.SAFE_A)
    first["candidate_role"] = "conservative"
    _, report = _apply_group(context, [first])
    issue = report["issue_results"][0]
    assert issue["status"] == "applied"
    assert "fresh_patch_candidate_role_mismatch" in issue["diagnostic_codes"]


def test_candidate_statistics_use_normalized_roles():
    context = base.repair_context()
    values = [
        base.candidate(context, base.SAFE_A, candidate_id="C1"),
        base.candidate(
            context,
            "项目进度按照合同约定及确认后的实施计划进行控制。",
            candidate_id="C2",
        ),
    ]
    for value in values:
        value.pop("candidate_role")
    _, report = _apply_group(context, values)
    assert report["tasks_with_two_candidates"] == 1
    assert report["tasks_missing_conservative_candidate"] == 0


def test_target_group_report_contains_member_and_resolution_state():
    context = _same_sentence_context()
    _, report = _apply_group(context, [base.candidate(context, base.SAFE_A)])
    group = report["repair_target_groups"][0]
    assert group["repair_target_id"]
    assert len(group["member_issue_fingerprints"]) == 2
    assert group["status"] == "resolved"


def test_shared_operation_resolution_is_visible_at_issue_level():
    context = _same_sentence_context()
    _, report = _apply_group(context, [base.candidate(context, base.SAFE_A)])
    operation_id = report["applied_operations"][0]["operation_id"]
    assert all(
        item["applied_operation_id"] == operation_id
        for item in report["issue_results"]
    )
    assert report["issues_resolved_by_shared_operation_count"] == 2

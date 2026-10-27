from __future__ import annotations

from copy import deepcopy
import json

import pytest
from jsonschema import ValidationError

from app.llm_client import ToolModelResult, empty_usage

from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    _FreshPatchProtocolError,
    _apply_validated_fresh_patches,
    _apply_fresh_patch_operations,
    _build_fresh_repair_context,
    _candidate_sha256,
    _fresh_patch_protocol_payload,
    _fresh_patch_protocol_failure_report,
    _fresh_patch_report_template,
    _fresh_patch_candidates_equivalent,
    _fresh_patch_payload_from_tool_result,
    _fresh_patch_response_schema,
    _fresh_quality_error_details,
    _fresh_quality_regeneration_instruction,
    _fresh_structured_patch_messages,
    _forced_structured_patch_tool_choice,
    _submit_structured_patch_tool_definition,
    build_technical_writing_agent,
    _fresh_candidate_is_monotonic_improvement,
    _patch_replacement_fact_codes,
)


SECTION_A = "CH-PATCH-01"
SECTION_B = "CH-PATCH-02"
BAD_A = "每周向采购人提交进度报告。"
SAFE_A = "项目实施过程按照确认后的计划开展进度跟踪，并结合实际执行情况分析阶段偏差。"


def target() -> dict:
    return {
        "chapter_id": "CH-PATCH",
        "title": "局部返修测试",
        "children": [
            {"chapter_id": SECTION_A, "title": "实施计划"},
            {"chapter_id": SECTION_B, "title": "风险控制"},
        ],
    }


def draft(first: str = BAD_A, second: str = "风险识别后持续跟踪处置结果。") -> dict:
    return {
        "chapter_title": "局部返修测试",
        "sections": [
            {
                "heading": "实施计划",
                "block_type": "implementation_plan",
                "content": first,
                "covered_score_item_ids": [],
                "covered_requirement_ids": [],
                "placeholder_ids": [],
            },
            {
                "heading": "风险控制",
                "block_type": "risk_control",
                "content": second,
                "covered_score_item_ids": [],
                "covered_requirement_ids": [],
                "placeholder_ids": [],
            },
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }


def permissions() -> dict:
    return {
        "allowed_tender_facts": [],
        "effective_tender_facts": [],
        "allowed_tender_numeric_facts": [],
        "effective_tender_numeric_facts": [],
        "allowed_confirmed_facts": [],
        "allowed_confirmed_numeric_facts": [],
        "allowed_confirmed_personnel_facts": [],
        "allowed_confirmed_product_capabilities": [],
        "allowed_placeholders": [],
        "forbid_unlisted_numeric_commitments": True,
        "forbid_unlisted_personnel_qualifications": True,
        "forbid_unlisted_product_capabilities": True,
        "personnel_content_mode": "source_only",
    }


def ledger() -> list[dict]:
    return [
        {
            "section_id": SECTION_A,
            "section_title": "实施计划",
            "generation_permissions": permissions(),
        },
        {
            "section_id": SECTION_B,
            "section_title": "风险控制",
            "generation_permissions": permissions(),
        },
    ]


def validation_with_duplicate_issue() -> dict:
    message = f"fact_boundary:实施计划:schedule_frequency:{BAD_A}"
    return {
        "errors": [message, message],
        "issues": [
            {
                "severity": "blocking",
                "category": "fact_boundary",
                "code": "schedule_frequency",
                "section_id": SECTION_A,
                "section_title": "实施计划",
                "message": message,
            }
        ],
    }


def repair_context() -> dict:
    return _build_fresh_repair_context(
        validation=validation_with_duplicate_issue(),
        draft=draft(),
        fact_ledger=ledger(),
        pending_confirmations=[],
        target=target(),
    )


def snapshot(value: dict) -> dict:
    text = "\n".join(section["content"] for section in value["sections"])
    errors: list[tuple[str, str]] = []
    for marker, code in (
        ("每周", "schedule_frequency"),
        ("提交进度报告", "unconfirmed_bidder_commitment"),
        ("本系统支持在线扩容", "unconfirmed_product_capability"),
        ("设立质量保障组", "unconfirmed_staffing_assignment"),
    ):
        if marker in text:
            errors.append((code, code))
    insufficient = int(any(len(section["content"].strip()) < 8 for section in value["sections"]))
    return {
        "schema_valid": True,
        "raw_error_count": len(errors),
        "blocking_error_categories": errors,
        "coverage": {
            "headings_match": True,
            "missing_response_point_count": 0,
            "missing_score_item_count": 0,
            "covered_response_point_count": 0,
            "covered_score_item_count": 0,
        },
        "quality_comparison": {
            "prose_ratio": 1.0,
            "insufficient_section_count": insufficient,
            "list_dominant_section_count": 0,
            "inline_step_chain_count": 0,
            "markdown_table_error_count": 0,
            "quality_error_count": insufficient,
        },
    }


def candidate(
    context: dict,
    replacement: str,
    *,
    candidate_id: str = "C1",
    operation_type: str = "replace_sentence",
) -> dict:
    role = (
        "delete"
        if operation_type == "delete_sentence"
        else "primary"
        if candidate_id == "C1"
        else "conservative"
    )
    return {
        "candidate_id": candidate_id,
        "candidate_role": role,
        "operation_type": operation_type,
        "replacement_text": replacement,
    }


def payload(context: dict, candidates: list[dict]) -> dict:
    card = context["repair_task_cards"][0]
    return {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": [
            {
                "issue_fingerprint": card["issue_fingerprint"],
                "section_id": card["section_id"],
                "candidates": candidates,
            }
        ],
    }


def apply(context: dict, candidates: list[dict]):
    initial = context["initial_candidate"]
    return _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=payload(context, candidates),
        repair_context=context,
        fact_ledger=ledger(),
        initial_validation=snapshot(initial),
        validate_candidate=snapshot,
    )


def test_fresh_receives_compiled_effective_writing_context():
    context = repair_context()
    compiled = context["compiled_effective_writing_context"]
    assert compiled["chapter_id"] == "CH-PATCH"
    assert set(compiled["section_contexts"]) == {SECTION_A}


def test_fresh_context_uses_same_permissions_as_initial():
    context = repair_context()
    effective = context["compiled_effective_writing_context"]["section_contexts"][SECTION_A]
    assert effective["effective_writing_permissions"] == ledger()[0]["generation_permissions"]


def test_patch_precheck_uses_same_permissions_as_full_audit():
    codes = _patch_replacement_fact_codes(
        initial=draft(),
        section_index=0,
        section_id=SECTION_A,
        replacement_text="本系统支持在线扩容。",
        fact_ledger=ledger(),
    )
    assert "unconfirmed_product_capability" in codes


def test_unique_issue_creates_one_repair_task_card():
    context = repair_context()
    assert len(context["repair_task_cards"]) == 1
    assert context["fresh_edit_scope"]["raw_auto_repair_issue_count"] == 2
    assert context["fresh_edit_scope"]["unique_auto_repair_issue_count"] == 1


def test_task_card_contains_problem_preserve_and_forbidden_boundaries():
    card = repair_context()["repair_task_cards"][0]
    assert card["problem"]
    assert card["must_preserve"]
    assert "产品能力或性能结果" in card["must_not_add"]


def test_review_only_issue_creates_guard_not_repair_task():
    value = validation_with_duplicate_issue()
    message = "required project facts are unconfirmed but no pending confirmation was recorded"
    value["errors"].append(message)
    value["issues"].append(
        {
            "severity": "blocking",
            "category": "fact_boundary",
            "code": "required_project_fact_pending_confirmation_missing",
            "section_id": SECTION_A,
            "section_title": "实施计划",
            "message": message,
        }
    )
    context = _build_fresh_repair_context(
        validation=value,
        draft=draft(),
        fact_ledger=ledger(),
        pending_confirmations=[],
        target=target(),
    )
    assert all(card["issue_code"] != "required_project_fact_pending_confirmation_missing" for card in context["repair_task_cards"])
    assert any(guard["issue_code"] == "required_project_fact_pending_confirmation_missing" for guard in context["review_only_guards"])


def test_each_issue_accepts_up_to_three_ordered_candidates():
    context = repair_context()
    valid = payload(context, [candidate(context, SAFE_A, candidate_id=f"C{i}") for i in range(1, 4)])
    assert _fresh_patch_protocol_payload(valid) == valid
    invalid = payload(context, [candidate(context, SAFE_A, candidate_id=f"C{i}") for i in range(1, 5)])
    with pytest.raises(ValidationError):
        _fresh_patch_protocol_payload(invalid)


def test_supported_patch_operations_are_limited():
    assert set(_fresh_patch_response_schema()["properties"]["repair_results"]["items"]["properties"]["candidates"]["items"]["properties"]["operation_type"]["enum"]) == {
        "replace_span", "replace_sentence", "delete_sentence"
    }


def test_full_section_rewrite_operation_is_rejected():
    context = repair_context()
    value = payload(context, [candidate(context, SAFE_A)])
    value["repair_results"][0]["candidates"][0]["operation_type"] = "rewrite_section"
    with pytest.raises(ValidationError):
        _fresh_patch_protocol_payload(value)


def test_first_valid_candidate_is_selected():
    context = repair_context()
    merged, report = apply(context, [candidate(context, SAFE_A)])
    assert merged["sections"][0]["content"] == SAFE_A
    assert report["issue_results"][0]["selected_candidate_id"] == "C1"


def test_second_candidate_is_tried_after_first_rejected():
    context = repair_context()
    bad = candidate(context, "按照计划向采购人提交进度报告。", candidate_id="C1")
    safe = candidate(context, SAFE_A, candidate_id="C2")
    merged, report = apply(context, [bad, safe])
    assert merged["sections"][0]["content"] == SAFE_A
    assert report["issue_results"][0]["candidate_attempts"][0]["status"] == "rejected"
    assert report["issue_results"][0]["selected_candidate_id"] == "C2"


def test_delete_candidate_is_used_only_after_rewrite_candidates_fail():
    context = repair_context()
    initial = context["initial_candidate"]
    initial["sections"][0]["content"] = BAD_A + "\n\n实施过程保留其他完整说明并持续开展偏差分析。"
    context["initial_candidate_sha256"] = _candidate_sha256(initial)
    delete = candidate(context, "", candidate_id="C3", operation_type="delete_sentence")
    bad = candidate(context, "按照计划向采购人提交进度报告。", candidate_id="C1")
    merged, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=payload(context, [delete, bad]),
        repair_context=context,
        fact_ledger=ledger(),
        initial_validation=snapshot(initial),
        validate_candidate=snapshot,
    )
    assert BAD_A not in merged["sections"][0]["content"]
    assert report["issue_results"][0]["selected_candidate_id"] == "C3"


def test_all_candidates_failed_preserves_initial_issue():
    context = repair_context()
    merged, report = apply(context, [candidate(context, "本系统支持在线扩容。")])
    assert merged == context["initial_candidate"]
    assert report["unresolved_repair_task_count"] == 1


def test_patch_with_new_report_submission_is_rejected():
    context = repair_context()
    _, report = apply(context, [candidate(context, "按照计划向采购人提交进度报告。")])
    assert report["applied_operation_count"] == 0


def test_safe_internal_progress_tracking_patch_is_allowed():
    context = repair_context()
    merged, _ = apply(context, [candidate(context, SAFE_A)])
    assert "进度跟踪" in merged["sections"][0]["content"]


@pytest.mark.parametrize(
    "replacement",
    ["问题在2小时内完成。", "设立质量保障组负责实施。", "本系统支持在线扩容。"],
)
def test_patch_cannot_add_numeric_staffing_or_product_fact(replacement: str):
    context = repair_context()
    merged, report = apply(context, [candidate(context, replacement)])
    assert merged == context["initial_candidate"]
    assert report["rejected_candidate_count"] == 1


def test_neutral_staffing_deferment_is_allowed():
    context = repair_context()
    replacement = "具体岗位和人员将在后续确认后填入，当前仅说明技术实施、质量控制和现场协调的协作原则。"
    merged, report = apply(context, [candidate(context, replacement)])
    assert report["applied_operation_count"] == 1
    assert replacement in merged["sections"][0]["content"]


def test_unmodified_text_remains_byte_equivalent_to_initial():
    context = repair_context()
    merged, _ = apply(context, [candidate(context, SAFE_A)])
    assert merged["sections"][1] == context["initial_candidate"]["sections"][1]


def test_legacy_full_chapter_response_is_protocol_invalid():
    with pytest.raises(ValidationError):
        _fresh_patch_protocol_payload(draft())


def test_instruction_requests_patch_not_full_chapter():
    prompt = _fresh_quality_regeneration_instruction(
        schema=_fresh_patch_response_schema(), repair_context=repair_context()
    )
    assert "不得返回完整章节" in prompt
    assert "multi_candidate_structured_patch" in prompt


def test_patch_candidate_must_bind_existing_issue():
    context = repair_context()
    value = payload(context, [candidate(context, SAFE_A)])
    value["repair_results"][0]["issue_fingerprint"] = "unknown"
    merged, report = _apply_validated_fresh_patches(
        initial=context["initial_candidate"],
        patch_payload=value,
        repair_context=context,
        fact_ledger=ledger(),
        initial_validation=snapshot(context["initial_candidate"]),
        validate_candidate=snapshot,
    )
    assert merged == context["initial_candidate"]
    assert report["issue_results"][-1]["candidate_attempts"][0]["reason_codes"] == [
        "fresh_patch_invalid_issue_binding"
    ]


def test_patch_candidate_must_anchor_initial_text():
    context = repair_context()
    context["initial_candidate"]["sections"][0]["content"] = "原问题句已不存在。"
    context["initial_candidate_sha256"] = _candidate_sha256(context["initial_candidate"])
    merged, report = apply(context, [candidate(context, SAFE_A)])
    assert merged == context["initial_candidate"]
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_anchor_not_found" in reasons


def test_patch_candidate_cannot_create_quality_regression():
    context = repair_context()
    delete = candidate(
        context, "", candidate_id="C3", operation_type="delete_sentence"
    )
    merged, report = apply(context, [delete])
    assert merged == context["initial_candidate"]
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_insufficient_section_count_regressed" in reasons


def test_patch_candidate_cannot_remove_registered_placeholder():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    sentence = f"每周由{{{{{placeholder_id}}}}}向采购人提交进度报告。"
    initial = draft(sentence)
    initial["sections"][0]["placeholder_ids"] = [placeholder_id]
    initial["placeholders_used"] = [placeholder_id]
    local_ledger = ledger()
    local_ledger[0]["generation_permissions"]["allowed_placeholders"] = [
        {
            "placeholder_id": placeholder_id,
            "placeholder_token": f"{{{{{placeholder_id}}}}}",
        }
    ]
    message = f"fact_boundary:实施计划:schedule_frequency:{sentence}"
    context = _build_fresh_repair_context(
        validation={
            "errors": [message],
            "issues": [{
                "severity": "blocking", "category": "fact_boundary",
                "code": "schedule_frequency", "section_id": SECTION_A,
                "section_title": "实施计划", "message": message,
            }],
        },
        draft=initial,
        fact_ledger=local_ledger,
        pending_confirmations=[],
        target=target(),
    )
    patch = candidate(context, SAFE_A)
    merged, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=payload(context, [patch]),
        repair_context=context,
        fact_ledger=local_ledger,
        initial_validation=snapshot(initial),
        validate_candidate=snapshot,
    )
    assert merged == initial
    assert "fresh_patch_registered_placeholder_removed" in report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]


def test_one_failed_issue_does_not_discard_other_applied_repairs():
    first = f"{BAD_A}\n\n问题将在2小时内完成处理。"
    initial = draft(first)
    errors = [
        f"fact_boundary:实施计划:schedule_frequency:{BAD_A}",
        "fact_boundary:实施计划:numeric_commitment:问题将在2小时内完成处理。",
    ]
    context = _build_fresh_repair_context(
        validation={
            "errors": errors,
            "issues": [
                {
                    "severity": "blocking", "category": "fact_boundary",
                    "code": "schedule_frequency", "section_id": SECTION_A,
                    "section_title": "实施计划", "message": errors[0],
                },
                {
                    "severity": "blocking", "category": "fact_boundary",
                    "code": "numeric_commitment", "section_id": SECTION_A,
                    "section_title": "实施计划", "message": errors[1],
                },
            ],
        },
        draft=initial,
        fact_ledger=ledger(), pending_confirmations=[], target=target(),
    )
    cards = context["repair_task_cards"]
    by_code = {card["issue_code"]: card for card in cards}
    response = {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": [
            {
                "issue_fingerprint": by_code["schedule_frequency"]["issue_fingerprint"],
                "section_id": SECTION_A,
                "candidates": [{
                    "candidate_id": "C1", "candidate_role": "primary",
                    "operation_type": "replace_sentence", "replacement_text": SAFE_A,
                }],
            },
            {
                "issue_fingerprint": by_code["numeric_commitment"]["issue_fingerprint"],
                "section_id": SECTION_A,
                "candidates": [{
                    "candidate_id": "C1", "candidate_role": "primary",
                    "operation_type": "replace_sentence",
                    "replacement_text": "本系统支持在线扩容。",
                }],
            },
        ],
    }
    merged, report = _apply_validated_fresh_patches(
        initial=initial, patch_payload=response, repair_context=context,
        fact_ledger=ledger(), initial_validation=snapshot(initial),
        validate_candidate=snapshot,
    )
    assert BAD_A not in merged["sections"][0]["content"]
    assert "2小时" in merged["sections"][0]["content"]
    assert report["applied_operation_count"] == 1
    assert report["unresolved_repair_task_count"] == 1


def test_legacy_same_target_results_do_not_create_overlapping_operations():
    context = repair_context()
    card = deepcopy(context["repair_task_cards"][0])
    card["issue_code"] = "unconfirmed_bidder_commitment"
    card["issue_fingerprint"] = "second"
    context["repair_task_cards"].append(card)
    value = payload(context, [candidate(context, SAFE_A)])
    value["repair_results"].append({
        "issue_fingerprint": "second",
        "section_id": SECTION_A,
        "candidates": [candidate(context, "项目实施过程开展内部偏差分析。")],
    })
    _, report = _apply_validated_fresh_patches(
        initial=context["initial_candidate"], patch_payload=value,
        repair_context=context, fact_ledger=ledger(),
        initial_validation=snapshot(context["initial_candidate"]),
        validate_candidate=snapshot,
    )
    assert report["applied_operation_count"] == 1
    assert all(
        "fresh_patch_operation_overlap" not in attempt["reason_codes"]
        for issue in report["issue_results"]
        for attempt in issue.get("candidate_attempts") or []
    )
    assert "fresh_patch_legacy_duplicate_target_result" in report["repair_target_groups"][0]["diagnostic_codes"]


def test_merged_candidate_runs_full_validation():
    context = repair_context()
    calls = []
    def validator(value: dict) -> dict:
        calls.append(deepcopy(value))
        return snapshot(value)
    _apply_validated_fresh_patches(
        initial=context["initial_candidate"],
        patch_payload=payload(context, [candidate(context, SAFE_A)]),
        repair_context=context, fact_ledger=ledger(),
        initial_validation=snapshot(context["initial_candidate"]),
        validate_candidate=validator,
    )
    assert calls and SAFE_A in calls[-1]["sections"][0]["content"]


def test_existing_monotonic_candidate_selection_is_unchanged():
    initial = snapshot(draft())
    fresh = snapshot(draft(SAFE_A))
    assert _fresh_candidate_is_monotonic_improvement(initial=initial, fresh=fresh) == (
        True, "fresh_is_strict_monotonic_improvement"
    )


class StructuredPatchFakeModel:
    def __init__(self, initial: dict):
        self.initial = initial
        self.calls: list[dict] = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        metadata = {"provider": "fake", "model": "fake", "usage": empty_usage()}
        if len(self.calls) == 1:
            return ToolModelResult(
                "final", [], json.dumps(self.initial, ensure_ascii=False), metadata
            )
        user_content = next(
            str(item["content"])
            for item in messages
            if item.get("role") == "user"
        )
        request = json.loads(user_content)
        card = request["repair_task_cards"][0]
        response = {
            "base_candidate_hash": request["base_candidate_hash"],
            "repair_results": [{
                "issue_fingerprint": card["issue_fingerprint"],
                "section_id": card["section_id"],
                "candidates": [{
                    "candidate_id": "C1",
                    "candidate_role": "primary",
                    "operation_type": "replace_sentence",
                    "replacement_text": (
                        "部署过程依据招标要求和确认后的技术资料组织配置，"
                        "完成后通过测试用例核验相关要求，并记录实际结果。"
                    ),
                }],
            }],
        }
        return ToolModelResult(
            "tool_calls",
            [{
                "tool_call_id": "patch-1",
                "name": "submit_structured_patch",
                "arguments": response,
            }],
            None,
            metadata,
        )


def test_fresh_issue_counts_are_serialized(tmp_path):
    from tests.test_technical_writing_fresh_repair import product_errors, run_agent

    model = StructuredPatchFakeModel(product_errors(1))
    result = run_agent(tmp_path, model)
    scope = result["fresh_quality_regeneration"]["fresh_edit_scope"]
    assert scope["raw_auto_repair_issue_count"] >= 1
    assert scope["unique_auto_repair_issue_count"] >= 1
    assert scope["editable_issue_count"] == result["fresh_quality_regeneration"]["fresh_patch"]["repair_task_count"]


def test_merged_patch_candidate_runs_full_validation_and_selection(tmp_path):
    from tests.test_technical_writing_fresh_repair import product_errors, run_agent

    model = StructuredPatchFakeModel(product_errors(1))
    result = run_agent(tmp_path, model)
    regeneration = result["fresh_quality_regeneration"]
    assert regeneration["generated"] is True
    assert regeneration["validated"] is True
    assert regeneration["fresh_patch"]["applied_operation_count"] == 1
    assert result["fresh_candidate_validation"] is not None


def test_fresh_uses_unique_auto_repair_issues():
    test_unique_issue_creates_one_repair_task_card()


def test_duplicate_raw_issues_create_one_repair_task():
    context = repair_context()
    assert context["fresh_edit_scope"]["raw_auto_repair_issue_count"] == 2
    assert context["fresh_edit_scope"]["editable_issue_count"] == 1


def test_review_only_issue_is_not_a_repair_target():
    test_review_only_issue_creates_guard_not_repair_task()


def test_patch_audit_and_full_audit_share_permission_source():
    context = repair_context()
    assert context["compiled_effective_writing_context"]["permission_source"] == "section_fact_ledger"
    assert context["compiled_effective_writing_context"]["section_contexts"][SECTION_A]["effective_writing_permissions"] == ledger()[0]["generation_permissions"]


def test_same_clause_has_same_classification_in_all_audit_paths():
    assert "unconfirmed_product_capability" in _patch_replacement_fact_codes(
        initial=draft(), section_index=0, section_id=SECTION_A,
        replacement_text="本系统支持在线扩容。", fact_ledger=ledger(),
    )


def test_patch_replacement_runs_existing_fact_audit():
    test_patch_with_new_report_submission_is_rejected()


def test_patch_cannot_add_numeric_commitment():
    context = repair_context()
    merged, _ = apply(context, [candidate(context, "问题在2小时内完成。")])
    assert merged == context["initial_candidate"]


def test_patch_cannot_add_staffing_or_resource_fact():
    context = repair_context()
    merged, _ = apply(context, [candidate(context, "设立质量保障组并调配公司后备资源。")])
    assert merged == context["initial_candidate"]


def test_patch_cannot_create_new_insufficient_prose():
    test_patch_candidate_cannot_create_quality_regression()


def test_patch_operations_apply_from_end_to_start():
    initial = draft("甲句。乙句。丙句。")
    operations = [
        {
            "section_index": 0, "start": 0, "end": 2,
            "replacement_text": "第一句", "section_id": SECTION_A,
        },
        {
            "section_index": 0, "start": 6, "end": 8,
            "replacement_text": "第三句", "section_id": SECTION_A,
        },
    ]
    merged = _apply_fresh_patch_operations(initial, operations)
    assert merged["sections"][0]["content"] == "第一句。乙句。第三句。"


def _tool_result(calls: list[dict], *, final_content: str | None = None) -> ToolModelResult:
    return ToolModelResult(
        "tool_calls" if calls else "final",
        calls,
        final_content,
        {"provider": "fake", "model": "fake", "usage": empty_usage()},
    )


def test_fresh_uses_forced_submit_structured_patch_tool():
    tool = _submit_structured_patch_tool_definition()
    choice = _forced_structured_patch_tool_choice()
    assert tool["function"]["name"] == "submit_structured_patch"
    assert choice == {"type": "function", "function": {"name": "submit_structured_patch"}}


def test_fresh_does_not_request_tool_free_final_json():
    messages = _fresh_structured_patch_messages(repair_context())
    assert "不得输出普通final文本" in messages[1]["content"]
    assert "submit_structured_patch" in messages[1]["content"]


def test_fresh_accepts_exactly_one_patch_tool_call():
    context = repair_context()
    value = payload(context, [candidate(context, SAFE_A)])
    result = _tool_result([{
        "tool_call_id": "call-1", "name": "submit_structured_patch", "arguments": value,
    }])
    assert _fresh_patch_payload_from_tool_result(result) == value


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (_tool_result([], final_content="普通文本"), "fresh_patch_tool_call_missing"),
        (_tool_result([
            {"tool_call_id": "1", "name": "submit_structured_patch", "arguments": {}},
            {"tool_call_id": "2", "name": "submit_structured_patch", "arguments": {}},
        ]), "fresh_patch_tool_call_multiple"),
        (_tool_result([{"tool_call_id": "1", "name": "save_section_draft", "arguments": {}}]), "fresh_patch_tool_name_invalid"),
        (_tool_result([{"tool_call_id": "1", "name": "submit_structured_patch", "arguments": "{broken"}]), "fresh_patch_arguments_invalid_json"),
        (_tool_result([{"tool_call_id": "1", "name": "submit_structured_patch", "arguments": {"base_candidate_hash": "x"}}]), "fresh_patch_schema_invalid"),
    ],
)
def test_fresh_rejects_invalid_tool_protocol(result: ToolModelResult, code: str):
    with pytest.raises(_FreshPatchProtocolError) as captured:
        _fresh_patch_payload_from_tool_result(result)
    assert captured.value.code == code


def test_fresh_rejects_missing_tool_call():
    test_fresh_rejects_invalid_tool_protocol(
        _tool_result([], final_content="普通文本"), "fresh_patch_tool_call_missing"
    )


def test_fresh_rejects_multiple_tool_calls():
    test_fresh_rejects_invalid_tool_protocol(
        _tool_result([
            {"tool_call_id": "1", "name": "submit_structured_patch", "arguments": {}},
            {"tool_call_id": "2", "name": "submit_structured_patch", "arguments": {}},
        ]), "fresh_patch_tool_call_multiple"
    )


def test_fresh_rejects_wrong_tool_name():
    test_fresh_rejects_invalid_tool_protocol(
        _tool_result([{"tool_call_id": "1", "name": "save_section_draft", "arguments": {}}]),
        "fresh_patch_tool_name_invalid",
    )


def test_fresh_rejects_invalid_tool_arguments_json():
    test_fresh_rejects_invalid_tool_protocol(
        _tool_result([{"tool_call_id": "1", "name": "submit_structured_patch", "arguments": "{broken"}]),
        "fresh_patch_arguments_invalid_json",
    )


def test_fresh_rejects_patch_schema_violation():
    test_fresh_rejects_invalid_tool_protocol(
        _tool_result([{"tool_call_id": "1", "name": "submit_structured_patch", "arguments": {"base_candidate_hash": "x"}}]),
        "fresh_patch_schema_invalid",
    )


def test_fresh_rejects_old_full_chapter_response():
    result = _tool_result([], final_content=json.dumps(draft(), ensure_ascii=False))
    with pytest.raises(_FreshPatchProtocolError) as captured:
        _fresh_patch_payload_from_tool_result(result)
    assert captured.value.code == "fresh_patch_protocol_invalid"


def test_delete_sentence_requires_empty_replacement():
    context = repair_context()
    value = payload(context, [candidate(context, "仍有正文", operation_type="delete_sentence")])
    with pytest.raises(ValidationError):
        _fresh_patch_protocol_payload(value)


def test_fresh_does_not_inherit_full_agent_messages():
    messages = _fresh_structured_patch_messages(repair_context())
    assert [item["role"] for item in messages] == ["system", "developer", "user"]
    serialized = json.dumps(messages, ensure_ascii=False)
    assert "save_section_draft" in messages[1]["content"]  # explicit prohibition only
    assert '"role":"assistant"' not in serialized
    assert '"role":"tool"' not in serialized


def test_fresh_uses_compiled_baseline_initial_and_tasks():
    context = repair_context()
    user_payload = json.loads(_fresh_structured_patch_messages(context)[2]["content"])
    assert user_payload["compiled_effective_writing_context"] == context["compiled_effective_writing_context"]
    assert user_payload["initial_candidate"] == context["initial_candidate"]
    assert user_payload["repair_task_cards"] == context["repair_task_cards"]
    assert user_payload["base_candidate_hash"] == context["initial_candidate_sha256"]


def test_fresh_context_does_not_duplicate_section_permissions():
    context = repair_context()
    user_payload = json.loads(_fresh_structured_patch_messages(context)[2]["content"])
    assert "effective_writing_permissions" not in json.dumps(user_payload["repair_task_cards"], ensure_ascii=False)
    assert "effective_writing_permissions" in json.dumps(user_payload["compiled_effective_writing_context"], ensure_ascii=False)


class InvalidPatchArgumentsModel:
    def __init__(self, initial: dict):
        self.initial = initial
        self.calls: list[dict] = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        metadata = {"provider": "fake", "model": "fake", "usage": empty_usage()}
        if len(self.calls) == 1:
            return ToolModelResult("final", [], json.dumps(self.initial, ensure_ascii=False), metadata)
        return ToolModelResult(
            "tool_calls",
            [{"tool_call_id": "broken", "name": "submit_structured_patch", "arguments": "{broken"}],
            None,
            metadata,
        )


def _run_invalid_patch_agent(tmp_path, *, events: list[dict] | None = None):
    from tests.test_technical_writing_fresh_repair import (
        EmptyStore, NoWeb, product_errors, write_inputs,
    )

    paths = write_inputs(tmp_path)
    model = InvalidPatchArgumentsModel(product_errors(1))
    agent = build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=paths["response"],
            writing_outline_path=paths["writing"],
            task_information_path=paths["tasks"],
            output_key="invalid-patch",
            target_chapter_id="CH-FRESH",
            web_search_policy="disabled",
        ),
        llm=model,
        output_dir=tmp_path,
        disable_web=True,
        metadata_store=EmptyStore(),
        web_provider=NoWeb(),
    )
    return agent.run(event_handler=(events.append if events is not None else None)), model


def test_json_parse_failure_sets_protocol_issue_code(tmp_path):
    result, _ = _run_invalid_patch_agent(tmp_path)
    patch = result["fresh_quality_regeneration"]["fresh_patch"]
    assert patch["protocol_issue_code"] == "fresh_patch_arguments_invalid_json"
    assert patch["raw_response_saved"] is True
    assert patch["raw_response_path"]
    assert patch["raw_response_sha256"]
    assert patch["raw_response_character_count"] == len("{broken")


def test_json_parse_failure_sets_model_response_parse_failed(tmp_path):
    result, _ = _run_invalid_patch_agent(tmp_path)
    status = result["fresh_quality_regeneration"]
    assert status["model_call_failed"] is False
    assert status["model_response_parse_failed"] is True


def test_json_parse_failure_does_not_report_zero_remaining_errors(tmp_path):
    result, _ = _run_invalid_patch_agent(tmp_path)
    status = result["fresh_quality_regeneration"]
    assert status["remaining_raw_error_count"] is None
    assert status["remaining_grouped_error_count"] is None
    assert status["remaining_error_details"] is None


def test_json_parse_failure_marks_remaining_validation_not_run(tmp_path):
    result, _ = _run_invalid_patch_agent(tmp_path)
    assert result["fresh_quality_regeneration"]["remaining_validation_status"] == "not_run"


def test_json_parse_failure_marks_all_tasks_unresolved(tmp_path):
    result, _ = _run_invalid_patch_agent(tmp_path)
    status = result["fresh_quality_regeneration"]
    assert status["unresolved_repair_task_count"] == status["repair_task_count"]
    assert len(status["unresolved_issue_fingerprints"]) == status["repair_task_count"]


def test_json_parse_failure_populates_issue_results(tmp_path):
    result, _ = _run_invalid_patch_agent(tmp_path)
    patch = result["fresh_quality_regeneration"]["fresh_patch"]
    assert len(patch["issue_results"]) == patch["repair_task_count"]
    assert all(item["status"] == "not_attempted" for item in patch["issue_results"])
    assert all(item["reason_codes"] == ["fresh_patch_arguments_invalid_json"] for item in patch["issue_results"])


def test_json_parse_failure_preserves_initial_candidate(tmp_path):
    result, model = _run_invalid_patch_agent(tmp_path)
    assert result["selected_candidate"] == "initial"
    assert _candidate_sha256(result["chapter_draft"]) == result["initial_candidate_sha256"]
    assert result["status"] == "completed"


def test_fresh_emits_model_call_started_and_completed(tmp_path):
    events: list[dict] = []
    _run_invalid_patch_agent(tmp_path, events=events)
    event_types = [item["event_type"] for item in events]
    assert "model_call_started" in event_types
    assert "model_call_completed" in event_types
    assert "model_usage_updated" in event_types


def test_fresh_emits_structured_patch_protocol_event(tmp_path):
    from tests.test_technical_writing_fresh_repair import (
        EmptyStore, NoWeb, product_errors, write_inputs,
    )

    paths = write_inputs(tmp_path)
    model = StructuredPatchFakeModel(product_errors(1))
    agent = build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=paths["response"],
            writing_outline_path=paths["writing"],
            task_information_path=paths["tasks"],
            output_key="valid-patch-events",
            target_chapter_id="CH-FRESH",
            web_search_policy="disabled",
        ),
        llm=model, output_dir=tmp_path, disable_web=True,
        metadata_store=EmptyStore(), web_provider=NoWeb(),
    )
    events: list[dict] = []
    agent.run(event_handler=events.append)
    event_types = [item["event_type"] for item in events]
    assert "structured_patch_response_received" in event_types
    assert "structured_patch_protocol_validated" in event_types


def test_fresh_parse_failure_event_contains_safe_diagnostics(tmp_path):
    events: list[dict] = []
    _run_invalid_patch_agent(tmp_path, events=events)
    failed = next(item for item in events if item["event_type"] == "fresh_quality_regeneration_failed")
    serialized = json.dumps(failed, ensure_ascii=False)
    assert "fresh_patch_arguments_invalid_json" in serialized
    assert "{broken" not in serialized


def test_task_prompt_requests_primary_and_conservative_candidates():
    context = repair_context()
    requirements = context["repair_task_cards"][0]["candidate_requirements"]
    assert "正常改写" in requirements["primary"]
    assert "must_preserve" in requirements["conservative"]
    messages = _fresh_structured_patch_messages(context)
    assert "C1" in messages[1]["content"]
    assert "C2" in messages[1]["content"]


def test_single_candidate_result_remains_task_local():
    context = repair_context()
    merged, report = apply(context, [candidate(context, SAFE_A)])
    issue = report["issue_results"][0]
    assert merged["sections"][0]["content"] == SAFE_A
    assert issue["status"] == "applied"
    assert issue["candidate_set_complete"] is False


def test_missing_conservative_candidate_is_reported():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    issue = report["issue_results"][0]
    assert issue["missing_candidate_roles"] == ["conservative"]
    assert "fresh_patch_conservative_candidate_missing" in issue["diagnostic_codes"]
    assert report["tasks_missing_conservative_candidate"] == 1


def test_first_candidate_failure_tries_conservative_candidate():
    context = repair_context()
    bad = candidate(context, "按照计划向采购人提交进度报告。", candidate_id="C1")
    safe = candidate(context, SAFE_A, candidate_id="C2")
    _, report = apply(context, [bad, safe])
    attempts = report["issue_results"][0]["candidate_attempts"]
    assert [item["status"] for item in attempts] == ["rejected", "applied"]
    assert report["issue_results"][0]["selected_candidate_id"] == "C2"


def test_optional_delete_candidate_is_not_required():
    context = repair_context()
    first = candidate(context, SAFE_A, candidate_id="C1")
    second = candidate(
        context,
        "项目进度按照合同约定及确认后的实施计划进行控制。",
        candidate_id="C2",
    )
    _, report = apply(context, [first, second])
    issue = report["issue_results"][0]
    assert issue["candidate_set_complete"] is True
    assert issue["missing_candidate_roles"] == []


def test_candidate_distribution_statistics():
    context = repair_context()
    _, one = apply(context, [candidate(context, SAFE_A)])
    assert one["tasks_with_one_candidate"] == 1
    _, two = apply(
        context,
        [
            candidate(context, SAFE_A, candidate_id="C1"),
            candidate(
                context,
                "项目进度按照合同约定及确认后的实施计划进行控制。",
                candidate_id="C2",
            ),
        ],
    )
    assert two["tasks_with_two_candidates"] == 1
    _, three = apply(
        context,
        [
            candidate(context, SAFE_A, candidate_id="C1"),
            candidate(
                context,
                "项目进度按照合同约定及确认后的实施计划进行控制。",
                candidate_id="C2",
            ),
            candidate(context, "", candidate_id="C3", operation_type="delete_sentence"),
        ],
    )
    assert three["tasks_with_three_candidates"] == 1


def test_primary_and_conservative_candidates_are_distinct():
    context = repair_context()
    values = [
        candidate(context, SAFE_A, candidate_id="C1"),
        candidate(
            context,
            "项目进度按照合同约定及确认后的实施计划进行控制。",
            candidate_id="C2",
        ),
    ]
    _, report = apply(context, values)
    assert report["duplicate_candidate_count"] == 0
    assert report["issue_results"][0]["candidate_set_complete"] is True


def test_duplicate_candidates_are_deduplicated():
    context = repair_context()
    values = [
        candidate(context, "按照计划提交阶段进度报告。", candidate_id="C1"),
        candidate(context, "按照计划提交阶段进度报告！", candidate_id="C2"),
    ]
    _, report = apply(context, values)
    issue = report["issue_results"][0]
    duplicate = next(
        item
        for item in issue["candidate_attempts"]
        if "fresh_patch_duplicate_candidate" in item["reason_codes"]
    )
    assert duplicate["status"] == "rejected"
    assert report["duplicate_candidate_count"] == 1


def test_placeholder_tokens_do_not_hide_meaningful_candidate_difference():
    tokens = "、".join(
        f"{{{{PH-FIELD-HUMAN-CONFIRMATION-TASK-00{index}-TECHNICAL-CONFIRM}}}}"
        for index in range(2, 7)
    )
    primary = {
        "operation_type": "replace_sentence",
        "original_text": BAD_A,
        "replacement_text": (
            "同时，质量复核岗位在技术响应形成阶段负责逐项核对，"
            f"相关页码、条款号及适用范围以{tokens}为准。"
        ),
    }
    conservative = {
        **primary,
        "replacement_text": f"相关页码、条款号及适用范围以{tokens}为准。",
    }

    assert not _fresh_patch_candidates_equivalent(primary, conservative)


def test_duplicate_candidate_does_not_fail_whole_response():
    context = repair_context()
    values = [
        candidate(context, "按照计划提交阶段进度报告。", candidate_id="C1"),
        candidate(context, "按照计划提交阶段进度报告！", candidate_id="C2"),
        candidate(context, SAFE_A, candidate_id="C2B"),
    ]
    merged, report = apply(context, values)
    assert merged["sections"][0]["content"] == SAFE_A
    assert report["issue_results"][0]["selected_candidate_id"] == "C2B"


def _related_issue_context(*, second_sentence: bool = False) -> dict:
    frequency_sentence = BAD_A
    commitment_sentence = (
        "另行向采购人提交书面进度报告。" if second_sentence else BAD_A
    )
    initial = draft(
        frequency_sentence
        + (f"\n\n{commitment_sentence}" if second_sentence else "")
    )
    errors = [
        f"fact_boundary:实施计划:schedule_frequency:{frequency_sentence}",
        f"fact_boundary:实施计划:unconfirmed_bidder_commitment:{commitment_sentence}",
    ]
    return _build_fresh_repair_context(
        validation={
            "errors": errors,
            "issues": [
                {
                    "severity": "blocking",
                    "category": "fact_boundary",
                    "code": "schedule_frequency",
                    "section_id": SECTION_A,
                    "section_title": "实施计划",
                    "message": errors[0],
                },
                {
                    "severity": "blocking",
                    "category": "fact_boundary",
                    "code": "unconfirmed_bidder_commitment",
                    "section_id": SECTION_A,
                    "section_title": "实施计划",
                    "message": errors[1],
                },
            ],
        },
        draft=initial,
        fact_ledger=ledger(),
        pending_confirmations=[],
        target=target(),
    )


def test_same_sentence_related_violations_are_in_task_card():
    context = _related_issue_context()
    schedule = next(
        item for item in context["repair_task_cards"] if item["issue_code"] == "schedule_frequency"
    )
    assert {item["issue_code"] for item in schedule["related_violations"]} == {
        "schedule_frequency",
        "unconfirmed_bidder_commitment",
    }


def test_schedule_repair_also_removes_unsupported_submission():
    context = _related_issue_context()
    card = next(
        item for item in context["repair_task_cards"] if item["issue_code"] == "schedule_frequency"
    )
    patch = {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": [
            {
                "issue_fingerprint": card["issue_fingerprint"],
                "section_id": SECTION_A,
                "candidates": [
                    {
                        "candidate_id": "C1",
                        "candidate_role": "primary",
                        "operation_type": "replace_sentence",
                        "replacement_text": "按照计划向采购人提交进度报告。",
                    }
                ],
            }
        ],
    }
    _, report = _apply_validated_fresh_patches(
        initial=context["initial_candidate"],
        patch_payload=patch,
        repair_context=context,
        fact_ledger=ledger(),
        initial_validation=snapshot(context["initial_candidate"]),
        validate_candidate=snapshot,
    )
    assert report["applied_operation_count"] == 0


def test_unrelated_violations_remain_separate():
    context = _related_issue_context(second_sentence=True)
    cards = context["repair_task_cards"]
    assert len(cards) == 2
    assert all(len(card["related_violations"]) == 1 for card in cards)


def test_issue_result_serializes_status_section_and_issue_code():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    issue = report["issue_results"][0]
    assert issue["status"] == "applied"
    assert issue["section_id"] == SECTION_A
    assert issue["section_title"] == "实施计划"
    assert issue["issue_code"] == "schedule_frequency"


def test_candidate_attempt_serializes_operation_and_text():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert attempt["candidate_order"] == 1
    assert attempt["operation_type"] == "replace_sentence"
    assert attempt["original_text"] == BAD_A
    assert attempt["replacement_text"] == SAFE_A
    assert "context_before" in attempt and "context_after" in attempt


def test_rejected_attempt_serializes_audit_findings():
    context = repair_context()
    _, report = apply(
        context,
        [candidate(context, "按照计划向采购人提交进度报告。")],
    )
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert attempt["status"] == "rejected"
    assert attempt["audit_findings"]
    finding = attempt["audit_findings"][0]
    assert finding["offending_span"] in finding["offending_sentence"]


def test_applied_operation_serializes_original_and_replacement():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    operation = report["applied_operations"][0]
    assert operation["original_text"] == BAD_A
    assert operation["replacement_text"] == SAFE_A
    assert len(operation["initial_text_sha256"]) == 64
    assert len(operation["result_text_sha256"]) == 64


def test_successful_protocol_metadata_is_in_run_report(tmp_path):
    from tests.test_technical_writing_fresh_repair import product_errors, run_agent

    result = run_agent(tmp_path, StructuredPatchFakeModel(product_errors(1)))
    protocol = result["fresh_quality_regeneration"]["fresh_patch"]
    assert protocol["response_type"] == "tool_calls"
    assert protocol["tool_call_count"] == 1
    assert protocol["forced_tool_name"] == "submit_structured_patch"
    assert protocol["protocol_validated"] is True


def test_remaining_errors_use_canonical_section_ids():
    value = draft()
    details = _fresh_quality_error_details(
        [f"fact_boundary:实施计划:schedule_frequency:{BAD_A}"],
        draft=value,
        expected_sections=[
            {"section_id": SECTION_A, "section_title": "实施计划", "expected_order": 0},
            {"section_id": SECTION_B, "section_title": "风险控制", "expected_order": 1},
        ],
    )
    assert details[0]["section_id"] == SECTION_A
    assert details[0]["section_id"] != details[0]["section_title"]

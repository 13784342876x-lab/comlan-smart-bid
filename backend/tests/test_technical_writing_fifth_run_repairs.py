from __future__ import annotations

from copy import deepcopy

import pytest

from app.technical_writing_agent import (
    _apply_personnel_content_gate,
    _audit_fresh_candidate_diff,
    _fresh_quality_regeneration_instruction,
    _normalized_span_is_within_sentence,
    _sanitize_fact_boundary_issue_spans,
)
from app.technical_writing_quality import (
    audit_generation_permissions,
    extract_tender_obligation_items,
    inspect_unconfirmed_commitments,
)


SECTION_A = "CH-01"
SECTION_B = "CH-02"


def _draft(content: str, *, section_id: str = SECTION_A) -> dict:
    return {
        "chapter_title": "测试章节",
        "sections": [
            {
                "section_id": section_id,
                "heading": "实施方案",
                "block_type": "implementation_plan",
                "content": content,
                "covered_score_item_ids": [],
                "covered_requirement_ids": [],
                "placeholder_ids": [],
            }
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }


def _ledger(
    *,
    placeholders: list[str] | None = None,
    tender_text: str | None = None,
    source_id: str = "REQ-DOC",
) -> list[dict]:
    allowed_placeholders = [
        {"placeholder_id": value, "placeholder_token": f"{{{{{value}}}}}"}
        for value in placeholders or []
    ]
    tender_facts = []
    if tender_text:
        tender_facts = [
            {
                "text": tender_text,
                "source_id": source_id,
                "requirement_id": source_id,
                "usage": "no_deviation_response",
                "obligation_items": extract_tender_obligation_items(
                    tender_text,
                    source_id=source_id,
                    requirement_id=source_id,
                    deviation_status="no_deviation",
                ),
            }
        ]
    permissions = {
        "allowed_confirmed_facts": [],
        "allowed_tender_facts": tender_facts,
        "chapter_allowed_tender_facts": tender_facts,
        "allowed_tender_numeric_facts": [],
        "chapter_allowed_tender_numeric_facts": [],
        "allowed_confirmed_numeric_facts": [],
        "allowed_confirmed_personnel_facts": [],
        "allowed_confirmed_product_capabilities": [],
        "allowed_placeholders": allowed_placeholders,
        "forbid_unlisted_numeric_commitments": True,
        "forbid_unlisted_personnel_qualifications": True,
        "forbid_unlisted_personnel_narrative": True,
        "forbid_unlisted_product_capabilities": True,
        "personnel_content_mode": "source_only",
    }
    return [
        {
            "section_id": SECTION_A,
            "section_title": "实施方案",
            "generation_permissions": permissions,
            "personnel_content_mode": "source_only",
        }
    ]


def _personnel_codes(content: str) -> set[str]:
    result = audit_generation_permissions(_draft(content), fact_ledger=_ledger())
    return {
        str(item.get("code"))
        for item in result["unauthorized_personnel_claims"]
    }


@pytest.mark.parametrize(
    "content",
    [
        "由项目经理组织进度评估。",
        "经项目经理审核后归档。",
        "由项目经理组织质量检查。",
    ],
)
def test_generic_project_manager_duty_is_allowed_without_personnel_profile(content):
    assert "personnel_narrative_without_source" not in _personnel_codes(content)


def test_project_manager_review_and_archive_is_not_personnel_fact():
    assert not _personnel_codes("经项目经理审核后归档。")


def test_generic_quality_check_role_is_allowed():
    assert not _personnel_codes("由项目经理组织质量检查。")


def test_weekly_frequency_is_audited_separately_from_role_duty():
    result = audit_generation_permissions(
        _draft("项目经理每周组织质量检查。"), fact_ledger=_ledger()
    )
    assert not result["unauthorized_personnel_claims"]
    assert {
        item["code"] for item in result["unauthorized_numeric_claims"]
    } == {"schedule_frequency"}


def test_unsupported_personnel_qualification_remains_blocked():
    assert "unconfirmed_personnel_qualification" in _personnel_codes(
        "项目经理具有丰富项目经验。"
    )


def test_staff_count_and_backup_personnel_remain_blocked():
    codes = _personnel_codes("项目设置三名实施人员，并设置备份人员。")
    errors = inspect_unconfirmed_commitments(
        _draft("项目设置三名实施人员，并设置备份人员。"),
        confirmed_facts={},
        generation_permissions=_ledger(),
    )
    assert "unconfirmed_resource_commitment" in codes
    assert any("numeric_commitment" in value for value in errors)


def test_neutral_team_configuration_principle_is_allowed():
    placeholder = "PH-FIELD-PROJECT-TEAM"
    content = (
        "项目团队按照专业分工和协同实施原则进行配置，"
        f"具体人员及岗位安排详见{{{{{placeholder}}}}}。"
    )
    result = audit_generation_permissions(
        _draft(content), fact_ledger=_ledger(placeholders=[placeholder])
    )
    assert not result["unauthorized_personnel_claims"]


def test_team_details_can_defer_to_registered_placeholder():
    placeholder = "PH-FIELD-PROJECT-TEAM"
    draft = _draft(
        "团队成员组成遵循专业分工、高效协作的原则，"
        f"具体成员清单为{{{{{placeholder}}}}}。"
    )
    draft["sections"][0]["placeholder_ids"] = [placeholder]
    draft["placeholders_used"] = [placeholder]
    filtered, report = _apply_personnel_content_gate(
        draft, fact_ledger=_ledger(placeholders=[placeholder])
    )
    assert f"{{{{{placeholder}}}}}" in filtered["sections"][0]["content"]
    assert report["removed_character_count"] == 0


def test_complexity_based_team_matching_is_not_confirmed_staffing_fact():
    placeholder = "PH-FIELD-PROJECT-TEAM"
    content = (
        "具体执行团队的组成将依据项目技术需求和实施复杂度进行匹配，"
        f"人员信息详见{{{{{placeholder}}}}}。"
    )
    result = audit_generation_permissions(
        _draft(content), fact_ledger=_ledger(placeholders=[placeholder])
    )
    assert not result["unauthorized_personnel_claims"]


def test_neutral_team_text_cannot_assert_count_or_qualification():
    content = "项目团队按照专业分工原则配置三名具有丰富经验的实施人员。"
    result = audit_generation_permissions(_draft(content), fact_ledger=_ledger())
    assert result["unauthorized_personnel_claims"]


def test_registered_personnel_placeholders_remain_preserved():
    placeholder = "PH-FIELD-PROJECT-TEAM"
    draft = _draft(f"具体人员详见{{{{{placeholder}}}}}。")
    filtered, _ = _apply_personnel_content_gate(
        draft, fact_ledger=_ledger(placeholders=[placeholder])
    )
    assert f"{{{{{placeholder}}}}}" in filtered["sections"][0]["content"]


def _fact_issue(sentence: str, span: str) -> dict:
    return {
        "severity": "blocking",
        "category": "fact_boundary",
        "code": "unconfirmed_bidder_commitment",
        "section_id": SECTION_A,
        "section_title": "实施方案",
        "message": f"fact_boundary:实施方案:unconfirmed_bidder_commitment:{span}",
        "offending_sentence": sentence,
        "offending_span": span,
    }


def test_offending_span_must_exist_in_sentence():
    sentence = "项目实施划分为需求确认、安装部署和验收交付等阶段。"
    assert not _normalized_span_is_within_sentence("服务响应", sentence)


def test_service_response_is_not_reported_when_absent_from_sentence():
    sentence = "项目实施划分为需求确认、安装部署和验收交付等阶段。"
    issues = _sanitize_fact_boundary_issue_spans(
        [_fact_issue(sentence, "服务响应")], draft=_draft(sentence)
    )
    assert issues == []


def test_tender_fact_keyword_is_not_used_as_offending_span():
    sentence = "实施过程形成检查记录。"
    issues = _sanitize_fact_boundary_issue_spans(
        [_fact_issue(sentence, "随机资料")], draft=_draft(sentence)
    )
    assert issues == []


def test_normalized_span_matching_allows_spacing_and_punctuation():
    assert _normalized_span_is_within_sentence(
        "配置 基线文件", "完成后形成《配置基线文件》，用于复核。"
    )


def test_invalid_fact_issue_is_dropped_when_span_cannot_be_located():
    sentence = "实施过程形成检查记录。"
    assert _sanitize_fact_boundary_issue_spans(
        [_fact_issue(sentence, "公司专家")], draft=_draft(sentence)
    ) == []


def test_multiple_document_obligations_are_split_from_one_requirement():
    items = extract_tender_obligation_items("提供完整的使用手册和维修手册。")
    assert {item["normalized_object"] for item in items} == {
        "使用手册",
        "维修手册",
    }


def test_each_document_item_inherits_same_source_id():
    items = extract_tender_obligation_items(
        "提供用户手册、维修手册。", source_id="DYNAMIC-SOURCE"
    )
    assert {item["source_id"] for item in items} == {"DYNAMIC-SOURCE"}


def test_use_manual_and_maintenance_manual_match_independently():
    tender = "交货时提供完整的使用手册和维修手册。"
    result = audit_generation_permissions(
        _draft("我方交货时提供使用手册和维修手册。"),
        fact_ledger=_ledger(tender_text=tender),
    )
    assert not result["unauthorized_bidder_commitments"]
    matched = result["matched_tender_fact_spans"][0]
    assert {"使用手册", "维修手册"} <= set(matched["allowed_spans"])


def test_combined_manual_phrase_is_normalized_into_separate_items():
    items = extract_tender_obligation_items("提供使用及维修手册。")
    assert {item["normalized_object"] for item in items} == {
        "使用手册",
        "维修手册",
    }


def test_unlisted_certificate_remains_blocked():
    tender = "交货时提供使用手册和维修手册。"
    result = audit_generation_permissions(
        _draft("我方交货时提供使用手册、维修手册和合格证。"),
        fact_ledger=_ledger(tender_text=tender),
    )
    assert {item["offending_span"] for item in result["unauthorized_bidder_commitments"]} == {"合格证"}


def test_random_material_permission_does_not_allow_all_documents():
    tender = "交货时提供随机资料，包括使用手册和维修手册。"
    result = audit_generation_permissions(
        _draft("我方提供随机资料、使用手册和安装指南。"),
        fact_ledger=_ledger(tender_text=tender),
    )
    assert {item["offending_span"] for item in result["unauthorized_bidder_commitments"]} == {"安装指南"}


def _two_section_draft(first: str, second: str) -> dict:
    value = _draft(first)
    value["sections"].append(
        {
            **deepcopy(value["sections"][0]),
            "section_id": SECTION_B,
            "heading": "风险控制",
            "content": second,
        }
    )
    return value


def _two_section_ledger() -> list[dict]:
    first = _ledger()[0]
    second = deepcopy(first)
    second["section_id"] = SECTION_B
    second["section_title"] = "风险控制"
    return [first, second]


def _repair_context() -> dict:
    return {
        "edit_mode": "minimal_diff_from_initial_candidate",
        "fresh_edit_scope": {
            "editable_section_ids": [SECTION_A],
            "editable_issue_count": 1,
            "editable_issue_spans": [
                {
                    "section_id": SECTION_A,
                    "issue_code": "numeric_commitment",
                    "offending_sentence": "发现问题后2小时内通报。",
                    "offending_span": "2小时内",
                }
            ],
            "frozen_section_count": 1,
        },
        "initial_candidate": _two_section_draft(
            "发现问题后2小时内通报。随后形成处置记录。",
            "风险识别后开展跟踪并记录结果。",
        ),
        "auto_repair_items": [],
        "review_only_guards": [],
        "must_preserve": {},
    }


def test_fresh_freezes_sections_without_auto_repair_issue():
    context = _repair_context()
    fresh = deepcopy(context["initial_candidate"])
    fresh["sections"][1]["content"] += "新增句。"
    audit = _audit_fresh_candidate_diff(
        initial=context["initial_candidate"],
        fresh=fresh,
        repair_context=context,
        fact_ledger=_two_section_ledger(),
    )
    assert audit["unauthorized_changed_section_ids"] == [SECTION_B]


def test_fresh_only_edits_authorized_issue_section():
    context = _repair_context()
    fresh = deepcopy(context["initial_candidate"])
    fresh["sections"][0]["content"] = "发现问题后及时通报。随后形成处置记录。"
    audit = _audit_fresh_candidate_diff(
        initial=context["initial_candidate"], fresh=fresh,
        repair_context=context, fact_ledger=_two_section_ledger()
    )
    assert audit["passed"] is True
    assert audit["changed_section_ids"] == [SECTION_A]


def test_fresh_only_edits_offending_sentence_and_required_context():
    context = _repair_context()
    fresh = deepcopy(context["initial_candidate"])
    fresh["sections"][0]["content"] = "发现问题后及时通报。随后形成完整处置记录。"
    audit = _audit_fresh_candidate_diff(
        initial=context["initial_candidate"], fresh=fresh,
        repair_context=context, fact_ledger=_two_section_ledger()
    )
    assert audit["passed"] is True


def test_fresh_unauthorized_section_change_is_rejected():
    context = _repair_context()
    fresh = deepcopy(context["initial_candidate"])
    fresh["sections"][1]["content"] = "完全重写风险章节。"
    audit = _audit_fresh_candidate_diff(
        initial=context["initial_candidate"], fresh=fresh,
        repair_context=context, fact_ledger=_two_section_ledger()
    )
    assert audit["passed"] is False


def test_fresh_diff_audit_detects_new_fact_outside_issue_scope():
    context = _repair_context()
    fresh = deepcopy(context["initial_candidate"])
    fresh["sections"][0]["content"] = (
        "发现问题后及时通报。本系统支持自动故障切换。随后形成处置记录。"
    )
    audit = _audit_fresh_candidate_diff(
        initial=context["initial_candidate"], fresh=fresh,
        repair_context=context, fact_ledger=_two_section_ledger()
    )
    assert "unconfirmed_product_capability" in audit["new_fact_category_codes"]
    assert audit["passed"] is False


def test_fresh_does_not_use_previous_failed_fresh_as_edit_base():
    context = _repair_context()
    prompt = _fresh_quality_regeneration_instruction(schema={}, repair_context=context)
    assert "initial_candidate是唯一编辑基线" in prompt
    assert "先前Fresh结果" in prompt


def test_initial_candidate_is_the_only_fresh_edit_base():
    context = _repair_context()
    assert context["edit_mode"] == "minimal_diff_from_initial_candidate"
    assert "initial_candidate" in context


def _commitment_errors(sentence: str) -> set[str]:
    return {
        value.split(":", 3)[2]
        for value in inspect_unconfirmed_commitments(
            _draft(sentence), confirmed_facts={}, generation_permissions=_ledger()
        )
        if value.startswith("fact_boundary:")
    }


def test_configuration_baseline_document_is_not_product_capability():
    codes = _commitment_errors("完成后形成安装部署记录和配置基线文件。")
    assert "unconfirmed_product_capability" not in codes


def test_document_supports_future_maintenance_is_process_outcome():
    codes = _commitment_errors("配置基线文件作为后续运维管理和配置复核的参考依据。")
    assert "unconfirmed_product_capability" not in codes


def test_product_capability_requires_technical_subject_and_predicate():
    assert "unconfirmed_product_capability" not in _commitment_errors(
        "形成配置记录，为后续问题追踪提供依据。"
    )


def test_absolute_result_wording_can_be_bidder_commitment_not_product_capability():
    codes = _commitment_errors(
        "完成后输出安装部署记录和配置基线文件，确保系统具备可重复部署和后续运维的基础。"
    )
    assert "unconfirmed_product_capability" not in codes
    assert "unconfirmed_bidder_commitment" in codes


def test_real_high_availability_claim_remains_product_capability():
    assert "unconfirmed_product_capability" in _commitment_errors(
        "本系统支持自动故障切换并确保业务不中断。"
    )


def test_fresh_document_sentence_does_not_add_product_capability_category():
    context = _repair_context()
    fresh = deepcopy(context["initial_candidate"])
    fresh["sections"][0]["content"] = (
        "发现问题后及时通报。完成后形成配置基线文件，作为后续运维参考。"
    )
    audit = _audit_fresh_candidate_diff(
        initial=context["initial_candidate"], fresh=fresh,
        repair_context=context, fact_ledger=_two_section_ledger()
    )
    assert "unconfirmed_product_capability" not in audit["new_fact_category_codes"]

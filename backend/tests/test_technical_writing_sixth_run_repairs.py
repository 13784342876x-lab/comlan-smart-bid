from __future__ import annotations

from copy import deepcopy

from app.technical_writing_agent import (
    _audit_fresh_candidate_diff,
    _build_fresh_repair_context,
    _deduplicate_fresh_repair_items,
    _resolve_section_identities,
)
from app.technical_writing_quality import (
    audit_generation_permissions,
    extract_tender_obligation_items,
    inspect_unconfirmed_commitments,
)


SECTION_A = "CH-TEST-01"
SECTION_B = "CH-TEST-02"


def _target(*, duplicate_titles: bool = False) -> dict:
    return {
        "chapter_id": "CH-TEST",
        "title": "测试章节",
        "children": [
            {"chapter_id": SECTION_A, "title": "相同标题" if duplicate_titles else "实施计划"},
            {"chapter_id": SECTION_B, "title": "相同标题" if duplicate_titles else "风险控制"},
        ],
    }


def _draft(first: str, second: str = "风险识别后记录并跟踪处置结果。", *, ids: bool = False) -> dict:
    sections = [
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
    ]
    if ids:
        sections[0]["section_id"] = SECTION_A
        sections[1]["section_id"] = SECTION_B
    return {
        "chapter_title": "测试章节",
        "sections": sections,
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }


def _permissions(*, tender_text: str = "", source_id: str = "REQ-DOC") -> dict:
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
    return {
        "allowed_tender_facts": tender_facts,
        "chapter_allowed_tender_facts": tender_facts,
        "allowed_tender_numeric_facts": [],
        "chapter_allowed_tender_numeric_facts": [],
        "allowed_confirmed_facts": [],
        "allowed_confirmed_numeric_facts": [],
        "allowed_confirmed_personnel_facts": [],
        "allowed_confirmed_product_capabilities": [],
        "allowed_placeholders": [],
        "forbid_unlisted_numeric_commitments": True,
        "forbid_unlisted_personnel_qualifications": True,
        "forbid_unlisted_personnel_narrative": True,
        "forbid_unlisted_product_capabilities": True,
        "personnel_content_mode": "source_only",
    }


def _ledger(*, tender_text: str = "", source_id: str = "REQ-DOC") -> list[dict]:
    return [
        {
            "section_id": SECTION_A,
            "section_title": "实施计划",
            "generation_permissions": _permissions(
                tender_text=tender_text, source_id=source_id
            ),
        },
        {
            "section_id": SECTION_B,
            "section_title": "风险控制",
            "generation_permissions": _permissions(
                tender_text=tender_text, source_id=source_id
            ),
        },
    ]


def _repair_context(initial: dict, *, editable: str = SECTION_A) -> dict:
    expected = [
        {"section_id": SECTION_A, "section_title": "实施计划", "expected_order": 0},
        {"section_id": SECTION_B, "section_title": "风险控制", "expected_order": 1},
    ]
    return {
        "initial_candidate": deepcopy(initial),
        "expected_sections": expected,
        "fresh_edit_scope": {
            "editable_section_ids": [editable],
            "editable_issue_count": 1,
            "editable_issue_spans": [
                {
                    "section_id": editable,
                    "issue_code": "numeric_commitment",
                    "offending_sentence": "发现问题后2小时内通报。",
                    "offending_span": "2小时内",
                }
            ],
            "frozen_section_count": 1,
        },
    }


def test_fresh_edit_scope_uses_canonical_section_ids():
    resolution = _resolve_section_identities(_draft("正文。"), target=_target())
    assert [item["section_id"] for item in resolution["section_identity_map"]] == [
        SECTION_A,
        SECTION_B,
    ]


def test_frozen_section_hashes_use_canonical_section_ids():
    context = _context_with_duplicate_errors()
    assert set(context["fresh_edit_scope"]["frozen_section_hashes"]) <= {
        SECTION_A,
        SECTION_B,
    }


def test_changed_and_editable_sections_use_same_identifier():
    initial = _draft("发现问题后2小时内通报。随后归档。")
    fresh = _draft("发现问题后及时通报。随后归档。")
    audit = _audit_fresh_candidate_diff(
        initial=initial,
        fresh=fresh,
        repair_context=_repair_context(initial),
        fact_ledger=_ledger(),
    )
    assert audit["changed_section_ids"] == [SECTION_A]
    assert audit["editable_section_ids"] == [SECTION_A]


def test_missing_model_section_id_is_resolved_from_expected_outline():
    resolution = _resolve_section_identities(_draft("正文。"), target=_target())
    assert resolution["section_identity_map"][0]["resolution_method"] == "unique_title"


def test_duplicate_title_uses_title_and_order_resolution():
    draft = _draft("第一节。", "第二节。")
    draft["sections"][0]["heading"] = "相同标题"
    draft["sections"][1]["heading"] = "相同标题"
    resolution = _resolve_section_identities(
        draft, target=_target(duplicate_titles=True)
    )
    assert [item["resolution_method"] for item in resolution["section_identity_map"]] == [
        "title_and_order",
        "title_and_order",
    ]


def test_unresolved_section_identity_uses_stable_error_code():
    draft = _draft("正文。")
    draft["sections"][0]["heading"] = "不存在的标题"
    resolution = _resolve_section_identities(draft, target=_target())
    assert resolution["issues"][0]["code"] == "section_identity_resolution_failed"


def test_title_id_mismatch_does_not_create_unauthorized_change():
    initial = _draft("发现问题后2小时内通报。随后归档。")
    fresh = _draft("发现问题后及时通报。随后归档。", ids=True)
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert audit["section_identity_consistent"] is True
    assert audit["unauthorized_changed_section_ids"] == []


def test_actual_frozen_section_change_is_still_rejected():
    initial = _draft("发现问题后2小时内通报。")
    fresh = _draft("发现问题后及时通报。", "冻结小节被改写。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert audit["unauthorized_changed_section_ids"] == [SECTION_B]


def test_editable_section_change_is_allowed_after_id_normalization():
    initial = _draft("发现问题后2小时内通报。随后归档。")
    fresh = _draft("发现问题后及时通报。随后归档。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert audit["passed"] is True


def _duplicate_items() -> list[dict]:
    base = {
        "section_id": SECTION_A,
        "section_title": "实施计划",
        "issue_code": "numeric_commitment",
        "error_code": "numeric_commitment",
        "repair_disposition": "auto_repairable",
        "offending_sentence": "发现问题后2小时内通报。",
        "offending_span": "2小时内",
        "violations": [{"offending_sentence": "发现问题后2小时内通报。", "offending_span": "2小时内"}],
    }
    other = deepcopy(base)
    other["violations"] = [
        {"offending_sentence": "发现问题后2小时内通报。", "offending_span": "2小时内", "source": "duplicate"}
    ]
    return [base, deepcopy(base), other]


def test_fresh_edit_scope_uses_unique_grouped_issues():
    assert len(_deduplicate_fresh_repair_items(_duplicate_items())) == 1


def test_duplicate_sentence_issues_create_one_editable_span():
    context = _context_with_duplicate_errors()
    assert context["fresh_edit_scope"]["editable_issue_count"] == 1


def test_raw_issue_count_is_preserved_for_reporting():
    context = _context_with_duplicate_errors()
    summary = context["repair_input_summary"]
    assert summary["raw_auto_repair_issue_count"] >= summary["unique_auto_repair_issue_count"]


def test_duplicate_violations_are_aggregated_not_repeated():
    item = _deduplicate_fresh_repair_items(_duplicate_items())[0]
    assert len(item["violations"]) == 2


def _context_with_duplicate_errors() -> dict:
    message = "fact_boundary:实施计划:numeric_commitment:2小时内"
    validation = {
        "errors": [message, message, message],
        "issues": [
            {
                "severity": "blocking",
                "category": "fact_boundary",
                "code": "numeric_commitment",
                "section_id": SECTION_A,
                "section_title": "实施计划",
                "message": message,
            }
        ],
    }
    return _build_fresh_repair_context(
        validation=validation,
        draft=_draft("发现问题后2小时内通报。随后归档。"),
        fact_ledger=_ledger(),
        pending_confirmations=[],
        target=_target(),
    )


def test_fresh_diff_uses_sentence_level_units():
    initial = _draft("发现问题后2小时内通报。随后归档。")
    fresh = _draft("发现问题后及时通报。本系统支持自动故障切换。随后归档。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert audit["new_fact_records"][0]["new_clause"].endswith("。")


def test_fresh_diff_does_not_return_mid_word_fragments():
    initial = _draft("形成交付记录。")
    fresh = _draft("我方额外提供技术文件，并形成交付记录。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context={
            **_repair_context(initial),
            "fresh_edit_scope": {
                **_repair_context(initial)["fresh_edit_scope"],
                "editable_issue_spans": [{"section_id": SECTION_A, "issue_code": "insufficient_prose"}],
            },
        },
        fact_ledger=_ledger(),
    )
    assert all(len(record["new_clause"]) > len(record["offending_span"]) or record["new_clause"] == record["offending_span"] for record in audit["new_fact_records"])
    assert all(not record["offending_span"].startswith("的") for record in audit["new_fact_records"])


def test_new_fact_span_exists_in_new_clause():
    initial = _draft("随后归档。")
    fresh = _draft("我方额外免费提供设备。随后归档。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert all(record["offending_span"] in record["new_clause"] for record in audit["new_fact_records"])


def test_new_clause_is_reaudited_with_effective_tender_permissions():
    tender = "交货时提供随机资料。"
    initial = _draft("交付前进行检查。")
    fresh = _draft("随机资料的检查依据招标文件REQ-DOC要求进行。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger(tender_text=tender)
    )
    assert audit["new_fact_records"] == []


def test_source_backed_new_clause_is_not_marked_as_new_fact():
    test_new_clause_is_reaudited_with_effective_tender_permissions()


def test_new_fact_codes_are_derived_from_valid_new_fact_records():
    initial = _draft("随后归档。")
    fresh = _draft("本系统支持在线扩容。随后归档。")
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert audit["new_fact_issue_codes"] == sorted({item["issue_code"] for item in audit["new_fact_records"]})
    assert audit["new_fact_category_codes"] == audit["new_fact_issue_codes"]


def test_same_issue_code_with_no_new_fact_record_is_not_reported():
    initial = _draft("我方额外免费提供设备。")
    fresh = deepcopy(initial)
    audit = _audit_fresh_candidate_diff(
        initial=initial, fresh=fresh,
        repair_context=_repair_context(initial), fact_ledger=_ledger()
    )
    assert audit["new_fact_records"] == []
    assert audit["new_fact_issue_codes"] == []


def test_source_backed_rewrite_does_not_create_new_fact_code():
    test_new_clause_is_reaudited_with_effective_tender_permissions()


def test_source_backed_random_material_check_is_not_bidder_commitment():
    tender = "交货时提供随机资料。"
    result = audit_generation_permissions(
        _single_section("随机资料的检查依据招标文件REQ-DOC要求进行。"),
        fact_ledger=_single_ledger(tender),
    )
    assert result["unauthorized_bidder_commitments"] == []


def test_mixed_deliverable_sentence_is_audited_by_individual_item():
    tender = "交付成果包括完整设备及随机资料。"
    sentence = (
        "本项目交付成果包括招标文件明确要求的完整设备及随机资料，"
        "以及项目实施过程中形成的过程记录、配置文档、测试报告和验收资料。"
    )
    result = audit_generation_permissions(
        _single_section(sentence), fact_ledger=_single_ledger(tender)
    )
    spans = {item["offending_span"] for item in result["unauthorized_bidder_commitments"]}
    assert "随机资料" not in spans
    assert {"过程记录", "配置文档", "测试报告", "验收资料"} <= spans


def test_mixed_commitment_issue_requires_nonempty_span():
    test_result = audit_generation_permissions(
        _single_section("我方提供随机资料，并额外免费提供设备。"),
        fact_ledger=_single_ledger("交货时提供随机资料。"),
    )
    assert all(item["offending_span"] for item in test_result["unauthorized_bidder_commitments"])


def test_duplicate_commitment_for_same_span_is_deduplicated():
    result = audit_generation_permissions(
        _single_section("我方额外免费提供设备。"), fact_ledger=_single_ledger("")
    )
    keys = [(item["code"], item["offending_span"]) for item in result["unauthorized_bidder_commitments"]]
    assert len(keys) == len(set(keys))


def test_whole_sentence_span_is_used_only_when_whole_sentence_is_unsupported():
    result = audit_generation_permissions(
        _single_section("我方额外免费提供设备。"), fact_ledger=_single_ledger("")
    )
    assert all(item["offending_span"] in item["offending_sentence"] for item in result["unauthorized_bidder_commitments"])


def _single_section(content: str) -> dict:
    value = _draft(content)
    value["sections"] = [value["sections"][0]]
    return value


def _single_ledger(tender_text: str) -> list[dict]:
    return _ledger(tender_text=tender_text)[:1]


def _codes(content: str) -> set[str]:
    result = audit_generation_permissions(_single_section(content), fact_ledger=_single_ledger(""))
    return {item["code"] for item in result["unauthorized_personnel_claims"]}


def test_neutral_role_duty_remains_allowed():
    assert not _codes("项目经理组织进度评估并审核归档。")


def test_neutral_team_principle_remains_allowed():
    assert not _codes("项目团队按照专业分工和多专业协同原则工作，职责清晰。")


def test_specific_matrix_structure_requires_source():
    assert "unconfirmed_staffing_assignment" in _codes("项目组织采用以项目经理为核心的矩阵式结构。")


def test_named_project_groups_require_staffing_source():
    assert "unconfirmed_staffing_assignment" in _codes("项目组织将设立项目管理组、技术实施组、质量保障组和综合协调组。")


def test_independent_quality_group_is_staffing_assignment():
    assert "unconfirmed_staffing_assignment" in _codes("质量保障组独立于实施组。")


def test_generic_resource_guarantee_requires_confirmation():
    assert "unconfirmed_resource_commitment" in _codes("各阶段获得必要的技术与管理资源保障。")


def test_generic_technical_team_monitoring_is_role_duty():
    assert not _codes("试运行期间，我方技术团队对系统运行情况进行持续监测。")


def test_operation_transfer_is_a_service_commitment():
    result = audit_generation_permissions(
        _single_section("对采购人指定操作人员进行现场操作转移。"),
        fact_ledger=_single_ledger(""),
    )
    assert {item["offending_span"] for item in result["unauthorized_bidder_commitments"]} == {"操作转移"}


def test_mixed_team_duty_and_service_commitment_are_split():
    sentence = "我方技术团队对系统运行情况进行持续监测，并对采购人指定操作人员进行现场操作转移。"
    result = audit_generation_permissions(_single_section(sentence), fact_ledger=_single_ledger(""))
    assert not result["unauthorized_personnel_claims"]
    assert {item["offending_span"] for item in result["unauthorized_bidder_commitments"]} == {"操作转移"}

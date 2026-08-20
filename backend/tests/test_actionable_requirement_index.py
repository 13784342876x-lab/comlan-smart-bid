from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.actionable_requirement_index import (
    build_action_groups,
    build_actionable_requirement_index,
    extract_quantity_rule,
    group_has_action,
    inspect_actionable_requirement_index,
    render_actionable_requirement_index_markdown,
)
from app.llm_client import FORBIDDEN_DECISION_TERMS


LOT_NAME = "0425139003001-6582"


def requirement(
    requirement_id: str,
    raw_text: str,
    *,
    requirement_type: str = "mandatory_substantive_response",
    compliance_level: str = "must_respond",
    risk_type: str = "substantial_response_risk",
    polarity: str = "positive_requirement",
    source_file_id: str = "file-a",
    scope_type: str = "current_package",
    external_reference: dict | None = None,
    human_review_required: bool = False,
    related_special_table_ids: list[str] | None = None,
    required_materials: list[dict] | None = None,
    score_ids: list[str] | None = None,
    chapter_ids: list[str] | None = None,
) -> dict:
    return {
        "requirement_id": requirement_id,
        "title": raw_text[:40],
        "requirement_type": requirement_type,
        "raw_text": raw_text,
        "normalized_requirement": {"text": raw_text, "source_type": "system_judgement"},
        "required_action": {"text": "按要求执行", "source_type": "system_judgement"},
        "required_response_content": {"items": [raw_text], "source_type": "suggested_writing"},
        "required_materials": required_materials or [],
        "source_ref": {
            "source_file_id": source_file_id,
            "source_file_name": f"{source_file_id}.docx",
            "document_type": "full_tender_document",
            "clause_path": "第二章 > 测试条款",
            "source_location": {"paragraph_index": 1},
            "location_precision": "clause_approximate",
            "excerpt": raw_text,
            "context_before": "",
            "context_after": "",
            "clause_id": f"{source_file_id}::c1",
            "source_type": "tender_original_fact",
        },
        "applicable_scope": {
            "scope_type": scope_type,
            "lot_name": LOT_NAME,
            "package_number": "包01",
            "applicable": True,
            "applicability_basis": {"text": "测试范围", "source_type": "system_judgement"},
            "human_review_required": False,
            "source_type": "system_judgement",
        },
        "compliance_level": compliance_level,
        "risk_type": risk_type,
        "risk_basis": {"text": "测试", "source_type": "system_judgement"},
        "related_score_item_ids": score_ids or [],
        "related_context_chapter_ids": chapter_ids or ["chapter-1"],
        "related_special_table_ids": related_special_table_ids or [],
        "external_reference": external_reference,
        "source_type": "tender_original_fact",
        "confidence": 0.9,
        "human_review_required": human_review_required,
        "human_review_questions": [],
        "status": "identified",
        "detected_risk_terms": ["否决其投标"] if "否决其投标" in raw_text else [],
        "detected_negation_terms": ["并不否决"] if "并不否决" in raw_text else [],
        "detected_condition_terms": ["未"] if raw_text.startswith("未") else [],
        "polarity": polarity,
        "classification_basis": {"text": "测试", "source_type": "system_judgement"},
    }


def matrix(requirements: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-07-13T00:00:00+00:00",
        "lot_name": LOT_NAME,
        "project_ref": {
            "context_path": "technical_response_context.json",
            "project": {"lot_name": LOT_NAME, "package_number": "包01"},
            "project_conflicts": [],
        },
        "source_files": [],
        "requirements": requirements,
        "requirement_conflicts": [],
        "unresolved_external_references": [],
        "global_manual_confirm_items": [],
        "statistics": {"requirement_count": len(requirements)},
        "build_warnings": [],
    }


def build_fixture(tmp_path: Path, requirements: list[dict]) -> tuple[dict, Path]:
    result = build_actionable_requirement_index(
        LOT_NAME,
        output_dir=tmp_path,
        matrix=matrix(requirements),
        update_context_index=False,
    )
    path = Path(result["index_path"])
    return json.loads(path.read_text(encoding="utf-8")), path


def test_equivalent_requirements_merge_and_keep_all_sources() -> None:
    left = requirement("req-a", "投标人应提交营业执照扫描件。", requirement_type="qualification_requirement", compliance_level="evidence_required", risk_type="qualification_risk")
    right = deepcopy(left)
    right["requirement_id"] = "req-b"
    right["source_ref"]["source_file_id"] = "file-b"
    right["source_ref"]["source_file_name"] = "file-b.docx"
    right["source_ref"]["clause_id"] = "file-b::c1"

    groups, ungrouped, _ = build_action_groups([left, right])

    assert not ungrouped
    assert len(groups) == 1
    assert groups[0]["member_requirement_ids"] == ["req-a", "req-b"]
    assert len(groups[0]["member_source_refs"]) == 2


def test_rejection_and_negative_exception_do_not_merge() -> None:
    rejected = requirement(
        "req-fatal",
        "未按要求提交的，否决其投标。",
        requirement_type="potential_invalidity_risk",
        compliance_level="fatal_if_missing",
        risk_type="bid_invalidity_risk",
        polarity="conditional_requirement",
    )
    exception = requirement(
        "req-exception",
        "该情形并不否决投标人的投标。",
        requirement_type="reference_only",
        compliance_level="reference_only",
        risk_type="none",
        polarity="negative_exception",
    )

    groups, _, _ = build_action_groups([rejected, exception])

    assert len(groups) == 2
    assert next(group for group in groups if "req-exception" in group["member_requirement_ids"])["action_kind"] == "no_direct_action"


def test_current_package_and_other_lot_do_not_merge() -> None:
    current = requirement("req-current", "当前包应提供合同关键页。", requirement_type="qualification_requirement", compliance_level="evidence_required", risk_type="qualification_risk")
    other = requirement("req-other", "其它标段：应提供合同关键页。", requirement_type="qualification_requirement", compliance_level="evidence_required", risk_type="qualification_risk")

    groups, _, _ = build_action_groups([current, other])

    assert len(groups) == 2
    other_group = next(group for group in groups if "req-other" in group["member_requirement_ids"])
    assert other_group["action_kind"] == "manual_confirm"
    assert other_group["human_review_required"] is True


def test_explicit_performance_quantity_is_rule_derived() -> None:
    item = requirement(
        "req-performance",
        "具有1项合格业绩得12分，每多提供一项合格业绩加2分，最高加8分。",
        requirement_type="score_related_requirement",
        compliance_level="score_related",
        risk_type="scoring_loss_risk",
    )

    rule = extract_quantity_rule(item)

    assert rule["rule_type"] == "score_accumulation"
    assert rule["required_count"] == 5
    assert rule["max_count_for_score"] == 5
    assert rule["score_per_item"] == 2.0
    assert rule["source_type"] == "rule_derived"


def test_missing_quantity_does_not_invent_required_count() -> None:
    rule = extract_quantity_rule(requirement("req-plan", "投标人应编写完整的技术方案。"))

    assert rule["rule_type"] == "not_explicit"
    assert rule["required_count"] is None
    assert rule["max_count_for_score"] is None


def test_upload_format_is_not_enterprise_evidence() -> None:
    item = requirement(
        "req-upload",
        "投标人应将技术文件制作成PDF并上传至交易平台。",
        requirement_type="format_submission_requirement",
        compliance_level="format_required",
        risk_type="format_risk",
    )

    groups, _, _ = build_action_groups([item])

    assert group_has_action(groups[0], "comply_format")
    assert not group_has_action(groups[0], "provide_evidence")


def test_qualification_and_performance_enter_provide_evidence() -> None:
    item = requirement(
        "req-evidence",
        "投标人应提供企业资质证书和类似项目合同关键页。",
        requirement_type="qualification_requirement",
        compliance_level="evidence_required",
        risk_type="qualification_risk",
    )

    groups, _, _ = build_action_groups([item])

    assert group_has_action(groups[0], "provide_evidence")
    assert {"enterprise_qualification", "project_performance", "contract"}.issubset(groups[0]["evidence_subject_types"])


def test_technical_plan_enters_write_response() -> None:
    item = requirement("req-write", "投标人应编写项目技术方案和实施计划。", requirement_type="technical_mandatory_requirement")

    groups, _, _ = build_action_groups([item])

    assert group_has_action(groups[0], "write_response")
    assert groups[0]["response_content_needed"]


def test_deviation_table_enters_fill_form() -> None:
    item = requirement(
        "req-form",
        "投标人应填写技术偏差表。",
        requirement_type="deviation_response_requirement",
        related_special_table_ids=["technical_deviation_table"],
    )

    groups, _, _ = build_action_groups([item])

    assert group_has_action(groups[0], "fill_form")
    assert groups[0]["form_or_table_refs"][0]["table_id"] == "technical_deviation_table"


def test_unresolved_external_reference_enters_resolve_action() -> None:
    item = requirement(
        "req-external",
        "投标人应按外部管理制度执行。",
        requirement_type="needs_review",
        compliance_level="needs_review",
        risk_type="unclear_reference_risk",
        external_reference={
            "reference_text": "外部管理制度",
            "resolved": False,
            "target_ref_ids": [],
            "human_review_required": True,
        },
        human_review_required=True,
    )

    groups, _, _ = build_action_groups([item])

    assert groups[0]["action_kind"] == "external_reference_resolve"
    assert group_has_action(groups[0], "manual_confirm")


def test_every_requirement_id_is_preserved() -> None:
    items = [requirement(f"req-{index}", f"投标人应提交第{index}项说明。") for index in range(3)]

    groups, _, _ = build_action_groups(items)

    indexed_ids = {requirement_id for group in groups for requirement_id in group["member_requirement_ids"]}
    assert indexed_ids == {"req-0", "req-1", "req-2"}


def test_markdown_is_display_only_and_not_needed_for_inspection(tmp_path: Path) -> None:
    payload, path = build_fixture(tmp_path, [requirement("req-a", "投标人应编写技术方案。")])
    markdown_path = path.with_suffix(".md")
    assert markdown_path.exists()
    markdown_path.unlink()

    inspected = inspect_actionable_requirement_index(LOT_NAME, output_dir=tmp_path)

    assert inspected["statistics"]["action_group_count"] == 1
    assert payload["generated_from"].endswith("tender_requirement_matrix.json")


def test_output_contains_no_formal_response_body(tmp_path: Path) -> None:
    payload, _ = build_fixture(tmp_path, [requirement("req-a", "投标人应编写技术方案。")])

    assert "formal_response" not in payload
    assert "draft_body" not in payload
    assert all("generated_content" not in group for group in payload["action_groups"])


def test_output_has_no_bid_decision_language(tmp_path: Path) -> None:
    payload, _ = build_fixture(tmp_path, [requirement("req-a", "投标人应编写技术方案。")])
    text = json.dumps(payload, ensure_ascii=False) + render_actionable_requirement_index_markdown(payload)

    for term in FORBIDDEN_DECISION_TERMS:
        assert term.lower() not in text.lower()

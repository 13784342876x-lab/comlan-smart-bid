from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import pytest

from app.llm_client import ModelCallResult
from app.technical_fulltext_semantic_audit import (
    DECISION_VERDICTS,
    RequirementObservationConsistencyError,
    TechnicalFulltextSemanticAuditor,
)


def _paragraph(
    paragraph_id: str,
    chapter_id: str,
    section_id: str,
    index: int,
    order: int,
    text: str,
) -> dict[str, Any]:
    normalized = " ".join(text.split())
    return {
        "paragraph_id": paragraph_id,
        "chapter_id": chapter_id,
        "section_id": section_id,
        "paragraph_index": index,
        "document_order": order,
        "text": text,
        "normalized_text": normalized,
        "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _calibration_audit(
    *,
    case_name: str,
    primary_heading: str,
    primary_block_type: str,
    primary_text: str,
    outside_heading: str,
    outside_block_type: str,
    outside_text: str,
    outside_markdown_heading: str | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, bool], str]:
    requirement_id = f"REQ-{case_name.upper()}"
    primary_section = "SEC-PRIMARY"
    outside_section = "SEC-CANDIDATE"
    paragraphs: list[dict[str, Any]] = []
    order = 0
    if primary_text:
        order += 1
        paragraphs.append(_paragraph("PAR-PRIMARY", "CH-PRIMARY", primary_section, 1, order, primary_text))
    if outside_markdown_heading:
        order += 1
        paragraphs.append(_paragraph("PAR-OUT-HEADING", "CH-OUTSIDE", outside_section, 1, order, outside_markdown_heading))
        outside_index = 2
    else:
        outside_index = 1
    if outside_text:
        order += 1
        paragraphs.append(_paragraph("PAR-OUT-BODY", "CH-OUTSIDE", outside_section, outside_index, order, outside_text))
    sections = [
        {
            "chapter_id": "CH-PRIMARY",
            "section_id": primary_section,
            "heading": primary_heading,
            "block_type": primary_block_type,
            "document_order": 1,
            "content": primary_text,
            "covered_requirement_ids": [requirement_id],
            "covered_score_item_ids": [],
            "placeholder_ids": [],
        },
        {
            "chapter_id": "CH-OUTSIDE",
            "section_id": outside_section,
            "heading": outside_heading,
            "block_type": outside_block_type,
            "document_order": 2,
            "content": outside_text,
            "covered_requirement_ids": [requirement_id],
            "covered_score_item_ids": [],
            "placeholder_ids": [],
        },
    ]
    planned = {
        "task_id": "TASK-PRIMARY",
        "target_path": ["Calibration", primary_heading],
        "mapping_role": "primary",
        "chapter_id": "CH-PRIMARY",
        "section_id": primary_section,
        "resolved": True,
    }
    audit = {
        "document_id": "calibration-document",
        "normalized_document": {
            "document": {},
            "chapters": [
                {"chapter_id": "CH-PRIMARY", "chapter_title": "Primary Chapter", "chapter_order": 1},
                {"chapter_id": "CH-OUTSIDE", "chapter_title": "Candidate Chapter", "chapter_order": 2},
            ],
            "sections": sections,
            "paragraphs": paragraphs,
            "claims": [], "placeholders": [], "requirements": [], "score_items": [],
        },
        "semantic_candidates": {
            "duplicate_pairs": [],
            "confirmation_aliases": [],
            "requirement_location_mismatches": [{
                "candidate_id": f"SAC-CAL-{case_name.upper()}",
                "candidate_type": "requirement_location_mismatch",
                "requirement_id": requirement_id,
                "outside_planned_locations": [{"chapter_id": "CH-OUTSIDE", "section_id": outside_section}],
                "planned_all_locations": [planned],
                "reason_codes": ["actual_location_outside_resolved_plan"],
            }],
            "score_distribution": [], "numeric_facts": [], "terminology": [],
        },
        "coverage_analysis": {
            "requirements": [{
                "requirement_id": requirement_id,
                "occurrence_count": 2,
                "chapter_ids": ["CH-PRIMARY", "CH-OUTSIDE"],
                "section_ids": [primary_section, outside_section],
                "actual_locations": [
                    {"chapter_id": "CH-PRIMARY", "section_id": primary_section},
                    {"chapter_id": "CH-OUTSIDE", "section_id": outside_section},
                ],
                "planned_primary_locations": [planned],
                "planned_supporting_locations": [],
                "planned_table_locations": [],
                "planned_all_locations": [planned],
                "outside_planned_locations": [{"chapter_id": "CH-OUTSIDE", "section_id": outside_section}],
                "planning_comparison_status": "completed",
            }],
            "score_items": [],
        },
    }
    context = {
        "response_outline": {
            "schema_version": "fixture",
            "response_points": [{"point_id": requirement_id, "original_text": "Calibration requirement text"}],
        },
        "draft_units": {
            "schema_version": "fixture",
            "draft_units": [{
                "draft_unit_id": "DU-CAL",
                "title": primary_heading,
                "chapter_id": "CH-PRIMARY",
                "task_ids": ["TASK-PRIMARY"],
                "response_point_ids": [requirement_id],
                "content_mode": "narrative",
                "generation_constraints": {
                    "primary_content_locations": [{"task_id": "TASK-PRIMARY", "target_path": [primary_heading], "mapping_role": "primary"}],
                    "supporting_reference_locations": [],
                    "table_response_locations": [],
                    "avoid_repeating_topics": [],
                    "transition_required": False,
                },
            }],
        },
    }
    trust = {"response_outline": True, "draft_units": True, "writing_outline": False}
    return audit, context, trust, requirement_id


class CalibrationFakeLLM:
    def generate_structured(self, **kwargs: Any) -> ModelCallResult:
        assert "primary location is the intended location" in kwargs["system_prompt"]
        assert "outside recorded plan -> misplaced" in kwargs["system_prompt"]
        assert "Runtime owns all formal decisions" in kwargs["system_prompt"]
        batch = json.loads(kwargs["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0])
        unit = batch["units"][0]
        content = unit["content"]
        assert content["recorded_planning_context"]["is_exclusive_location_contract"] is False
        assert content["candidate_trigger"]["is_issue_by_itself"] is False
        case_name = unit["source_candidate_ids"][0].lower().removeprefix("sac-cal-")
        if case_name in {"warranty_duplicate", "sla_duplicate"}:
            primary_status, outside_role, outside_complete = "adequate", "independent_response", "yes"
        elif case_name == "empty_primary_full_elsewhere":
            primary_status, outside_role, outside_complete = "missing", "independent_response", "yes"
        elif case_name == "empty_primary_generic":
            primary_status, outside_role, outside_complete = "missing", "generic_promise", "no"
        elif case_name == "insufficient":
            primary_status, outside_role, outside_complete = "uncertain", "uncertain", "uncertain"
        elif case_name in {"certification", "manufacturer", "point_table"}:
            primary_status, outside_role, outside_complete = "adequate", "evidence_reference", "no"
        else:
            primary_status, outside_role, outside_complete = "adequate", "contextual_mention", "no"
        evidence = "insufficient" if case_name == "insufficient" else "sufficient"
        result = {
            "audit_unit_id": unit["audit_unit_id"],
            "evidence_sufficiency": evidence,
            "primary_response_status": primary_status,
            "actual_location_assessments": [
                {
                    "assessment_id": item["assessment_id"],
                    "semantic_role": "primary_response" if item["is_planned_primary"] else outside_role,
                    "substantive_level": (
                        "substantive" if item["is_planned_primary"] and primary_status == "adequate"
                        else "none" if item["is_planned_primary"] and primary_status == "missing"
                        else "substantive" if outside_complete == "yes"
                        else "uncertain" if outside_role == "uncertain"
                        else "partial"
                    ),
                    "independent_complete_response": "no" if item["is_planned_primary"] else outside_complete,
                }
                for item in content["all_actual_locations"]
            ],
            "confidence": "high" if evidence == "sufficient" else "low",
            "subject": {"subject_type": "requirement", "canonical_key": unit["allowed_requirement_ids"][0], "display_name": "Calibration"},
            "requirement_ids": unit["allowed_requirement_ids"],
            "summary": "Calibration observation",
            "rationale": "Observed primary sufficiency and every location role.",
            "details": {"values": [], "comparison": "observation", "evidence_notes": []},
            "recommended_action": {"resolution_kind": "review", "text": "Review calibrated ownership."},
        }
        return ModelCallResult(
            provider="fake", protocol="fake", profile_name="deepseek_draft",
            model="fake", api_mode="structured", request_id="fake", started_at="x",
            completed_at="x", duration_ms=1,
            parsed_output={"batch_id": batch["batch_id"], "pass_type": batch["pass_type"], "results": [result]},
            raw_output_available=False, finish_status="completed", retry_count=0,
            repair_attempted=False, usage={}, warnings=[],
        )


@pytest.mark.parametrize(
    ("case_name", "primary_heading", "primary_block", "primary_text", "outside_heading", "outside_block", "outside_text", "markdown_heading", "expected"),
    [
        ("timeline", "交付成果", "service_commitment", "完整交付成果响应，明确采购人指定地点。", "项目设计实施周期", "implementation_plan", "交货期30日，安装10日，交货地点为采购人指定地点。", None, "acceptable_reference"),
        ("certification", "产品技术资料", "evidence_explanation", "完整产品技术资料响应。", "产品检测及认证材料索引", "evidence_explanation", "检测、检验、试验、测试报告及第三方认证证书索引。", "### 产品检测报告", "acceptable_reference"),
        ("manufacturer", "产品技术资料", "evidence_explanation", "完整产品资料响应。", "制造商资质材料索引", "evidence_explanation", "制造商质量、环境、信息安全体系认证材料索引。", "### 制造商概况", "acceptable_reference"),
        ("point_table", "技术要求点对点应答表", "required_response_table", "完整点对点参数正式响应。", "技术支撑材料索引", "evidence_explanation", "为点对点应答中的参数承诺建立证据索引。", None, "acceptable_reference"),
        ("warranty_duplicate", "保修与维修方案", "requirement_response", "完整正式响应：免费保修、终身维修及保修责任。", "服务承诺", "service_commitment", "完整正式响应：免费保修、终身维修及保修责任。", None, "redundant_response"),
        ("sla_duplicate", "服务响应机制", "service_commitment", "完整正式响应：7×24小时响应、24小时工程师到场。", "服务承诺", "service_commitment", "完整正式响应：7×24小时响应、24小时工程师到场。", None, "redundant_response"),
        ("empty_primary_generic", "服务机构与人员", "organization_plan", "", "服务承诺", "service_commitment", "制造商服务机构地址、联系方式、人员数量等依据招标文件要求提供。", None, "coverage_content_mismatch"),
        ("empty_primary_full_elsewhere", "主体方案", "organization_plan", "", "错误职责章节", "requirement_response", "完整主体响应，包含全部可独立评审内容与明确承诺。", None, "misplaced_content"),
        ("insufficient", "主体方案", "organization_plan", "", "一般说明", "general", "可能相关，但现有上下文无法判断是否构成响应。", None, "uncertain"),
    ],
)
def test_requirement_location_calibration_contract(
    case_name: str,
    primary_heading: str,
    primary_block: str,
    primary_text: str,
    outside_heading: str,
    outside_block: str,
    outside_text: str,
    markdown_heading: str | None,
    expected: str,
) -> None:
    audit, context, trust, requirement_id = _calibration_audit(
        case_name=case_name,
        primary_heading=primary_heading,
        primary_block_type=primary_block,
        primary_text=primary_text,
        outside_heading=outside_heading,
        outside_block_type=outside_block,
        outside_text=outside_text,
        outside_markdown_heading=markdown_heading,
    )
    service = TechnicalFulltextSemanticAuditor(llm_client=CalibrationFakeLLM())
    prompt = service._system_prompt("requirement_location")
    assert "formal service-commitment or commitment-summary section is NOT automatically a harmless reference" in prompt
    assert "Do not use acceptable_reference merely because the second response is shorter" in prompt
    assert "direct response intent" in prompt
    assert "standalone satisfaction" in prompt
    assert "Do not apply a universal narrative-prose adequacy test" in prompt
    assert "dominant section/paragraph purpose" in prompt
    assert "dedicated child/sibling evidence subsection" in prompt
    units, _ = service._build_units(audit, context=context, trust=trust)
    unit = units["requirement_location"][0]
    outside_context = next(
        item for item in unit.payload["all_actual_locations"]
        if item["section_id"] == "SEC-CANDIDATE"
    )
    assert all(not item["excerpt"].lstrip().startswith("#") for item in outside_context["relevant_excerpts"])
    schema = service._load_result_schema()
    results, _ = service._call_batch(
        client=service.llm_client,
        audit={"document_id": "calibration-document"},
        batch_id=f"FSB-CAL-{case_name.upper()}",
        pass_type="requirement_location",
        units=[unit],
        response_schema=schema,
    )
    assert unit.requirement_ids == [requirement_id]
    assert results[0]["decision"] == expected
    assert results[0]["verdict"] == DECISION_VERDICTS["requirement_location"][expected]


@pytest.mark.parametrize(
    ("requirement_text", "content_mode", "primary_block_type", "planned_table", "expected_form", "table_visibility"),
    [
        (
            "提供ISO认证证明材料及有效证书扫描件",
            "appendix",
            "evidence_explanation",
            False,
            "index_response",
            "not_applicable",
        ),
        (
            "详细说明项目实施方案",
            "narrative",
            "evidence_explanation",
            False,
            "narrative_response",
            "not_applicable",
        ),
        (
            "完整填写技术要求点对点应答表",
            "table",
            "required_response_table",
            True,
            "table_response",
            "summary_only",
        ),
    ],
)
def test_expected_response_form_uses_existing_planning_metadata(
    requirement_text: str,
    content_mode: str,
    primary_block_type: str,
    planned_table: bool,
    expected_form: str,
    table_visibility: str,
) -> None:
    context = TechnicalFulltextSemanticAuditor._expected_requirement_response_form(
        requirement_metadata={"node_type": "clause", "content_type": "paragraph"},
        requirement_text=requirement_text,
        requirement_planning={"content_modes": [content_mode]},
        planned_primary_contexts=[{
            "section_semantic_role": TechnicalFulltextSemanticAuditor._section_semantic_role({
                "heading": "主响应位置", "block_type": primary_block_type,
            }),
            "block_type": primary_block_type,
        }],
        planned_table_locations=[{"section_id": "SEC-TABLE"}] if planned_table else [],
    )
    assert context["expected_response_form"] == expected_form
    assert context["table_content_visibility"] == table_visibility
    assert context["material_truth_status_is_separate"] is True


def test_requirement_unit_exposes_response_form_context() -> None:
    audit, context, trust, _ = _calibration_audit(
        case_name="evidence_form",
        primary_heading="制造商资质材料索引",
        primary_block_type="evidence_explanation",
        primary_text="ISO9001、ISO14001、ISO27001认证材料索引，实际证书待人工确认。",
        outside_heading="技术资料",
        outside_block_type="evidence_explanation",
        outside_text="相关认证材料见制造商资质材料索引。",
    )
    context["response_outline"]["response_points"][0]["original_text"] = "提供ISO认证证明材料"
    context["draft_units"]["draft_units"][0]["content_mode"] = "appendix"
    service = TechnicalFulltextSemanticAuditor(llm_client=CalibrationFakeLLM())
    unit = service._build_units(audit, context=context, trust=trust)[0]["requirement_location"][0]
    response_form = unit.payload["expected_response_form_context"]
    assert response_form["expected_response_form"] == "index_response"
    assert response_form["material_truth_status_is_separate"] is True
    assert unit.payload["requirement_source_metadata"]["content_type"] is None
    resolution = unit.payload["planned_response_responsibility_resolution"]
    assert resolution["recorded_primary_locations_are_exclusive"] is False
    assert {item["section_id"] for item in resolution["specialized_actual_evidence_sections"]} == {
        "SEC-PRIMARY", "SEC-CANDIDATE",
    }
    assert unit.payload["required_assessment_ids"] == [
        item["assessment_id"] for item in unit.payload["all_actual_locations"]
    ]


def test_table_summary_context_requires_safe_insufficient_exit_in_prompt() -> None:
    prompt = TechnicalFulltextSemanticAuditor._system_prompt("requirement_location")
    assert "only a prose summary is visible" in prompt
    assert "evidence_sufficiency=insufficient" in prompt
    assert "never missing merely because Markdown omitted the cells" in prompt


def test_requirement_unit_reuses_existing_tender_table_cells_without_claiming_response_visibility() -> None:
    audit, context, trust, _ = _calibration_audit(
        case_name="source_table_cells",
        primary_heading="Point-by-point response table",
        primary_block_type="required_response_table",
        primary_text="The response table is pending confirmed product values.",
        outside_heading="Source requirement reference",
        outside_block_type="general",
        outside_text="Tender parameter source reference.",
    )
    point = context["response_outline"]["response_points"][0]
    point.update({
        "node_type": "table",
        "content_type": "table",
        "source_refs": [{
            "table_name": "Procurement requirements",
            "table_range": "A1:C2",
        }],
        "table": {
            "rows": [
                {"row": 1, "cells": [
                    {"row": 1, "column": 1, "text": "Item"},
                    {"row": 1, "column": 2, "text": "Parameter"},
                    {"row": 1, "column": 3, "text": "Quantity"},
                ]},
                {"row": 2, "cells": [
                    {"row": 2, "column": 1, "text": "Node"},
                    {"row": 2, "column": 2, "text": "CPU >= 64 cores"},
                    {"row": 2, "column": 3, "text": "8"},
                ]},
            ]
        },
    })

    service = TechnicalFulltextSemanticAuditor(llm_client=CalibrationFakeLLM())
    unit = service._build_units(audit, context=context, trust=trust)[0][
        "requirement_location"
    ][0]
    source_table = unit.payload["requirement_source_metadata"]["source_table"]

    assert source_table["visibility"] == "structured_cells"
    assert source_table["source_role"] == "tender_requirement_source_only"
    assert source_table["is_bid_response_evidence"] is False
    assert source_table["row_count"] == 2
    assert source_table["column_count"] == 3
    assert source_table["table_range"] == "A1:C2"
    assert {item["text"] for item in source_table["cells"]} >= {
        "Parameter", "CPU >= 64 cores", "8"
    }
    assert unit.payload["expected_response_form_context"][
        "table_content_visibility"
    ] == "summary_only"


def test_inconsistent_requirement_observation_is_rejected() -> None:
    audit, context, trust, _ = _calibration_audit(
        case_name="inconsistent",
        primary_heading="主体响应",
        primary_block_type="requirement_response",
        primary_text="完整主体响应。",
        outside_heading="上下文说明",
        outside_block_type="general",
        outside_text="用于上下文说明。",
    )
    service = TechnicalFulltextSemanticAuditor(llm_client=CalibrationFakeLLM())
    units, _ = service._build_units(audit, context=context, trust=trust)
    unit = units["requirement_location"][0]
    observation = {
        "audit_unit_id": unit.audit_unit_id,
        "evidence_sufficiency": "sufficient",
        "primary_response_status": "adequate",
        "actual_location_assessments": [
            {
                "assessment_id": item["assessment_id"],
                "semantic_role": "primary_response" if item["is_planned_primary"] else "contextual_mention",
                "substantive_level": "substantive" if item["is_planned_primary"] else "partial",
                "independent_complete_response": "no" if item["is_planned_primary"] else "yes",
            }
            for item in unit.payload["all_actual_locations"]
        ],
        "confidence": "high",
        "subject": {"subject_type": "requirement", "canonical_key": unit.requirement_ids[0], "display_name": "Fixture"},
        "requirement_ids": unit.requirement_ids,
        "summary": "Inconsistent fixture",
        "rationale": "Fixture",
        "details": {"values": [], "comparison": "fixture", "evidence_notes": []},
        "recommended_action": {"resolution_kind": "review", "text": "Review."},
    }
    with pytest.raises(RequirementObservationConsistencyError):
        service._validate_requirement_observation(observation, unit)


def test_different_requirement_units_have_distinct_fingerprints() -> None:
    audit_a, context_a, trust_a, _ = _calibration_audit(
        case_name="cache_a", primary_heading="主体", primary_block_type="requirement_response",
        primary_text="完整响应。", outside_heading="引用", outside_block_type="general", outside_text="辅助引用。",
    )
    audit_b, context_b, trust_b, _ = _calibration_audit(
        case_name="cache_b", primary_heading="主体", primary_block_type="requirement_response",
        primary_text="完整响应。", outside_heading="引用", outside_block_type="general", outside_text="辅助引用。",
    )
    service = TechnicalFulltextSemanticAuditor(llm_client=CalibrationFakeLLM())
    unit_a = service._build_units(audit_a, context=context_a, trust=trust_a)[0]["requirement_location"][0]
    unit_b = service._build_units(audit_b, context=context_b, trust=trust_b)[0]["requirement_location"][0]
    assert unit_a.requirement_ids != unit_b.requirement_ids
    assert unit_a.semantic_fingerprint() != unit_b.semantic_fingerprint()

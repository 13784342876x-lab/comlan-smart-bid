from __future__ import annotations

import hashlib
from typing import Any

import pytest

from app.technical_fulltext_semantic_audit import (
    SemanticAuditUnit,
    TechnicalFulltextSemanticAuditor,
)
from app.technical_fulltext_stable_fact_consistency import (
    PASS_TYPE,
    extract_stable_fact_candidates,
)


def _normalized(*rows: tuple[str, str, str, str]) -> dict[str, Any]:
    paragraphs: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    for index, (chapter_id, section_id, requirement_id, text) in enumerate(rows, 1):
        paragraphs.append(
            {
                "paragraph_id": f"PAR-{index}",
                "chapter_id": chapter_id,
                "section_id": section_id,
                "paragraph_index": index,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        sections.append(
            {
                "chapter_id": chapter_id,
                "section_id": section_id,
                "covered_requirement_ids": [requirement_id],
            }
        )
    return {"paragraphs": paragraphs, "sections": sections}


def _only(*rows: tuple[str, str, str, str]) -> dict[str, Any]:
    candidates = extract_stable_fact_candidates(_normalized(*rows))
    assert len(candidates) == 1
    return candidates[0]


def test_same_cross_chapter_service_duration_is_consistent() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", "本项目服务期限为3年。"),
        ("CH-7", "SEC-7", "REQ-B", "售后条款确认服务期限为36个月。"),
    )

    assert candidate["attribute"] == "service_duration"
    assert candidate["normalized_values"] == ["36 month"]
    assert candidate["deterministic_decision"] == "consistent"


def test_cross_chapter_service_duration_three_vs_five_years_conflicts() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", "本项目服务期限为3年。"),
        ("CH-7", "SEC-7", "REQ-B", "服务承诺中的服务期限为5年。"),
    )

    assert candidate["deterministic_decision"] == "stable_fact_conflict"
    assert set(candidate["requirement_ids"]) == {"REQ-A", "REQ-B"}
    assert len(candidate["chapter_ids"]) == 2


def test_response_time_and_warranty_are_different_attributes() -> None:
    candidates = extract_stable_fact_candidates(
        _normalized(
            ("CH-2", "SEC-2", "REQ-A", "响应时间为24小时。"),
            ("CH-7", "SEC-7", "REQ-B", "质保期为3年。"),
        )
    )

    assert candidates == []


def test_different_personnel_phases_are_context_different() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", "部署阶段人员数量为10人。"),
        ("CH-7", "SEC-7", "REQ-B", "运维阶段人员数量为5人。"),
    )

    assert candidate["attribute"] == "personnel_quantity"
    assert candidate["deterministic_decision"] == "context_different"


def test_same_product_with_different_models_conflicts() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", "超融合节点产品型号为HX-1000。"),
        ("CH-7", "SEC-7", "REQ-B", "超融合节点产品型号为HX-2000。"),
    )

    assert candidate["fact_type"] == "product_identity"
    assert candidate["attribute"] == "product_model"
    assert candidate["deterministic_decision"] == "stable_fact_conflict"


def test_same_model_across_chapters_is_consistent() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", "超融合节点产品型号为HX-1000。"),
        ("CH-7", "SEC-7", "REQ-B", "超融合节点产品型号为 HX-1000。"),
    )

    assert candidate["deterministic_decision"] == "consistent"


@pytest.mark.parametrize(
    ("left", "right", "attribute"),
    [
        ("交货周期为30日。", "交货周期为45日。", "delivery_cycle"),
        ("合同金额为100万元。", "合同金额为1200000元。", "contract_amount"),
        ("付款比例为30%。", "付款比例为40%。", "payment_percentage"),
        ("超融合节点内存容量为512GB。", "超融合节点内存容量为1TB。", "memory_capacity"),
        ("提供7×24小时服务。", "提供5×8小时服务。", "service_schedule"),
        ("公司地址为天津市滨海新区一号路。", "公司地址为天津市滨海新区二号路。", "company_address"),
    ],
)
def test_supported_stable_fact_families_conflict(
    left: str, right: str, attribute: str
) -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", left),
        ("CH-7", "SEC-7", "REQ-B", right),
    )

    assert candidate["attribute"] == attribute
    assert candidate["deterministic_decision"] == "stable_fact_conflict"


def test_same_company_fact_is_consistent() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-A", "公司名称为智标科技有限公司。"),
        ("CH-7", "SEC-7", "REQ-B", "公司名称为 智标科技有限公司。"),
    )

    assert candidate["fact_type"] == "company_stable_fact"
    assert candidate["deterministic_decision"] == "consistent"


def test_placeholder_identity_is_not_treated_as_a_confirmed_fact() -> None:
    candidates = extract_stable_fact_candidates(
        _normalized(
            ("CH-2", "SEC-2", "REQ-A", "超融合节点产品型号为{{PH-FIELD-MODEL-A}}。"),
            ("CH-7", "SEC-7", "REQ-B", "超融合节点产品型号为{{PH-FIELD-MODEL-B}}。"),
        )
    )

    assert candidates == []


def test_conflict_finding_contains_all_locations_values_and_requirements() -> None:
    candidate = _only(
        ("CH-2", "SEC-2", "REQ-ALPHA", "本项目服务期限为3年。"),
        ("CH-7", "SEC-7", "REQ-OMEGA", "本项目服务期限为5年。"),
    )
    locations = []
    for fact in candidate["facts"]:
        locations.append(
            {
                "location_id": "LOC-" + fact["fact_id"][4:],
                "chapter_id": fact["chapter_id"],
                "chapter_title": fact["chapter_id"],
                "section_id": fact["section_id"],
                "section_heading": fact["section_id"],
                "paragraph_id": fact["paragraph_id"],
                "paragraph_index": fact["paragraph_index"],
                "excerpt": fact["excerpt"],
                "text_sha256": fact["text_sha256"],
            }
        )
    unit = SemanticAuditUnit(
        audit_unit_id="FSU-STABLE-TEST",
        pass_type=PASS_TYPE,
        source_candidate_ids=[candidate["candidate_id"]],
        payload={**candidate, "facts": candidate["facts"]},
        locations=locations,
        requirement_ids=candidate["requirement_ids"],
        score_item_ids=[],
        placeholder_ids=[],
    )

    result = TechnicalFulltextSemanticAuditor._deterministic_stable_fact_result(unit)
    assert result is not None
    finding = TechnicalFulltextSemanticAuditor._to_finding(
        pass_type=PASS_TYPE, unit=unit, result=result
    )

    assert finding["code"] == "stable_fact_conflict"
    assert finding["verdict"] == "issue"
    assert set(finding["requirement_ids"]) == {"REQ-ALPHA", "REQ-OMEGA"}
    assert {item["chapter_id"] for item in finding["locations"]} == {"CH-2", "CH-7"}
    assert "3年" in finding["summary"] and "5年" in finding["summary"]
    assert finding["recommended_action"]["resolution_kind"] == "resolve_stable_fact_conflict"


def test_candidate_identity_does_not_depend_on_tr_id_hardcoding() -> None:
    first = _only(
        ("CH-2", "SEC-2", "ARBITRARY-ONE", "本项目服务期限为3年。"),
        ("CH-7", "SEC-7", "ARBITRARY-TWO", "本项目服务期限为5年。"),
    )
    second = _only(
        ("CH-2", "SEC-2", "OTHER-ONE", "本项目服务期限为3年。"),
        ("CH-7", "SEC-7", "OTHER-TWO", "本项目服务期限为5年。"),
    )

    assert first["candidate_id"] == second["candidate_id"]
    assert first["deterministic_decision"] == second["deterministic_decision"]

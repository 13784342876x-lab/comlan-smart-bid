from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.evidence_coverage_builder import (
    apply_complete_package_support,
    build_evidence_packages,
    build_requirement_coverage,
    build_score_item_coverage,
    build_warnings,
    dedupe_evidence_items,
    ensure_no_decision_terms,
    evaluate_support_relation,
    inspect_evidence_coverage,
    not_explicit_quantity_rule,
    render_evidence_coverage_markdown,
    validate_coverage_matrix,
)


LOT_NAME = "0425139003001-6582"


def evidence(
    evidence_id: str,
    evidence_type: str,
    *,
    file_id: str = "file-a",
    title: str = "测试材料",
    excerpt: str = "可定位的材料摘录",
    project: str = "项目A",
    certificate: str = "",
    person: str = "",
    identity_status: str = "identified",
    human_review_required: bool = False,
    valid_until: str | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "title": title,
        "normalized_title": title.lower(),
        "source_file_id": file_id,
        "source_file_name": f"{file_id}.docx",
        "source_location": {"clause_path": "测试材料"},
        "location_precision": "clause_approximate",
        "excerpt": excerpt,
        "structured_facts": {"certificate_number": certificate} if certificate else {},
        "related_enterprise": "测试企业",
        "related_person": person,
        "related_project": project,
        "related_certificate": certificate,
        "related_qualification": title,
        "issue_date": None,
        "valid_from": None,
        "valid_until": valid_until,
        "evidence_owner": person or "测试企业",
        "source_type": "enterprise_existing_fact",
        "extraction_confidence": 0.9,
        "identity_status": identity_status,
        "human_review_required": human_review_required,
        "human_review_questions": ["请人工确认。"] if human_review_required else [],
        "physical_file_exists": True,
    }


def requirement(
    requirement_id: str = "evidence-requirement-a",
    *,
    subjects: list[str] | None = None,
    required_count: int | None = None,
    score_id: str = "technical_04_业绩情况",
) -> dict:
    quantity = not_explicit_quantity_rule()
    if required_count is not None:
        quantity = {
            "rule_type": "fixed_count",
            "required_count": required_count,
            "max_count_for_score": required_count,
            "score_per_item": None,
            "raw_rule": f"明确需要{required_count}项",
            "derived_from_requirement_ids": ["req-source"],
            "source_type": "rule_derived",
        }
    return {
        "evidence_requirement_id": requirement_id,
        "canonical_title": "业绩情况",
        "requirement_origin": "score_item",
        "source_score_item_ids": [score_id] if score_id else [],
        "source_action_group_ids": ["action-a"],
        "source_requirement_ids": ["req-source"],
        "expected_evidence_subject_types": subjects or ["project_performance", "contract", "acceptance_certificate"],
        "expected_evidence_types": ["合同关键页", "验收证明"],
        "required_conditions": [],
        "quantity_rule": quantity,
        "quality_rule": {"conditions": [], "source_type": "system_judgement", "status": "not_explicit"},
        "time_conditions": [],
        "validity_conditions": [],
        "subject_conditions": [],
        "relevance_conditions": [],
        "related_context_chapter_ids": ["chapter-4"],
        "priority": "high",
        "source_type": "rule_derived" if required_count is not None else "system_judgement",
        "human_review_required": False,
        "human_review_questions": [],
    }


def relation(evidence_id: str, *, status: str = "confirmed", strength: str = "strong", eligible: bool = True) -> dict:
    return {
        "support_id": f"support-{evidence_id}",
        "evidence_requirement_id": "evidence-requirement-a",
        "evidence_id": evidence_id,
        "matched_score_item_ids": ["technical_04_业绩情况"],
        "matched_action_group_ids": ["action-a"],
        "matched_requirement_ids": ["req-source"],
        "match_basis": [{"basis_type": "类型匹配", "detail": "project_performance contract", "source_type": "system_judgement"}],
        "matched_conditions": [],
        "unmet_conditions": [],
        "support_strength": strength,
        "usability_status": status,
        "confidence": 0.9,
        "quantity_eligible": eligible,
        "can_be_used_in_writing": status == "confirmed",
        "permitted_claims": [],
        "restricted_claims": [],
        "rejection_reasons": [],
        "human_review_required": status == "needs_review",
        "human_review_questions": [],
        "source_type": "system_judgement",
    }


def test_legacy_matched_is_not_promoted_to_confirmed() -> None:
    warnings = build_warnings(
        {"material_analysis": {"matched_materials": [{"match_status": "matched"}]}},
        {"build_warnings": [], "evidence_items": []},
        [],
    )

    assert any("match_status=matched 未用于 confirmed" in item for item in warnings)


def test_resume_project_experience_is_not_company_performance() -> None:
    item = evidence("resume-a", "personnel_resume", person="张三")
    item["structured_facts"] = {"projects": [{"name": "项目A"}], "availability_status": "unknown"}

    support = evaluate_support_relation(requirement(), item)

    assert support is not None
    assert support["support_strength"] == "none"
    assert support["usability_status"] == "unusable"
    assert any("不能作为企业合同业绩" in reason for reason in support["rejection_reasons"])


def test_contract_and_acceptance_form_complete_package() -> None:
    contract = evidence("contract-a", "contract")
    acceptance = evidence("acceptance-a", "acceptance_certificate", file_id="file-b")
    catalog = {"evidence_items": [contract, acceptance]}
    supports = [relation("contract-a", status="candidate", eligible=False), relation("acceptance-a", status="candidate", eligible=False)]

    packages = build_evidence_packages(catalog, supports)
    apply_complete_package_support(supports, packages, catalog)

    assert packages[0]["package_completeness"] == "complete"
    promoted = next(item for item in supports if item["evidence_id"] == "contract-a")
    assert promoted["usability_status"] == "confirmed"
    assert promoted["quantity_eligible"] is True


def test_contract_only_is_incomplete_package() -> None:
    packages = build_evidence_packages({"evidence_items": [evidence("contract-a", "contract")]}, [])

    assert packages[0]["package_completeness"] == "incomplete"
    assert "中标通知书、验收证明或用户证明至少一类" in packages[0]["missing_package_components"]


def test_five_performance_count_uses_independent_confirmed_packages() -> None:
    items = [evidence(f"contract-{index}", "contract", project=f"项目{index}") for index in range(5)]
    supports = [relation(item["evidence_id"]) for item in items]
    req = requirement(required_count=5)
    score = {"id": "technical_04_业绩情况", "item_name": "业绩情况", "score_range": {"max": 20}, "score_rule": "满分5项"}

    coverage = build_score_item_coverage([score], [req], supports, {"evidence_items": items}, [])[0]

    assert coverage["confirmed_count"] == 5
    assert coverage["remaining_confirmed_gap"] == 0


def test_candidate_and_weak_do_not_reduce_confirmed_gap() -> None:
    items = [evidence("candidate-a", "contract"), evidence("weak-a", "contract", project="项目B")]
    supports = [relation("candidate-a", status="candidate", eligible=False), relation("weak-a", status="candidate", strength="weak", eligible=False)]
    req = requirement(required_count=5)
    score = {"id": "technical_04_业绩情况", "item_name": "业绩情况", "score_range": {"max": 20}, "score_rule": "满分5项"}

    coverage = build_score_item_coverage([score], [req], supports, {"evidence_items": items}, [])[0]

    assert coverage["confirmed_count"] == 0
    assert coverage["remaining_confirmed_gap"] == 5


def test_same_patent_is_counted_once() -> None:
    left = evidence("patent-a", "patent", certificate="CN123456", project="")
    right = evidence("patent-b", "patent", file_id="file-b", certificate="CN123456", project="")
    req = requirement(subjects=["patent"], required_count=2, score_id="technical_08_专利数量")
    supports = [relation("patent-a"), relation("patent-b")]
    for item in supports:
        item["matched_score_item_ids"] = ["technical_08_专利数量"]
    score = {"id": "technical_08_专利数量", "item_name": "专利数量", "score_range": {"max": 2}, "score_rule": "上限2项"}

    coverage = build_score_item_coverage([score], [req], supports, {"evidence_items": [left, right]}, [])[0]

    assert coverage["confirmed_count"] == 1
    assert coverage["remaining_confirmed_gap"] == 1


@pytest.mark.parametrize("valid_until", [(date.today() - timedelta(days=1)).isoformat(), None])
def test_expired_or_unknown_certificate_is_not_confirmed(valid_until: str | None) -> None:
    cert = evidence("cert-a", "management_system_certificate", title="ISO 9001质量管理体系认证证书", valid_until=valid_until)
    req = requirement(subjects=["management_system_certificate"], score_id="business_05_绿色管理体系认证")

    support = evaluate_support_relation(req, cert)

    assert support is not None
    assert support["usability_status"] != "confirmed"


def test_personnel_department_and_availability_require_review() -> None:
    resume = evidence("resume-a", "personnel_resume", person="张三")
    resume["structured_facts"] = {"department_confirm_status": "rule_inferred", "availability_status": "unknown"}
    req = requirement(subjects=["personnel_resume"], score_id="technical_02_项目负责人情况")

    support = evaluate_support_relation(req, resume)

    assert support is not None
    assert support["usability_status"] == "needs_review"
    assert support["human_review_required"] is True


def test_not_explicit_quantity_has_no_required_count_or_gap() -> None:
    req = requirement(required_count=None)
    coverage = build_requirement_coverage([req], [], {"evidence_items": []}, [])[0]

    assert coverage["required_count"] is None
    assert coverage["remaining_confirmed_gap"] is None


def test_support_relation_has_basis_and_source_evidence() -> None:
    support = evaluate_support_relation(requirement(subjects=["contract"]), evidence("contract-a", "contract"))

    assert support is not None
    assert support["evidence_id"] == "contract-a"
    assert support["match_basis"]


def test_confirmed_support_requires_traceable_source() -> None:
    item = evidence("contract-a", "contract", excerpt="")
    matrix = {
        "support_relations": [relation("contract-a")],
        "score_item_coverage": [],
        "evidence_packages": [],
    }

    with pytest.raises(ValueError, match="traceable source"):
        validate_coverage_matrix(matrix, {"evidence_items": [item]})


def test_wrong_material_type_is_unusable() -> None:
    support = evaluate_support_relation(requirement(), evidence("resume-a", "personnel_certificate", person="张三"))

    assert support is not None
    assert support["usability_status"] == "unusable"


def test_same_file_duplicate_records_are_safely_deduplicated() -> None:
    first = evidence("cert-a", "personnel_certificate", title="ACP", certificate="CERT-1", person="张三")
    second = deepcopy(first)
    second["evidence_id"] = "cert-b"
    second["related_certificate"] = ""
    second["structured_facts"] = {}

    deduped, duplicate_groups, _ = dedupe_evidence_items([first, second])

    assert len(deduped) == 1
    assert len(duplicate_groups) == 1


def test_different_files_with_uncertain_identity_are_not_merged() -> None:
    first = evidence("cert-a", "personnel_certificate", title="ACP", certificate="CERT-1", person="张三")
    second = evidence("cert-b", "personnel_certificate", file_id="file-b", title="ACP", certificate="CERT-1", person="张三")

    deduped, _, conflicts = dedupe_evidence_items([first, second])

    assert len(deduped) == 2
    assert len(conflicts) == 1


def test_unnamed_certificate_images_in_same_file_are_not_merged() -> None:
    first = evidence("cert-a", "personnel_certificate", title="待识别人员证书", certificate="", person="张三", project="", identity_status="needs_review")
    second = deepcopy(first)
    second["evidence_id"] = "cert-b"

    deduped, duplicate_groups, _ = dedupe_evidence_items([first, second])

    assert len(deduped) == 2
    assert not duplicate_groups


def test_research_team_count_is_deduplicated_by_person() -> None:
    resume = evidence("resume-a", "personnel_resume", person="张三")
    cert_a = evidence("cert-a", "personnel_certificate", person="张三", certificate="A-1")
    cert_b = evidence("cert-b", "personnel_certificate", person="张三", certificate="B-1")
    req = requirement(subjects=["personnel_resume", "personnel_certificate"], required_count=30, score_id="business_10_研发团队规模")
    supports = [relation(item["evidence_id"], status="needs_review", eligible=False) for item in [resume, cert_a, cert_b]]
    for item in supports:
        item["matched_score_item_ids"] = ["business_10_研发团队规模"]
    score = {"id": "business_10_研发团队规模", "item_name": "研发团队规模", "score_range": {"max": 3}, "score_rule": "不少于30人"}

    coverage = build_score_item_coverage([score], [req], supports, {"evidence_items": [resume, cert_a, cert_b]}, [])[0]

    assert coverage["needs_review_count"] == 1
    assert coverage["remaining_confirmed_gap"] == 30


def test_markdown_is_display_only_and_inspection_reads_json(tmp_path: Path) -> None:
    evidence_dir = tmp_path / LOT_NAME / "evidence"
    evidence_dir.mkdir(parents=True)
    matrix = {
        "lot_name": LOT_NAME,
        "statistics": {},
        "evidence_requirements": [],
        "support_relations": [],
        "score_item_coverage": [],
        "requirement_coverage": [],
        "global_missing_evidence": [],
        "global_manual_review_items": [],
        "evidence_packages": [],
        "evidence_catalog_path": str(evidence_dir / "enterprise_evidence_catalog.json"),
    }
    (evidence_dir / "evidence_coverage_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")
    (evidence_dir / "enterprise_evidence_catalog.json").write_text(json.dumps({"evidence_items": [], "statistics": {}}, ensure_ascii=False), encoding="utf-8")
    markdown_path = evidence_dir / "evidence_coverage_matrix.md"
    markdown_path.write_text(render_evidence_coverage_markdown(matrix), encoding="utf-8")
    markdown_path.unlink()

    inspected = inspect_evidence_coverage(LOT_NAME, output_dir=tmp_path)

    assert inspected["statistics"] == {}


def test_outputs_do_not_contain_formal_body_or_bid_decision() -> None:
    payload = {
        "evidence_requirements": [requirement()],
        "support_relations": [],
        "score_item_coverage": [],
    }
    text = json.dumps(payload, ensure_ascii=False)

    assert "formal_response" not in text
    assert "draft_body" not in text
    ensure_no_decision_terms(payload)

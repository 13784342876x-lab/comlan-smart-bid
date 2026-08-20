from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.evidence_coverage_builder import build_score_item_coverage, evaluate_support_relation
from app.evidence_resolution import (
    active_decisions,
    apply_resolution_decisions,
    build_material_intake_plan,
    build_resolution_queue,
    empty_decision_store,
    ensure_decision_store,
    inspect_resolution_outputs,
    merge_decision_input,
    render_material_intake_plan_markdown,
    render_resolution_queue_markdown,
)
from app.llm_client import FORBIDDEN_DECISION_TERMS


LOT_NAME = "0425139003001-6582"


def evidence(
    evidence_id: str = "evidence-a",
    evidence_type: str = "personnel_resume",
    *,
    title: str = "张三人员简历",
    person: str = "张三",
    excerpt: str = "姓名：张三；项目经验：项目A",
) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "title": title,
        "normalized_title": title,
        "source_file_id": f"file-{evidence_id}",
        "source_file_name": f"{evidence_id}.docx",
        "source_location": {"clause_path": "测试材料"},
        "location_precision": "clause_approximate",
        "excerpt": excerpt,
        "structured_facts": {
            "person_name": person,
            "projects": [{"name": "项目A"}],
            "department_confirm_status": "unknown",
            "availability_status": "unknown",
        },
        "related_enterprise": "",
        "related_person": person,
        "related_project": "",
        "related_certificate": "",
        "related_qualification": "",
        "valid_until": None,
        "source_type": "enterprise_existing_fact",
        "extraction_confidence": 0.9,
        "identity_status": "identified",
        "human_review_required": True,
        "human_review_questions": [f"请确认{person}当前所属部门。", f"请确认{person}是否可投入本项目及拟任角色。"],
        "physical_file_exists": True,
        "verification_status": "unverified",
        "decision_ref_ids": [],
        "verification_method": None,
        "manually_verified_fields": {},
        "visual_source_ref": None,
        "verification_missing_fields": [],
    }


def support(
    support_id: str,
    evidence_id: str = "evidence-a",
    requirement_id: str = "evidence-requirement-a",
    score_id: str = "technical_02_项目负责人情况",
    *,
    status: str = "needs_review",
) -> dict:
    return {
        "support_id": support_id,
        "evidence_requirement_id": requirement_id,
        "evidence_id": evidence_id,
        "matched_score_item_ids": [score_id],
        "matched_action_group_ids": ["action-a"],
        "matched_requirement_ids": ["req-a"],
        "match_basis": [{"basis_type": "类型匹配", "detail": "测试", "source_type": "system_judgement"}],
        "matched_conditions": [],
        "unmet_conditions": [],
        "support_strength": "strong",
        "usability_status": status,
        "confidence": 0.9,
        "quantity_eligible": False,
        "can_be_used_in_writing": False,
        "permitted_claims": [],
        "restricted_claims": ["未确认前不得使用。"],
        "rejection_reasons": [],
        "human_review_required": True,
        "human_review_questions": [],
        "source_type": "system_judgement",
        "verification_status": "unverified",
        "decision_ref_ids": [],
        "verification_method": None,
    }


def evidence_requirement(
    requirement_id: str = "evidence-requirement-a",
    score_id: str = "technical_02_项目负责人情况",
    *,
    subjects: list[str] | None = None,
    required_count: int | None = None,
    title: str = "项目负责人情况",
) -> dict:
    return {
        "evidence_requirement_id": requirement_id,
        "canonical_title": title,
        "requirement_origin": "score_item",
        "source_score_item_ids": [score_id],
        "source_action_group_ids": ["action-a"],
        "source_requirement_ids": ["req-a"],
        "expected_evidence_subject_types": subjects or ["personnel_resume"],
        "expected_evidence_types": ["人员简历"],
        "required_conditions": [],
        "quantity_rule": {
            "rule_type": "fixed_count" if required_count is not None else "not_explicit",
            "required_count": required_count,
            "max_count_for_score": required_count,
            "score_per_item": None,
            "raw_rule": "测试数量规则" if required_count is not None else "",
            "derived_from_requirement_ids": ["req-a"] if required_count is not None else [],
            "source_type": "rule_derived" if required_count is not None else "system_judgement",
        },
        "quality_rule": {"conditions": [], "source_type": "system_judgement", "status": "not_explicit"},
        "time_conditions": [],
        "validity_conditions": [],
        "subject_conditions": [],
        "relevance_conditions": [],
        "related_context_chapter_ids": ["chapter-a"],
        "priority": "high",
        "source_type": "system_judgement",
        "human_review_required": False,
        "human_review_questions": [],
    }


def manual_review(evidence_id: str, question: str, requirement_id: str = "evidence-requirement-a", score_ids: list[str] | None = None) -> dict:
    return {
        "manual_review_id": f"review-{evidence_id}-{requirement_id}",
        "category": "personnel_department" if "部门" in question else "evidence_identity",
        "evidence_id": evidence_id,
        "evidence_requirement_id": requirement_id,
        "score_item_ids": score_ids or ["technical_02_项目负责人情况"],
        "requirement_ids": ["req-a"],
        "question": question,
        "current_judgement": "needs_review",
        "confirmation_needed": question,
        "source_type": "human_confirmation_required",
    }


def matrix(catalog: dict, requirements: list[dict], relations: list[dict], reviews: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "lot_name": LOT_NAME,
        "evidence_requirements": requirements,
        "support_relations": relations,
        "evidence_packages": [],
        "global_manual_review_items": reviews,
        "requirement_coverage": [],
        "score_item_coverage": [],
        "global_missing_evidence": [],
        "statistics": {"confirmed_count": 0},
    }


def decision(case: dict, decision_id: str = "decision-a", *, action: str = "confirm", fields: dict | None = None, method: str = "structured_fact_review", supersedes: str | None = None) -> dict:
    return {
        "decision_id": decision_id,
        "resolution_id": case["resolution_id"],
        "subject_id": case["subject_id"],
        "decision": action,
        "field_updates": fields or {},
        "reason": "人工复核测试",
        "reviewer": "测试复核人",
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "source_refs": case.get("source_refs") or [],
        "verification_method": method,
        "applicable_scope": {},
        "supersedes_decision_id": supersedes,
        "status": "active",
    }


def decision_payload(*items: dict) -> dict:
    return {"schema_version": "1.0", "lot_name": LOT_NAME, "decisions": list(items)}


def queue_for(catalog: dict, requirements: list[dict], relations: list[dict], reviews: list[dict], store: dict | None = None) -> dict:
    payload = matrix(catalog, requirements, relations, reviews)
    return build_resolution_queue(LOT_NAME, payload, catalog, store or empty_decision_store(LOT_NAME), "data/manual_reviews/test.json")


def test_person_department_affecting_multiple_relations_creates_one_case() -> None:
    item = evidence()
    relations = [support("support-a"), support("support-b", requirement_id="evidence-requirement-b", score_id="technical_03_项目团队人员结构")]
    requirements = [evidence_requirement(), evidence_requirement("evidence-requirement-b", "technical_03_项目团队人员结构")]
    reviews = [manual_review("evidence-a", "请确认张三当前所属部门。", score_ids=["technical_02_项目负责人情况", "technical_03_项目团队人员结构"])]

    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, requirements, relations, reviews)
    cases = [case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department"]

    assert len(cases) == 1
    assert set(cases[0]["related_support_relation_ids"]) == {"support-a", "support-b"}


def test_same_certificate_identity_question_is_one_case_for_multiple_requirements() -> None:
    cert = evidence("cert-a", "management_system_certificate", title="ISO 9001质量管理体系认证证书", person="")
    cert["related_certificate"] = "CERT-001"
    relations = [support("support-a", "cert-a"), support("support-b", "cert-a", "evidence-requirement-b")]
    reviews = [manual_review("cert-a", "请核对证书扫描件中的持证主体是否与投标主体完全一致。")]

    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [cert]}, [evidence_requirement(), evidence_requirement("evidence-requirement-b")], relations, reviews)
    cases = [case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "certificate_identity"]

    assert len(cases) == 1
    assert len(cases[0]["related_support_relation_ids"]) == 2


def test_decision_store_is_outside_outputs_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "data" / "manual_reviews" / LOT_NAME / "evidence_resolution_decisions.json"
    store, saved_path = ensure_decision_store(LOT_NAME, path)
    store["decisions"].append({"sentinel": "not overwritten"})
    saved_path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")

    reopened = json.loads(saved_path.read_text(encoding="utf-8"))

    assert "outputs" not in str(saved_path)
    assert reopened["decisions"][0]["sentinel"] == "not overwritten"


def test_confirm_without_minimum_fields_does_not_become_confirmed() -> None:
    item = evidence()
    requirements = [evidence_requirement()]
    relations = [support("support-a")]
    reviews = [manual_review("evidence-a", "请确认张三当前所属部门。")]
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, requirements, relations, reviews)
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    store = empty_decision_store(LOT_NAME)
    store["decisions"] = [decision(case, fields={"department": "技术部"})]

    warnings = apply_resolution_decisions({"evidence_items": [item]}, requirements, relations, [], queue, store)

    assert item["verification_status"] == "unverified"
    assert relations[0]["usability_status"] != "confirmed"
    assert any("最低确认字段" in warning for warning in warnings)


def test_human_visual_review_can_confirm_without_text_excerpt() -> None:
    cert = evidence("cert-a", "personnel_certificate", title="待识别人员证书", excerpt="")
    cert["source_location"] = {"asset_id": "asset-a"}
    cert["human_review_questions"] = ["证书图片尚无OCR摘录，请人工识别证书名称、编号、持有人和有效期。"]
    req = evidence_requirement(subjects=["personnel_certificate"])
    relations = [support("support-a", "cert-a")]
    reviews = [manual_review("cert-a", cert["human_review_questions"][0])]
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [cert]}, [req], relations, reviews)
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "evidence_document")
    fields = {
        "visual_source_ref": {"asset_id": "asset-a", "source_file_id": cert["source_file_id"]},
        "certificate_name": "云计算专业证书",
        "person_name": "张三",
        "certificate_number": "CERT-001",
        "unreadable_fields": ["发证机构"],
    }
    store = empty_decision_store(LOT_NAME)
    store["decisions"] = [decision(case, fields=fields, method="human_visual_review")]

    apply_resolution_decisions({"evidence_items": [cert]}, [req], relations, [], queue, store)

    assert cert["excerpt"] == ""
    assert cert["verification_status"] == "human_confirmed"
    assert cert["visual_source_ref"]["asset_id"] == "asset-a"
    assert relations[0]["usability_status"] == "confirmed"


def test_no_text_and_no_visual_confirmation_cannot_be_confirmed() -> None:
    cert = evidence("cert-a", "personnel_certificate", title="待识别人员证书", excerpt="")

    assert evaluate_support_relation(evidence_requirement(subjects=["personnel_certificate"]), cert)["usability_status"] != "confirmed"


def test_reject_decision_does_not_restore_candidate_on_reapply() -> None:
    base_evidence = evidence()
    requirements = [evidence_requirement()]
    reviews = [manual_review("evidence-a", "请确认张三当前所属部门。")]
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [base_evidence]}, requirements, [support("support-a", status="candidate")], reviews)
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    store = empty_decision_store(LOT_NAME)
    store["decisions"] = [decision(case, action="reject")]

    for _ in range(2):
        item = deepcopy(base_evidence)
        relations = [support("support-a", status="candidate")]
        apply_resolution_decisions({"evidence_items": [item]}, requirements, relations, [], queue, store)
        assert relations[0]["usability_status"] == "unusable"
        assert relations[0]["verification_status"] == "rejected"


def test_new_decision_supersedes_old_without_deleting_history(tmp_path: Path) -> None:
    item = evidence()
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, [evidence_requirement()], [support("support-a")], [manual_review("evidence-a", "请确认张三当前所属部门。")])
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    path = tmp_path / "decisions.json"

    merge_decision_input(LOT_NAME, decision_payload(decision(case, "decision-old", action="defer")), queue, store_path=path)
    merge_decision_input(LOT_NAME, decision_payload(decision(case, "decision-new", fields={"department": "技术部", "department_confirm_status": "human_confirmed"}, supersedes="decision-old")), queue, store_path=path)
    store = json.loads(path.read_text(encoding="utf-8"))

    assert len(store["decisions"]) == 2
    assert next(item for item in store["decisions"] if item["decision_id"] == "decision-old")["status"] == "superseded"
    assert [item["decision_id"] for item in active_decisions(store)] == ["decision-new"]


def test_missing_evidence_id_decision_produces_warning() -> None:
    item = evidence()
    requirements = [evidence_requirement()]
    relations = [support("support-a")]
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, requirements, relations, [manual_review("evidence-a", "请确认张三当前所属部门。")])
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    store = empty_decision_store(LOT_NAME)
    store["decisions"] = [decision(case, fields={"department": "技术部"})]

    warnings = apply_resolution_decisions({"evidence_items": []}, requirements, relations, [], queue, store)

    assert any("不存在的 evidence_id" in warning for warning in warnings)


def test_decision_import_rejects_unknown_resolution_id(tmp_path: Path) -> None:
    item = evidence()
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, [evidence_requirement()], [support("support-a")], [manual_review("evidence-a", "请确认张三当前所属部门。")])
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    payload = decision(case)
    payload["resolution_id"] = "resolution-does-not-exist"

    with pytest.raises(ValueError, match="resolution_id not found"):
        merge_decision_input(LOT_NAME, decision_payload(payload), queue, store_path=tmp_path / "decisions.json")


def test_decision_import_validates_top_level_schema(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        merge_decision_input(
            LOT_NAME,
            {"schema_version": "0.1", "lot_name": LOT_NAME, "decisions": []},
            {"resolution_cases": []},
            store_path=tmp_path / "decisions.json",
        )


def test_decision_import_dry_run_validates_without_writing_store(tmp_path: Path) -> None:
    item = evidence()
    catalog = {"lot_name": LOT_NAME, "evidence_items": [item]}
    queue = queue_for(catalog, [evidence_requirement()], [support("support-a")], [manual_review("evidence-a", "请确认张三当前所属部门。")])
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    payload = decision(case, action="confirm", fields={"department": "技术部"})
    payload["manual_fields_must_be_entered"] = ["department", "department_confirm_status"]
    path = tmp_path / "decisions.json"

    result = merge_decision_input(
        LOT_NAME,
        decision_payload(payload),
        queue,
        store_path=path,
        dry_run=True,
        catalog=catalog,
    )

    assert result["dry_run"] is True
    assert result["decision_store_written"] is False
    assert result["minimum_field_check_passed"] is False
    assert "department_confirm_status" in result["predicted_changes"][0]["missing_task_fields"]
    assert not path.exists()


def test_scoped_visual_decision_only_applies_to_selected_evidence() -> None:
    first = evidence("cert-a", "personnel_certificate", title="待识别人员证书", excerpt="")
    second = evidence("cert-b", "personnel_certificate", title="待识别人员证书", excerpt="")
    case = {
        "resolution_id": "resolution-visual",
        "resolution_subject_type": "evidence_document",
        "subject_id": "visual-certificates:file-a:张三",
        "related_evidence_ids": ["cert-a", "cert-b"],
        "related_support_relation_ids": ["support-a", "support-b"],
    }
    queue = {"resolution_cases": [case]}
    relations = [support("support-a", "cert-a"), support("support-b", "cert-b")]
    review = decision(case, fields={
        "evidence_updates": {
            "cert-a": {
                "visual_source_ref": {"asset_id": "asset-a"},
                "certificate_name": "人工确认测试证书",
                "person_name": "张三",
                "certificate_number": "CERT-A",
                "unreadable_fields": [],
            }
        }
    }, method="human_visual_review")
    review["applicable_scope"] = {"evidence_ids": ["cert-a"]}
    store = empty_decision_store(LOT_NAME)
    store["decisions"] = [review]

    apply_resolution_decisions(
        {"evidence_items": [first, second]},
        [evidence_requirement(subjects=["personnel_certificate"])],
        relations,
        [],
        queue,
        store,
    )

    assert first["verification_status"] == "human_confirmed"
    assert second["verification_status"] == "unverified"
    assert relations[0]["usability_status"] == "confirmed"
    assert relations[1]["usability_status"] == "needs_review"


def test_one_person_department_decision_covers_multiple_scores() -> None:
    item = evidence()
    relations = [support("support-a"), support("support-b", score_id="technical_03_项目团队人员结构")]
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, [evidence_requirement()], relations, [manual_review("evidence-a", "请确认张三当前所属部门。")])
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")

    assert set(case["related_score_item_ids"]) == {"technical_02_项目负责人情况", "technical_03_项目团队人员结构"}
    assert len(case["decision_refs"]) == 0


def test_performance_gap_creates_specific_evidence_package() -> None:
    req = evidence_requirement(score_id="technical_04_业绩情况", subjects=["project_performance", "contract"], required_count=5, title="业绩情况")
    payload = matrix({"evidence_items": []}, [req], [], [])
    payload["requirement_coverage"] = [{"evidence_requirement_id": req["evidence_requirement_id"], "required_count": 5, "confirmed_count": 0, "candidate_count": 0, "needs_review_count": 0, "quality_gaps": ["缺少合同和验收材料"]}]
    payload["score_item_coverage"] = [{"score_item_id": "technical_04_业绩情况", "required_count": 5, "confirmed_count": 0, "candidate_count": 0, "needs_review_count": 0}]
    payload["global_missing_evidence"] = [{"evidence_requirement_id": req["evidence_requirement_id"], "related_evidence_requirement_ids": [req["evidence_requirement_id"]], "affects_full_score_writing": True, "affects_mandatory_response": False, "reason_needed": "确认口径缺5项"}]

    plan = build_material_intake_plan(LOT_NAME, payload)
    package = plan["material_intake_packages"][0]

    assert package["title"] == "企业业绩完整证据包"
    assert package["requested_count"] == 5
    assert "合同关键页" in package["requested_material_components"]
    assert "验收证明或用户证明" in package["requested_material_components"]


def management_fixture() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    certs = []
    relations = []
    reviews = []
    req = evidence_requirement(score_id="business_05_绿色管理体系认证", subjects=["management_system_certificate"], required_count=4, title="绿色管理体系认证")
    for index, name in enumerate(["ISO 9001质量管理体系认证证书", "ISO 14001环境管理体系认证证书"], start=1):
        cert = evidence(f"cert-{index}", "management_system_certificate", title=name, person="")
        cert["structured_facts"] = {"certificate_name": name, "certificate_number": f"CERT-{index}"}
        cert["related_certificate"] = f"CERT-{index}"
        cert["valid_until"] = "2099-12-31"
        certs.append(cert)
        relations.append(support(f"support-{index}", cert["evidence_id"], score_id="business_05_绿色管理体系认证", status="candidate"))
        reviews.append(manual_review(cert["evidence_id"], "请核对证书扫描件中的持证主体是否与投标主体完全一致。", score_ids=["business_05_绿色管理体系认证"]))
    return certs, [req], relations, reviews


def test_management_candidates_do_not_count_before_confirmation() -> None:
    certs, requirements, relations, _ = management_fixture()
    score = {"id": "business_05_绿色管理体系认证", "item_name": "绿色管理体系认证", "score_range": {"max": 4}, "score_rule": "取得4项"}

    coverage = build_score_item_coverage([score], requirements, relations, {"evidence_items": certs}, [])[0]

    assert coverage["confirmed_count"] == 0
    assert coverage["remaining_confirmed_gap"] == 4


def test_confirmed_management_candidates_update_count_and_gap() -> None:
    certs, requirements, relations, reviews = management_fixture()
    catalog = {"lot_name": LOT_NAME, "evidence_items": certs}
    queue = queue_for(catalog, requirements, relations, reviews)
    store = empty_decision_store(LOT_NAME)
    for index, case in enumerate([item for item in queue["resolution_cases"] if item["resolution_subject_type"] == "certificate_identity"], start=1):
        store["decisions"].append(decision(case, f"decision-{index}", fields={"holder": "投标企业", "certificate_name": case["current_facts"][0]["title"], "certificate_number": f"CERT-{index}"}))
    apply_resolution_decisions(catalog, requirements, relations, [], queue, store)
    score = {"id": "business_05_绿色管理体系认证", "item_name": "绿色管理体系认证", "score_range": {"max": 4}, "score_rule": "取得4项"}

    coverage = build_score_item_coverage([score], requirements, relations, catalog, [])[0]

    assert coverage["confirmed_count"] == 2
    assert coverage["remaining_confirmed_gap"] == 2


def test_research_team_is_one_intake_package_not_thirty_requests() -> None:
    req = evidence_requirement(score_id="business_10_研发团队规模", subjects=["personnel_resume", "personnel_certificate"], required_count=30, title="研发团队规模")
    payload = matrix({"evidence_items": []}, [req], [], [])
    payload["requirement_coverage"] = [{"evidence_requirement_id": req["evidence_requirement_id"], "required_count": 30, "confirmed_count": 0, "candidate_count": 18, "needs_review_count": 0, "quality_gaps": []}]
    payload["score_item_coverage"] = [{"score_item_id": "business_10_研发团队规模", "required_count": 30, "confirmed_count": 0, "candidate_count": 0, "needs_review_count": 2}]
    payload["global_missing_evidence"] = [{"evidence_requirement_id": req["evidence_requirement_id"], "related_evidence_requirement_ids": [req["evidence_requirement_id"]], "affects_full_score_writing": True, "affects_mandatory_response": False, "reason_needed": "确认人数不足"}]

    plan = build_material_intake_plan(LOT_NAME, payload)

    assert len(plan["material_intake_packages"]) == 1
    assert plan["material_intake_packages"][0]["title"] == "研发团队不少于30人材料包"
    assert plan["material_intake_packages"][0]["candidate_count"] == 2


def test_resume_cannot_be_confirmed_as_company_performance() -> None:
    item = evidence()
    req = evidence_requirement(score_id="technical_04_业绩情况", subjects=["project_performance"], required_count=5, title="业绩情况")
    relation = evaluate_support_relation(req, item)

    assert relation["usability_status"] == "unusable"
    assert any("不能作为企业合同业绩" in reason for reason in relation["rejection_reasons"])


def test_intake_plan_is_derived_from_formal_matrix() -> None:
    payload = matrix({"evidence_items": []}, [], [], [])
    plan = build_material_intake_plan(LOT_NAME, payload)

    assert plan["generated_from"].endswith("evidence_coverage_matrix.json")
    assert "query" not in plan
    assert "matched_materials" not in plan


def test_decision_application_does_not_modify_tender_requirement_facts() -> None:
    item = evidence()
    requirements = [evidence_requirement()]
    original = deepcopy(requirements)
    relations = [support("support-a")]
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, requirements, relations, [manual_review("evidence-a", "请确认张三当前所属部门。")])
    case = next(case for case in queue["resolution_cases"] if case["resolution_subject_type"] == "person_department")
    store = empty_decision_store(LOT_NAME)
    store["decisions"] = [decision(case, fields={"department": "技术部", "department_confirm_status": "human_confirmed"})]

    apply_resolution_decisions({"evidence_items": [item]}, requirements, relations, [], queue, store)

    assert requirements == original


def test_resolution_outputs_do_not_generate_formal_body_or_decision_language() -> None:
    item = evidence()
    queue = queue_for({"lot_name": LOT_NAME, "evidence_items": [item]}, [evidence_requirement()], [support("support-a")], [manual_review("evidence-a", "请确认张三当前所属部门。")])
    plan = build_material_intake_plan(LOT_NAME, matrix({"evidence_items": [item]}, [], [], []))
    text = json.dumps(queue, ensure_ascii=False) + json.dumps(plan, ensure_ascii=False)

    assert "formal_response" not in text
    assert "draft_body" not in text
    for term in FORBIDDEN_DECISION_TERMS:
        assert term.lower() not in text.lower()


def test_markdown_is_display_only_for_resolution_inspection(tmp_path: Path) -> None:
    evidence_dir = tmp_path / LOT_NAME / "evidence"
    evidence_dir.mkdir(parents=True)
    queue = {"lot_name": LOT_NAME, "decision_store_path": "data/decisions.json", "resolution_cases": [], "statistics": {}}
    intake = {"lot_name": LOT_NAME, "material_intake_packages": [], "statistics": {}}
    summary = {"confirmed_change": {}, "quantity_gap_changes": []}
    matrix_payload = {"statistics": {"confirmed_count": 0}}
    catalog_payload = {"evidence_items": []}
    for name, payload in [
        ("evidence_resolution_queue.json", queue),
        ("material_intake_plan.json", intake),
        ("evidence_resolution_summary.json", summary),
        ("evidence_coverage_matrix.json", matrix_payload),
        ("enterprise_evidence_catalog.json", catalog_payload),
    ]:
        (evidence_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (evidence_dir / "evidence_resolution_queue.md").write_text(render_resolution_queue_markdown(queue), encoding="utf-8")
    (evidence_dir / "material_intake_plan.md").write_text(render_material_intake_plan_markdown(intake), encoding="utf-8")
    (evidence_dir / "evidence_resolution_queue.md").unlink()
    (evidence_dir / "material_intake_plan.md").unlink()

    inspected = inspect_resolution_outputs(LOT_NAME, output_dir=tmp_path)

    assert inspected["current_confirmed_count"] == 0

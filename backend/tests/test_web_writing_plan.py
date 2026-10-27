from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.web.api import create_app
from app.web.writing_plan_query_service import _is_technical_writing_requirement
from test_web_tender_analysis import (
    HOSPITAL_PROJECT,
    _analysis_project,
    _attach_writing_preparation_artifacts,
    _write,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attach_plan(tmp_path: Path, project_id: str) -> None:
    _attach_writing_preparation_artifacts(tmp_path, project_id)
    state_path = next((tmp_path / "bid_runs").glob("*/pipeline_state.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    prep = tmp_path / f"prep-fixture-{project_id}"
    response = _write(prep / "technical_response_outline.json", {
        "schema_version": "technical_response_outline_v2",
        "generated_at": "2026-09-02T00:00:00+00:00",
        "chapters": [{"chapter_id": "CH-1", "response_points": [
            {"point_id": "TR-003", "original_text": "提供制造商服务机构地址、联系方式和技术人员数量。"},
            {"point_id": "TR-015", "original_text": "提供环境标志产品证明。"},
            {"point_id": "TR-018", "original_text": "响应大型技术参数表。"},
        ]}],
    })
    writing = _write(prep / "technical_writing_outline.json", {
        "schema_version": "technical_writing_outline_v0",
        "source_response_outline": {"sha256": _sha(response)},
        "chapters": [
            {"chapter_id": "WC-005", "title": "售后服务与运行保障方案", "adaptation_reason": "响应售后服务要求。", "children": [
                {"chapter_id": "WC-005-04", "title": "服务机构与人员", "children": [], "writing_requirements": {"required_content_points": ["说明制造商服务机构。"]}},
            ]},
            {"chapter_id": "WC-006", "title": "绿色供应链管理方案", "children": []},
            {"chapter_id": "WC-007", "title": "技术参数及点对点应答", "children": [
                {"chapter_id": "WC-007-01", "title": "技术要求点对点应答表", "children": []},
                {"chapter_id": "WC-007-02", "title": "技术方案说明", "children": []},
            ]},
        ],
    })
    tasks_path = prep / "section_writing_tasks.json"
    tasks = {
        "schema_version": "technical_section_writing_tasks_v0",
        "tasks": [
            {"task_id": "TASK-1", "chapter_id": "WC-005", "section_id": "WC-005-04", "target_path": ["售后服务与运行保障方案", "服务机构与人员"], "writing_objective": "响应制造商服务机构要求且不得虚构事实。", "required_content_points": ["地址、联系方式、技术人员数量"], "response_point_mappings": [{"response_point_id": "TR-003", "mapping_role": "primary"}], "score_item_refs": [], "project_fact_slots": [{"fact_slot_id": "FACT-013", "fact_key": "manufacturer_service_network", "name": "制造商服务机构", "status": "missing", "source_policy": "confirmed_project_fact"}], "evidence_slots": [], "kb_query_plan": [], "readiness": "needs_project_fact", "body_generated": False},
            {"task_id": "TASK-2", "chapter_id": "WC-006", "section_id": "WC-006", "target_path": ["绿色供应链管理方案"], "writing_objective": "规划认证类评分响应。", "required_content_points": ["环境标志证明"], "response_point_mappings": [{"response_point_id": "TR-015", "mapping_role": "primary"}], "score_item_refs": ["SCORE-GREEN"], "project_fact_slots": [], "evidence_slots": [{"evidence_type": "product_certificate", "required": True, "source_policy": "local_kb_evidence"}], "kb_query_plan": [{"query_id": "KBQ-CERT", "evidence_type": "product_certificate", "execution_status": "planned_not_executed"}], "readiness": "needs_local_evidence", "body_generated": False},
            {"task_id": "TASK-3", "chapter_id": "WC-007", "section_id": "WC-007-01", "target_path": ["技术参数及点对点应答", "技术要求点对点应答表"], "writing_objective": "响应技术参数表。", "required_content_points": [], "response_point_mappings": [{"response_point_id": "TR-018", "mapping_role": "primary"}], "score_item_refs": [], "project_fact_slots": [], "evidence_slots": [], "kb_query_plan": [], "readiness": "planned", "body_generated": False},
            {"task_id": "TASK-4", "chapter_id": "WC-007", "section_id": "WC-007-02", "target_path": ["技术参数及点对点应答", "技术方案说明"], "writing_objective": "说明关键参数设计。", "required_content_points": [], "response_point_mappings": [{"response_point_id": "TR-018", "mapping_role": "supporting"}], "score_item_refs": [], "project_fact_slots": [], "evidence_slots": [], "kb_query_plan": [], "readiness": "planned", "body_generated": False},
        ],
    }
    _write(tasks_path, tasks)
    information = _write(prep / "task_information_requirements.json", {
        "schema_version": "technical_task_information_requirements_v0",
        "tasks": [{
            "task_id": value["task_id"],
            "chapter_id": value["chapter_id"],
            "section_id": value["section_id"],
            "response_point_ids": [
                mapping["response_point_id"]
                for mapping in value.get("response_point_mappings", [])
            ],
            "score_item_refs": value.get("score_item_refs", []),
            "draftability": {
                "status": "can_draft_with_placeholders" if value["task_id"] == "TASK-1" else "can_draft",
                "missing_inputs": ["manufacturer_service_network"] if value["task_id"] == "TASK-1" else [],
            },
        } for value in tasks["tasks"]],
        "source_integrity": {"tasks": {"sha256_after": _sha(tasks_path)}},
    })
    response_stage = next(item for item in state["stages"] if item["stage_id"] == "build_technical_response_outline")
    response_stage.update({"status": "completed", "artifacts": {"technical_response_outline_json": str(response)}})
    writing_stage = next(item for item in state["stages"] if item["stage_id"] == "build_technical_writing_outline")
    writing_stage.update({"status": "completed", "artifacts": {"technical_writing_outline_json": str(writing)}})
    task_stage = next(item for item in state["stages"] if item["stage_id"] == "build_writing_tasks")
    task_stage.update({"status": "completed", "artifacts": {
        **task_stage.get("artifacts", {}),
        "technical_writing_outline_json": str(writing),
        "section_writing_tasks_json": str(tasks_path),
        "task_information_requirements_json": str(information),
    }})
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_new_analysis_without_plan_is_a_safe_not_generated_state(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    response = TestClient(create_app(tmp_path)).get(f"/api/projects/{project_id}/writing-plan")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_generated"
    assert response.json()["chapters"] == []


def test_writing_plan_projects_structure_coverage_and_dynamic_fact_overlay(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    client = TestClient(create_app(tmp_path))
    initial = client.get(f"/api/projects/{project_id}/writing-plan")
    assert initial.status_code == 200, initial.text
    payload = initial.json()
    assert payload["summary"]["chapter_count"] == 3
    assert payload["summary"]["task_count"] == 4
    assert payload["summary"]["pending_human_confirmation_count"] == 1
    task = next(item for item in payload["tasks"] if item["task_id"] == "TASK-1")
    assert task["requirement_refs"][0]["trace_id"] == "TR-003"
    assert task["fact_dependencies"][0]["availability"] == "placeholder_required"
    assert task["human_confirmation_refs"] == []
    cert = next(item for item in payload["tasks"] if item["task_id"] == "TASK-2")
    assert cert["material_dependencies"][0]["status"] == "query_not_executed"
    assert cert["material_dependencies"][0]["status_label"] == "尚未查询"
    tr018 = next(item for item in payload["requirement_coverage"] if item["trace_id"] == "TR-018")
    assert tr018["status"] == "multiple_sections" and len(tr018["section_ids"]) == 2

    preparation = client.get(f"/api/projects/{project_id}/writing-preparation")
    command = {"status": "confirmed", "value": {"address": "天津市滨海新区", "contact": "022-12345678", "technician_count": 12}, "source_description": "制造商书面确认", "confirmation_basis": "项目负责人核验", "note": None, "change_reason": None}
    saved = client.patch(f"/api/projects/{project_id}/writing-preparation/facts/FACT-013", headers={"If-Match": preparation.headers["etag"]}, json=command)
    assert saved.status_code == 200, saved.text
    current = client.get(f"/api/projects/{project_id}/writing-plan").json()
    updated = next(item for item in current["tasks"] if item["task_id"] == "TASK-1")
    assert updated["fact_dependencies"][0]["availability"] == "available"
    assert "天津市滨海新区" in updated["fact_dependencies"][0]["value"]
    assert current["lineage"]["writing_tasks_sha256"] == payload["lineage"]["writing_tasks_sha256"]


def test_technical_requirement_scope_uses_structured_taxonomy_and_keeps_unmapped_items(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    matrix_path = tmp_path / "tender_requirement_matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["requirements"].extend([
        {"requirement_id": "TR-TECH-UNMAPPED", "title": "未映射技术功能要求", "raw_text": "技术功能要求正文", "requirement_type": "technical_mandatory_requirement"},
        {"requirement_id": "TR-QUAL", "title": "营业执照", "raw_text": "资格材料正文", "requirement_type": "qualification_requirement", "applicable_scope": {"scope_type": "qualification_documents"}},
        {"requirement_id": "TR-PRICE", "title": "价格评分", "raw_text": "价格评分正文", "requirement_type": "score_related_requirement", "related_score_item_ids": ["price_total"]},
        {"requirement_id": "TR-FORMAT", "title": "文件递交格式", "raw_text": "格式要求正文", "requirement_type": "format_submission_requirement", "applicable_scope": {"scope_type": "technical_response"}},
    ])
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")

    payload = TestClient(create_app(tmp_path)).get(f"/api/projects/{project_id}/writing-plan").json()
    coverage = {item["trace_id"]: item for item in payload["requirement_coverage"]}
    assert "TR-003" in coverage
    assert "TR-015" in coverage  # linked by the existing technical scoring projection
    assert coverage["TR-TECH-UNMAPPED"]["status"] == "unassigned"
    assert {"TR-QUAL", "TR-PRICE", "TR-FORMAT"}.isdisjoint(coverage)


def test_cross_domain_requirement_requires_technical_scope_or_task_mapping() -> None:
    common = {
        "mapped_requirement_ids": {"TR-DELIVERY-MAPPED", "TR-SECURITY-MAPPED"},
        "technical_score_ids": {"technical_service"},
        "technical_score_requirement_ids": set(),
    }
    assert _is_technical_writing_requirement(
        {"requirement_id": "TR-DELIVERY-MAPPED", "requirement_type": "delivery_acceptance_commitment", "applicable_scope": {"scope_type": "service_projects"}},
        **common,
    )
    assert _is_technical_writing_requirement(
        {"requirement_id": "TR-SECURITY", "requirement_type": "confidentiality_security_requirement", "applicable_scope": {"scope_type": "technical_response"}},
        **common,
    )
    assert not _is_technical_writing_requirement(
        {"requirement_id": "TR-DELIVERY-AMBIGUOUS", "requirement_type": "delivery_acceptance_commitment", "applicable_scope": {"scope_type": "service_projects"}},
        **common,
    )
    assert _is_technical_writing_requirement(
        {"requirement_id": "TR-SCORE-TECH", "requirement_type": "score_related_requirement", "related_score_item_ids": ["technical_service"]},
        **common,
    )
    assert not _is_technical_writing_requirement(
        {"requirement_id": "TR-SCORE-PRICE", "requirement_type": "score_related_requirement", "related_score_item_ids": ["price_total"]},
        **common,
    )
    assert not _is_technical_writing_requirement(
        {"requirement_id": "TR-SCORE-AMBIGUOUS", "requirement_type": "score_related_requirement"},
        **common,
    )


def test_writing_plan_rejects_an_internally_mixed_planning_bundle(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    writing_path = tmp_path / f"prep-fixture-{project_id}" / "technical_writing_outline.json"
    writing = json.loads(writing_path.read_text(encoding="utf-8"))
    writing["source_response_outline"]["sha256"] = "0" * 64
    writing_path.write_text(json.dumps(writing, ensure_ascii=False), encoding="utf-8")
    payload = TestClient(create_app(tmp_path)).get(f"/api/projects/{project_id}/writing-plan").json()
    assert payload["status"] == "invalid"
    assert payload["lineage"]["lineage_valid"] is False
    assert "response outline SHA" in payload["lineage"]["stale_reason"]


def test_hospital_plan_has_real_eight_chapters_and_key_traces_without_paths() -> None:
    outputs = Path(__file__).resolve().parents[1] / "outputs"
    response = TestClient(create_app(outputs)).get(f"/api/projects/{HOSPITAL_PROJECT}/writing-plan")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["chapter_count"] == 8
    assert payload["summary"]["task_count"] == 63
    assert [item["chapter_id"] for item in payload["chapters"]] == [f"WC-{index:03d}" for index in range(1, 9)]
    tr003 = next(item for item in payload["tasks"] if item["section_id"] == "WC-005-04")
    assert any(item["trace_id"] == "TR-003" for item in tr003["requirement_refs"])
    assert any(item["fact_id"] == "FACT-013" for item in tr003["fact_dependencies"])
    tr018 = next(item for item in payload["requirement_coverage"] if item["trace_id"] == "TR-018")
    assert tr018["status"] == "multiple_sections"
    assert "D:\\" not in response.text and str(outputs) not in response.text


def test_writing_plan_is_project_scoped(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    client = TestClient(create_app(tmp_path))
    assert client.get(f"/api/projects/{project_id}/writing-plan").status_code == 200
    assert client.get("/api/projects/proj-unknown/writing-plan").status_code == 404

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.bid_pipeline_runner import ANALYSIS_STOP_STAGE, BidPipelineRunner, STAGE_DEFINITIONS, StageResult
from app.web.analysis_query_service import TenderAnalysisReadService
from app.web.api import create_app
from app.web.dtos import ProjectCreateDTO
from app.web.project_lifecycle_service import ProjectLifecycleService
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import ProjectReadService
from app.web.writing_preparation_query_service import WritingPreparationReadService


HOSPITAL_PROJECT = "hospital_hci_001_fulltext_real_20260806_02"


class _Launcher:
    def launch(self, *, project_id: str, run_id: str, resume: bool) -> None:
        return None


def _docx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return stream.getvalue()


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _analysis_project(tmp_path: Path, project_name: str = "Analysis Fixture") -> tuple[str, TenderAnalysisReadService]:
    lifecycle = ProjectLifecycleService(tmp_path, _Launcher())
    project = lifecycle.create_project(ProjectCreateDTO(project_name=project_name))
    project_id = project.project.project_id
    source = lifecycle.add_source_file(project_id, "招标文件.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _docx())
    lifecycle.update_source_role(project_id, source.file_id, "main_tender")

    context = _write(tmp_path / "technical_response_context.json", {
        "project": {"project_name": project_name, "tenderee": "测试采购人", "tender_number": "TEST-001", "max_price": "100万元"},
        "chapters": [{
            "requirement_ids": ["TR-015"],
            "score_refs": [{
                "score_item_id": "SCORE-GREEN",
                "score_item_name": "环境标志产品",
                "score_rule": "提供有效证明得2分，未提供不得分。",
                "raw_score_text": "2分",
                "score_range": {"min": 0, "max": 2},
                "source_location": {"source_file_id": source.file_id, "clause_path": "评标办法 > 环境标志产品"},
            }],
        }],
        "global_manual_confirm_items": [{"confirm_id": "CONF-001", "question": "确认投标产品品牌", "reason": "写作前需要真实项目事实", "priority": "high"}],
        "global_missing_materials": [{"missing_id": "MAT-001", "title": "环境标志产品证明", "reason": "关联评分项", "status": "query_not_executed", "related_requirement_ref_ids": ["TR-015"], "related_score_item_ids": ["SCORE-GREEN"]}],
    })
    matrix = _write(tmp_path / "tender_requirement_matrix.json", {
        "requirements": [
            {"requirement_id": "TR-003", "title": "制造商服务机构信息", "raw_text": "提供制造商服务机构地址、联系方式和技术人员数量。", "requirement_type": "technical_mandatory_requirement", "source_ref": {"source_file_name": "招标文件.docx", "clause_path": "服务要求", "page_start": 10}},
            {"requirement_id": "TR-015", "title": "环境标志产品", "raw_text": "提供环境标志产品证明。", "requirement_type": "score_related_requirement", "source_ref": {"source_file_name": "招标文件.docx", "clause_path": "评分办法", "page_start": 12}},
            {"requirement_id": "TR-018", "title": "技术参数表", "raw_text": "设备\tCPU\t内存\n服务器\t≥32核\t≥512GB", "requirement_type": "technical_mandatory_requirement", "source_ref": {"source_file_name": "招标文件.docx", "clause_path": "技术参数", "table_range": "A1:C2", "source_location": {"table_index": 1}}},
        ],
    })
    actionable = _write(tmp_path / "actionable_requirement_index.json", {
        "action_groups": [{"action_group_id": "AG-TR003", "canonical_title": "制造商服务机构信息", "canonical_requirement": {"text": "准备地址、联系方式和技术人员数量。"}, "primary_action_kind": "write_response", "action_priority": "high", "member_requirement_ids": ["TR-003"], "member_source_refs": [{"source_file_name": "招标文件.docx", "clause_path": "服务要求", "page_start": 10}] }],
    })
    score_matrix = _write(tmp_path / "score_maximization_matrix.json", {"sections": [{"score_item": "环境标志产品", "max_score_preparation_target": "准备有效产品证明", "writing_response_points": ["说明证书名称和有效期"]}]})
    missing = _write(tmp_path / "missing_material_request.json", {"requests": [{"request_id": "MAT-001", "target_material": "环境标志产品证明", "why_needed": "满足2分条件", "status": "query_not_executed", "related_score_item": "SCORE-GREEN"}]})
    scoring_plan = _write(tmp_path / "scoring_action_plan.json", {})

    overrides = {}
    for stage_id, _ in STAGE_DEFINITIONS:
        if stage_id == "build_project_response_context":
            overrides[stage_id] = lambda _, c=context, s=scoring_plan: StageResult(artifacts={"technical_response_context": str(c), "scoring_action_plan_json": str(s)})
        elif stage_id == "build_requirement_analysis":
            overrides[stage_id] = lambda _, m=matrix, a=actionable: StageResult(artifacts={"tender_requirement_matrix_json": str(m), "actionable_requirement_index_json": str(a)})
        elif stage_id == "build_scoring_analysis":
            overrides[stage_id] = lambda _, s=score_matrix, m=missing: StageResult(artifacts={"score_maximization_matrix_json": str(s), "missing_material_request_json": str(m)})
        else:
            overrides[stage_id] = lambda _: StageResult()
    run = lifecycle.start_run(project_id)
    source_dir, _ = lifecycle.worker_inputs(project_id, run.run_id)
    BidPipelineRunner(output_dir=tmp_path, stage_overrides=overrides).run(source_dir=source_dir, run_id=run.run_id, stop_after_stage=ANALYSIS_STOP_STAGE)
    resolver = ReadOnlyProjectResolver(tmp_path)
    query = ProjectReadService(resolver)
    return project_id, TenderAnalysisReadService(resolver, ProjectLifecycleService(tmp_path, _Launcher()), query)


def test_pre_writing_analysis_projection_is_stage1_4_only(tmp_path: Path) -> None:
    project_id, service = _analysis_project(tmp_path)
    overview = service.overview(project_id)
    assert overview.requirement_count == 3
    assert overview.score_rule_count == 1
    assert overview.available_score_total == 2
    assert overview.lineage.source_mode == "pipeline_analysis"
    scoring = service.scoring(project_id)
    assert scoring.machine_assessment_included is False
    assert scoring.rules[0].criteria_text == "提供有效证明得2分，未提供不得分。"
    assert scoring.rules[0].material_needs[0].status == "query_not_executed"
    assert scoring.rules[0].material_needs[0].status_label == "尚未查询"
    parameters = service.parameters(project_id)
    assert parameters.table_count == 1 and parameters.tables[0].source_reference.source_kind == "table"
    preparation = service.risks_preparation(project_id)
    assert preparation.blocking_semantics_available is False
    assert preparation.materials[0].status_label == "尚未查询"
    assert any(item.trace_id == "AG-TR003" for item in preparation.writing_responses)


def test_pre_writing_analysis_http_and_facts_fallback(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    client = TestClient(create_app(tmp_path))
    for suffix in ("analysis", "analysis/files", "analysis/scoring", "analysis/parameters", "analysis/risks", "analysis/preparation", "facts-materials", "requirements"):
        response = client.get(f"/api/projects/{project_id}/{suffix}")
        assert response.status_code == 200, response.text
        assert str(tmp_path) not in response.text
        assert "\\source_files\\" not in response.text
    unknown = client.get("/api/projects/proj-unknown/analysis")
    assert unknown.status_code == 404


def test_hospital_original_analysis_has_31_requirements_15_rules_and_tr018_table() -> None:
    outputs = Path(__file__).resolve().parents[1] / "outputs"
    client = TestClient(create_app(outputs))
    overview = client.get(f"/api/projects/{HOSPITAL_PROJECT}/analysis")
    assert overview.status_code == 200
    assert overview.json()["requirement_count"] == 31
    assert overview.json()["score_rule_count"] == 15
    assert overview.json()["available_score_total"] == 70
    scoring = client.get(f"/api/projects/{HOSPITAL_PROJECT}/analysis/scoring").json()
    assert scoring["machine_assessment_included"] is False
    assert scoring["rule_count"] == 15
    assert "estimated_score" not in json.dumps(scoring)
    parameters = client.get(f"/api/projects/{HOSPITAL_PROJECT}/analysis/parameters").json()
    tr018 = next(item for item in parameters["tables"] if item["requirement_id"] == "TR-018")
    assert tr018["row_count"] == 211
    assert tr018["source_reference"]["table_range"] == "A1:H211"
    risks = client.get(f"/api/projects/{HOSPITAL_PROJECT}/analysis/risks").json()
    responses = {item["trace_id"]: item for item in risks["writing_responses"]}
    assert "TR-003" in responses and "地址" in responses["TR-003"]["description"]
    assert "TR-015" in responses
    payload = overview.text + json.dumps(scoring, ensure_ascii=False) + json.dumps(parameters, ensure_ascii=False)
    assert str(outputs) not in payload
    assert "technical_fulltext.scoring_assessment" not in payload


def _attach_writing_preparation_artifacts(tmp_path: Path, project_id: str) -> None:
    lifecycle = ProjectLifecycleService(tmp_path, _Launcher())
    run = lifecycle.list_runs(project_id).runs[0]
    prep = tmp_path / f"prep-fixture-{project_id}"
    prep.mkdir()
    facts = _write(prep / "project_fact_request.json", {
        "schema_version": "project_fact_request_v0",
        "slots": [
            {"fact_slot_id": "FACT-CONFIRMED", "fact_key": "delivery_period", "name": "交付期", "required": True, "status": "confirmed", "value": "合同签订后30日", "source_policy": "confirmed_project_fact", "target_task_ids": ["TASK-1"]},
            {"fact_slot_id": "FACT-PENDING", "fact_key": "manufacturer", "name": "制造商", "required": True, "status": "missing", "value": None, "source_policy": "confirmed_project_fact", "target_task_ids": ["TASK-1"], "prohibited_inference": "不得从招标最低要求反推"},
            {"fact_slot_id": "FACT-013", "fact_key": "manufacturer_service_network", "name": "制造商服务机构", "required": True, "status": "missing", "value": None, "source_policy": "confirmed_project_fact", "target_task_ids": ["TASK-1"], "prohibited_inference": "不得从招标最低要求反推"},
        ],
    })
    queries = _write(prep / "local_evidence_query_plan.json", {
        "schema_version": "local_evidence_query_plan_v0",
        "queries": [{"query_id": "KBQ-CERT", "query_kind": "evidence", "evidence_type": "product_certificate", "query": "产品认证", "purpose": "准备评分证明", "required": True, "target_task_ids": ["TASK-1"], "execution_status": "planned_not_executed"}],
    })
    tasks = _write(prep / "section_writing_tasks.json", {
        "schema_version": "technical_section_writing_tasks_v0",
        "tasks": [{"task_id": "TASK-1", "section_id": "WC-005-04", "target_path": ["售后服务", "制造商服务机构"], "response_point_mappings": [{"response_point_id": "TR-003"}], "score_item_refs": ["SCORE-GREEN"], "tender_source_refs": []}],
    })
    state_path = tmp_path / "bid_runs" / run.run_id / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    stage = next(item for item in state["stages"] if item["stage_id"] == "build_writing_tasks")
    stage["artifacts"] = {
        "project_fact_request": str(facts),
        "local_evidence_query_plan": str(queries),
        "section_writing_tasks_json": str(tasks),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_writing_preparation_keeps_confirmed_unknown_and_query_states_separate(tmp_path: Path) -> None:
    project_id, analysis = _analysis_project(tmp_path)
    _attach_writing_preparation_artifacts(tmp_path, project_id)
    service = WritingPreparationReadService(analysis.resolver, analysis.lifecycle, analysis)
    value = service.get(project_id)
    confirmed = next(item for item in value.items if item.item_id == "FACT-CONFIRMED")
    pending = next(item for item in value.items if item.item_id == "FACT-PENDING")
    material = next(item for item in value.items if item.item_id == "KBQ-CERT")
    assert confirmed.writing_context and confirmed.writing_context.availability == "available"
    assert confirmed.writing_context.value == "合同签订后30日"
    assert pending.writing_context and pending.writing_context.availability == "placeholder_required"
    assert pending.writing_context.value is None
    assert material.status == "query_not_executed"
    assert material.status_label == "尚未查询"
    assert value.summary.planning_accessible is True
    assert value.read_only is False and value.mutation_available is True
    assert any(item.kind == "requirement" and item.trace_id == "TR-003" for item in pending.dependencies)

    summary = service.facts_materials(project_id)
    projected_fact = next(item for item in summary.items if item.trace_id == "FACT-PENDING")
    projected_confirmation = next(item for item in summary.items if item.category == "confirmation_required")
    projected_material = next(item for item in summary.items if item.trace_id == "KBQ-CERT")
    assert projected_fact.title == pending.title
    assert projected_fact.related_locations == ["售后服务 / 制造商服务机构"]
    assert projected_fact.action_label == "填写信息"
    assert projected_fact.action_url.endswith("/writing-preparation?item=FACT-PENDING")
    assert projected_confirmation.title == "确认投标产品品牌"
    assert projected_confirmation.description == "写作前需要真实项目事实"
    assert projected_confirmation.action_label == "确认"
    assert projected_material.title == "产品认证"
    assert projected_material.description == "准备评分证明"
    assert projected_material.action_label == "查看 / 补充材料"


def test_writing_preparation_http_is_project_scoped_and_path_safe(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_writing_preparation_artifacts(tmp_path, project_id)
    client = TestClient(create_app(tmp_path))
    response = client.get(f"/api/projects/{project_id}/writing-preparation")
    assert response.status_code == 200, response.text
    assert str(tmp_path) not in response.text
    assert client.get(f"/api/projects/{project_id}/writing-context").status_code == 200
    assert client.get(f"/api/projects/{project_id}/writing-preparation/items/FACT-PENDING").status_code == 200
    assert client.get("/api/projects/proj-unknown/writing-preparation").status_code == 404


def test_hospital_writing_preparation_fact_013_and_certificate_trace() -> None:
    outputs = Path(__file__).resolve().parents[1] / "outputs"
    client = TestClient(create_app(outputs))
    response = client.get(f"/api/projects/{HOSPITAL_PROJECT}/writing-preparation")
    assert response.status_code == 200, response.text
    payload = response.json()
    fact = next(item for item in payload["items"] if item["item_id"] == "FACT-013")
    assert fact["status"] == "missing"
    assert fact["writing_context"]["availability"] == "placeholder_required"
    assert "KBQ-017" in fact["technical_refs"]
    assert any(item["kind"] == "requirement" and item["trace_id"] == "TR-003" for item in fact["dependencies"])
    assert any(item["kind"] == "writing" and item["trace_id"] == "WC-005-04" for item in fact["dependencies"])
    certificate = next(item for item in payload["items"] if item["item_id"] == "KBQ-003")
    assert certificate["status"] == "query_not_executed"
    assert certificate["status_label"] == "尚未查询"
    assert str(outputs) not in response.text
    assert "technical_fulltext.human_review" not in response.text

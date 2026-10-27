from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.technical_writing_tools import TechnicalWritingToolContext
from app.web.api import create_app
from test_web_tender_analysis import _analysis_project, _attach_writing_preparation_artifacts


def _fixture(tmp_path: Path):
    project_id, _ = _analysis_project(tmp_path)
    _attach_writing_preparation_artifacts(tmp_path, project_id)
    app = create_app(tmp_path)
    return project_id, app, TestClient(app)


def _command(address: str = "天津市滨海新区测试路 1 号", count: int = 12, *, reason: str | None = None) -> dict:
    return {
        "status": "confirmed",
        "value": {"address": address, "contact": "022-12345678", "technician_count": count},
        "source_description": "制造商书面确认",
        "confirmation_basis": "项目负责人核验",
        "note": "仅用于测试 fixture",
        "change_reason": reason,
    }


def test_fact_013_confirm_rebuild_history_and_stage9_loader(tmp_path: Path) -> None:
    project_id, app, client = _fixture(tmp_path)
    before = client.get(f"/api/projects/{project_id}/writing-preparation")
    assert before.status_code == 200 and before.headers.get("etag")
    token = before.headers["etag"]
    fact = next(item for item in before.json()["items"] if item["item_id"] == "FACT-013")
    assert fact["status"] == "missing" and fact["writing_context"]["availability"] == "placeholder_required"
    source = app.state.writing_preparation_service.fact_source(project_id)
    request_sha = hashlib.sha256(source.fact_request_path.read_bytes()).hexdigest()

    response = client.patch(
        f"/api/projects/{project_id}/writing-preparation/facts/FACT-013",
        headers={"If-Match": token},
        json=_command(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert response.headers["etag"] != token
    updated = next(item for item in payload["writing_preparation"]["items"] if item["item_id"] == "FACT-013")
    assert updated["status"] == "confirmed"
    assert updated["writing_context"]["availability"] == "available"
    assert updated["current_raw_value"]["technician_count"] == 12
    assert hashlib.sha256(source.fact_request_path.read_bytes()).hexdigest() == request_sha

    snapshot = app.state.writing_preparation_service.fact_service.snapshot(project_id)
    state = json.loads(snapshot.state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == "confirmed_project_facts_v1"
    assert state["facts"]["manufacturer_service_network"]["source_request_id"] == "FACT-013"
    assert len(state["change_history"]) == 1
    context = _stage9_context(tmp_path, snapshot.state_path)
    loaded = context.get_confirmed_project_facts({})
    assert loaded["facts"]["manufacturer_service_network"]["address"].startswith("天津市")
    assert "manufacturer" not in loaded["facts"]

    overview = client.get(f"/api/projects/{project_id}/overview").json()
    assert overview["facts_materials"]["missing_project_facts"] == before.json()["summary"]["pending_fact_count"] - 1
    requirement = client.get(f"/api/projects/{project_id}/requirements/TR-003").json()
    dependency = next(item for item in requirement["preparation_dependencies"] if item["trace_id"] == "FACT-013")
    assert dependency["status"] == "confirmed"


def test_preconditions_validation_unknown_and_stale_write(tmp_path: Path) -> None:
    project_id, _, client = _fixture(tmp_path)
    path = f"/api/projects/{project_id}/writing-preparation/facts/FACT-013"
    assert client.patch(path, json=_command()).status_code == 428
    token = client.get(f"/api/projects/{project_id}/writing-preparation").headers["etag"]
    assert client.patch(path, headers={"If-Match": token}, json={**_command(), "value": {"address": "x"}}).status_code == 422
    assert client.patch(path.replace("FACT-013", "FACT-UNKNOWN"), headers={"If-Match": token}, json=_command()).status_code == 404
    first = client.patch(path, headers={"If-Match": token}, json=_command("地址 A"))
    assert first.status_code == 200
    stale = client.patch(path, headers={"If-Match": token}, json=_command("地址 B", reason="修改地址"))
    assert stale.status_code == 409
    current = client.get(f"/api/projects/{project_id}/writing-preparation").json()
    fact = next(item for item in current["items"] if item["item_id"] == "FACT-013")
    assert fact["current_raw_value"]["address"] == "地址 A"


def test_update_history_and_reanalysis_lineage_stale(tmp_path: Path) -> None:
    project_id, app, client = _fixture(tmp_path)
    url = f"/api/projects/{project_id}/writing-preparation"
    token_a = client.get(url).headers["etag"]
    first = client.patch(f"{url}/facts/FACT-013", headers={"If-Match": token_a}, json=_command("地址 A"))
    token_b = first.headers["etag"]
    second = client.patch(f"{url}/facts/FACT-013", headers={"If-Match": token_b}, json=_command("地址 B", reason="服务机构搬迁"))
    assert second.status_code == 200
    snapshot = app.state.writing_preparation_service.fact_service.snapshot(project_id)
    assert len(snapshot.state["change_history"]) == 2

    source = app.state.writing_preparation_service.fact_source(project_id)
    request = json.loads(source.fact_request_path.read_text(encoding="utf-8"))
    request["lineage_test_marker"] = "new-analysis"
    source.fact_request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    stale = client.patch(f"{url}/facts/FACT-013", headers={"If-Match": second.headers["etag"]}, json=_command("地址 C", reason="旧页面提交"))
    assert stale.status_code == 409


def test_cross_project_and_response_do_not_expose_paths(tmp_path: Path) -> None:
    project_a, _, client = _fixture(tmp_path)
    project_b, _ = _analysis_project(tmp_path, "Analysis Fixture B")
    _attach_writing_preparation_artifacts(tmp_path, project_b)
    token_a = client.get(f"/api/projects/{project_a}/writing-preparation").headers["etag"]
    response = client.patch(
        f"/api/projects/{project_b}/writing-preparation/facts/FACT-013",
        headers={"If-Match": token_a}, json=_command(),
    )
    assert response.status_code == 409
    assert str(tmp_path) not in client.get(f"/api/projects/{project_a}/writing-preparation").text


def _stage9_context(tmp_path: Path, facts_path: Path) -> TechnicalWritingToolContext:
    response = tmp_path / "response.json"
    writing = tmp_path / "writing.json"
    tasks = tmp_path / "tasks.json"
    response.write_text(json.dumps({"chapters": []}), encoding="utf-8")
    writing.write_text(json.dumps({"chapters": [{"chapter_id": "WC-001", "children": [], "response_point_ids": []}]}), encoding="utf-8")
    tasks.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    return TechnicalWritingToolContext(
        response_outline_path=response,
        writing_outline_path=writing,
        task_information_path=tasks,
        output_root=tmp_path,
        output_key="project_fact_test",
        target_chapter_id="WC-001",
        target_section_id=None,
        confirmed_project_facts_path=facts_path,
    )

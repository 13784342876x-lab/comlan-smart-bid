from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.bid_pipeline_runner import (
    ANALYSIS_STOP_STAGE,
    WRITING_PLAN_READY_STATUS,
    WRITING_PLAN_STOP_STAGE,
    BidPipelineRunner,
    StageResult,
    STAGE_DEFINITIONS,
)
from app.web.api import create_app
from app.web.project_lifecycle_service import ProjectLifecycleService
from test_web_project_lifecycle import RecordingLauncher, _analysis_overrides, _ready_project
from test_web_tender_analysis import _analysis_project
from test_web_writing_plan import HOSPITAL_PROJECT, _attach_plan


class GenerationLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def launch(self, *, project_id: str, generation_id: str) -> None:
        self.calls.append((project_id, generation_id))


class FakeAgent:
    def __init__(self, agent_input: Any, output_dir: Path, *, preserve_placeholders: bool) -> None:
        self.agent_input = agent_input
        self.output_dir = Path(output_dir)
        self.preserve_placeholders = preserve_placeholders

    def run(self, **kwargs: Any) -> dict[str, Any]:
        handler = kwargs.get("event_handler")
        if handler:
            handler({"event_type": "model_started", "phase": "writing"})
            handler({"event_type": "validation_started", "phase": "validating"})
        task = next(
            item
            for item in self.agent_input.writing_constraint_context["tasks"]
            if item["section_id"] == self.agent_input.target_section_id
        )
        tokens = [item["placeholder_token"] for item in task["placeholder_requirements"]]
        body = "Generated canonical draft"
        if self.preserve_placeholders:
            body += "\n" + "\n".join(tokens)
        draft = (
            self.output_dir
            / self.agent_input.output_key
            / "drafts"
            / f"{self.agent_input.target_section_id}.md"
        )
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(body, encoding="utf-8")
        return {
            "status": "completed",
            "validation": {"valid": True, "errors": []},
            "outputs": {"formal_markdown": str(draft)},
            "model_profile": {"name": "fixture-model"},
        }


def _agent_factory(preserve_placeholders: bool):
    def build(agent_input: Any, *, output_dir: Path, **_: Any) -> FakeAgent:
        return FakeAgent(agent_input, Path(output_dir), preserve_placeholders=preserve_placeholders)
    return build


def test_analysis_run_continues_durably_to_writing_plan_stop(tmp_path: Path) -> None:
    service = ProjectLifecycleService(tmp_path, RecordingLauncher())
    project = _ready_project(service)
    run = service.start_run(project.project.project_id)
    source, _ = service.worker_inputs(project.project.project_id, run.run_id)
    artifacts = tmp_path / "planner"
    artifacts.mkdir()
    response = artifacts / "response.json"
    writing = artifacts / "writing.json"
    tasks = artifacts / "tasks.json"
    info = artifacts / "information.json"
    drafts = artifacts / "drafts.json"
    for path in (response, writing, tasks, info, drafts):
        path.write_text("{}", encoding="utf-8")
    overrides = _analysis_overrides(tmp_path)
    overrides["build_technical_response_outline"] = lambda _: StageResult(
        artifacts={"technical_response_outline_json": str(response)}
    )
    overrides["build_technical_writing_outline"] = lambda _: StageResult(
        artifacts={"technical_writing_outline_json": str(writing)}
    )
    overrides["build_writing_tasks"] = lambda _: StageResult(
        artifacts={
            "technical_writing_outline_json": str(writing),
            "section_writing_tasks_json": str(tasks),
            "task_information_requirements_json": str(info),
            "draft_units_json": str(drafts),
        }
    )
    runner = BidPipelineRunner(output_dir=tmp_path, stage_overrides=overrides)
    state = runner.run(source_dir=source, run_id=run.run_id, stop_after_stage=ANALYSIS_STOP_STAGE)
    assert state["pipeline_status"] == "ANALYSIS_READY"
    queued = service.generate_writing_plan(project.project.project_id)
    assert queued.status == "QUEUED"
    assert service.launcher.plan_calls == [(project.project.project_id, run.run_id)]
    state = runner.continue_to_writing_plan(run.run_id)
    assert state["pipeline_status"] == WRITING_PLAN_READY_STATUS
    assert state["inputs"]["stop_after_stage"] == WRITING_PLAN_STOP_STAGE
    assert next(item for item in state["stages"] if item["stage_id"] == "build_writing_tasks")["status"] == "completed"
    assert next(item for item in state["stages"] if item["stage_id"] == "generate_fulltext")["status"] == "pending"
    assert service.run(project.project.project_id, run.run_id).writing_plan_ready is True


def test_task_generation_is_durable_scoped_and_idempotent(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    app = create_app(tmp_path)
    launcher = GenerationLauncher()
    service = app.state.writing_generation_service
    service.launcher = launcher
    service.agent_factory = _agent_factory(True)
    client = TestClient(app)
    readiness = client.get(f"/api/projects/{project_id}/writing-readiness").json()
    response = client.post(
        f"/api/projects/{project_id}/writing/tasks/TASK-1/generate",
        json={"expected_input_fingerprint": readiness["input_fingerprint"]},
    )
    assert response.status_code == 202, response.text
    created = response.json()
    assert launcher.calls == [(project_id, created["generation_id"])]
    duplicate = client.post(
        f"/api/projects/{project_id}/writing/tasks/TASK-1/generate",
        json={"expected_input_fingerprint": readiness["input_fingerprint"]},
    )
    assert duplicate.status_code == 409
    completed = service.execute(project_id, created["generation_id"])
    assert completed.status == "completed"
    assert completed.draft_markdown and "{{PH-" in completed.draft_markdown
    restored = client.get(
        f"/api/projects/{project_id}/writing/generations/{created['generation_id']}"
    )
    assert restored.status_code == 200
    assert str(tmp_path) not in restored.text
    assert client.post(
        f"/api/projects/{project_id}/writing/tasks/UNKNOWN/generate",
        json={"expected_input_fingerprint": readiness["input_fingerprint"]},
    ).status_code == 404


def test_placeholder_omission_is_validation_failed(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    app = create_app(tmp_path)
    service = app.state.writing_generation_service
    service.launcher = GenerationLauncher()
    service.agent_factory = _agent_factory(False)
    readiness = app.state.writing_plan_service.readiness(project_id)
    created = service.start(project_id, "TASK-1", str(readiness.input_fingerprint))
    result = service.execute(project_id, created.generation_id)
    assert result.status == "validation_failed"
    assert any("required placeholder omitted" in value for value in result.validation_errors)


def test_stale_fingerprint_is_rejected_before_worker_launch(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_plan(tmp_path, project_id)
    app = create_app(tmp_path)
    service = app.state.writing_generation_service
    launcher = GenerationLauncher()
    service.launcher = launcher
    old = app.state.writing_plan_service.readiness(project_id)
    preparation = app.state.writing_preparation_service.get(project_id)
    app.state.writing_context_command_service.confirm_fact(
        project_id,
        "FACT-013",
        type("Command", (), {
            "status": "confirmed",
            "value": {"address": "Tianjin", "contact": "022-12345678", "technician_count": 12},
            "source_description": "fixture",
            "confirmation_basis": "fixture",
            "note": None,
            "change_reason": None,
        })(),
        preparation.version_token,
    )
    stale = TestClient(app).post(
        f"/api/projects/{project_id}/writing/tasks/TASK-1/generate",
        json={"expected_input_fingerprint": old.input_fingerprint},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_writing_context"
    assert launcher.calls == []


def test_hospital_historical_lineage_remains_generation_blocked() -> None:
    outputs = Path(__file__).resolve().parents[1] / "outputs"
    client = TestClient(create_app(outputs))
    readiness = client.get(
        f"/api/projects/{HOSPITAL_PROJECT}/writing-readiness"
    )
    assert readiness.status_code == 200
    assert readiness.json()["stage9_contract_ready"] is False
    listing = client.get(
        f"/api/projects/{HOSPITAL_PROJECT}/writing/generations"
    )
    assert listing.status_code == 200
    assert listing.json()["generations"] == []

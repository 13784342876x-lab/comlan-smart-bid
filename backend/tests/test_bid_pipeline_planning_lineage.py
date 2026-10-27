from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app import bid_pipeline_runner as pipeline_module
from app.bid_pipeline_runner import BidPipelineRunner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_writing_task_stage_repins_information_after_outline_enrichment(
    monkeypatch, tmp_path: Path
) -> None:
    response = tmp_path / "response.json"
    writing = tmp_path / "writing.json"
    tasks = tmp_path / "tasks.json"
    company = tmp_path / "company.json"
    information_path = tmp_path / "task_information.json"
    draft_path = tmp_path / "draft_units.json"
    for path in (response, writing, tasks, company):
        path.write_text("{}", encoding="utf-8")

    calls: list[str] = []

    def build_tasks(*args, **kwargs):
        return {
            "plan": {"summary": {}},
            "files": {
                "section_writing_tasks_json": str(tasks),
                "section_writing_tasks_markdown": str(tmp_path / "tasks.md"),
            },
        }

    def build_information(*args, **kwargs):
        calls.append(_sha(writing))
        payload = {"source_integrity": {"writing": {"sha256_after": _sha(writing)}}}
        information_path.write_text(json.dumps(payload), encoding="utf-8")
        return {
            "readiness_matrix": {},
            "files": {
                "task_information_requirements_json": str(information_path),
                "task_information_requirements_markdown": str(tmp_path / "information.md"),
                "task_readiness_matrix": str(tmp_path / "readiness.json"),
            },
        }

    def enrich(*args, **kwargs):
        writing.write_text('{"writing_requirement_merge": {}}', encoding="utf-8")
        return {
            "files": {
                "technical_writing_outline_json": str(writing),
                "technical_writing_outline_markdown": str(tmp_path / "writing.md"),
            }
        }

    def build_drafts(*args, **kwargs):
        draft_path.write_text("{}", encoding="utf-8")
        return {
            "draft_units": {"summary": {}},
            "files": {
                "draft_units_json": str(draft_path),
                "draft_units_markdown": str(tmp_path / "draft_units.md"),
            },
        }

    monkeypatch.setattr(pipeline_module, "build_section_task_plan_files", build_tasks)
    monkeypatch.setattr(pipeline_module, "build_task_information_files", build_information)
    monkeypatch.setattr(pipeline_module, "enrich_writing_outline_files", enrich)
    monkeypatch.setattr(pipeline_module, "build_draft_unit_files", build_drafts)

    runner = BidPipelineRunner(output_dir=tmp_path)
    artifacts = {
        ("build_technical_response_outline", "technical_response_outline_json"): str(response),
        ("build_technical_writing_outline", "technical_writing_outline_json"): str(writing),
        ("build_project_response_context", "technical_response_context"): str(response),
    }
    monkeypatch.setattr(runner, "_artifact", lambda state, stage, key: artifacts[(stage, key)])
    monkeypatch.setattr(runner, "_stage_output_dir", lambda state: tmp_path)
    state = {
        "inputs": {"company_outline": str(company)},
        "routing": {"planning_output_key": "acceptance"},
    }

    result = runner._stage_build_writing_tasks(state)

    assert len(calls) == 2
    assert calls[0] != calls[1]
    recorded = json.loads(information_path.read_text(encoding="utf-8"))
    assert recorded["source_integrity"]["writing"]["sha256_after"] == _sha(writing)
    assert result.artifacts["task_information_requirements_json"] == str(information_path)

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import typer

import cli as cli_module
from app.bid_cli_progress import BidCliProgressReporter
from app.bid_pipeline_runner import FINAL_STATUS


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in values), encoding="utf-8")


def _event(event_type: str, data: dict[str, Any], *, step: int = 0, phase: str = "writing") -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "step": step,
        "max_steps": 12,
        "phase": phase,
        "data": data,
    }


def _state(tmp_path: Path, *, stage_status: str = "running") -> tuple[Path, dict[str, Any]]:
    run_id = "bid-progress-fixture"
    fulltext_root = tmp_path / "bid_pipeline_outputs" / run_id
    payload = {
        "run_id": run_id,
        "pipeline_status": "RUNNING",
        "updated_at": "2026-08-27T00:00:00+00:00",
        "current_stage": "generate_fulltext",
        "inputs": {"source_dir": "D:/fixture/source"},
        "routing": {
            "fulltext_output_dir": str(fulltext_root),
            "fulltext_output_key": f"fulltext-{run_id}",
        },
        "stages": [
            {"stage_id": "validate_inputs", "status": "completed", "started_at": "2026-08-27T00:00:00+00:00", "completed_at": "2026-08-27T00:00:01+00:00"},
            {"stage_id": "generate_fulltext", "status": stage_status, "started_at": "2026-08-27T00:00:01+00:00", "completed_at": None, "error": None},
        ],
    }
    path = tmp_path / "bid_runs" / run_id / "pipeline_state.json"
    _write_json(path, payload)
    return path, payload


def _persist_failed_pipeline_state(
    output_dir: Path, run_id: str, *, error_type: str, message: str = ""
) -> Path:
    path = output_dir / "bid_runs" / run_id / "pipeline_state.json"
    _write_json(path, {
        "run_id": run_id,
        "pipeline_status": "FAILED",
        "current_stage": "parse_sources",
        "failure": {
            "stage_id": "parse_sources",
            "error_type": error_type,
            "message": message,
        },
    })
    return path


def test_reporter_reads_stage_and_fulltext_events_without_mutating_state(tmp_path: Path) -> None:
    state_path, state = _state(tmp_path)
    lines: list[str] = []
    reporter = BidCliProgressReporter(output_dir=tmp_path, run_id=state["run_id"], emit=lines.append)
    root = Path(state["routing"]["fulltext_output_dir"]) / state["routing"]["fulltext_output_key"]
    index_path = root / "fulltext" / "chapter_production_index.json"
    chapter_events = root / "drafts" / "WC-001.events.jsonl"
    _write_json(index_path, {"chapters": [{
        "chapter_id": "WC-001", "chapter_title": "项目概述与需求理解", "chapter_order": 1,
        "status": "completed", "started_at": "2026-08-27T00:00:01+00:00", "completed_at": "2026-08-27T00:00:05+00:00",
        "candidate_reviewable": True, "validation_status": "blocked", "issue_counts": {"blocking": 3},
    }]})
    _write_jsonl(root / "fulltext" / "technical_fulltext.events.jsonl", [
        _event("fulltext_run_started", {"chapter_count_expected": 1, "chapter_production_index_path": str(index_path)}),
        _event("chapter_run_started", {"chapter_id": "WC-001", "order": 1, "events_path": str(chapter_events)}),
        _event("chapter_run_completed", {"chapter_id": "WC-001", "order": 1}),
        _event("chapter_production_finished", {"chapter_count_completed": 1, "chapter_count_blocked": 1, "chapter_count_failed": 0}),
    ])
    _write_jsonl(chapter_events, [
        _event("agent_run_started", {}),
        _event("model_output_incomplete", {}),
        _event("fresh_final_generation_started", {}),
        _event("fresh_final_generation_completed", {}),
        _event("validation_completed", {"validation_status": "blocked", "issue_counts": {"blocking": 3}}),
        _event("candidate_artifact_saved", {}),
        _event("agent_run_completed", {}),
    ])
    before = state_path.read_bytes()
    reporter.poll_once()
    assert state_path.read_bytes() == before
    output = "\n".join(lines)
    assert "BID RUN START" in output
    assert "[01/02] ▶ validate_inputs" in output
    assert "[02/02] ▶ generate_fulltext" in output
    assert "FULLTEXT START" in output
    assert "[01/01] ▶ WC-001 项目概述与需求理解" in output
    assert "model output incomplete" in output
    assert "fresh-final fallback started" in output
    assert "validation  status=blocked  blockers=3" in output
    assert "candidate saved  reviewable=true" in output
    assert "[01/01] ✓ WC-001 项目概述与需求理解" in output
    assert "FULLTEXT COMPLETE" in output


def test_reporter_heartbeat_shows_model_wait_and_underlying_failure(tmp_path: Path) -> None:
    _, state = _state(tmp_path)
    lines: list[str] = []
    reporter = BidCliProgressReporter(output_dir=tmp_path, run_id=state["run_id"], emit=lines.append)
    root = Path(state["routing"]["fulltext_output_dir"]) / state["routing"]["fulltext_output_key"]
    index_path = root / "fulltext" / "chapter_production_index.json"
    chapter_events = root / "drafts" / "WC-002.events.jsonl"
    _write_json(index_path, {"chapters": [{"chapter_id": "WC-002", "chapter_title": "总体技术方案", "chapter_order": 1, "status": "running"}]})
    _write_jsonl(root / "fulltext" / "technical_fulltext.events.jsonl", [
        _event("fulltext_run_started", {"chapter_count_expected": 1, "chapter_production_index_path": str(index_path)}),
        _event("chapter_run_started", {"chapter_id": "WC-002", "order": 1, "events_path": str(chapter_events)}),
    ])
    _write_jsonl(chapter_events, [_event("model_call_started", {"model": "deepseek-v4-pro"}, step=5, phase="finalizing")])
    reporter.poll_once()
    reporter._last_heartbeat = 0
    reporter.poll_once()
    _write_jsonl(root / "fulltext" / "technical_fulltext.events.jsonl", [
        _event("fulltext_run_started", {"chapter_count_expected": 1, "chapter_production_index_path": str(index_path)}),
        _event("chapter_run_started", {"chapter_id": "WC-002", "order": 1, "events_path": str(chapter_events)}),
        _event("chapter_run_failed", {"chapter_id": "WC-002", "order": 1, "error": {
            "error_type": "OSError", "message": "chapter artifacts are not reviewable: candidate_file_missing",
            "underlying_error_type": "ModelOutputIncompleteError", "underlying_error_message": "provider returned neither tool calls nor final content",
        }}),
    ])
    reporter.poll_once()
    output = "\n".join(lines)
    assert "model call started" in output
    assert "waiting=model" in output
    assert "model_elapsed=" in output
    assert "underlying_error_type: ModelOutputIncompleteError" in output
    assert "candidate_file_missing" in output


def test_reporter_renders_failed_stage_and_pipeline_terminal(tmp_path: Path) -> None:
    state_path, state = _state(tmp_path, stage_status="failed")
    state["pipeline_status"] = "FAILED"
    state["current_stage"] = "generate_fulltext"
    state["failure"] = {
        "stage_id": "generate_fulltext",
        "error_type": "RuntimeError",
        "message": "fixture failure",
    }
    state["stages"][1]["error"] = dict(state["failure"])
    state["stages"][1]["completed_at"] = "2026-08-27T00:00:03+00:00"
    _write_json(state_path, state)
    lines: list[str] = []
    reporter = BidCliProgressReporter(output_dir=tmp_path, run_id=state["run_id"], emit=lines.append)
    reporter.poll_once()
    assert reporter.render_pipeline_terminal(state) is True
    output = "\n".join(lines)
    assert "[02/02] ✗ generate_fulltext" in output
    assert "error_type: RuntimeError" in output
    assert "PIPELINE FAILED" in output
    assert "failed_stage: generate_fulltext" in output


def test_reporter_thread_stops_without_writing_artifacts(tmp_path: Path) -> None:
    state_path, state = _state(tmp_path)
    before = state_path.read_bytes()
    reporter = BidCliProgressReporter(output_dir=tmp_path, run_id=state["run_id"], heartbeat_seconds=0.01, emit=lambda _: None)
    reporter.start()
    time.sleep(0.05)
    reporter.stop()
    assert reporter.running is False
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("raised", [KeyboardInterrupt(), SystemExit(2)])
def test_run_bid_stops_reporter_and_preserves_baseexception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: BaseException) -> None:
    events: list[str] = []

    class Reporter:
        def __init__(self, **_: Any) -> None:
            pass
        def start(self) -> None:
            events.append("start")
        def stop(self) -> None:
            events.append("stop")
        def render_pipeline_terminal(self, *_: Any) -> bool:
            return False

    class Pipeline:
        def __init__(self, **_: Any) -> None:
            pass
        def run(self, **_: Any) -> dict[str, Any]:
            raise raised

    monkeypatch.setattr(cli_module, "BidCliProgressReporter", Reporter)
    monkeypatch.setattr(cli_module, "BidPipelineRunner", Pipeline)
    with pytest.raises(type(raised)):
        cli_module.run_bid_command(source_dir=tmp_path, output_dir=tmp_path, stream_model=False)
    assert events == ["start", "stop"]


def test_run_bid_success_prints_pipeline_complete_and_passes_generated_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, Any] = {}

    class Reporter:
        def __init__(self, **_: Any) -> None:
            pass
        def start(self) -> None:
            pass
        def stop(self) -> None:
            pass
        def render_pipeline_terminal(self, state: dict[str, Any] | None = None) -> bool:
            if state and state.get("pipeline_status") == FINAL_STATUS:
                print("PIPELINE COMPLETE")
            return True

    class Pipeline:
        def __init__(self, **_: Any) -> None:
            pass
        def run(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"run_id": kwargs["run_id"], "pipeline_status": FINAL_STATUS, "final_status": FINAL_STATUS,
                    "current_stage": None, "failure": None, "created_at": datetime.now(timezone.utc).isoformat(),
                    "routing": {"pipeline_state_path": "state.json"}}

    monkeypatch.setattr(cli_module, "BidCliProgressReporter", Reporter)
    monkeypatch.setattr(cli_module, "BidPipelineRunner", Pipeline)
    cli_module.run_bid_command(source_dir=tmp_path, output_dir=tmp_path, stream_model=False)
    assert str(captured["run_id"]).startswith("bid-")
    assert "PIPELINE COMPLETE" in capsys.readouterr().out


def test_run_bid_keyboardinterrupt_renders_persisted_failure_without_mutating_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = "bid-keyboardinterrupt"
    stops: list[str] = []
    persisted: dict[str, bytes] = {}

    class Reporter:
        def __init__(self, **_: Any) -> None:
            pass
        def start(self) -> None:
            pass
        def stop(self) -> None:
            stops.append("stop")
        def render_pipeline_terminal(self, *_: Any) -> bool:
            return False

    class Pipeline:
        def __init__(self, **_: Any) -> None:
            pass
        def run(self, **kwargs: Any) -> dict[str, Any]:
            path = _persist_failed_pipeline_state(
                tmp_path, kwargs["run_id"], error_type="KeyboardInterrupt"
            )
            persisted["bytes"] = path.read_bytes()
            raise KeyboardInterrupt()

    monkeypatch.setattr(cli_module, "new_bid_run_id", lambda: run_id)
    monkeypatch.setattr(cli_module, "BidCliProgressReporter", Reporter)
    monkeypatch.setattr(cli_module, "BidPipelineRunner", Pipeline)
    state_path = tmp_path / "bid_runs" / run_id / "pipeline_state.json"
    with pytest.raises(KeyboardInterrupt):
        cli_module.run_bid_command(source_dir=tmp_path, output_dir=tmp_path, stream_model=False)
    output = capsys.readouterr().out
    assert "PIPELINE INTERRUPTED" in output
    assert f"run_id: {run_id}" in output
    assert "failed_stage: parse_sources" in output
    assert "error_type: KeyboardInterrupt" in output
    assert stops == ["stop"]
    assert state_path.read_bytes() == persisted["bytes"]


def test_run_bid_systemexit_renders_persisted_failure_and_keeps_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = "bid-systemexit"
    stops: list[str] = []

    class Reporter:
        def __init__(self, **_: Any) -> None:
            pass
        def start(self) -> None:
            pass
        def stop(self) -> None:
            stops.append("stop")
        def render_pipeline_terminal(self, *_: Any) -> bool:
            return False

    class Pipeline:
        def __init__(self, **_: Any) -> None:
            pass
        def run(self, **kwargs: Any) -> dict[str, Any]:
            _persist_failed_pipeline_state(
                tmp_path, kwargs["run_id"], error_type="SystemExit", message="2"
            )
            raise SystemExit(2)

    monkeypatch.setattr(cli_module, "new_bid_run_id", lambda: run_id)
    monkeypatch.setattr(cli_module, "BidCliProgressReporter", Reporter)
    monkeypatch.setattr(cli_module, "BidPipelineRunner", Pipeline)
    with pytest.raises(SystemExit) as raised:
        cli_module.run_bid_command(source_dir=tmp_path, output_dir=tmp_path, stream_model=False)
    assert raised.value.code == 2
    output = capsys.readouterr().out
    assert "PIPELINE EXITED" in output
    assert "failed_stage: parse_sources" in output
    assert "error_type: SystemExit" in output
    assert "code: 2" in output
    assert stops == ["stop"]


def test_run_bid_typer_exit_is_not_rendered_as_control_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Reporter:
        def __init__(self, **_: Any) -> None:
            pass
        def start(self) -> None:
            pass
        def stop(self) -> None:
            pass
        def render_pipeline_terminal(self, *_: Any) -> bool:
            return False

    class Pipeline:
        def __init__(self, **_: Any) -> None:
            pass
        def run(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "run_id": kwargs["run_id"], "pipeline_status": "FAILED", "final_status": None,
                "current_stage": "parse_sources", "failure": {"error_type": "RuntimeError"},
                "routing": {"pipeline_state_path": "state.json"},
            }

    monkeypatch.setattr(cli_module, "BidCliProgressReporter", Reporter)
    monkeypatch.setattr(cli_module, "BidPipelineRunner", Pipeline)
    with pytest.raises(typer.Exit):
        cli_module.run_bid_command(source_dir=tmp_path, output_dir=tmp_path, stream_model=False)
    output = capsys.readouterr().out
    assert "PIPELINE INTERRUPTED" not in output
    assert "PIPELINE EXITED" not in output


def test_run_bid_ordinary_exception_keeps_existing_failed_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Reporter:
        def __init__(self, **_: Any) -> None:
            pass
        def start(self) -> None:
            pass
        def stop(self) -> None:
            pass
        def render_pipeline_terminal(self, *_: Any) -> bool:
            print("PIPELINE FAILED")
            return True

    class Pipeline:
        def __init__(self, **_: Any) -> None:
            pass
        def run(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("fixture failure")

    monkeypatch.setattr(cli_module, "BidCliProgressReporter", Reporter)
    monkeypatch.setattr(cli_module, "BidPipelineRunner", Pipeline)
    with pytest.raises(typer.Exit) as raised:
        cli_module.run_bid_command(source_dir=tmp_path, output_dir=tmp_path, stream_model=False)
    assert raised.value.exit_code == 1
    output = capsys.readouterr().out
    assert "PIPELINE FAILED" in output
    assert "ERROR: fixture failure" in output
    assert "PIPELINE INTERRUPTED" not in output

"""Read-only foreground progress rendering for ``cli.py run-bid``.

This module deliberately observes existing state and JSONL artifacts only.  It
does not call pipeline services, mutate pipeline state, or persist its cursors.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in lines:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    events.append(value)
    except json.JSONDecodeError:
        # Writers may be between atomic replacement / flush boundaries.  The
        # next read retries the entire file; no business artifact is touched.
        return []
    return events


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _elapsed_seconds(started: Any, completed: Any | None = None) -> float | None:
    origin = _timestamp(started)
    if origin is None:
        return None
    end = _timestamp(completed) if completed is not None else datetime.now(timezone.utc)
    if end is None:
        end = datetime.now(timezone.utc)
    return max(0.0, (end - origin).total_seconds())


def _duration(value: float | None) -> str:
    if value is None:
        return "?"
    if value < 60:
        return f"{value:.1f}s"
    total = int(value)
    return f"{total // 60:02d}:{total % 60:02d}"


def _clock(value: Any) -> str:
    parsed = _timestamp(value)
    return (parsed.astimezone().strftime("%H:%M:%S") if parsed else datetime.now().strftime("%H:%M:%S"))


class BidCliProgressReporter:
    """Incrementally render existing pipeline/fulltext events to stdout."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        run_id: str,
        heartbeat_seconds: float = 20.0,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.run_id = str(run_id)
        self.heartbeat_seconds = max(0.01, float(heartbeat_seconds))
        self._emit = emit or (lambda line: print(line, flush=True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._announced_run = False
        self._stage_statuses: dict[str, str] = {}
        self._jsonl_offsets: dict[Path, int] = {}
        self._chapter_info: dict[str, dict[str, Any]] = {}
        self._chapter_events: dict[str, Path] = {}
        self._current_chapter_id: str | None = None
        self._current_model: dict[str, Any] | None = None
        self._fulltext_expected: int | None = None
        self._fulltext_index_path: Path | None = None
        self._fulltext_started = False
        self._fulltext_finished = False
        self._pipeline_terminal_rendered = False
        self._last_heartbeat = time.monotonic()

    @property
    def state_path(self) -> Path:
        return self.output_dir / "bid_runs" / self.run_id / "pipeline_state.json"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"bid-progress-{self.run_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(0.25)

    def poll_once(self) -> None:
        """Read current artifacts once. Intended for deterministic unit tests too."""
        state = _read_json(self.state_path)
        if state is None:
            return
        self._render_run_start(state)
        self._render_stages(state)
        self._consume_fulltext(state)
        if time.monotonic() - self._last_heartbeat >= self.heartbeat_seconds:
            self._render_heartbeat(state)
            self._last_heartbeat = time.monotonic()

    def render_pipeline_terminal(self, state: dict[str, Any] | None = None) -> bool:
        """Render an existing terminal pipeline state, if one is available."""
        if self._pipeline_terminal_rendered:
            return False
        payload = state if isinstance(state, dict) else _read_json(self.state_path)
        if not isinstance(payload, dict):
            return False
        status = str(payload.get("pipeline_status") or "")
        if status not in {"FAILED", "HUMAN_REVIEW_READY"}:
            return False
        self._pipeline_terminal_rendered = True
        elapsed = _duration(_elapsed_seconds(payload.get("created_at"), payload.get("completed_at") or payload.get("updated_at")))
        if status == "HUMAN_REVIEW_READY":
            self._line("PIPELINE COMPLETE")
            self._line(f"run_id: {payload.get('run_id')}")
            self._line(f"final_status: {payload.get('final_status')}")
            self._line(f"elapsed: {elapsed}")
        else:
            failure = payload.get("failure") if isinstance(payload.get("failure"), dict) else {}
            self._line("PIPELINE FAILED")
            self._line(f"run_id: {payload.get('run_id')}")
            self._line(f"failed_stage: {payload.get('current_stage') or failure.get('stage_id') or 'unknown'}")
            self._line(f"error_type: {failure.get('error_type') or 'unknown'}")
            self._line(f"message: {failure.get('message') or 'unknown'}")
        return True

    def _line(self, text: str) -> None:
        self._emit(text)

    def _render_run_start(self, state: dict[str, Any]) -> None:
        if self._announced_run:
            return
        self._announced_run = True
        self._line("BID RUN START")
        self._line(f"run_id: {state.get('run_id')}")
        self._line(f"source_dir: {(state.get('inputs') or {}).get('source_dir')}")

    def _render_stages(self, state: dict[str, Any]) -> None:
        stages = list(state.get("stages") or [])
        total = len(stages)
        completed = sum(item.get("status") == "completed" for item in stages)
        for index, stage in enumerate(stages, start=1):
            stage_id = str(stage.get("stage_id") or "")
            status = str(stage.get("status") or "pending")
            previous = self._stage_statuses.get(stage_id)
            if status == previous:
                continue
            # A fast stage can complete between polls.  Preserve both visible
            # transitions even though their artifact state is already final.
            if status in {"completed", "failed"} and previous is None:
                self._line(f"[{index:02d}/{total:02d}] ▶ {stage_id}")
            if status == "running":
                self._line(f"[{index:02d}/{total:02d}] ▶ {stage_id}")
            elif status == "completed":
                elapsed = _duration(_elapsed_seconds(stage.get("started_at"), stage.get("completed_at")))
                self._line(f"[{index:02d}/{total:02d}] ✓ {stage_id}  {elapsed}  completed={completed}/{total}")
            elif status == "failed":
                error = stage.get("error") if isinstance(stage.get("error"), dict) else {}
                self._line(f"[{index:02d}/{total:02d}] ✗ {stage_id}")
                self._line(f"         error_type: {error.get('error_type') or 'unknown'}")
                self._line(f"         message: {error.get('message') or 'unknown'}")
            self._stage_statuses[stage_id] = status

    def _consume(self, path: Path) -> list[dict[str, Any]]:
        events = _read_jsonl(path)
        offset = self._jsonl_offsets.get(path, 0)
        if len(events) < offset:
            offset = 0
        self._jsonl_offsets[path] = len(events)
        return events[offset:]

    def _consume_fulltext(self, state: dict[str, Any]) -> None:
        routing = state.get("routing") if isinstance(state.get("routing"), dict) else {}
        root = routing.get("fulltext_output_dir")
        output_key = routing.get("fulltext_output_key")
        if not root or not output_key:
            return
        fulltext_dir = Path(str(root)) / str(output_key) / "fulltext"
        events_path = fulltext_dir / "technical_fulltext.events.jsonl"
        if self._fulltext_index_path:
            self._load_chapter_plan(self._fulltext_index_path)
        for event in self._consume(events_path):
            self._render_fulltext_event(event)
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            index_value = data.get("chapter_production_index_path")
            if index_value:
                self._fulltext_index_path = Path(str(index_value))
                self._load_chapter_plan(self._fulltext_index_path)
        for chapter_id, path in list(self._chapter_events.items()):
            for event in self._consume(path):
                self._render_agent_event(chapter_id, event)

    def _load_chapter_plan(self, index_path: Path) -> None:
        index = _read_json(index_path)
        if index is None:
            return
        for chapter in index.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            chapter_id = str(chapter.get("chapter_id") or "")
            if chapter_id:
                self._chapter_info[chapter_id] = chapter

    def _chapter_label(self, chapter_id: str, order: Any = None) -> str:
        chapter = self._chapter_info.get(chapter_id) or {}
        ordinal = int(order or chapter.get("chapter_order") or 0)
        expected = int(self._fulltext_expected or 0)
        prefix = f"[{ordinal:02d}/{expected:02d}] " if ordinal and expected else ""
        title = str(chapter.get("chapter_title") or chapter.get("title") or "").strip()
        return f"{prefix}{chapter_id}{(' ' + title) if title else ''}"

    def _chapter_progress(self, chapter_id: str, order: Any, marker: str) -> str:
        chapter = self._chapter_info.get(chapter_id) or {}
        ordinal = int(order or chapter.get("chapter_order") or 0)
        expected = int(self._fulltext_expected or 0)
        prefix = f"[{ordinal:02d}/{expected:02d}] " if ordinal and expected else ""
        title = str(chapter.get("chapter_title") or chapter.get("title") or "").strip()
        return f"{prefix}{marker} {chapter_id}{(' ' + title) if title else ''}"

    def _render_fulltext_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        stamp = _clock(event.get("timestamp"))
        if event_type == "fulltext_run_started":
            self._fulltext_started = True
            self._fulltext_expected = int(data.get("chapter_count_expected") or 0) or None
            self._line("FULLTEXT START")
            self._line(f"chapters: {self._fulltext_expected or '?'}")
        elif event_type == "chapter_run_started":
            chapter_id = str(data.get("chapter_id") or "")
            self._current_chapter_id = chapter_id or self._current_chapter_id
            event_path = data.get("events_path")
            if chapter_id and event_path:
                self._chapter_events[chapter_id] = Path(str(event_path))
            self._line(self._chapter_progress(chapter_id, data.get("order"), "▶"))
        elif event_type == "chapter_run_completed":
            chapter_id = str(data.get("chapter_id") or "")
            chapter = self._chapter_info.get(chapter_id) or {}
            elapsed = _duration(_elapsed_seconds(chapter.get("started_at"), chapter.get("completed_at")))
            reviewable = bool(chapter.get("candidate_reviewable"))
            validation = chapter.get("validation_status") or "unknown"
            blockers = int((chapter.get("issue_counts") or {}).get("blocking") or 0)
            self._line(self._chapter_progress(chapter_id, data.get("order"), "✓"))
            self._line(f"       elapsed={elapsed}  candidate={'reviewable' if reviewable else 'not_reviewable'}  validation={validation}  blockers={blockers}")
            self._current_model = None
        elif event_type == "chapter_run_failed":
            chapter_id = str(data.get("chapter_id") or "")
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            self._line(self._chapter_progress(chapter_id, data.get("order"), "✗"))
            self._line(f"       error_type: {error.get('error_type') or 'unknown'}")
            self._line(f"       message: {error.get('message') or 'unknown'}")
            if error.get("underlying_error_type"):
                self._line(f"       underlying_error_type: {error['underlying_error_type']}")
                self._line(f"       underlying_message: {error.get('underlying_error_message') or ''}")
            self._current_model = None
        elif event_type == "chapter_production_finished" and not self._fulltext_finished:
            self._fulltext_finished = True
            completed = int(data.get("chapter_count_completed") or 0)
            failed = int(data.get("chapter_count_failed") or 0)
            expected = self._fulltext_expected or completed + failed
            label = "FULLTEXT FAILED" if failed else "FULLTEXT COMPLETE"
            self._line(label)
            self._line(f"chapters expected: {expected}")
            self._line(f"completed: {completed}")
            self._line(f"reviewable: {completed}")
            self._line(f"blocked: {int(data.get('chapter_count_blocked') or 0)}")
            self._line(f"failed: {failed}")
            failed_ids = [
                str(item.get("chapter_id"))
                for item in self._chapter_info.values()
                if item.get("status") == "failed"
            ]
            if failed_ids:
                self._line(f"failed chapters: {', '.join(failed_ids)}")

    def _render_agent_event(self, chapter_id: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type not in {
            "agent_run_started", "model_call_started", "model_call_completed", "model_call_failed",
            "model_call_retry_scheduled", "model_output_incomplete", "fresh_final_generation_started",
            "fresh_final_generation_completed", "fresh_quality_regeneration_started",
            "fresh_quality_regeneration_failed", "validation_completed", "candidate_artifact_saved",
            "agent_run_completed", "agent_run_failed", "tool_call_completed",
        }:
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        stamp = _clock(event.get("timestamp"))
        step = int(event.get("step") or 0)
        maximum = int(event.get("max_steps") or 0)
        prefix = f"[{stamp}]     "
        if event_type == "agent_run_started":
            self._line(f"{prefix}agent started")
        elif event_type == "model_call_started":
            self._current_model = {"chapter_id": chapter_id, "timestamp": event.get("timestamp"), "step": step, "max_steps": maximum, "phase": event.get("phase")}
            self._line(f"{prefix}model call started")
            self._line(f"              step={step}/{maximum}  model={data.get('model') or data.get('profile_name') or 'configured model'}")
        elif event_type == "model_call_completed":
            self._current_model = None
            self._line(f"{prefix}model call completed")
        elif event_type == "model_call_failed":
            self._current_model = None
            self._line(f"{prefix}model call failed  error_type={data.get('error_type') or 'unknown'}")
        elif event_type == "model_call_retry_scheduled":
            self._line(f"{prefix}model retry scheduled  retry={data.get('retry_count') or data.get('attempt') or '?'}")
        elif event_type == "model_output_incomplete":
            self._line(f"{prefix}model output incomplete")
        elif event_type == "fresh_final_generation_started":
            self._line(f"{prefix}fresh-final fallback started  tools=none  streaming=false")
        elif event_type == "fresh_final_generation_completed":
            self._line(f"{prefix}fresh-final completed")
        elif event_type == "fresh_quality_regeneration_started":
            self._line(f"{prefix}fresh quality repair started")
        elif event_type == "fresh_quality_regeneration_failed":
            self._line(f"{prefix}fresh quality repair failed")
        elif event_type == "validation_completed":
            counts = data.get("issue_counts") if isinstance(data.get("issue_counts"), dict) else {}
            self._line(f"{prefix}validation  status={data.get('validation_status') or 'unknown'}  blockers={int(counts.get('blocking') or 0)}")
        elif event_type == "candidate_artifact_saved":
            self._line(f"{prefix}candidate saved  reviewable=true")
        elif event_type == "agent_run_completed":
            self._line(f"{prefix}agent completed")
        elif event_type == "agent_run_failed":
            self._line(f"{prefix}agent failed")
        elif event_type == "tool_call_completed":
            self._line(f"{prefix}tool completed  name={data.get('tool_name') or 'tool'}")

    def _render_heartbeat(self, state: dict[str, Any]) -> None:
        stages = list(state.get("stages") or [])
        completed = sum(item.get("status") == "completed" for item in stages)
        total = len(stages)
        if self._current_model and self._current_chapter_id:
            started = self._current_model.get("timestamp")
            self._line(f"[{datetime.now().strftime('%H:%M:%S')}] … {self._chapter_label(self._current_chapter_id)}")
            self._line(f"            step={self._current_model.get('step')}/{self._current_model.get('max_steps')}  phase={self._current_model.get('phase') or 'writing'}")
            self._line(f"            waiting=model  model_elapsed={_duration(_elapsed_seconds(started))}")
            self._line(f"            fulltext_completed={sum(1 for item in self._chapter_info.values() if item.get('status') == 'completed')}/{self._fulltext_expected or '?'}  pipeline_completed={completed}/{total}")
            return
        current = str(state.get("current_stage") or "pipeline")
        running = next((item for item in stages if item.get("stage_id") == current), {})
        self._line(f"[{datetime.now().strftime('%H:%M:%S')}] … {current} running")
        self._line(f"            elapsed={_duration(_elapsed_seconds(running.get('started_at')))}  completed={completed}/{total}")

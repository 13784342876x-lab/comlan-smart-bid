from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from config import BACKEND_ROOT
from app.technical_fulltext_composer import atomic_write_json
from app.technical_writing_agent import TechnicalWritingAgentInput, build_technical_writing_agent
from app.web.dtos import WritingGenerationDTO, WritingGenerationListDTO
from app.web.writing_plan_query_service import WritingPlanReadService


SCHEMA_VERSION = "writing_generation_run_v1"
GENERATION_ID_PATTERN = re.compile(r"^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
ACTIVE_STATUSES = {"queued", "running", "validating"}


class WritingGenerationError(RuntimeError):
    pass


class WritingGenerationNotFound(LookupError):
    pass


class WritingGenerationConflict(WritingGenerationError):
    pass


class WritingGenerationValidationError(WritingGenerationError):
    pass


class GenerationLauncher(Protocol):
    def launch(self, *, project_id: str, generation_id: str) -> None: ...


class DetachedGenerationLauncher:
    def __init__(self, outputs_root: Path) -> None:
        self.outputs_root = outputs_root.resolve()

    def launch(self, *, project_id: str, generation_id: str) -> None:
        log_dir = self._project_root(project_id) / "generation_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "app.web.writing_generation_worker",
            "--outputs-root",
            str(self.outputs_root),
            "--project-id",
            project_id,
            "--generation-id",
            generation_id,
        ]
        creationflags = 0
        options: dict[str, Any] = {"cwd": str(BACKEND_ROOT), "close_fds": True}
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            options["start_new_session"] = True
        with (log_dir / f"{generation_id}.log").open("ab") as stream:
            subprocess.Popen(
                command,
                stdout=stream,
                stderr=stream,
                creationflags=creationflags,
                **options,
            )

    def _project_root(self, project_id: str) -> Path:
        target = (self.outputs_root / "web_projects" / project_id).resolve()
        try:
            target.relative_to((self.outputs_root / "web_projects").resolve())
        except ValueError as exc:
            raise WritingGenerationNotFound("project is not available") from exc
        if not (target / "project.json").is_file():
            raise WritingGenerationNotFound("project is not available")
        return target


class WritingGenerationService:
    """Durable task-generation attempts over the existing Stage9 agent contract."""

    def __init__(
        self,
        outputs_root: str | Path,
        writing_plan: WritingPlanReadService,
        launcher: GenerationLauncher | None = None,
        agent_factory: Callable[..., Any] = build_technical_writing_agent,
    ) -> None:
        self.outputs_root = Path(outputs_root).expanduser().resolve()
        self.writing_plan = writing_plan
        self.launcher = launcher or DetachedGenerationLauncher(self.outputs_root)
        self.agent_factory = agent_factory
        self._lock = threading.RLock()

    def start(
        self,
        project_id: str,
        task_id: str,
        expected_input_fingerprint: str,
    ) -> WritingGenerationDTO:
        with self._lock:
            plan = self.writing_plan.get(project_id)
            if not any(item.task_id == task_id for item in plan.tasks):
                raise WritingGenerationNotFound(
                    f"Writing Task is not available: {task_id}"
                )
            inputs = self.writing_plan.generation_inputs(project_id, task_id)
            current_fingerprint = str(inputs["handoff"].get("fingerprint") or "")
            if expected_input_fingerprint != current_fingerprint:
                raise WritingGenerationConflict(
                    "writing context changed; refresh before starting generation"
                )
            task = inputs["task_context"]
            if str(task.get("generation_policy") or "") == "blocked":
                raise WritingGenerationValidationError("Writing Task is not generatable")
            for state in self._states(project_id):
                if state.get("task_id") == task_id and state.get("status") in ACTIVE_STATUSES:
                    raise WritingGenerationConflict(
                        "an active generation already exists for this Writing Task"
                    )
            generation_id = _new_generation_id()
            now = _utc_now()
            state = {
                "schema_version": SCHEMA_VERSION,
                "generation_id": generation_id,
                "project_id": project_id,
                "task_id": task_id,
                "chapter_id": str(task.get("chapter_id") or ""),
                "section_id": str(task.get("section_id") or ""),
                "input_fingerprint": current_fingerprint,
                "task_fingerprint": str(task.get("task_fingerprint") or ""),
                "status": "queued",
                "current_step": "queued",
                "created_at": now,
                "started_at": None,
                "updated_at": now,
                "completed_at": None,
                "output_key": generation_id,
                "placeholder_ids": [
                    str(item.get("placeholder_id") or "")
                    for item in task.get("placeholder_requirements") or []
                    if item.get("placeholder_id")
                ],
                "validation_errors": [],
                "error": None,
                "model_profile": None,
                "result": {},
            }
            self._save_state(project_id, state)
            try:
                self.launcher.launch(project_id=project_id, generation_id=generation_id)
            except Exception as exc:
                state["status"] = "failed"
                state["current_step"] = "worker_launch_failed"
                state["completed_at"] = _utc_now()
                state["error"] = {
                    "code": "worker_launch_failed",
                    "message": _safe_error(exc, self.outputs_root),
                }
                self._save_state(project_id, state)
                raise WritingGenerationError("generation worker could not be started") from exc
            return self._dto(project_id, state)

    def execute(self, project_id: str, generation_id: str) -> WritingGenerationDTO:
        """Worker entry point. All inputs are re-resolved immediately before the model call."""
        state = self._load_state(project_id, generation_id)
        if state.get("status") != "queued":
            if state.get("status") in ACTIVE_STATUSES:
                raise WritingGenerationConflict("generation is already running")
            return self._dto(project_id, state)
        try:
            inputs = self.writing_plan.generation_inputs(project_id, str(state["task_id"]))
            handoff = inputs["handoff"]
            if str(handoff.get("fingerprint") or "") != state["input_fingerprint"]:
                raise WritingGenerationConflict(
                    "writing context changed before the generation worker started"
                )
            task = inputs["task_context"]
            if str(task.get("task_fingerprint") or "") != state["task_fingerprint"]:
                raise WritingGenerationConflict("Writing Task context changed")
            self._update(project_id, state, status="running", current_step="resolving_context", started_at=_utc_now())
            source = inputs["source"]
            output_root = self._project_root(project_id) / "writing_outputs"
            agent_input = TechnicalWritingAgentInput(
                response_outline_path=source.response_path,
                writing_outline_path=source.writing_path,
                task_information_path=source.information_path,
                draft_units_path=source.draft_units_path,
                confirmed_project_facts_path=inputs["confirmed_project_facts_path"],
                writing_constraint_context=handoff,
                output_key=generation_id,
                target_section_id=str(task.get("section_id") or ""),
                web_search_policy="auto",
            )

            def on_event(event: dict[str, Any]) -> None:
                event_type = str(event.get("event_type") or event.get("type") or "")
                phase = str(event.get("phase") or "")
                status = "validating" if phase == "validating" else "running"
                self._update(
                    project_id,
                    state,
                    status=status,
                    current_step=event_type or phase or "generating",
                )

            self._update(project_id, state, status="running", current_step="model_generation")
            agent = self.agent_factory(agent_input, output_dir=output_root)
            result = agent.run(
                event_handler=on_event,
                event_log_path=output_root / generation_id / "events.jsonl",
                stream_model=False,
            )
            self._update(project_id, state, status="validating", current_step="validating_result")
            validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
            output_reference = (result.get("outputs") or {}).get("formal_markdown")
            draft_path = self._safe_output_path(output_root, output_reference)
            draft = draft_path.read_text(encoding="utf-8-sig") if draft_path else None
            required_tokens = [
                str(item.get("placeholder_token") or "")
                for item in task.get("placeholder_requirements") or []
                if item.get("placeholder_token")
            ]
            missing_tokens = [token for token in required_tokens if token not in (draft or "")]
            validation_errors = [str(value) for value in validation.get("errors") or []]
            validation_errors.extend(
                f"required placeholder omitted: {token}" for token in missing_tokens
            )
            completed = (
                result.get("status") == "completed"
                and bool(validation.get("valid"))
                and draft is not None
                and not missing_tokens
            )
            state["result"] = {
                "draft_markdown": draft,
                "generated_input_fingerprint": state["input_fingerprint"],
            }
            state["model_profile"] = _model_name(result.get("model_profile"))
            state["validation_errors"] = validation_errors
            state["status"] = "completed" if completed else "validation_failed"
            state["current_step"] = "completed" if completed else "validation_failed"
            state["completed_at"] = _utc_now()
            self._save_state(project_id, state)
        except WritingGenerationConflict as exc:
            state["status"] = "failed"
            state["current_step"] = "stale_writing_context"
            state["completed_at"] = _utc_now()
            state["error"] = {"code": "stale_writing_context", "message": str(exc)}
            self._save_state(project_id, state)
        except Exception as exc:
            state["status"] = "failed"
            state["current_step"] = "application_error"
            state["completed_at"] = _utc_now()
            state["error"] = {
                "code": "application_error",
                "message": _safe_error(exc, self.outputs_root),
            }
            self._save_state(project_id, state)
        return self.get(project_id, generation_id)

    def get(self, project_id: str, generation_id: str) -> WritingGenerationDTO:
        return self._dto(project_id, self._load_state(project_id, generation_id))

    def list(self, project_id: str) -> WritingGenerationListDTO:
        try:
            self._project_root(project_id)
        except WritingGenerationNotFound:
            # Historical artifact projects remain readable, but never receive
            # Web generation state unless they have a registered Project root.
            self.writing_plan.get(project_id)
            return WritingGenerationListDTO(project_id=project_id, generations=[])
        return WritingGenerationListDTO(
            project_id=project_id,
            generations=[self._dto(project_id, state) for state in self._states(project_id)],
        )

    def _dto(self, project_id: str, state: dict[str, Any]) -> WritingGenerationDTO:
        current = True
        try:
            current = (
                str(self.writing_plan.stage9_input_context(project_id).get("fingerprint") or "")
                == str(state.get("input_fingerprint") or "")
            )
        except Exception:
            current = False
        error = state.get("error") if isinstance(state.get("error"), dict) else {}
        result = state.get("result") if isinstance(state.get("result"), dict) else {}
        status = str(state.get("status") or "failed")
        return WritingGenerationDTO(
            generation_id=str(state["generation_id"]),
            project_id=project_id,
            task_id=str(state["task_id"]),
            chapter_id=str(state.get("chapter_id") or ""),
            section_id=str(state.get("section_id") or ""),
            input_fingerprint=str(state.get("input_fingerprint") or ""),
            task_fingerprint=str(state.get("task_fingerprint") or ""),
            status=status,  # type: ignore[arg-type]
            status_label={
                "queued": "等待生成",
                "running": "正在生成",
                "validating": "正在校验",
                "completed": "生成完成",
                "failed": "生成失败",
                "validation_failed": "结果校验未通过",
            }.get(status, status),
            current_step=str(state.get("current_step") or "") or None,
            created_at=str(state.get("created_at") or ""),
            started_at=str(state.get("started_at") or "") or None,
            updated_at=str(state.get("updated_at") or ""),
            completed_at=str(state.get("completed_at") or "") or None,
            draft_markdown=result.get("draft_markdown"),
            placeholder_ids=[str(value) for value in state.get("placeholder_ids") or []],
            validation_errors=[str(value) for value in state.get("validation_errors") or []],
            error_code=str(error.get("code") or "") or None,
            error_message=str(error.get("message") or "") or None,
            generated_from_current_context=current,
            model_profile=str(state.get("model_profile") or "") or None,
        )

    def _states(self, project_id: str) -> list[dict[str, Any]]:
        root = self._generation_root(project_id)
        values: list[dict[str, Any]] = []
        if root.is_dir():
            for path in root.glob("*/generation.json"):
                try:
                    values.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(values, key=lambda value: str(value.get("created_at") or ""), reverse=True)

    def _load_state(self, project_id: str, generation_id: str) -> dict[str, Any]:
        if not GENERATION_ID_PATTERN.fullmatch(generation_id):
            raise WritingGenerationNotFound("generation is not available")
        path = self._state_path(project_id, generation_id)
        if not path.is_file():
            raise WritingGenerationNotFound("generation is not available")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WritingGenerationError("generation state is unavailable") from exc
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("project_id") != project_id
            or value.get("generation_id") != generation_id
        ):
            raise WritingGenerationError("generation state identity is invalid")
        return value

    def _update(self, project_id: str, state: dict[str, Any], **values: Any) -> None:
        state.update(values)
        state["updated_at"] = _utc_now()
        self._save_state(project_id, state)

    def _save_state(self, project_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(project_id, str(state["generation_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            path,
            state,
            required_fields=("schema_version", "generation_id", "project_id", "task_id", "status"),
        )

    def _state_path(self, project_id: str, generation_id: str) -> Path:
        if not GENERATION_ID_PATTERN.fullmatch(generation_id):
            raise WritingGenerationNotFound("generation is not available")
        return self._generation_root(project_id) / generation_id / "generation.json"

    def _generation_root(self, project_id: str) -> Path:
        return self._project_root(project_id) / "writing_generations"

    def _project_root(self, project_id: str) -> Path:
        root = (self.outputs_root / "web_projects").resolve()
        target = (root / project_id).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise WritingGenerationNotFound("project is not available") from exc
        if not (target / "project.json").is_file():
            raise WritingGenerationNotFound("project is not available")
        return target

    @staticmethod
    def _safe_output_path(root: Path, reference: Any) -> Path | None:
        if not reference:
            return None
        path = Path(str(reference)).expanduser().resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None


def _new_generation_id() -> str:
    return f"gen-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: Exception, outputs_root: Path) -> str:
    value = f"{type(exc).__name__}: {exc}".replace(str(outputs_root), "[server output]")
    return re.sub(r"[A-Za-z]:[\\/][^\n;]+", "[server path]", value)[:1000]


def _model_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("model") or "") or None
    return str(value or "") or None

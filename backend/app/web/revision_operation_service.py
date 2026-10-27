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
from typing import Any, Protocol

from config import BACKEND_ROOT
from app.technical_fulltext_composer import atomic_write_json, load_json_object, sha256_file
from app.technical_fulltext_revision_review import review_stage10_revision
from app.technical_fulltext_targeted_revision import execute_targeted_section_revision
from app.web.command_service import PreconditionRequiredError
from app.web.dtos import RevisionOperationDTO
from app.web.project_lifecycle_service import (
    ProjectLifecycleConflictError,
    ProjectLifecycleService,
)
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.writing_plan_query_service import WritingPlanReadService


SCHEMA_VERSION = "stage13_revision_operation_v1"
OPERATION_ID_PATTERN = re.compile(r"^revop-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
ACTIVE_STATUSES = {"queued", "running"}


class RevisionOperationError(RuntimeError):
    pass


class RevisionOperationConflict(RevisionOperationError):
    pass


class RevisionOperationLauncher(Protocol):
    def launch(self, *, project_id: str, operation_id: str) -> None: ...


class DetachedRevisionOperationLauncher:
    def __init__(self, outputs_root: Path) -> None:
        self.outputs_root = outputs_root.resolve()

    def launch(self, *, project_id: str, operation_id: str) -> None:
        project_root = _project_root(self.outputs_root, project_id)
        log_path = project_root / "revision_operation.log"
        command = [
            sys.executable,
            "-m",
            "app.web.revision_operation_worker",
            "--outputs-root",
            str(self.outputs_root),
            "--project-id",
            project_id,
            "--operation-id",
            operation_id,
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
        with log_path.open("ab") as stream:
            subprocess.Popen(
                command,
                stdout=stream,
                stderr=stream,
                creationflags=creationflags,
                **options,
            )


class RevisionOperationService:
    """Run the current project's one explicit Stage12 revision candidate."""

    def __init__(
        self,
        outputs_root: str | Path,
        resolver: ReadOnlyProjectResolver,
        lifecycle: ProjectLifecycleService,
        writing_plan: WritingPlanReadService,
        launcher: RevisionOperationLauncher | None = None,
        revision_llm: Any = None,
        semantic_llm_client: Any = None,
        metadata_store: Any = None,
    ) -> None:
        self.outputs_root = Path(outputs_root).expanduser().resolve()
        self.resolver = resolver
        self.lifecycle = lifecycle
        self.writing_plan = writing_plan
        self.launcher = launcher or DetachedRevisionOperationLauncher(self.outputs_root)
        self.revision_llm = revision_llm
        self.semantic_llm_client = semantic_llm_client
        self.metadata_store = metadata_store
        self._lock = threading.RLock()

    def start(
        self,
        project_id: str,
        *,
        source_type: str,
        source_id: str,
        if_match: str | None,
    ) -> RevisionOperationDTO:
        if if_match is None:
            raise PreconditionRequiredError("If-Match is required for AI modification")
        with self._lock:
            existing = self._load_optional(project_id)
            if existing is not None and existing.get("status") in ACTIVE_STATUSES:
                raise RevisionOperationConflict(
                    "an AI modification is already active for this project"
                )
            artifacts = self.resolver.resolve(project_id)
            human_sha = sha256_file(artifacts.human_review_json)
            if _normalize_etag(if_match) != human_sha:
                raise RevisionOperationConflict(
                    "Human Review changed; refresh before starting AI modification"
                )
            handoff_path, handoff = self._ready_handoff(
                artifacts.root, human_sha, source_type, source_id
            )
            operation_id = _new_operation_id()
            now = _utc_now()
            state = {
                "schema_version": SCHEMA_VERSION,
                "operation_id": operation_id,
                "project_id": project_id,
                "source_type": source_type,
                "source_id": source_id,
                "status": "queued",
                "phase": "rewriting",
                "created_at": now,
                "started_at": None,
                "updated_at": now,
                "completed_at": None,
                "error": None,
                "parent": {
                    "artifact_root": str(artifacts.root.resolve()),
                    "fulltext_path": str(artifacts.fulltext_json.resolve()),
                    "fulltext_sha256": sha256_file(artifacts.fulltext_json),
                    "output_key": str(
                        load_json_object(
                            artifacts.commit_json, label="current Stage10 commit"
                        ).get("output_key")
                        or ""
                    ),
                    "human_review_sha256": human_sha,
                    "handoff_path": str(handoff_path),
                    "handoff_sha256": sha256_file(handoff_path),
                    "handoff_id": handoff["artifact_identity"]["handoff_id"],
                },
                "output_key": _new_output_key(),
            }
            self._save(project_id, state)
            try:
                self.launcher.launch(project_id=project_id, operation_id=operation_id)
            except (OSError, subprocess.SubprocessError) as exc:
                self._fail(project_id, state, "worker_launch_failed", exc)
                raise RevisionOperationError(
                    "AI modification worker could not be started"
                ) from exc
            return self._dto(state)

    def current(self, project_id: str) -> RevisionOperationDTO | None:
        state = self._load_optional(project_id)
        return self._dto(state) if state is not None else None

    def execute(self, project_id: str, operation_id: str) -> RevisionOperationDTO:
        state = self._load(project_id)
        if state.get("operation_id") != operation_id:
            raise RevisionOperationConflict("AI modification operation is no longer current")
        if state.get("status") != "queued":
            if state.get("status") in ACTIVE_STATUSES:
                raise RevisionOperationConflict("AI modification is already running")
            return self._dto(state)
        try:
            artifacts = self._verify_worker_inputs(state)
            self._update(
                project_id,
                state,
                status="running",
                phase="rewriting",
                started_at=_utc_now(),
            )
            stage9_source, draft_units_path = self.writing_plan.revision_inputs(project_id)
            revised = execute_targeted_section_revision(
                source_fulltext_path=artifacts.fulltext_json,
                stage12_handoff_path=state["parent"]["handoff_path"],
                candidate_source_type=state["source_type"],
                candidate_source_id=state["source_id"],
                stage9_source=stage9_source,
                draft_units_path=draft_units_path,
                output_dir=artifacts.root.parent,
                output_key=state["output_key"],
                llm=self.revision_llm,
                metadata_store=self.metadata_store,
            )
            revision_fulltext = Path(
                revised["materialization"]["outputs"]["technical_fulltext_json"]
            ).resolve()
            self._update(project_id, state, phase="reviewing")
            review_stage10_revision(
                fulltext_json_path=revision_fulltext,
                semantic_llm_client=self.semantic_llm_client,
            )
            self._update(project_id, state, phase="publishing")
            self.lifecycle.publish_current_artifact_set(
                project_id,
                fulltext_output_dir=revision_fulltext.parent.parent.parent,
                output_key=state["output_key"],
            )
            self._update(
                project_id,
                state,
                status="completed",
                phase="publishing",
                completed_at=_utc_now(),
            )
        except ProjectLifecycleConflictError as exc:
            self._fail(project_id, state, "stale_revision", exc)
        except Exception as exc:
            self._fail(project_id, state, "revision_failed", exc)
        return self._dto(state)

    def _verify_worker_inputs(self, state: dict[str, Any]):
        artifacts = self.resolver.resolve(str(state["project_id"]))
        parent = state["parent"]
        checks = (
            (str(artifacts.root.resolve()), parent["artifact_root"]),
            (str(artifacts.fulltext_json.resolve()), parent["fulltext_path"]),
            (sha256_file(artifacts.fulltext_json), parent["fulltext_sha256"]),
            (sha256_file(artifacts.human_review_json), parent["human_review_sha256"]),
            (sha256_file(parent["handoff_path"]), parent["handoff_sha256"]),
        )
        if any(actual != expected for actual, expected in checks):
            raise ProjectLifecycleConflictError(
                "current review changed before AI modification started"
            )
        handoff = load_json_object(parent["handoff_path"], label="Stage13 handoff")
        if (handoff.get("artifact_identity") or {}).get("handoff_id") != parent[
            "handoff_id"
        ]:
            raise ProjectLifecycleConflictError(
                "Stage13 handoff changed before AI modification started"
            )
        return artifacts

    @staticmethod
    def _ready_handoff(
        artifact_root: Path,
        human_sha: str,
        source_type: str,
        source_id: str,
    ) -> tuple[Path, dict[str, Any]]:
        review_root = artifact_root / "fulltext" / "review"
        handoff_path = review_root / "technical_fulltext.stage13_handoff.json"
        if not handoff_path.is_file():
            raise RevisionOperationConflict(
                "saved Human Review is not ready for AI modification"
            )
        handoff = load_json_object(handoff_path, label="ready Stage13 handoff")
        identity = handoff.get("artifact_identity") or {}
        human_ref = (handoff.get("source_lineage") or {}).get(
            "stage12b_human_review"
        ) or {}
        if identity.get("status") != "ready" or human_ref.get("sha256") != human_sha:
            raise RevisionOperationConflict(
                "saved Human Review is not ready for AI modification"
            )
        matches = [
            item
            for item in handoff.get("revision_candidates") or []
            if isinstance(item, dict)
            and (item.get("source_type"), item.get("source_id"))
            == (source_type, source_id)
        ]
        if len(matches) != 1:
            raise RevisionOperationConflict(
                "requested saved revision candidate must match exactly once"
            )
        candidate = matches[0]
        decision = str(candidate.get("human_decision") or "")
        instruction = str(candidate.get("effective_revision_instruction") or "").strip()
        if decision == "accepted":
            action = candidate.get("machine_recommended_action") or {}
            machine_instruction = (
                str(action.get("text") or "").strip()
                if isinstance(action, dict)
                else ""
            )
            if (
                candidate.get("handoff_mode")
                not in {"revision", "structural_normalization"}
                or not machine_instruction
                or not instruction
                or candidate.get("human_revision_instruction")
            ):
                raise RevisionOperationConflict(
                    "accepted candidate has no executable machine recommendation"
                )
        elif decision == "modified":
            if not instruction:
                raise RevisionOperationConflict(
                    "saved revision candidate has no modification instruction"
                )
        else:
            raise RevisionOperationConflict(
                "saved revision candidate is not eligible for AI modification"
            )
        return handoff_path.resolve(), handoff

    def _dto(self, state: dict[str, Any]) -> RevisionOperationDTO:
        error = state.get("error") if isinstance(state.get("error"), dict) else {}
        status = str(state["status"])
        phase = str(state["phase"])
        return RevisionOperationDTO(
            operation_id=str(state["operation_id"]),
            project_id=str(state["project_id"]),
            source_type=str(state["source_type"]),
            source_id=str(state["source_id"]),
            status=status,  # type: ignore[arg-type]
            phase=phase,  # type: ignore[arg-type]
            status_label={
                "queued": "等待 AI 修改",
                "running": {
                    "rewriting": "正在修改标书…",
                    "reviewing": "正在重新审核…",
                    "publishing": "正在更新结果…",
                }.get(phase, "AI 修改进行中"),
                "completed": "AI 修改已完成",
                "failed": "AI 修改失败",
            }.get(status, status),
            created_at=str(state["created_at"]),
            started_at=state.get("started_at"),
            updated_at=str(state["updated_at"]),
            completed_at=state.get("completed_at"),
            error_code=str(error.get("code") or "") or None,
            error_message=str(error.get("message") or "") or None,
        )

    def _load_optional(self, project_id: str) -> dict[str, Any] | None:
        path = self._state_path(project_id)
        if not path.is_file():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RevisionOperationError("AI modification state is unavailable") from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != SCHEMA_VERSION
            or state.get("project_id") != project_id
            or not OPERATION_ID_PATTERN.fullmatch(str(state.get("operation_id") or ""))
        ):
            raise RevisionOperationError("AI modification state identity is invalid")
        return state

    def _load(self, project_id: str) -> dict[str, Any]:
        state = self._load_optional(project_id)
        if state is None:
            raise RevisionOperationError("AI modification operation is unavailable")
        return state

    def _save(self, project_id: str, state: dict[str, Any]) -> None:
        atomic_write_json(
            self._state_path(project_id),
            state,
            required_fields=(
                "schema_version",
                "operation_id",
                "project_id",
                "source_type",
                "source_id",
                "status",
                "phase",
                "parent",
                "output_key",
            ),
        )

    def _update(self, project_id: str, state: dict[str, Any], **values: Any) -> None:
        state.update(values)
        state["updated_at"] = _utc_now()
        self._save(project_id, state)

    def _fail(
        self,
        project_id: str,
        state: dict[str, Any],
        code: str,
        exc: Exception,
    ) -> None:
        state["status"] = "failed"
        state["completed_at"] = _utc_now()
        state["error"] = {"code": code, "message": _safe_error(exc, self.outputs_root)}
        self._update(project_id, state)

    def _state_path(self, project_id: str) -> Path:
        return _project_root(self.outputs_root, project_id) / "revision_operation.json"


def _project_root(outputs_root: Path, project_id: str) -> Path:
    registry = (outputs_root / "web_projects").resolve()
    target = (registry / project_id).resolve()
    try:
        target.relative_to(registry)
    except ValueError as exc:
        raise RevisionOperationError("project is not available") from exc
    if not (target / "project.json").is_file():
        raise RevisionOperationError("project is not available")
    return target


def _normalize_etag(value: str) -> str:
    token = value.strip()
    if token.startswith("W/"):
        token = token[2:].strip()
    return token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token


def _new_operation_id() -> str:
    return f"revop-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _new_output_key() -> str:
    return f"stage13-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: Exception, outputs_root: Path) -> str:
    value = f"{type(exc).__name__}: {exc}".replace(str(outputs_root), "[server output]")
    return re.sub(r"[A-Za-z]:[\\/][^\n;]+", "[server path]", value)[:1000]

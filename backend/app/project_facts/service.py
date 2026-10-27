from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from app.technical_fulltext_composer import atomic_write_json, stable_json_sha256


SCHEMA_VERSION = "confirmed_project_facts_v1"
WRITING_CONTEXT_SCHEMA_VERSION = "project_writing_context_v1"
MAX_VALUE_BYTES = 32 * 1024
MAX_TEXT = 4000


class ProjectFactError(RuntimeError):
    pass


class ProjectFactNotFound(ProjectFactError):
    pass


class ProjectFactValidationError(ProjectFactError):
    pass


class ProjectFactLineageConflict(ProjectFactError):
    pass


@dataclass(frozen=True)
class ProjectFactSource:
    project_id: str
    source_mode: str
    analysis_run_id: str | None
    fact_request_path: Path
    writing_tasks_path: Path
    existing_confirmed_facts_path: Path | None = None


@dataclass(frozen=True)
class ProjectFactCommand:
    status: str
    value: Any
    source_description: str
    confirmation_basis: str
    note: str | None = None
    change_reason: str | None = None


@dataclass(frozen=True)
class ProjectFactSnapshot:
    source: ProjectFactSource
    fact_request: dict[str, Any]
    writing_tasks: dict[str, Any]
    state: dict[str, Any]
    token: str
    state_path: Path

    @property
    def facts(self) -> dict[str, Any]:
        return dict(self.state.get("facts") or {})

    @property
    def writing_context(self) -> dict[str, Any]:
        return dict(self.state.get("writing_context") or {})


class ProjectFactApplicationService:
    """Confirm project facts without mutating the historical Stage4 request.

    The canonical artifact deliberately contains both confirmed facts and its
    deterministic writing-context projection.  A single atomic replace keeps
    those two views consistent and the top-level ``facts`` mapping remains
    directly consumable by the existing Stage9 loader.
    """

    def __init__(
        self,
        outputs_root: str | Path,
        source_resolver: Callable[[str], ProjectFactSource],
    ) -> None:
        self.outputs_root = Path(outputs_root).expanduser().resolve()
        self.source_resolver = source_resolver
        self.state_root = (self.outputs_root / "project_fact_states").resolve()
        self._lock = threading.RLock()

    def snapshot(self, project_id: str) -> ProjectFactSnapshot:
        source = self.source_resolver(project_id)
        request_path = self._safe_file(source.fact_request_path)
        tasks_path = self._safe_file(source.writing_tasks_path)
        request = _load_object(request_path, "project fact request")
        tasks = _load_object(tasks_path, "section writing tasks")
        if request.get("schema_version") != "project_fact_request_v0":
            raise ProjectFactValidationError("unsupported project fact request schema")
        lineage = self._lineage(source, request_path, tasks_path)
        state_path = self._state_path(project_id, lineage)
        if state_path.is_file():
            state = _load_object(state_path, "confirmed project facts")
            self._validate_state(state, project_id, lineage)
        else:
            state = self._initial_state(source, request, tasks, lineage)
        token = self._token(project_id, lineage, state)
        return ProjectFactSnapshot(source, request, tasks, state, token, state_path)

    def confirm(
        self,
        project_id: str,
        fact_id: str,
        command: ProjectFactCommand,
        expected_token: str,
    ) -> ProjectFactSnapshot:
        with self._lock:
            current = self.snapshot(project_id)
            if _normalize_etag(expected_token) != current.token:
                raise ProjectFactLineageConflict(
                    "写作准备内容已发生变化或所属分析 Run 已更新，请刷新后重试"
                )
            slot = _find_slot(current.fact_request, fact_id)
            if command.status != "confirmed":
                raise ProjectFactValidationError("V1 project facts only accept confirmed status")
            fact_key = str(slot.get("fact_key") or "").strip()
            value = _validated_value(fact_key, command.value)
            source_description = _bounded_text(command.source_description, "source_description", required=True)
            basis = _bounded_text(command.confirmation_basis, "confirmation_basis", required=True)
            note = _bounded_text(command.note, "note", required=False)
            reason = _bounded_text(command.change_reason, "change_reason", required=False)
            facts = copy.deepcopy(current.facts)
            previous = facts.get(fact_key)
            if previous is not None and not reason:
                raise ProjectFactValidationError("修改已确认事实时必须填写变更说明")
            now = _utc_now()
            refs = _dependency_refs(slot, current.writing_tasks)
            record = {
                "fact_id": fact_id,
                "source_request_id": fact_id,
                "fact_key": fact_key,
                "label": str(slot.get("name") or fact_key),
                "status": "confirmed",
                "value": value,
                "source_type": "manual_confirmation",
                "source_description": source_description,
                "confirmation_basis": basis,
                "note": note,
                "confirmed_at": str((previous or {}).get("confirmed_at") or now),
                "updated_at": now,
                **refs,
            }
            facts[fact_key] = record
            history = copy.deepcopy(current.state.get("change_history") or [])
            history.append(
                {
                    "fact_id": fact_id,
                    "fact_key": fact_key,
                    "previous_status": str((previous or {}).get("status") or "missing"),
                    "previous_value": copy.deepcopy((previous or {}).get("value")),
                    "new_status": "confirmed",
                    "new_value": copy.deepcopy(value),
                    "changed_at": now,
                    "change_reason": reason,
                    "source_description": source_description,
                    "confirmation_basis": basis,
                    "actor": None,
                }
            )
            state = {
                "schema_version": SCHEMA_VERSION,
                "project_id": project_id,
                "lineage": copy.deepcopy(current.state["lineage"]),
                "revision": int(current.state.get("revision") or 0) + 1,
                "created_at": now if int(current.state.get("revision") or 0) == 0 else str(current.state.get("created_at") or now),
                "updated_at": now,
                "facts": facts,
                "writing_context": _build_writing_context(current.fact_request, current.writing_tasks, facts),
                "change_history": history,
            }
            state["content_sha256"] = _state_content_sha(state)
            _validate_schema(state)
            current.state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                current.state_path,
                state,
                required_fields=("schema_version", "project_id", "lineage", "facts", "writing_context", "change_history"),
            )
            return self.snapshot(project_id)

    def _initial_state(
        self,
        source: ProjectFactSource,
        request: dict[str, Any],
        tasks: dict[str, Any],
        lineage: dict[str, Any],
    ) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        for slot in request.get("slots") or []:
            if not isinstance(slot, dict):
                continue
            key = str(slot.get("fact_key") or "")
            if slot.get("status") == "confirmed" and _has_value(slot.get("value")):
                facts[key] = _source_record(slot)
        reference = source.existing_confirmed_facts_path
        if reference:
            path = self._safe_optional_file(reference)
            if path:
                payload = _load_object(path, "existing confirmed project facts")
                for key, value in dict(payload.get("facts") or {}).items():
                    if isinstance(value, dict) and str(value.get("status") or value.get("confirmation_status") or "") == "confirmed":
                        facts[str(key)] = copy.deepcopy(value)
        now = "not_persisted"
        state = {
            "schema_version": SCHEMA_VERSION,
            "project_id": source.project_id,
            "lineage": lineage,
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "facts": facts,
            "writing_context": _build_writing_context(request, tasks, facts),
            "change_history": [],
        }
        state["content_sha256"] = _state_content_sha(state)
        return state

    def _lineage(self, source: ProjectFactSource, request_path: Path, tasks_path: Path) -> dict[str, Any]:
        return {
            "source_mode": source.source_mode,
            "analysis_run_id": source.analysis_run_id,
            "fact_request_sha256": _sha256_file(request_path),
            "writing_tasks_sha256": _sha256_file(tasks_path),
        }

    def _state_path(self, project_id: str, lineage: dict[str, Any]) -> Path:
        project_key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
        lineage_key = stable_json_sha256(lineage)[:24]
        target = (self.state_root / project_key / lineage_key / "confirmed_project_facts.json").resolve()
        target.relative_to(self.state_root)
        return target

    def _safe_file(self, value: Path) -> Path:
        path = Path(value).expanduser().resolve()
        try:
            path.relative_to(self.outputs_root)
        except ValueError as exc:
            raise ProjectFactValidationError("project fact source is outside the configured output root") from exc
        if not path.is_file():
            raise ProjectFactValidationError("project fact source artifact is unavailable")
        return path

    def _safe_optional_file(self, value: Path) -> Path | None:
        path = Path(value).expanduser().resolve()
        try:
            path.relative_to(self.outputs_root)
        except ValueError as exc:
            raise ProjectFactValidationError("confirmed fact source is outside the configured output root") from exc
        return path if path.is_file() else None

    @staticmethod
    def _token(project_id: str, lineage: dict[str, Any], state: dict[str, Any]) -> str:
        return stable_json_sha256(
            {
                "project_id": project_id,
                "lineage": lineage,
                "revision": int(state.get("revision") or 0),
                "content_sha256": str(state.get("content_sha256") or _state_content_sha(state)),
            }
        )

    @staticmethod
    def _validate_state(state: dict[str, Any], project_id: str, lineage: dict[str, Any]) -> None:
        _validate_schema(state)
        if state.get("schema_version") != SCHEMA_VERSION or state.get("project_id") != project_id:
            raise ProjectFactValidationError("confirmed project fact artifact identity is invalid")
        if state.get("lineage") != lineage:
            raise ProjectFactLineageConflict("confirmed project facts belong to another analysis lineage")
        if state.get("content_sha256") != _state_content_sha(state):
            raise ProjectFactValidationError("confirmed project fact artifact content hash is invalid")


def _find_slot(request: dict[str, Any], fact_id: str) -> dict[str, Any]:
    slot = next(
        (item for item in request.get("slots") or [] if isinstance(item, dict) and str(item.get("fact_slot_id") or item.get("fact_id") or "") == fact_id),
        None,
    )
    if slot is None:
        raise ProjectFactNotFound(f"project fact request is not available: {fact_id}")
    return slot


def _validated_value(fact_key: str, value: Any) -> Any:
    if fact_key == "manufacturer_service_network":
        if not isinstance(value, dict):
            raise ProjectFactValidationError("manufacturer service network requires a structured value")
        allowed = {"address", "contact", "technician_count"}
        if set(value) != allowed:
            raise ProjectFactValidationError("manufacturer service network fields are invalid")
        address = _bounded_text(value.get("address"), "address", required=True)
        contact = _bounded_text(value.get("contact"), "contact", required=True)
        count = value.get("technician_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ProjectFactValidationError("technician_count must be a non-negative integer")
        result: Any = {"address": address, "contact": contact, "technician_count": count}
    else:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProjectFactValidationError("this project fact accepts a text or numeric value")
        result = value.strip() if isinstance(value, str) else value
        if result == "":
            raise ProjectFactValidationError("confirmed project fact value is required")
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_VALUE_BYTES:
        raise ProjectFactValidationError("project fact value is too large")
    return result


def _build_writing_context(
    request: dict[str, Any], tasks: dict[str, Any], facts: dict[str, Any]
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for slot in request.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        fact_id = str(slot.get("fact_slot_id") or slot.get("fact_id") or "")
        fact_key = str(slot.get("fact_key") or "")
        record = facts.get(fact_key)
        confirmed = isinstance(record, dict) and str(record.get("status") or record.get("confirmation_status") or "") == "confirmed" and _has_value(record.get("value"))
        refs = _dependency_refs(slot, tasks)
        items.append(
            {
                "context_id": fact_id,
                "fact_key": fact_key,
                "label": str(slot.get("name") or fact_key),
                "availability": "available" if confirmed else "placeholder_required",
                "value": copy.deepcopy(record.get("value")) if confirmed else None,
                "source_policy": str(slot.get("source_policy") or "confirmed_project_fact"),
                **refs,
            }
        )
    return {
        "schema_version": WRITING_CONTEXT_SCHEMA_VERSION,
        "items": items,
        "summary": {
            "available_count": sum(item["availability"] == "available" for item in items),
            "placeholder_required_count": sum(item["availability"] == "placeholder_required" for item in items),
        },
    }


def _dependency_refs(slot: dict[str, Any], tasks: dict[str, Any]) -> dict[str, list[str]]:
    task_index = {
        str(item.get("task_id") or ""): item
        for item in tasks.get("tasks") or []
        if isinstance(item, dict)
    }
    requirements: list[str] = []
    scores: list[str] = []
    writing: list[str] = []
    for task_id in slot.get("target_task_ids") or []:
        task = task_index.get(str(task_id), {})
        requirements.extend(
            str(item.get("response_point_id") or "")
            for item in task.get("response_point_mappings") or []
            if isinstance(item, dict) and item.get("response_point_id")
        )
        scores.extend(str(value) for value in task.get("score_item_refs") or [] if value)
        section = str(task.get("section_id") or task.get("chapter_id") or "")
        if section:
            writing.append(section)
    return {
        "requirement_refs": sorted(set(requirements)),
        "score_refs": sorted(set(scores)),
        "writing_refs": sorted(set(writing)),
    }


def _source_record(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": str(slot.get("fact_slot_id") or slot.get("fact_id") or ""),
        "source_request_id": str(slot.get("fact_slot_id") or slot.get("fact_id") or ""),
        "fact_key": str(slot.get("fact_key") or ""),
        "label": str(slot.get("name") or slot.get("fact_key") or ""),
        "status": "confirmed",
        "value": copy.deepcopy(slot.get("value")),
        "source_type": "analysis_supplied_project_context",
        "source_description": "confirmed project context supplied before Stage4 planning",
        "confirmation_basis": "pipeline_input",
        "note": None,
        "confirmed_at": None,
        "updated_at": None,
        "requirement_refs": [],
        "score_refs": [],
        "writing_refs": [],
    }


def _state_content_sha(state: dict[str, Any]) -> str:
    value = {key: copy.deepcopy(item) for key, item in state.items() if key != "content_sha256"}
    return stable_json_sha256(value)


def _bounded_text(value: Any, label: str, *, required: bool) -> str | None:
    text = str(value or "").strip()
    if required and not text:
        raise ProjectFactValidationError(f"{label} is required")
    if len(text) > MAX_TEXT:
        raise ProjectFactValidationError(f"{label} is too long")
    return text or None


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _normalize_etag(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectFactValidationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ProjectFactValidationError(f"{label} must contain an object")
    return value


def _validate_schema(state: dict[str, Any]) -> None:
    schema_path = Path(__file__).parent / "schemas" / "confirmed_project_facts_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(state), key=lambda item: list(item.path))
    if errors:
        raise ProjectFactValidationError(f"confirmed project fact artifact is invalid: {errors[0].message}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

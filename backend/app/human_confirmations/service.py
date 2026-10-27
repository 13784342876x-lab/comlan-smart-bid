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


SCHEMA_VERSION = "prewriting_human_confirmations_v1"
WRITING_CONTEXT_SCHEMA_VERSION = "prewriting_human_confirmation_context_v1"
MAX_TEXT = 4000


class HumanConfirmationError(RuntimeError):
    pass


class HumanConfirmationNotFound(HumanConfirmationError):
    pass


class HumanConfirmationValidationError(HumanConfirmationError):
    pass


class HumanConfirmationLineageConflict(HumanConfirmationError):
    pass


@dataclass(frozen=True)
class HumanConfirmationSource:
    project_id: str
    source_mode: str
    analysis_run_id: str | None
    response_context_path: Path | None
    fact_request_path: Path
    writing_tasks_path: Path


@dataclass(frozen=True)
class HumanConfirmationCommand:
    answer: bool | str
    source_description: str
    confirmation_basis: str
    note: str | None = None
    change_reason: str | None = None


@dataclass(frozen=True)
class HumanConfirmationSnapshot:
    source: HumanConfirmationSource
    state: dict[str, Any]
    token: str
    state_path: Path

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.state.get("confirmation_requests") or []]

    @property
    def decisions(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self.state.get("decisions") or {})


class HumanConfirmationApplicationService:
    """Persist pre-writing business choices without changing Stage4 or Stage8.

    Stage4 remains the immutable source of confirmation requests.  Requests
    that are actually Project Facts or material availability checks are
    classified but cannot be mutated through this service.
    """

    def __init__(
        self,
        outputs_root: str | Path,
        source_resolver: Callable[[str], HumanConfirmationSource],
    ) -> None:
        self.outputs_root = Path(outputs_root).expanduser().resolve()
        self.source_resolver = source_resolver
        self.state_root = (self.outputs_root / "project_confirmation_states").resolve()
        self._lock = threading.RLock()

    def snapshot(self, project_id: str) -> HumanConfirmationSnapshot:
        source = self.source_resolver(project_id)
        context_path = self._safe_optional_file(source.response_context_path)
        request_path = self._safe_file(source.fact_request_path)
        tasks_path = self._safe_file(source.writing_tasks_path)
        context = _load_object(context_path, "technical response context") if context_path else {}
        fact_request = _load_object(request_path, "project fact request")
        tasks = _load_object(tasks_path, "section writing tasks")
        if fact_request.get("schema_version") != "project_fact_request_v0":
            raise HumanConfirmationValidationError("unsupported project fact request schema")
        lineage = self._lineage(source, context_path, request_path, tasks_path)
        requests = _extract_requests(context, fact_request, tasks)
        state_path = self._state_path(project_id, lineage)
        if state_path.is_file():
            state = _load_object(state_path, "human confirmation state")
            self._validate_state(state, project_id, lineage, requests)
        else:
            state = self._initial_state(project_id, lineage, requests)
        return HumanConfirmationSnapshot(
            source=source,
            state=state,
            token=self._token(project_id, lineage, state),
            state_path=state_path,
        )

    def decide(
        self,
        project_id: str,
        confirmation_id: str,
        command: HumanConfirmationCommand,
        expected_token: str,
    ) -> HumanConfirmationSnapshot:
        with self._lock:
            current = self.snapshot(project_id)
            if _normalize_etag(expected_token) != current.token:
                raise HumanConfirmationLineageConflict(
                    "人工确认内容已变化或所属 Analysis Run 已更新，请刷新后重试"
                )
            request = next(
                (item for item in current.requests if item.get("confirmation_id") == confirmation_id),
                None,
            )
            if request is None:
                raise HumanConfirmationNotFound(
                    f"human confirmation request is not available: {confirmation_id}"
                )
            request_kind = str(request.get("request_kind") or "")
            if request_kind == "project_fact":
                raise HumanConfirmationValidationError(
                    "该确认本质上是 Project Fact，必须通过项目事实服务处理"
                )
            if request_kind == "material_availability":
                raise HumanConfirmationValidationError(
                    "该确认属于材料可用性，必须通过材料工作流处理"
                )
            answer = _validated_answer(request, command.answer)
            source_description = _bounded_text(
                command.source_description, "source_description", required=True
            )
            confirmation_basis = _bounded_text(
                command.confirmation_basis, "confirmation_basis", required=True
            )
            note = _bounded_text(command.note, "note", required=False)
            change_reason = _bounded_text(
                command.change_reason, "change_reason", required=False
            )
            decisions = current.decisions
            previous = decisions.get(confirmation_id)
            if previous is not None and not change_reason:
                raise HumanConfirmationValidationError("修改已确认业务选择时必须填写变更说明")
            now = _utc_now()
            decision = {
                "confirmation_id": confirmation_id,
                "status": "confirmed",
                "response_kind": request["response_kind"],
                "answer": answer,
                "writing_effect": request["writing_effect"],
                "source_description": source_description,
                "confirmation_basis": confirmation_basis,
                "note": note,
                "confirmed_at": str((previous or {}).get("confirmed_at") or now),
                "updated_at": now,
            }
            decisions[confirmation_id] = decision
            history = copy.deepcopy(current.state.get("change_history") or [])
            history.append(
                {
                    "confirmation_id": confirmation_id,
                    "previous_status": str((previous or {}).get("status") or "pending"),
                    "previous_answer": copy.deepcopy((previous or {}).get("answer")),
                    "new_status": "confirmed",
                    "new_answer": copy.deepcopy(answer),
                    "changed_at": now,
                    "change_reason": change_reason,
                    "source_description": source_description,
                    "confirmation_basis": confirmation_basis,
                    "actor": None,
                }
            )
            revision = int(current.state.get("revision") or 0) + 1
            state = {
                "schema_version": SCHEMA_VERSION,
                "project_id": project_id,
                "lineage": copy.deepcopy(current.state["lineage"]),
                "revision": revision,
                "created_at": str(current.state.get("created_at") or now)
                if revision > 1
                else now,
                "updated_at": now,
                "confirmation_requests": current.requests,
                "decisions": decisions,
                "writing_context": _build_writing_context(current.requests, decisions),
                "change_history": history,
            }
            state["content_sha256"] = _state_content_sha(state)
            _validate_schema(state)
            current.state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                current.state_path,
                state,
                required_fields=(
                    "schema_version",
                    "project_id",
                    "lineage",
                    "confirmation_requests",
                    "decisions",
                    "writing_context",
                    "change_history",
                ),
            )
            return self.snapshot(project_id)

    def _initial_state(
        self,
        project_id: str,
        lineage: dict[str, Any],
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state = {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "lineage": lineage,
            "revision": 0,
            "created_at": "not_persisted",
            "updated_at": "not_persisted",
            "confirmation_requests": requests,
            "decisions": {},
            "writing_context": _build_writing_context(requests, {}),
            "change_history": [],
        }
        state["content_sha256"] = _state_content_sha(state)
        return state

    def _lineage(
        self,
        source: HumanConfirmationSource,
        context_path: Path | None,
        fact_request_path: Path,
        tasks_path: Path,
    ) -> dict[str, Any]:
        return {
            "source_mode": source.source_mode,
            "analysis_run_id": source.analysis_run_id,
            "technical_response_context_sha256": _sha256_file(context_path)
            if context_path
            else None,
            "fact_request_sha256": _sha256_file(fact_request_path),
            "writing_tasks_sha256": _sha256_file(tasks_path),
        }

    def _state_path(self, project_id: str, lineage: dict[str, Any]) -> Path:
        project_key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
        lineage_key = stable_json_sha256(lineage)[:24]
        target = (
            self.state_root / project_key / lineage_key / "confirmations.json"
        ).resolve()
        target.relative_to(self.state_root)
        return target

    def _safe_file(self, value: Path) -> Path:
        path = Path(value).expanduser().resolve()
        try:
            path.relative_to(self.outputs_root)
        except ValueError as exc:
            raise HumanConfirmationValidationError(
                "human confirmation source is outside the configured output root"
            ) from exc
        if not path.is_file():
            raise HumanConfirmationValidationError(
                "human confirmation source artifact is unavailable"
            )
        return path

    def _safe_optional_file(self, value: Path | None) -> Path | None:
        if value is None:
            return None
        return self._safe_file(value)

    @staticmethod
    def _token(project_id: str, lineage: dict[str, Any], state: dict[str, Any]) -> str:
        return stable_json_sha256(
            {
                "project_id": project_id,
                "lineage": lineage,
                "revision": int(state.get("revision") or 0),
                "content_sha256": str(
                    state.get("content_sha256") or _state_content_sha(state)
                ),
            }
        )

    @staticmethod
    def _validate_state(
        state: dict[str, Any],
        project_id: str,
        lineage: dict[str, Any],
        requests: list[dict[str, Any]],
    ) -> None:
        _validate_schema(state)
        if state.get("schema_version") != SCHEMA_VERSION or state.get("project_id") != project_id:
            raise HumanConfirmationValidationError(
                "human confirmation artifact identity is invalid"
            )
        if state.get("lineage") != lineage:
            raise HumanConfirmationLineageConflict(
                "human confirmations belong to another analysis lineage"
            )
        if state.get("confirmation_requests") != requests:
            raise HumanConfirmationLineageConflict(
                "human confirmation requests changed with the analysis lineage"
            )
        if state.get("content_sha256") != _state_content_sha(state):
            raise HumanConfirmationValidationError(
                "human confirmation artifact content hash is invalid"
            )


def _extract_requests(
    context: dict[str, Any],
    fact_request: dict[str, Any],
    tasks: dict[str, Any],
) -> list[dict[str, Any]]:
    fact_slots = [item for item in fact_request.get("slots") or [] if isinstance(item, dict)]
    task_values = [item for item in tasks.get("tasks") or [] if isinstance(item, dict)]
    candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for item in context.get("global_manual_confirm_items") or []:
        if isinstance(item, dict):
            candidates.append((item, None))
    for chapter in context.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        for item in chapter.get("manual_confirm_items") or []:
            if isinstance(item, dict):
                candidates.append((item, chapter))

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw, chapter in candidates:
        confirmation_id = str(raw.get("confirm_id") or "").strip()
        if not confirmation_id or confirmation_id in seen:
            continue
        seen.add(confirmation_id)
        request_kind, managed_by, linked_fact_id = _classify_request(raw, fact_slots)
        requirement_refs = _string_refs(
            raw,
            "requirement_refs",
            "related_requirement_ref_ids",
            "related_requirement_ids",
        )
        score_refs = _string_refs(
            raw, "score_refs", "related_score_item_ids", "score_item_refs"
        )
        task_refs = _string_refs(raw, "target_task_ids", "writing_task_refs")
        if chapter:
            requirement_refs.extend(_chapter_requirement_refs(chapter))
            score_refs.extend(_chapter_score_refs(chapter))
        if not task_refs and (requirement_refs or score_refs):
            task_refs = [
                str(task.get("task_id") or "")
                for task in task_values
                if _task_matches(task, requirement_refs, score_refs)
            ]
        task_index = {
            str(task.get("task_id") or ""): task
            for task in task_values
            if task.get("task_id")
        }
        section_refs = _dedupe(
            [
                str(task_index.get(task_id, {}).get("section_id") or "")
                for task_id in task_refs
                if task_id in task_index
            ]
        )
        response_kind = _response_kind(raw)
        candidates_values = _dedupe(
            [str(value).strip() for value in raw.get("candidate_values") or []]
        )
        result.append(
            {
                "confirmation_id": confirmation_id,
                "request_kind": request_kind,
                "managed_by": managed_by,
                "question": str(raw.get("question") or "需要人工确认").strip(),
                "reason": str(raw.get("reason") or raw.get("question") or "").strip(),
                "category": str(raw.get("category") or "business_choice"),
                "priority": str(raw.get("priority") or "medium"),
                "response_kind": response_kind,
                "candidate_values": candidates_values,
                "current_value": copy.deepcopy(raw.get("current_value")),
                "related_field": str(raw.get("related_field") or ""),
                "linked_fact_id": linked_fact_id,
                "writing_effect": str(raw.get("writing_effect") or "writing_constraint")
                if request_kind == "business_choice"
                else "none",
                "requirement_refs": _dedupe(requirement_refs),
                "score_refs": _dedupe(score_refs),
                "writing_task_refs": _dedupe(task_refs),
                "writing_section_refs": section_refs,
                "source_refs": copy.deepcopy(raw.get("source_refs") or []),
            }
        )
    return result


def _classify_request(
    raw: dict[str, Any], fact_slots: list[dict[str, Any]]
) -> tuple[str, str, str | None]:
    explicit_fact = str(
        raw.get("fact_slot_id") or raw.get("fact_id") or raw.get("related_fact_id") or ""
    )
    related_field = str(raw.get("related_field") or "")
    slot = next(
        (
            item
            for item in fact_slots
            if explicit_fact
            and str(item.get("fact_slot_id") or item.get("fact_id") or "") == explicit_fact
        ),
        None,
    )
    if slot is None and related_field:
        slot = next(
            (
                item
                for item in fact_slots
                if str(item.get("fact_key") or "") == related_field
            ),
            None,
        )
    if slot is not None:
        return (
            "project_fact",
            "project_fact_service",
            str(slot.get("fact_slot_id") or slot.get("fact_id") or "") or None,
        )
    category = str(raw.get("category") or "").lower()
    # Older Stage4 artifacts use the broad ``chapter_review`` category for
    # several generated review templates.  Material-related templates are
    # still material workflow requests: treating them as a free-form business
    # choice would create a second, non-canonical material confirmation path.
    review_text = " ".join(
        str(raw.get(key) or "") for key in ("question", "reason")
    )
    legacy_material_review = category == "chapter_review" and any(
        marker in review_text
        for marker in (
            "候选材料",
            "材料缺口",
            "正式材料",
            "证明材料",
            "evidence material",
            "material gap",
        )
    )
    if (
        raw.get("material_id")
        or raw.get("evidence_id")
        or category in {"material", "material_availability", "evidence_availability"}
        or legacy_material_review
    ):
        return "material_availability", "material_workflow", None
    return "business_choice", "human_confirmation_service", None


def _response_kind(raw: dict[str, Any]) -> str:
    explicit = str(raw.get("response_kind") or raw.get("answer_type") or "").lower()
    if explicit in {"boolean", "selection", "text"}:
        return explicit
    return "selection" if raw.get("candidate_values") else "text"


def _validated_answer(request: dict[str, Any], answer: bool | str) -> bool | str:
    response_kind = request.get("response_kind")
    if response_kind == "boolean":
        if not isinstance(answer, bool):
            raise HumanConfirmationValidationError("该业务确认需要是/否答案")
        return answer
    if isinstance(answer, bool):
        raise HumanConfirmationValidationError("该业务确认需要文本答案")
    text = _bounded_text(answer, "answer", required=True)
    if response_kind == "selection" and text not in request.get("candidate_values", []):
        raise HumanConfirmationValidationError("答案不在允许的业务选项中")
    return str(text)


def _build_writing_context(
    requests: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for request in requests:
        if request.get("request_kind") != "business_choice":
            continue
        confirmation_id = str(request["confirmation_id"])
        decision = decisions.get(confirmation_id)
        confirmed = isinstance(decision, dict) and decision.get("status") == "confirmed"
        items.append(
            {
                "context_id": confirmation_id,
                "context_kind": "human_confirmation",
                "title": request["question"],
                "status": "confirmed" if confirmed else "pending",
                "value": copy.deepcopy(decision.get("answer")) if confirmed else None,
                "writing_effect": request["writing_effect"],
                "writing_task_refs": list(request["writing_task_refs"]),
                "writing_section_refs": list(request["writing_section_refs"]),
                "requirement_refs": list(request["requirement_refs"]),
                "score_refs": list(request["score_refs"]),
                "stage9_policy": "apply_constraint" if confirmed else "placeholder_required",
                "stage9_consumable": True,
            }
        )
    return {
        "schema_version": WRITING_CONTEXT_SCHEMA_VERSION,
        "items": items,
        "summary": {
            "confirmed_count": sum(item["status"] == "confirmed" for item in items),
            "pending_count": sum(item["status"] == "pending" for item in items),
            "stage9_consumable_count": len(items),
        },
    }


def _chapter_requirement_refs(chapter: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "tender_requirement_refs",
        "technical_requirement_refs",
        "format_requirement_refs",
        "requirement_refs",
    ):
        for item in chapter.get(key) or []:
            if isinstance(item, dict):
                value = item.get("ref_id") or item.get("requirement_id")
                if value:
                    values.append(str(value))
            elif item:
                values.append(str(item))
    return _dedupe(values)


def _chapter_score_refs(chapter: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            str(item.get("score_item_id") or item.get("score_id") or "")
            for item in chapter.get("score_refs") or []
            if isinstance(item, dict) and (item.get("score_item_id") or item.get("score_id"))
        ]
    )


def _task_matches(
    task: dict[str, Any], requirement_refs: list[str], score_refs: list[str]
) -> bool:
    task_requirements = {
        str(item.get("response_point_id") or "")
        for item in task.get("response_point_mappings") or []
        if isinstance(item, dict)
    }
    task_scores = {str(value) for value in task.get("score_item_refs") or []}
    return bool(task_requirements.intersection(requirement_refs)) or bool(
        task_scores.intersection(score_refs)
    )


def _string_refs(raw: dict[str, Any], *keys: str) -> list[str]:
    result: list[str] = []
    for key in keys:
        for value in raw.get(key) or []:
            if isinstance(value, dict):
                item = (
                    value.get("trace_id")
                    or value.get("requirement_id")
                    or value.get("score_item_id")
                    or value.get("task_id")
                )
                if item:
                    result.append(str(item))
            elif value:
                result.append(str(value))
    return _dedupe(result)


def _state_content_sha(state: dict[str, Any]) -> str:
    return stable_json_sha256(
        {key: copy.deepcopy(value) for key, value in state.items() if key != "content_sha256"}
    )


def _normalize_etag(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("W/"):
        text = text[2:].strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text


def _bounded_text(value: Any, label: str, *, required: bool) -> str | None:
    text = str(value or "").strip()
    if required and not text:
        raise HumanConfirmationValidationError(f"{label} is required")
    if len(text) > MAX_TEXT:
        raise HumanConfirmationValidationError(f"{label} is too long")
    return text or None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HumanConfirmationValidationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise HumanConfirmationValidationError(f"{label} must contain an object")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_schema(state: dict[str, Any]) -> None:
    schema_path = Path(__file__).parent / "schemas" / "prewriting_human_confirmations_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(state), key=lambda item: list(item.path))
    if errors:
        raise HumanConfirmationValidationError(
            "human confirmation state failed schema validation: " + errors[0].message
        )

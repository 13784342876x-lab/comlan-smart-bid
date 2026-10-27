from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.technical_fulltext_composer import stable_json_sha256
from app.technical_placeholders import placeholder_id_for_field, placeholder_token


SCHEMA_VERSION = "technical_writing_resolved_context_v1"


class Stage9InputResolutionError(RuntimeError):
    pass


class Stage9InputLineageError(Stage9InputResolutionError):
    pass


@dataclass(frozen=True)
class Stage9InputSource:
    project_id: str
    analysis_run_id: str | None
    response_outline_path: Path
    writing_outline_path: Path
    writing_tasks_path: Path
    task_information_path: Path
    confirmed_project_facts: dict[str, Any]
    human_confirmation_state: dict[str, Any]


class Stage9InputResolver:
    """Resolve immutable planning plus current facts/choices before any model call."""

    def resolve(self, source: Stage9InputSource) -> dict[str, Any]:
        response_path = _required_file(source.response_outline_path)
        writing_path = _required_file(source.writing_outline_path)
        tasks_path = _required_file(source.writing_tasks_path)
        information_path = _required_file(source.task_information_path)
        task_plan = _load_object(tasks_path)
        task_information = _load_object(information_path)
        facts = copy.deepcopy(source.confirmed_project_facts)
        confirmations = copy.deepcopy(source.human_confirmation_state)

        task_plan_sha = _sha256_file(tasks_path)
        self._validate_lineage(
            source=source,
            response_path=response_path,
            writing_path=writing_path,
            task_plan_sha=task_plan_sha,
            task_information=task_information,
            facts=facts,
            confirmations=confirmations,
        )

        plan_index = {
            str(item.get("task_id") or ""): item
            for item in task_plan.get("tasks") or []
            if isinstance(item, dict) and item.get("task_id")
        }
        task_index = dict(plan_index)
        for item in task_information.get("tasks") or []:
            if not isinstance(item, dict) or not item.get("task_id"):
                continue
            task_id = str(item["task_id"])
            task_index[task_id] = {**copy.deepcopy(plan_index.get(task_id, {})), **item}
        if not task_index:
            raise Stage9InputResolutionError("writing plan contains no Writing Task")

        resolved_by_task = {
            task_id: {
                "task_id": task_id,
                "chapter_id": str(task.get("chapter_id") or ""),
                "section_id": str(task.get("section_id") or ""),
                "confirmed_project_facts": _confirmed_facts_for_task(task, facts),
                "pending_project_facts": _pending_facts_for_task(task, facts),
                "confirmed_business_choices": [],
                "pending_confirmations": [],
                "available_evidence": [],
                "evidence_dependencies": _evidence_for_task(task),
                "writing_constraints": [],
                "placeholder_requirements": [],
                "generation_policy": "allow",
            }
            for task_id, task in task_index.items()
        }

        requests = [
            item
            for item in confirmations.get("confirmation_requests") or []
            if isinstance(item, dict) and item.get("request_kind") == "business_choice"
        ]
        decisions = confirmations.get("decisions") or {}
        for request in requests:
            confirmation_id = str(request.get("confirmation_id") or "")
            if not confirmation_id:
                raise Stage9InputResolutionError("human confirmation request has no identity")
            decision = decisions.get(confirmation_id)
            confirmed = isinstance(decision, dict) and decision.get("status") == "confirmed"
            scoped_task_ids = _scoped_task_ids(
                request,
                task_index,
                allow_unresolved_pending=not confirmed,
            )
            if confirmed:
                constraint = _confirmed_constraint(request, decision)
                for task_id in scoped_task_ids:
                    resolved_by_task[task_id]["confirmed_business_choices"].append(
                        copy.deepcopy(constraint)
                    )
                    resolved_by_task[task_id]["writing_constraints"].append(
                        copy.deepcopy(constraint)
                    )
            else:
                pending, placeholder = _pending_constraint(request)
                for task_id in scoped_task_ids:
                    resolved_by_task[task_id]["pending_confirmations"].append(
                        copy.deepcopy(pending)
                    )
                    resolved_by_task[task_id]["placeholder_requirements"].append(
                        copy.deepcopy(placeholder)
                    )
                    resolved_by_task[task_id]["generation_policy"] = (
                        "allow_with_placeholders"
                    )

        task_contexts = []
        for task_id in sorted(resolved_by_task):
            context = resolved_by_task[task_id]
            context["task_fingerprint"] = stable_json_sha256(context)
            task_contexts.append(context)

        lineage = {
            "project_id": source.project_id,
            "analysis_run_id": source.analysis_run_id,
            "response_outline_sha256": _sha256_file(response_path),
            "writing_outline_sha256": _sha256_file(writing_path),
            "writing_tasks_sha256": task_plan_sha,
            "task_information_sha256": _sha256_file(information_path),
            "project_fact_revision": int(facts.get("revision") or 0),
            "confirmed_project_facts_sha256": str(
                facts.get("content_sha256") or _content_sha(facts)
            ),
            "human_confirmation_revision": int(confirmations.get("revision") or 0),
            "human_confirmation_state_sha256": str(
                confirmations.get("content_sha256") or _content_sha(confirmations)
            ),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "lineage": lineage,
            "pending_policy": "placeholder_required",
            "tasks": task_contexts,
            "summary": {
                "task_count": len(task_contexts),
                "confirmed_business_choice_count": len(
                    {
                        item["confirmation_id"]
                        for task in task_contexts
                        for item in task["confirmed_business_choices"]
                    }
                ),
                "pending_confirmation_count": len(
                    {
                        item["confirmation_id"]
                        for task in task_contexts
                        for item in task["pending_confirmations"]
                    }
                ),
                "placeholder_task_count": sum(
                    bool(task["placeholder_requirements"]) for task in task_contexts
                ),
                "blocked_task_count": 0,
            },
        }
        result["fingerprint"] = stable_json_sha256(result)
        validate_stage9_input_handoff(result)
        return result

    @staticmethod
    def _validate_lineage(
        *,
        source: Stage9InputSource,
        response_path: Path,
        writing_path: Path,
        task_plan_sha: str,
        task_information: dict[str, Any],
        facts: dict[str, Any],
        confirmations: dict[str, Any],
    ) -> None:
        integrity = task_information.get("source_integrity") or {}
        expected = {
            "response": _sha256_file(response_path),
            "writing": _sha256_file(writing_path),
            "tasks": task_plan_sha,
        }
        for key, actual in expected.items():
            recorded = str(((integrity.get(key) or {}).get("sha256_after")) or "")
            if recorded and recorded != actual:
                raise Stage9InputLineageError(
                    f"task information does not belong to the current {key} planning artifact"
                )

        if facts.get("project_id") not in {None, "", source.project_id}:
            raise Stage9InputLineageError("confirmed Project Facts belong to another project")
        if confirmations.get("project_id") != source.project_id:
            raise Stage9InputLineageError("Human Confirmations belong to another project")
        for label, state in (("Project Fact", facts), ("Human Confirmation", confirmations)):
            lineage = state.get("lineage") or {}
            run_id = lineage.get("analysis_run_id")
            if source.analysis_run_id is not None and run_id != source.analysis_run_id:
                raise Stage9InputLineageError(
                    f"{label} state belongs to another Analysis Run"
                )
            recorded_tasks = str(lineage.get("writing_tasks_sha256") or "")
            if recorded_tasks and recorded_tasks != task_plan_sha:
                raise Stage9InputLineageError(
                    f"{label} state belongs to another Writing Plan"
                )
            recorded_content = str(state.get("content_sha256") or "")
            if recorded_content and recorded_content != _content_sha(state):
                raise Stage9InputLineageError(f"{label} state content hash is invalid")


def validate_stage9_input_handoff(
    value: dict[str, Any],
    *,
    response_outline_path: Path | None = None,
    writing_outline_path: Path | None = None,
    task_information_path: Path | None = None,
    confirmed_project_facts_path: Path | None = None,
) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise Stage9InputResolutionError("unsupported resolved Stage9 input schema")
    fingerprint = str(value.get("fingerprint") or "")
    expected = stable_json_sha256(
        {key: copy.deepcopy(item) for key, item in value.items() if key != "fingerprint"}
    )
    if fingerprint != expected:
        raise Stage9InputLineageError("resolved Stage9 input fingerprint is invalid")
    lineage = value.get("lineage") or {}
    path_checks = (
        ("response_outline_sha256", response_outline_path),
        ("writing_outline_sha256", writing_outline_path),
        ("task_information_sha256", task_information_path),
    )
    for key, path in path_checks:
        if path is not None and str(lineage.get(key) or "") != _sha256_file(_required_file(path)):
            raise Stage9InputLineageError(
                "resolved Stage9 input does not match the active planning input"
            )
    if confirmed_project_facts_path is not None:
        fact_state = _load_object(_required_file(confirmed_project_facts_path))
        if str(lineage.get("confirmed_project_facts_sha256") or "") != str(
            fact_state.get("content_sha256") or _content_sha(fact_state)
        ):
            raise Stage9InputLineageError(
                "resolved Stage9 input does not match the active Project Fact state"
            )
    tasks = value.get("tasks")
    if not isinstance(tasks, list):
        raise Stage9InputResolutionError("resolved Stage9 input tasks must be a list")
    for task in tasks:
        if not isinstance(task, dict) or not task.get("task_id"):
            raise Stage9InputResolutionError("resolved Stage9 task context is invalid")
        task_fingerprint = str(task.get("task_fingerprint") or "")
        expected_task = stable_json_sha256(
            {key: copy.deepcopy(item) for key, item in task.items() if key != "task_fingerprint"}
        )
        if task_fingerprint != expected_task:
            raise Stage9InputLineageError("resolved task context fingerprint is invalid")


def scoped_task_contexts(
    handoff: dict[str, Any],
    *,
    chapter_id: str | None,
    section_id: str | None,
) -> list[dict[str, Any]]:
    validate_stage9_input_handoff(handoff)
    values = [
        copy.deepcopy(item)
        for item in handoff.get("tasks") or []
        if (
            section_id is not None
            and str(item.get("section_id") or "") == section_id
        )
        or (
            section_id is None
            and chapter_id is not None
            and str(item.get("chapter_id") or "") == chapter_id
        )
    ]
    if not values:
        raise Stage9InputLineageError(
            "resolved Stage9 input has no context for the requested writing target"
        )
    return values


def _scoped_task_ids(
    request: dict[str, Any],
    task_index: dict[str, dict[str, Any]],
    *,
    allow_unresolved_pending: bool = False,
) -> list[str]:
    explicit_tasks = {str(value) for value in request.get("writing_task_refs") or [] if value}
    unknown = explicit_tasks - set(task_index)
    if unknown:
        raise Stage9InputLineageError(
            "Human Confirmation references an unknown Writing Task: "
            + ", ".join(sorted(unknown))
        )
    sections = {str(value) for value in request.get("writing_section_refs") or [] if value}
    requirements = {str(value) for value in request.get("requirement_refs") or [] if value}
    scores = {str(value) for value in request.get("score_refs") or [] if value}
    matched = set(explicit_tasks)
    for task_id, task in task_index.items():
        if str(task.get("section_id") or "") in sections:
            matched.add(task_id)
        task_requirement_ids = {
            str(value) for value in task.get("response_point_ids") or [] if value
        }
        task_requirement_ids.update(
            str(item.get("response_point_id") or "")
            for item in task.get("response_point_mappings") or []
            if isinstance(item, dict) and item.get("response_point_id")
        )
        if requirements.intersection(task_requirement_ids):
            matched.add(task_id)
        if scores.intersection(str(value) for value in task.get("score_item_refs") or []):
            matched.add(task_id)
    has_scope = bool(explicit_tasks or sections or requirements or scores)
    if has_scope and not matched:
        if allow_unresolved_pending:
            return sorted(task_index)
        raise Stage9InputLineageError(
            "Human Confirmation cannot be resolved to the current Writing Plan"
        )
    return sorted(matched or set(task_index))


def _confirmed_constraint(
    request: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    answer = copy.deepcopy(decision.get("answer"))
    response_kind = str(request.get("response_kind") or decision.get("response_kind") or "text")
    if response_kind == "boolean":
        if not isinstance(answer, bool):
            raise Stage9InputResolutionError("confirmed boolean choice has an invalid answer")
        semantic = "positive_constraint" if answer else "negative_constraint"
    elif response_kind == "selection":
        if not isinstance(answer, str) or not answer:
            raise Stage9InputResolutionError("confirmed selection has an invalid answer")
        semantic = "selected_option"
    else:
        if not isinstance(answer, str) or not answer:
            raise Stage9InputResolutionError("confirmed text choice has an invalid answer")
        semantic = "free_text_business_instruction"
    return {
        "confirmation_id": str(request["confirmation_id"]),
        "status": "confirmed",
        "answer_type": response_kind,
        "answer": answer,
        "constraint_semantic": semantic,
        "question": str(request.get("question") or ""),
        "policy": "apply_constraint",
        "source_lineage": "prewriting_human_confirmation",
    }


def _pending_constraint(
    request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    confirmation_id = str(request["confirmation_id"])
    field = f"human_confirmation_{confirmation_id}"
    placeholder_id = placeholder_id_for_field(field)
    pending = {
        "confirmation_id": confirmation_id,
        "status": "pending",
        "answer_type": str(request.get("response_kind") or "text"),
        "question": str(request.get("question") or ""),
        "policy": "placeholder_required",
        "source_lineage": "prewriting_human_confirmation",
    }
    placeholder = {
        "placeholder_id": placeholder_id,
        "placeholder_token": placeholder_token(placeholder_id),
        "source_kind": "human_confirmation",
        "source_id": confirmation_id,
        "field": field,
        "label": str(request.get("question") or confirmation_id),
        "required_for_task": True,
    }
    return pending, placeholder


def _confirmed_facts_for_task(
    task: dict[str, Any], fact_state: dict[str, Any]
) -> list[dict[str, Any]]:
    facts = fact_state.get("facts") or {}
    result: list[dict[str, Any]] = []
    for slot in task.get("project_fact_slots") or []:
        if not isinstance(slot, dict):
            continue
        fact_key = str(slot.get("fact_key") or "")
        record = facts.get(fact_key)
        if not isinstance(record, dict) or str(
            record.get("status") or record.get("confirmation_status") or ""
        ) != "confirmed":
            continue
        value = record.get("value")
        if value is None or value == "":
            continue
        result.append(
            {
                "fact_id": str(
                    record.get("fact_id")
                    or slot.get("fact_slot_id")
                    or slot.get("fact_id")
                    or ""
                ),
                "fact_key": fact_key,
                "label": str(record.get("label") or slot.get("name") or fact_key),
                "status": "confirmed",
                "value": copy.deepcopy(value),
                "source": "confirmed_project_facts",
            }
        )
    return result


def _pending_facts_for_task(
    task: dict[str, Any], fact_state: dict[str, Any]
) -> list[dict[str, Any]]:
    confirmed_keys = {
        str(item.get("fact_key") or "")
        for item in _confirmed_facts_for_task(task, fact_state)
    }
    result: list[dict[str, Any]] = []
    for slot in task.get("project_fact_slots") or []:
        if not isinstance(slot, dict):
            continue
        fact_key = str(slot.get("fact_key") or "")
        if fact_key in confirmed_keys:
            continue
        result.append(
            {
                "fact_id": str(slot.get("fact_slot_id") or slot.get("fact_id") or ""),
                "fact_key": fact_key,
                "label": str(slot.get("name") or fact_key),
                "status": "placeholder_required",
                "source": "project_fact_request",
            }
        )
    return result


def _evidence_for_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    queries = [
        item for item in task.get("kb_query_plan") or [] if isinstance(item, dict)
    ]
    result: list[dict[str, Any]] = []
    for index, slot in enumerate(task.get("evidence_slots") or [], start=1):
        if not isinstance(slot, dict):
            continue
        evidence_type = str(slot.get("evidence_type") or "")
        query = next(
            (
                item
                for item in queries
                if str(item.get("evidence_type") or "") == evidence_type
            ),
            {},
        )
        execution = str(query.get("execution_status") or "")
        status = (
            "query_not_executed"
            if execution in {"", "planned_not_executed", "query_not_executed", "not_executed"}
            else "candidate"
            if execution in {"found", "candidate", "completed_with_candidates"}
            else execution
        )
        result.append(
            {
                "evidence_id": str(query.get("query_id") or f"EVIDENCE-{index}"),
                "evidence_type": evidence_type,
                "required": bool(slot.get("required", True)),
                "status": status,
                "available_as_verified_evidence": False,
            }
        )
    return result


def _required_file(value: Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise Stage9InputResolutionError(f"required Stage9 input is unavailable: {path.name}")
    return path


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage9InputResolutionError(f"invalid Stage9 input: {path.name}") from exc
    if not isinstance(value, dict):
        raise Stage9InputResolutionError(f"Stage9 input must be an object: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_sha(value: dict[str, Any]) -> str:
    return stable_json_sha256(
        {key: copy.deepcopy(item) for key, item in value.items() if key != "content_sha256"}
    )

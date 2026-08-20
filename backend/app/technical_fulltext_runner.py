from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.technical_fulltext_composer import (
    MANIFEST_SCHEMA_VERSION,
    TechnicalFulltextComposer,
    TechnicalFulltextCompositionError,
    atomic_write_json,
    issue_counts,
    load_json_object,
    sha256_file,
    utc_now,
    validate_chapter_artifact_pair,
)
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
)


INDEX_SCHEMA_VERSION = "technical_fulltext_chapter_production_index_v1"
FULLTEXT_RUN_SCHEMA_VERSION = "technical_fulltext_run_v1"
SAFE_OUTPUT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class TechnicalFulltextRunnerError(RuntimeError):
    pass


def validate_output_key(output_key: str) -> str:
    value = str(output_key or "").strip()
    if not SAFE_OUTPUT_KEY.fullmatch(value) or value in {".", ".."} or ".." in value:
        raise ValueError(
            "output_key must start with an ASCII letter or digit and contain only "
            "ASCII letters, digits, '.', '_' or '-' (without '..')"
        )
    return value


def _safe_error(exc: BaseException, *, phase: str) -> dict[str, str]:
    message = re.sub(r"\s+", " ", str(exc)).strip()[:500]
    return {
        "phase": phase,
        "error_type": type(exc).__name__,
        "message": message,
    }


def _readable_json_path(path: str | Path, *, label: str) -> tuple[Path, dict[str, Any], str]:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"{label} file not found: {target}")
    value = load_json_object(target, label=label)
    return target, value, sha256_file(target)


def _optional_input(path: str | Path | None, *, label: str) -> tuple[Path | None, str | None]:
    if path is None:
        return None, None
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"{label} file not found: {target}")
    try:
        target.read_bytes()
    except OSError as exc:
        raise OSError(f"{label} file is not readable: {target}") from exc
    return target, sha256_file(target)


def _outline_id(node: dict[str, Any]) -> str:
    return str(node.get("chapter_id") or node.get("section_id") or node.get("id") or "").strip()


class TechnicalFulltextRunner:
    """Sequentially invokes the existing chapter Agent, then composes its artifacts."""

    def __init__(
        self,
        *,
        output_dir: str | Path = "outputs",
        agent_factory: Callable[..., Any] = build_technical_writing_agent,
        composer_factory: Callable[..., TechnicalFulltextComposer] = TechnicalFulltextComposer,
        chapter_event_handler_factory: Callable[[str], Callable[[dict[str, Any]], None] | None] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.agent_factory = agent_factory
        self.composer_factory = composer_factory
        self.chapter_event_handler_factory = chapter_event_handler_factory

    def run(
        self,
        *,
        response_outline_path: str | Path,
        writing_outline_path: str | Path,
        task_information_path: str | Path,
        output_key: str,
        draft_units_path: str | Path | None = None,
        confirmed_project_facts_path: str | Path | None = None,
        web_search_policy: str = "disabled",
        stream_model: bool = True,
    ) -> dict[str, Any]:
        started_clock = time.monotonic()
        started_at = utc_now()
        output_key = validate_output_key(output_key)
        output_root = self.output_dir.expanduser().resolve()
        run_root = output_root / output_key
        if run_root.exists():
            raise FileExistsError(
                f"output key already exists and resume is not supported: {run_root}; use a new output key"
            )

        response_path, _, response_sha = _readable_json_path(
            response_outline_path, label="response outline"
        )
        writing_path, outline, writing_sha = _readable_json_path(
            writing_outline_path, label="standard writing outline"
        )
        task_path, _, task_sha = _readable_json_path(
            task_information_path, label="task information"
        )
        draft_path, draft_sha = _optional_input(draft_units_path, label="draft units")
        facts_path, facts_sha = _optional_input(
            confirmed_project_facts_path, label="confirmed project facts"
        )
        canonical_chapters = outline.get("chapters")
        if not isinstance(canonical_chapters, list) or not canonical_chapters:
            raise ValueError("standard writing outline chapters must be a non-empty list")

        ids: list[str] = []
        plan_nodes: list[dict[str, Any]] = []
        for order, chapter in enumerate(canonical_chapters, start=1):
            if not isinstance(chapter, dict):
                raise ValueError(f"standard outline chapter at order {order} must be an object")
            chapter_id = _outline_id(chapter)
            title = str(chapter.get("title") or "").strip()
            if not chapter_id:
                raise ValueError(f"standard outline chapter at order {order} has no chapter ID")
            if chapter_id in ids:
                raise ValueError(f"standard outline contains duplicate chapter ID: {chapter_id}")
            if not title:
                raise ValueError(f"standard outline chapter {chapter_id} has an empty title")
            ids.append(chapter_id)
            plan_nodes.append({"chapter_id": chapter_id, "chapter_title": title, "chapter_order": order})

        fulltext_dir = run_root / "fulltext"
        drafts_dir = run_root / "drafts"
        index_path = fulltext_dir / "chapter_production_index.json"
        events_path = fulltext_dir / "technical_fulltext.events.jsonl"
        fulltext_run_path = fulltext_dir / "technical_fulltext.run.json"
        manifest_path = fulltext_dir / "technical_fulltext_build_manifest.json"
        generated_at = utc_now()
        planning_inputs = {
            "response_outline_path": str(response_path),
            "response_outline_sha256": response_sha,
            "writing_outline_path": str(writing_path),
            "writing_outline_sha256": writing_sha,
            "task_information_path": str(task_path),
            "task_information_sha256": task_sha,
            "draft_units_path": str(draft_path) if draft_path else None,
            "draft_units_sha256": draft_sha,
            "confirmed_project_facts_path": str(facts_path) if facts_path else None,
            "confirmed_project_facts_sha256": facts_sha,
        }
        records = [self._planned_record(node, drafts_dir) for node in plan_nodes]
        for record in records:
            record.update({
                "response_outline_path": str(response_path),
                "writing_outline_path": str(writing_path),
                "task_information_path": str(task_path),
                "draft_units_path": str(draft_path) if draft_path else None,
                "confirmed_project_facts_path": str(facts_path) if facts_path else None,
                "output_key": output_key,
                "web_search_policy": web_search_policy,
            })
        index = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "output_key": output_key,
            "standard_outline": {
                "original_path": str(writing_outline_path),
                "resolved_path": str(writing_path),
                "schema_version": outline.get("schema_version"),
                "sha256": writing_sha,
            },
            "planning_inputs": planning_inputs,
            "chapter_count_expected": len(records),
            "chapter_count_completed": 0,
            "chapter_count_submission_ready": 0,
            "chapter_count_blocked": 0,
            "chapter_count_failed": 0,
            "chapters": records,
            "production_status": "running",
            "generated_at": generated_at,
            "updated_at": generated_at,
        }
        atomic_write_json(
            index_path,
            index,
            required_fields=("schema_version", "output_key", "chapters", "production_status"),
        )
        self._append_event(events_path, "fulltext_run_started", output_key, {
            "chapter_count_expected": len(records),
            "chapter_production_index_path": str(index_path.resolve()),
        })
        self._append_event(events_path, "chapter_plan_created", output_key, {
            "chapter_order": ids,
        })

        failures: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for record, node in zip(records, canonical_chapters):
            chapter_id = record["chapter_id"]
            chapter_started_clock = time.monotonic()
            record["status"] = "running"
            record["started_at"] = utc_now()
            self._save_index(index_path, index)
            self._append_event(events_path, "chapter_run_started", output_key, {
                "chapter_id": chapter_id,
                "order": record["chapter_order"],
                "events_path": record["events_jsonl_path"],
            })
            try:
                agent_input = TechnicalWritingAgentInput(
                    response_outline_path=response_path,
                    writing_outline_path=writing_path,
                    task_information_path=task_path,
                    draft_units_path=draft_path,
                    confirmed_project_facts_path=facts_path,
                    output_key=output_key,
                    target_chapter_id=chapter_id,
                    target_section_id=None,
                    web_search_policy=web_search_policy,
                )
                agent = self.agent_factory(
                    agent_input,
                    output_dir=output_root,
                    disable_web=web_search_policy == "disabled",
                )
                handler = (
                    self.chapter_event_handler_factory(chapter_id)
                    if self.chapter_event_handler_factory
                    else None
                )
                agent.run(
                    event_handler=handler,
                    event_log_path=Path(record["events_jsonl_path"]),
                    stream_model=stream_model,
                )
                self._accept_chapter_artifacts(record, node, output_key)
                record["status"] = "completed"
                event_type = "chapter_run_completed"
                event_data = {
                    "chapter_id": chapter_id,
                    "order": record["chapter_order"],
                    "chapter_status": record["chapter_status"],
                    "submission_ready": record["submission_ready"],
                    "events_path": record["events_jsonl_path"],
                }
            except Exception as exc:
                error = _safe_error(exc, phase="chapter_run_or_artifact_acceptance")
                record["status"] = "failed"
                record["error"] = error
                failures.append({"chapter_id": chapter_id, **error})
                event_type = "chapter_run_failed"
                event_data = {
                    "chapter_id": chapter_id,
                    "order": record["chapter_order"],
                    "error": error,
                    "events_path": record["events_jsonl_path"],
                }
            finally:
                record["completed_at"] = utc_now()
                record["duration_ms"] = max(0, int((time.monotonic() - chapter_started_clock) * 1000))
                self._save_index(index_path, index)
                self._append_event(events_path, event_type, output_key, event_data)

        production_status = self._production_status(records)
        index["production_status"] = production_status
        self._save_index(index_path, index)
        index_sha = sha256_file(index_path)
        self._append_event(events_path, "chapter_production_finished", output_key, {
            "production_status": production_status,
            "chapter_count_completed": index["chapter_count_completed"],
            "chapter_count_submission_ready": index["chapter_count_submission_ready"],
            "chapter_count_blocked": index["chapter_count_blocked"],
            "chapter_count_failed": index["chapter_count_failed"],
        })

        manifest_sha: str | None = None
        composition_result: dict[str, Any] | None = None
        composition_status = "failed"
        composition_issues: list[dict[str, Any]] = []
        if production_status in {"complete", "complete_with_blockers"}:
            manifest = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "document_id": f"technical-fulltext:{output_key}",
                "output_key": output_key,
                "standard_outline_path": str(writing_path),
                "standard_outline_sha256": writing_sha,
                "chapters": [
                    {
                        "chapter_id": record["chapter_id"],
                        "chapter_order": record["chapter_order"],
                        "candidate_json_path": record["candidate_json_path"],
                        "candidate_json_sha256": record["candidate_json_sha256"],
                        "run_json_path": record["run_json_path"],
                        "run_json_sha256": record["run_json_sha256"],
                        "events_jsonl_path": record["events_jsonl_path"],
                        "events_jsonl_sha256": record["events_jsonl_sha256"],
                    }
                    for record in records
                ],
                "generated_from": {
                    "chapter_production_index_path": str(index_path.resolve()),
                    "chapter_production_index_sha256": index_sha,
                },
                "generated_at": utc_now(),
            }
            try:
                manifest_sha = atomic_write_json(
                    manifest_path,
                    manifest,
                    required_fields=("schema_version", "document_id", "chapters", "generated_from"),
                )
                self._append_event(events_path, "composition_started", output_key, {
                    "manifest_path": str(manifest_path.resolve()),
                })
                composer = self.composer_factory(output_dir=output_root)
                composition_result = composer.compose(
                    manifest_path=manifest_path,
                    output_key=output_key,
                )
                returned_status = str(composition_result.get("composition_status") or "")
                composition_status = (
                    returned_status
                    if returned_status in {"complete", "completed_with_blockers"}
                    else "failed"
                )
                self._append_event(events_path, "composition_completed", output_key, {
                    "composition_status": composition_status,
                    "outputs": composition_result.get("outputs") or {},
                })
            except Exception as exc:
                error = _safe_error(exc, phase="composition")
                failures.append(error)
                if isinstance(exc, TechnicalFulltextCompositionError):
                    composition_issues = list(exc.issues)
                else:
                    composition_issues = [{
                        "issue_id": "FCI-RUNTIME-COMPOSITION",
                        "severity": "blocking",
                        "category": "runtime",
                        "code": "composition_runtime_failure",
                        "message": error["message"],
                        "release_blocking": True,
                    }]
                composition_status = "failed"
                self._append_event(events_path, "composition_failed", output_key, {
                    "error": error,
                    "composition_issue_counts": issue_counts(composition_issues),
                })
        else:
            warnings.append({
                "code": "composition_not_started",
                "message": (
                    "Normal composition was not started because chapters did not all "
                    "reach composition qualification."
                ),
            })

        completed_at = utc_now()
        top_status = (
            "completed"
            if composition_status in {"complete", "completed_with_blockers"}
            else "failed"
        )
        if top_status == "completed":
            self._append_event(events_path, "fulltext_run_completed", output_key, {
                "production_status": production_status,
                "composition_status": composition_status,
            })
        else:
            self._append_event(events_path, "fulltext_run_failed", output_key, {
                "production_status": production_status,
                "composition_status": composition_status,
                "failure_count": len(failures),
            })

        fulltext_run = self._build_run_report(
            status=top_status,
            production_status=production_status,
            composition_status=composition_status,
            output_key=output_key,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((time.monotonic() - started_clock) * 1000)),
            planning_inputs=planning_inputs,
            records=records,
            index_path=index_path,
            manifest_path=manifest_path if manifest_sha else None,
            manifest_sha=manifest_sha,
            events_path=events_path,
            composition_result=composition_result,
            composition_issues=composition_issues,
            failures=failures,
            warnings=warnings,
        )
        run_sha = atomic_write_json(
            fulltext_run_path,
            fulltext_run,
            required_fields=("schema_version", "status", "production_status", "composition_status", "chapters", "outputs"),
        )
        return {
            **fulltext_run,
            "run_path": str(fulltext_run_path.resolve()),
            "run_sha256": run_sha,
        }

    def retry_failed_chapter(
        self,
        *,
        output_key: str,
        chapter_id: str,
        stream_model: bool = True,
    ) -> dict[str, Any]:
        """Retry one explicitly failed chapter, then revalidate and recompose the original run."""
        started_clock = time.monotonic()
        started_at = utc_now()
        output_key = validate_output_key(output_key)
        chapter_id = str(chapter_id or "").strip()
        if not chapter_id:
            raise ValueError("retry chapter_id must be non-empty")

        output_root = self.output_dir.expanduser().resolve()
        run_root = (output_root / output_key).resolve()
        if not run_root.is_dir():
            raise FileNotFoundError(f"output key directory does not exist: {run_root}")
        fulltext_dir = run_root / "fulltext"
        drafts_dir = run_root / "drafts"
        index_path = fulltext_dir / "chapter_production_index.json"
        events_path = fulltext_dir / "technical_fulltext.events.jsonl"
        fulltext_run_path = fulltext_dir / "technical_fulltext.run.json"
        manifest_path = fulltext_dir / "technical_fulltext_build_manifest.json"
        original_index = load_json_object(index_path, label="chapter production index")
        original_index_sha = sha256_file(index_path)
        if original_index.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise TechnicalFulltextRunnerError("chapter production index schema is invalid")
        if str(original_index.get("output_key") or "") != output_key:
            raise TechnicalFulltextRunnerError("chapter production index output_key mismatch")
        if original_index.get("production_status") != "incomplete":
            raise TechnicalFulltextRunnerError(
                "retry requires an existing production_status=incomplete run"
            )

        planning_inputs = original_index.get("planning_inputs")
        if not isinstance(planning_inputs, dict):
            raise TechnicalFulltextRunnerError("chapter production index planning_inputs is invalid")
        verified_inputs = self._verify_retry_planning_inputs(planning_inputs)
        response_path = verified_inputs["response_outline_path"]
        writing_path = verified_inputs["writing_outline_path"]
        task_path = verified_inputs["task_information_path"]
        draft_path = verified_inputs["draft_units_path"]
        facts_path = verified_inputs["confirmed_project_facts_path"]
        outline = load_json_object(writing_path, label="standard writing outline")
        writing_sha = sha256_file(writing_path)
        standard_outline = original_index.get("standard_outline")
        if not isinstance(standard_outline, dict):
            raise TechnicalFulltextRunnerError("chapter production index standard_outline is invalid")
        if (
            str(standard_outline.get("sha256") or "") != writing_sha
            or Path(str(standard_outline.get("resolved_path") or "")).expanduser().resolve()
            != writing_path
        ):
            raise TechnicalFulltextRunnerError(
                "standard writing outline differs from the original production; use a new output_key"
            )

        canonical_chapters = outline.get("chapters")
        if not isinstance(canonical_chapters, list) or not canonical_chapters:
            raise TechnicalFulltextRunnerError("standard writing outline chapters must be non-empty")
        plan_nodes: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for order, node in enumerate(canonical_chapters, start=1):
            if not isinstance(node, dict):
                raise TechnicalFulltextRunnerError(f"standard chapter at order {order} is invalid")
            current_id = _outline_id(node)
            title = str(node.get("title") or "").strip()
            if not current_id or current_id in seen_ids or not title:
                raise TechnicalFulltextRunnerError("standard writing outline chapter identity is invalid")
            seen_ids.add(current_id)
            plan_nodes.append({
                "chapter_id": current_id,
                "chapter_title": title,
                "chapter_order": order,
            })
        if chapter_id not in seen_ids:
            raise TechnicalFulltextRunnerError(f"retry chapter is not in the standard outline: {chapter_id}")

        original_records = original_index.get("chapters")
        if not isinstance(original_records, list) or any(not isinstance(item, dict) for item in original_records):
            raise TechnicalFulltextRunnerError("chapter production index chapters are invalid")
        original_ids = [str(item.get("chapter_id") or "").strip() for item in original_records]
        if len(original_ids) != len(set(original_ids)) or set(original_ids) != seen_ids:
            raise TechnicalFulltextRunnerError(
                "chapter production index does not match the current standard outline"
            )
        try:
            recorded_expected_count = int(original_index.get("chapter_count_expected"))
        except (TypeError, ValueError):
            recorded_expected_count = -1
        if recorded_expected_count != len(plan_nodes):
            raise TechnicalFulltextRunnerError(
                "chapter production index count does not match the standard outline"
            )
        original_by_id = {str(item.get("chapter_id") or "").strip(): item for item in original_records}
        recorded_policies: set[str] = set()
        expected_input_paths = {
            "response_outline_path": response_path,
            "writing_outline_path": writing_path,
            "task_information_path": task_path,
            "draft_units_path": draft_path,
            "confirmed_project_facts_path": facts_path,
        }
        for node in plan_nodes:
            original = original_by_id[node["chapter_id"]]
            canonical_artifacts = self._planned_record(node, drafts_dir)
            try:
                original_order = int(original.get("chapter_order"))
            except (TypeError, ValueError):
                original_order = -1
            if (
                str(original.get("chapter_title") or "") != node["chapter_title"]
                or original_order != node["chapter_order"]
                or str(original.get("output_key") or "") != output_key
            ):
                raise TechnicalFulltextRunnerError(
                    f"chapter production provenance mismatch: {node['chapter_id']}"
                )
            for field, expected_path in expected_input_paths.items():
                recorded_value = original.get(field)
                if expected_path is None:
                    matches = recorded_value in (None, "")
                else:
                    matches = (
                        bool(recorded_value)
                        and Path(str(recorded_value)).expanduser().resolve() == expected_path
                    )
                if not matches:
                    raise TechnicalFulltextRunnerError(
                        f"chapter planning input path mismatch: {node['chapter_id']} {field}"
                    )
            for field in (
                "candidate_json_path",
                "candidate_markdown_path",
                "run_json_path",
                "events_jsonl_path",
            ):
                recorded_artifact_path = original.get(field)
                if (
                    not recorded_artifact_path
                    or Path(str(recorded_artifact_path)).expanduser().resolve()
                    != Path(canonical_artifacts[field]).resolve()
                ):
                    raise TechnicalFulltextRunnerError(
                        f"chapter artifact path is outside its production slot: {node['chapter_id']} {field}"
                    )
            recorded_policies.add(str(original.get("web_search_policy") or "disabled"))
        if len(recorded_policies) != 1:
            raise TechnicalFulltextRunnerError(
                "chapter production contains inconsistent web_search_policy values"
            )

        completed_original = [item for item in original_records if item.get("status") == "completed"]
        failed_original = [item for item in original_records if item.get("status") == "failed"]
        ready_original = [
            item for item in completed_original
            if bool(item.get("submission_ready"))
            and int((item.get("issue_counts") or {}).get("fatal") or 0) == 0
            and int((item.get("issue_counts") or {}).get("blocking") or 0) == 0
            and str(item.get("validation_status") or "").lower() != "blocked"
        ]
        derived_original_counts = {
            "chapter_count_completed": len(completed_original),
            "chapter_count_submission_ready": len(ready_original),
            "chapter_count_blocked": len(completed_original) - len(ready_original),
            "chapter_count_failed": len(failed_original),
        }
        for field, expected_value in derived_original_counts.items():
            try:
                actual_value = int(original_index.get(field))
            except (TypeError, ValueError):
                actual_value = -1
            if actual_value != expected_value:
                raise TechnicalFulltextRunnerError(
                    f"chapter production index counter mismatch: {field}"
                )
        target_original = original_by_id[chapter_id]
        if target_original.get("status") != "failed":
            raise TechnicalFulltextRunnerError(
                f"retry is only allowed for a failed chapter: {chapter_id} status={target_original.get('status')}"
            )
        for node, outline_chapter in zip(plan_nodes, canonical_chapters):
            current_id = node["chapter_id"]
            if current_id == chapter_id:
                continue
            original = original_by_id[current_id]
            if original.get("status") != "completed" or not bool(original.get("candidate_reviewable")):
                raise TechnicalFulltextRunnerError(
                    f"non-target chapter is not reusable: {current_id}"
                )
            probe = self._planned_record(node, drafts_dir)
            self._copy_planning_fields(
                probe, response_path, writing_path, task_path, draft_path, facts_path,
                output_key, str(original.get("web_search_policy") or "disabled"),
            )
            self._assert_preserved_chapter_artifacts(probe, original)
            self._accept_chapter_artifacts(
                probe, outline_chapter, output_key, validate_structure=True
            )

        web_search_policy = str(target_original.get("web_search_policy") or "disabled")
        self._append_event(events_path, "chapter_retry_started", output_key, {
            "chapter_id": chapter_id,
            "previous_chapter_production_index_sha256": original_index_sha,
        })
        for name in (
            "technical_fulltext_build_manifest.json",
            "technical_fulltext.json",
            "technical_fulltext.preview.md",
            "technical_fulltext.commit.json",
        ):
            (fulltext_dir / name).unlink(missing_ok=True)

        target_plan = next(item for item in plan_nodes if item["chapter_id"] == chapter_id)
        target_record = self._planned_record(target_plan, drafts_dir)
        self._copy_planning_fields(
            target_record, response_path, writing_path, task_path, draft_path, facts_path,
            output_key, web_search_policy,
        )
        for artifact_path in (
            target_record["candidate_json_path"],
            target_record["candidate_markdown_path"],
            target_record["run_json_path"],
            target_record["events_jsonl_path"],
        ):
            Path(artifact_path).unlink(missing_ok=True)

        target_agent_error: dict[str, str] | None = None
        retry_started_clock = time.monotonic()
        try:
            agent_input = TechnicalWritingAgentInput(
                response_outline_path=response_path,
                writing_outline_path=writing_path,
                task_information_path=task_path,
                draft_units_path=draft_path,
                confirmed_project_facts_path=facts_path,
                output_key=output_key,
                target_chapter_id=chapter_id,
                target_section_id=None,
                web_search_policy=web_search_policy,
            )
            agent = self.agent_factory(
                agent_input,
                output_dir=output_root,
                disable_web=web_search_policy == "disabled",
            )
            handler = (
                self.chapter_event_handler_factory(chapter_id)
                if self.chapter_event_handler_factory
                else None
            )
            agent.run(
                event_handler=handler,
                event_log_path=Path(target_record["events_jsonl_path"]),
                stream_model=stream_model,
            )
            self._append_event(events_path, "chapter_retry_agent_completed", output_key, {
                "chapter_id": chapter_id,
            })
        except Exception as exc:
            target_agent_error = _safe_error(exc, phase="chapter_retry")
            self._append_event(events_path, "chapter_retry_agent_failed", output_key, {
                "chapter_id": chapter_id,
                "error": target_agent_error,
            })

        failures: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        self._append_event(events_path, "chapter_revalidation_started", output_key, {
            "chapter_count_expected": len(plan_nodes),
        })
        for node, outline_chapter in zip(plan_nodes, canonical_chapters):
            current_id = node["chapter_id"]
            record = self._planned_record(node, drafts_dir)
            original = original_by_id[current_id]
            current_web_policy = str(original.get("web_search_policy") or "disabled")
            self._copy_planning_fields(
                record, response_path, writing_path, task_path, draft_path, facts_path,
                output_key, current_web_policy,
            )
            record["started_at"] = utc_now()
            try:
                if current_id != chapter_id:
                    self._assert_preserved_chapter_artifacts(record, original)
                elif target_agent_error is not None:
                    raise RuntimeError(target_agent_error["message"])
                self._accept_chapter_artifacts(
                    record, outline_chapter, output_key, validate_structure=True
                )
                record["status"] = "completed"
            except Exception as exc:
                error = _safe_error(exc, phase="chapter_revalidation")
                record["status"] = "failed"
                record["error"] = error
                failures.append({"chapter_id": current_id, **error})
            record["completed_at"] = utc_now()
            record["duration_ms"] = (
                max(0, int((time.monotonic() - retry_started_clock) * 1000))
                if current_id == chapter_id
                else 0
            )
            records.append(record)

        rebuilt_at = utc_now()
        rebuilt_index = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "output_key": output_key,
            "standard_outline": deepcopy(standard_outline),
            "planning_inputs": deepcopy(planning_inputs),
            "chapter_count_expected": len(records),
            "chapter_count_completed": 0,
            "chapter_count_submission_ready": 0,
            "chapter_count_blocked": 0,
            "chapter_count_failed": 0,
            "chapters": records,
            "production_status": self._production_status(records),
            "generated_at": rebuilt_at,
            "updated_at": rebuilt_at,
            "recovery": {
                "mode": "retry_failed_chapter",
                "retry_chapter_id": chapter_id,
                "previous_chapter_production_index_sha256": original_index_sha,
                "revalidated_chapter_count": len(records),
            },
        }
        self._save_index(index_path, rebuilt_index)
        production_status = rebuilt_index["production_status"]
        rebuilt_index_sha = sha256_file(index_path)
        self._append_event(events_path, "chapter_revalidation_finished", output_key, {
            "production_status": production_status,
            "chapter_count_completed": rebuilt_index["chapter_count_completed"],
            "chapter_count_failed": rebuilt_index["chapter_count_failed"],
            "chapter_production_index_sha256": rebuilt_index_sha,
        })

        manifest_sha: str | None = None
        composition_result: dict[str, Any] | None = None
        composition_status = "failed"
        composition_issues: list[dict[str, Any]] = []
        if production_status in {"complete", "complete_with_blockers"}:
            manifest = self._build_manifest(
                output_key=output_key,
                writing_path=writing_path,
                writing_sha=writing_sha,
                records=records,
                index_path=index_path,
                index_sha=rebuilt_index_sha,
            )
            try:
                manifest_sha = atomic_write_json(
                    manifest_path,
                    manifest,
                    required_fields=("schema_version", "document_id", "chapters", "generated_from"),
                )
                self._append_event(events_path, "composition_started", output_key, {
                    "manifest_path": str(manifest_path.resolve()),
                    "recovery_chapter_id": chapter_id,
                })
                composition_result = self.composer_factory(output_dir=output_root).compose(
                    manifest_path=manifest_path,
                    output_key=output_key,
                )
                returned_status = str(composition_result.get("composition_status") or "")
                composition_status = (
                    returned_status
                    if returned_status in {"complete", "completed_with_blockers"}
                    else "failed"
                )
                self._append_event(events_path, "composition_completed", output_key, {
                    "composition_status": composition_status,
                    "recovery_chapter_id": chapter_id,
                    "outputs": composition_result.get("outputs") or {},
                })
            except Exception as exc:
                error = _safe_error(exc, phase="composition")
                failures.append(error)
                if isinstance(exc, TechnicalFulltextCompositionError):
                    composition_issues = list(exc.issues)
                else:
                    composition_issues = [{
                        "issue_id": "FCI-RUNTIME-RECOVERY-COMPOSITION",
                        "severity": "blocking",
                        "category": "runtime",
                        "code": "composition_runtime_failure",
                        "message": error["message"],
                        "release_blocking": True,
                    }]
                self._append_event(events_path, "composition_failed", output_key, {
                    "error": error,
                    "composition_issue_counts": issue_counts(composition_issues),
                })
        else:
            warnings.append({
                "code": "composition_not_started",
                "message": "Recovered chapter set is still incomplete; composition was not started.",
            })

        completed_at = utc_now()
        top_status = (
            "completed"
            if composition_status in {"complete", "completed_with_blockers"}
            else "failed"
        )
        self._append_event(
            events_path,
            "fulltext_recovery_completed" if top_status == "completed" else "fulltext_recovery_failed",
            output_key,
            {
                "retry_chapter_id": chapter_id,
                "production_status": production_status,
                "composition_status": composition_status,
            },
        )
        fulltext_run = self._build_run_report(
            status=top_status,
            production_status=production_status,
            composition_status=composition_status,
            output_key=output_key,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((time.monotonic() - started_clock) * 1000)),
            planning_inputs=planning_inputs,
            records=records,
            index_path=index_path,
            manifest_path=manifest_path if manifest_sha else None,
            manifest_sha=manifest_sha,
            events_path=events_path,
            composition_result=composition_result,
            composition_issues=composition_issues,
            failures=failures,
            warnings=warnings,
        )
        fulltext_run["run_mode"] = "retry_failed_chapter"
        fulltext_run["retry_chapter_id"] = chapter_id
        fulltext_run["previous_chapter_production_index_sha256"] = original_index_sha
        run_sha = atomic_write_json(
            fulltext_run_path,
            fulltext_run,
            required_fields=("schema_version", "status", "production_status", "composition_status", "chapters", "outputs"),
        )
        return {
            **fulltext_run,
            "run_path": str(fulltext_run_path.resolve()),
            "run_sha256": run_sha,
        }

    @staticmethod
    def _verify_retry_planning_inputs(planning_inputs: dict[str, Any]) -> dict[str, Path | None]:
        result: dict[str, Path | None] = {}
        required = {"response_outline", "writing_outline", "task_information"}
        for name in (
            "response_outline",
            "writing_outline",
            "task_information",
            "draft_units",
            "confirmed_project_facts",
        ):
            path_value = planning_inputs.get(f"{name}_path")
            recorded_sha = planning_inputs.get(f"{name}_sha256")
            if not path_value:
                if name in required or recorded_sha not in (None, ""):
                    raise TechnicalFulltextRunnerError(
                        f"original planning input reference is missing: {name}"
                    )
                result[f"{name}_path"] = None
                continue
            path = Path(str(path_value)).expanduser().resolve()
            if not path.is_file():
                raise TechnicalFulltextRunnerError(f"original planning input is unavailable: {path}")
            actual_sha = sha256_file(path)
            if actual_sha != str(recorded_sha or ""):
                raise TechnicalFulltextRunnerError(
                    f"original planning input changed: {name}; use a new output_key"
                )
            result[f"{name}_path"] = path
        return result

    @staticmethod
    def _copy_planning_fields(
        record: dict[str, Any],
        response_path: Path,
        writing_path: Path,
        task_path: Path,
        draft_path: Path | None,
        facts_path: Path | None,
        output_key: str,
        web_search_policy: str,
    ) -> None:
        record.update({
            "response_outline_path": str(response_path),
            "writing_outline_path": str(writing_path),
            "task_information_path": str(task_path),
            "draft_units_path": str(draft_path) if draft_path else None,
            "confirmed_project_facts_path": str(facts_path) if facts_path else None,
            "output_key": output_key,
            "web_search_policy": web_search_policy,
        })

    @staticmethod
    def _assert_preserved_chapter_artifacts(
        expected_record: dict[str, Any],
        original_record: dict[str, Any],
    ) -> None:
        pairs = (
            ("candidate_json_path", "candidate_json_sha256"),
            ("candidate_markdown_path", "candidate_markdown_sha256"),
            ("run_json_path", "run_json_sha256"),
            ("events_jsonl_path", "events_jsonl_sha256"),
        )
        for path_field, hash_field in pairs:
            expected_path = Path(expected_record[path_field]).resolve()
            recorded_path = Path(str(original_record.get(path_field) or "")).expanduser().resolve()
            recorded_sha = str(original_record.get(hash_field) or "")
            if recorded_path != expected_path or not recorded_sha:
                raise TechnicalFulltextRunnerError(
                    f"reused chapter artifact reference mismatch: {expected_record['chapter_id']} {path_field}"
                )
            if not expected_path.is_file() or sha256_file(expected_path) != recorded_sha:
                raise TechnicalFulltextRunnerError(
                    f"reused chapter artifact changed: {expected_record['chapter_id']} {path_field}"
                )

    @staticmethod
    def _build_manifest(
        *,
        output_key: str,
        writing_path: Path,
        writing_sha: str,
        records: list[dict[str, Any]],
        index_path: Path,
        index_sha: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "document_id": f"technical-fulltext:{output_key}",
            "output_key": output_key,
            "standard_outline_path": str(writing_path),
            "standard_outline_sha256": writing_sha,
            "chapters": [
                {
                    "chapter_id": record["chapter_id"],
                    "chapter_order": record["chapter_order"],
                    "candidate_json_path": record["candidate_json_path"],
                    "candidate_json_sha256": record["candidate_json_sha256"],
                    "run_json_path": record["run_json_path"],
                    "run_json_sha256": record["run_json_sha256"],
                    "events_jsonl_path": record["events_jsonl_path"],
                    "events_jsonl_sha256": record["events_jsonl_sha256"],
                }
                for record in records
            ],
            "generated_from": {
                "chapter_production_index_path": str(index_path.resolve()),
                "chapter_production_index_sha256": index_sha,
            },
            "generated_at": utc_now(),
        }

    @staticmethod
    def _planned_record(node: dict[str, Any], drafts_dir: Path) -> dict[str, Any]:
        chapter_id = node["chapter_id"]
        return {
            **node,
            "status": "planned",
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "candidate_json_path": str((drafts_dir / f"{chapter_id}.candidate.json").resolve()),
            "candidate_markdown_path": str((drafts_dir / f"{chapter_id}.candidate.md").resolve()),
            "formal_markdown_path": str((drafts_dir / f"{chapter_id}.md").resolve()),
            "run_json_path": str((drafts_dir / f"{chapter_id}.run.json").resolve()),
            "events_jsonl_path": str((drafts_dir / f"{chapter_id}.events.jsonl").resolve()),
            "candidate_json_sha256": None,
            "candidate_markdown_sha256": None,
            "run_json_sha256": None,
            "events_jsonl_sha256": None,
            "event_count": 0,
            "chapter_status": None,
            "validation_status": None,
            "issue_counts": {"fatal": 0, "blocking": 0, "warning": 0},
            "candidate_generated": False,
            "candidate_saved": False,
            "candidate_reviewable": False,
            "submission_ready": False,
            "selected_candidate": None,
            "candidate_selection_reason": None,
            "revision_round": None,
            "error": None,
        }

    @staticmethod
    def _save_index(path: Path, index: dict[str, Any]) -> None:
        records = list(index.get("chapters") or [])
        completed = [item for item in records if item.get("status") == "completed"]
        submission_ready = [
            item for item in completed
            if bool(item.get("submission_ready"))
            and int((item.get("issue_counts") or {}).get("fatal") or 0) == 0
            and int((item.get("issue_counts") or {}).get("blocking") or 0) == 0
            and str(item.get("validation_status") or "").lower() != "blocked"
        ]
        index["chapter_count_completed"] = len(completed)
        index["chapter_count_submission_ready"] = len(submission_ready)
        index["chapter_count_blocked"] = len(completed) - len(submission_ready)
        index["chapter_count_failed"] = sum(item.get("status") == "failed" for item in records)
        index["updated_at"] = utc_now()
        atomic_write_json(
            path,
            index,
            required_fields=("schema_version", "output_key", "chapters", "production_status", "updated_at"),
        )

    @staticmethod
    def _append_event(path: Path, event_type: str, output_key: str, data: dict[str, Any]) -> None:
        existing: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise OSError("fulltext event log contains a non-object event")
                    existing.append(value)
        existing.append({
            "event_type": event_type,
            "timestamp": utc_now(),
            "output_key": output_key,
            "data": deepcopy(data),
        })
        content = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in existing)
        from app.technical_fulltext_composer import _atomic_write_text

        _atomic_write_text(path, content)
        reparsed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if reparsed != existing:
            raise OSError("fulltext event log changed during persistence")

    @staticmethod
    def _accept_chapter_artifacts(
        record: dict[str, Any],
        outline_chapter: dict[str, Any],
        output_key: str,
        *,
        validate_structure: bool = False,
    ) -> None:
        artifact, issues = validate_chapter_artifact_pair(
            chapter_id=record["chapter_id"],
            outline_chapter=outline_chapter,
            candidate_path=record["candidate_json_path"],
            run_path=record["run_json_path"],
            events_path=record["events_jsonl_path"],
            expected_output_key=output_key,
            validate_structure=validate_structure,
            require_submission_ready=False,
        )
        if artifact is None:
            codes = ", ".join(item["code"] for item in issues)
            raise OSError(f"chapter artifacts are not reviewable: {codes}")
        run = artifact.run
        if run.get("status") != "completed":
            raise RuntimeError(f"chapter Agent did not complete successfully: status={run.get('status')}")
        run_output_key = (run.get("run_summary") or {}).get("output_key") or run.get("output_key")
        if run_output_key not in (None, "", output_key):
            raise OSError(f"chapter Run output_key mismatch: {run_output_key}")
        candidate_markdown = Path(record["candidate_markdown_path"])
        if not candidate_markdown.is_file() or candidate_markdown.stat().st_size <= 0:
            raise OSError("Candidate Markdown artifact is missing or empty")
        counts = run.get("issue_counts") if isinstance(run.get("issue_counts"), dict) else {}
        record.update({
            "candidate_json_sha256": artifact.candidate_sha256,
            "candidate_markdown_sha256": sha256_file(candidate_markdown),
            "run_json_sha256": artifact.run_sha256,
            "events_jsonl_sha256": artifact.events_sha256,
            "event_count": artifact.event_count,
            "chapter_status": run.get("chapter_status"),
            "validation_status": run.get("validation_status"),
            "issue_counts": {
                "fatal": int(counts.get("fatal") or 0),
                "blocking": int(counts.get("blocking") or 0),
                "warning": int(counts.get("warning") or 0),
            },
            "candidate_generated": bool(run.get("candidate_generated")),
            "candidate_saved": bool(run.get("candidate_saved")),
            "candidate_reviewable": bool(run.get("candidate_reviewable")),
            "submission_ready": bool(run.get("submission_ready")),
            "selected_candidate": run.get("selected_candidate"),
            "candidate_selection_reason": run.get("candidate_selection_reason"),
            "revision_round": run.get("revision_round"),
            "error": None,
        })

    @staticmethod
    def _production_status(records: list[dict[str, Any]]) -> str:
        if any(item["status"] != "completed" or not item["candidate_reviewable"] for item in records):
            return "incomplete"
        if all(
            item["submission_ready"]
            and int(item["issue_counts"].get("blocking") or 0) == 0
            and int(item["issue_counts"].get("fatal") or 0) == 0
            and str(item.get("validation_status") or "").lower() != "blocked"
            for item in records
        ):
            return "complete"
        return "complete_with_blockers"

    @staticmethod
    def _build_run_report(
        *,
        status: str,
        production_status: str,
        composition_status: str,
        output_key: str,
        started_at: str,
        completed_at: str,
        duration_ms: int,
        planning_inputs: dict[str, Any],
        records: list[dict[str, Any]],
        index_path: Path,
        manifest_path: Path | None,
        manifest_sha: str | None,
        events_path: Path,
        composition_result: dict[str, Any] | None,
        composition_issues: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        completed = [item for item in records if item["status"] == "completed"]
        blocked = [
            item for item in completed
            if not item["submission_ready"]
            or int(item["issue_counts"].get("fatal") or 0) > 0
            or int(item["issue_counts"].get("blocking") or 0) > 0
            or str(item.get("validation_status") or "").lower() == "blocked"
        ]
        submission_ready = [item for item in completed if item not in blocked]
        composition_outputs = (composition_result or {}).get("outputs") or {}
        return {
            "schema_version": FULLTEXT_RUN_SCHEMA_VERSION,
            "status": status,
            "production_status": production_status,
            "composition_status": composition_status,
            "output_key": output_key,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "input_paths": {key: value for key, value in planning_inputs.items() if key.endswith("_path")},
            "input_hashes": {key: value for key, value in planning_inputs.items() if key.endswith("_sha256")},
            "chapter_count_expected": len(records),
            "chapter_count_completed": len(completed),
            "chapter_count_submission_ready": len(submission_ready),
            "chapter_count_blocked": len(blocked),
            "chapter_count_failed": sum(item["status"] == "failed" for item in records),
            "chapters": [
                {
                    "chapter_id": item["chapter_id"],
                    "order": item["chapter_order"],
                    "status": item["status"],
                    "chapter_status": item["chapter_status"],
                    "validation_status": item["validation_status"],
                    "issue_counts": deepcopy(item["issue_counts"]),
                    "submission_ready": item["submission_ready"],
                    "candidate_path": item["candidate_json_path"],
                    "candidate_sha256": item["candidate_json_sha256"],
                    "run_path": item["run_json_path"],
                    "run_sha256": item["run_json_sha256"],
                    "events_path": item["events_jsonl_path"],
                    "events_sha256": item["events_jsonl_sha256"],
                    "event_count": item["event_count"],
                    "error": deepcopy(item["error"]),
                }
                for item in records
            ],
            "manifest_path": str(manifest_path.resolve()) if manifest_path else None,
            "manifest_sha256": manifest_sha,
            "outputs": {
                "chapter_production_index": str(index_path.resolve()),
                "technical_fulltext_json": composition_outputs.get("technical_fulltext_json"),
                "technical_fulltext_preview": composition_outputs.get("technical_fulltext_preview"),
                "technical_fulltext_commit": composition_outputs.get("technical_fulltext_commit"),
                "fulltext_events": str(events_path.resolve()),
            },
            "composition_issues": deepcopy(composition_issues),
            "composition_issue_counts": issue_counts(composition_issues),
            "failures": deepcopy(failures),
            "warnings": deepcopy(warnings),
        }

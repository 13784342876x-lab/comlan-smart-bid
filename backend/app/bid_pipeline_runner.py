from __future__ import annotations

import json
import os
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from app.actionable_requirement_index import build_actionable_requirement_index
from app.bid_source_resolver import SOURCE_MANIFEST_SCHEMA_VERSION, resolve_bid_sources
from app.bid_lot_resolver import resolve_project_lot
from app.bid_score_review import BidScoreReviewError, BidScoreReviewService
from app.company_writing_outline import load_company_outline, validate_company_outline
from app.draft_unit_planner import build_draft_unit_files
from app.file_store import resolve_storage_key
from app.kb_service import KnowledgeBaseService
from app.material_matcher import (
    analyze_project_materials,
    load_tender_projects,
    save_tender_projects,
)
from app.metadata_store import MetadataStore
from app.response_outline_builder import (
    build_response_outline,
    build_technical_outline_from_directory,
)
from app.score_maximization_matrix import build_score_material_outputs
from app.source_package_service import (
    ProjectPackageProfileBuilder,
    SourcePackageService,
    SourceReferenceResolver,
    apply_project_profile_to_score_parse,
)
from app.technical_fulltext_audit_finalizer import TechnicalFulltextAuditFinalizer
from app.technical_fulltext_auditor import TechnicalFulltextAuditor
from app.technical_fulltext_hygiene import TechnicalFulltextHygiene
from app.technical_fulltext_review_package import TechnicalFulltextReviewPackageAssembler
from app.technical_fulltext_runner import TechnicalFulltextRunner
from app.technical_fulltext_scoring_assessment import TechnicalFulltextScoringAssessor
from app.technical_fulltext_semantic_audit import TechnicalFulltextSemanticAuditor
from app.technical_section_task_planner import build_section_task_plan_files
from app.technical_task_information_planner import build_task_information_files
from app.technical_writing_outline_adapter import (
    build_technical_writing_outline_files,
    render_outline_markdown,
)
from app.technical_writing_outline_enricher import enrich_writing_outline_files
from app.technical_outline_builder import discover_selected_files
from app.tender_metadata_extractor import extract_tender_metadata, inspect_tender_source_files
from app.tender_requirement_matrix import build_tender_requirement_matrix
from app.tender_source_guard import (
    is_tender_source_file,
)
from config import BACKEND_ROOT, SOURCE_PACKAGE_ARCHIVE_EXTENSIONS


STATE_SCHEMA_VERSION = "bid_pipeline_state_v1"
FINAL_STATUS = "HUMAN_REVIEW_READY"
ANALYSIS_READY_STATUS = "ANALYSIS_READY"
ANALYSIS_STOP_STAGE = "build_scoring_analysis"
WRITING_PLAN_READY_STATUS = "WRITING_PLAN_READY"
WRITING_PLAN_STOP_STAGE = "build_writing_tasks"
SAFE_COMPONENT = re.compile(r"[^0-9A-Za-z._-]+")
DEFAULT_COMPANY_OUTLINE = (
    BACKEND_ROOT / "resources" / "writing" / "company_master_outline_v0.json"
).resolve()
BID_PIPELINE_SOURCE_TAG = "bid_pipeline_source"


class BidPipelineStateError(RuntimeError):
    pass


@dataclass
class StageResult:
    artifacts: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    gate_passed: bool = True
    gate_reason: str | None = None


STAGE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("validate_inputs", "Validate source inputs"),
    ("ingest_sources", "Ingest tender and scoring sources"),
    ("parse_sources", "Parse tender and scoring sources"),
    ("build_tender_project", "Build score analysis and tender project graph"),
    ("extract_tender_metadata", "Extract project metadata"),
    ("match_enterprise_materials", "Match enterprise materials"),
    ("build_project_response_context", "Build project response context"),
    ("build_requirement_analysis", "Build requirement matrix and actionable index"),
    ("build_scoring_analysis", "Build scoring and material analysis"),
    ("build_technical_response_outline", "Build technical response outline v2"),
    ("build_technical_writing_outline", "Build technical writing outline"),
    ("build_writing_tasks", "Build writing tasks and draft units"),
    ("generate_fulltext", "Generate and compose technical fulltext"),
    ("hygiene_fulltext", "Clean technical fulltext"),
    ("audit_fulltext", "Run deterministic fulltext audit"),
    ("semantic_audit", "Run semantic audit"),
    ("scoring_assessment", "Assess scoring coverage"),
    ("finalize_audit", "Finalize Stage11 audit"),
    ("assemble_stage12a", "Assemble Stage12-A human review package"),
)

STAGE_INPUT_BINDINGS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "build_requirement_analysis": (
        ("build_project_response_context", "technical_response_context", "technical_response_context"),
    ),
    "build_scoring_analysis": (
        ("build_project_response_context", "technical_response_context", "technical_response_context"),
    ),
    "build_technical_writing_outline": (
        ("build_technical_response_outline", "technical_response_outline_json", "response_outline"),
    ),
    "build_writing_tasks": (
        ("build_technical_response_outline", "technical_response_outline_json", "response_outline"),
        ("build_technical_writing_outline", "technical_writing_outline_json", "writing_outline"),
        ("build_project_response_context", "technical_response_context", "project_context"),
    ),
    "generate_fulltext": (
        ("build_technical_response_outline", "technical_response_outline_json", "response_outline"),
        ("build_writing_tasks", "technical_writing_outline_json", "writing_outline"),
        ("build_writing_tasks", "task_information_requirements_json", "task_information"),
        ("build_writing_tasks", "draft_units_json", "draft_units"),
    ),
    "hygiene_fulltext": (
        ("generate_fulltext", "technical_fulltext_json", "technical_fulltext"),
    ),
    "audit_fulltext": (
        ("hygiene_fulltext", "technical_fulltext_cleaned_json", "cleaned_fulltext"),
    ),
    "semantic_audit": (
        ("audit_fulltext", "technical_fulltext_audit_json", "audit"),
    ),
    "scoring_assessment": (
        ("semantic_audit", "technical_fulltext_audit_json", "audit"),
    ),
    "finalize_audit": (
        ("scoring_assessment", "technical_fulltext_audit_json", "audit"),
        ("scoring_assessment", "technical_fulltext_scoring_assessment_json", "scoring_assessment"),
    ),
    "assemble_stage12a": (
        ("finalize_audit", "technical_fulltext_audit_json", "final_audit"),
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str, *, fallback: str) -> str:
    cleaned = SAFE_COMPONENT.sub("-", str(value or "").strip()).strip("-._")
    return (cleaned[:120] or fallback).replace("..", "-")


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bid-{stamp}-{uuid.uuid4().hex[:8]}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.02 * (attempt + 1))


class BidPipelineRunner:
    """Orchestrate existing bid services with durable, stage-level state."""

    def __init__(
        self,
        *,
        output_dir: str | Path = "outputs",
        stream_model: bool = True,
        web_search_policy: str = "disabled",
        semantic_model_profile: str | None = None,
        stage_overrides: Mapping[str, Callable[[dict[str, Any]], StageResult | dict[str, Any]]] | None = None,
        fulltext_runner_factory: Callable[..., Any] = TechnicalFulltextRunner,
        stage9_input_loader: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.state_root = self.output_dir / "bid_runs"
        self.stage_output_root = self.output_dir / "bid_pipeline_outputs"
        self.stream_model = bool(stream_model)
        self.web_search_policy = web_search_policy
        self.semantic_model_profile = semantic_model_profile
        self.stage_overrides = dict(stage_overrides or {})
        self.fulltext_runner_factory = fulltext_runner_factory
        self.stage9_input_loader = stage9_input_loader
        self._handlers: dict[str, Callable[[dict[str, Any]], StageResult]] = {
            "validate_inputs": self._stage_validate_inputs,
            "ingest_sources": self._stage_ingest_sources,
            "parse_sources": self._stage_parse_sources,
            "build_tender_project": self._stage_build_tender_project,
            "extract_tender_metadata": self._stage_extract_tender_metadata,
            "match_enterprise_materials": self._stage_match_enterprise_materials,
            "build_project_response_context": self._stage_build_project_response_context,
            "build_requirement_analysis": self._stage_build_requirement_analysis,
            "build_scoring_analysis": self._stage_build_scoring_analysis,
            "build_technical_response_outline": self._stage_build_technical_response_outline,
            "build_technical_writing_outline": self._stage_build_technical_writing_outline,
            "build_writing_tasks": self._stage_build_writing_tasks,
            "generate_fulltext": self._stage_generate_fulltext,
            "hygiene_fulltext": self._stage_hygiene_fulltext,
            "audit_fulltext": self._stage_audit_fulltext,
            "semantic_audit": self._stage_semantic_audit,
            "scoring_assessment": self._stage_scoring_assessment,
            "finalize_audit": self._stage_finalize_audit,
            "assemble_stage12a": self._stage_assemble_stage12a,
        }

    def run(
        self,
        *,
        source_dir: str | Path,
        lot_name: str | None = None,
        run_id: str | None = None,
        stop_after_stage: str | None = None,
    ) -> dict[str, Any]:
        stage_ids = {stage_id for stage_id, _ in STAGE_DEFINITIONS}
        if stop_after_stage is not None and stop_after_stage not in stage_ids:
            raise ValueError(f"unknown bid pipeline stop stage: {stop_after_stage}")
        identity = _safe_component(run_id or _new_run_id(), fallback="bid-run")
        state_path = self._state_path(identity)
        if state_path.exists():
            raise FileExistsError(f"bid pipeline run already exists: {identity}")
        resolved_source_dir = Path(source_dir).expanduser().resolve()
        requested_lot_name = str(lot_name or "").strip() or None
        planning_key = _safe_component(
            requested_lot_name or identity,
            fallback=identity,
        )
        fulltext_output_key = _safe_component(
            f"fulltext-{identity}", fallback=f"fulltext-{uuid.uuid4().hex[:8]}"
        )
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "run_id": identity,
            "pipeline_status": "CREATED",
            "final_status": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "current_stage": None,
            "inputs": {
                "source_dir": str(resolved_source_dir),
                "company_outline": str(DEFAULT_COMPANY_OUTLINE),
                "lot_name": requested_lot_name,
                "lot_resolution_status": "explicit" if requested_lot_name else "pending",
                "lot_candidates": [],
                "lot_resolution_reason": (
                    "explicit --lot-name supplied by user" if requested_lot_name else None
                ),
                "stop_after_stage": stop_after_stage,
            },
            "source_manifest": {
                "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
                "source_dir": str(resolved_source_dir),
                "files": [],
                "logical_sources": [],
                "tender_file_id": None,
                "score_file_id": None,
                "anchor_candidates": {"tender": [], "score": []},
                "selection_failure": None,
                "selection_status": "pending",
                "package_manifests": [],
                "reference_resolution": None,
                "project_profile_path": None,
                "package_completeness": None,
            },
            "routing": {
                "pipeline_state_path": str(state_path),
                "pipeline_run_dir": str(state_path.parent),
                "source_metadata_path": str((state_path.parent / "source_metadata.json").resolve()),
                "stage_output_root": str((self.stage_output_root / identity).resolve()),
                "planning_output_key": planning_key,
                "fulltext_output_dir": str((self.stage_output_root / identity).resolve()),
                "fulltext_output_key": fulltext_output_key,
            },
            "score_review": {
                "decision_artifact_path": None,
                "decision_ids": [],
                "decision_sha256": None,
                "application_status": "not_available",
            },
            "stages": [self._new_stage(stage_id, name) for stage_id, name in STAGE_DEFINITIONS],
            "failure": None,
        }
        self._save_state(state)
        return self._continue(state)

    def resume(self, run_id: str) -> dict[str, Any]:
        state = self.load_state(run_id)
        if state.get("pipeline_status") in {
            FINAL_STATUS,
            ANALYSIS_READY_STATUS,
            WRITING_PLAN_READY_STATUS,
        }:
            self._record_completed_skips(state)
            self._save_state(state)
            return state
        if state.get("pipeline_status") == "RUNNING":
            raise BidPipelineStateError(
                "already_running: "
                f"run_id={state.get('run_id')} current_stage={state.get('current_stage')}"
            )
        state["failure"] = None
        return self._continue(state)

    def continue_to_writing_plan(self, run_id: str) -> dict[str, Any]:
        """Continue an analysis-ready run through Stage5-8, never into Stage9."""
        state = self.load_state(run_id)
        status = state.get("pipeline_status")
        if status == WRITING_PLAN_READY_STATUS:
            self._record_completed_skips(state)
            self._save_state(state)
            return state
        if status != ANALYSIS_READY_STATUS:
            raise BidPipelineStateError(
                "writing_plan_requires_analysis_ready: "
                f"run_id={state.get('run_id')} pipeline_status={status}"
            )
        state.setdefault("inputs", {})["stop_after_stage"] = WRITING_PLAN_STOP_STAGE
        state["completed_at"] = None
        state["failure"] = None
        return self._continue(state)

    def continue_full_pipeline(self, run_id: str) -> dict[str, Any]:
        """Continue one existing analysis run through the canonical final stage.

        Analysis and writing-plan ready states are deliberate Web pause points, not
        separate runs.  Clearing the stop marker resumes the same durable stage list.
        """
        state = self.load_state(run_id)
        status = str(state.get("pipeline_status") or "")
        if status == FINAL_STATUS:
            self._record_completed_skips(state)
            self._save_state(state)
            return state
        if status == "RUNNING":
            raise BidPipelineStateError(
                "already_running: "
                f"run_id={state.get('run_id')} current_stage={state.get('current_stage')}"
            )
        analysis_stage = self._stage(state, ANALYSIS_STOP_STAGE)
        if analysis_stage.get("status") != "completed":
            raise BidPipelineStateError(
                "full_pipeline_requires_analysis_ready: "
                f"run_id={state.get('run_id')} pipeline_status={status}"
            )
        state.setdefault("inputs", {})["stop_after_stage"] = None
        state["completed_at"] = None
        state["failure"] = None
        return self._continue(state)

    def status(self, run_id: str) -> dict[str, Any]:
        state = self.load_state(run_id)
        counts: dict[str, int] = {}
        for stage in state["stages"]:
            status = str(stage.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        failed = next((item for item in state["stages"] if item.get("status") == "failed"), None)
        return {
            "run_id": state["run_id"],
            "pipeline_status": state["pipeline_status"],
            "final_status": state.get("final_status"),
            "current_stage": state.get("current_stage"),
            "stage_counts": counts,
            "failed_stage": failed.get("stage_id") if failed else None,
            "failure": deepcopy(state.get("failure")),
            "pipeline_state_path": state["routing"]["pipeline_state_path"],
            "updated_at": state["updated_at"],
        }

    def inspect(self, run_id: str) -> dict[str, Any]:
        state = self.load_state(run_id)
        checks: list[dict[str, Any]] = []
        for stage in state["stages"]:
            for name, value in (stage.get("inputs") or {}).items():
                path = Path(value)
                checks.append({
                    "stage_id": stage["stage_id"],
                    "artifact": name,
                    "direction": "input",
                    "path": str(path),
                    "exists": path.exists(),
                    "is_file": path.is_file(),
                })
            for name, value in (stage.get("artifacts") or {}).items():
                path = Path(value)
                checks.append({
                    "stage_id": stage["stage_id"],
                    "artifact": name,
                    "direction": "output",
                    "path": str(path),
                    "exists": path.exists(),
                    "is_file": path.is_file(),
                })
        return {"state": state, "artifact_checks": checks}

    def load_state(self, run_id: str) -> dict[str, Any]:
        path = self._state_path(_safe_component(run_id, fallback="invalid"))
        if not path.is_file():
            raise FileNotFoundError(f"bid pipeline state not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise BidPipelineStateError("unsupported bid pipeline state schema")
        if payload.get("run_id") != path.parent.name:
            raise BidPipelineStateError("bid pipeline run_id does not match state path")
        return payload

    def _continue(self, state: dict[str, Any]) -> dict[str, Any]:
        state["pipeline_status"] = "RUNNING"
        state["final_status"] = None
        self._save_state(state)
        for stage in state["stages"]:
            if stage["status"] == "completed":
                stage["skip_count"] = int(stage.get("skip_count") or 0) + 1
                stage["last_skipped_at"] = utc_now()
                self._save_state(state)
                if stage["stage_id"] == state.get("inputs", {}).get("stop_after_stage"):
                    return self._complete_stop(state)
                continue
            if not self._artifacts_available_for_dependencies(state, stage["stage_id"]):
                self._fail_stage(
                    state,
                    stage,
                    BidPipelineStateError("a completed dependency references a missing artifact"),
                )
                return state
            self._execute_stage(state, stage)
            if stage["status"] != "completed":
                return state
            if stage["stage_id"] == state.get("inputs", {}).get("stop_after_stage"):
                return self._complete_stop(state)
        state["pipeline_status"] = FINAL_STATUS
        state["final_status"] = FINAL_STATUS
        state["current_stage"] = None
        state["failure"] = None
        state["completed_at"] = utc_now()
        self._save_state(state)
        return state

    def _complete_stop(self, state: dict[str, Any]) -> dict[str, Any]:
        stop_stage = state.get("inputs", {}).get("stop_after_stage")
        ready_status = (
            WRITING_PLAN_READY_STATUS
            if stop_stage == WRITING_PLAN_STOP_STAGE
            else ANALYSIS_READY_STATUS
        )
        state["pipeline_status"] = ready_status
        state["final_status"] = ready_status
        state["current_stage"] = None
        state["failure"] = None
        state["completed_at"] = utc_now()
        self._save_state(state)
        return state

    def _complete_analysis(self, state: dict[str, Any]) -> dict[str, Any]:
        """Backward-compatible alias retained for callers and older tests."""
        return self._complete_stop(state)

    def _execute_stage(self, state: dict[str, Any], stage: dict[str, Any]) -> None:
        if stage.get("status") == "failed" and stage.get("error"):
            attempts = list(stage.get("attempt_history") or [])
            attempts.append({
                "attempt": int(stage.get("attempts") or 0),
                "status": "failed",
                "error": deepcopy(stage["error"]),
                "completed_at": stage.get("completed_at"),
            })
            stage["attempt_history"] = attempts
        stage["status"] = "running"
        stage["attempts"] = int(stage.get("attempts") or 0) + 1
        stage["started_at"] = utc_now()
        stage["completed_at"] = None
        stage["error"] = None
        stage["gate"] = {"passed": None, "reason": None}
        state["current_stage"] = stage["stage_id"]
        self._save_state(state)
        try:
            stage["inputs"] = self._collect_stage_inputs(state, stage["stage_id"])
            self._save_state(state)
            override = self.stage_overrides.get(stage["stage_id"])
            raw = override(state) if override else self._handlers[stage["stage_id"]](state)
            result = raw if isinstance(raw, StageResult) else StageResult(**raw)
            stage["artifacts"] = self._normalize_artifacts(result.artifacts)
            stage["outputs"] = deepcopy(result.outputs)
            stage["details"] = deepcopy(result.details)
            stage["gate"] = {
                "passed": bool(result.gate_passed),
                "reason": result.gate_reason,
            }
            if not result.gate_passed:
                raise RuntimeError(result.gate_reason or "stage gate rejected the service result")
            self._validate_recorded_artifacts(stage)
            stage["status"] = "completed"
            stage["completed_at"] = utc_now()
            state["current_stage"] = None
            self._save_state(state)
        except BaseException as exc:
            self._fail_stage(state, stage, exc)
            # Ordinary service failures retain the runner's existing contract:
            # persist FAILED and return the state to the caller.  Control-flow
            # exceptions must retain their Python/CLI exit semantics, but only
            # after the durable failure state has been written.
            if not isinstance(exc, Exception):
                raise

    def _fail_stage(
        self,
        state: dict[str, Any],
        stage: dict[str, Any],
        exc: BaseException,
    ) -> None:
        error = {
            "error_type": type(exc).__name__,
            "message": re.sub(r"\s+", " ", str(exc)).strip()[:2000],
            "failed_at": utc_now(),
        }
        stage["status"] = "failed"
        stage["completed_at"] = utc_now()
        stage["error"] = error
        if stage.get("gate", {}).get("passed") is None:
            stage["gate"] = {"passed": False, "reason": error["message"]}
        state["pipeline_status"] = "FAILED"
        state["final_status"] = None
        state["current_stage"] = stage["stage_id"]
        state["failure"] = {"stage_id": stage["stage_id"], **error}
        self._save_state(state)

    def _stage_validate_inputs(self, state: dict[str, Any]) -> StageResult:
        inputs = state["inputs"]
        source_dir = Path(inputs["source_dir"])
        company_outline = Path(inputs["company_outline"])
        if not source_dir.is_dir():
            raise FileNotFoundError(f"source_dir not found: {source_dir}")
        source_files = discover_selected_files(source_dir)
        archive_files = self._discover_archives(source_dir)
        if not source_files and not archive_files:
            raise ValueError(f"no supported tender source files found: {source_dir}")
        if not company_outline.is_file():
            raise FileNotFoundError(f"default company outline not found: {company_outline}")
        company = load_company_outline(company_outline)
        validation = validate_company_outline(company)
        if not validation.get("valid"):
            raise ValueError(
                "company outline validation failed: "
                + "; ".join(str(item) for item in validation.get("errors") or [])
            )
        return StageResult(
            artifacts={"company_outline": str(company_outline)},
            outputs={
                "source_file_count": len(source_files) + len(archive_files),
                "lot_name_supplied": bool(inputs["lot_name"]),
            },
            details={
                "company_outline_validation": validation,
                "source_dir": str(source_dir),
                "source_files": [str(path) for path in source_files],
                "source_archives": [str(path) for path in archive_files],
            },
        )

    def _stage_ingest_sources(self, state: dict[str, Any]) -> StageResult:
        service = KnowledgeBaseService(metadata=self._source_store(state))
        source_dir = Path(state["inputs"]["source_dir"])
        source_files = discover_selected_files(source_dir)
        manifest = state["source_manifest"]
        existing_by_path = {
            str(Path(item["path"]).resolve()): item
            for item in manifest.get("files") or []
            if item.get("path") and item.get("file_id")
        }
        artifacts: dict[str, str] = {}
        entries: list[dict[str, Any]] = []
        for index, path in enumerate(source_files, start=1):
            resolved = str(path.resolve())
            entry = existing_by_path.get(resolved)
            if entry is None:
                record = service.ingest_file(
                    path,
                    "待分类",
                    ["自动识别", BID_PIPELINE_SOURCE_TAG, f"bid_run:{state['run_id']}"],
                )
                stored_path = resolve_storage_key(record["storage_key"]).resolve()
                entry = {
                    "file_id": record["id"],
                    "original_name": record.get("original_name") or path.name,
                    "path": resolved,
                    "stored_path": str(stored_path),
                    "parsed_status": "pending",
                    "source_origin": None,
                    "ownership_role": None,
                    "document_role": None,
                    "detected_role": None,
                    "duplicate_of": None,
                    "derivative_of": None,
                    "selection_reason": "included_by_source_dir_boundary",
                    "selected_as": [],
                    "selection_score": 0,
                    "score_selection_score": 0,
                    "included_for_requirements": False,
                    "included_for_writing_context": True,
                }
                manifest["files"] = self._deduplicate_source_manifest_entries(
                    entries
                    + [entry]
                    + [
                        item
                        for item in manifest.get("files") or []
                        if str(Path(item.get("path") or "").resolve()) not in {resolved}
                    ]
                )
                self._save_state(state)
            entries.append(entry)
            artifacts[f"ingested_source_{index:03d}"] = entry["stored_path"]
        package_by_source = {
            str(item.get("source_path") or ""): item
            for item in manifest.get("package_manifests") or []
        }
        for archive_index, archive_path in enumerate(self._discover_archives(source_dir), start=1):
            archive_key = str(archive_path.resolve())
            package_record = package_by_source.get(archive_key)
            if package_record and Path(str(package_record.get("manifest_path") or "")).is_file():
                package = json.loads(Path(package_record["manifest_path"]).read_text(encoding="utf-8"))
            else:
                package_service = SourcePackageService(output_root=Path(state["routing"]["pipeline_run_dir"]))
                package = package_service.discover(archive_path)
                package = package_service.ingest_and_parse(
                    package,
                    service,
                    tags=["自动识别", BID_PIPELINE_SOURCE_TAG, "source_package", f"bid_run:{state['run_id']}"],
                )
                package_record = {
                    "source_path": archive_key,
                    "package_id": package["package_id"],
                    "manifest_path": package["manifest_path"],
                    "summary": package["summary"],
                }
                manifest.setdefault("package_manifests", []).append(package_record)
                self._save_state(state)
            for package_entry in package.get("files") or []:
                if (
                    not package_entry.get("file_id")
                    or package_entry.get("duplicate_of")
                    or package_entry.get("parsed_status") not in {"pending", "success"}
                ):
                    continue
                entry = self._source_manifest_entry_from_package(package_entry)
                existing = next(
                    (item for item in entries if item.get("file_id") == entry["file_id"]),
                    None,
                )
                if existing is None:
                    entries.append(entry)
                    artifacts[f"package_{archive_index:03d}_{len(entries):03d}"] = str(entry.get("stored_path") or "")
                else:
                    self._merge_source_manifest_entry(existing, entry)
            artifacts[f"package_manifest_{archive_index:03d}"] = str(package["manifest_path"])
        manifest["files"] = self._deduplicate_source_manifest_entries(entries)
        manifest["selection_status"] = "ingested"
        self._save_state(state)
        artifacts["source_metadata"] = state["routing"]["source_metadata_path"]
        return StageResult(
            artifacts=artifacts,
            outputs={"source_file_ids": [item["file_id"] for item in entries]},
            details={"source_manifest": deepcopy(manifest)},
        )

    def _stage_parse_sources(self, state: dict[str, Any]) -> StageResult:
        service = KnowledgeBaseService(metadata=self._source_store(state))
        manifest = state["source_manifest"]
        warnings: dict[str, list[Any]] = {}
        for entry in manifest.get("files") or []:
            if entry.get("parsed_status") == "success":
                continue
            parsed = service.parse_file(entry["file_id"])
            entry["parsed_status"] = "success" if parsed.get("ok") else "failed"
            warnings[entry["file_id"]] = list(parsed.get("warnings") or [])
            self._save_state(state)
            if not parsed.get("ok"):
                raise RuntimeError(
                    f"source parsing failed: {entry['original_name']}: {parsed.get('message') or 'unknown error'}"
                )

        source_metadata = self._all_source_metadata(state)
        records = {
            str(item.get("id") or item.get("file_id") or ""): item
            for item in source_metadata.get("files") or []
            if isinstance(item, dict)
        }
        for entry in manifest.get("files") or []:
            record = records[entry["file_id"]]
            if not isinstance(record.get("classification"), dict):
                entry["classification"] = service.classify_file(entry["file_id"])
                self._save_state(state)
        source_metadata = self._all_source_metadata(state)
        self._detect_roles_and_select_anchors(state, source_metadata)
        resolution_status = state["inputs"].get("lot_resolution_status")
        explicit_lot = (
            state["inputs"].get("lot_name")
            if resolution_status == "explicit"
            or (resolution_status is None and state["inputs"].get("lot_name"))
            else None
        )
        lot_resolution = resolve_project_lot(
            self._source_metadata(state),
            explicit_lot_name=explicit_lot,
        )
        state["inputs"].update(lot_resolution)
        if lot_resolution["lot_resolution_status"] == "ambiguous":
            self._save_state(state)
            summary = ", ".join(
                f"{item.get('display_name') or item.get('value')} "
                f"[{item.get('source_location') or 'unknown location'}]"
                for item in lot_resolution.get("lot_candidates") or []
            )
            raise BidPipelineStateError(
                "multiple lot/package candidates found; pass --lot-name to select one: " + summary
            )
        if state["inputs"].get("lot_name"):
            state["routing"]["planning_output_key"] = _safe_component(
                state["inputs"]["lot_name"], fallback=state["run_id"]
            )
        else:
            state["routing"]["planning_output_key"] = _safe_component(
                state["run_id"], fallback="bid-run"
            )
        self._save_state(state)
        tender_file_id = manifest["tender_file_id"]
        score_file_id = manifest["score_file_id"]
        scoped_records = self._source_metadata(state).get("files") or []
        reference_resolution = SourceReferenceResolver().resolve(scoped_records)
        project_profile = ProjectPackageProfileBuilder().build(scoped_records, reference_resolution)
        profile_path = Path(state["routing"]["pipeline_run_dir"]) / "project_package_profile.json"
        profile_path.write_text(json.dumps(project_profile, ensure_ascii=False, indent=2), encoding="utf-8")
        missing = list(reference_resolution.get("missing_references") or [])
        critical_missing = [
            item
            for item in missing
            if any(
                token in str(item.get("expected_name") or item.get("reference_text") or "")
                for token in ("项目采购需求",)
            )
        ]
        completeness = {
            "status": "needs_review" if critical_missing else "complete",
            "references_total": len(reference_resolution.get("references") or []),
            "resolved_references": len([item for item in reference_resolution.get("references") or [] if item.get("status") == "resolved"]),
            "missing_references": missing,
            "critical_missing_references": critical_missing,
        }
        manifest["reference_resolution"] = reference_resolution
        manifest["project_profile_path"] = str(profile_path.resolve())
        manifest["package_completeness"] = completeness
        self._save_state(state)
        return StageResult(
            outputs={
                "source_file_ids": [item["file_id"] for item in manifest.get("files") or []],
                "tender_file_id": tender_file_id,
                "score_file_id": score_file_id,
                "lot_name": state["inputs"]["lot_name"],
                "lot_resolution_status": state["inputs"]["lot_resolution_status"],
                "project_profile_path": str(profile_path.resolve()),
            },
            details={
                "parse_warnings": warnings,
                "source_manifest": deepcopy(manifest),
                "lot_resolution": deepcopy(lot_resolution),
                "package_completeness": completeness,
            },
            gate_passed=not critical_missing,
            gate_reason=(
                "referenced procurement source missing: "
                + ", ".join(str(item.get("expected_name") or item.get("reference_text")) for item in critical_missing)
                if critical_missing
                else None
            ),
        )

    def _stage_build_tender_project(self, state: dict[str, Any]) -> StageResult:
        parsed = self._stage(state, "parse_sources")["outputs"]
        source_store = self._source_store(state)
        service = KnowledgeBaseService(metadata=source_store)
        review_service = BidScoreReviewService(output_dir=self.output_dir)
        project_profile = self._load_project_package_profile(state)
        review_application: dict[str, Any] | None = None
        score_record = source_store.get_file(parsed["score_file_id"])
        machine_parse = score_record.get("score_table_parse")
        if isinstance(machine_parse, dict) and machine_parse.get("schema_version") == "score_table_parse_v2":
            if project_profile:
                profiled_machine_parse = apply_project_profile_to_score_parse(machine_parse, project_profile)
                source_store.update_file(parsed["score_file_id"], {"score_table_parse": profiled_machine_parse})
            review_application = review_service.effective_score_parse(state["run_id"])
            result = service.parse_tender_project(
                parsed["tender_file_id"],
                parsed["score_file_id"],
                state["inputs"]["lot_name"],
                score_parse_override=review_application["effective_score_parse"],
                project_profile=project_profile,
            )
        else:
            result = service.parse_tender_project(
                parsed["tender_file_id"],
                parsed["score_file_id"],
                state["inputs"]["lot_name"],
                project_profile=project_profile,
            )
            try:
                review_application = review_service.effective_score_parse(state["run_id"])
            except BidScoreReviewError:
                review_application = None
        if review_application is not None:
            self._record_score_review_application(state, review_application)
        ok = result.get("status") == "success" and bool(result.get("merged"))
        return StageResult(
            outputs={
                "tender_file_id": parsed["tender_file_id"],
                "score_file_id": parsed["score_file_id"],
                "lot_name": state["inputs"]["lot_name"],
                "score_item_count": len(result.get("score_items") or []),
                "generation_task_count": len(result.get("generation_tasks") or []),
                "score_rules_extracted": result.get("score_rules_extracted"),
                "technical_template_resolution_status": result.get("technical_template_resolution_status"),
                "component_weights_unresolved": result.get("component_weights_unresolved"),
            },
            details={
                "service_status": result.get("status"),
                "message": result.get("message"),
                "review_gate": result.get("review_gate"),
                "score_rules_extracted": result.get("score_rules_extracted"),
                "technical_template_resolution_status": result.get("technical_template_resolution_status"),
                "technical_template_unresolved": result.get("technical_template_unresolved"),
                "component_weights_unresolved": result.get("component_weights_unresolved"),
                "unsupported_score_format": result.get("unsupported_score_format"),
                "technical_candidate_count": result.get("technical_candidate_count"),
                "business_score_table_extracted": result.get("business_score_table_extracted"),
                "price_scoring_method_extracted": result.get("price_scoring_method_extracted"),
                "weights": deepcopy(result.get("weights") or {}),
                "score_review_application_status": (
                    review_application.get("application_status") if review_application else "not_available"
                ),
            },
            gate_passed=ok,
            gate_reason=None if ok else str(result.get("message") or "tender project merge failed"),
        )

    def _stage_extract_tender_metadata(self, state: dict[str, Any]) -> StageResult:
        result = extract_tender_metadata(
            state["inputs"]["lot_name"],
            metadata=self._source_metadata(state),
            save=False,
            lot_resolution_status=state["inputs"].get("lot_resolution_status"),
        )
        self._save_project_field(state, "tender_project_metadata", result)
        return StageResult(
            outputs={"lot_name": state["inputs"]["lot_name"]},
            details={
                "schema_version": result.get("schema_version") or result.get("version"),
                "manual_confirm_count": len(result.get("manual_confirm_items") or []),
            },
        )

    def _stage_match_enterprise_materials(self, state: dict[str, Any]) -> StageResult:
        project = self._load_project(state)
        result = analyze_project_materials(project, self._enterprise_metadata())
        self._save_project_field(
            state,
            "material_analysis",
            {
                "summary": result.get("summary", {}),
                "task_material_map": result.get("task_material_map", []),
                "matched_materials": result.get("matched_materials", []),
                "missing_materials": result.get("missing_materials", []),
            },
        )
        return StageResult(
            outputs={"lot_name": state["inputs"]["lot_name"]},
            details={"summary": deepcopy(result.get("summary") or {})},
        )

    def _stage_build_project_response_context(self, state: dict[str, Any]) -> StageResult:
        project = self._load_project(state)
        result = build_response_outline(
            project,
            metadata=self._source_metadata(state),
            output_dir=self._stage_output_dir(state),
            output_key=state["routing"]["planning_output_key"],
        )
        self._save_project_field(
            state,
            "response_outline",
            {
                "version": result.get("version") or "v1.5",
                "output_dir": result.get("output_dir"),
                "files": result.get("files", []),
                "summary": result.get("summary", {}),
            },
        )
        artifacts = {
            str(item["kind"]): str(item["path"])
            for item in result.get("files") or []
            if isinstance(item, dict) and item.get("kind") and item.get("path")
        }
        return StageResult(
            artifacts=artifacts,
            details={"summary": deepcopy(result.get("summary") or {})},
            gate_passed="technical_response_context" in artifacts,
            gate_reason=(
                None
                if "technical_response_context" in artifacts
                else "project response context service returned no context path"
            ),
        )

    def _stage_build_requirement_analysis(self, state: dict[str, Any]) -> StageResult:
        project = self._load_project(state)
        matrix = build_tender_requirement_matrix(
            state["inputs"]["lot_name"],
            output_dir=self._stage_output_dir(state),
            metadata=self._source_metadata(state),
            project=project,
            output_key=state["routing"]["planning_output_key"],
        )
        actionable = build_actionable_requirement_index(
            state["inputs"]["lot_name"],
            output_dir=self._stage_output_dir(state),
            output_key=state["routing"]["planning_output_key"],
        )
        artifacts = dict(matrix.get("files") or {})
        artifacts.update({
            "actionable_requirement_index_json": actionable["index_path"],
            "actionable_requirement_index_markdown": actionable["markdown_path"],
        })
        return StageResult(
            artifacts=artifacts,
            details={
                "matrix_statistics": deepcopy(matrix.get("statistics") or {}),
                "actionable_statistics": deepcopy(actionable.get("statistics") or {}),
            },
        )

    def _stage_build_scoring_analysis(self, state: dict[str, Any]) -> StageResult:
        result = build_score_material_outputs(
            state["inputs"]["lot_name"],
            output_dir=self._stage_output_dir(state),
            project=self._load_project(state),
            output_key=state["routing"]["planning_output_key"],
        )
        return StageResult(
            artifacts=dict(result.get("files") or {}),
            details={"summary": deepcopy(result.get("summary") or {})},
        )

    def _stage_build_technical_response_outline(self, state: dict[str, Any]) -> StageResult:
        result = build_technical_outline_from_directory(
            state["inputs"]["source_dir"],
            output_dir=self._stage_output_dir(state),
            output_key=state["routing"]["planning_output_key"],
            project=self._load_project(state),
        )
        return StageResult(
            artifacts={
                "technical_response_outline_json": result["files"]["json"],
                "technical_response_outline_markdown": result["files"]["markdown"],
                "technical_response_outline_html": result["files"]["html_preview"],
            },
            details={
                "statistics": deepcopy(result.get("statistics") or {}),
                "source_file_ids": [
                    item["file_id"] for item in state["source_manifest"].get("files") or []
                ],
            },
        )

    def _stage_build_technical_writing_outline(self, state: dict[str, Any]) -> StageResult:
        response = self._artifact(
            state, "build_technical_response_outline", "technical_response_outline_json"
        )
        result = build_technical_writing_outline_files(
            state["inputs"]["company_outline"],
            response,
            output_key=state["routing"]["planning_output_key"],
            output_dir=self._stage_output_dir(state),
        )
        validation = result.get("company_validation") or {}
        return StageResult(
            artifacts=dict(result.get("files") or {}),
            details={"company_validation": deepcopy(validation)},
            gate_passed=bool(validation.get("valid")),
            gate_reason=(
                None if validation.get("valid") else "technical writing outline rejected the company outline"
            ),
        )

    def _stage_build_writing_tasks(self, state: dict[str, Any]) -> StageResult:
        response = self._artifact(
            state, "build_technical_response_outline", "technical_response_outline_json"
        )
        writing = self._artifact(
            state, "build_technical_writing_outline", "technical_writing_outline_json"
        )
        company = state["inputs"]["company_outline"]
        project_context = self._artifact(
            state, "build_project_response_context", "technical_response_context"
        )
        tasks = build_section_task_plan_files(
            response,
            writing,
            company,
            output_key=state["routing"]["planning_output_key"],
            output_dir=self._stage_output_dir(state),
            project_context_path=project_context,
        )
        task_json = tasks["files"]["section_writing_tasks_json"]
        information = build_task_information_files(
            response,
            writing,
            task_json,
            company,
            output_key=state["routing"]["planning_output_key"],
            output_dir=self._stage_output_dir(state),
        )
        enriched = enrich_writing_outline_files(
            writing,
            information["files"]["task_information_requirements_json"],
            task_json,
            markdown_renderer=render_outline_markdown,
        )
        # Enrichment intentionally replaces the canonical writing outline.
        # Rebuild task information against that final artifact so its strict
        # source-integrity record describes the inputs Stage9 will consume.
        information = build_task_information_files(
            response,
            writing,
            task_json,
            company,
            output_key=state["routing"]["planning_output_key"],
            output_dir=self._stage_output_dir(state),
        )
        draft_units = build_draft_unit_files(
            writing,
            task_json,
            information["files"]["task_information_requirements_json"],
            output_key=state["routing"]["planning_output_key"],
            output_dir=self._stage_output_dir(state),
        )
        artifacts = {
            **tasks["files"],
            **information["files"],
            **draft_units["files"],
            **enriched["files"],
        }
        return StageResult(
            artifacts=artifacts,
            details={
                "task_summary": deepcopy((tasks.get("plan") or {}).get("summary") or {}),
                "readiness_matrix": deepcopy(information.get("readiness_matrix") or {}),
                "draft_unit_summary": deepcopy((draft_units.get("draft_units") or {}).get("summary") or {}),
            },
        )

    def _stage_generate_fulltext(self, state: dict[str, Any]) -> StageResult:
        stage = self._stage(state, "generate_fulltext")
        runner = self.fulltext_runner_factory(output_dir=state["routing"]["fulltext_output_dir"])
        existing = runner.inspect_existing_run(output_key=state["routing"]["fulltext_output_key"])
        stage9_inputs = (
            dict(self.stage9_input_loader(state))
            if self.stage9_input_loader and not existing.get("exists")
            else {}
        )
        facts_path = stage9_inputs.get("confirmed_project_facts_path")
        handoff = stage9_inputs.get("writing_constraint_context")
        handoff_path: Path | None = None
        if isinstance(handoff, dict):
            handoff_path = Path(state["routing"]["pipeline_run_dir"]) / "stage9_input_handoff.json"
            _atomic_write_json(handoff_path, handoff)
        recovery = deepcopy((stage.get("details") or {}).get("chapter_recovery") or {})
        if existing.get("exists"):
            # Tender metadata is durable project context, but intentionally
            # runtime-only for recovery: it must reach the retried chapter
            # without becoming lineage for preserved chapter artifacts.
            tender_metadata = deepcopy(
                (self._load_project(state).get("tender_project_metadata") or {})
            )
            previous = existing["index"]
            failed = [
                str(item.get("chapter_id"))
                for item in previous.get("chapters") or []
                if item.get("status") == "failed"
            ]
            reused = [
                {
                    "chapter_id": str(item.get("chapter_id")),
                    "candidate_sha256": item.get("candidate_json_sha256"),
                    "run_sha256": item.get("run_json_sha256"),
                }
                for item in previous.get("chapters") or []
                if item.get("status") == "completed"
            ]
            result = runner.recover_existing_run(
                output_key=state["routing"]["fulltext_output_key"],
                stream_model=self.stream_model,
                tender_metadata=tender_metadata,
            )
            attempts = list(recovery.get("retry_attempts") or [])
            attempts.append({
                "chapter_id": failed[0] if len(failed) == 1 else None,
                "attempted_at": utc_now(),
                "status": result.get("status"),
                "production_status": result.get("production_status"),
                "composition_status": result.get("composition_status"),
                "run_path": result.get("run_path"),
                "manifest_path": result.get("manifest_path"),
            })
            recovery = {
                "mode": result.get("recovery_mode") or "existing_run_recovery",
                "failed_chapters_before": failed,
                "reused_completed_chapters": reused,
                "retry_attempts": attempts,
                "recomposed": result.get("composition_status") in {"complete", "completed_with_blockers"},
            }
        else:
            result = runner.run(
                response_outline_path=self._artifact(
                    state, "build_technical_response_outline", "technical_response_outline_json"
                ),
                writing_outline_path=self._artifact(
                    state, "build_writing_tasks", "technical_writing_outline_json"
                ),
                task_information_path=self._artifact(
                    state, "build_writing_tasks", "task_information_requirements_json"
                ),
                draft_units_path=self._artifact(state, "build_writing_tasks", "draft_units_json"),
                confirmed_project_facts_path=facts_path,
                writing_constraint_context_path=handoff_path,
                tender_metadata=deepcopy(
                    (self._load_project(state).get("tender_project_metadata") or {})
                ),
                output_key=state["routing"]["fulltext_output_key"],
                web_search_policy=self.web_search_policy,
                stream_model=self.stream_model,
            )
            recovery = {
                "mode": "initial_run",
                "failed_chapters_before": [
                    str(item.get("chapter_id"))
                    for item in result.get("chapters") or []
                    if item.get("status") == "failed"
                ],
                "completed_chapters": [
                    str(item.get("chapter_id"))
                    for item in result.get("chapters") or []
                    if item.get("status") == "completed"
                ],
                "retry_attempts": [],
                "recomposed": False,
            }
        service_outputs = result.get("outputs") or {}
        artifacts = {
            key: value
            for key, value in {
                "fulltext_run": result.get("run_path"),
                "fulltext_manifest": result.get("manifest_path"),
                "chapter_production_index": service_outputs.get("chapter_production_index"),
                "technical_fulltext_json": service_outputs.get("technical_fulltext_json"),
                "technical_fulltext_preview": service_outputs.get("technical_fulltext_preview"),
                "technical_fulltext_commit": service_outputs.get("technical_fulltext_commit"),
                "fulltext_events": service_outputs.get("fulltext_events"),
            }.items()
            if value
        }
        ok = (
            result.get("status") == "completed"
            and result.get("composition_status") in {"complete", "completed_with_blockers"}
            and bool(service_outputs.get("technical_fulltext_json"))
        )
        recovery["failed_chapters_after"] = [
            str(item.get("chapter_id"))
            for item in result.get("chapters") or []
            if item.get("status") == "failed"
        ]
        return StageResult(
            artifacts=artifacts,
            outputs={
                "output_key": state["routing"]["fulltext_output_key"],
                "status": result.get("status"),
                "production_status": result.get("production_status"),
                "composition_status": result.get("composition_status"),
            },
            details={
                "chapter_recovery": recovery,
                "chapter_count_expected": result.get("chapter_count_expected"),
                "chapter_count_completed": result.get("chapter_count_completed"),
                "chapter_count_failed": result.get("chapter_count_failed"),
                "failures": deepcopy(result.get("failures") or []),
            },
            gate_passed=ok,
            gate_reason=None if ok else "technical fulltext production or composition did not complete",
        )

    def _stage_hygiene_fulltext(self, state: dict[str, Any]) -> StageResult:
        result = TechnicalFulltextHygiene().run(
            fulltext_json_path=self._artifact(state, "generate_fulltext", "technical_fulltext_json")
        )
        return StageResult(
            artifacts=self._path_outputs(result),
            details={"summary": deepcopy(result.get("summary") or {})},
            gate_passed=result.get("status") == "completed",
            gate_reason="fulltext hygiene did not complete",
        )

    def _stage_audit_fulltext(self, state: dict[str, Any]) -> StageResult:
        result = TechnicalFulltextAuditor().audit(
            fulltext_json_path=self._artifact(state, "hygiene_fulltext", "technical_fulltext_cleaned_json")
        )
        return StageResult(
            artifacts=self._path_outputs(result),
            details={"audit_result": result.get("audit_result"), "issue_counts": result.get("issue_counts")},
            gate_passed=result.get("status") == "completed",
            gate_reason="deterministic fulltext audit did not complete",
        )

    def _stage_semantic_audit(self, state: dict[str, Any]) -> StageResult:
        kwargs = {"model_profile": self.semantic_model_profile} if self.semantic_model_profile else {}
        result = TechnicalFulltextSemanticAuditor(**kwargs).audit(
            audit_json_path=self._artifact(state, "audit_fulltext", "technical_fulltext_audit_json")
        )
        return StageResult(
            artifacts=self._path_outputs(result),
            details={"semantic_audit": deepcopy(result.get("semantic_audit") or {})},
            gate_passed=result.get("status") == "completed",
            gate_reason="semantic audit did not complete",
        )

    def _stage_scoring_assessment(self, state: dict[str, Any]) -> StageResult:
        result = TechnicalFulltextScoringAssessor().assess(
            audit_json_path=self._artifact(state, "semantic_audit", "technical_fulltext_audit_json")
        )
        return StageResult(
            artifacts=self._path_outputs(result),
            details={"summary": deepcopy(result.get("summary") or {})},
            gate_passed=result.get("status") == "completed",
            gate_reason="scoring assessment did not complete",
        )

    def _stage_finalize_audit(self, state: dict[str, Any]) -> StageResult:
        result = TechnicalFulltextAuditFinalizer().finalize(
            audit_json_path=self._artifact(state, "scoring_assessment", "technical_fulltext_audit_json")
        )
        return StageResult(
            artifacts=self._path_outputs(result),
            details={"audit_result": result.get("audit_result"), "issue_counts": result.get("issue_counts")},
            gate_passed=result.get("status") == "completed",
            gate_reason="Stage11 audit finalization did not complete",
        )

    def _stage_assemble_stage12a(self, state: dict[str, Any]) -> StageResult:
        result = TechnicalFulltextReviewPackageAssembler().assemble(
            audit_json_path=self._artifact(state, "finalize_audit", "technical_fulltext_audit_json")
        )
        return StageResult(
            artifacts=self._path_outputs(result),
            outputs={"review_package_id": result.get("review_package_id"), "status": result.get("status")},
            details={"summary": deepcopy(result.get("summary") or {})},
            gate_passed=result.get("status") == "assembled",
            gate_reason="Stage12-A review package was not assembled",
        )

    @staticmethod
    def _discover_archives(source_dir: Path) -> list[Path]:
        return sorted(
            [
                path
                for path in source_dir.rglob("*")
                if path.is_file()
                and path.suffix.lower().lstrip(".") in SOURCE_PACKAGE_ARCHIVE_EXTENSIONS
                and not any(part.startswith(".") for part in path.relative_to(source_dir).parts)
            ],
            key=lambda path: str(path.relative_to(source_dir)).lower(),
        )

    @staticmethod
    def _source_manifest_entry_from_package(entry: dict[str, Any]) -> dict[str, Any]:
        stored = str(entry.get("stored_path") or "")
        stored_path = Path(stored)
        if stored and not stored_path.is_absolute():
            stored_path = resolve_storage_key(stored).resolve()
        return {
            "file_id": entry["file_id"],
            "original_name": entry.get("original_name"),
            "path": entry.get("extracted_path"),
            "stored_path": str(stored_path) if stored else None,
            "parsed_status": entry.get("parsed_status"),
            "source_package_id": entry.get("source_package_id"),
            "parent_archive_id": entry.get("parent_archive_id"),
            "archive_internal_path": entry.get("archive_internal_path"),
            "archive_depth": entry.get("archive_depth"),
            "source_sha256": entry.get("source_sha256"),
            "file_type": entry.get("file_type"),
            "logical_source_id": entry.get("logical_source_id"),
            "duplicate_of": entry.get("duplicate_of"),
            "extraction_status": entry.get("extraction_status"),
            "source_origin": None,
            "ownership_role": None,
            "document_role": None,
            "detected_role": None,
            "derivative_of": None,
            "selection_reason": "included_by_source_package_boundary",
            "selected_as": [],
            "selection_score": 0,
            "score_selection_score": 0,
            "included_for_requirements": False,
            "included_for_writing_context": True,
        }

    @staticmethod
    def _merge_source_manifest_entry(
        target: dict[str, Any],
        incoming: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Merge duplicate canonical records without discarding package lineage."""
        for key, value in incoming.items():
            if key == "file_id" or value in (None, "", [], {}):
                continue
            if key == "selected_as":
                target[key] = list(dict.fromkeys([
                    *(target.get(key) or []),
                    *value,
                ]))
            elif key in {
                "source_package_id",
                "parent_archive_id",
                "archive_internal_path",
                "archive_depth",
                "source_sha256",
                "file_type",
                "logical_source_id",
                "duplicate_of",
                "extraction_status",
            }:
                # Package fields are absent from direct-source entries.  When
                # the same canonical file is discovered in a package, retain
                # that provenance on the one manifest entry.
                target[key] = deepcopy(value)
            elif key not in target or target.get(key) in (None, "", [], {}):
                target[key] = deepcopy(value)
        return target

    @classmethod
    def _deduplicate_source_manifest_entries(
        cls,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return one canonical manifest entry per KnowledgeBase file id."""
        canonical: list[dict[str, Any]] = []
        by_file_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            file_id = str(entry.get("file_id") or "")
            if not file_id:
                canonical.append(entry)
                continue
            existing = by_file_id.get(file_id)
            if existing is None:
                canonical.append(entry)
                by_file_id[file_id] = entry
            else:
                cls._merge_source_manifest_entry(existing, entry)
        return canonical

    @staticmethod
    def _load_project_package_profile(state: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(state["source_manifest"].get("project_profile_path") or ""))
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _source_metadata(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return this run's canonical buyer sources admitted for requirements."""
        admitted = {
            str(item.get("file_id") or "")
            for item in state["source_manifest"].get("files") or []
            if item.get("file_id")
            and (
                "included_for_requirements" not in item
                or bool(item.get("included_for_requirements"))
            )
        }
        metadata = self._all_source_metadata(state)
        return {
            "files": [
                deepcopy(item)
                for item in metadata.get("files") or []
                if str(item.get("id") or item.get("file_id") or "") in admitted
            ]
        }

    def _all_source_metadata(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return every record explicitly bounded by this run's source manifest."""
        file_ids = [
            str(item.get("file_id") or "")
            for item in state["source_manifest"].get("files") or []
            if item.get("file_id")
        ]
        metadata = self._source_store(state).load()
        by_id = {
            str(item.get("id") or item.get("file_id") or ""): item
            for item in metadata.get("files") or []
            if isinstance(item, dict)
        }
        missing = sorted(file_id for file_id in file_ids if file_id not in by_id)
        if missing:
            raise BidPipelineStateError(
                "source manifest references missing metadata records: " + ", ".join(missing)
            )
        return {"files": [deepcopy(by_id[file_id]) for file_id in file_ids]}

    @staticmethod
    def _source_store(state: dict[str, Any]) -> MetadataStore:
        return MetadataStore(Path(state["routing"]["source_metadata_path"]))

    @staticmethod
    def _enterprise_metadata() -> dict[str, Any]:
        """Exclude every tender/package source from enterprise material matching."""
        metadata = MetadataStore().load()
        scoped = deepcopy(metadata)
        scoped["files"] = [
            deepcopy(item)
            for item in metadata.get("files") or []
            if isinstance(item, dict)
            and BID_PIPELINE_SOURCE_TAG not in {str(tag) for tag in item.get("tags") or []}
            and not is_tender_source_file(item)
        ]
        return scoped

    def _detect_roles_and_select_anchors(
        self,
        state: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        inspected = inspect_tender_source_files(state["inputs"]["lot_name"], metadata)
        resolution = resolve_bid_sources(
            state["source_manifest"].get("files") or [],
            metadata,
            inspected_sources=inspected,
        )
        manifest = state["source_manifest"]
        manifest.update(resolution)
        self._save_state(state)
        if manifest["selection_status"] != "selected":
            raise BidPipelineStateError(
                str(manifest.get("selection_failure") or "source anchor selection requires manual review")
            )

    def _record_score_review_application(
        self,
        state: dict[str, Any],
        application: dict[str, Any],
    ) -> None:
        """Persist only score-review provenance references, never the decision payload."""
        state["score_review"] = {
            "decision_artifact_path": application.get("decision_artifact_path"),
            "decision_ids": list(application.get("applied_decision_ids") or []),
            "decision_sha256": application.get("decision_artifact_sha256"),
            "application_status": application.get("application_status") or "not_available",
        }
        self._save_state(state)

    def _save_project_field(self, state: dict[str, Any], name: str, value: Any) -> None:
        identity = self._stage(state, "build_tender_project")["outputs"]
        data = load_tender_projects()
        matches = [
            item
            for item in data.get("projects") or []
            if item.get("lot_name") == identity["lot_name"]
            and item.get("tender_file_id") == identity["tender_file_id"]
            and item.get("score_file_id") == identity["score_file_id"]
        ]
        if len(matches) != 1:
            raise BidPipelineStateError(
                "persisted tender project identity is missing or ambiguous for this pipeline run"
            )
        matches[0][name] = deepcopy(value)
        save_tender_projects(data)

    def _load_project(self, state: dict[str, Any]) -> dict[str, Any]:
        project_ids = self._stage(state, "build_tender_project")["outputs"]
        matches = [
            item
            for item in load_tender_projects().get("projects") or []
            if item.get("lot_name") == project_ids["lot_name"]
            and item.get("tender_file_id") == project_ids["tender_file_id"]
            and item.get("score_file_id") == project_ids["score_file_id"]
        ]
        if len(matches) != 1:
            raise BidPipelineStateError(
                "persisted tender project identity is missing or ambiguous for this pipeline run"
            )
        return matches[0]

    @staticmethod
    def _new_stage(stage_id: str, name: str) -> dict[str, Any]:
        return {
            "stage_id": stage_id,
            "name": name,
            "status": "pending",
            "attempts": 0,
            "skip_count": 0,
            "started_at": None,
            "completed_at": None,
            "last_skipped_at": None,
            "inputs": {},
            "artifacts": {},
            "outputs": {},
            "details": {},
            "gate": {"passed": None, "reason": None},
            "error": None,
        }

    def _state_path(self, run_id: str) -> Path:
        return (self.state_root / run_id / "pipeline_state.json").resolve()

    def _stage_output_dir(self, state: dict[str, Any]) -> Path:
        return Path(state["routing"]["stage_output_root"])

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        _atomic_write_json(Path(state["routing"]["pipeline_state_path"]), state)

    @staticmethod
    def _stage(state: dict[str, Any], stage_id: str) -> dict[str, Any]:
        for stage in state["stages"]:
            if stage["stage_id"] == stage_id:
                return stage
        raise BidPipelineStateError(f"pipeline stage not found: {stage_id}")

    def _artifact(self, state: dict[str, Any], stage_id: str, name: str) -> str:
        stage = self._stage(state, stage_id)
        value = (stage.get("artifacts") or {}).get(name)
        if not value:
            raise BidPipelineStateError(f"artifact not recorded: {stage_id}.{name}")
        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(f"recorded artifact is unavailable: {path}")
        return str(path)

    @staticmethod
    def _normalize_artifacts(artifacts: Mapping[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, value in artifacts.items():
            if value in (None, ""):
                continue
            normalized[str(name)] = str(Path(str(value)).expanduser().resolve())
        return normalized

    @staticmethod
    def _validate_recorded_artifacts(stage: dict[str, Any]) -> None:
        missing = [name for name, value in stage.get("artifacts", {}).items() if not Path(value).exists()]
        if missing:
            raise FileNotFoundError(
                f"stage returned artifact paths that do not exist: {', '.join(sorted(missing))}"
            )

    @staticmethod
    def _path_outputs(result: Mapping[str, Any]) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in (result.get("outputs") or {}).items()
            if value and not str(key).endswith("_sha256") and "sha256" not in str(key)
        }

    def _artifacts_available_for_dependencies(self, state: dict[str, Any], stage_id: str) -> bool:
        for stage in state["stages"]:
            if stage["stage_id"] == stage_id:
                break
            if stage["status"] != "completed":
                continue
            if any(not Path(path).exists() for path in (stage.get("artifacts") or {}).values()):
                return False
        return True

    def _record_completed_skips(self, state: dict[str, Any]) -> None:
        for stage in state["stages"]:
            if stage["status"] == "completed":
                stage["skip_count"] = int(stage.get("skip_count") or 0) + 1
                stage["last_skipped_at"] = utc_now()

    def _collect_stage_inputs(self, state: dict[str, Any], stage_id: str) -> dict[str, str]:
        values: dict[str, str] = {
            "source_dir": str(Path(state["inputs"]["source_dir"]).resolve())
        }
        if stage_id in {"validate_inputs", "build_technical_writing_outline", "build_writing_tasks"}:
            values["company_outline"] = str(Path(state["inputs"]["company_outline"]).resolve())
        for source_stage, artifact_name, input_name in STAGE_INPUT_BINDINGS.get(stage_id, ()):
            source = self._stage(state, source_stage)
            path = (source.get("artifacts") or {}).get(artifact_name)
            if not path:
                raise BidPipelineStateError(
                    f"stage input artifact not recorded: {source_stage}.{artifact_name}"
                )
            values[input_name] = str(Path(path).resolve())
        return values

    @staticmethod
    def _load_recorded_json(path: str | Path, label: str) -> dict[str, Any]:
        target = Path(path)
        if not target.is_file():
            raise FileNotFoundError(f"{label} not found: {target}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise BidPipelineStateError(f"{label} must contain a JSON object")
        return payload

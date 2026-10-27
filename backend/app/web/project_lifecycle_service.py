from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from config import BACKEND_ROOT, MIME_TYPES, SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES
from app.bid_pipeline_runner import (
    ANALYSIS_READY_STATUS,
    ANALYSIS_STOP_STAGE,
    WRITING_PLAN_READY_STATUS,
    WRITING_PLAN_STOP_STAGE,
    BidPipelineRunner,
)
from app.web.dtos import (
    FactMaterialHeadlineDTO,
    LineageInfoDTO,
    PipelineRunDTO,
    PipelineRunErrorDTO,
    PipelineRunListDTO,
    PipelineStageDTO,
    ProjectCreateDTO,
    ProjectDetailDTO,
    ProjectOverviewDTO,
    ProjectSummaryDTO,
    RequirementDependencyDTO,
    RequirementDetailDTO,
    RequirementListDTO,
    RequirementResponseDTO,
    RequirementReviewSummaryDTO,
    ReviewProgressDTO,
    ScoreRangeDTO,
    ScoringHeadlineDTO,
    SourceFileDTO,
    SourceFileListDTO,
    SourceReferenceDTO,
    TenderAnalysisSummaryDTO,
    VersionRefDTO,
    WorkflowStepDTO,
)
from app.technical_fulltext_composer import load_json_object, sha256_file
from app.web.project_resolver import ReadOnlyProjectResolver


PROJECT_SCHEMA = "web_project_manifest_v1"
PROJECT_ID_PATTERN = re.compile(r"^prj-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
RUN_ID_PATTERN = re.compile(r"^bid-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
ALLOWED_EXTENSIONS = {"doc", "docx", "pdf", "xls", "xlsx", "ppt", "pptx", "txt", "md", "zip"}
OLE_EXTENSIONS = {"doc", "xls", "ppt"}
ROLE_VALUES = {"main_tender", "technical_spec", "scoring_document", "attachment", "supplement", "unknown"}
ZIP_MIME = "application/zip"
STAGE_LABELS = {
    "validate_inputs": "检查招标资料",
    "ingest_sources": "导入招标资料",
    "parse_sources": "读取招标文件",
    "build_tender_project": "识别评分规则与项目结构",
    "extract_tender_metadata": "识别项目关键信息",
    "match_enterprise_materials": "检查企业材料匹配",
    "build_project_response_context": "整理写作前上下文",
    "build_requirement_analysis": "提取招标要求",
    "build_scoring_analysis": "分析评分与材料需求",
    "build_technical_response_outline": "规划技术响应结构",
    "build_technical_writing_outline": "生成写作规划",
    "build_writing_tasks": "拆分写作任务",
    "generate_fulltext": "生成标书正文",
    "hygiene_fulltext": "清理正文结构",
    "audit_fulltext": "执行确定性审核",
    "semantic_audit": "执行语义审核",
    "scoring_assessment": "评估评分覆盖",
    "finalize_audit": "汇总机器审核",
    "assemble_stage12a": "准备人工审核",
}


class ProjectLifecycleError(RuntimeError):
    pass


class LifecycleProjectNotFoundError(LookupError):
    pass


class LifecycleSourceNotFoundError(LookupError):
    pass


class LifecycleRunNotFoundError(LookupError):
    pass


class ProjectLifecycleConflictError(ProjectLifecycleError):
    pass


class ProjectLifecycleValidationError(ProjectLifecycleError):
    pass


class PipelineLauncher(Protocol):
    def launch(self, *, project_id: str, run_id: str, resume: bool) -> None: ...

    def launch_writing_plan(self, *, project_id: str, run_id: str) -> None: ...

    def launch_full_bid_generation(self, *, project_id: str, run_id: str) -> None: ...


class DetachedPipelineLauncher:
    """Launch a service worker process; it imports BidPipelineRunner and never shells through CLI."""

    def __init__(self, outputs_root: Path, registry_root: Path) -> None:
        self.outputs_root = outputs_root
        self.registry_root = registry_root

    def launch(self, *, project_id: str, run_id: str, resume: bool) -> None:
        self._launch(project_id=project_id, run_id=run_id, mode="resume" if resume else "start")

    def launch_writing_plan(self, *, project_id: str, run_id: str) -> None:
        self._launch(project_id=project_id, run_id=run_id, mode="writing-plan")

    def launch_full_bid_generation(self, *, project_id: str, run_id: str) -> None:
        self._launch(project_id=project_id, run_id=run_id, mode="full-bid")

    def _launch(self, *, project_id: str, run_id: str, mode: str) -> None:
        project_dir = _bounded_child(self.registry_root, project_id, PROJECT_ID_PATTERN)
        log_dir = project_dir / "run_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "app.web.pipeline_worker",
            "--outputs-root",
            str(self.outputs_root),
            "--project-id",
            project_id,
            "--run-id",
            run_id,
            "--mode",
            mode,
        ]
        creationflags = 0
        popen_options: dict[str, Any] = {"cwd": str(BACKEND_ROOT), "close_fds": True}
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            popen_options["start_new_session"] = True
        with (log_dir / f"{run_id}.log").open("ab") as stream:
            subprocess.Popen(command, stdout=stream, stderr=stream, creationflags=creationflags, **popen_options)


class ProjectLifecycleService:
    def __init__(self, outputs_root: str | Path, launcher: PipelineLauncher | None = None) -> None:
        self.outputs_root = Path(outputs_root).expanduser().resolve()
        self.registry_root = (self.outputs_root / "web_projects").resolve()
        self.launcher = launcher or DetachedPipelineLauncher(self.outputs_root, self.registry_root)
        self._lock = threading.RLock()

    def create_project(self, command: ProjectCreateDTO) -> ProjectDetailDTO:
        name = command.project_name.strip()
        if not name:
            raise ProjectLifecycleValidationError("project name is required")
        with self._lock:
            if any(str(item.get("project_name") or "").casefold() == name.casefold() for item in self._manifests()):
                raise ProjectLifecycleConflictError("a project with the same name already exists")
            project_id = _new_project_id()
            project_dir = _bounded_child(self.registry_root, project_id, PROJECT_ID_PATTERN)
            (project_dir / "source_files").mkdir(parents=True, exist_ok=False)
            now = _utc_now()
            manifest = {
                "schema_version": PROJECT_SCHEMA,
                "project_id": project_id,
                "project_name": name,
                "procuring_entity": _optional_text(command.procuring_entity),
                "procurement_number": _optional_text(command.procurement_number),
                "deadline": _optional_text(command.deadline),
                "notes": _optional_text(command.notes),
                "source_files": [],
                "pipeline_runs": [],
                "current_run_id": None,
                "created_at": now,
                "updated_at": now,
            }
            self._save_manifest(manifest)
        return self.project_detail(project_id)

    def list_manifests(self) -> list[dict[str, Any]]:
        return self._manifests()

    def project_detail(self, project_id: str) -> ProjectDetailDTO:
        manifest = self._load_manifest(project_id)
        return ProjectDetailDTO(
            project=self.project_summary(manifest),
            notes=_optional_text(manifest.get("notes")),
            source_files=[self._source_dto(item) for item in manifest.get("source_files") or []],
            runs=self.list_runs(project_id).runs,
        )

    def delete_project(self, project_id: str) -> None:
        """Delete only storage whose ownership is proven by the Web project manifest."""
        with self._lock:
            manifest = self._load_manifest(project_id)
            runs = self.list_runs(project_id).runs
            if any(item.status in {"QUEUED", "CREATED", "RUNNING"} or item.full_bid_generation_active for item in runs):
                raise ProjectLifecycleConflictError("project processing is active; wait for it to finish before deleting")
            run_ids = [str(item.get("run_id") or "") for item in manifest.get("pipeline_runs") or []]
            for run_id in run_ids:
                if not RUN_ID_PATTERN.fullmatch(run_id):
                    raise ProjectLifecycleError("project manifest contains an invalid pipeline run identity")
            owned_roots = [self.outputs_root / "bid_runs" / run_id for run_id in run_ids]
            owned_roots.extend(self.outputs_root / "bid_pipeline_outputs" / run_id for run_id in run_ids)
            owned_roots.extend(self._project_state_roots("project_fact_states", "confirmed_project_facts.json", project_id))
            owned_roots.extend(self._project_state_roots("project_confirmation_states", "confirmations.json", project_id))
            project_root = _bounded_child(self.registry_root, project_id, PROJECT_ID_PATTERN)
            for path in owned_roots:
                self._remove_owned_tree(path, self.outputs_root)
            self._remove_owned_tree(project_root, self.registry_root)

    def delete_legacy_project(self, project_id: str, artifact_root: str | Path) -> None:
        """Delete a resolver-validated top-level legacy artifact root and exact identity states."""
        with self._lock:
            root = Path(artifact_root).expanduser().resolve()
            if root.parent != self.outputs_root or root.name != project_id:
                raise ProjectLifecycleValidationError("legacy project ownership is not safely bounded")
            owned_roots = self._project_state_roots("project_fact_states", "confirmed_project_facts.json", project_id)
            owned_roots.extend(self._project_state_roots("project_confirmation_states", "confirmations.json", project_id))
            for path in owned_roots:
                self._remove_owned_tree(path, self.outputs_root)
            self._remove_owned_tree(root, self.outputs_root)

    def _project_state_roots(self, directory: str, filename: str, project_id: str) -> list[Path]:
        state_root = (self.outputs_root / directory).resolve()
        if not state_root.is_dir():
            return []
        values: list[Path] = []
        for state_path in state_root.glob(f"*/*/{filename}"):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                state_path.resolve().relative_to(state_root)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("project_id") == project_id:
                values.append(state_path.parent.resolve())
        return values

    @staticmethod
    def _remove_owned_tree(path: Path, boundary: Path) -> None:
        target = path.resolve()
        root = boundary.resolve()
        target.relative_to(root)
        if target == root:
            raise ProjectLifecycleValidationError("refusing to delete a shared storage root")
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)

    def project_summary(self, manifest_or_id: dict[str, Any] | str) -> ProjectSummaryDTO:
        manifest = self._load_manifest(manifest_or_id) if isinstance(manifest_or_id, str) else manifest_or_id
        runs = self.list_runs(str(manifest["project_id"])).runs
        latest = runs[0] if runs else None
        files = list(manifest.get("source_files") or [])
        can_start, _ = self._start_readiness(files)
        if latest and latest.status in {"QUEUED", "CREATED", "RUNNING"}:
            workflow, next_action, action, action_url = "招标分析中", "查看当前处理进度", "查看分析进度", f"/projects/{manifest['project_id']}/runs/{latest.run_id}"
        elif latest and latest.analysis_ready:
            workflow, next_action, action, action_url = "招标分析完成", "查看招标分析结果", "查看招标分析", f"/projects/{manifest['project_id']}/analysis"
        elif latest and latest.status == "FAILED":
            workflow, next_action, action, action_url = "招标分析失败", "查看失败原因并继续处理", "查看失败阶段", f"/projects/{manifest['project_id']}/runs/{latest.run_id}"
        elif can_start:
            workflow, next_action, action, action_url = "资料已确认", "启动招标分析", "开始招标分析", f"/projects/{manifest['project_id']}/setup"
        else:
            workflow, next_action, action, action_url = "资料准备", "上传并确认主招标文件", "继续上传", f"/projects/{manifest['project_id']}/setup"
        if latest and latest.analysis_ready:
            workflow = "写作准备"
            next_action = "完善写作准备；未确认信息不会作为确定事实使用"
            action = "进入写作准备"
            action_url = f"/projects/{manifest['project_id']}/writing-preparation"
        if latest and latest.writing_plan_ready:
            workflow = "写作规划完成"
            next_action = "生成技术标正文"
            action = "返回项目总览"
            action_url = f"/projects/{manifest['project_id']}"
        if latest and latest.full_bid_generation_active:
            workflow = "技术标正文生成中"
            next_action = latest.current_step or "AI 正在生成技术标正文"
            action = "查看真实进度"
            action_url = f"/projects/{manifest['project_id']}/runs/{latest.run_id}"
        elif latest and latest.human_review_ready:
            workflow = "等待人工审核"
            next_action = "进入人工审核"
            action = "进入人工审核"
            action_url = f"/projects/{manifest['project_id']}/review"
        elif latest and latest.machine_audit_ready:
            workflow = "机器审核已完成"
            next_action = "查看审核结果"
            action = "查看审核结果"
            action_url = f"/projects/{manifest['project_id']}/fulltext"
        elif latest and latest.fulltext_ready:
            workflow = "完整正文已形成"
            next_action = "查看技术标正文"
            action = "查看技术标正文"
            action_url = f"/projects/{manifest['project_id']}/fulltext"
        elif latest and latest.status == "FAILED" and latest.analysis_ready:
            workflow = "生成未完成"
            next_action = latest.error.user_message if latest.error else "继续生成技术标正文"
            action = "查看失败阶段"
            action_url = f"/projects/{manifest['project_id']}/runs/{latest.run_id}"
        return ProjectSummaryDTO(
            project_id=str(manifest["project_id"]),
            project_name=str(manifest["project_name"]),
            source_document_name=next((str(item.get("display_name")) for item in files if item.get("role") == "main_tender"), None),
            current_revision=_empty_version("尚未形成正文"),
            current_reviewed_version=_empty_version("尚未进入审核"),
            workflow_status=workflow,
            release_status="技术正文待生成",
            next_action=next_action,
            blocking_count=0,
            pending_decision_count=0,
            pending_verification_count=0,
            procuring_entity=_optional_text(manifest.get("procuring_entity")),
            procurement_number=_optional_text(manifest.get("procurement_number")),
            deadline=_optional_text(manifest.get("deadline")),
            updated_at=_optional_text(manifest.get("updated_at")),
            catalog_source="registry",
            primary_action=action,
            primary_action_url=action_url,
        )

    def overview(self, project_id: str) -> ProjectOverviewDTO:
        project = self.project_summary(project_id)
        runs = self.list_runs(project_id).runs
        current = runs[0] if runs else None
        state = self._load_run_state(current.run_id) if current else None
        analysis_ready = bool(current and current.analysis_ready)
        plan_ready = bool(current and current.writing_plan_ready)
        fulltext_ready = bool(current and current.fulltext_ready)
        audit_ready = bool(current and current.machine_audit_ready)
        review_ready = bool(current and current.human_review_ready)
        current_stage = current.current_stage if current else None

        def step_status(done: bool, active_stages: set[str]) -> str:
            if done:
                return "completed"
            if current_stage in active_stages:
                return "active"
            return "pending"

        result = ProjectOverviewDTO(
            project=project,
            workflow=[
                WorkflowStepDTO(key="analysis", label="招标分析", status="completed" if analysis_ready else "active" if current and current.status in {"QUEUED", "RUNNING"} else "pending", detail="使用现有 Stage1–4 分析主链"),
                WorkflowStepDTO(key="preparation", label="写作准备", status="completed" if plan_ready else "active" if analysis_ready else "pending", detail="待确认信息按现有 placeholder policy 处理" if analysis_ready else "等待招标分析"),
                WorkflowStepDTO(key="planning", label="写作规划", status=step_status(plan_ready, {"build_technical_response_outline", "build_technical_writing_outline", "build_writing_tasks"}), detail="写作规划已形成" if plan_ready else "由同一 Pipeline Run 生成"),
                WorkflowStepDTO(key="generation", label="AI 标书生成", status=step_status(fulltext_ready, {"generate_fulltext"}), detail="已完成整标批量生成" if fulltext_ready else "AI 正在生成标书" if current_stage == "generate_fulltext" else "等待写作规划"),
                WorkflowStepDTO(key="fulltext", label="完整正文", status=step_status(fulltext_ready, {"hygiene_fulltext"}), detail="Stage10 committed fulltext 已形成" if fulltext_ready else "尚未形成正式全文"),
                WorkflowStepDTO(key="machine_audit", label="机器审核", status=step_status(audit_ready, {"audit_fulltext", "semantic_audit", "scoring_assessment", "finalize_audit", "assemble_stage12a"}), detail="Stage11 已完成" if audit_ready else "等待完整正文"),
                WorkflowStepDTO(key="human_review", label="人工审核", status="active" if review_ready else "pending", detail="审核包已就绪" if review_ready else "等待机器审核"),
            ],
            review_progress=ReviewProgressDTO(issue_decisions_total=0, issue_decisions_completed=0, fact_confirmations_total=0, fact_confirmations_completed=0, material_verifications_total=0, material_verifications_completed=0),
            scoring=ScoringHeadlineDTO(technical_score_max=0, confirmed=0, estimated_range=ScoreRangeDTO(minimum=0, maximum=0), pending_exposure=0, potential_gain=ScoreRangeDTO(minimum=0, maximum=0), single_total_score_permitted=False),
            facts_materials=FactMaterialHeadlineDTO(missing_project_facts=0, confirmations_required=0, queries_not_executed=0, candidate_materials=0, verified_materials=0),
            actionability_counts={},
            revision_ready=False,
            revision_readiness_reason="当前仍处于招标分析阶段，尚未进入正文审核。",
            lineage=LineageInfoDTO(audited_content_sha256="", audit_sha256="", scoring_sha256="", review_package_id="", human_review_id=None, finalization_id=None),
            current_pipeline_run=current,
            can_start_full_bid_generation=bool(current and current.can_start_full_bid_generation),
            can_resume=bool(current and current.resume_available),
            fulltext_ready=fulltext_ready,
            machine_audit_ready=audit_ready,
            human_review_ready=review_ready,
        )
        return result

    def add_source_file(self, project_id: str, filename: str, content_type: str | None, data: bytes) -> SourceFileDTO:
        manifest = self._load_manifest(project_id)
        display_name = _validate_original_name(filename)
        extension = Path(display_name).suffix.lower().lstrip(".")
        if extension not in ALLOWED_EXTENSIONS:
            raise ProjectLifecycleValidationError(f"unsupported tender source file type: {extension or 'none'}")
        if not data:
            raise ProjectLifecycleValidationError("empty source files are not accepted")
        if len(data) > SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES:
            raise ProjectLifecycleValidationError("source file exceeds the configured single-file limit")
        _validate_file_signature(extension, data, content_type)
        with self._lock:
            manifest = self._load_manifest(project_id)
            if any(str(item.get("display_name") or "").casefold() == display_name.casefold() for item in manifest.get("source_files") or []):
                raise ProjectLifecycleConflictError("a source file with the same display name already exists")
            file_id = f"source_{uuid.uuid4().hex[:12]}"
            role, confidence = _suggest_role(display_name)
            storage_name = f"{_safe_stem(display_name)}_{file_id}.{extension}"
            target = self._source_root(project_id) / storage_name
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            entry = {
                "file_id": file_id,
                "display_name": display_name,
                "storage_name": storage_name,
                "extension": extension,
                "mime_type": _mime_for(extension),
                "size": len(data),
                "uploaded_at": _utc_now(),
                "role": role,
                "role_confidence": confidence,
                "role_confirmed": False,
                "status": "uploaded",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            manifest.setdefault("source_files", []).append(entry)
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)
        return self._source_dto(entry)

    def files(self, project_id: str) -> SourceFileListDTO:
        manifest = self._load_manifest(project_id)
        files = list(manifest.get("source_files") or [])
        can_start, blockers = self._start_readiness(files)
        return SourceFileListDTO(
            project_id=project_id,
            files=[self._source_dto(item) for item in files],
            allowed_extensions=sorted(ALLOWED_EXTENSIONS),
            max_file_bytes=SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES,
            can_start=can_start,
            start_blockers=blockers,
        )

    def update_source_role(self, project_id: str, file_id: str, role: str) -> SourceFileDTO:
        if role not in ROLE_VALUES:
            raise ProjectLifecycleValidationError("unsupported source file role")
        with self._lock:
            manifest = self._load_manifest(project_id)
            target = self._find_source(manifest, file_id)
            if role == "main_tender":
                for item in manifest.get("source_files") or []:
                    if item.get("file_id") != file_id and item.get("role") == "main_tender":
                        item.update({"role": "attachment", "role_confidence": 1.0, "role_confirmed": True})
            target.update({"role": role, "role_confidence": 1.0, "role_confirmed": True})
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)
        return self._source_dto(target)

    def delete_source(self, project_id: str, file_id: str) -> None:
        with self._lock:
            manifest = self._load_manifest(project_id)
            if manifest.get("pipeline_runs"):
                raise ProjectLifecycleConflictError("source files cannot be removed after a pipeline run has been created")
            target = self._find_source(manifest, file_id)
            path = (self._source_root(project_id) / str(target["storage_name"])).resolve()
            path.relative_to(self._source_root(project_id))
            path.unlink(missing_ok=True)
            manifest["source_files"] = [item for item in manifest.get("source_files") or [] if item.get("file_id") != file_id]
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)

    def start_run(self, project_id: str, lot_name: str | None = None) -> PipelineRunDTO:
        with self._lock:
            manifest = self._load_manifest(project_id)
            can_start, blockers = self._start_readiness(list(manifest.get("source_files") or []))
            if not can_start:
                raise ProjectLifecycleValidationError("; ".join(blockers))
            for run in self.list_runs(project_id).runs:
                if run.status in {"QUEUED", "CREATED", "RUNNING"}:
                    raise ProjectLifecycleConflictError("this project already has an active pipeline run")
            run_id = _new_run_id()
            manifest.setdefault("pipeline_runs", []).append({"run_id": run_id, "created_at": _utc_now(), "lot_name": _optional_text(lot_name), "launch_status": "queued"})
            manifest["current_run_id"] = run_id
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)
            try:
                self.launcher.launch(project_id=project_id, run_id=run_id, resume=False)
            except Exception as exc:
                manifest["pipeline_runs"][-1]["launch_status"] = "failed"
                manifest["pipeline_runs"][-1]["launch_error"] = _redact(str(exc), self.outputs_root)
                self._save_manifest(manifest)
                raise ProjectLifecycleError("pipeline worker could not be started") from exc
        return self.run(project_id, run_id)

    def publish_current_artifact_set(
        self,
        project_id: str,
        *,
        fulltext_output_dir: str | Path,
        output_key: str,
    ) -> None:
        """Publish a completed revision only while its Stage10 parent remains current."""
        with self._lock:
            manifest = self._load_manifest(project_id)
            resolver = ReadOnlyProjectResolver(self.outputs_root)
            current = resolver.resolve(project_id)
            revision = resolver.resolve_artifact_set(
                project_id=project_id,
                fulltext_output_dir=fulltext_output_dir,
                output_key=output_key,
            )
            revision_commit = load_json_object(
                revision.commit_json, label="revision Stage10 commit"
            )
            parent = (revision_commit.get("revision_provenance") or {}).get(
                "parent_stage10"
            )
            if not isinstance(parent, dict):
                raise ProjectLifecycleValidationError(
                    "published artifact set is not a Stage10 revision"
                )
            current_commit = load_json_object(
                current.commit_json, label="current Stage10 commit"
            )
            parent_matches = (
                str(parent.get("output_key") or "")
                == str(current_commit.get("output_key") or "")
                and str(parent.get("document_id") or "")
                == str(current_commit.get("document_id") or "")
                and Path(str(parent.get("fulltext_path") or "")).resolve()
                == current.fulltext_json.resolve()
                and str(parent.get("fulltext_sha256") or "")
                == sha256_file(current.fulltext_json)
            )
            if not parent_matches:
                raise ProjectLifecycleConflictError(
                    "current artifact set changed after this revision was prepared"
                )
            latest_manifest = self._load_manifest(project_id)
            if (
                latest_manifest.get("current_run_id") != manifest.get("current_run_id")
                or latest_manifest.get("current_artifact_set")
                != manifest.get("current_artifact_set")
            ):
                raise ProjectLifecycleConflictError(
                    "current artifact set changed during revision publication"
                )
            latest_manifest["current_artifact_set"] = {
                "fulltext_output_dir": str(revision.root.parent),
                "output_key": revision.root.name,
            }
            latest_manifest["updated_at"] = _utc_now()
            self._save_manifest(latest_manifest)

    def resume_run(self, project_id: str, run_id: str) -> PipelineRunDTO:
        with self._lock:
            manifest = self._load_manifest(project_id)
            run_ref = self._find_run_ref(manifest, run_id)
            current = self.run(project_id, run_id)
            if not current.resume_available:
                raise ProjectLifecycleConflictError("this pipeline run is not resumable")
            run_ref["launch_status"] = "resuming"
            run_ref["resume_requested_at"] = _utc_now()
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)
            try:
                self.launcher.launch(project_id=project_id, run_id=run_id, resume=True)
            except Exception as exc:
                run_ref["launch_status"] = "resume_launch_failed"
                run_ref["launch_error"] = _redact(str(exc), self.outputs_root)
                self._save_manifest(manifest)
                raise ProjectLifecycleError("pipeline worker could not be resumed") from exc
        return self.run(project_id, run_id)

    def generate_writing_plan(self, project_id: str) -> PipelineRunDTO:
        """Continue the current durable run through Stage8 and stop before Stage9."""
        with self._lock:
            manifest = self._load_manifest(project_id)
            run_id = str(manifest.get("current_run_id") or "")
            if not run_id:
                raise ProjectLifecycleConflictError("project has no analysis run")
            run_ref = self._find_run_ref(manifest, run_id)
            current = self.run(project_id, run_id)
            if current.writing_plan_ready:
                return current
            if current.status != ANALYSIS_READY_STATUS:
                raise ProjectLifecycleConflictError(
                    "writing plan generation requires an analysis-ready run"
                )
            if run_ref.get("launch_status") == "planning":
                raise ProjectLifecycleConflictError(
                    "writing plan generation is already active"
                )
            run_ref["launch_status"] = "planning"
            run_ref["writing_plan_requested_at"] = _utc_now()
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)
            try:
                self.launcher.launch_writing_plan(project_id=project_id, run_id=run_id)
            except Exception as exc:
                run_ref["launch_status"] = "planning_launch_failed"
                run_ref["launch_error"] = _redact(str(exc), self.outputs_root)
                self._save_manifest(manifest)
                raise ProjectLifecycleError("writing plan worker could not be started") from exc
        return self.run(project_id, run_id)

    def start_full_bid_generation(self, project_id: str) -> PipelineRunDTO:
        """Continue the project's canonical run through Stage9-12 asynchronously."""
        with self._lock:
            manifest = self._load_manifest(project_id)
            run_id = str(manifest.get("current_run_id") or "")
            if not run_id:
                raise ProjectLifecycleConflictError("project has no analysis run")
            run_ref = self._find_run_ref(manifest, run_id)
            current = self.run(project_id, run_id)
            if current.full_bid_generation_active:
                return current
            if current.human_review_ready:
                return current
            state = self._load_run_state(run_id)
            if not _stage_completed(state, ANALYSIS_STOP_STAGE):
                raise ProjectLifecycleConflictError(
                    "full bid generation requires a completed tender analysis"
                )
            if current.status == "RUNNING":
                return current
            if current.status not in {
                ANALYSIS_READY_STATUS,
                WRITING_PLAN_READY_STATUS,
                "FAILED",
                "HUMAN_REVIEW_READY",
            }:
                raise ProjectLifecycleConflictError(
                    "the current pipeline run cannot continue full bid generation"
                )
            run_ref["launch_status"] = "full_generation"
            run_ref["full_generation_requested_at"] = _utc_now()
            run_ref.pop("launch_error", None)
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)
            try:
                self.launcher.launch_full_bid_generation(project_id=project_id, run_id=run_id)
            except Exception as exc:
                run_ref["launch_status"] = "full_generation_launch_failed"
                run_ref["launch_error"] = _redact(str(exc), self.outputs_root)
                self._save_manifest(manifest)
                raise ProjectLifecycleError("full bid generation worker could not be started") from exc
        return self.run(project_id, run_id)

    def record_full_bid_worker_outcome(
        self,
        project_id: str,
        run_id: str,
        *,
        error: str | None = None,
    ) -> None:
        """Persist only detached-adapter outcome; Pipeline state remains canonical."""
        with self._lock:
            manifest = self._load_manifest(project_id)
            run_ref = self._find_run_ref(manifest, run_id)
            run_ref["launch_status"] = "full_generation_failed" if error else "full_generation_completed"
            run_ref["full_generation_worker_finished_at"] = _utc_now()
            if error:
                run_ref["launch_error"] = _redact(error, self.outputs_root)
            else:
                run_ref.pop("launch_error", None)
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)

    def record_resume_worker_outcome(self, project_id: str, run_id: str) -> None:
        with self._lock:
            manifest = self._load_manifest(project_id)
            run_ref = self._find_run_ref(manifest, run_id)
            run_ref["launch_status"] = "resume_completed"
            run_ref["resume_worker_finished_at"] = _utc_now()
            manifest["updated_at"] = _utc_now()
            self._save_manifest(manifest)

    def list_runs(self, project_id: str) -> PipelineRunListDTO:
        manifest = self._load_manifest(project_id)
        values = [self._run_dto(manifest, item) for item in reversed(manifest.get("pipeline_runs") or [])]
        return PipelineRunListDTO(project_id=project_id, runs=values)

    def run(self, project_id: str, run_id: str) -> PipelineRunDTO:
        manifest = self._load_manifest(project_id)
        return self._run_dto(manifest, self._find_run_ref(manifest, run_id))

    def analysis_summary(self, project_id: str) -> TenderAnalysisSummaryDTO:
        manifest = self._load_manifest(project_id)
        runs = self.list_runs(project_id).runs
        latest = runs[0] if runs else None
        state = self._load_run_state(latest.run_id) if latest else None
        requirement_stage = _stage(state, "build_requirement_analysis")
        scoring_stage = _stage(state, "build_scoring_analysis")
        requirement_stats = (requirement_stage.get("details") or {}).get("matrix_statistics") or {}
        scoring_summary = (scoring_stage.get("details") or {}).get("summary") or {}
        files = list(manifest.get("source_files") or [])
        return TenderAnalysisSummaryDTO(
            project_id=project_id,
            project_name=str(manifest["project_name"]),
            status=latest.status_label if latest else "尚未开始",
            source_file_count=len(files),
            main_tender_count=sum(item.get("role") == "main_tender" for item in files),
            attachment_count=sum(item.get("role") != "main_tender" for item in files),
            requirement_count=_optional_int(requirement_stats.get("requirement_count")),
            score_item_count=_optional_int(scoring_summary.get("score_item_count")),
            technical_score_max=None,
            fact_need_count=None,
            material_need_count=_optional_int(scoring_summary.get("missing_material_request_count")),
            current_run_id=latest.run_id if latest else None,
            analysis_ready=bool(latest and latest.analysis_ready),
        )

    def requirements(self, project_id: str) -> RequirementListDTO:
        manifest = self._load_manifest(project_id)
        state = self._latest_analysis_state(manifest)
        matrix = self._requirement_matrix(state)
        requirements = [self._prewriting_requirement(item) for item in matrix.get("requirements") or []]
        return RequirementListDTO(
            project_id=project_id,
            reviewed_version=_analysis_version(state),
            summary={"total": len(requirements), "pre_writing": len(requirements)},
            requirements=requirements,
        )

    def requirement_detail(self, project_id: str, requirement_id: str) -> RequirementDetailDTO:
        manifest = self._load_manifest(project_id)
        state = self._latest_analysis_state(manifest)
        matrix = self._requirement_matrix(state)
        raw = next((item for item in matrix.get("requirements") or [] if str(item.get("requirement_id") or "") == requirement_id), None)
        if raw is None:
            raise LifecycleSourceNotFoundError(f"requirement is not available: {requirement_id}")
        requirement = self._prewriting_requirement(raw)
        material_dependencies = []
        for index, value in enumerate(raw.get("required_materials") or [], start=1):
            title = str(value.get("name") if isinstance(value, dict) else value).strip() or "待准备材料"
            trace_id = f"PREMAT-{hashlib.sha256(f'{requirement_id}:{index}:{title}'.encode('utf-8')).hexdigest()[:12].upper()}"
            material_dependencies.append(RequirementDependencyDTO(kind="evidence_requirement", title=title, status="not_started", status_label="尚未准备", description="来自写作前招标要求分析。", required=True, trace_id=trace_id))
        return RequirementDetailDTO(
            project_id=project_id,
            reviewed_version=_analysis_version(state),
            requirement=requirement,
            findings=[],
            scoring_impacts=[],
            fact_dependencies=[],
            material_dependencies=material_dependencies,
            review_states=[],
            read_only=True,
        )

    def worker_inputs(self, project_id: str, run_id: str) -> tuple[Path, str | None]:
        manifest = self._load_manifest(project_id)
        run_ref = self._find_run_ref(manifest, run_id)
        return self._source_root(project_id), _optional_text(run_ref.get("lot_name"))

    def _latest_analysis_state(self, manifest: dict[str, Any]) -> dict[str, Any]:
        for run_ref in reversed(manifest.get("pipeline_runs") or []):
            state = self._load_run_state(str(run_ref.get("run_id") or ""))
            if state and _stage(state, "build_requirement_analysis").get("status") == "completed":
                return state
        raise ProjectLifecycleConflictError("tender requirement analysis is not available yet")

    def _requirement_matrix(self, state: dict[str, Any]) -> dict[str, Any]:
        stage = _stage(state, "build_requirement_analysis")
        path = self._safe_artifact((stage.get("artifacts") or {}).get("tender_requirement_matrix_json"))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectLifecycleError("tender requirement analysis artifact is unavailable") from exc
        return payload if isinstance(payload, dict) else {}

    def _prewriting_requirement(self, item: dict[str, Any]) -> RequirementResponseDTO:
        source = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        location = source.get("source_location") if isinstance(source.get("source_location"), dict) else {}
        clause = _optional_text(source.get("clause_path"))
        materials = list(item.get("required_materials") or [])
        related_scores = list(item.get("related_score_item_ids") or [])
        compliance = str(item.get("compliance_level") or "")
        text = str((item.get("normalized_requirement") or {}).get("text") or item.get("raw_text") or "")
        return RequirementResponseDTO(
            title=str(item.get("title") or text[:80] or item.get("requirement_id") or "招标要求"),
            requirement_text=text,
            mandatory=compliance in {"fatal_if_missing", "must_respond", "evidence_required", "format_required"},
            category=_optional_text(item.get("requirement_type")),
            source_document=_optional_text(source.get("source_file_name")),
            source_location=clause,
            source_reference=SourceReferenceDTO(
                file_name=_optional_text(source.get("source_file_name")),
                clause_path=[part.strip() for part in re.split(r"\s*>\s*", clause or "") if part.strip()],
                page_start=None,
                page_end=None,
                table_name=_optional_text(location.get("sheet_name")),
                table_range=None,
                row_or_cell=(f"row {location.get('row_index')}" if location.get("row_index") is not None else None),
                source_kind="table" if location.get("table_index") is not None or location.get("sheet_name") else "paragraph",
            ),
            response_status="not_started",
            current_response=None,
            primary_response_location=None,
            response_locations=[],
            related_finding_count=0,
            related_score_count=len(related_scores),
            review=RequirementReviewSummaryDTO(),
            fact_dependency_count=0,
            material_dependency_count=len(materials),
            query_not_executed_count=0,
            trace_id=str(item.get("requirement_id") or ""),
        )

    def _run_dto(self, manifest: dict[str, Any], run_ref: dict[str, Any]) -> PipelineRunDTO:
        run_id = str(run_ref.get("run_id") or "")
        state = self._load_run_state(run_id)
        if state is None:
            launch_failed = run_ref.get("launch_status") == "failed"
            error = PipelineRunErrorDTO(category="system_error", user_message="招标分析任务未能启动", technical_detail=_optional_text(run_ref.get("launch_error"))) if launch_failed else None
            return PipelineRunDTO(
                run_id=run_id,
                project_id=str(manifest["project_id"]),
                status="FAILED" if launch_failed else "QUEUED",
                status_label="启动失败" if launch_failed else "等待启动",
                started_at=None,
                updated_at=_optional_text(run_ref.get("created_at")),
                completed_at=None,
                current_phase="任务准备",
                current_stage=None,
                current_step=None,
                completed_steps=0,
                total_steps=_analysis_stage_count(),
                stages=[],
                last_successful_checkpoint=None,
                error=error,
                resume_available=False,
                analysis_ready=False,
            )
        planning_requested = run_ref.get("launch_status") == "planning"
        full_generation_requested = run_ref.get("launch_status") == "full_generation"
        post_plan_activity = any(
            int(item.get("attempts") or 0) > 0
            for item in list(state.get("stages") or [])[_writing_plan_stage_count():]
        )
        visible_count = (
            len(STAGE_LABELS)
            if full_generation_requested or post_plan_activity
            else _writing_plan_stage_count()
            if str(state.get("pipeline_status") or "") == WRITING_PLAN_READY_STATUS
            or str((state.get("inputs") or {}).get("stop_after_stage") or "") == WRITING_PLAN_STOP_STAGE
            or planning_requested
            else _analysis_stage_count()
        )
        stages = list(state.get("stages") or [])[:visible_count]
        stage_dtos = [self._stage_dto(item) for item in stages]
        completed = [item for item in stage_dtos if item.status == "completed"]
        current_id = _optional_text(state.get("current_stage"))
        persisted_status = str(state.get("pipeline_status") or "CREATED")
        full_generation_failed = run_ref.get("launch_status") == "full_generation_failed"
        resume_queued = persisted_status == "FAILED" and run_ref.get("launch_status") == "resuming"
        full_generation_queued = (
            full_generation_requested
            and persisted_status in {ANALYSIS_READY_STATUS, WRITING_PLAN_READY_STATUS, "HUMAN_REVIEW_READY"}
            and not post_plan_activity
        )
        status = (
            "FAILED"
            if full_generation_failed
            else
            "QUEUED"
            if resume_queued
            or (persisted_status == ANALYSIS_READY_STATUS and planning_requested)
            or full_generation_queued
            else persisted_status
        )
        error = (
            PipelineRunErrorDTO(
                category="system_error",
                user_message="技术标正文生成未完成",
                technical_detail=_optional_text(run_ref.get("launch_error")),
            )
            if full_generation_failed
            else None if resume_queued
            else _run_error(state.get("failure"), current_id, self.outputs_root)
        )
        analysis_ready = _stage_completed(state, ANALYSIS_STOP_STAGE)
        writing_plan_ready = _stage_completed(state, WRITING_PLAN_STOP_STAGE)
        fulltext_ready = _artifact_ready(state, "generate_fulltext", "technical_fulltext_commit")
        machine_audit_ready = _artifact_ready(state, "finalize_audit", "technical_fulltext_audit_json")
        human_review_ready = _human_review_artifacts_ready(state)
        generation_active = (
            (full_generation_requested and not human_review_ready and status != "FAILED")
            or (status in {"QUEUED", "RUNNING"} and post_plan_activity)
        )
        return PipelineRunDTO(
            run_id=run_id,
            project_id=str(manifest["project_id"]),
            status=status,
            status_label=_run_status_label(status),
            started_at=_optional_text(state.get("created_at")),
            updated_at=_optional_text(state.get("updated_at")),
            completed_at=_optional_text(state.get("completed_at")),
            current_phase=(
                _full_pipeline_phase(current_id, writing_plan_ready, fulltext_ready, machine_audit_ready)
                if visible_count == len(STAGE_LABELS)
                else "写作规划"
                if visible_count == _writing_plan_stage_count()
                else "招标分析"
                if status in {"RUNNING", ANALYSIS_READY_STATUS}
                else "任务处理"
            ),
            current_stage=None if resume_queued else current_id,
            current_step=None if resume_queued else STAGE_LABELS.get(current_id or "") if current_id else None,
            completed_steps=len(completed),
            total_steps=len(stage_dtos),
            stages=stage_dtos,
            last_successful_checkpoint=completed[-1].label if completed else None,
            error=error,
            resume_available=status == "FAILED",
            analysis_ready=analysis_ready,
            writing_plan_ready=writing_plan_ready,
            full_bid_generation_active=generation_active,
            can_start_full_bid_generation=(
                analysis_ready
                and not generation_active
                and not human_review_ready
                and status != "RUNNING"
            ),
            fulltext_ready=fulltext_ready,
            machine_audit_ready=machine_audit_ready,
            human_review_ready=human_review_ready,
        )

    def _stage_dto(self, stage: dict[str, Any]) -> PipelineStageDTO:
        error = _run_error(stage.get("error"), str(stage.get("stage_id") or ""), self.outputs_root)
        return PipelineStageDTO(
            stage_id=str(stage.get("stage_id") or ""),
            label=STAGE_LABELS.get(str(stage.get("stage_id") or ""), "处理招标资料"),
            status=str(stage.get("status") or "pending"),
            started_at=_optional_text(stage.get("started_at")),
            completed_at=_optional_text(stage.get("completed_at")),
            attempt_count=int(stage.get("attempts") or 0),
            error=error,
        )

    def _load_run_state(self, run_id: str) -> dict[str, Any] | None:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            return None
        path = (self.outputs_root / "bid_runs" / run_id / "pipeline_state.json").resolve()
        try:
            path.relative_to(self.outputs_root)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) and value.get("run_id") == run_id else None

    def _safe_artifact(self, value: Any) -> Path:
        path = Path(str(value or "")).expanduser().resolve()
        try:
            path.relative_to(self.outputs_root)
        except ValueError as exc:
            raise ProjectLifecycleError("pipeline artifact falls outside the configured output root") from exc
        if not path.is_file():
            raise ProjectLifecycleError("pipeline artifact is unavailable")
        return path

    def _source_root(self, project_id: str) -> Path:
        root = (_bounded_child(self.registry_root, project_id, PROJECT_ID_PATTERN) / "source_files").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _find_source(self, manifest: dict[str, Any], file_id: str) -> dict[str, Any]:
        source = next((item for item in manifest.get("source_files") or [] if item.get("file_id") == file_id), None)
        if source is None:
            raise LifecycleSourceNotFoundError(f"source file is not available: {file_id}")
        return source

    @staticmethod
    def _find_run_ref(manifest: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = next((item for item in manifest.get("pipeline_runs") or [] if item.get("run_id") == run_id), None)
        if run is None:
            raise LifecycleRunNotFoundError(f"pipeline run is not available: {run_id}")
        return run

    @staticmethod
    def _start_readiness(files: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if not files:
            blockers.append("请先上传至少一份招标资料")
        primary = [item for item in files if item.get("role") == "main_tender" and item.get("role_confirmed")]
        if len(primary) != 1:
            blockers.append("请确认且仅确认一份主招标文件")
        unconfirmed = [item for item in files if not item.get("role_confirmed")]
        if unconfirmed:
            blockers.append(f"仍有 {len(unconfirmed)} 份文件角色待确认")
        return not blockers, blockers

    @staticmethod
    def _source_dto(item: dict[str, Any]) -> SourceFileDTO:
        return SourceFileDTO(
            file_id=str(item["file_id"]),
            display_name=str(item["display_name"]),
            extension=str(item["extension"]),
            mime_type=str(item["mime_type"]),
            size=int(item["size"]),
            uploaded_at=str(item["uploaded_at"]),
            role=str(item["role"]),
            role_confidence=float(item["role_confidence"]),
            role_confirmed=bool(item["role_confirmed"]),
            status=str(item["status"]),
            sha256=str(item["sha256"]),
        )

    def _manifests(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        if not self.registry_root.is_dir():
            return values
        for path in self.registry_root.glob("*/project.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("schema_version") == PROJECT_SCHEMA and value.get("project_id") == path.parent.name:
                values.append(value)
        return sorted(values, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def _load_manifest(self, project_id: str) -> dict[str, Any]:
        path = _bounded_child(self.registry_root, project_id, PROJECT_ID_PATTERN) / "project.json"
        if not path.is_file():
            raise LifecycleProjectNotFoundError(f"project is not available: {project_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectLifecycleError("project manifest is unavailable or invalid") from exc
        if not isinstance(value, dict) or value.get("schema_version") != PROJECT_SCHEMA or value.get("project_id") != project_id:
            raise ProjectLifecycleError("project manifest identity is invalid")
        return value

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        project_id = str(manifest["project_id"])
        path = _bounded_child(self.registry_root, project_id, PROJECT_ID_PATTERN) / "project.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def _new_project_id() -> str:
    return f"prj-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _new_run_id() -> str:
    return f"bid-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_child(root: Path, value: str, pattern: re.Pattern[str]) -> Path:
    if not pattern.fullmatch(str(value or "")):
        raise LifecycleProjectNotFoundError("project identity is invalid")
    child = (root / value).resolve()
    child.relative_to(root.resolve())
    return child


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _safe_stem(filename: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", Path(filename).stem).strip("._")
    return stem[:64] or "tender_source"


def _validate_original_name(filename: str) -> str:
    value = str(filename or "").strip()
    if not value or Path(value).name != value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ProjectLifecycleValidationError("source filename contains an unsafe path")
    if len(value) > 240:
        raise ProjectLifecycleValidationError("source filename is too long")
    return value


def _mime_for(extension: str) -> str:
    return ZIP_MIME if extension == "zip" else MIME_TYPES.get(extension, "application/octet-stream")


def _validate_file_signature(extension: str, data: bytes, content_type: str | None) -> None:
    if extension == "pdf" and not data.startswith(b"%PDF-"):
        raise ProjectLifecycleValidationError("PDF signature does not match the filename")
    if extension in OLE_EXTENSIONS and not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ProjectLifecycleValidationError("Office binary signature does not match the filename")
    if extension in {"docx", "xlsx", "pptx", "zip"}:
        try:
            from io import BytesIO
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
                if extension == "docx" and not any(name.startswith("word/") for name in names):
                    raise ProjectLifecycleValidationError("DOCX package does not contain a Word document")
                if extension == "xlsx" and not any(name.startswith("xl/") for name in names):
                    raise ProjectLifecycleValidationError("XLSX package does not contain a workbook")
                if extension == "pptx" and not any(name.startswith("ppt/") for name in names):
                    raise ProjectLifecycleValidationError("PPTX package does not contain a presentation")
        except zipfile.BadZipFile as exc:
            raise ProjectLifecycleValidationError("ZIP-based file is invalid or corrupted") from exc
    if extension in {"txt", "md"} and b"\x00" in data[:4096]:
        raise ProjectLifecycleValidationError("text source contains binary content")
    supplied = str(content_type or "").split(";", 1)[0].strip().lower()
    expected = _mime_for(extension).lower()
    if supplied and supplied not in {"application/octet-stream", expected, "application/x-zip-compressed"}:
        raise ProjectLifecycleValidationError("request content type does not match the source filename")


def _suggest_role(filename: str) -> tuple[str, float]:
    name = Path(filename).stem.lower()
    if any(value in name for value in ("技术参数", "技术规范", "技术要求")):
        return "technical_spec", 0.82
    if any(value in name for value in ("评分", "评标", "评分办法")):
        return "scoring_document", 0.82
    if any(value in name for value in ("招标文件", "采购文件", "谈判文件")):
        return "main_tender", 0.86
    if any(value in name for value in ("补充", "澄清", "答疑")):
        return "supplement", 0.72
    return "attachment", 0.4


def _empty_version(label: str) -> VersionRefDTO:
    return VersionRefDTO(version_id="not-created", label=label, content_sha256=None, artifact_sha256=None, status="not_started")


def _analysis_version(state: dict[str, Any]) -> VersionRefDTO:
    return VersionRefDTO(version_id=str(state.get("run_id") or "analysis"), label="招标分析版本（尚无正文）", content_sha256=None, artifact_sha256=None, status="analysis_ready")


def _analysis_stage_count() -> int:
    return list(STAGE_LABELS).index(ANALYSIS_STOP_STAGE) + 1


def _writing_plan_stage_count() -> int:
    return list(STAGE_LABELS).index(WRITING_PLAN_STOP_STAGE) + 1


def _stage(state: dict[str, Any] | None, stage_id: str) -> dict[str, Any]:
    return next((item for item in (state or {}).get("stages") or [] if item.get("stage_id") == stage_id), {})


def _stage_completed(state: dict[str, Any] | None, stage_id: str) -> bool:
    return _stage(state, stage_id).get("status") == "completed"


def _artifact_ready(state: dict[str, Any] | None, stage_id: str, artifact: str) -> bool:
    value = (_stage(state, stage_id).get("artifacts") or {}).get(artifact)
    return bool(value and Path(str(value)).is_file())


def _human_review_artifacts_ready(state: dict[str, Any] | None) -> bool:
    package = (_stage(state, "assemble_stage12a").get("artifacts") or {}).get(
        "technical_fulltext_review_package_json"
    )
    if not package:
        return False
    review_dir = Path(str(package)).parent
    return all(
        (review_dir / name).is_file()
        for name in (
            "technical_fulltext.human_review.json",
            "technical_fulltext.review_finalization.json",
        )
    )


def _full_pipeline_phase(
    current_stage: str | None,
    writing_plan_ready: bool,
    fulltext_ready: bool,
    machine_audit_ready: bool,
) -> str:
    if machine_audit_ready:
        return "人工审核准备"
    if current_stage in {
        "audit_fulltext",
        "semantic_audit",
        "scoring_assessment",
        "finalize_audit",
        "assemble_stage12a",
    }:
        return "机器审核"
    if fulltext_ready or current_stage == "hygiene_fulltext":
        return "形成完整正文"
    if writing_plan_ready:
        return "AI 标书生成"
    return "写作规划"


def _run_status_label(status: str) -> str:
    return {
        "QUEUED": "等待处理",
        "CREATED": "等待启动",
        "RUNNING": "正在分析",
        "FAILED": "分析未完成",
        ANALYSIS_READY_STATUS: "招标分析已完成",
        WRITING_PLAN_READY_STATUS: "写作规划已完成",
        "HUMAN_REVIEW_READY": "人工审核包已就绪",
    }.get(status, status)


def _run_error(value: Any, stage_id: str | None, outputs_root: Path) -> PipelineRunErrorDTO | None:
    if not isinstance(value, dict):
        return None
    error_type = str(value.get("error_type") or "PipelineError")
    message = _redact(str(value.get("message") or ""), outputs_root)
    category = "input_error" if error_type in {"ValueError", "FileNotFoundError", "BidPipelineStateError"} or stage_id in {"validate_inputs", "parse_sources"} else "system_error"
    user = "招标资料需要调整后才能继续" if category == "input_error" else "招标分析未完成"
    return PipelineRunErrorDTO(category=category, user_message=user, technical_detail=f"{error_type}: {message}".strip(": "))


def _redact(message: str, outputs_root: Path) -> str:
    result = message.replace(str(outputs_root), "[server output]")
    result = re.sub(r"[A-Za-z]:[\\/][^\n;]+", "[server path]", result)
    return result[:1000]

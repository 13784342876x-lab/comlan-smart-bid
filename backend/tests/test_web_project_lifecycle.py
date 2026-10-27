from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bid_pipeline_runner import ANALYSIS_STOP_STAGE, BidPipelineRunner, StageResult, STAGE_DEFINITIONS
from app.web.api import create_app
from app.web.dtos import ProjectCreateDTO
from app.web.project_lifecycle_service import (
    LifecycleRunNotFoundError,
    LifecycleSourceNotFoundError,
    ProjectLifecycleConflictError,
    ProjectLifecycleService,
    ProjectLifecycleValidationError,
)
from app.web.project_resolver import ReadOnlyProjectResolver


class RecordingLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.plan_calls: list[tuple[str, str]] = []
        self.full_bid_calls: list[tuple[str, str]] = []

    def launch(self, *, project_id: str, run_id: str, resume: bool) -> None:
        self.calls.append((project_id, run_id, resume))

    def launch_writing_plan(self, *, project_id: str, run_id: str) -> None:
        self.plan_calls.append((project_id, run_id))

    def launch_full_bid_generation(self, *, project_id: str, run_id: str) -> None:
        self.full_bid_calls.append((project_id, run_id))


def _zip_file(prefix: str, name: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(f"{prefix}/{name}", "content")
    return stream.getvalue()


def _project(service: ProjectLifecycleService, name: str = "Web Test Tender"):
    return service.create_project(ProjectCreateDTO(project_name=name))


def _ready_project(service: ProjectLifecycleService, name: str = "Web Test Tender"):
    project = _project(service, name)
    source = service.add_source_file(project.project.project_id, "采购文件.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _zip_file("word", "document.xml"))
    service.update_source_role(project.project.project_id, source.file_id, "main_tender")
    return project


def _analysis_overrides(tmp_path: Path, *, fail_stage: str | None = None, matrix: Path | None = None):
    context = tmp_path / "technical_response_context.json"
    context.write_text("{}", encoding="utf-8")
    result = {}
    for stage_id, _ in STAGE_DEFINITIONS:
        if stage_id == fail_stage:
            def fail(_: dict, message=stage_id):
                raise RuntimeError(f"fixture failure: {message}")
            result[stage_id] = fail
        elif stage_id == "build_project_response_context":
            result[stage_id] = lambda _: StageResult(artifacts={"technical_response_context": str(context)})
        elif stage_id == "build_requirement_analysis" and matrix is not None:
            result[stage_id] = lambda _: StageResult(
                artifacts={"tender_requirement_matrix_json": str(matrix)},
                details={"matrix_statistics": {"requirement_count": 1}},
            )
        elif stage_id == "build_scoring_analysis":
            result[stage_id] = lambda _: StageResult(details={"summary": {"score_item_count": 2, "missing_material_request_count": 1}})
        else:
            result[stage_id] = lambda _: StageResult()
    return result


def test_project_upload_role_and_storage_are_project_scoped(tmp_path: Path) -> None:
    service = ProjectLifecycleService(tmp_path, RecordingLauncher())
    project = _project(service)
    project_id = project.project.project_id
    docx = service.add_source_file(project_id, "主采购文件.docx", "application/octet-stream", _zip_file("word", "document.xml"))
    pdf = service.add_source_file(project_id, "评分办法.pdf", "application/pdf", b"%PDF-1.4\nfixture")
    xlsx = service.add_source_file(project_id, "技术参数.xlsx", "application/octet-stream", _zip_file("xl", "workbook.xml"))
    assert {docx.extension, pdf.extension, xlsx.extension} == {"docx", "pdf", "xlsx"}
    assert service.files(project_id).can_start is False
    service.update_source_role(project_id, docx.file_id, "main_tender")
    service.update_source_role(project_id, pdf.file_id, "scoring_document")
    service.update_source_role(project_id, xlsx.file_id, "technical_spec")
    listing = service.files(project_id)
    assert listing.can_start is True
    assert all("\\" not in item.display_name and "/" not in item.display_name for item in listing.files)
    stored = list((tmp_path / "web_projects" / project_id / "source_files").iterdir())
    assert len(stored) == 3
    assert all(project_id in str(item) for item in stored)
    reloaded = ProjectLifecycleService(tmp_path, RecordingLauncher()).project_detail(project_id)
    assert reloaded.project.project_name == "Web Test Tender"
    assert len(reloaded.source_files) == 3


def test_project_creation_validates_required_and_duplicate_names(tmp_path: Path) -> None:
    service = ProjectLifecycleService(tmp_path, RecordingLauncher())
    _project(service, "Unique Tender")
    with pytest.raises(ProjectLifecycleConflictError):
        _project(service, "unique tender")
    with pytest.raises(ProjectLifecycleValidationError):
        service.create_project(ProjectCreateDTO(project_name="   "))


def test_upload_rejects_invalid_empty_traversal_duplicate_and_cross_project(tmp_path: Path) -> None:
    service = ProjectLifecycleService(tmp_path, RecordingLauncher())
    first = _project(service, "First")
    second = _project(service, "Second")
    with pytest.raises(ProjectLifecycleValidationError):
        service.add_source_file(first.project.project_id, "../escape.pdf", "application/pdf", b"%PDF-1.4")
    with pytest.raises(ProjectLifecycleValidationError):
        service.add_source_file(first.project.project_id, "empty.pdf", "application/pdf", b"")
    with pytest.raises(ProjectLifecycleValidationError):
        service.add_source_file(first.project.project_id, "script.exe", "application/octet-stream", b"MZ")
    source = service.add_source_file(first.project.project_id, "采购文件.pdf", "application/pdf", b"%PDF-1.4")
    with pytest.raises(ProjectLifecycleConflictError):
        service.add_source_file(first.project.project_id, "采购文件.pdf", "application/pdf", b"%PDF-1.5")
    with pytest.raises(LifecycleSourceNotFoundError):
        service.update_source_role(second.project.project_id, source.file_id, "main_tender")


def test_start_duplicate_failure_resume_and_analysis_checkpoint(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    service = ProjectLifecycleService(tmp_path, launcher)
    project = _ready_project(service)
    project_id = project.project.project_id
    queued = service.start_run(project_id)
    assert queued.status == "QUEUED"
    with pytest.raises(ProjectLifecycleConflictError):
        service.start_run(project_id)
    source_dir, _ = service.worker_inputs(project_id, queued.run_id)
    state = BidPipelineRunner(output_dir=tmp_path, stage_overrides=_analysis_overrides(tmp_path)).run(
        source_dir=source_dir,
        run_id=queued.run_id,
        stop_after_stage=ANALYSIS_STOP_STAGE,
    )
    assert state["pipeline_status"] == "ANALYSIS_READY"
    completed = service.run(project_id, queued.run_id)
    assert completed.analysis_ready is True
    assert completed.completed_steps == completed.total_steps == 9

    failed_project = _ready_project(service, "Failure Project")
    failed = service.start_run(failed_project.project.project_id)
    failed_source, _ = service.worker_inputs(failed_project.project.project_id, failed.run_id)
    BidPipelineRunner(output_dir=tmp_path, stage_overrides=_analysis_overrides(tmp_path, fail_stage="parse_sources")).run(
        source_dir=failed_source,
        run_id=failed.run_id,
        stop_after_stage=ANALYSIS_STOP_STAGE,
    )
    failure = service.run(failed_project.project.project_id, failed.run_id)
    assert failure.status == "FAILED" and failure.resume_available
    resume_queued = service.resume_run(failed_project.project.project_id, failed.run_id)
    assert resume_queued.status == "QUEUED" and not resume_queued.resume_available
    assert launcher.calls[-1] == (failed_project.project.project_id, failed.run_id, True)
    BidPipelineRunner(output_dir=tmp_path, stage_overrides=_analysis_overrides(tmp_path)).resume(failed.run_id)
    assert service.run(failed_project.project.project_id, failed.run_id).analysis_ready
    with pytest.raises(LifecycleRunNotFoundError):
        service.run(project_id, failed.run_id)


def test_pre_writing_requirement_projection_uses_stage1_4_artifact(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    service = ProjectLifecycleService(tmp_path, launcher)
    project = _ready_project(service)
    project_id = project.project.project_id
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({
        "requirements": [{
            "requirement_id": "req-web-001",
            "title": "提供项目技术方案",
            "raw_text": "投标人应提供完整项目技术方案。",
            "normalized_requirement": {"text": "投标人应提供完整项目技术方案。"},
            "requirement_type": "technical_mandatory_requirement",
            "compliance_level": "must_respond",
            "related_score_item_ids": ["score-1"],
            "required_materials": ["技术路线说明"],
            "source_ref": {"source_file_name": "采购文件.docx", "clause_path": "第三章 > 技术要求", "source_location": {"paragraph_index": 12}},
        }]
    }, ensure_ascii=False), encoding="utf-8")
    run = service.start_run(project_id)
    source_dir, _ = service.worker_inputs(project_id, run.run_id)
    BidPipelineRunner(output_dir=tmp_path, stage_overrides=_analysis_overrides(tmp_path, matrix=matrix)).run(
        source_dir=source_dir,
        run_id=run.run_id,
        stop_after_stage=ANALYSIS_STOP_STAGE,
    )
    listing = service.requirements(project_id)
    assert listing.reviewed_version.status == "analysis_ready"
    assert listing.requirements[0].response_status == "not_started"
    assert listing.requirements[0].related_score_count == 1
    detail = service.requirement_detail(project_id, "req-web-001")
    assert detail.findings == [] and detail.review_states == []
    assert detail.material_dependencies[0].title == "技术路线说明"


def test_http_project_upload_start_and_status_smoke(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    launcher = RecordingLauncher()
    app.state.project_lifecycle_service.launcher = launcher
    client = TestClient(app)
    created = client.post("/api/projects", json={"project_name": "HTTP Project"})
    assert created.status_code == 201
    project_id = created.json()["project"]["project_id"]
    upload = client.post(
        f"/api/projects/{project_id}/files?filename=%E9%87%87%E8%B4%AD%E6%96%87%E4%BB%B6.docx",
        content=_zip_file("word", "document.xml"),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 201 and "storage" not in upload.text and "D:\\" not in upload.text
    file_id = upload.json()["file_id"]
    assert client.patch(f"/api/projects/{project_id}/files/{file_id}", json={"role": "main_tender"}).status_code == 200
    started = client.post(f"/api/projects/{project_id}/runs", json={})
    assert started.status_code == 202
    assert client.get(f"/api/projects/{project_id}/runs/{started.json()['run_id']}").status_code == 200
    assert client.get("/api/projects").json()["projects"][0]["catalog_source"] == "registry"


def test_http_delete_project_removes_only_manifest_owned_storage(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    launcher = RecordingLauncher()
    service = app.state.project_lifecycle_service
    service.launcher = launcher
    project = _ready_project(service, "Delete Me")
    project_id = project.project.project_id
    run = service.start_run(project_id)
    run_root = tmp_path / "bid_runs" / run.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "pipeline_state.json").write_text(json.dumps({
        "run_id": run.run_id,
        "pipeline_status": "FAILED",
        "stages": [],
        "routing": {},
        "failure": {"message": "fixture failure"},
    }), encoding="utf-8")
    artifact_root = tmp_path / "bid_pipeline_outputs" / run.run_id
    artifact_root.mkdir(parents=True)
    (artifact_root / "owned.txt").write_text("owned", encoding="utf-8")
    fact_state = tmp_path / "project_fact_states" / "project-key" / "lineage-key"
    fact_state.mkdir(parents=True)
    (fact_state / "confirmed_project_facts.json").write_text(json.dumps({"project_id": project_id}), encoding="utf-8")
    confirmation_state = tmp_path / "project_confirmation_states" / "project-key" / "lineage-key"
    confirmation_state.mkdir(parents=True)
    (confirmation_state / "confirmations.json").write_text(json.dumps({"project_id": project_id}), encoding="utf-8")
    shared = tmp_path / "shared_material_library"
    shared.mkdir()
    (shared / "keep.txt").write_text("shared", encoding="utf-8")
    client = TestClient(app)

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 204
    assert not (tmp_path / "web_projects" / project_id).exists()
    assert not run_root.exists()
    assert not artifact_root.exists()
    assert not fact_state.exists()
    assert not confirmation_state.exists()
    assert (shared / "keep.txt").read_text(encoding="utf-8") == "shared"
    assert all(item["project_id"] != project_id for item in client.get("/api/projects").json()["projects"])
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_http_delete_project_rejects_active_run(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    service = app.state.project_lifecycle_service
    service.launcher = RecordingLauncher()
    project = _ready_project(service, "Active Project")
    project_id = project.project.project_id
    service.start_run(project_id)

    response = TestClient(app).delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert (tmp_path / "web_projects" / project_id / "project.json").is_file()


def test_http_delete_legacy_project_is_bounded_to_resolved_artifact_root(tmp_path: Path) -> None:
    project_id = "legacy-delete-project"
    root = tmp_path / project_id
    required = (
        root / "fulltext" / "technical_fulltext.json",
        root / "fulltext" / "technical_fulltext.commit.json",
        root / "fulltext" / "hygiene" / "technical_fulltext.cleaned.json",
        root / "fulltext" / "audit" / "technical_fulltext.audit.json",
        root / "fulltext" / "audit" / "technical_fulltext.scoring_assessment.json",
        root / "fulltext" / "review" / "technical_fulltext.review_package.json",
        root / "fulltext" / "review" / "technical_fulltext.human_review.json",
    )
    for path in required:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    shared = tmp_path / "global-materials"
    shared.mkdir()
    (shared / "keep.txt").write_text("shared", encoding="utf-8")
    client = TestClient(create_app(tmp_path))

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 204
    assert not root.exists()
    assert (shared / "keep.txt").is_file()
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_full_bid_command_reuses_current_run_and_is_idempotent_while_queued(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    service = ProjectLifecycleService(tmp_path, launcher)
    project = _ready_project(service, "Full Bid Web Command")
    project_id = project.project.project_id
    queued = service.start_run(project_id)
    source_dir, _ = service.worker_inputs(project_id, queued.run_id)
    BidPipelineRunner(output_dir=tmp_path, stage_overrides=_analysis_overrides(tmp_path)).run(
        source_dir=source_dir,
        run_id=queued.run_id,
        stop_after_stage=ANALYSIS_STOP_STAGE,
    )

    first = service.start_full_bid_generation(project_id)
    second = service.start_full_bid_generation(project_id)

    assert first.run_id == second.run_id == queued.run_id
    assert first.full_bid_generation_active is True
    assert first.status == "QUEUED"
    assert launcher.full_bid_calls == [(project_id, queued.run_id)]


def test_full_bid_command_rejects_cross_project_and_pre_analysis_run(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    service = ProjectLifecycleService(tmp_path, launcher)
    first = _ready_project(service, "Full Bid Boundary A")
    second = _ready_project(service, "Full Bid Boundary B")
    first_run = service.start_run(first.project.project_id)
    service.start_run(second.project.project_id)

    with pytest.raises(ProjectLifecycleConflictError, match="completed tender analysis"):
        service.start_full_bid_generation(first.project.project_id)
    with pytest.raises(LifecycleRunNotFoundError):
        service.run(second.project.project_id, first_run.run_id)


def test_registered_project_resolves_to_current_run_canonical_fulltext(tmp_path: Path) -> None:
    service = ProjectLifecycleService(tmp_path, RecordingLauncher())
    project = _ready_project(service, "Canonical Fulltext Route")
    project_id = project.project.project_id
    run = service.start_run(project_id)
    output_root = tmp_path / "bid_pipeline_outputs" / run.run_id / f"fulltext-{run.run_id}"
    fulltext = output_root / "fulltext"
    review = fulltext / "review"
    for path in (
        fulltext / "technical_fulltext.json",
        fulltext / "technical_fulltext.commit.json",
        fulltext / "hygiene" / "technical_fulltext.cleaned.json",
        fulltext / "audit" / "technical_fulltext.audit.json",
        fulltext / "audit" / "technical_fulltext.scoring_assessment.json",
        review / "technical_fulltext.review_package.json",
        review / "technical_fulltext.human_review.json",
        review / "technical_fulltext.review_finalization.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    state_path = tmp_path / "bid_runs" / run.run_id / "pipeline_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "run_id": run.run_id,
        "routing": {
            "fulltext_output_dir": str(tmp_path / "bid_pipeline_outputs" / run.run_id),
            "fulltext_output_key": f"fulltext-{run.run_id}",
        },
    }), encoding="utf-8")

    artifacts = ReadOnlyProjectResolver(tmp_path).resolve(project_id)

    assert artifacts.project_id == project_id
    assert artifacts.fulltext_json == fulltext / "technical_fulltext.json"
    assert artifacts.commit_json == fulltext / "technical_fulltext.commit.json"


def test_http_full_bid_command_and_overview_use_durable_current_run(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    launcher = RecordingLauncher()
    service = app.state.project_lifecycle_service
    service.launcher = launcher
    project = _ready_project(service, "HTTP Full Bid")
    project_id = project.project.project_id
    run = service.start_run(project_id)
    source_dir, _ = service.worker_inputs(project_id, run.run_id)
    BidPipelineRunner(output_dir=tmp_path, stage_overrides=_analysis_overrides(tmp_path)).run(
        source_dir=source_dir,
        run_id=run.run_id,
        stop_after_stage=ANALYSIS_STOP_STAGE,
    )
    client = TestClient(app)

    started = client.post(f"/api/projects/{project_id}/full-bid-generation", json={})
    repeated = client.post(f"/api/projects/{project_id}/full-bid-generation", json={})
    overview = client.get(f"/api/projects/{project_id}/overview")

    assert started.status_code == repeated.status_code == 202
    assert started.json()["run_id"] == repeated.json()["run_id"] == run.run_id
    assert overview.status_code == 200
    assert overview.json()["current_pipeline_run"]["run_id"] == run.run_id
    assert overview.json()["current_pipeline_run"]["full_bid_generation_active"] is True
    assert launcher.full_bid_calls == [(project_id, run.run_id)]

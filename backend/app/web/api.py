from __future__ import annotations

from pathlib import Path
import re

from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.web.dtos import (
    AnalysisSourceFileDTO,
    FactMaterialSummaryDTO,
    AuditedFulltextDTO,
    ProjectListDTO,
    ProjectCreateDTO,
    ProjectDetailDTO,
    ProjectOverviewDTO,
    ProjectSummaryDTO,
    RequirementDetailDTO,
    RequirementListDTO,
    ReviewWorkspaceDTO,
    ReviewMutationResultDTO,
    ScoringSummaryDTO,
    VersionSummaryDTO,
    SourceFileDTO,
    SourceFileListDTO,
    SourceFileUpdateDTO,
    PipelineRunDTO,
    PipelineRunListDTO,
    PipelineRunStartDTO,
    TenderAnalysisSummaryDTO,
    TenderAnalysisOverviewDTO,
    TenderParametersDTO,
    TenderRiskDTO,
    TenderRisksPreparationDTO,
    TenderScoringRuleDTO,
    TenderScoringRulesDTO,
    ScoringFactPreparationDTO,
    WritingContextItemDTO,
    WritingPreparationDTO,
    WritingPreparationItemDTO,
    ProjectFactMutationResultDTO,
    WritingPlanDTO,
    AIWritingReadinessDTO,
    WritingGenerationDTO,
    WritingGenerationListDTO,
    RevisionOperationDTO,
    ReleaseStatusDTO,
    RequirementDependencyDTO,
    HumanConfirmationListDTO,
    HumanConfirmationRequestDTO,
    HumanConfirmationMutationResultDTO,
)
from app.web.command_dtos import (
    FactConfirmationCommandDTO,
    IssueDecisionCommandDTO,
    MaterialVerificationCommandDTO,
    RiskAcceptanceCommandDTO,
    ProjectFactCommandDTO,
    HumanConfirmationCommandDTO,
    WritingGenerationCommandDTO,
    AIModifyCommandDTO,
)
from app.human_confirmations import (
    HumanConfirmationLineageConflict,
    HumanConfirmationNotFound,
    HumanConfirmationValidationError,
)
from app.project_facts import (
    ProjectFactLineageConflict,
    ProjectFactNotFound,
    ProjectFactValidationError,
)
from app.web.command_service import (
    PreconditionRequiredError,
    ReviewCommandService,
    ReviewCommandValidationError,
    ReviewSourceNotFoundError,
    StaleReviewError,
    WebCommandError,
)
from app.web.project_resolver import ProjectNotFoundError, ReadOnlyProjectResolver
from app.web.query_service import (
    LineageConflictError,
    ProjectReadService,
    RequirementNotFoundError,
    WebProjectionError,
)
from app.web.fulltext_query_service import AuditedFulltextReadService
from app.web.analysis_query_service import (
    AnalysisItemNotFoundError,
    TenderAnalysisReadService,
)
from app.web.writing_preparation_query_service import (
    PreparationItemNotFoundError,
    WritingPreparationReadService,
)
from app.web.writing_context_command_service import WritingContextCommandService
from app.web.writing_plan_query_service import WritingPlanReadService
from app.web.writing_generation_service import (
    WritingGenerationConflict,
    WritingGenerationError,
    WritingGenerationNotFound,
    WritingGenerationService,
    WritingGenerationValidationError,
)
from app.web.revision_operation_service import (
    RevisionOperationConflict,
    RevisionOperationError,
    RevisionOperationService,
)
from app.technical_fulltext_release import (
    TechnicalFulltextReleaseBlocked,
    TechnicalFulltextReleaseError,
    TechnicalFulltextReleaseStale,
)
from app.web.release_service import (
    ReleaseArtifactNotFound,
    TechnicalFulltextReleaseService,
)
from app.web.project_lifecycle_service import (
    LifecycleProjectNotFoundError,
    LifecycleRunNotFoundError,
    LifecycleSourceNotFoundError,
    ProjectLifecycleConflictError,
    ProjectLifecycleError,
    ProjectLifecycleService,
    ProjectLifecycleValidationError,
)
from config import SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES


def create_app(outputs_root: str | Path | None = None) -> FastAPI:
    app = FastAPI(
        title="comlan智标 Web API",
        version="0.2.0",
        description="UI projections and lineage-safe Stage12 human review commands over canonical artifacts.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "PATCH", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "If-None-Match", "If-Match"],
        expose_headers=["ETag"],
    )
    service = ProjectReadService(ReadOnlyProjectResolver(outputs_root))
    command_service = ReviewCommandService(service.resolver)
    fulltext_service = AuditedFulltextReadService(service.resolver, service)
    lifecycle_service = ProjectLifecycleService(service.resolver.outputs_root)
    analysis_service = TenderAnalysisReadService(service.resolver, lifecycle_service, service)
    writing_preparation_service = WritingPreparationReadService(
        service.resolver, lifecycle_service, analysis_service
    )
    writing_context_command_service = WritingContextCommandService(writing_preparation_service)
    writing_plan_service = WritingPlanReadService(
        service.resolver, lifecycle_service, analysis_service, writing_preparation_service
    )
    writing_generation_service = WritingGenerationService(
        service.resolver.outputs_root, writing_plan_service
    )
    revision_operation_service = RevisionOperationService(
        service.resolver.outputs_root,
        service.resolver,
        lifecycle_service,
        writing_plan_service,
    )
    release_service = TechnicalFulltextReleaseService(
        service.resolver,
        analysis_service.required_submission_items,
    )
    app.state.project_read_service = service
    app.state.review_command_service = command_service
    app.state.audited_fulltext_service = fulltext_service
    app.state.project_lifecycle_service = lifecycle_service
    app.state.tender_analysis_service = analysis_service
    app.state.writing_preparation_service = writing_preparation_service
    app.state.writing_context_command_service = writing_context_command_service
    app.state.writing_plan_service = writing_plan_service
    app.state.writing_generation_service = writing_generation_service
    app.state.revision_operation_service = revision_operation_service
    app.state.technical_fulltext_release_service = release_service

    @app.exception_handler(ProjectNotFoundError)
    async def project_not_found(_: Request, exc: ProjectNotFoundError) -> JSONResponse:
        return _error(404, "project_not_found", str(exc))

    @app.exception_handler(LineageConflictError)
    async def lineage_conflict(_: Request, exc: LineageConflictError) -> JSONResponse:
        return _error(409, "lineage_conflict", str(exc))

    @app.exception_handler(RequirementNotFoundError)
    async def requirement_not_found(_: Request, exc: RequirementNotFoundError) -> JSONResponse:
        return _error(404, "requirement_not_found", str(exc))

    @app.exception_handler(AnalysisItemNotFoundError)
    async def analysis_item_not_found(_: Request, exc: AnalysisItemNotFoundError) -> JSONResponse:
        return _error(404, "analysis_item_not_found", str(exc))

    @app.exception_handler(PreparationItemNotFoundError)
    async def preparation_item_not_found(_: Request, exc: PreparationItemNotFoundError) -> JSONResponse:
        return _error(404, "preparation_item_not_found", str(exc))

    @app.exception_handler(ProjectFactNotFound)
    async def project_fact_not_found(_: Request, exc: ProjectFactNotFound) -> JSONResponse:
        return _error(404, "project_fact_not_found", str(exc))

    @app.exception_handler(ProjectFactLineageConflict)
    async def project_fact_stale(_: Request, exc: ProjectFactLineageConflict) -> JSONResponse:
        return _error(409, "stale_writing_context", str(exc))

    @app.exception_handler(ProjectFactValidationError)
    async def project_fact_invalid(_: Request, exc: ProjectFactValidationError) -> JSONResponse:
        return _error(422, "project_fact_invalid", str(exc))

    @app.exception_handler(HumanConfirmationNotFound)
    async def human_confirmation_not_found(_: Request, exc: HumanConfirmationNotFound) -> JSONResponse:
        return _error(404, "human_confirmation_not_found", str(exc))

    @app.exception_handler(HumanConfirmationLineageConflict)
    async def human_confirmation_stale(_: Request, exc: HumanConfirmationLineageConflict) -> JSONResponse:
        return _error(409, "stale_writing_context", str(exc))

    @app.exception_handler(HumanConfirmationValidationError)
    async def human_confirmation_invalid(_: Request, exc: HumanConfirmationValidationError) -> JSONResponse:
        return _error(422, "human_confirmation_invalid", str(exc))

    @app.exception_handler(WebProjectionError)
    async def projection_error(_: Request, exc: WebProjectionError) -> JSONResponse:
        return _error(500, "artifact_projection_failed", str(exc))

    @app.exception_handler(PreconditionRequiredError)
    async def precondition_required(_: Request, exc: PreconditionRequiredError) -> JSONResponse:
        return _error(428, "if_match_required", str(exc))

    @app.exception_handler(StaleReviewError)
    async def stale_review(_: Request, exc: StaleReviewError) -> JSONResponse:
        return _error(409, "stale_review", str(exc))

    @app.exception_handler(ReviewSourceNotFoundError)
    async def review_source_not_found(_: Request, exc: ReviewSourceNotFoundError) -> JSONResponse:
        return _error(404, "review_source_not_found", str(exc))

    @app.exception_handler(ReviewCommandValidationError)
    async def review_command_invalid(_: Request, exc: ReviewCommandValidationError) -> JSONResponse:
        return _error(422, "review_command_invalid", str(exc))

    @app.exception_handler(WebCommandError)
    async def command_failed(_: Request, exc: WebCommandError) -> JSONResponse:
        return _error(500, "review_command_failed", str(exc))

    @app.exception_handler(LifecycleProjectNotFoundError)
    async def lifecycle_project_not_found(_: Request, exc: LifecycleProjectNotFoundError) -> JSONResponse:
        return _error(404, "project_not_found", str(exc))

    @app.exception_handler(LifecycleSourceNotFoundError)
    async def lifecycle_source_not_found(_: Request, exc: LifecycleSourceNotFoundError) -> JSONResponse:
        return _error(404, "source_file_not_found", str(exc))

    @app.exception_handler(LifecycleRunNotFoundError)
    async def lifecycle_run_not_found(_: Request, exc: LifecycleRunNotFoundError) -> JSONResponse:
        return _error(404, "pipeline_run_not_found", str(exc))

    @app.exception_handler(ProjectLifecycleConflictError)
    async def lifecycle_conflict(_: Request, exc: ProjectLifecycleConflictError) -> JSONResponse:
        return _error(409, "project_lifecycle_conflict", str(exc))

    @app.exception_handler(ProjectLifecycleValidationError)
    async def lifecycle_validation(_: Request, exc: ProjectLifecycleValidationError) -> JSONResponse:
        return _error(422, "project_lifecycle_invalid", str(exc))

    @app.exception_handler(ProjectLifecycleError)
    async def lifecycle_failed(_: Request, exc: ProjectLifecycleError) -> JSONResponse:
        return _error(500, "project_lifecycle_failed", str(exc))

    @app.exception_handler(WritingGenerationNotFound)
    async def writing_generation_not_found(_: Request, exc: WritingGenerationNotFound) -> JSONResponse:
        return _error(404, "writing_generation_not_found", str(exc))

    @app.exception_handler(WritingGenerationConflict)
    async def writing_generation_conflict(_: Request, exc: WritingGenerationConflict) -> JSONResponse:
        return _error(409, "stale_writing_context", str(exc))

    @app.exception_handler(WritingGenerationValidationError)
    async def writing_generation_invalid(_: Request, exc: WritingGenerationValidationError) -> JSONResponse:
        return _error(422, "task_not_generatable", str(exc))

    @app.exception_handler(WritingGenerationError)
    async def writing_generation_failed(_: Request, exc: WritingGenerationError) -> JSONResponse:
        return _error(500, "writing_generation_failed", str(exc))

    @app.exception_handler(RevisionOperationConflict)
    async def revision_operation_conflict(_: Request, exc: RevisionOperationConflict) -> JSONResponse:
        return _error(409, "stale_revision", str(exc))

    @app.exception_handler(RevisionOperationError)
    async def revision_operation_failed(_: Request, exc: RevisionOperationError) -> JSONResponse:
        return _error(500, "revision_operation_failed", str(exc))

    @app.exception_handler(TechnicalFulltextReleaseBlocked)
    async def release_blocked(_: Request, exc: TechnicalFulltextReleaseBlocked) -> JSONResponse:
        return _error(
            409,
            "release_blocked",
            str(exc),
            details={"blocking_reasons": exc.blocking_reasons},
        )

    @app.exception_handler(TechnicalFulltextReleaseStale)
    async def release_stale(_: Request, exc: TechnicalFulltextReleaseStale) -> JSONResponse:
        return _error(409, "stale_release", str(exc))

    @app.exception_handler(ReleaseArtifactNotFound)
    async def release_not_found(_: Request, exc: ReleaseArtifactNotFound) -> JSONResponse:
        return _error(404, "release_artifact_not_found", str(exc))

    @app.exception_handler(TechnicalFulltextReleaseError)
    async def release_failed(_: Request, exc: TechnicalFulltextReleaseError) -> JSONResponse:
        return _error(500, "release_failed", str(exc))

    @app.get("/api/projects", response_model=ProjectListDTO)
    def list_projects() -> ProjectListDTO:
        registered = [lifecycle_service.project_summary(item) for item in lifecycle_service.list_manifests()]
        registered_ids = {item.project_id for item in registered}
        legacy = [
            item.model_copy(update={
                "catalog_source": "legacy_artifact",
                "primary_action": "继续审核",
                "primary_action_url": f"/projects/{item.project_id}",
            })
            for item in service.list_projects().projects
            if item.project_id not in registered_ids
        ]
        projects = registered + legacy
        for project in projects:
            _enrich_project_business_fields(project, writing_preparation_service, project.project_id)
        return ProjectListDTO(projects=projects)

    @app.post("/api/projects", response_model=ProjectDetailDTO, status_code=201)
    def create_project(command: ProjectCreateDTO) -> ProjectDetailDTO:
        return lifecycle_service.create_project(command)

    @app.get("/api/projects/{project_id}", response_model=ProjectDetailDTO)
    def project_detail(project_id: str) -> ProjectDetailDTO:
        try:
            return lifecycle_service.project_detail(project_id)
        except LifecycleProjectNotFoundError:
            legacy = service.overview(project_id).project
            return ProjectDetailDTO(
                project=legacy,
                notes="该项目通过既有 Stage artifact 兼容发现，尚未建立 Web Project Manifest。",
                source_files=[],
                runs=[],
            )

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str) -> Response:
        try:
            lifecycle_service.delete_project(project_id)
        except LifecycleProjectNotFoundError:
            artifacts = service.resolver.resolve(project_id)
            lifecycle_service.delete_legacy_project(project_id, artifacts.root)
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/overview", response_model=ProjectOverviewDTO)
    def project_overview(project_id: str) -> ProjectOverviewDTO:
        try:
            result = lifecycle_service.overview(project_id)
        except LifecycleProjectNotFoundError:
            result = service.overview(project_id)
            _enrich_project_business_fields(result.project, writing_preparation_service, project_id)
            return result
        try:
            reviewed = service.overview(project_id)
        except (ProjectNotFoundError, WebProjectionError, LineageConflictError):
            reviewed = None
        if reviewed is not None:
            result.project.current_revision = reviewed.project.current_revision
            result.project.current_reviewed_version = reviewed.project.current_reviewed_version
            result.project.release_status = reviewed.project.release_status
            result.review_progress = reviewed.review_progress
            result.scoring = reviewed.scoring
            result.facts_materials = reviewed.facts_materials
            result.actionability_counts = reviewed.actionability_counts
            result.revision_ready = reviewed.revision_ready
            result.revision_readiness_reason = reviewed.revision_readiness_reason
            result.lineage = reviewed.lineage
            result.scoring_item_count = len(service.scoring(project_id).items)
            result.scoring_available = result.scoring_item_count > 0
        else:
            try:
                result.scoring_item_count = len(analysis_service.scoring(project_id).rules)
                result.scoring_available = result.scoring_item_count > 0
            except (WebProjectionError, AnalysisItemNotFoundError, LifecycleProjectNotFoundError, ProjectNotFoundError):
                pass
        _enrich_project_business_fields(result.project, writing_preparation_service, project_id)
        if result.human_review_ready:
            return result
        try:
            preparation = writing_preparation_service.get(project_id)
        except (WebProjectionError, ProjectFactValidationError):
            return result
        result.facts_materials.missing_project_facts = preparation.summary.pending_fact_count
        result.facts_materials.confirmations_required = preparation.summary.confirmation_count
        result.facts_materials.queries_not_executed = preparation.summary.query_not_executed_count
        result.facts_materials.candidate_materials = preparation.summary.candidate_material_count
        result.facts_materials.verified_materials = preparation.summary.prepared_material_count
        return result

    @app.post("/api/projects/{project_id}/files", response_model=SourceFileDTO, status_code=201)
    async def upload_project_file(project_id: str, request: Request, filename: str) -> SourceFileDTO:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES:
                raise ProjectLifecycleValidationError("source file exceeds the configured single-file limit")
        return lifecycle_service.add_source_file(project_id, filename, request.headers.get("content-type"), bytes(content))

    @app.get("/api/projects/{project_id}/files", response_model=SourceFileListDTO)
    def project_files(project_id: str) -> SourceFileListDTO:
        return lifecycle_service.files(project_id)

    @app.patch("/api/projects/{project_id}/files/{file_id}", response_model=SourceFileDTO)
    def update_project_file(project_id: str, file_id: str, command: SourceFileUpdateDTO) -> SourceFileDTO:
        return lifecycle_service.update_source_role(project_id, file_id, command.role)

    @app.delete("/api/projects/{project_id}/files/{file_id}", status_code=204)
    def delete_project_file(project_id: str, file_id: str) -> Response:
        lifecycle_service.delete_source(project_id, file_id)
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/runs", response_model=PipelineRunDTO, status_code=202)
    def start_project_run(project_id: str, command: PipelineRunStartDTO) -> PipelineRunDTO:
        return lifecycle_service.start_run(project_id, command.lot_name)

    @app.get("/api/projects/{project_id}/runs", response_model=PipelineRunListDTO)
    def project_runs(project_id: str) -> PipelineRunListDTO:
        return lifecycle_service.list_runs(project_id)

    @app.get("/api/projects/{project_id}/runs/{run_id}", response_model=PipelineRunDTO)
    def project_run(project_id: str, run_id: str) -> PipelineRunDTO:
        return lifecycle_service.run(project_id, run_id)

    @app.post("/api/projects/{project_id}/runs/{run_id}/resume", response_model=PipelineRunDTO, status_code=202)
    def resume_project_run(project_id: str, run_id: str) -> PipelineRunDTO:
        return lifecycle_service.resume_run(project_id, run_id)

    @app.post(
        "/api/projects/{project_id}/full-bid-generation",
        response_model=PipelineRunDTO,
        status_code=202,
    )
    def start_full_bid_generation(project_id: str) -> PipelineRunDTO:
        return lifecycle_service.start_full_bid_generation(project_id)

    @app.get("/api/projects/{project_id}/analysis-summary", response_model=TenderAnalysisSummaryDTO)
    def project_analysis_summary(project_id: str) -> TenderAnalysisSummaryDTO:
        try:
            return lifecycle_service.analysis_summary(project_id)
        except LifecycleProjectNotFoundError:
            legacy = service.overview(project_id).project
            return TenderAnalysisSummaryDTO(
                project_id=project_id,
                project_name=legacy.project_name,
                status="历史审核项目",
                source_file_count=0,
                main_tender_count=0,
                attachment_count=0,
                requirement_count=None,
                score_item_count=None,
                technical_score_max=None,
                fact_need_count=None,
                material_need_count=None,
                current_run_id=None,
                analysis_ready=True,
            )

    @app.get("/api/projects/{project_id}/analysis", response_model=TenderAnalysisOverviewDTO)
    def project_analysis(project_id: str) -> TenderAnalysisOverviewDTO:
        return analysis_service.overview(project_id)

    @app.get("/api/projects/{project_id}/analysis/files", response_model=list[AnalysisSourceFileDTO])
    def project_analysis_files(project_id: str) -> list[AnalysisSourceFileDTO]:
        return analysis_service.files(project_id)

    @app.get("/api/projects/{project_id}/analysis/scoring", response_model=TenderScoringRulesDTO)
    def project_analysis_scoring(project_id: str) -> TenderScoringRulesDTO:
        result = analysis_service.scoring(project_id)
        try:
            preparation = writing_preparation_service.get(project_id)
        except (WebProjectionError, ProjectFactValidationError):
            return result
        result.rules = [
            rule.model_copy(update={
                "fact_preparation": [
                    ScoringFactPreparationDTO(
                        trace_id=item.item_id,
                        title=item.title,
                        status=item.status,
                        status_label=item.status_label,
                        target_url=f"/projects/{project_id}/writing-preparation?item={item.item_id}",
                    )
                    for item in preparation.items
                    if item.kind == "project_fact"
                    and any(dep.kind == "score" and dep.trace_id == rule.score_id for dep in item.dependencies)
                ]
            })
            for rule in result.rules
        ]
        return result

    @app.get(
        "/api/projects/{project_id}/analysis/scoring/{score_id}",
        response_model=TenderScoringRuleDTO,
    )
    def project_analysis_scoring_detail(project_id: str, score_id: str) -> TenderScoringRuleDTO:
        result = project_analysis_scoring(project_id)
        match = next((item for item in result.rules if item.score_id == score_id), None)
        if match is None:
            raise AnalysisItemNotFoundError(f"scoring rule is not available: {score_id}")
        return match

    @app.get("/api/projects/{project_id}/analysis/parameters", response_model=TenderParametersDTO)
    def project_analysis_parameters(project_id: str) -> TenderParametersDTO:
        return analysis_service.parameters(project_id)

    @app.get("/api/projects/{project_id}/analysis/risks", response_model=TenderRisksPreparationDTO)
    def project_analysis_risks(project_id: str) -> TenderRisksPreparationDTO:
        return analysis_service.risks_preparation(project_id)

    @app.get("/api/projects/{project_id}/analysis/preparation", response_model=TenderRisksPreparationDTO)
    def project_analysis_preparation(project_id: str) -> TenderRisksPreparationDTO:
        return analysis_service.risks_preparation(project_id)

    @app.get("/api/projects/{project_id}/writing-preparation", response_model=WritingPreparationDTO)
    def project_writing_preparation(project_id: str, response: Response) -> WritingPreparationDTO:
        payload = writing_preparation_service.get(project_id)
        response.headers["ETag"] = _etag(writing_preparation_service.current_token(project_id))
        return payload

    @app.patch(
        "/api/projects/{project_id}/writing-preparation/facts/{fact_id}",
        response_model=ProjectFactMutationResultDTO,
    )
    def confirm_project_fact(
        project_id: str,
        fact_id: str,
        command: ProjectFactCommandDTO,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> ProjectFactMutationResultDTO:
        result = writing_context_command_service.confirm_fact(project_id, fact_id, command, if_match)
        response.headers["ETag"] = _etag(result.version_token)
        return result

    @app.get(
        "/api/projects/{project_id}/writing-preparation/items/{item_id}",
        response_model=WritingPreparationItemDTO,
    )
    def project_writing_preparation_item(project_id: str, item_id: str) -> WritingPreparationItemDTO:
        return writing_preparation_service.item(project_id, item_id)

    @app.get(
        "/api/projects/{project_id}/writing-context",
        response_model=list[WritingContextItemDTO],
    )
    def project_writing_context(project_id: str) -> list[WritingContextItemDTO]:
        return writing_preparation_service.context(project_id)

    @app.get(
        "/api/projects/{project_id}/confirmations",
        response_model=HumanConfirmationListDTO,
    )
    def project_confirmations(project_id: str, response: Response) -> HumanConfirmationListDTO:
        payload = writing_preparation_service.confirmations(project_id)
        response.headers["ETag"] = _etag(payload.version_token)
        return payload

    @app.get(
        "/api/projects/{project_id}/confirmations/{confirmation_id}",
        response_model=HumanConfirmationRequestDTO,
    )
    def project_confirmation(
        project_id: str, confirmation_id: str, response: Response
    ) -> HumanConfirmationRequestDTO:
        payload = writing_preparation_service.confirmation(project_id, confirmation_id)
        response.headers["ETag"] = _etag(writing_preparation_service.current_token(project_id))
        return payload

    @app.patch(
        "/api/projects/{project_id}/confirmations/{confirmation_id}",
        response_model=HumanConfirmationMutationResultDTO,
    )
    def decide_project_confirmation(
        project_id: str,
        confirmation_id: str,
        command: HumanConfirmationCommandDTO,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> HumanConfirmationMutationResultDTO:
        result = writing_context_command_service.decide_confirmation(
            project_id, confirmation_id, command, if_match
        )
        response.headers["ETag"] = _etag(result.version_token)
        return result

    @app.get("/api/projects/{project_id}/writing-plan", response_model=WritingPlanDTO)
    def project_writing_plan(project_id: str) -> WritingPlanDTO:
        return writing_plan_service.get(project_id)

    @app.post(
        "/api/projects/{project_id}/writing-plan/generate",
        response_model=PipelineRunDTO,
        status_code=202,
    )
    def generate_project_writing_plan(project_id: str) -> PipelineRunDTO:
        return lifecycle_service.generate_writing_plan(project_id)

    @app.get(
        "/api/projects/{project_id}/writing-readiness",
        response_model=AIWritingReadinessDTO,
    )
    def project_writing_readiness(project_id: str) -> AIWritingReadinessDTO:
        return writing_plan_service.readiness(project_id)

    @app.post(
        "/api/projects/{project_id}/writing/tasks/{task_id}/generate",
        response_model=WritingGenerationDTO,
        status_code=202,
    )
    def generate_writing_task(
        project_id: str,
        task_id: str,
        command: WritingGenerationCommandDTO,
    ) -> WritingGenerationDTO:
        return writing_generation_service.start(
            project_id, task_id, command.expected_input_fingerprint
        )

    @app.get(
        "/api/projects/{project_id}/writing/generations",
        response_model=WritingGenerationListDTO,
    )
    def writing_generations(project_id: str) -> WritingGenerationListDTO:
        return writing_generation_service.list(project_id)

    @app.get(
        "/api/projects/{project_id}/writing/generations/{generation_id}",
        response_model=WritingGenerationDTO,
    )
    def writing_generation(project_id: str, generation_id: str) -> WritingGenerationDTO:
        return writing_generation_service.get(project_id, generation_id)

    @app.get("/api/projects/{project_id}/review", response_model=ReviewWorkspaceDTO)
    def project_review(project_id: str, response: Response) -> ReviewWorkspaceDTO:
        payload = service.review(project_id)
        response.headers["ETag"] = _etag(command_service.current_token(project_id))
        return payload

    @app.get(
        "/api/projects/{project_id}/review/ai-modify",
        response_model=RevisionOperationDTO | None,
    )
    def current_ai_modify(project_id: str) -> RevisionOperationDTO | None:
        return revision_operation_service.current(project_id)

    @app.post(
        "/api/projects/{project_id}/review/issues/{source_id}/ai-modify",
        response_model=RevisionOperationDTO,
        status_code=202,
    )
    def start_ai_modify(
        project_id: str,
        source_id: str,
        command: AIModifyCommandDTO,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> RevisionOperationDTO:
        return revision_operation_service.start(
            project_id,
            source_type=command.source_type,
            source_id=source_id,
            if_match=if_match,
        )

    @app.patch(
        "/api/projects/{project_id}/review/issues/{source_id}",
        response_model=ReviewMutationResultDTO,
    )
    def decide_review_issue(
        project_id: str,
        source_id: str,
        command: IssueDecisionCommandDTO,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> ReviewMutationResultDTO:
        result = command_service.decide_issue(project_id, source_id, command, if_match)
        response.headers["ETag"] = _etag(result.version_token)
        return result

    @app.patch(
        "/api/projects/{project_id}/review/facts/{source_id}",
        response_model=ReviewMutationResultDTO,
    )
    def confirm_review_fact(
        project_id: str,
        source_id: str,
        command: FactConfirmationCommandDTO,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> ReviewMutationResultDTO:
        result = command_service.confirm_fact(project_id, source_id, command, if_match)
        response.headers["ETag"] = _etag(result.version_token)
        return result

    @app.patch(
        "/api/projects/{project_id}/review/materials/{source_id}",
        response_model=ReviewMutationResultDTO,
    )
    def verify_review_material(
        project_id: str,
        source_id: str,
        command: MaterialVerificationCommandDTO,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> ReviewMutationResultDTO:
        result = command_service.verify_material(project_id, source_id, command, if_match)
        response.headers["ETag"] = _etag(result.version_token)
        return result

    @app.post(
        "/api/projects/{project_id}/review/issues/{source_id}/risk-acceptances",
        response_model=ReviewMutationResultDTO,
    )
    def accept_review_risk(
        project_id: str,
        source_id: str,
        command: RiskAcceptanceCommandDTO,
        response: Response,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> ReviewMutationResultDTO:
        result = command_service.accept_risk(project_id, source_id, command, if_match)
        response.headers["ETag"] = _etag(result.version_token)
        return result

    @app.get("/api/projects/{project_id}/requirements", response_model=RequirementListDTO)
    def project_requirements(project_id: str) -> RequirementListDTO:
        try:
            return service.requirements(project_id)
        except ProjectNotFoundError:
            return lifecycle_service.requirements(project_id)

    @app.get(
        "/api/projects/{project_id}/requirements/{requirement_id}",
        response_model=RequirementDetailDTO,
    )
    def project_requirement(project_id: str, requirement_id: str) -> RequirementDetailDTO:
        try:
            result = service.requirement_detail(project_id, requirement_id)
        except ProjectNotFoundError:
            result = lifecycle_service.requirement_detail(project_id, requirement_id)
        try:
            preparation = writing_preparation_service.get(project_id)
        except (WebProjectionError, ProjectFactValidationError):
            return result
        result.preparation_dependencies = [
                RequirementDependencyDTO(
                    kind="project_fact",
                    title=item.title,
                    status=item.status,
                    status_label=item.status_label,
                    description=item.description,
                    required=item.required,
                    trace_id=item.item_id,
                )
                for item in preparation.items
                if item.kind == "project_fact"
                and any(dep.kind == "requirement" and dep.trace_id == requirement_id for dep in item.dependencies)
            ]
        return result

    @app.get("/api/projects/{project_id}/scoring", response_model=ScoringSummaryDTO)
    def project_scoring(project_id: str) -> ScoringSummaryDTO:
        return service.scoring(project_id)

    @app.get("/api/projects/{project_id}/facts-materials", response_model=FactMaterialSummaryDTO)
    def project_facts_materials(project_id: str) -> FactMaterialSummaryDTO:
        try:
            return service.facts_materials(project_id)
        except ProjectNotFoundError:
            try:
                return writing_preparation_service.facts_materials(project_id)
            except (WebProjectionError, ProjectFactValidationError):
                return analysis_service.facts_materials(project_id)

    @app.get("/api/projects/{project_id}/fulltext", response_model=AuditedFulltextDTO)
    def project_fulltext(project_id: str) -> AuditedFulltextDTO:
        return fulltext_service.get(project_id)

    @app.get("/api/projects/{project_id}/release", response_model=ReleaseStatusDTO)
    def project_release(project_id: str) -> ReleaseStatusDTO:
        return release_service.status(project_id)

    @app.post("/api/projects/{project_id}/release", response_model=ReleaseStatusDTO)
    def publish_project_release(project_id: str) -> ReleaseStatusDTO:
        return release_service.publish(project_id)

    @app.get("/api/projects/{project_id}/release/docx")
    def download_project_docx(project_id: str) -> FileResponse:
        path, filename = release_service.download(project_id, "docx")
        return FileResponse(
            path,
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            filename=filename,
        )

    @app.get("/api/projects/{project_id}/release/pdf")
    def download_project_pdf(project_id: str) -> FileResponse:
        path, filename = release_service.download(project_id, "pdf")
        return FileResponse(path, media_type="application/pdf", filename=filename)

    @app.get("/api/projects/{project_id}/versions", response_model=VersionSummaryDTO)
    def project_versions(project_id: str) -> VersionSummaryDTO:
        return service.versions(project_id)

    return app


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": _public_message(message),
                "details": details,
            }
        },
    )


def _enrich_project_business_fields(project: ProjectSummaryDTO, preparation_service: WritingPreparationReadService, project_id: str) -> None:
    try:
        preparation = preparation_service.get(project_id)
    except (WebProjectionError, AnalysisItemNotFoundError, ProjectFactValidationError, ProjectFactLineageConflict, HumanConfirmationValidationError, HumanConfirmationLineageConflict, ProjectNotFoundError, LifecycleProjectNotFoundError, ProjectLifecycleConflictError):
        return
    confirmed = {
        item.fact_key: item.current_value
        for item in preparation.items
        if item.kind == "project_fact"
        and item.status == "confirmed"
        and item.fact_key
        and item.current_value
    }
    project.supplier_name = confirmed.get("bidder_name")
    project.bid_subject = confirmed.get("project_name_and_code")


app = create_app()


def _etag(token: str) -> str:
    return f'"{token}"'


def _public_message(message: str) -> str:
    return re.sub(r"[A-Za-z]:[\\/][^\n]+", "[server path]", message)

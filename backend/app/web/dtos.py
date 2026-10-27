from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WebDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionRefDTO(WebDTO):
    version_id: str
    label: str
    content_sha256: str | None = None
    artifact_sha256: str | None = None
    status: str


class ProjectSummaryDTO(WebDTO):
    project_id: str
    project_name: str
    source_document_name: str | None = None
    current_revision: VersionRefDTO
    current_reviewed_version: VersionRefDTO
    workflow_status: str
    release_status: str
    next_action: str
    blocking_count: int
    pending_decision_count: int
    pending_verification_count: int
    procuring_entity: str | None = None
    procurement_number: str | None = None
    deadline: str | None = None
    updated_at: str | None = None
    catalog_source: Literal["registry", "legacy_artifact"] = "legacy_artifact"
    primary_action: str = "打开项目"
    primary_action_url: str | None = None
    bid_subject: str | None = None
    supplier_name: str | None = None


class ProjectListDTO(WebDTO):
    projects: list[ProjectSummaryDTO]


SourceFileRole = Literal[
    "main_tender",
    "technical_spec",
    "scoring_document",
    "attachment",
    "supplement",
    "unknown",
]


class ProjectCreateDTO(WebDTO):
    project_name: str = Field(min_length=1, max_length=200)
    procuring_entity: str | None = Field(default=None, max_length=200)
    procurement_number: str | None = Field(default=None, max_length=120)
    deadline: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)


class SourceFileDTO(WebDTO):
    file_id: str
    display_name: str
    extension: str
    mime_type: str
    size: int
    uploaded_at: str
    role: SourceFileRole
    role_confidence: float
    role_confirmed: bool
    status: str
    sha256: str


class SourceFileListDTO(WebDTO):
    project_id: str
    files: list[SourceFileDTO]
    allowed_extensions: list[str]
    max_file_bytes: int
    can_start: bool
    start_blockers: list[str]


class SourceFileUpdateDTO(WebDTO):
    role: SourceFileRole


class PipelineRunStartDTO(WebDTO):
    lot_name: str | None = Field(default=None, max_length=200)


class PipelineRunErrorDTO(WebDTO):
    category: Literal["input_error", "system_error"]
    user_message: str
    technical_detail: str | None = None


class PipelineStageDTO(WebDTO):
    stage_id: str
    label: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    attempt_count: int = 0
    error: PipelineRunErrorDTO | None = None


class PipelineRunDTO(WebDTO):
    run_id: str
    project_id: str
    status: str
    status_label: str
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    current_phase: str
    current_stage: str | None = None
    current_step: str | None = None
    completed_steps: int
    total_steps: int
    stages: list[PipelineStageDTO]
    last_successful_checkpoint: str | None = None
    error: PipelineRunErrorDTO | None = None
    resume_available: bool
    analysis_ready: bool
    writing_plan_ready: bool = False
    full_bid_generation_active: bool = False
    can_start_full_bid_generation: bool = False
    fulltext_ready: bool = False
    machine_audit_ready: bool = False
    human_review_ready: bool = False


class PipelineRunListDTO(WebDTO):
    project_id: str
    runs: list[PipelineRunDTO]


class ProjectDetailDTO(WebDTO):
    project: ProjectSummaryDTO
    notes: str | None = None
    source_files: list[SourceFileDTO]
    runs: list[PipelineRunDTO]


class TenderAnalysisSummaryDTO(WebDTO):
    project_id: str
    project_name: str
    status: str
    source_file_count: int
    main_tender_count: int
    attachment_count: int
    requirement_count: int | None = None
    score_item_count: int | None = None
    technical_score_max: float | None = None
    fact_need_count: int | None = None
    material_need_count: int | None = None
    current_run_id: str | None = None
    analysis_ready: bool


class AnalysisLineageDTO(WebDTO):
    source_mode: Literal["pipeline_analysis", "legacy_response_outline"]
    run_id: str | None = None
    completed_at: str | None = None
    analysis_ready: bool
    post_writing_available: bool = False


class AnalysisSourceFileDTO(WebDTO):
    file_id: str
    display_name: str
    role: str
    role_label: str
    parse_status: str
    size: int | None = None
    source_reference: SourceReferenceDTO | None = None


class TenderProjectInfoItemDTO(WebDTO):
    key: str
    label: str
    value: str
    source_reference: SourceReferenceDTO | None = None


class RequirementCategoryCountDTO(WebDTO):
    key: str
    label: str
    count: int


class AnalysisPriorityItemDTO(WebDTO):
    trace_id: str
    title: str
    description: str
    action_kind: str
    action_label: str
    priority: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    source_references: list[SourceReferenceDTO] = Field(default_factory=list)


class TenderAnalysisOverviewDTO(WebDTO):
    project_id: str
    project_name: str
    status: str
    lineage: AnalysisLineageDTO
    source_file_count: int
    project_information: list[TenderProjectInfoItemDTO] = Field(default_factory=list)
    requirement_count: int
    requirement_categories: list[RequirementCategoryCountDTO] = Field(default_factory=list)
    score_rule_count: int
    available_score_total: float | None = None
    parameter_table_count: int
    parameter_row_count: int
    fact_need_count: int
    material_need_count: int
    confirmation_count: int
    priority_items: list[AnalysisPriorityItemDTO] = Field(default_factory=list)


class ScoringFactPreparationDTO(WebDTO):
    trace_id: str
    title: str
    status: str
    status_label: str
    target_url: str


class TenderScoringRuleDTO(WebDTO):
    score_id: str
    title: str
    criteria_text: str
    available_score: float | None = None
    score_text: str
    score_group: str | None = None
    preparation_text: str | None = None
    response_suggestions: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    fact_needs: list[str] = Field(default_factory=list)
    fact_preparation: list[ScoringFactPreparationDTO] = Field(default_factory=list)
    material_needs: list[RequirementDependencyDTO] = Field(default_factory=list)
    source_reference: SourceReferenceDTO


class TenderScoringRulesDTO(WebDTO):
    project_id: str
    lineage: AnalysisLineageDTO
    rule_count: int
    available_score_total: float | None = None
    rules: list[TenderScoringRuleDTO]
    machine_assessment_included: Literal[False] = False


class TenderTableCellDTO(WebDTO):
    text: str
    row: int
    column: int
    rowspan: int = 1
    colspan: int = 1


class TenderParameterTableDTO(WebDTO):
    table_id: str
    title: str
    requirement_id: str | None = None
    column_count: int
    row_count: int
    rows: list[list[TenderTableCellDTO]] = Field(default_factory=list)
    raw_text: str | None = None
    mandatory_marker: str | None = None
    source_reference: SourceReferenceDTO


class TenderParametersDTO(WebDTO):
    project_id: str
    lineage: AnalysisLineageDTO
    table_count: int
    row_count: int
    tables: list[TenderParameterTableDTO]


class PreparationNeedDTO(WebDTO):
    trace_id: str
    kind: Literal["project_fact", "fact_confirmation", "material", "writing_response"]
    title: str
    description: str
    status: str
    status_label: str
    required: bool = True
    priority: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    score_item_ids: list[str] = Field(default_factory=list)
    source_references: list[SourceReferenceDTO] = Field(default_factory=list)


class TenderRiskDTO(WebDTO):
    trace_id: str
    category: str
    category_label: str
    title: str
    description: str
    action_priority: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    source_references: list[SourceReferenceDTO] = Field(default_factory=list)


class TenderRisksPreparationDTO(WebDTO):
    project_id: str
    lineage: AnalysisLineageDTO
    risks: list[TenderRiskDTO]
    facts: list[PreparationNeedDTO]
    materials: list[PreparationNeedDTO]
    confirmations: list[PreparationNeedDTO]
    writing_responses: list[PreparationNeedDTO]
    blocking_semantics_available: bool = False


class WorkflowStepDTO(WebDTO):
    key: str
    label: str
    status: Literal["completed", "active", "pending", "blocked", "not_started"]
    detail: str | None = None


class ScoreRangeDTO(WebDTO):
    minimum: float
    maximum: float


class ScoringHeadlineDTO(WebDTO):
    technical_score_max: float
    confirmed: float
    estimated_range: ScoreRangeDTO
    pending_exposure: float
    potential_gain: ScoreRangeDTO
    single_total_score_permitted: bool = False


class ReviewProgressDTO(WebDTO):
    issue_decisions_total: int
    issue_decisions_completed: int
    fact_confirmations_total: int
    fact_confirmations_completed: int
    material_verifications_total: int
    material_verifications_completed: int


class FactMaterialHeadlineDTO(WebDTO):
    missing_project_facts: int
    confirmations_required: int
    queries_not_executed: int
    candidate_materials: int
    verified_materials: int


class LineageInfoDTO(WebDTO):
    audited_content_sha256: str
    audit_sha256: str
    scoring_sha256: str
    review_package_id: str
    human_review_id: str | None = None
    finalization_id: str | None = None


class ProjectOverviewDTO(WebDTO):
    project: ProjectSummaryDTO
    workflow: list[WorkflowStepDTO]
    review_progress: ReviewProgressDTO
    scoring: ScoringHeadlineDTO
    facts_materials: FactMaterialHeadlineDTO
    actionability_counts: dict[str, int]
    revision_ready: bool
    revision_readiness_reason: str
    lineage: LineageInfoDTO
    current_pipeline_run: PipelineRunDTO | None = None
    can_start_full_bid_generation: bool = False
    can_resume: bool = False
    fulltext_ready: bool = False
    machine_audit_ready: bool = False
    human_review_ready: bool = False
    scoring_item_count: int = 0
    scoring_available: bool = False


class AuditedFulltextVersionDTO(WebDTO):
    display_name: str
    revision: str
    audited_content_sha256: str
    audited_artifact_sha256: str
    audit_sha256: str
    review_package_id: str
    human_review_id: str
    generated_at: str | None = None


class AuditedFulltextStatusDTO(WebDTO):
    release_ready: bool
    audit_result: str
    review_status: str
    stage13_ready: bool


class FulltextTraceRefsDTO(WebDTO):
    requirement_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    score_item_ids: list[str] = Field(default_factory=list)
    review_item_ids: list[str] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    material_refs: list[str] = Field(default_factory=list)


class FulltextParagraphDTO(WebDTO):
    paragraph_id: str
    paragraph_index: int
    document_order: int
    text: str
    text_sha256: str
    content_type: Literal["paragraph", "table", "list"]
    placeholder_ids: list[str] = Field(default_factory=list)
    trace: FulltextTraceRefsDTO


class FulltextSectionDTO(WebDTO):
    section_id: str
    heading: str
    order: int
    block_type: str
    paragraphs: list[FulltextParagraphDTO]
    trace: FulltextTraceRefsDTO


class FulltextChapterDTO(WebDTO):
    chapter_id: str
    title: str
    order: int
    sections: list[FulltextSectionDTO]


class FulltextRequirementTraceDTO(WebDTO):
    trace_id: str
    title: str
    requirement_text: str
    response_status: str
    response_locations: list[ReviewLocationDTO] = Field(default_factory=list)


class FulltextFindingTraceDTO(WebDTO):
    trace_id: str
    title: str
    summary: str
    rationale: str | None = None
    severity: str | None = None
    release_blocking: bool = False
    recommendation: str | None = None
    locations: list[ReviewLocationDTO] = Field(default_factory=list)


class FulltextReviewTraceDTO(WebDTO):
    trace_id: str
    title: str
    actionability: ActionabilityKey
    current_decision: str
    risk_accepted: bool = False
    response_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    recommended_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    material_refs: list[str] = Field(default_factory=list)
    revision_candidate: bool = False


class FulltextScoreTraceDTO(WebDTO):
    trace_id: str
    title: str
    criteria_text: str
    max_score: float
    assessment_status: str
    score_range: ScoreRangeDTO
    pending_evidence: list[RequirementPendingEvidenceDTO] = Field(default_factory=list)
    response_locations: list[ReviewLocationDTO] = Field(default_factory=list)


class FulltextDependencyTraceDTO(WebDTO):
    trace_id: str
    kind: str
    title: str
    status: str
    status_label: str


class AuditedFulltextTraceIndexDTO(WebDTO):
    requirements: list[FulltextRequirementTraceDTO] = Field(default_factory=list)
    findings: list[FulltextFindingTraceDTO] = Field(default_factory=list)
    reviews: list[FulltextReviewTraceDTO] = Field(default_factory=list)
    scores: list[FulltextScoreTraceDTO] = Field(default_factory=list)
    dependencies: list[FulltextDependencyTraceDTO] = Field(default_factory=list)


class AuditedFulltextDTO(WebDTO):
    project_id: str
    document_id: str
    version: AuditedFulltextVersionDTO
    status: AuditedFulltextStatusDTO
    chapters: list[FulltextChapterDTO]
    trace_index: AuditedFulltextTraceIndexDTO


class ReviewLocationDTO(WebDTO):
    chapter_id: str | None = None
    chapter_title: str | None = None
    section_id: str | None = None
    section_heading: str | None = None
    paragraph_id: str | None = None
    paragraph_index: int | None = None
    excerpt: str | None = None
    response_role: str | None = None
    technical_ref: str | None = None


ActionabilityKey = Literal["must_decide", "verification", "no_action", "informational"]


class ReviewScoreImpactDTO(WebDTO):
    title: str
    max_score: float
    assessment_status: str
    score_range: ScoreRangeDTO
    trace_id: str


class ReviewItemDTO(WebDTO):
    title: str
    actionability: ActionabilityKey
    severity: str
    release_blocking: bool
    requirement_text: str | None = None
    current_response: str | None = None
    machine_issue: str
    machine_rationale: str | None = None
    recommendation: str | None = None
    response_location: ReviewLocationDTO | None = None
    revision_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    required_materials: list[str] = Field(default_factory=list)
    scoring_impacts: list[ReviewScoreImpactDTO] = Field(default_factory=list)
    current_decision: str | None = None
    rejection_reason: str | None = None
    revision_instruction: str | None = None
    optional_comment: str | None = None
    use_machine_location: bool = True
    location_override: dict[str, Any] | None = None
    location_override_reason: str | None = None
    risk_acceptance_ref: str | None = None
    updated_at: str | None = None
    change_history: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str
    source_type: str
    confidence: str | None = None
    machine_revision_available: bool = False
    revision_candidate_ready: bool = False


class ReviewGroupDTO(WebDTO):
    key: ActionabilityKey
    label: str
    description: str
    items: list[ReviewItemDTO]


class FactConfirmationDTO(WebDTO):
    title: str
    description: str | None = None
    source_type: Literal["FACT", "FCA"]
    trace_id: str
    confirmation_status: str
    confirmed_value: Any | None = None
    source_description: str | None = None
    confirmation_basis: str | None = None
    confirmation_note: str | None = None
    rejection_reason: str | None = None
    required: bool = True
    related_requirement_ids: list[str] = Field(default_factory=list)
    response_location: ReviewLocationDTO | None = None
    response_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    affected_location_count: int = 0
    updated_at: str | None = None
    change_history: list[dict[str, Any]] = Field(default_factory=list)


class MaterialVerificationDTO(WebDTO):
    title: str
    description: str | None = None
    source_type: Literal["EVIDENCE_QUERY", "EVIDENCE"]
    trace_id: str
    upstream_status: str
    upstream_status_label: str
    verification_status: str
    can_verify: bool
    material_name: str | None = None
    subject_match: bool | None = None
    validity_date: str | None = None
    verification_note: str | None = None
    candidate_reference: str | None = None
    file_reference: str | None = None
    required: bool = True
    related_requirement_ids: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    change_history: list[dict[str, Any]] = Field(default_factory=list)


class ReviewWorkspaceDTO(WebDTO):
    project: ProjectSummaryDTO
    reviewed_version: VersionRefDTO
    progress: ReviewProgressDTO
    actionability_counts: dict[str, int]
    groups: list[ReviewGroupDTO]
    fact_confirmations: list[FactConfirmationDTO] = Field(default_factory=list)
    material_verifications: list[MaterialVerificationDTO] = Field(default_factory=list)
    lineage: LineageInfoDTO
    read_only: bool = False


class ReviewMutationResultDTO(WebDTO):
    updated_kind: Literal["issue", "fact", "material", "risk"]
    updated_id: str
    version_token: str
    review: ReviewWorkspaceDTO
    overview: ProjectOverviewDTO


class SourceReferenceDTO(WebDTO):
    file_name: str | None = None
    clause_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    table_name: str | None = None
    table_range: str | None = None
    row_or_cell: str | None = None
    source_kind: Literal["paragraph", "table"]


class PreparationDependencyDTO(WebDTO):
    kind: Literal["requirement", "score", "writing"]
    trace_id: str
    title: str
    description: str | None = None
    target_url: str | None = None


class WritingContextItemDTO(WebDTO):
    context_id: str
    title: str
    value: str | None = None
    availability: Literal["available", "pending", "placeholder_required", "unavailable"]
    availability_label: str
    source_policy: str
    source_description: str | None = None
    technical_ref: str | None = None
    context_kind: Literal["project_fact", "human_confirmation", "material"] = "project_fact"
    stage9_consumable: bool = True
    stage9_policy: str | None = None


class FactValueFieldDTO(WebDTO):
    key: str
    label: str
    field_type: Literal["text", "number"]
    required: bool = True
    minimum: float | None = None


class WritingPreparationItemDTO(WebDTO):
    item_id: str
    kind: Literal["project_fact", "human_confirmation", "material", "evidence_query"]
    fact_key: str | None = None
    title: str
    description: str
    status: str
    status_label: str
    required: bool = True
    priority: str | None = None
    current_value: str | None = None
    current_raw_value: Any | None = None
    value_type: Literal["text", "number", "structured"] | None = None
    value_fields: list[FactValueFieldDTO] = Field(default_factory=list)
    candidate_values: list[str] = Field(default_factory=list)
    dependencies: list[PreparationDependencyDTO] = Field(default_factory=list)
    source_references: list[SourceReferenceDTO] = Field(default_factory=list)
    writing_context: WritingContextItemDTO | None = None
    technical_refs: list[str] = Field(default_factory=list)
    can_confirm: bool = False
    can_defer: bool = False
    confirmation_response_kind: Literal["boolean", "selection", "text"] | None = None
    stage9_consumable: bool = True


class WritingPreparationSummaryDTO(WebDTO):
    confirmed_fact_count: int
    pending_fact_count: int
    confirmation_count: int
    query_not_executed_count: int
    candidate_material_count: int
    prepared_material_count: int
    placeholder_count: int
    analysis_ready: bool
    planning_accessible: bool
    gate_source: str
    guidance: str


class WritingPreparationDTO(WebDTO):
    project_id: str
    project_name: str
    version_token: str
    lineage: AnalysisLineageDTO
    summary: WritingPreparationSummaryDTO
    items: list[WritingPreparationItemDTO]
    writing_context: list[WritingContextItemDTO]
    read_only: bool = True
    mutation_available: bool = False
    mutation_unavailable_reason: str | None = None


class ProjectFactMutationResultDTO(WebDTO):
    updated_fact_id: str
    version_token: str
    writing_preparation: WritingPreparationDTO
    writing_context: list[WritingContextItemDTO]


class HumanConfirmationRequestDTO(WebDTO):
    confirmation_id: str
    request_kind: Literal["project_fact", "business_choice", "material_availability"]
    managed_by: Literal["project_fact_service", "human_confirmation_service", "material_workflow"]
    question: str
    reason: str
    category: str
    priority: str
    response_kind: Literal["boolean", "selection", "text"]
    candidate_values: list[str] = Field(default_factory=list)
    current_value: Any | None = None
    status: Literal["pending", "confirmed"]
    status_label: str
    answer: Any | None = None
    source_description: str | None = None
    confirmation_basis: str | None = None
    note: str | None = None
    writing_effect: str
    linked_fact_id: str | None = None
    requirement_refs: list[str] = Field(default_factory=list)
    score_refs: list[str] = Field(default_factory=list)
    writing_task_refs: list[str] = Field(default_factory=list)
    writing_section_refs: list[str] = Field(default_factory=list)
    source_references: list[SourceReferenceDTO] = Field(default_factory=list)
    can_decide: bool
    stage9_consumable: bool = True


class HumanConfirmationListDTO(WebDTO):
    project_id: str
    project_name: str
    version_token: str
    lineage: AnalysisLineageDTO
    pending_count: int
    confirmed_count: int
    project_fact_routed_count: int
    material_routed_count: int
    confirmations: list[HumanConfirmationRequestDTO] = Field(default_factory=list)


class HumanConfirmationMutationResultDTO(WebDTO):
    updated_confirmation_id: str
    version_token: str
    confirmation: HumanConfirmationRequestDTO
    writing_preparation: WritingPreparationDTO
    writing_context: list[WritingContextItemDTO]


class WritingPlanLineageDTO(WebDTO):
    source_mode: Literal["pipeline_planning", "legacy_planning", "not_generated"]
    analysis_run_id: str | None = None
    current_analysis_run_id: str | None = None
    planning_key: str | None = None
    generated_at: str | None = None
    response_outline_sha256: str | None = None
    writing_outline_sha256: str | None = None
    writing_tasks_sha256: str | None = None
    task_information_sha256: str | None = None
    is_current: bool = True
    lineage_valid: bool = True
    stale_reason: str | None = None


class WritingPlanFactDependencyDTO(WebDTO):
    fact_id: str
    fact_key: str
    title: str
    required: bool
    status: str
    status_label: str
    availability: Literal["available", "placeholder_required", "pending", "unavailable"]
    value: str | None = None
    source_policy: str
    target_url: str | None = None


class WritingPlanMaterialDependencyDTO(WebDTO):
    dependency_id: str
    evidence_type: str
    title: str
    required: bool
    status: str
    status_label: str
    source_policy: str
    target_url: str | None = None


class WritingPlanReferenceDTO(WebDTO):
    trace_id: str
    title: str
    description: str | None = None
    max_score: float | None = None
    mapping_role: str | None = None
    target_url: str


class WritingTaskDTO(WebDTO):
    task_id: str
    chapter_id: str
    section_id: str
    title: str
    objective: str
    instruction_points: list[str] = Field(default_factory=list)
    requirement_refs: list[WritingPlanReferenceDTO] = Field(default_factory=list)
    score_refs: list[WritingPlanReferenceDTO] = Field(default_factory=list)
    fact_dependencies: list[WritingPlanFactDependencyDTO] = Field(default_factory=list)
    material_dependencies: list[WritingPlanMaterialDependencyDTO] = Field(default_factory=list)
    human_confirmation_refs: list[WritingContextItemDTO] = Field(default_factory=list)
    placeholder_refs: list[str] = Field(default_factory=list)
    prohibited_inference: list[str] = Field(default_factory=list)
    readiness: str
    readiness_label: str
    body_generated: bool = False


class WritingSectionDTO(WebDTO):
    section_id: str
    chapter_id: str
    title: str
    path: list[str] = Field(default_factory=list)
    order: int
    depth: int
    purpose: str
    task_ids: list[str] = Field(default_factory=list)
    requirement_count: int = 0
    score_count: int = 0
    pending_fact_count: int = 0
    pending_material_count: int = 0
    preparation_label: str


class WritingChapterDTO(WebDTO):
    chapter_id: str
    title: str
    order: int
    purpose: str
    sections: list[WritingSectionDTO] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    requirement_count: int = 0
    score_count: int = 0
    pending_fact_count: int = 0
    pending_material_count: int = 0


class WritingPlanCoverageDTO(WebDTO):
    trace_id: str
    title: str
    section_ids: list[str] = Field(default_factory=list)
    status: Literal["planned", "multiple_sections", "unassigned"]
    max_score: float | None = None
    target_url: str


class WritingPlanSummaryDTO(WebDTO):
    chapter_count: int = 0
    section_count: int = 0
    task_count: int = 0
    requirement_count: int = 0
    score_count: int = 0
    confirmed_fact_count: int = 0
    placeholder_fact_count: int = 0
    query_not_executed_count: int = 0
    pending_human_confirmation_count: int = 0
    stage9_unconsumable_confirmation_count: int = 0
    unassigned_requirement_count: int = 0


class WritingPlanDTO(WebDTO):
    project_id: str
    project_name: str
    available: bool
    status: Literal["ready", "stale", "not_generated", "invalid"]
    status_label: str
    message: str
    lineage: WritingPlanLineageDTO
    summary: WritingPlanSummaryDTO
    chapters: list[WritingChapterDTO] = Field(default_factory=list)
    tasks: list[WritingTaskDTO] = Field(default_factory=list)
    requirement_coverage: list[WritingPlanCoverageDTO] = Field(default_factory=list)
    score_coverage: list[WritingPlanCoverageDTO] = Field(default_factory=list)
    writing_context: list[WritingContextItemDTO] = Field(default_factory=list)
    generation_available: bool = False
    generation_reason: str | None = None


class AIWritingReadinessDTO(WebDTO):
    project_id: str
    project_name: str
    writing_plan_ready: bool
    project_facts_connected: bool
    human_confirmations_connected: bool
    pending_confirmation_count: int = 0
    confirmed_business_choice_count: int = 0
    task_count: int = 0
    task_ready_count: int = 0
    task_blocked_count: int = 0
    placeholder_task_count: int = 0
    stage9_contract_ready: bool
    pending_policy: Literal["placeholder_required", "generation_blocked_for_task"]
    input_fingerprint: str | None = None
    message: str


class WritingGenerationDTO(WebDTO):
    generation_id: str
    project_id: str
    task_id: str
    chapter_id: str
    section_id: str
    input_fingerprint: str
    task_fingerprint: str
    status: Literal["queued", "running", "validating", "completed", "failed", "validation_failed"]
    status_label: str
    current_step: str | None = None
    created_at: str
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    draft_markdown: str | None = None
    placeholder_ids: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    generated_from_current_context: bool = True
    model_profile: str | None = None


class WritingGenerationListDTO(WebDTO):
    project_id: str
    generations: list[WritingGenerationDTO] = Field(default_factory=list)


class RevisionOperationDTO(WebDTO):
    operation_id: str
    project_id: str
    source_type: str
    source_id: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["rewriting", "reviewing", "publishing"]
    status_label: str
    created_at: str
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ReleaseArtifactDTO(WebDTO):
    filename: str
    sha256: str
    size_bytes: int


class RequiredSubmissionItemDTO(WebDTO):
    item_key: str
    display_title: str
    status: Literal["pending", "prepared"]
    description: str
    submission_kind: Literal["form", "external_upload"]
    current_record: str | None = None


class ReleaseStatusDTO(WebDTO):
    project_id: str
    current_output_key: str
    releaseable: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    status: Literal["blocked", "ready", "published"]
    generated_at: str | None = None
    docx: ReleaseArtifactDTO | None = None
    pdf: ReleaseArtifactDTO | None = None
    required_submission_items: list[RequiredSubmissionItemDTO] = Field(default_factory=list)


class RequirementReviewSummaryDTO(WebDTO):
    actionability: ActionabilityKey | None = None
    pending_count: int = 0
    completed_count: int = 0
    trace_ids: list[str] = Field(default_factory=list)


class RequirementResponseDTO(WebDTO):
    title: str
    requirement_text: str
    mandatory: bool
    category: str | None = None
    source_document: str | None = None
    source_location: str | None = None
    source_reference: SourceReferenceDTO
    response_status: str
    current_response: str | None = None
    primary_response_location: ReviewLocationDTO | None = None
    response_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    related_finding_count: int
    related_score_count: int
    review: RequirementReviewSummaryDTO
    fact_dependency_count: int = 0
    material_dependency_count: int = 0
    query_not_executed_count: int = 0
    trace_id: str


class RequirementListDTO(WebDTO):
    project_id: str
    reviewed_version: VersionRefDTO
    summary: dict[str, int]
    requirements: list[RequirementResponseDTO]


class RequirementFindingDTO(WebDTO):
    title: str
    summary: str
    rationale: str | None = None
    severity: str | None = None
    release_blocking: bool = False
    verdict: str | None = None
    recommendation: str | None = None
    requires_human_review: bool = False
    trace_id: str
    source_type: str


class RequirementPendingEvidenceDTO(WebDTO):
    evidence_type: str
    status: str
    status_label: str
    reason: str | None = None


class RequirementScoreImpactDTO(WebDTO):
    title: str
    criteria_text: str
    max_score: float
    assessment_status: str
    score_range: ScoreRangeDTO
    pending_conditions: list[str] = Field(default_factory=list)
    pending_evidence: list[RequirementPendingEvidenceDTO] = Field(default_factory=list)
    potential_gain: ScoreRangeDTO
    trace_id: str


class RequirementDependencyDTO(WebDTO):
    kind: Literal["project_fact", "fact_confirmation", "evidence_requirement", "evidence_query", "material"]
    title: str
    status: str
    status_label: str
    description: str | None = None
    required: bool = True
    trace_id: str


class RequirementReviewStateDTO(WebDTO):
    title: str
    actionability: ActionabilityKey
    severity: str
    release_blocking: bool
    machine_recommendation: str | None = None
    current_decision: str | None = None
    revision_instruction: str | None = None
    risk_acceptance_ref: str | None = None
    revision_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    trace_id: str


class RequirementDetailDTO(WebDTO):
    project_id: str
    reviewed_version: VersionRefDTO
    requirement: RequirementResponseDTO
    findings: list[RequirementFindingDTO] = Field(default_factory=list)
    scoring_impacts: list[RequirementScoreImpactDTO] = Field(default_factory=list)
    fact_dependencies: list[RequirementDependencyDTO] = Field(default_factory=list)
    material_dependencies: list[RequirementDependencyDTO] = Field(default_factory=list)
    preparation_dependencies: list[RequirementDependencyDTO] = Field(default_factory=list)
    review_states: list[RequirementReviewStateDTO] = Field(default_factory=list)
    read_only: bool = True


class ScoringItemDTO(WebDTO):
    title: str
    criteria_text: str
    max_score: float
    assessment_status: str
    confirmed_score: float | None = None
    estimated_score: float | None = None
    score_range: ScoreRangeDTO
    satisfied_conditions: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    pending_conditions: list[str] = Field(default_factory=list)
    deduction_reasons: list[str] = Field(default_factory=list)
    potential_gain: ScoreRangeDTO
    response_locations: list[ReviewLocationDTO] = Field(default_factory=list)
    trace_id: str


class ScoringSummaryDTO(WebDTO):
    project_id: str
    reviewed_version: VersionRefDTO
    headline: ScoringHeadlineDTO
    item_counts: dict[str, int]
    items: list[ScoringItemDTO]


class FactMaterialItemDTO(WebDTO):
    category: str
    title: str
    status: str
    status_label: str
    description: str
    required: bool
    related_task_count: int
    trace_id: str
    current_value: str | None = None
    related_locations: list[str] = Field(default_factory=list)
    requirement_context: list[str] = Field(default_factory=list)
    source_label: str | None = None
    action_label: str
    action_url: str
    semantic_resolved: bool = True


class FactMaterialSummaryDTO(WebDTO):
    project_id: str
    reviewed_version: VersionRefDTO
    headline: FactMaterialHeadlineDTO
    status_legend: dict[str, str]
    status_counts: dict[str, int]
    items: list[FactMaterialItemDTO]


class VersionLineageEntryDTO(WebDTO):
    kind: str
    label: str
    status: str
    generated_at: str | None = None
    content_sha256: str | None = None
    artifact_sha256: str | None = None
    internal_id: str | None = None
    current: bool = False


class VersionSummaryDTO(WebDTO):
    project_id: str
    current_revision: VersionRefDTO
    current_reviewed_version: VersionRefDTO
    entries: list[VersionLineageEntryDTO]
    revision_round_contract_available: bool = False


class ApiErrorDetailDTO(WebDTO):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ApiErrorDTO(WebDTO):
    error: ApiErrorDetailDTO

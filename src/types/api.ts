export type WorkflowState = "completed" | "active" | "pending" | "blocked" | "not_started";
export type ActionabilityKey = "must_decide" | "verification" | "no_action" | "informational";

export interface VersionRefDTO {
  version_id: string;
  label: string;
  content_sha256: string | null;
  artifact_sha256: string | null;
  status: string;
}

export interface ProjectSummaryDTO {
  project_id: string;
  project_name: string;
  source_document_name: string | null;
  current_revision: VersionRefDTO;
  current_reviewed_version: VersionRefDTO;
  workflow_status: string;
  release_status: string;
  next_action: string;
  blocking_count: number;
  pending_decision_count: number;
  pending_verification_count: number;
  procuring_entity: string | null;
  procurement_number: string | null;
  deadline: string | null;
  updated_at: string | null;
  catalog_source: "registry" | "legacy_artifact";
  primary_action: string;
  primary_action_url: string | null;
  bid_subject: string | null;
  supplier_name: string | null;
}

export interface ProjectListDTO {
  projects: ProjectSummaryDTO[];
}

export type SourceFileRole = "main_tender" | "technical_spec" | "scoring_document" | "attachment" | "supplement" | "unknown";

export interface ProjectCreateDTO {
  project_name: string;
  procuring_entity?: string | null;
  procurement_number?: string | null;
  deadline?: string | null;
  notes?: string | null;
}

export interface SourceFileDTO {
  file_id: string;
  display_name: string;
  extension: string;
  mime_type: string;
  size: number;
  uploaded_at: string;
  role: SourceFileRole;
  role_confidence: number;
  role_confirmed: boolean;
  status: string;
  sha256: string;
}

export interface SourceFileListDTO {
  project_id: string;
  files: SourceFileDTO[];
  allowed_extensions: string[];
  max_file_bytes: number;
  can_start: boolean;
  start_blockers: string[];
}

export interface PipelineRunErrorDTO {
  category: "input_error" | "system_error";
  user_message: string;
  technical_detail: string | null;
}

export interface PipelineStageDTO {
  stage_id: string;
  label: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  attempt_count: number;
  error: PipelineRunErrorDTO | null;
}

export interface PipelineRunDTO {
  run_id: string;
  project_id: string;
  status: string;
  status_label: string;
  started_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  current_phase: string;
  current_stage: string | null;
  current_step: string | null;
  completed_steps: number;
  total_steps: number;
  stages: PipelineStageDTO[];
  last_successful_checkpoint: string | null;
  error: PipelineRunErrorDTO | null;
  resume_available: boolean;
  analysis_ready: boolean;
  writing_plan_ready: boolean;
  full_bid_generation_active: boolean;
  can_start_full_bid_generation: boolean;
  fulltext_ready: boolean;
  machine_audit_ready: boolean;
  human_review_ready: boolean;
}

export interface PipelineRunListDTO {
  project_id: string;
  runs: PipelineRunDTO[];
}

export interface ProjectDetailDTO {
  project: ProjectSummaryDTO;
  notes: string | null;
  source_files: SourceFileDTO[];
  runs: PipelineRunDTO[];
}

export interface TenderAnalysisSummaryDTO {
  project_id: string;
  project_name: string;
  status: string;
  source_file_count: number;
  main_tender_count: number;
  attachment_count: number;
  requirement_count: number | null;
  score_item_count: number | null;
  technical_score_max: number | null;
  fact_need_count: number | null;
  material_need_count: number | null;
  current_run_id: string | null;
  analysis_ready: boolean;
}

export interface AnalysisLineageDTO {
  source_mode: "pipeline_analysis" | "legacy_response_outline";
  run_id: string | null;
  completed_at: string | null;
  analysis_ready: boolean;
  post_writing_available: boolean;
}

export interface AnalysisSourceFileDTO {
  file_id: string;
  display_name: string;
  role: string;
  role_label: string;
  parse_status: string;
  size: number | null;
  source_reference: SourceReferenceDTO | null;
}

export interface TenderProjectInfoItemDTO {
  key: string;
  label: string;
  value: string;
  source_reference: SourceReferenceDTO | null;
}

export interface RequirementCategoryCountDTO {
  key: string;
  label: string;
  count: number;
}

export interface AnalysisPriorityItemDTO {
  trace_id: string;
  title: string;
  description: string;
  action_kind: string;
  action_label: string;
  priority: string | null;
  requirement_ids: string[];
  source_references: SourceReferenceDTO[];
}

export interface TenderAnalysisOverviewDTO {
  project_id: string;
  project_name: string;
  status: string;
  lineage: AnalysisLineageDTO;
  source_file_count: number;
  project_information: TenderProjectInfoItemDTO[];
  requirement_count: number;
  requirement_categories: RequirementCategoryCountDTO[];
  score_rule_count: number;
  available_score_total: number | null;
  parameter_table_count: number;
  parameter_row_count: number;
  fact_need_count: number;
  material_need_count: number;
  confirmation_count: number;
  priority_items: AnalysisPriorityItemDTO[];
}

export interface TenderScoringRuleDTO {
  score_id: string;
  title: string;
  criteria_text: string;
  available_score: number | null;
  score_text: string;
  score_group: string | null;
  preparation_text: string | null;
  response_suggestions: string[];
  requirement_ids: string[];
  fact_needs: string[];
  fact_preparation: Array<{ trace_id: string; title: string; status: string; status_label: string; target_url: string }>;
  material_needs: RequirementDependencyDTO[];
  source_reference: SourceReferenceDTO;
}

export interface TenderScoringRulesDTO {
  project_id: string;
  lineage: AnalysisLineageDTO;
  rule_count: number;
  available_score_total: number | null;
  rules: TenderScoringRuleDTO[];
  machine_assessment_included: false;
}

export interface TenderTableCellDTO {
  text: string;
  row: number;
  column: number;
  rowspan: number;
  colspan: number;
}

export interface TenderParameterTableDTO {
  table_id: string;
  title: string;
  requirement_id: string | null;
  column_count: number;
  row_count: number;
  rows: TenderTableCellDTO[][];
  raw_text: string | null;
  mandatory_marker: string | null;
  source_reference: SourceReferenceDTO;
}

export interface TenderParametersDTO {
  project_id: string;
  lineage: AnalysisLineageDTO;
  table_count: number;
  row_count: number;
  tables: TenderParameterTableDTO[];
}

export type PreparationNeedKind = "project_fact" | "fact_confirmation" | "material" | "writing_response";

export interface PreparationNeedDTO {
  trace_id: string;
  kind: PreparationNeedKind;
  title: string;
  description: string;
  status: string;
  status_label: string;
  required: boolean;
  priority: string | null;
  requirement_ids: string[];
  score_item_ids: string[];
  source_references: SourceReferenceDTO[];
}

export interface TenderRiskDTO {
  trace_id: string;
  category: string;
  category_label: string;
  title: string;
  description: string;
  action_priority: string | null;
  requirement_ids: string[];
  source_references: SourceReferenceDTO[];
}

export interface TenderRisksPreparationDTO {
  project_id: string;
  lineage: AnalysisLineageDTO;
  risks: TenderRiskDTO[];
  facts: PreparationNeedDTO[];
  materials: PreparationNeedDTO[];
  confirmations: PreparationNeedDTO[];
  writing_responses: PreparationNeedDTO[];
  blocking_semantics_available: boolean;
}

export interface PreparationDependencyDTO {
  kind: "requirement" | "score" | "writing";
  trace_id: string;
  title: string;
  description: string | null;
  target_url: string | null;
}

export interface WritingContextItemDTO {
  context_id: string;
  title: string;
  value: string | null;
  availability: "available" | "pending" | "placeholder_required" | "unavailable";
  availability_label: string;
  source_policy: string;
  source_description: string | null;
  technical_ref: string | null;
  context_kind: "project_fact" | "human_confirmation" | "material";
  stage9_consumable: boolean;
  stage9_policy: string | null;
}

export interface FactValueFieldDTO {
  key: string;
  label: string;
  field_type: "text" | "number";
  required: boolean;
  minimum: number | null;
}

export interface WritingPreparationItemDTO {
  item_id: string;
  kind: "project_fact" | "human_confirmation" | "material" | "evidence_query";
  fact_key: string | null;
  title: string;
  description: string;
  status: string;
  status_label: string;
  required: boolean;
  priority: string | null;
  current_value: string | null;
  current_raw_value: unknown;
  value_type: "text" | "number" | "structured" | null;
  value_fields: FactValueFieldDTO[];
  candidate_values: string[];
  dependencies: PreparationDependencyDTO[];
  source_references: SourceReferenceDTO[];
  writing_context: WritingContextItemDTO | null;
  technical_refs: string[];
  can_confirm: boolean;
  can_defer: boolean;
  confirmation_response_kind: "boolean" | "selection" | "text" | null;
  stage9_consumable: boolean;
}

export interface WritingPreparationSummaryDTO {
  confirmed_fact_count: number;
  pending_fact_count: number;
  confirmation_count: number;
  query_not_executed_count: number;
  candidate_material_count: number;
  prepared_material_count: number;
  placeholder_count: number;
  analysis_ready: boolean;
  planning_accessible: boolean;
  gate_source: string;
  guidance: string;
}

export interface WritingPreparationDTO {
  project_id: string;
  project_name: string;
  version_token: string;
  lineage: AnalysisLineageDTO;
  summary: WritingPreparationSummaryDTO;
  items: WritingPreparationItemDTO[];
  writing_context: WritingContextItemDTO[];
  read_only: boolean;
  mutation_available: boolean;
  mutation_unavailable_reason: string | null;
}

export interface ProjectFactCommandDTO {
  status: "confirmed";
  value: unknown;
  source_description: string;
  confirmation_basis: string;
  note: string | null;
  change_reason: string | null;
}

export interface ProjectFactMutationResultDTO {
  updated_fact_id: string;
  version_token: string;
  writing_preparation: WritingPreparationDTO;
  writing_context: WritingContextItemDTO[];
}

export interface HumanConfirmationRequestDTO {
  confirmation_id: string;
  request_kind: "project_fact" | "business_choice" | "material_availability";
  managed_by: "project_fact_service" | "human_confirmation_service" | "material_workflow";
  question: string;
  reason: string;
  category: string;
  priority: string;
  response_kind: "boolean" | "selection" | "text";
  candidate_values: string[];
  current_value: unknown;
  status: "pending" | "confirmed";
  status_label: string;
  answer: unknown;
  source_description: string | null;
  confirmation_basis: string | null;
  note: string | null;
  writing_effect: string;
  linked_fact_id: string | null;
  requirement_refs: string[];
  score_refs: string[];
  writing_task_refs: string[];
  writing_section_refs: string[];
  source_references: SourceReferenceDTO[];
  can_decide: boolean;
  stage9_consumable: boolean;
}

export interface HumanConfirmationListDTO {
  project_id: string;
  project_name: string;
  version_token: string;
  lineage: AnalysisLineageDTO;
  pending_count: number;
  confirmed_count: number;
  project_fact_routed_count: number;
  material_routed_count: number;
  confirmations: HumanConfirmationRequestDTO[];
}

export interface HumanConfirmationCommandDTO {
  answer: boolean | string;
  source_description: string;
  confirmation_basis: string;
  note: string | null;
  change_reason: string | null;
}

export interface HumanConfirmationMutationResultDTO {
  updated_confirmation_id: string;
  version_token: string;
  confirmation: HumanConfirmationRequestDTO;
  writing_preparation: WritingPreparationDTO;
  writing_context: WritingContextItemDTO[];
}

export interface WritingPlanLineageDTO {
  source_mode: "pipeline_planning" | "legacy_planning" | "not_generated";
  analysis_run_id: string | null;
  current_analysis_run_id: string | null;
  planning_key: string | null;
  generated_at: string | null;
  response_outline_sha256: string | null;
  writing_outline_sha256: string | null;
  writing_tasks_sha256: string | null;
  task_information_sha256: string | null;
  is_current: boolean;
  lineage_valid: boolean;
  stale_reason: string | null;
}

export interface WritingPlanFactDependencyDTO {
  fact_id: string;
  fact_key: string;
  title: string;
  required: boolean;
  status: string;
  status_label: string;
  availability: "available" | "placeholder_required" | "pending" | "unavailable";
  value: string | null;
  source_policy: string;
  target_url: string | null;
}

export interface WritingPlanMaterialDependencyDTO {
  dependency_id: string;
  evidence_type: string;
  title: string;
  required: boolean;
  status: string;
  status_label: string;
  source_policy: string;
  target_url: string | null;
}

export interface WritingPlanReferenceDTO {
  trace_id: string;
  title: string;
  description: string | null;
  max_score: number | null;
  mapping_role: string | null;
  target_url: string;
}

export interface WritingTaskDTO {
  task_id: string;
  chapter_id: string;
  section_id: string;
  title: string;
  objective: string;
  instruction_points: string[];
  requirement_refs: WritingPlanReferenceDTO[];
  score_refs: WritingPlanReferenceDTO[];
  fact_dependencies: WritingPlanFactDependencyDTO[];
  material_dependencies: WritingPlanMaterialDependencyDTO[];
  human_confirmation_refs: WritingContextItemDTO[];
  placeholder_refs: string[];
  prohibited_inference: string[];
  readiness: string;
  readiness_label: string;
  body_generated: boolean;
}

export interface WritingSectionDTO {
  section_id: string;
  chapter_id: string;
  title: string;
  path: string[];
  order: number;
  depth: number;
  purpose: string;
  task_ids: string[];
  requirement_count: number;
  score_count: number;
  pending_fact_count: number;
  pending_material_count: number;
  preparation_label: string;
}

export interface WritingChapterDTO {
  chapter_id: string;
  title: string;
  order: number;
  purpose: string;
  sections: WritingSectionDTO[];
  task_ids: string[];
  requirement_count: number;
  score_count: number;
  pending_fact_count: number;
  pending_material_count: number;
}

export interface WritingPlanCoverageDTO {
  trace_id: string;
  title: string;
  section_ids: string[];
  status: "planned" | "multiple_sections" | "unassigned";
  max_score: number | null;
  target_url: string;
}

export interface WritingPlanSummaryDTO {
  chapter_count: number;
  section_count: number;
  task_count: number;
  requirement_count: number;
  score_count: number;
  confirmed_fact_count: number;
  placeholder_fact_count: number;
  query_not_executed_count: number;
  pending_human_confirmation_count: number;
  stage9_unconsumable_confirmation_count: number;
  unassigned_requirement_count: number;
}

export interface WritingPlanDTO {
  project_id: string;
  project_name: string;
  available: boolean;
  status: "ready" | "stale" | "not_generated" | "invalid";
  status_label: string;
  message: string;
  lineage: WritingPlanLineageDTO;
  summary: WritingPlanSummaryDTO;
  chapters: WritingChapterDTO[];
  tasks: WritingTaskDTO[];
  requirement_coverage: WritingPlanCoverageDTO[];
  score_coverage: WritingPlanCoverageDTO[];
  writing_context: WritingContextItemDTO[];
  generation_available: boolean;
  generation_reason: string | null;
}

export interface AIWritingReadinessDTO {
  project_id: string;
  project_name: string;
  writing_plan_ready: boolean;
  project_facts_connected: boolean;
  human_confirmations_connected: boolean;
  pending_confirmation_count: number;
  confirmed_business_choice_count: number;
  task_count: number;
  task_ready_count: number;
  task_blocked_count: number;
  placeholder_task_count: number;
  stage9_contract_ready: boolean;
  pending_policy: "placeholder_required" | "generation_blocked_for_task";
  input_fingerprint: string | null;
  message: string;
}

export type WritingGenerationStatus =
  | "queued"
  | "running"
  | "validating"
  | "completed"
  | "failed"
  | "validation_failed";

export interface WritingGenerationDTO {
  generation_id: string;
  project_id: string;
  task_id: string;
  chapter_id: string;
  section_id: string;
  input_fingerprint: string;
  task_fingerprint: string;
  status: WritingGenerationStatus;
  status_label: string;
  current_step: string | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  draft_markdown: string | null;
  placeholder_ids: string[];
  validation_errors: string[];
  error_code: string | null;
  error_message: string | null;
  generated_from_current_context: boolean;
  model_profile: string | null;
}

export interface WritingGenerationListDTO {
  project_id: string;
  generations: WritingGenerationDTO[];
}

export interface WorkflowStepDTO {
  key: string;
  label: string;
  status: WorkflowState;
  detail: string | null;
}

export interface ScoreRangeDTO {
  minimum: number;
  maximum: number;
}

export interface ScoringHeadlineDTO {
  technical_score_max: number;
  confirmed: number;
  estimated_range: ScoreRangeDTO;
  pending_exposure: number;
  potential_gain: ScoreRangeDTO;
  single_total_score_permitted: boolean;
}

export interface ReviewProgressDTO {
  issue_decisions_total: number;
  issue_decisions_completed: number;
  fact_confirmations_total: number;
  fact_confirmations_completed: number;
  material_verifications_total: number;
  material_verifications_completed: number;
}

export interface FactMaterialHeadlineDTO {
  missing_project_facts: number;
  confirmations_required: number;
  queries_not_executed: number;
  candidate_materials: number;
  verified_materials: number;
}

export interface LineageInfoDTO {
  audited_content_sha256: string;
  audit_sha256: string;
  scoring_sha256: string;
  review_package_id: string;
  human_review_id: string | null;
  finalization_id: string | null;
}

export interface ProjectOverviewDTO {
  project: ProjectSummaryDTO;
  workflow: WorkflowStepDTO[];
  review_progress: ReviewProgressDTO;
  scoring: ScoringHeadlineDTO;
  facts_materials: FactMaterialHeadlineDTO;
  actionability_counts: Record<ActionabilityKey, number>;
  revision_ready: boolean;
  revision_readiness_reason: string;
  lineage: LineageInfoDTO;
  current_pipeline_run: PipelineRunDTO | null;
  can_start_full_bid_generation: boolean;
  can_resume: boolean;
  fulltext_ready: boolean;
  machine_audit_ready: boolean;
  human_review_ready: boolean;
  scoring_item_count: number;
  scoring_available: boolean;
}

export interface ReviewLocationDTO {
  chapter_id: string | null;
  chapter_title: string | null;
  section_id: string | null;
  section_heading: string | null;
  paragraph_id: string | null;
  paragraph_index: number | null;
  excerpt: string | null;
  response_role: string | null;
  technical_ref: string | null;
}

export interface ReviewItemDTO {
  title: string;
  actionability: ActionabilityKey;
  severity: string;
  release_blocking: boolean;
  requirement_text: string | null;
  current_response: string | null;
  machine_issue: string;
  machine_rationale: string | null;
  recommendation: string | null;
  response_location: ReviewLocationDTO | null;
  revision_locations: ReviewLocationDTO[];
  required_facts: string[];
  required_materials: string[];
  scoring_impacts: ReviewScoreImpactDTO[];
  current_decision: string | null;
  rejection_reason: string | null;
  revision_instruction: string | null;
  optional_comment: string | null;
  use_machine_location: boolean;
  location_override: Record<string, unknown> | null;
  location_override_reason: string | null;
  risk_acceptance_ref: string | null;
  updated_at: string | null;
  change_history: ReviewHistoryEntryDTO[];
  trace_id: string;
  source_type: string;
  confidence: string | null;
  machine_revision_available: boolean;
  revision_candidate_ready: boolean;
}

export type RevisionOperationStatus = "queued" | "running" | "completed" | "failed";
export type RevisionOperationPhase = "rewriting" | "reviewing" | "publishing";

export interface RevisionOperationDTO {
  operation_id: string;
  project_id: string;
  source_type: string;
  source_id: string;
  status: RevisionOperationStatus;
  phase: RevisionOperationPhase;
  status_label: string;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ReleaseArtifactDTO {
  filename: string;
  sha256: string;
  size_bytes: number;
}

export interface RequiredSubmissionItemDTO {
  item_key: string;
  display_title: string;
  status: "pending" | "prepared";
  description: string;
  submission_kind: "form" | "external_upload";
  current_record: string | null;
}

export interface ReleaseStatusDTO {
  project_id: string;
  current_output_key: string;
  releaseable: boolean;
  blocking_reasons: string[];
  status: "blocked" | "ready" | "published";
  generated_at: string | null;
  docx: ReleaseArtifactDTO | null;
  pdf: ReleaseArtifactDTO | null;
  required_submission_items: RequiredSubmissionItemDTO[];
}

export interface ReviewScoreImpactDTO {
  title: string;
  max_score: number;
  assessment_status: string;
  score_range: ScoreRangeDTO;
  trace_id: string;
}

export interface ReviewHistoryEntryDTO {
  changed_at?: string;
  updated_at?: string;
  change_reason?: string;
  previous?: Record<string, unknown>;
}

export interface FactConfirmationDTO {
  title: string;
  description: string | null;
  source_type: "FACT" | "FCA";
  trace_id: string;
  confirmation_status: string;
  confirmed_value: unknown;
  source_description: string | null;
  confirmation_basis: string | null;
  confirmation_note: string | null;
  rejection_reason: string | null;
  required: boolean;
  related_requirement_ids: string[];
  response_location: ReviewLocationDTO | null;
  response_locations: ReviewLocationDTO[];
  affected_location_count: number;
  updated_at: string | null;
  change_history: ReviewHistoryEntryDTO[];
}

export interface MaterialVerificationDTO {
  title: string;
  description: string | null;
  source_type: "EVIDENCE_QUERY" | "EVIDENCE";
  trace_id: string;
  upstream_status: string;
  upstream_status_label: string;
  verification_status: string;
  can_verify: boolean;
  material_name: string | null;
  subject_match: boolean | null;
  validity_date: string | null;
  verification_note: string | null;
  candidate_reference: string | null;
  file_reference: string | null;
  required: boolean;
  related_requirement_ids: string[];
  updated_at: string | null;
  change_history: ReviewHistoryEntryDTO[];
}

export interface ReviewGroupDTO {
  key: ActionabilityKey;
  label: string;
  description: string;
  items: ReviewItemDTO[];
}

export interface ReviewWorkspaceDTO {
  project: ProjectSummaryDTO;
  reviewed_version: VersionRefDTO;
  progress: ReviewProgressDTO;
  actionability_counts: Record<ActionabilityKey, number>;
  groups: ReviewGroupDTO[];
  fact_confirmations: FactConfirmationDTO[];
  material_verifications: MaterialVerificationDTO[];
  lineage: LineageInfoDTO;
  read_only: boolean;
}

export interface ReviewMutationResultDTO {
  updated_kind: "issue" | "fact" | "material" | "risk";
  updated_id: string;
  version_token: string;
  review: ReviewWorkspaceDTO;
  overview: ProjectOverviewDTO;
}

export interface IssueDecisionCommandDTO {
  decision: "accepted" | "modified" | "rejected";
  revision_instruction?: string | null;
  rejection_reason?: string | null;
  optional_comment?: string | null;
  use_machine_location: boolean;
  location_override?: { chapter_id?: string; section_id?: string; paragraph_id?: string; location_note?: string } | null;
  location_override_reason?: string | null;
  change_reason?: string | null;
}

export interface FactConfirmationCommandDTO {
  confirmation_status: "confirmed" | "rejected";
  confirmed_value?: unknown;
  source_description?: string | null;
  confirmation_basis?: "human_provided" | "document_verified" | "explicitly_confirmed" | null;
  confirmation_note?: string | null;
  rejection_reason?: string | null;
  change_reason?: string | null;
}

export interface MaterialVerificationCommandDTO {
  verification_status: "verified" | "invalid" | "expired" | "not_provided";
  material_name?: string | null;
  subject_match?: boolean | null;
  validity_date?: string | null;
  verification_note?: string | null;
  candidate_reference?: string | null;
  file_reference?: string | null;
  change_reason?: string | null;
}

export interface RiskAcceptanceCommandDTO {
  reason: string;
  scope: string;
  optional_comment?: string | null;
  confirmed: true;
  change_reason?: string | null;
}

export interface RequirementResponseDTO {
  title: string;
  requirement_text: string;
  mandatory: boolean;
  category: string | null;
  source_document: string | null;
  source_location: string | null;
  source_reference: SourceReferenceDTO;
  response_status: string;
  current_response: string | null;
  primary_response_location: ReviewLocationDTO | null;
  response_locations: ReviewLocationDTO[];
  related_finding_count: number;
  related_score_count: number;
  review: RequirementReviewSummaryDTO;
  fact_dependency_count: number;
  material_dependency_count: number;
  query_not_executed_count: number;
  trace_id: string;
}

export interface SourceReferenceDTO {
  file_name: string | null;
  clause_path: string[];
  page_start: number | null;
  page_end: number | null;
  table_name: string | null;
  table_range: string | null;
  row_or_cell: string | null;
  source_kind: "paragraph" | "table";
}

export interface RequirementReviewSummaryDTO {
  actionability: ActionabilityKey | null;
  pending_count: number;
  completed_count: number;
  trace_ids: string[];
}

export interface RequirementListDTO {
  project_id: string;
  reviewed_version: VersionRefDTO;
  summary: Record<string, number>;
  requirements: RequirementResponseDTO[];
}

export interface RequirementFindingDTO {
  title: string;
  summary: string;
  rationale: string | null;
  severity: string | null;
  release_blocking: boolean;
  verdict: string | null;
  recommendation: string | null;
  requires_human_review: boolean;
  trace_id: string;
  source_type: string;
}

export interface RequirementPendingEvidenceDTO {
  evidence_type: string;
  status: string;
  status_label: string;
  reason: string | null;
}

export interface RequirementScoreImpactDTO {
  title: string;
  criteria_text: string;
  max_score: number;
  assessment_status: string;
  score_range: ScoreRangeDTO;
  pending_conditions: string[];
  pending_evidence: RequirementPendingEvidenceDTO[];
  potential_gain: ScoreRangeDTO;
  trace_id: string;
}

export type RequirementDependencyKind =
  | "project_fact"
  | "fact_confirmation"
  | "evidence_requirement"
  | "evidence_query"
  | "material";

export interface RequirementDependencyDTO {
  kind: RequirementDependencyKind;
  title: string;
  status: string;
  status_label: string;
  description: string | null;
  required: boolean;
  trace_id: string;
}

export interface RequirementReviewStateDTO {
  title: string;
  actionability: ActionabilityKey;
  severity: string;
  release_blocking: boolean;
  machine_recommendation: string | null;
  current_decision: string | null;
  revision_instruction: string | null;
  risk_acceptance_ref: string | null;
  revision_locations: ReviewLocationDTO[];
  trace_id: string;
}

export interface RequirementDetailDTO {
  project_id: string;
  reviewed_version: VersionRefDTO;
  requirement: RequirementResponseDTO;
  findings: RequirementFindingDTO[];
  scoring_impacts: RequirementScoreImpactDTO[];
  fact_dependencies: RequirementDependencyDTO[];
  material_dependencies: RequirementDependencyDTO[];
  preparation_dependencies: RequirementDependencyDTO[];
  review_states: RequirementReviewStateDTO[];
  read_only: boolean;
}

export interface ScoringItemDTO {
  title: string;
  criteria_text: string;
  max_score: number;
  assessment_status: string;
  confirmed_score: number | null;
  estimated_score: number | null;
  score_range: ScoreRangeDTO;
  satisfied_conditions: string[];
  missing_conditions: string[];
  pending_conditions: string[];
  deduction_reasons: string[];
  potential_gain: ScoreRangeDTO;
  response_locations: ReviewLocationDTO[];
  trace_id: string;
}

export interface AuditedFulltextVersionDTO {
  display_name: string;
  revision: string;
  audited_content_sha256: string;
  audited_artifact_sha256: string;
  audit_sha256: string;
  review_package_id: string;
  human_review_id: string;
  generated_at: string | null;
}

export interface AuditedFulltextStatusDTO {
  release_ready: boolean;
  audit_result: string;
  review_status: string;
  stage13_ready: boolean;
}

export interface FulltextTraceRefsDTO {
  requirement_ids: string[];
  finding_ids: string[];
  score_item_ids: string[];
  review_item_ids: string[];
  fact_refs: string[];
  material_refs: string[];
}

export interface FulltextParagraphDTO {
  paragraph_id: string;
  paragraph_index: number;
  document_order: number;
  text: string;
  text_sha256: string;
  content_type: "paragraph" | "table" | "list";
  placeholder_ids: string[];
  trace: FulltextTraceRefsDTO;
}

export interface FulltextSectionDTO {
  section_id: string;
  heading: string;
  order: number;
  block_type: string;
  paragraphs: FulltextParagraphDTO[];
  trace: FulltextTraceRefsDTO;
}

export interface FulltextChapterDTO {
  chapter_id: string;
  title: string;
  order: number;
  sections: FulltextSectionDTO[];
}

export interface FulltextRequirementTraceDTO {
  trace_id: string;
  title: string;
  requirement_text: string;
  response_status: string;
  response_locations: ReviewLocationDTO[];
}

export interface FulltextFindingTraceDTO {
  trace_id: string;
  title: string;
  summary: string;
  rationale: string | null;
  severity: string | null;
  release_blocking: boolean;
  recommendation: string | null;
  locations: ReviewLocationDTO[];
}

export interface FulltextReviewTraceDTO {
  trace_id: string;
  title: string;
  actionability: ActionabilityKey;
  current_decision: string;
  risk_accepted: boolean;
  response_locations: ReviewLocationDTO[];
  recommended_locations: ReviewLocationDTO[];
  fact_refs: string[];
  material_refs: string[];
  revision_candidate: boolean;
}

export interface FulltextScoreTraceDTO {
  trace_id: string;
  title: string;
  criteria_text: string;
  max_score: number;
  assessment_status: string;
  score_range: ScoreRangeDTO;
  pending_evidence: RequirementPendingEvidenceDTO[];
  response_locations: ReviewLocationDTO[];
}

export interface FulltextDependencyTraceDTO {
  trace_id: string;
  kind: string;
  title: string;
  status: string;
  status_label: string;
}

export interface AuditedFulltextDTO {
  project_id: string;
  document_id: string;
  version: AuditedFulltextVersionDTO;
  status: AuditedFulltextStatusDTO;
  chapters: FulltextChapterDTO[];
  trace_index: {
    requirements: FulltextRequirementTraceDTO[];
    findings: FulltextFindingTraceDTO[];
    reviews: FulltextReviewTraceDTO[];
    scores: FulltextScoreTraceDTO[];
    dependencies: FulltextDependencyTraceDTO[];
  };
}

export interface ScoringSummaryDTO {
  project_id: string;
  reviewed_version: VersionRefDTO;
  headline: ScoringHeadlineDTO;
  item_counts: Record<string, number>;
  items: ScoringItemDTO[];
}

export interface FactMaterialItemDTO {
  category: string;
  title: string;
  status: string;
  status_label: string;
  description: string;
  required: boolean;
  related_task_count: number;
  trace_id: string;
  current_value: string | null;
  related_locations: string[];
  requirement_context: string[];
  source_label: string | null;
  action_label: string;
  action_url: string;
  semantic_resolved: boolean;
}

export interface FactMaterialSummaryDTO {
  project_id: string;
  reviewed_version: VersionRefDTO;
  headline: FactMaterialHeadlineDTO;
  status_legend: Record<string, string>;
  status_counts: Record<string, number>;
  items: FactMaterialItemDTO[];
}

export interface VersionLineageEntryDTO {
  kind: string;
  label: string;
  status: string;
  generated_at: string | null;
  content_sha256: string | null;
  artifact_sha256: string | null;
  internal_id: string | null;
  current: boolean;
}

export interface VersionSummaryDTO {
  project_id: string;
  current_revision: VersionRefDTO;
  current_reviewed_version: VersionRefDTO;
  entries: VersionLineageEntryDTO[];
  revision_round_contract_available: boolean;
}

export interface ApiErrorPayload {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown> | null;
  };
}

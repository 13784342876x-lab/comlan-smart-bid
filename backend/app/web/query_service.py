from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from app.technical_fulltext_auditor import inspect_technical_fulltext_audit
from app.technical_fulltext_human_review import inspect_technical_fulltext_human_review
from app.technical_fulltext_human_review_finalization import (
    accepted_machine_revision_instruction,
    inspect_technical_fulltext_review_finalization,
)
from app.technical_fulltext_review_package import (
    inspect_technical_fulltext_review_package,
)
from app.technical_fulltext_scoring_assessment import (
    inspect_technical_fulltext_scoring_assessment,
)
from app.technical_review_projection import (
    active_technical_review_items,
    confirmation_display_title,
    confirmation_groups,
    identity_title_map,
    user_facing_text,
)
from app.web.dtos import (
    FactMaterialHeadlineDTO,
    FactMaterialItemDTO,
    FactMaterialSummaryDTO,
    FactConfirmationDTO,
    LineageInfoDTO,
    ProjectListDTO,
    ProjectOverviewDTO,
    ProjectSummaryDTO,
    RequirementDependencyDTO,
    RequirementDetailDTO,
    RequirementFindingDTO,
    RequirementListDTO,
    RequirementPendingEvidenceDTO,
    RequirementResponseDTO,
    RequirementReviewStateDTO,
    RequirementReviewSummaryDTO,
    RequirementScoreImpactDTO,
    ReviewGroupDTO,
    ReviewItemDTO,
    ReviewLocationDTO,
    MaterialVerificationDTO,
    ReviewProgressDTO,
    ReviewScoreImpactDTO,
    ReviewWorkspaceDTO,
    ScoreRangeDTO,
    ScoringHeadlineDTO,
    ScoringItemDTO,
    ScoringSummaryDTO,
    SourceReferenceDTO,
    VersionLineageEntryDTO,
    VersionRefDTO,
    VersionSummaryDTO,
    WorkflowStepDTO,
)
from app.web.project_resolver import ProjectArtifacts, ReadOnlyProjectResolver


class WebProjectionError(RuntimeError):
    pass


class LineageConflictError(WebProjectionError):
    pass


class RequirementNotFoundError(LookupError):
    pass


ACTIONABILITY_META: dict[str, tuple[str, str]] = {
    "must_decide": ("必须决定", "这些事项会阻塞进入修改阶段，需要人工作出业务决定。"),
    "verification": ("事实与材料核验", "确认真实事实或材料状态，不要求人工重新发现问题。"),
    "no_action": ("无需修改，待知悉", "机器已判断无需产生修改任务，可作为审查记录知悉。"),
    "informational": ("背景信息", "仅提供上下文，不阻塞后续流程。"),
}

FACT_STATUS_LABELS = {
    "query_not_executed": "尚未查询",
    "candidate_found": "已发现候选材料",
    "candidate": "候选材料",
    "verified": "已核验",
    "confirmed_missing": "确认缺失",
    "missing": "缺少项目事实",
    "confirmation_required": "待人工确认",
    "invalid": "材料无效",
    "expired": "材料已过期",
    "not_provided": "未提供",
    "pending": "待处理",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebProjectionError(f"artifact is unavailable or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise WebProjectionError(f"artifact must contain an object: {path.name}")
    return value


def _load_optional_referenced_json(reference: Any, allowed_root: Path) -> dict[str, Any]:
    if not isinstance(reference, dict) or not reference.get("path"):
        return {}
    try:
        candidate = Path(str(reference["path"])).expanduser().resolve()
        candidate.relative_to(allowed_root.resolve())
    except (OSError, ValueError):
        return {}
    return _load_json(candidate) if candidate.is_file() else {}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _score_range(value: Any) -> ScoreRangeDTO:
    data = value if isinstance(value, dict) else {}
    return ScoreRangeDTO(minimum=_number(data.get("min")), maximum=_number(data.get("max")))


def _text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result or None


def _response_excerpt(value: Any) -> str | None:
    if isinstance(value, dict):
        direct = _text(value.get("excerpt"))
        if direct:
            return direct
        location = value.get("location")
        if isinstance(location, dict):
            return _text(location.get("excerpt"))
        return None
    return _text(value)


def _source_page_label(value: Any) -> str | None:
    if isinstance(value, dict):
        start = value.get("start")
        end = value.get("end")
        if start is None and end is None:
            return None
        if start == end or end is None:
            return f"第 {start} 页"
        if start is None:
            return f"第 {end} 页"
        return f"第 {start}–{end} 页"
    return f"第 {value} 页" if value is not None else None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _location(value: Any) -> ReviewLocationDTO | None:
    if not isinstance(value, dict):
        return None
    technical_parts = [
        _text(value.get("chapter_id")),
        _text(value.get("section_id")),
        _text(value.get("paragraph_id")),
    ]
    technical_ref = " / ".join(part for part in technical_parts if part)
    paragraph_index = value.get("paragraph_index")
    return ReviewLocationDTO(
        chapter_id=_text(value.get("chapter_id")),
        chapter_title=_text(value.get("chapter_title")),
        section_id=_text(value.get("section_id")),
        section_heading=_text(value.get("section_heading")) or _text(value.get("heading")),
        paragraph_id=_text(value.get("paragraph_id")),
        paragraph_index=int(paragraph_index) if isinstance(paragraph_index, int) else None,
        excerpt=_text(value.get("excerpt")),
        response_role=_text(value.get("response_role")),
        technical_ref=technical_ref or None,
    )


def _user_location(
    value: Any, titles: dict[str, str]
) -> ReviewLocationDTO | None:
    location = _location(value)
    if location is None:
        return None
    chapter_title = location.chapter_title
    section_heading = location.section_heading
    if not chapter_title and location.chapter_id:
        chapter_title = titles.get(location.chapter_id)
    if not section_heading and location.section_id:
        mapped = titles.get(location.section_id)
        if mapped:
            section_heading = mapped.split(" > ")[-1]
    return location.model_copy(
        update={
            "chapter_title": user_facing_text(chapter_title, titles),
            "section_heading": user_facing_text(section_heading, titles),
            "excerpt": user_facing_text(location.excerpt, titles),
        }
    )


def _source_reference(item: dict[str, Any]) -> SourceReferenceDTO:
    value = item.get("source_reference")
    data = value if isinstance(value, dict) else {}
    table = item.get("source_table_or_cell")
    table_data = table if isinstance(table, dict) else {}
    table_name = _text(data.get("table_name")) or _text(table_data.get("table_name"))
    table_range = _text(data.get("table_range")) or _text(table_data.get("table_range"))
    row_or_cell = _text(data.get("row_or_cell")) or _text(table_data.get("row_or_cell"))
    page = item.get("source_page")
    page_data = page if isinstance(page, dict) else {}
    return SourceReferenceDTO(
        file_name=_text(data.get("file_name")) or _text(item.get("source_file")),
        clause_path=_strings(data.get("clause_path")) or _strings(item.get("source_clause")),
        page_start=(data.get("page_start") if isinstance(data.get("page_start"), int) else page_data.get("start")),
        page_end=(data.get("page_end") if isinstance(data.get("page_end"), int) else page_data.get("end")),
        table_name=table_name,
        table_range=table_range,
        row_or_cell=row_or_cell,
        source_kind="table" if table_name or table_range or (row_or_cell and "table" in row_or_cell) else "paragraph",
    )


def _actionability(item: dict[str, Any]) -> str:
    if bool(item.get("must_decide_before_stage13")):
        return "must_decide"
    mode = str(item.get("handoff_mode") or "")
    if mode == "verification":
        return "verification"
    if mode == "no_action":
        return "no_action"
    return "informational"


def _display_title(item: dict[str, Any]) -> str:
    title = str(item.get("display_title") or "").strip()
    title = re.sub(r"^TR-\d+\s*[:：-]?\s*", "", title)
    if title and not (item.get("handoff_mode") == "structural_normalization" and title.isascii()):
        return title
    if item.get("handoff_mode") == "structural_normalization":
        return "统一正文中的同一事实占位标识"
    return str(item.get("machine_issue_summary") or "待人工核验事项").strip()


class ProjectReadService:
    """UI-friendly read projections over validated Stage11/12 artifacts."""

    def __init__(self, resolver: ReadOnlyProjectResolver | None = None) -> None:
        self.resolver = resolver or ReadOnlyProjectResolver()

    def list_projects(self) -> ProjectListDTO:
        return ProjectListDTO(projects=[self._project_summary(self._bundle(item)) for item in self.resolver.list_projects()])

    def overview(self, project_id: str) -> ProjectOverviewDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        project = self._project_summary(bundle)
        package_summary = bundle["package"]["summary"]
        finalization = bundle["finalization"]
        final_gate = finalization["stage13_gate"] if finalization is not None else None
        return ProjectOverviewDTO(
            project=project,
            workflow=[
                WorkflowStepDTO(key="fulltext", label="全文冻结", status="completed", detail="当前全文版本已提交"),
                WorkflowStepDTO(
                    key="machine_audit",
                    label="机器审核",
                    status="completed",
                    detail="审核已完成，存在需要人工处理的事项",
                ),
                WorkflowStepDTO(key="human_review", label="人工审核", status="active", detail=project.next_action),
                WorkflowStepDTO(key="revision", label="修改与版本", status="blocked", detail="等待人工审核完成"),
                WorkflowStepDTO(key="release", label="发布", status="not_started", detail="当前版本尚未达到发布条件"),
            ],
            review_progress=self._review_progress(bundle),
            scoring=self._scoring_headline(package_summary),
            facts_materials=self._facts_headline(bundle),
            actionability_counts=self._actionability_counts(bundle),
            revision_ready=bool(final_gate and final_gate.get("stage13_ready")),
            revision_readiness_reason=str(
                (final_gate or {}).get("human_readable_reason")
                or "尚未完成人工审核。"
            ),
            lineage=self._lineage(bundle),
            fulltext_ready=True,
            machine_audit_ready=True,
            human_review_ready=True,
            scoring_item_count=len(bundle["package"].get("scoring_matrix") or []),
            scoring_available=bool(bundle["package"].get("scoring_matrix") or []),
        )

    def review(self, project_id: str) -> ReviewWorkspaceDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        package = bundle["package"]
        human = bundle["human"]
        audit = bundle["audit"]
        finalization = bundle["finalization"]
        review_items = active_technical_review_items(
            package, bundle["artifacts"].review_package_json
        )
        titles = identity_title_map(package)
        ready_candidates = (
            {
                (str(item.get("source_type") or ""), str(item.get("source_id") or ""))
                for item in finalization.get("revision_candidates") or []
            }
            if finalization is not None
            and (finalization.get("stage13_gate") or {}).get("stage13_ready")
            else set()
        )
        issue_records = {
            (str(item.get("source_type")), str(item.get("source_id"))): item
            for item in human.get("issue_decisions") or []
        }
        rationale_by_id = {
            str(item.get("issue_id")): _text(item.get("rationale"))
            for item in audit.get("issues") or []
        }
        rationale_by_id.update(
            {
                str(item.get("finding_id")): _text(item.get("rationale"))
                for item in audit.get("semantic_findings") or []
            }
        )
        grouped: dict[str, list[ReviewItemDTO]] = {key: [] for key in ACTIONABILITY_META}
        scoring_matrix = list(package.get("scoring_matrix") or [])
        for item in review_items:
            key = _actionability(item)
            action = item.get("machine_recommended_action") or {}
            decision = issue_records.get((str(item.get("source_type")), str(item.get("source_id")))) or {}
            requirement_ids = {str(value) for value in item.get("requirement_ids") or []}
            score_impacts = [
                score for score in scoring_matrix
                if str(item.get("source_id")) in (score.get("finding_refs") or [])
                or requirement_ids.intersection(str(value) for value in score.get("requirement_ids") or [])
            ]
            grouped[key].append(
                ReviewItemDTO(
                    title=user_facing_text(_display_title(item), titles)
                    or "待人工审核事项",
                    actionability=key,
                    severity=str(item.get("machine_severity") or "uncertain"),
                    release_blocking=bool(item.get("release_blocking")),
                    requirement_text=user_facing_text(item.get("requirement_text"), titles),
                    current_response=user_facing_text(
                        item.get("current_response_excerpt"), titles
                    ),
                    machine_issue=user_facing_text(
                        item.get("machine_issue_summary"), titles
                    )
                    or "待人工审核事项",
                    machine_rationale=user_facing_text(
                        rationale_by_id.get(str(item.get("source_id"))), titles
                    ),
                    recommendation=user_facing_text(
                        action.get("text") if isinstance(action, dict) else action,
                        titles,
                    ),
                    response_location=_user_location(
                        item.get("current_response_location"), titles
                    ),
                    revision_locations=[
                        location
                        for location in (
                            _user_location(value, titles)
                            for value in item.get("recommended_revision_locations") or []
                        )
                        if location is not None
                    ],
                    required_facts=_strings(item.get("required_fact_refs")),
                    required_materials=_strings(item.get("required_material_refs")),
                    scoring_impacts=[
                        ReviewScoreImpactDTO(
                            title=str(score.get("criteria_title") or "评分细则"),
                            max_score=_number(score.get("max_score")),
                            assessment_status=str(score.get("assessment_status") or "pending"),
                            score_range=_score_range(score.get("score_range")),
                            trace_id=str(score.get("score_item_id") or ""),
                        )
                        for score in score_impacts
                    ],
                    current_decision=str(decision.get("decision") or "pending"),
                    rejection_reason=_text(decision.get("rejection_reason")),
                    revision_instruction=_text(decision.get("revision_instruction")),
                    optional_comment=_text(decision.get("optional_comment")),
                    use_machine_location=bool(decision.get("use_machine_location", True)),
                    location_override=decision.get("location_override"),
                    location_override_reason=_text(decision.get("location_override_reason")),
                    risk_acceptance_ref=_text(decision.get("risk_acceptance_ref")),
                    updated_at=_text(decision.get("updated_at")),
                    change_history=list(decision.get("change_history") or []),
                    trace_id=str(item.get("source_id") or ""),
                    source_type=str(item.get("source_type") or ""),
                    confidence=_text(item.get("confidence")),
                    machine_revision_available=bool(
                        accepted_machine_revision_instruction(item)
                    ),
                    revision_candidate_ready=(
                        str(item.get("source_type") or ""),
                        str(item.get("source_id") or ""),
                    )
                    in ready_candidates,
                )
            )
        groups = [
            ReviewGroupDTO(key=key, label=meta[0], description=meta[1], items=grouped[key])
            for key, meta in ACTIONABILITY_META.items()
        ]
        queue_by_ref = {
            (str(item.get("source_type")), str(item.get("source_id"))): item
            for item in package.get("fact_material_queue") or []
        }
        def related_requirements(source_ids: list[str], kind: str) -> list[str]:
            values: list[str] = []
            for review_item in review_items:
                refs = (
                    (review_item.get("required_fact_refs") or [])
                    + (review_item.get("required_confirmation_refs") or [])
                    + (review_item.get("deferred_confirmation_refs") or [])
                    if kind == "fact"
                    else (review_item.get("required_material_refs") or [])
                )
                if any(source_id in refs for source_id in source_ids) or (
                    kind == "fact"
                    and str(review_item.get("source_id") or "") in source_ids
                ):
                    values.extend(str(value) for value in review_item.get("requirement_ids") or [])
            return list(dict.fromkeys(values))

        facts: list[FactConfirmationDTO] = []
        for semantic_group in confirmation_groups(
            package, human, bundle["artifacts"].review_package_json
        ):
            record = semantic_group["representative"]
            records = semantic_group["records"]
            source_type = str(record.get("source_type"))
            source_id = str(record.get("source_id"))
            source_ids = [str(value.get("source_id") or "") for value in records]
            queue = semantic_group["queue"] or queue_by_ref.get((source_type, source_id)) or {}
            linked_review = next(
                (item for item in review_items if str(item.get("source_id")) in source_ids), {}
            )
            raw_title = str(
                queue.get("display_title")
                or linked_review.get("display_title")
                or "待确认信息（名称未记录）"
            )
            locations = [
                location
                for location in (
                    _user_location(value, titles)
                    for value in semantic_group["locations"]
                )
                if location is not None
            ]
            facts.append(
                FactConfirmationDTO(
                    title=confirmation_display_title(
                        semantic_group, raw_title, titles
                    ),
                    description=user_facing_text(
                        queue.get("description")
                        or linked_review.get("machine_issue_summary"),
                        titles,
                    ),
                    source_type=source_type,
                    trace_id=source_id,
                    confirmation_status=str(semantic_group["status"]),
                    confirmed_value=record.get("confirmed_value"),
                    source_description=_text(record.get("source_description")),
                    confirmation_basis=_text(record.get("confirmation_basis")),
                    confirmation_note=_text(record.get("confirmation_note")),
                    rejection_reason=_text(record.get("rejection_reason")),
                    required=bool(queue.get("required", True)),
                    related_requirement_ids=related_requirements(source_ids, "fact"),
                    response_location=(
                        locations[0]
                        if locations
                        else _user_location(
                            linked_review.get("current_response_location"), titles
                        )
                    ),
                    response_locations=locations,
                    affected_location_count=int(semantic_group["occurrence_count"]),
                    updated_at=_text(record.get("updated_at")),
                    change_history=list(record.get("change_history") or []),
                )
            )

        materials: list[MaterialVerificationDTO] = []
        for record in human.get("material_verifications") or []:
            source_type = str(record.get("source_type"))
            source_id = str(record.get("material_or_evidence_ref"))
            queue = queue_by_ref.get((source_type, source_id)) or {}
            upstream_status = str(record.get("upstream_status") or queue.get("status") or "pending")
            materials.append(
                MaterialVerificationDTO(
                    title=user_facing_text(
                        queue.get("display_title") or "待核验材料", titles
                    )
                    or "待核验材料",
                    description=user_facing_text(queue.get("description"), titles),
                    source_type=source_type,
                    trace_id=source_id,
                    upstream_status=upstream_status,
                    upstream_status_label=FACT_STATUS_LABELS.get(upstream_status, upstream_status),
                    verification_status=str(record.get("verification_status") or "pending"),
                    can_verify=(
                        upstream_status in {"candidate", "candidate_found", "provided", "available", "verified"}
                        or bool(record.get("candidate_reference") or record.get("file_reference"))
                    ),
                    material_name=_text(record.get("material_name")),
                    subject_match=record.get("subject_match"),
                    validity_date=_text(record.get("validity_date")),
                    verification_note=_text(record.get("verification_note")),
                    candidate_reference=_text(record.get("candidate_reference")),
                    file_reference=_text(record.get("file_reference")),
                    required=bool(queue.get("required", True)),
                    related_requirement_ids=related_requirements([source_id], "material"),
                    updated_at=_text(record.get("updated_at")),
                    change_history=list(record.get("change_history") or []),
                )
            )
        return ReviewWorkspaceDTO(
            project=self._project_summary(bundle),
            reviewed_version=self._version_refs(bundle)[1],
            progress=self._review_progress(bundle),
            actionability_counts={key: len(items) for key, items in grouped.items()},
            groups=groups,
            fact_confirmations=facts,
            material_verifications=materials,
            lineage=self._lineage(bundle),
        )

    def requirements(self, project_id: str) -> RequirementListDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        trace = self._requirement_trace_index(bundle)
        rows = [
            self._requirement_row(item, trace)
            for item in bundle["package"].get("response_matrix") or []
        ]
        counts = bundle["package"]["summary"].get("response_status_counts") or {}
        return RequirementListDTO(
            project_id=project_id,
            reviewed_version=self._version_refs(bundle)[1],
            summary={str(key): int(value) for key, value in counts.items()},
            requirements=rows,
        )

    def requirement_detail(self, project_id: str, requirement_id: str) -> RequirementDetailDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        item = next(
            (
                value
                for value in bundle["package"].get("response_matrix") or []
                if str(value.get("requirement_id") or "") == requirement_id
            ),
            None,
        )
        if item is None:
            raise RequirementNotFoundError(f"requirement is not available: {requirement_id}")
        trace = self._requirement_trace_index(bundle)
        review_items = trace["reviews_by_requirement"].get(requirement_id, [])
        score_items = [
            trace["scores_by_id"][score_id]
            for score_id in item.get("score_item_refs") or []
            if score_id in trace["scores_by_id"]
        ]
        fact_dependencies, material_dependencies = self._requirement_dependencies(
            item, review_items, score_items, trace
        )
        findings: list[RequirementFindingDTO] = []
        for finding_id in item.get("finding_refs") or []:
            finding = trace["findings_by_id"].get(str(finding_id))
            if not finding:
                continue
            subject = finding.get("subject")
            subject_data = subject if isinstance(subject, dict) else {}
            recommended = finding.get("recommended_action")
            recommended_data = recommended if isinstance(recommended, dict) else {}
            findings.append(
                RequirementFindingDTO(
                    title=str(subject_data.get("display_name") or finding.get("subject") or "机器审核发现"),
                    summary=str(finding.get("summary") or "机器审核记录"),
                    rationale=_text(finding.get("rationale")),
                    severity=_text(finding.get("severity") or finding.get("suggested_severity")),
                    release_blocking=bool(
                        finding.get("release_blocking") or finding.get("suggested_release_blocking")
                    ),
                    verdict=_text(finding.get("verdict") or finding.get("decision")),
                    recommendation=_text(recommended_data.get("text")) or _text(recommended),
                    requires_human_review=bool(finding.get("requires_human_review")),
                    trace_id=str(finding_id),
                    source_type="audit_issue" if finding.get("issue_id") else "semantic_finding",
                )
            )
        scoring_impacts = [self._requirement_score_impact(score) for score in score_items]
        review_states = [self._requirement_review_state(value, trace) for value in review_items]
        return RequirementDetailDTO(
            project_id=project_id,
            reviewed_version=self._version_refs(bundle)[1],
            requirement=self._requirement_row(item, trace),
            findings=findings,
            scoring_impacts=scoring_impacts,
            fact_dependencies=fact_dependencies,
            material_dependencies=material_dependencies,
            review_states=review_states,
        )

    def _requirement_trace_index(self, bundle: dict[str, Any]) -> dict[str, Any]:
        package = bundle["package"]
        reviews_by_requirement: dict[str, list[dict[str, Any]]] = {}
        for review in package.get("review_items") or []:
            for requirement_id in review.get("requirement_ids") or []:
                reviews_by_requirement.setdefault(str(requirement_id), []).append(review)
        findings_by_id: dict[str, dict[str, Any]] = {}
        for finding in (bundle["audit"].get("issues") or []) + (bundle["audit"].get("semantic_findings") or []):
            trace_id = str(finding.get("issue_id") or finding.get("finding_id") or "")
            if trace_id:
                findings_by_id[trace_id] = finding
        issue_decisions = {
            (str(value.get("source_type") or ""), str(value.get("source_id") or "")): value
            for value in bundle["human"].get("issue_decisions") or []
        }
        fact_status = {
            str(value.get("source_id") or ""): str(value.get("confirmation_status") or "pending")
            for value in bundle["human"].get("fact_confirmations") or []
        }
        material_status = {
            str(value.get("material_or_evidence_ref") or ""): (
                str(value.get("verification_status"))
                if value.get("verification_status") != "pending"
                else str(value.get("upstream_status") or "pending")
            )
            for value in bundle["human"].get("material_verifications") or []
        }
        queue_by_id = {
            str(value.get("source_id") or ""): {
                **value,
                "status": fact_status.get(str(value.get("source_id") or ""), material_status.get(str(value.get("source_id") or ""), value.get("status"))),
            }
            for value in package.get("fact_material_queue") or []
            if value.get("source_id")
        }
        evidence_queries_by_type = {
            str(value.get("evidence_type") or ""): value
            for value in bundle.get("evidence_query_plan", {}).get("queries") or []
            if value.get("evidence_type")
        }
        return {
            "reviews_by_requirement": reviews_by_requirement,
            "findings_by_id": findings_by_id,
            "scores_by_id": {
                str(value.get("score_item_id") or ""): value
                for value in package.get("scoring_matrix") or []
            },
            "queue_by_id": queue_by_id,
            "evidence_queries_by_type": evidence_queries_by_type,
            "issue_decisions": issue_decisions,
        }

    def _requirement_row(self, item: dict[str, Any], trace: dict[str, Any]) -> RequirementResponseDTO:
        requirement_id = str(item.get("requirement_id") or "")
        review_items = trace["reviews_by_requirement"].get(requirement_id, [])
        score_items = [
            trace["scores_by_id"][score_id]
            for score_id in item.get("score_item_refs") or []
            if score_id in trace["scores_by_id"]
        ]
        fact_dependencies, material_dependencies = self._requirement_dependencies(
            item, review_items, score_items, trace
        )
        source_parts: list[str] = []
        for field in ("source_section", "source_clause"):
            value = item.get(field)
            source_parts.extend(_strings(value) if isinstance(value, list) else ([str(value)] if value else []))
        page_label = _source_page_label(item.get("source_page"))
        if page_label:
            source_parts.append(page_label)
        responses = item.get("response_locations") or []
        primary_response = _response_excerpt(item.get("primary_response"))
        if not primary_response:
            excerpts = item.get("response_excerpts") or []
            primary_response = _response_excerpt(excerpts[0]) if excerpts else None
        primary_value = item.get("primary_response")
        primary_data = primary_value if isinstance(primary_value, dict) else {}
        primary_location = _location(primary_data.get("location"))
        actions = [_actionability(value) for value in review_items]
        actionability = next(
            (key for key in ("must_decide", "verification", "no_action", "informational") if key in actions),
            None,
        )
        decisions = [
            trace["issue_decisions"].get(
                (str(review.get("source_type") or ""), str(review.get("source_id") or "")),
                {},
            ).get("decision", "pending")
            for review in review_items
        ]
        requirement_text = str(item.get("requirement_text") or "")
        title = re.sub(r"^\s*\d+[.、]\s*", "", requirement_text).strip() or "招标要求"
        sections = _strings(item.get("source_section"))
        clauses = _strings(item.get("source_clause"))
        return RequirementResponseDTO(
            title=title[:160],
            requirement_text=requirement_text,
            mandatory=bool(item.get("mandatory_response")),
            category=(sections[-1] if sections else (clauses[-1] if clauses else None)),
            source_document=_text(item.get("source_file")),
            source_location=" / ".join(dict.fromkeys(source_parts)) or None,
            source_reference=_source_reference(item),
            response_status=str(item.get("response_status") or "uncertain"),
            current_response=primary_response,
            primary_response_location=primary_location,
            response_locations=[
                location for location in (_location(value) for value in responses) if location is not None
            ],
            related_finding_count=len(item.get("finding_refs") or []),
            related_score_count=len(item.get("score_item_refs") or []),
            review=RequirementReviewSummaryDTO(
                actionability=actionability,
                pending_count=sum(1 for value in decisions if value == "pending"),
                completed_count=sum(1 for value in decisions if value != "pending"),
                trace_ids=[str(value.get("source_id") or "") for value in review_items],
            ),
            fact_dependency_count=len(fact_dependencies),
            material_dependency_count=sum(
                1 for value in material_dependencies if value.kind in {"evidence_query", "material"}
            ),
            query_not_executed_count=sum(
                1
                for value in material_dependencies
                if value.kind == "evidence_query" and value.status == "query_not_executed"
            ),
            trace_id=requirement_id,
        )

    @staticmethod
    def _requirement_score_impact(item: dict[str, Any]) -> RequirementScoreImpactDTO:
        pending_evidence = []
        for value in item.get("pending_evidence") or []:
            if not isinstance(value, dict):
                continue
            status = str(value.get("status") or "pending")
            pending_evidence.append(
                RequirementPendingEvidenceDTO(
                    evidence_type=str(value.get("evidence_type") or "evidence"),
                    status=status,
                    status_label=FACT_STATUS_LABELS.get(status, status),
                    reason=_text(value.get("reason")),
                )
            )
        return RequirementScoreImpactDTO(
            title=str(item.get("criteria_title") or "评分细则"),
            criteria_text=str(item.get("criteria_text") or ""),
            max_score=_number(item.get("max_score")),
            assessment_status=str(item.get("assessment_status") or "pending"),
            score_range=_score_range(item.get("score_range")),
            pending_conditions=_strings(item.get("pending_conditions")),
            pending_evidence=pending_evidence,
            potential_gain=_score_range(item.get("potential_gain_range") or item.get("potential_gain")),
            trace_id=str(item.get("score_item_id") or ""),
        )

    @staticmethod
    def _dependency_from_queue(item: dict[str, Any]) -> RequirementDependencyDTO:
        trace_id = str(item.get("source_id") or "")
        status = str(item.get("status") or "pending")
        if trace_id.startswith("FACT-"):
            kind = "project_fact"
        elif trace_id.startswith("FCA-"):
            kind = "fact_confirmation"
        elif trace_id.startswith("KBQ-"):
            kind = "evidence_query"
        else:
            kind = "material"
        return RequirementDependencyDTO(
            kind=kind,
            title=str(item.get("display_title") or item.get("description") or trace_id),
            status=status,
            status_label=FACT_STATUS_LABELS.get(status, status),
            description=_text(item.get("description")),
            required=bool(item.get("required", True)),
            trace_id=trace_id,
        )

    def _requirement_dependencies(
        self,
        item: dict[str, Any],
        review_items: list[dict[str, Any]],
        score_items: list[dict[str, Any]],
        trace: dict[str, Any],
    ) -> tuple[list[RequirementDependencyDTO], list[RequirementDependencyDTO]]:
        fact_refs = list(item.get("confirmation_refs") or [])
        material_refs: list[str] = []
        for review in review_items:
            fact_refs.extend(review.get("required_fact_refs") or [])
            fact_refs.extend(review.get("required_confirmation_refs") or [])
            material_refs.extend(review.get("required_material_refs") or [])
        evidence_types: list[str] = []
        pending_status_by_type: dict[str, str] = {}
        for score in score_items:
            evidence_types.extend(str(value) for value in score.get("evidence_requirements") or [])
            for pending in score.get("pending_evidence") or []:
                if isinstance(pending, dict) and pending.get("evidence_type"):
                    evidence_type = str(pending["evidence_type"])
                    pending_status_by_type[evidence_type] = str(pending.get("status") or "pending")
        for evidence_type in dict.fromkeys(evidence_types):
            query = trace["evidence_queries_by_type"].get(evidence_type)
            if query and query.get("query_id"):
                material_refs.append(str(query["query_id"]))
        fact_dependencies = [
            self._dependency_from_queue(trace["queue_by_id"][ref])
            for ref in dict.fromkeys(str(value) for value in fact_refs)
            if ref in trace["queue_by_id"]
        ]
        material_dependencies: list[RequirementDependencyDTO] = []
        for evidence_type in dict.fromkeys(evidence_types):
            query = trace["evidence_queries_by_type"].get(evidence_type, {})
            status = pending_status_by_type.get(evidence_type, "pending")
            material_dependencies.append(
                RequirementDependencyDTO(
                    kind="evidence_requirement",
                    title=str(query.get("query") or evidence_type),
                    status=status,
                    status_label=FACT_STATUS_LABELS.get(status, status),
                    description=_text(query.get("purpose")),
                    trace_id=evidence_type,
                )
            )
        material_dependencies.extend(
            self._dependency_from_queue(trace["queue_by_id"][ref])
            for ref in dict.fromkeys(str(value) for value in material_refs)
            if ref in trace["queue_by_id"]
        )
        return fact_dependencies, material_dependencies

    @staticmethod
    def _requirement_review_state(item: dict[str, Any], trace: dict[str, Any]) -> RequirementReviewStateDTO:
        source_type = str(item.get("source_type") or "")
        source_id = str(item.get("source_id") or "")
        decision = trace["issue_decisions"].get((source_type, source_id), {})
        action = item.get("machine_recommended_action")
        action_data = action if isinstance(action, dict) else {}
        return RequirementReviewStateDTO(
            title=_display_title(item),
            actionability=_actionability(item),
            severity=str(item.get("machine_severity") or "uncertain"),
            release_blocking=bool(item.get("release_blocking")),
            machine_recommendation=_text(action_data.get("text")) or _text(action),
            current_decision=_text(decision.get("decision")) or "pending",
            revision_instruction=_text(decision.get("revision_instruction")),
            risk_acceptance_ref=_text(decision.get("risk_acceptance_ref")),
            revision_locations=[
                location
                for location in (_location(value) for value in item.get("recommended_revision_locations") or [])
                if location is not None
            ],
            trace_id=source_id,
        )

    def scoring(self, project_id: str) -> ScoringSummaryDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        package = bundle["package"]
        items: list[ScoringItemDTO] = []
        for item in package.get("scoring_matrix") or []:
            potential = item.get("potential_gain_range") or item.get("potential_gain") or {}
            items.append(
                ScoringItemDTO(
                    title=str(item.get("criteria_title") or "评分细则"),
                    criteria_text=str(item.get("criteria_text") or ""),
                    max_score=_number(item.get("max_score")),
                    assessment_status=str(item.get("assessment_status") or "pending"),
                    confirmed_score=(
                        _number(item.get("confirmed_score")) if item.get("confirmed_score") is not None else None
                    ),
                    estimated_score=(
                        _number(item.get("estimated_score")) if item.get("estimated_score") is not None else None
                    ),
                    score_range=_score_range(item.get("score_range")),
                    satisfied_conditions=_strings(item.get("satisfied_conditions")),
                    missing_conditions=_strings(item.get("missing_conditions")),
                    pending_conditions=_strings(item.get("pending_conditions")),
                    deduction_reasons=_strings(item.get("deduction_reasons")),
                    potential_gain=_score_range(potential),
                    response_locations=[
                        location
                        for location in (_location(value) for value in item.get("response_locations") or [])
                        if location is not None
                    ],
                    trace_id=str(item.get("score_item_id") or ""),
                )
            )
        counts = Counter(item.assessment_status for item in items)
        return ScoringSummaryDTO(
            project_id=project_id,
            reviewed_version=self._version_refs(bundle)[1],
            headline=self._scoring_headline(package["summary"]),
            item_counts={key: int(counts.get(key, 0)) for key in ("confirmed", "estimated", "pending")},
            items=items,
        )

    def facts_materials(self, project_id: str) -> FactMaterialSummaryDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        package = bundle["package"]
        human = bundle["human"]
        base = f"/projects/{quote(project_id, safe='')}"
        section_paths: dict[str, str] = {}
        for chapter in bundle["fulltext"].get("chapters") or []:
            chapter_title = str(chapter.get("chapter_title") or "").strip()
            for section in chapter.get("sections") or []:
                section_id = str(section.get("section_id") or "")
                section_title = str(section.get("heading") or "").strip()
                if section_id and section_title:
                    section_paths[section_id] = " > ".join(value for value in (chapter_title, section_title) if value)
        confirmation_locations: dict[str, list[str]] = {}
        for confirmation in bundle["audit"].get("confirmation_register") or []:
            confirmation_id = str(confirmation.get("confirmation_id") or "")
            locations = [
                section_paths.get(str(location.get("section_id") or ""), "")
                for location in confirmation.get("affected_locations") or []
            ]
            confirmation_locations[confirmation_id] = list(dict.fromkeys(value for value in locations if value))
        latest_status = {
            str(item.get("source_id") or ""): str(item.get("confirmation_status") or "pending")
            for item in human.get("fact_confirmations") or []
        }
        latest_status.update(
            {
                str(item.get("material_or_evidence_ref") or ""): (
                    str(item.get("verification_status"))
                    if item.get("verification_status") != "pending"
                    else str(item.get("upstream_status") or "pending")
                )
                for item in human.get("material_verifications") or []
            }
        )
        rows: list[FactMaterialItemDTO] = []
        for item in package.get("fact_material_queue") or []:
            source_id = str(item.get("source_id") or "")
            status = latest_status.get(source_id, str(item.get("status") or "pending"))
            category = str(item.get("queue_type") or "verification")
            raw_title = str(item.get("display_title") or item.get("description") or "待核验事项")
            semantic_resolved = re.search(r"PH-FIELD-[A-Z0-9-]+", raw_title, flags=re.IGNORECASE) is None
            title = raw_title if semantic_resolved else "待确认信息（名称未记录）"
            description = str(item.get("description") or "")
            if not semantic_resolved:
                description = "该占位字段没有保存可读的业务名称，请到关联章节核对并确认。"
            is_confirmation = category == "confirmation_required"
            rows.append(
                FactMaterialItemDTO(
                    category=category,
                    title=title,
                    status=status,
                    status_label=FACT_STATUS_LABELS.get(status, status),
                    description=description,
                    required=bool(item.get("required")),
                    related_task_count=len(item.get("related_task_ids") or []),
                    trace_id=source_id,
                    related_locations=confirmation_locations.get(source_id, []),
                    source_label="人工审核",
                    action_label="确认" if is_confirmation else "填写信息" if category == "missing_project_fact" else "查看 / 补充材料",
                    action_url=(
                        f"{base}/review?item={quote(source_id, safe='')}"
                        if is_confirmation
                        else f"{base}/writing-preparation?item={quote(source_id, safe='')}"
                    ),
                    semantic_resolved=semantic_resolved,
                )
            )
        counts = Counter(item.status for item in rows)
        legend = {
            str(key): str(value)
            for key, value in (package.get("fact_material_status_legend") or {}).items()
        }
        return FactMaterialSummaryDTO(
            project_id=project_id,
            reviewed_version=self._version_refs(bundle)[1],
            headline=self._facts_headline(bundle),
            status_legend=legend,
            status_counts=dict(counts),
            items=rows,
        )

    def versions(self, project_id: str) -> VersionSummaryDTO:
        bundle = self._bundle(self.resolver.resolve(project_id))
        current, reviewed = self._version_refs(bundle)
        package = bundle["package"]
        human = bundle["human"]
        finalization = bundle["finalization"]
        inspected = bundle["inspected"]
        lineage = package["source_lineage"]
        entries = [
            VersionLineageEntryDTO(
                kind="fulltext_commit",
                label="全文冻结",
                status=str(bundle["commit"].get("status") or "committed"),
                generated_at=_text(bundle["commit"].get("committed_at")),
                content_sha256=_text(
                    (bundle["audit"].get("source_lineage") or {}).get("committed_fulltext", {}).get("content_sha256")
                ),
                artifact_sha256=_text(bundle["commit"].get("technical_fulltext_json_sha256")),
                internal_id=_text(bundle["commit"].get("document_id")),
            ),
            VersionLineageEntryDTO(
                kind="machine_audit",
                label="机器审核",
                status=str(bundle["audit"].get("audit_result") or "completed"),
                generated_at=_text(bundle["audit"].get("generated_at")),
                content_sha256=reviewed.content_sha256,
                artifact_sha256=_text(lineage["final_stage11_audit"].get("file_sha256")),
                internal_id=_text(bundle["audit"].get("document_id")),
            ),
            VersionLineageEntryDTO(
                kind="review_package",
                label="人工审核包",
                status=str(package["artifact_identity"].get("status") or "assembled"),
                generated_at=_text(package.get("generated_at")),
                content_sha256=reviewed.content_sha256,
                artifact_sha256=_text(human.get("review_package_ref", {}).get("sha256")),
                internal_id=_text(package["artifact_identity"].get("review_package_id")),
            ),
            VersionLineageEntryDTO(
                kind="human_review",
                label="人工审核",
                status="pending" if human.get("summary", {}).get("issue_decision_counts", {}).get("pending") else "completed",
                generated_at=_text(human.get("updated_at")),
                content_sha256=reviewed.content_sha256,
                artifact_sha256=_text(inspected["human"].get("human_review_sha256")),
                internal_id=_text(human.get("artifact_identity", {}).get("human_review_id")),
                current=True,
            ),
        ]
        if finalization is not None:
            entries.append(
                VersionLineageEntryDTO(
                    kind="review_finalization",
                    label="审核汇总",
                    status="not_ready" if not finalization["stage13_gate"].get("stage13_ready") else "completed",
                    generated_at=_text(finalization.get("generated_at")),
                    content_sha256=reviewed.content_sha256,
                    artifact_sha256=_text(inspected["finalization"].get("finalization_sha256")),
                    internal_id=_text(finalization.get("artifact_identity", {}).get("finalization_id")),
                )
            )
        return VersionSummaryDTO(
            project_id=project_id,
            current_revision=current,
            current_reviewed_version=reviewed,
            entries=entries,
        )

    def _bundle(self, artifacts: ProjectArtifacts) -> dict[str, Any]:
        try:
            inspected: dict[str, Any] = {
                "audit": inspect_technical_fulltext_audit(artifacts.audit_json),
                "scoring": inspect_technical_fulltext_scoring_assessment(artifacts.scoring_json),
                "package": inspect_technical_fulltext_review_package(artifacts.review_package_json),
                "human": inspect_technical_fulltext_human_review(artifacts.human_review_json),
                "finalization": (
                    inspect_technical_fulltext_review_finalization(artifacts.finalization_json)
                    if artifacts.finalization_json is not None
                    else None
                ),
            }
        except Exception as exc:
            if "stale" in str(exc).lower() or "changed" in str(exc).lower():
                raise LineageConflictError(str(exc)) from exc
            raise WebProjectionError(f"canonical artifact validation failed: {exc}") from exc
        package = _load_json(artifacts.review_package_json)
        human = _load_json(artifacts.human_review_json)
        if artifacts.finalization_json is None:
            summary = human.get("summary") or {}
            pending = any(
                int((summary.get(key) or {}).get("pending") or 0) > 0
                for key in (
                    "issue_decision_counts",
                    "fact_confirmation_counts",
                    "material_verification_counts",
                )
            )
            if not pending:
                raise WebProjectionError(
                    "review finalization is missing after Human Review completed"
                )
        evidence_query_plan = _load_optional_referenced_json(
            (package.get("source_lineage") or {}).get("local_evidence_query_plan"),
            self.resolver.outputs_root,
        )
        return {
            "artifacts": artifacts,
            "fulltext": _load_json(artifacts.fulltext_json),
            "commit": _load_json(artifacts.commit_json),
            "audit": _load_json(artifacts.audit_json),
            "scoring": _load_json(artifacts.scoring_json),
            "package": package,
            "human": human,
            "finalization": (
                _load_json(artifacts.finalization_json)
                if artifacts.finalization_json is not None
                else None
            ),
            "inspected": inspected,
            "evidence_query_plan": evidence_query_plan,
        }

    def _project_summary(self, bundle: dict[str, Any]) -> ProjectSummaryDTO:
        package = bundle["package"]
        human_summary = bundle["human"].get("summary") or {}
        counts = self._actionability_counts(bundle)
        progress = self._review_progress(bundle)
        current, reviewed = self._version_refs(bundle)
        source_names = [
            str(item.get("source_file") or "").strip()
            for item in package.get("response_matrix") or []
            if str(item.get("source_file") or "").strip()
        ]
        source_name = next(iter(dict.fromkeys(source_names)), None)
        project_name = Path(source_name).stem if source_name else bundle["artifacts"].project_id
        pending_decisions = progress.issue_decisions_total - progress.issue_decisions_completed
        pending_facts = progress.fact_confirmations_total - progress.fact_confirmations_completed
        pending_materials = int((human_summary.get("material_verification_counts") or {}).get("pending") or 0)
        must_decide = counts["must_decide"]
        next_action = (
            f"先完成 {must_decide} 项必须决定的审核事项"
            if must_decide
            else "继续完成事实与材料核验"
        )
        return ProjectSummaryDTO(
            project_id=bundle["artifacts"].project_id,
            project_name=project_name,
            source_document_name=source_name,
            current_revision=current,
            current_reviewed_version=reviewed,
            workflow_status="人工审核中" if pending_decisions or pending_facts or pending_materials else "审核已完成",
            release_status="技术正文待生成" if not bundle["audit"].get("release_ready") else "技术正文可生成",
            next_action=next_action,
            blocking_count=must_decide,
            pending_decision_count=pending_decisions,
            pending_verification_count=pending_facts + pending_materials,
        )

    @staticmethod
    def _version_refs(bundle: dict[str, Any]) -> tuple[VersionRefDTO, VersionRefDTO]:
        audit_lineage = bundle["audit"].get("source_lineage") or {}
        committed = audit_lineage.get("committed_fulltext") or {}
        reviewed_sha = str(bundle["package"].get("reviewed_content_sha256") or "")
        current = VersionRefDTO(
            version_id=str(bundle["commit"].get("document_id") or bundle["artifacts"].project_id),
            label="当前冻结全文",
            content_sha256=_text(committed.get("content_sha256")),
            artifact_sha256=_text(bundle["commit"].get("technical_fulltext_json_sha256")),
            status=str(bundle["commit"].get("status") or "committed"),
        )
        reviewed = VersionRefDTO(
            version_id=f"reviewed:{reviewed_sha[:16]}",
            label="当前审核正文",
            content_sha256=reviewed_sha or None,
            artifact_sha256=_text((bundle["package"].get("source_lineage") or {}).get("audited_response_content", {}).get("file_sha256")),
            status="under_review",
        )
        return current, reviewed

    @staticmethod
    def _actionability_counts(bundle: dict[str, Any]) -> dict[str, int]:
        counts = Counter(
            _actionability(item)
            for item in active_technical_review_items(
                bundle["package"], bundle["artifacts"].review_package_json
            )
        )
        return {key: int(counts.get(key, 0)) for key in ACTIONABILITY_META}

    @staticmethod
    def _review_progress(bundle: dict[str, Any]) -> ReviewProgressDTO:
        summary = bundle["human"].get("summary") or {}
        active_keys = {
            (str(item.get("source_type") or ""), str(item.get("source_id") or ""))
            for item in active_technical_review_items(
                bundle["package"], bundle["artifacts"].review_package_json
            )
        }
        issue_records = [
            item
            for item in bundle["human"].get("issue_decisions") or []
            if (str(item.get("source_type") or ""), str(item.get("source_id") or ""))
            in active_keys
        ]
        issue_total = len(issue_records)
        issue_pending = sum(item.get("decision") == "pending" for item in issue_records)
        fact_groups = confirmation_groups(
            bundle["package"],
            bundle["human"],
            bundle["artifacts"].review_package_json,
        )
        fact_total = len(fact_groups)
        fact_pending = sum(item["status"] == "pending" for item in fact_groups)
        material_total = int(summary.get("material_verification_count") or 0)
        material_pending = int((summary.get("material_verification_counts") or {}).get("pending") or 0)
        return ReviewProgressDTO(
            issue_decisions_total=issue_total,
            issue_decisions_completed=issue_total - issue_pending,
            fact_confirmations_total=fact_total,
            fact_confirmations_completed=fact_total - fact_pending,
            material_verifications_total=material_total,
            material_verifications_completed=material_total - material_pending,
        )

    @staticmethod
    def _scoring_headline(summary: dict[str, Any]) -> ScoringHeadlineDTO:
        return ScoringHeadlineDTO(
            technical_score_max=_number(summary.get("technical_score_max")),
            confirmed=_number(summary.get("confirmed_score", summary.get("confirmed_score_subtotal"))),
            estimated_range=_score_range(summary.get("estimated_score_range")),
            pending_exposure=_number(summary.get("pending_score_max")),
            potential_gain=_score_range(summary.get("potential_gain_range")),
        )

    @staticmethod
    def _facts_headline(bundle: dict[str, Any]) -> FactMaterialHeadlineDTO:
        human = bundle["human"]
        human_summary = human.get("summary") or {}
        fact_records = list(human.get("fact_confirmations") or [])
        material_records = list(human.get("material_verifications") or [])
        return FactMaterialHeadlineDTO(
            missing_project_facts=sum(
                1 for item in fact_records
                if item.get("source_type") == "FACT" and item.get("confirmation_status") != "confirmed"
            ),
            confirmations_required=int((human_summary.get("fact_confirmation_counts") or {}).get("pending") or 0),
            queries_not_executed=sum(1 for item in material_records if item.get("upstream_status") == "query_not_executed"),
            candidate_materials=sum(
                1 for item in material_records
                if item.get("upstream_status") != "query_not_executed" and item.get("verification_status") == "pending"
            ),
            verified_materials=int((human_summary.get("material_verification_counts") or {}).get("verified") or 0),
        )

    @staticmethod
    def _lineage(bundle: dict[str, Any]) -> LineageInfoDTO:
        package = bundle["package"]
        lineage = package.get("source_lineage") or {}
        return LineageInfoDTO(
            audited_content_sha256=str(package.get("reviewed_content_sha256") or ""),
            audit_sha256=str((lineage.get("final_stage11_audit") or {}).get("file_sha256") or ""),
            scoring_sha256=str((lineage.get("stage11_scoring_assessment") or {}).get("file_sha256") or ""),
            review_package_id=str((package.get("artifact_identity") or {}).get("review_package_id") or ""),
            human_review_id=_text((bundle["human"].get("artifact_identity") or {}).get("human_review_id")),
            finalization_id=(
                _text((bundle["finalization"].get("artifact_identity") or {}).get("finalization_id"))
                if bundle["finalization"] is not None
                else None
            ),
        )

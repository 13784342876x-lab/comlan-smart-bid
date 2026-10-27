from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from app.technical_fulltext_composer import load_json_object, sha256_file
from app.web.dtos import (
    AuditedFulltextDTO,
    AuditedFulltextStatusDTO,
    AuditedFulltextTraceIndexDTO,
    AuditedFulltextVersionDTO,
    FulltextChapterDTO,
    FulltextDependencyTraceDTO,
    FulltextFindingTraceDTO,
    FulltextParagraphDTO,
    FulltextRequirementTraceDTO,
    FulltextReviewTraceDTO,
    FulltextScoreTraceDTO,
    FulltextSectionDTO,
    FulltextTraceRefsDTO,
    RequirementPendingEvidenceDTO,
    ReviewLocationDTO,
    ScoreRangeDTO,
)
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import LineageConflictError, ProjectReadService, _actionability, _display_title


PLACEHOLDER_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
TRACE_FIELDS = ("requirement_ids", "finding_ids", "score_item_ids", "review_item_ids", "fact_refs", "material_refs")


class AuditedFulltextReadService:
    """Project the exact cleaned content and Stage11 paragraph index bound by Stage12."""

    def __init__(self, resolver: ReadOnlyProjectResolver, read_service: ProjectReadService | None = None) -> None:
        self.resolver = resolver
        self.read_service = read_service or ProjectReadService(resolver)

    def get(self, project_id: str) -> AuditedFulltextDTO:
        artifacts = self.resolver.resolve(project_id)
        bundle = self.read_service._bundle(artifacts)
        cleaned = load_json_object(artifacts.cleaned_fulltext_json, label="audited cleaned fulltext")
        package = bundle["package"]
        audit = bundle["audit"]
        human = bundle["human"]
        finalization = bundle["finalization"]
        source_ref = (package.get("source_lineage") or {}).get("audited_response_content") or {}
        cleaned_file_sha = sha256_file(artifacts.cleaned_fulltext_json)
        reviewed_sha = str(package.get("reviewed_content_sha256") or "")
        checks = {
            "audited artifact SHA": (cleaned_file_sha, str(source_ref.get("file_sha256") or "")),
            "audited content SHA": (str(cleaned.get("cleaned_content_sha256") or ""), reviewed_sha),
            "Human Review audited content SHA": (str(human.get("audited_content_sha256") or ""), reviewed_sha),
            "Stage11 source artifact SHA": (str((audit.get("source_fulltext") or {}).get("sha256") or ""), cleaned_file_sha),
        }
        for label, (actual, expected) in checks.items():
            if not actual or actual != expected:
                raise LineageConflictError(f"{label} mismatch; audited fulltext cannot be displayed")

        fulltext = cleaned.get("fulltext") or {}
        normalized = audit.get("normalized_document") or {}
        document_id = str(fulltext.get("document_id") or "")
        if document_id != str((normalized.get("document") or {}).get("document_id") or ""):
            raise LineageConflictError("Stage11 paragraph index is not bound to the audited document")

        section_source: dict[str, dict[str, Any]] = {}
        chapter_source: list[dict[str, Any]] = list(fulltext.get("chapters") or [])
        for chapter in chapter_source:
            for section in chapter.get("sections") or []:
                section_source[str(section.get("section_id") or "")] = section
        paragraphs_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
        paragraph_by_id: dict[str, dict[str, Any]] = {}
        for paragraph in normalized.get("paragraphs") or []:
            section_id = str(paragraph.get("section_id") or "")
            paragraph_id = str(paragraph.get("paragraph_id") or "")
            if section_id not in section_source or not paragraph_id:
                raise LineageConflictError("Stage11 paragraph index contains an unknown audited section")
            paragraphs_by_section[section_id].append(paragraph)
            paragraph_by_id[paragraph_id] = paragraph

        section_refs = {section_id: _empty_refs() for section_id in section_source}
        paragraph_refs = {paragraph_id: _empty_refs() for paragraph_id in paragraph_by_id}

        def attach(field: str, trace_id: str, locations: Iterable[dict[str, Any]]) -> None:
            for location in locations:
                paragraph_id = str(location.get("paragraph_id") or "")
                section_id = str(location.get("section_id") or "")
                paragraph_index = location.get("paragraph_index")
                if paragraph_id and paragraph_id in paragraph_refs:
                    paragraph_refs[paragraph_id][field].add(trace_id)
                    continue
                if section_id and paragraph_index is not None:
                    match = next(
                        (item for item in paragraphs_by_section.get(section_id, []) if item.get("paragraph_index") == paragraph_index),
                        None,
                    )
                    if match:
                        paragraph_refs[str(match["paragraph_id"])][field].add(trace_id)
                        continue
                if section_id in section_refs:
                    section_refs[section_id][field].add(trace_id)

        for item in normalized.get("requirements") or []:
            attach("requirement_ids", str(item.get("requirement_id") or ""), item.get("locations") or [])
        for item in normalized.get("score_items") or []:
            attach("score_item_ids", str(item.get("score_item_id") or ""), item.get("locations") or [])

        requirements: list[FulltextRequirementTraceDTO] = []
        for item in package.get("response_matrix") or []:
            trace_id = str(item.get("requirement_id") or "")
            locations = list(item.get("response_locations") or [])
            attach("requirement_ids", trace_id, locations)
            text = str(item.get("requirement_text") or "")
            requirements.append(
                FulltextRequirementTraceDTO(
                    trace_id=trace_id,
                    title=_short_title(text, trace_id),
                    requirement_text=text,
                    response_status=str(item.get("response_status") or "uncertain"),
                    response_locations=[value for value in (_location(raw) for raw in locations) if value],
                )
            )

        findings: list[FulltextFindingTraceDTO] = []
        for item in list(audit.get("issues") or []) + list(audit.get("semantic_findings") or []):
            trace_id = str(item.get("issue_id") or item.get("finding_id") or "")
            locations = list(item.get("locations") or [])
            attach("finding_ids", trace_id, locations)
            recommendation = item.get("recommended_action") or {}
            findings.append(
                FulltextFindingTraceDTO(
                    trace_id=trace_id,
                    title=_subject_title(item, trace_id),
                    summary=str(item.get("summary") or ""),
                    rationale=_text(item.get("rationale")),
                    severity=_text(item.get("severity") or item.get("suggested_severity")),
                    release_blocking=bool(item.get("release_blocking") or item.get("suggested_release_blocking")),
                    recommendation=_text(recommendation.get("text") if isinstance(recommendation, dict) else recommendation),
                    locations=[value for value in (_location(raw) for raw in locations) if value],
                )
            )

        decisions = {
            (str(item.get("source_type") or ""), str(item.get("source_id") or "")): item
            for item in human.get("issue_decisions") or []
        }
        accepted_risks = {
            str(item.get("risk_acceptance_id") or "")
            for item in human.get("risk_acceptances") or []
            if item.get("accepted") is True
        }
        revision_sources = (
            {
                str(item.get("source_id") or item.get("source_ref") or "")
                for item in finalization.get("revision_candidates") or []
            }
            if finalization is not None
            else set()
        )
        reviews: list[FulltextReviewTraceDTO] = []
        for item in package.get("review_items") or []:
            trace_id = str(item.get("source_id") or "")
            source_type = str(item.get("source_type") or "")
            decision = decisions.get((source_type, trace_id)) or {}
            response_locations = list(item.get("current_response_locations") or [])
            if not response_locations and item.get("current_response_location"):
                response_locations = [item["current_response_location"]]
            recommended_locations = list(item.get("recommended_revision_locations") or [])
            attach("review_item_ids", trace_id, response_locations + recommended_locations)
            for fact_ref in item.get("required_fact_refs") or []:
                attach("fact_refs", str(fact_ref), response_locations + recommended_locations)
            for material_ref in item.get("required_material_refs") or []:
                attach("material_refs", str(material_ref), response_locations + recommended_locations)
            reviews.append(
                FulltextReviewTraceDTO(
                    trace_id=trace_id,
                    title=_display_title(item),
                    actionability=_actionability(item),
                    current_decision=str(decision.get("decision") or "pending"),
                    risk_accepted=str(decision.get("risk_acceptance_ref") or "") in accepted_risks,
                    response_locations=[value for value in (_location(raw) for raw in response_locations) if value],
                    recommended_locations=[value for value in (_location(raw) for raw in recommended_locations) if value],
                    fact_refs=_strings(item.get("required_fact_refs")),
                    material_refs=_strings(item.get("required_material_refs")),
                    revision_candidate=trace_id in revision_sources,
                )
            )

        scores: list[FulltextScoreTraceDTO] = []
        for item in package.get("scoring_matrix") or []:
            trace_id = str(item.get("score_item_id") or "")
            locations = list(item.get("response_locations") or [])
            attach("score_item_ids", trace_id, locations)
            scores.append(
                FulltextScoreTraceDTO(
                    trace_id=trace_id,
                    title=str(item.get("criteria_title") or "评分细则"),
                    criteria_text=str(item.get("criteria_text") or ""),
                    max_score=_number(item.get("max_score")),
                    assessment_status=str(item.get("assessment_status") or "pending"),
                    score_range=_score_range(item.get("score_range")),
                    pending_evidence=[
                        RequirementPendingEvidenceDTO(
                            evidence_type=str(value.get("evidence_type") or ""),
                            status=str(value.get("status") or "pending"),
                            status_label=_status_label(str(value.get("status") or "pending")),
                            reason=_text(value.get("reason")),
                        )
                        for value in item.get("pending_evidence") or []
                        if isinstance(value, dict)
                    ],
                    response_locations=[value for value in (_location(raw) for raw in locations) if value],
                )
            )

        fact_status = {
            str(item.get("source_id") or ""): str(item.get("confirmation_status") or "pending")
            for item in human.get("fact_confirmations") or []
        }
        material_status = {
            str(item.get("material_or_evidence_ref") or ""): (
                str(item.get("verification_status"))
                if item.get("verification_status") != "pending"
                else str(item.get("upstream_status") or "pending")
            )
            for item in human.get("material_verifications") or []
        }
        dependencies = [
            FulltextDependencyTraceDTO(
                trace_id=str(item.get("source_id") or ""),
                kind=str(item.get("queue_type") or "dependency"),
                title=str(item.get("display_title") or item.get("description") or "依赖项"),
                status=fact_status.get(str(item.get("source_id") or ""), material_status.get(str(item.get("source_id") or ""), str(item.get("status") or "pending"))),
                status_label=_status_label(fact_status.get(str(item.get("source_id") or ""), material_status.get(str(item.get("source_id") or ""), str(item.get("status") or "pending")))),
            )
            for item in package.get("fact_material_queue") or []
        ]

        chapters: list[FulltextChapterDTO] = []
        for chapter_position, chapter in enumerate(chapter_source, start=1):
            sections: list[FulltextSectionDTO] = []
            for section_position, section in enumerate(chapter.get("sections") or [], start=1):
                section_id = str(section.get("section_id") or "")
                paragraphs = sorted(paragraphs_by_section.get(section_id, []), key=lambda value: int(value.get("paragraph_index") or 0))
                sections.append(
                    FulltextSectionDTO(
                        section_id=section_id,
                        heading=str(section.get("heading") or ""),
                        order=section_position,
                        block_type=str(section.get("block_type") or ""),
                        paragraphs=[
                            FulltextParagraphDTO(
                                paragraph_id=str(paragraph["paragraph_id"]),
                                paragraph_index=int(paragraph.get("paragraph_index") or 0),
                                document_order=int(paragraph.get("document_order") or 0),
                                text=str(paragraph.get("text") or ""),
                                text_sha256=str(paragraph.get("text_sha256") or ""),
                                content_type=_content_type(str(paragraph.get("text") or "")),
                                placeholder_ids=list(dict.fromkeys(PLACEHOLDER_PATTERN.findall(str(paragraph.get("text") or "")))),
                                trace=_trace_dto(paragraph_refs[str(paragraph["paragraph_id"])]),
                            )
                            for paragraph in paragraphs
                        ],
                        trace=_trace_dto(section_refs[section_id]),
                    )
                )
            chapters.append(
                FulltextChapterDTO(
                    chapter_id=str(chapter.get("chapter_id") or ""),
                    title=str(chapter.get("chapter_title") or ""),
                    order=int(chapter.get("order") or chapter_position),
                    sections=sections,
                )
            )

        return AuditedFulltextDTO(
            project_id=project_id,
            document_id=document_id,
            version=AuditedFulltextVersionDTO(
                display_name="当前审核正文",
                revision=self.read_service._version_refs(bundle)[1].label,
                audited_content_sha256=reviewed_sha,
                audited_artifact_sha256=cleaned_file_sha,
                audit_sha256=str((package.get("source_lineage") or {}).get("final_stage11_audit", {}).get("file_sha256") or ""),
                review_package_id=str((package.get("artifact_identity") or {}).get("review_package_id") or ""),
                human_review_id=str((human.get("artifact_identity") or {}).get("human_review_id") or ""),
                generated_at=_text(cleaned.get("generated_at")),
            ),
            status=AuditedFulltextStatusDTO(
                release_ready=bool(audit.get("release_ready")),
                audit_result=str(audit.get("audit_result") or "unknown"),
                review_status=str((human.get("artifact_identity") or {}).get("status") or "unknown"),
                stage13_ready=bool(
                    finalization is not None
                    and (finalization.get("stage13_gate") or {}).get("stage13_ready")
                ),
            ),
            chapters=chapters,
            trace_index=AuditedFulltextTraceIndexDTO(
                requirements=requirements,
                findings=findings,
                reviews=reviews,
                scores=scores,
                dependencies=dependencies,
            ),
        )


def _empty_refs() -> dict[str, set[str]]:
    return {field: set() for field in TRACE_FIELDS}


def _trace_dto(value: dict[str, set[str]]) -> FulltextTraceRefsDTO:
    return FulltextTraceRefsDTO(**{field: sorted(value[field]) for field in TRACE_FIELDS})


def _location(value: Any) -> ReviewLocationDTO | None:
    if not isinstance(value, dict):
        return None
    chapter_id = _text(value.get("chapter_id"))
    section_id = _text(value.get("section_id"))
    paragraph_id = _text(value.get("paragraph_id"))
    if not any((chapter_id, section_id, paragraph_id)):
        return None
    return ReviewLocationDTO(
        chapter_id=chapter_id,
        chapter_title=_text(value.get("chapter_title")),
        section_id=section_id,
        section_heading=_text(value.get("section_heading") or value.get("heading")),
        paragraph_id=paragraph_id,
        paragraph_index=int(value["paragraph_index"]) if value.get("paragraph_index") is not None else None,
        excerpt=_text(value.get("excerpt")),
        response_role=_text(value.get("response_role")),
        technical_ref=" / ".join(part for part in (chapter_id, section_id, paragraph_id) if part),
    )


def _short_title(text: str, fallback: str) -> str:
    cleaned = re.sub(r"^\s*\d+[.、]\s*", "", text).strip()
    return cleaned[:72] + ("…" if len(cleaned) > 72 else "") if cleaned else fallback


def _subject_title(item: dict[str, Any], fallback: str) -> str:
    subject = item.get("subject")
    if isinstance(subject, dict):
        return str(subject.get("display_name") or subject.get("canonical_key") or fallback)
    return str(subject or fallback)


def _content_type(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and all(line.startswith("|") and line.endswith("|") for line in lines):
        return "table"
    if lines and all(re.match(r"^(?:[-*+]\s+|\d+[.、)]\s*)", line) for line in lines):
        return "list"
    return "paragraph"


def _status_label(value: str) -> str:
    return {
        "query_not_executed": "尚未查询",
        "candidate_found": "已发现候选材料",
        "verified": "已核验",
        "confirmed": "已确认",
        "rejected": "已驳回",
        "invalid": "材料无效",
        "expired": "材料已过期",
        "not_provided": "未提供",
        "missing": "缺少项目事实",
        "pending": "待处理",
        "confirmation_required": "待人工确认",
    }.get(value, value)


def _score_range(value: Any) -> ScoreRangeDTO:
    data = value if isinstance(value, dict) else {}
    return ScoreRangeDTO(minimum=_number(data.get("min", data.get("minimum"))), maximum=_number(data.get("max", data.get("maximum"))))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _strings(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values or [] if str(value)))

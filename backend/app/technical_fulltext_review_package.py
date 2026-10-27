from __future__ import annotations

import re
from math import isclose
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from app.technical_fulltext_auditor import (
    TechnicalFulltextAuditError,
    TechnicalFulltextAuditor,
)
from app.technical_fulltext_composer import (
    atomic_write_json,
    atomic_write_markdown,
    load_json_object,
    sha256_file,
    stable_json_sha256,
    utc_now,
)
from app.technical_fulltext_scoring_assessment import (
    SCHEMA_VERSION as SCORING_SCHEMA_VERSION,
    TechnicalFulltextScoringAssessor,
)


SCHEMA_VERSION = "technical_fulltext_review_package_v1"
ACTIONABILITY_CONTRACT_VERSION = "stage12_actionability_dependency_v1"
MARKDOWN_NOTICE = "Stage12-A 全文人工审核包已组装；本产物不包含人工决定，也不修改正文或机器结论。"


class TechnicalFulltextReviewPackageError(RuntimeError):
    """Raised when Stage12-A cannot assemble one trusted review package."""


def _stable_id(prefix: str, value: Any) -> str:
    return prefix + stable_json_sha256(value)[:16].upper()


def _unique(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _compact(value: Any, limit: int = 1600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


class TechnicalFulltextReviewPackageAssembler:
    """Assemble the read-only Stage12-A projection from frozen Stage11 outputs."""

    def __init__(self, *, schema_path: str | Path | None = None):
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path is not None
            else Path(__file__).resolve().parent.parent
            / "schemas"
            / "technical_fulltext_review_package_v1.json"
        )

    def assemble(
        self,
        *,
        audit_json_path: str | Path,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        audit_path = Path(audit_json_path).expanduser().resolve()
        try:
            audit = load_json_object(audit_path, label="Stage11 final audit")
            audit_sha = sha256_file(audit_path)
            TechnicalFulltextAuditor()._validate_audit_payload(audit)
        except (FileNotFoundError, OSError, ValueError, TechnicalFulltextAuditError) as exc:
            raise TechnicalFulltextReviewPackageError(
                f"Stage11 final audit is unavailable or invalid: {exc}"
            ) from exc
        self._validate_final_audit(audit)

        scoring_ref = audit.get("scoring_assessment_ref") or {}
        scoring_path = Path(str(scoring_ref.get("path") or "")).expanduser().resolve()
        try:
            scoring_sha = sha256_file(scoring_path)
            if scoring_sha != scoring_ref.get("sha256"):
                raise TechnicalFulltextReviewPackageError(
                    "Stage11 scoring assessment SHA256 does not match final audit reference"
                )
            scoring = load_json_object(scoring_path, label="Stage11 scoring assessment")
            TechnicalFulltextScoringAssessor()._validate_payload(scoring)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise TechnicalFulltextReviewPackageError(
                f"Stage11 scoring assessment is unavailable or invalid: {exc}"
            ) from exc
        scoring_matrix = deepcopy(scoring.get("score_items") or [])
        self._validate_scoring_binding(
            audit=audit,
            scoring=scoring,
            scoring_matrix=scoring_matrix,
        )

        try:
            resolved_source = TechnicalFulltextAuditor.revalidate_audit_source(audit)
        except TechnicalFulltextAuditError as exc:
            raise TechnicalFulltextReviewPackageError(
                f"audited response content is unavailable or changed: {exc}"
            ) from exc
        audited_content = (audit.get("source_lineage") or {}).get("audited_fulltext") or {}
        reviewed_content_sha = stable_json_sha256(resolved_source["fulltext"])
        if reviewed_content_sha != audited_content.get("content_sha256"):
            raise TechnicalFulltextReviewPackageError(
                "reviewed content SHA256 does not match Stage11 audited content"
            )
        if reviewed_content_sha != (scoring.get("audited_fulltext") or {}).get(
            "content_sha256"
        ):
            raise TechnicalFulltextReviewPackageError(
                "reviewed content SHA256 does not match Stage11 scoring content"
            )

        response_outline, response_ref = self._load_pinned_context(
            audit, "response_outline", "technical_response_outline_v2"
        )
        writing_outline, writing_ref = self._load_pinned_context(
            audit, "writing_outline", "technical_writing_outline_v0"
        )
        draft_units, draft_ref = self._load_pinned_context(
            audit, "draft_units", "technical_draft_units_v0"
        )
        task_dir = Path(draft_ref["path"]).parent
        fact_path = (task_dir / "project_fact_request.json").resolve()
        evidence_path = (task_dir / "local_evidence_query_plan.json").resolve()
        project_facts, fact_ref = self._load_sibling_artifact(
            fact_path, "project_fact_request_v0", "project fact request"
        )
        evidence_plan, evidence_ref = self._load_sibling_artifact(
            evidence_path, "local_evidence_query_plan_v0", "local evidence query plan"
        )

        requirement_points = self._requirement_points(response_outline)
        requirement_by_id = {
            item["point_id"]: item for item in requirement_points
        }
        location_context = self._location_context(audit)
        review_items = self._review_items(
            audit=audit,
            requirement_by_id=requirement_by_id,
            location_context=location_context,
            project_facts=project_facts,
            evidence_plan=evidence_plan,
        )
        response_matrix = self._response_matrix(
            audit=audit,
            scoring=scoring,
            writing_outline=writing_outline,
            requirement_points=requirement_points,
            location_context=location_context,
        )
        fact_material_queue = self._fact_material_queue(
            audit=audit,
            project_facts=project_facts,
            evidence_plan=evidence_plan,
        )
        risk_summary = self._machine_risk_summary(
            audit=audit,
            response_matrix=response_matrix,
            scoring=scoring,
            fact_material_queue=fact_material_queue,
        )
        summary = self._summary(
            audit=audit,
            response_matrix=response_matrix,
            review_items=review_items,
            scoring=scoring,
            fact_material_queue=fact_material_queue,
        )

        source_lineage = {
            "final_stage11_audit": {
                "path": str(audit_path),
                "file_sha256": audit_sha,
                "schema_version": audit["schema_version"],
                "audit_result": audit["audit_result"],
            },
            "stage11_scoring_assessment": {
                "path": str(scoring_path),
                "file_sha256": scoring_sha,
                "schema_version": scoring["schema_version"],
                "assessment_id": scoring["assessment_id"],
            },
            "audited_response_content": {
                "path": str(audited_content["path"]),
                "file_sha256": audited_content["file_sha256"],
                "content_sha256": reviewed_content_sha,
                "schema_version": audited_content["schema_version"],
            },
            "technical_response_outline": response_ref,
            "technical_writing_outline": writing_ref,
            "technical_draft_units": draft_ref,
            "project_fact_request": fact_ref,
            "local_evidence_query_plan": evidence_ref,
        }
        package_id = _stable_id(
            "TRP-",
            {
                "output_key": audit["output_key"],
                "final_audit_sha256": audit_sha,
                "scoring_sha256": scoring_sha,
                "reviewed_content_sha256": reviewed_content_sha,
                "actionability_contract_version": ACTIONABILITY_CONTRACT_VERSION,
            },
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "actionability_contract_version": ACTIONABILITY_CONTRACT_VERSION,
            "artifact_identity": {
                "review_package_id": package_id,
                "document_id": audit["document_id"],
                "output_key": audit["output_key"],
                "status": "assembled",
                "stage": "Stage12-A",
                "human_decisions_included": False,
            },
            "source_lineage": source_lineage,
            "reviewed_content_sha256": reviewed_content_sha,
            "review_items": review_items,
            "response_matrix": response_matrix,
            "scoring_matrix": scoring_matrix,
            "fact_material_queue": fact_material_queue,
            "fact_material_status_legend": {
                "query_not_executed": "尚未执行材料查询；不代表材料不存在。",
                "candidate_found": "已发现候选材料，但尚未完成有效性核验。",
                "verified": "材料已经过当前证据流程核验。",
                "confirmed_missing": "经执行查询和人工确认后明确缺失。",
                "invalid": "材料存在但不满足使用条件。",
                "expired": "材料存在但已过有效期。",
            },
            "machine_risk_summary": risk_summary,
            "summary": summary,
            "pre_stage13_dependencies": [
                {
                    "code": "full_stage11_2_iterative_replay_stability",
                    "status": "open",
                    "description": (
                        "完整 Stage11.2 replay 曾因 requirement_location provider 返回 missing/duplicate "
                        "unit ID 被严格原子拒绝；不得放宽校验，须在 Stage13 新 revision 前解决。"
                    ),
                }
            ],
            "generated_at": utc_now(),
        }
        self._validate_payload(payload)

        tracked_hashes = {
            audit_path: audit_sha,
            scoring_path: scoring_sha,
            Path(response_ref["path"]): response_ref["file_sha256"],
            Path(writing_ref["path"]): writing_ref["file_sha256"],
            Path(draft_ref["path"]): draft_ref["file_sha256"],
            fact_path: fact_ref["file_sha256"],
            evidence_path: evidence_ref["file_sha256"],
            Path(audited_content["path"]): audited_content["file_sha256"],
        }
        for path, expected_sha in tracked_hashes.items():
            if sha256_file(path) != expected_sha:
                raise TechnicalFulltextReviewPackageError(
                    f"Stage12-A input changed during assembly: {path}"
                )

        target_dir = (
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else audit_path.parent.parent / "review"
        )
        json_path = target_dir / "technical_fulltext.review_package.json"
        markdown_path = target_dir / "technical_fulltext.review_package.md"
        markdown = self._render_markdown(payload)
        target_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            json_path,
            payload,
            required_fields=(
                "schema_version",
                "artifact_identity",
                "source_lineage",
                "review_items",
                "response_matrix",
                "scoring_matrix",
                "fact_material_queue",
                "machine_risk_summary",
                "summary",
            ),
        )
        atomic_write_markdown(
            markdown_path,
            markdown,
            required_text=MARKDOWN_NOTICE,
        )
        return {
            "status": "assembled",
            "review_package_id": package_id,
            "summary": deepcopy(summary),
            "outputs": {
                "technical_fulltext_review_package_json": str(json_path),
                "technical_fulltext_review_package_json_sha256": sha256_file(json_path),
                "technical_fulltext_review_package_markdown": str(markdown_path),
                "technical_fulltext_review_package_markdown_sha256": sha256_file(
                    markdown_path
                ),
            },
        }

    @staticmethod
    def _validate_final_audit(audit: dict[str, Any]) -> None:
        passes = audit.get("audit_passes") or {}
        if audit.get("status") != "completed":
            raise TechnicalFulltextReviewPackageError(
                "Stage12-A requires a completed Stage11 final audit"
            )
        for name in ("deterministic", "semantic", "scoring", "finalization"):
            if (passes.get(name) or {}).get("status") != "completed":
                raise TechnicalFulltextReviewPackageError(
                    f"Stage12-A requires completed Stage11 pass: {name}"
                )
        if (audit.get("finalization") or {}).get("status") != "completed":
            raise TechnicalFulltextReviewPackageError(
                "Stage12-A requires Stage11.4 finalization metadata"
            )

    @staticmethod
    def _validate_scoring_binding(
        *,
        audit: dict[str, Any],
        scoring: dict[str, Any],
        scoring_matrix: list[dict[str, Any]] | None = None,
    ) -> None:
        if scoring.get("schema_version") != SCORING_SCHEMA_VERSION:
            raise TechnicalFulltextReviewPackageError(
                "Stage12-A requires technical_fulltext_scoring_assessment_v1"
            )
        if scoring.get("status") != "completed":
            raise TechnicalFulltextReviewPackageError(
                "Stage12-A requires completed Stage11 scoring"
            )
        if scoring.get("output_key") != audit.get("output_key"):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 audit and scoring assessment output_key mismatch"
            )
        if scoring.get("document_id") != audit.get("document_id"):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 audit and scoring assessment document_id mismatch"
            )
        score_items = list(scoring.get("score_items") or [])
        catalog = scoring.get("score_catalog") or {}
        declared_count = catalog.get("score_item_count")
        if not score_items:
            raise TechnicalFulltextReviewPackageError(
                "Stage12-A requires a non-empty scoring catalog"
            )
        if declared_count != len(score_items):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 scoring catalog count does not match score_items"
            )
        score_item_ids = [str(item.get("score_item_id") or "") for item in score_items]
        if any(not re.match(r"^SCORE-", item_id) for item_id in score_item_ids):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 scoring item identity does not satisfy the assessment contract"
            )
        if len(score_item_ids) != len(set(score_item_ids)):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 scoring catalog contains duplicate score_item_id"
            )
        try:
            actual_total = sum(float(item["max_score"]) for item in score_items)
            catalog_total = float(catalog["total_max_score"])
            summary_total = float((scoring.get("summary") or {})["technical_score_max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TechnicalFulltextReviewPackageError(
                f"Stage11 scoring total is unavailable or invalid: {exc}"
            ) from exc
        if not isclose(actual_total, catalog_total, rel_tol=0.0, abs_tol=1e-9):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 scoring catalog total does not match score_items"
            )
        if not isclose(actual_total, summary_total, rel_tol=0.0, abs_tol=1e-9):
            raise TechnicalFulltextReviewPackageError(
                "Stage11 scoring summary total does not match score_items"
            )
        if scoring_matrix is not None:
            matrix_ids = [str(item.get("score_item_id") or "") for item in scoring_matrix]
            if len(scoring_matrix) != len(score_items) or set(matrix_ids) != set(score_item_ids):
                raise TechnicalFulltextReviewPackageError(
                    "Stage12-A scoring_matrix does not completely match the scoring assessment"
                )

    @staticmethod
    def _load_pinned_context(
        audit: dict[str, Any], name: str, expected_schema: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = (audit.get("input_context") or {}).get(name)
        if not isinstance(metadata, dict) or metadata.get("sha256_matches_run") is not True:
            raise TechnicalFulltextReviewPackageError(
                f"Stage12-A requires trusted Stage11 input context: {name}"
            )
        path = Path(str(metadata.get("path") or "")).expanduser().resolve()
        try:
            actual_sha = sha256_file(path)
            payload = load_json_object(path, label=name)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise TechnicalFulltextReviewPackageError(
                f"trusted input context is unavailable: {name}: {exc}"
            ) from exc
        if actual_sha != metadata.get("sha256"):
            raise TechnicalFulltextReviewPackageError(
                f"trusted input context SHA256 mismatch: {name}"
            )
        if payload.get("schema_version") != expected_schema:
            raise TechnicalFulltextReviewPackageError(
                f"trusted input context schema mismatch: {name}"
            )
        return payload, {
            "path": str(path),
            "file_sha256": actual_sha,
            "schema_version": payload["schema_version"],
        }

    @staticmethod
    def _load_sibling_artifact(
        path: Path, expected_schema: str, label: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            payload = load_json_object(path, label=label)
            file_sha = sha256_file(path)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise TechnicalFulltextReviewPackageError(
                f"{label} is unavailable: {exc}"
            ) from exc
        if payload.get("schema_version") != expected_schema:
            raise TechnicalFulltextReviewPackageError(
                f"{label} schema mismatch"
            )
        return payload, {
            "path": str(path),
            "file_sha256": file_sha,
            "schema_version": expected_schema,
        }

    @staticmethod
    def _requirement_points(response_outline: dict[str, Any]) -> list[dict[str, Any]]:
        points = [
            item
            for item in _walk_dicts(response_outline)
            if re.fullmatch(r"TR-\d+", str(item.get("point_id") or ""))
        ]
        by_id = {item["point_id"]: item for item in points}
        if len(by_id) != len(points):
            raise TechnicalFulltextReviewPackageError(
                "technical response outline contains duplicate Requirement IDs"
            )
        return [by_id[key] for key in sorted(by_id)]

    @staticmethod
    def _location_context(audit: dict[str, Any]) -> dict[str, Any]:
        normalized = audit.get("normalized_document") or {}
        return {
            "chapter_by_id": {
                item["chapter_id"]: item for item in normalized.get("chapters") or []
            },
            "section_by_id": {
                item["section_id"]: item for item in normalized.get("sections") or []
            },
            "paragraph_by_id": {
                item["paragraph_id"]: item
                for item in normalized.get("paragraphs") or []
            },
        }

    @staticmethod
    def _project_location(
        raw: dict[str, Any], location_context: dict[str, Any]
    ) -> dict[str, Any]:
        chapter_id = str(raw.get("chapter_id") or "")
        section_id = str(raw.get("section_id") or "")
        paragraph_id = str(raw.get("paragraph_id") or "") or None
        chapter = location_context["chapter_by_id"].get(chapter_id) or {}
        section = location_context["section_by_id"].get(section_id) or {}
        paragraph = location_context["paragraph_by_id"].get(paragraph_id) or {}
        return {
            "chapter_id": chapter_id,
            "chapter_title": chapter.get("chapter_title"),
            "section_id": section_id,
            "section_heading": section.get("heading"),
            "paragraph_id": paragraph_id,
            "paragraph_index": raw.get("paragraph_index", paragraph.get("paragraph_index")),
            "excerpt": _compact(raw.get("excerpt") or paragraph.get("text") or section.get("content")),
        }

    @classmethod
    def _review_items(
        cls,
        *,
        audit: dict[str, Any],
        requirement_by_id: dict[str, dict[str, Any]],
        location_context: dict[str, Any],
        project_facts: dict[str, Any],
        evidence_plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        relationships = audit.get("confirmation_relationships") or []
        relationships_by_finding: dict[str, list[dict[str, Any]]] = defaultdict(list)
        relationships_by_confirmation: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relationship in relationships:
            relationships_by_finding[str(relationship.get("finding_id") or "")].append(
                relationship
            )
            for confirmation_id in relationship.get("confirmation_ids") or []:
                relationships_by_confirmation[confirmation_id].append(relationship)

        fact_id_by_key = {
            str(slot.get("fact_key") or ""): str(slot.get("fact_slot_id") or "")
            for slot in project_facts.get("fact_slots") or project_facts.get("slots") or []
        }
        fca_id_by_placeholder = {
            placeholder: str(confirmation.get("confirmation_id") or "")
            for confirmation in audit.get("confirmation_register") or []
            for placeholder in confirmation.get("member_placeholder_ids") or []
        }
        evidence_id_by_type = {
            str(query.get("evidence_type") or ""): str(query.get("query_id") or "")
            for query in evidence_plan.get("queries") or []
        }

        def requirement_projection(ids: list[str]) -> tuple[str | None, list[dict[str, str]]]:
            projected = [
                {
                    "requirement_id": requirement_id,
                    "requirement_text": str(
                        (requirement_by_id.get(requirement_id) or {}).get("original_text")
                        or ""
                    ),
                }
                for requirement_id in ids
                if requirement_id in requirement_by_id
            ]
            text = "\n\n".join(
                f"{item['requirement_id']}：{item['requirement_text']}"
                for item in projected
            )
            return text or None, projected

        def base_item(
            *,
            source_type: str,
            source_id: str,
            display_title: str,
            summary: str,
            requirement_ids: list[str],
            raw_locations: list[dict[str, Any]],
            recommended_action: dict[str, Any] | None,
            severity: str | None,
            release_blocking: bool,
            machine_disposition: str,
            confidence: str | None,
            finding_refs: list[str],
            confirmation_refs: list[str],
            related_relationships: list[dict[str, Any]],
        ) -> dict[str, Any]:
            locations = [
                cls._project_location(item, location_context)
                for item in raw_locations
            ]
            requirement_text, requirement_texts = requirement_projection(
                requirement_ids
            )
            item = {
                "source_type": source_type,
                "source_id": source_id,
                "display_title": display_title,
                "machine_issue_summary": summary,
                "requirement_ids": requirement_ids,
                "requirement_text": requirement_text,
                "requirement_texts": requirement_texts,
                "current_response_location": locations[0] if locations else None,
                "current_response_locations": locations,
                "current_response_excerpt": locations[0]["excerpt"] if locations else None,
                "current_response_excerpts": _unique(
                    item["excerpt"] for item in locations if item["excerpt"]
                ),
                "machine_recommended_action": deepcopy(recommended_action),
                "machine_severity": severity,
                "release_blocking": release_blocking,
                "machine_disposition": machine_disposition,
                "confidence": confidence,
                "finding_refs": finding_refs,
                "confirmation_refs": confirmation_refs,
                "confirmation_relationships": deepcopy(related_relationships),
            }
            item.update(
                cls._actionability_metadata(
                    item=item,
                    location_context=location_context,
                    fact_id_by_key=fact_id_by_key,
                    fca_id_by_placeholder=fca_id_by_placeholder,
                    evidence_id_by_type=evidence_id_by_type,
                )
            )
            return item

        items: list[dict[str, Any]] = []
        for issue in audit.get("issues") or []:
            source_findings = _unique(issue.get("source_finding_ids") or [])
            related = [
                relationship
                for finding_id in source_findings
                for relationship in relationships_by_finding.get(finding_id, [])
            ]
            semantic_subject = (issue.get("details") or {}).get("semantic_subject") or {}
            items.append(
                base_item(
                    source_type="FAI",
                    source_id=issue["issue_id"],
                    display_title=str(
                        semantic_subject.get("display_name")
                        or issue.get("summary")
                        or issue["issue_id"]
                    ),
                    summary=str(issue.get("summary") or ""),
                    requirement_ids=_unique(issue.get("requirement_ids") or []),
                    raw_locations=issue.get("locations") or [],
                    recommended_action=issue.get("recommended_action"),
                    severity=issue.get("severity"),
                    release_blocking=bool(issue.get("release_blocking")),
                    machine_disposition="unresolved",
                    confidence=(issue.get("details") or {}).get("confidence"),
                    finding_refs=source_findings,
                    confirmation_refs=_unique(
                        confirmation_id
                        for relationship in related
                        for confirmation_id in relationship.get("confirmation_ids") or []
                    ),
                    related_relationships=related,
                )
            )

        disposition_by_id = {
            item["finding_id"]: item
            for item in audit.get("semantic_finding_dispositions") or []
        }
        for finding in audit.get("semantic_findings") or []:
            disposition = disposition_by_id.get(finding["finding_id"]) or {}
            if finding.get("verdict") != "uncertain" or disposition.get(
                "disposition"
            ) != "human_review":
                continue
            related = relationships_by_finding.get(finding["finding_id"], [])
            items.append(
                base_item(
                    source_type="FSF",
                    source_id=finding["finding_id"],
                    display_title=str(
                        (finding.get("subject") or {}).get("display_name")
                        or finding.get("summary")
                        or finding["finding_id"]
                    ),
                    summary=str(finding.get("summary") or ""),
                    requirement_ids=_unique(finding.get("requirement_ids") or []),
                    raw_locations=finding.get("locations") or [],
                    recommended_action=finding.get("recommended_action"),
                    severity=finding.get("suggested_severity"),
                    release_blocking=bool(finding.get("suggested_release_blocking")),
                    machine_disposition="human_review",
                    confidence=finding.get("confidence"),
                    finding_refs=[finding["finding_id"]],
                    confirmation_refs=_unique(
                        confirmation_id
                        for relationship in related
                        for confirmation_id in relationship.get("confirmation_ids") or []
                    ),
                    related_relationships=related,
                )
            )

        for confirmation in audit.get("confirmation_register") or []:
            if confirmation.get("status") != "pending":
                continue
            confirmation_id = confirmation["confirmation_id"]
            placeholders = _unique(confirmation.get("member_placeholder_ids") or [])
            related = relationships_by_confirmation.get(confirmation_id, [])
            items.append(
                base_item(
                    source_type="FCA",
                    source_id=confirmation_id,
                    display_title="待确认事实：" + "、".join(placeholders),
                    summary="当前正文中的事实占位符尚未由人工确认或回填。",
                    requirement_ids=[],
                    raw_locations=confirmation.get("affected_locations") or [],
                    recommended_action={
                        "resolution_kind": "confirm_project_fact",
                        "text": "提供经确认的真实项目事实及来源，供后续人工决定和 Stage13 修改使用。",
                    },
                    severity=None,
                    release_blocking=False,
                    machine_disposition="pending",
                    confidence=None,
                    finding_refs=_unique(
                        relationship.get("finding_id") for relationship in related
                    ),
                    confirmation_refs=[confirmation_id],
                    related_relationships=related,
                )
            )
        order = {"FAI": 0, "FSF": 1, "FCA": 2}
        return sorted(
            items,
            key=lambda item: (
                0 if item["release_blocking"] else 1,
                order[item["source_type"]],
                item["source_id"],
            ),
        )

    @classmethod
    def _actionability_metadata(
        cls,
        *,
        item: dict[str, Any],
        location_context: dict[str, Any],
        fact_id_by_key: dict[str, str],
        fca_id_by_placeholder: dict[str, str],
        evidence_id_by_type: dict[str, str],
    ) -> dict[str, Any]:
        """Derive Stage12-only handoff semantics without changing Stage11 findings."""

        action = item.get("machine_recommended_action") or {}
        resolution_kind = str(action.get("resolution_kind") or "")
        action_text = str(action.get("text") or "")
        source_type = str(item.get("source_type") or "")
        requirement_ids = set(item.get("requirement_ids") or [])
        title_and_summary = " ".join(
            (str(item.get("display_title") or ""), str(item.get("machine_issue_summary") or ""))
        ).lower()
        locations = deepcopy(item.get("current_response_locations") or [])

        metadata: dict[str, Any] = {
            "actionability_classes": ["revision_actionable"],
            "handoff_mode": "revision",
            "must_decide_before_stage13": True,
            "required_fact_refs": [],
            "required_material_refs": [],
            "required_confirmation_refs": [],
            "deferred_confirmation_refs": [],
            "recommended_revision_locations": locations,
            "shared_dependency": None,
            "default_handoff_instruction": str(action.get("text") or ""),
        }
        if source_type == "FCA":
            metadata.update(
                {
                    "actionability_classes": ["verification_only"],
                    "handoff_mode": "verification",
                    "must_decide_before_stage13": False,
                    "default_handoff_instruction": "",
                }
            )
            return metadata

        # TR-003 has an already parsed, stable project-fact slot.  The target is
        # intentionally explicit: WC-005-11 is only a generic promise, while
        # WC-005-04 is the substantive manufacturer-service response location.
        if source_type == "FAI" and "TR-003" in requirement_ids:
            target = cls._project_location({"section_id": "WC-005-04"}, location_context)
            metadata.update(
                {
                    "actionability_classes": ["revision_actionable", "fact_dependent"],
                    "required_fact_refs": _unique(
                        [fact_id_by_key.get("manufacturer_service_network")]
                    ),
                    "recommended_revision_locations": [target],
                }
            )
            return metadata

        # Alias normalization is safe before a real offered value is confirmed:
        # it only normalizes placeholder identity and never substitutes a value.
        if source_type == "FAI" and "placeholder alias" in title_and_summary:
            metadata.update(
                {
                    "actionability_classes": ["revision_actionable", "fact_dependent"],
                    "handoff_mode": "structural_normalization",
                    "required_fact_refs": [],
                    "required_confirmation_refs": [],
                    "deferred_confirmation_refs": _unique(item.get("confirmation_refs") or []),
                    "default_handoff_instruction": (
                        "Normalize the equivalent placeholders to one canonical placeholder "
                        "identity only. Preserve every placeholder and do not insert an actual "
                        "offered-product value; value substitution remains pending fact confirmation."
                    ),
                }
            )
            return metadata

        is_point_to_point = source_type == "FSF" and (
            "点对点" in title_and_summary
            or "point-to-point" in title_and_summary
            or "技术要求点对点应答表" in action_text
        )
        if is_point_to_point:
            fact_keys = (
                "offered_product_brand",
                "offered_product_model",
                "manufacturer",
                "origin",
                "actual_product_configuration",
                "actual_technical_parameters",
                "technical_deviation_status",
            )
            placeholders = (
                "PH-FIELD-OFFERED-PRODUCT-BRAND",
                "PH-FIELD-OFFERED-PRODUCT-MODEL",
                "PH-FIELD-MANUFACTURER",
                "PH-FIELD-ORIGIN",
                "PH-FIELD-ACTUAL-PRODUCT-CONFIGURATION",
                "PH-FIELD-ACTUAL-TECHNICAL-PARAMETERS",
                "PH-FIELD-TECHNICAL-DEVIATION-STATUS",
            )
            preferred_locations = [
                location for location in locations if location.get("section_id") == "WC-007-01"
            ] or locations
            metadata.update(
                {
                    "actionability_classes": [
                        "revision_actionable",
                        "fact_dependent",
                        "material_dependent",
                    ],
                    "required_fact_refs": _unique(fact_id_by_key.get(key) for key in fact_keys),
                    "required_confirmation_refs": _unique(
                        fca_id_by_placeholder.get(placeholder) for placeholder in placeholders
                    ),
                    "required_material_refs": _unique(
                        evidence_id_by_type.get(kind)
                        for kind in (
                            "product_certificate",
                            "product_test_report",
                            "product_parameter_proof",
                            "manufacturer_official_document",
                        )
                    ),
                    "recommended_revision_locations": preferred_locations,
                    "shared_dependency": {
                        "dependency_id": "response_side_point_to_point_table",
                        "status": "unavailable",
                        "description": (
                            "No completed response-side point-to-point table exists in the "
                            "current lineage; tender-side requirement cells are comparison baselines only."
                        ),
                    },
                    "default_handoff_instruction": (
                        "Generate or complete the formal technical-requirement point-to-point "
                        "response table. Answer each tender requirement with a response-side value "
                        "or explicit deviation status. Keep any unconfirmed offered-product fact "
                        "unresolved; never infer an offered value from a tender minimum."
                    ),
                }
            )
            return metadata

        if resolution_kind in {"no_action", "semantic_observation", "runtime_confirm"}:
            metadata.update(
                {
                    "actionability_classes": ["no_action_acknowledgement"],
                    "handoff_mode": "no_action",
                    "must_decide_before_stage13": False,
                    "default_handoff_instruction": "",
                }
            )
            return metadata

        if resolution_kind == "none":
            metadata.update(
                {
                    "actionability_classes": ["informational_or_context"],
                    "handoff_mode": "informational",
                    "must_decide_before_stage13": False,
                    "recommended_revision_locations": [],
                    "default_handoff_instruction": "",
                }
            )
        return metadata

    @classmethod
    def _response_matrix(
        cls,
        *,
        audit: dict[str, Any],
        scoring: dict[str, Any],
        writing_outline: dict[str, Any],
        requirement_points: list[dict[str, Any]],
        location_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        coverage_by_id = {
            item["requirement_id"]: item
            for item in (audit.get("coverage_analysis") or {}).get("requirements") or []
        }
        mandatory_ids = set(
            (writing_outline.get("coverage") or {}).get(
                "mandatory_response_point_ids"
            )
            or []
        )
        findings = audit.get("semantic_findings") or []
        issues = audit.get("issues") or []
        confirmations = audit.get("confirmation_register") or []
        scoring_items = scoring.get("score_items") or []

        matrix: list[dict[str, Any]] = []
        for point in requirement_points:
            requirement_id = point["point_id"]
            coverage = coverage_by_id.get(requirement_id) or {}
            requirement_findings = [
                item
                for item in findings
                if requirement_id in (item.get("requirement_ids") or [])
            ]
            actual_locations = list(coverage.get("actual_locations") or [])
            seen_locations = {
                (
                    item.get("chapter_id"),
                    item.get("section_id"),
                    item.get("paragraph_id"),
                    item.get("excerpt"),
                )
                for item in actual_locations
            }
            for finding in requirement_findings:
                for finding_location in finding.get("locations") or []:
                    key = (
                        finding_location.get("chapter_id"),
                        finding_location.get("section_id"),
                        finding_location.get("paragraph_id"),
                        finding_location.get("excerpt"),
                    )
                    if key not in seen_locations:
                        actual_locations.append(finding_location)
                        seen_locations.add(key)
            primary_sections = {
                item.get("section_id")
                for item in (
                    (coverage.get("planned_primary_locations") or [])
                    + (coverage.get("planned_table_locations") or [])
                )
                if item.get("resolved") and item.get("section_id")
            }
            supporting_sections = {
                item.get("section_id")
                for item in coverage.get("planned_supporting_locations") or []
                if item.get("resolved") and item.get("section_id")
            }
            locations: list[dict[str, Any]] = []
            excerpts: list[dict[str, Any]] = []
            for raw in actual_locations:
                projected = cls._project_location(raw, location_context)
                section_id = projected["section_id"]
                role = (
                    "primary"
                    if section_id in primary_sections
                    else "supporting"
                    if section_id in supporting_sections
                    else "unplanned"
                )
                projected["response_role"] = role
                locations.append(projected)
                if projected["excerpt"]:
                    excerpts.append(
                        {
                            "chapter_id": projected["chapter_id"],
                            "section_id": section_id,
                            "response_role": role,
                            "excerpt": projected["excerpt"],
                        }
                    )
            primary = next(
                (
                    {
                        "location": location,
                        "excerpt": location["excerpt"],
                    }
                    for location in locations
                    if location["response_role"] == "primary"
                ),
                None,
            )
            supporting = [
                {"location": location, "excerpt": location["excerpt"]}
                for location in locations
                if primary is None or location is not primary.get("location")
            ]
            response_status = cls._response_status(
                coverage=coverage, findings=requirement_findings
            )
            section_ids = {item["section_id"] for item in locations}
            finding_refs = _unique(
                [
                    issue["issue_id"]
                    for issue in issues
                    if requirement_id in (issue.get("requirement_ids") or [])
                ]
                + [item["finding_id"] for item in requirement_findings]
            )
            score_refs = _unique(
                item["score_item_id"]
                for item in scoring_items
                if requirement_id in (item.get("requirement_ids") or [])
            )
            confirmation_refs = _unique(
                confirmation["confirmation_id"]
                for confirmation in confirmations
                if any(
                    location.get("section_id") in section_ids
                    for location in confirmation.get("affected_locations") or []
                )
            )
            source_ref = deepcopy((point.get("source_refs") or [None])[0])
            source_table_or_cell = None
            if source_ref:
                table_values = {
                    key: source_ref.get(key)
                    for key in ("sheet_name", "table_name", "table_range", "row_or_cell")
                }
                if any(value not in (None, "") for value in table_values.values()):
                    source_table_or_cell = table_values
            matrix.append(
                {
                    "requirement_id": requirement_id,
                    "requirement_text": str(point.get("original_text") or ""),
                    "mandatory_marker": str(point.get("mandatory_marker") or ""),
                    "mandatory_response": requirement_id in mandatory_ids,
                    "source_reference": source_ref,
                    "source_file": source_ref.get("file_name") if source_ref else None,
                    "source_section": deepcopy(point.get("section_path") or []),
                    "source_clause": deepcopy(source_ref.get("clause_path") or [])
                    if source_ref
                    else [],
                    "source_page": (
                        {
                            "start": source_ref.get("page_start"),
                            "end": source_ref.get("page_end"),
                        }
                        if source_ref
                        else None
                    ),
                    "source_table_or_cell": source_table_or_cell,
                    "response_status": response_status,
                    "response_locations": locations,
                    "response_excerpts": excerpts,
                    "primary_response": primary,
                    "supporting_responses": supporting,
                    "finding_refs": finding_refs,
                    "score_item_refs": score_refs,
                    "confirmation_refs": confirmation_refs,
                }
            )
        return matrix

    @staticmethod
    def _response_status(
        *, coverage: dict[str, Any], findings: list[dict[str, Any]]
    ) -> str:
        if not coverage.get("actual_locations"):
            return "missing"
        if any(item.get("verdict") == "uncertain" for item in findings):
            return "uncertain"
        issue_findings = [item for item in findings if item.get("verdict") == "issue"]
        for finding in issue_findings:
            observation = finding.get("semantic_observation") or {}
            primary_status = observation.get("primary_response_status")
            if primary_status == "missing":
                return "missing"
            if primary_status == "inadequate" or finding.get("decision") in {
                "coverage_content_mismatch",
                "misplaced_content",
            }:
                return "partial"
        return "present"

    @staticmethod
    def _fact_material_queue(
        *,
        audit: dict[str, Any],
        project_facts: dict[str, Any],
        evidence_plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        queue: list[dict[str, Any]] = []
        for slot in project_facts.get("slots") or []:
            if slot.get("status") != "missing":
                continue
            queue.append(
                {
                    "queue_type": "missing_project_fact",
                    "source_type": "FACT",
                    "source_id": slot["fact_slot_id"],
                    "display_title": str(slot.get("name") or slot.get("fact_key")),
                    "status": "missing",
                    "required": bool(slot.get("required")),
                    "description": str(
                        slot.get("prohibited_inference")
                        or "需要人工提供经确认的项目事实。"
                    ),
                    "related_task_ids": _unique(slot.get("target_task_ids") or []),
                    "related_confirmation_ids": [],
                }
            )
        for query in evidence_plan.get("queries") or []:
            status = str(query.get("execution_status") or "")
            if status != "planned_not_executed":
                continue
            queue.append(
                {
                    "queue_type": "evidence_query_not_executed",
                    "source_type": "EVIDENCE_QUERY",
                    "source_id": query["query_id"],
                    "display_title": str(query.get("query") or query.get("evidence_type")),
                    "status": "query_not_executed",
                    "required": bool(query.get("required")),
                    "description": (
                        "尚未执行材料查询；当前无已验证材料，但不能据此认定材料不存在。"
                    ),
                    "related_task_ids": _unique(query.get("target_task_ids") or []),
                    "related_confirmation_ids": [],
                }
            )
        for confirmation in audit.get("confirmation_register") or []:
            if confirmation.get("status") != "pending":
                continue
            queue.append(
                {
                    "queue_type": "confirmation_required",
                    "source_type": "FCA",
                    "source_id": confirmation["confirmation_id"],
                    "display_title": "待确认占位事实："
                    + "、".join(confirmation.get("member_placeholder_ids") or []),
                    "status": "confirmation_required",
                    "required": True,
                    "description": "当前正文中的稳定事实尚未获得人工确认。",
                    "related_task_ids": [],
                    "related_confirmation_ids": [confirmation["confirmation_id"]],
                }
            )
        return sorted(queue, key=lambda item: (item["queue_type"], item["source_id"]))

    @staticmethod
    def _machine_risk_summary(
        *,
        audit: dict[str, Any],
        response_matrix: list[dict[str, Any]],
        scoring: dict[str, Any],
        fact_material_queue: list[dict[str, Any]],
    ) -> dict[str, Any]:
        issues = audit.get("issues") or []
        uncertain = [
            item
            for item in audit.get("semantic_findings") or []
            if item.get("verdict") == "uncertain"
        ]
        mandatory_gaps = [
            item
            for item in response_matrix
            if item["mandatory_response"] and item["response_status"] != "present"
        ]
        pending_scores = [
            item
            for item in scoring.get("score_items") or []
            if item.get("assessment_status") == "pending"
        ]
        queue_counts = Counter(item["queue_type"] for item in fact_material_queue)
        evidence_state = scoring.get("evidence_state") or {}
        return {
            "blocking_machine_issues": [
                {
                    "source_type": "FAI",
                    "source_id": item["issue_id"],
                    "title": item["summary"],
                    "severity": item["severity"],
                    "release_blocking": item["release_blocking"],
                }
                for item in issues
                if item.get("release_blocking")
            ],
            "warning_machine_issues": [
                {
                    "source_type": "FAI",
                    "source_id": item["issue_id"],
                    "title": item["summary"],
                    "severity": item["severity"],
                    "release_blocking": item["release_blocking"],
                }
                for item in issues
                if item.get("severity") == "warning"
            ],
            "uncertain_items": [
                {
                    "source_type": "FSF",
                    "source_id": item["finding_id"],
                    "title": item["summary"],
                    "requirement_ids": deepcopy(item.get("requirement_ids") or []),
                }
                for item in uncertain
            ],
            "mandatory_response_gaps": [
                {
                    "requirement_id": item["requirement_id"],
                    "requirement_text": item["requirement_text"],
                    "response_status": item["response_status"],
                    "finding_refs": item["finding_refs"],
                }
                for item in mandatory_gaps
            ],
            "score_pending_exposure": {
                "score_item_refs": [item["score_item_id"] for item in pending_scores],
                "pending_item_count": len(pending_scores),
                "pending_score_max": (scoring.get("summary") or {}).get(
                    "pending_score_max"
                ),
                "potential_gain_range": deepcopy(
                    (scoring.get("summary") or {}).get("potential_gain_range")
                ),
            },
            "fact_material_exposure": {
                "missing_project_fact_count": queue_counts["missing_project_fact"],
                "evidence_query_not_executed_count": queue_counts[
                    "evidence_query_not_executed"
                ],
                "material_pending_verification_count": queue_counts[
                    "material_pending_verification"
                ],
                "confirmation_required_count": queue_counts["confirmation_required"],
                "candidate_material_count": int(
                    evidence_state.get("candidate_evidence_count") or 0
                ),
                "verified_material_count": int(
                    evidence_state.get("verified_evidence_count") or 0
                ),
            },
        }

    @staticmethod
    def _summary(
        *,
        audit: dict[str, Any],
        response_matrix: list[dict[str, Any]],
        review_items: list[dict[str, Any]],
        scoring: dict[str, Any],
        fact_material_queue: list[dict[str, Any]],
    ) -> dict[str, Any]:
        response_counts = Counter(item["response_status"] for item in response_matrix)
        review_counts = Counter(item["source_type"] for item in review_items)
        queue_counts = Counter(item["queue_type"] for item in fact_material_queue)
        score_summary = scoring.get("summary") or {}
        issue_counts = audit.get("issue_counts") or {}
        return {
            "requirement_count": len(response_matrix),
            "response_status_counts": {
                name: response_counts[name]
                for name in ("present", "partial", "missing", "uncertain")
            },
            "review_item_count": len(review_items),
            "fai_count": review_counts["FAI"],
            "uncertain_fsf_count": review_counts["FSF"],
            "pending_fca_count": review_counts["FCA"],
            "blocking_count": int(issue_counts.get("blocking") or 0),
            "warning_count": int(issue_counts.get("warning") or 0),
            "score_item_count": (scoring.get("score_catalog") or {}).get(
                "score_item_count"
            ),
            "technical_score_max": score_summary.get("technical_score_max"),
            "confirmed_score": score_summary.get("confirmed_score_subtotal"),
            "estimated_score_range": deepcopy(
                score_summary.get("estimated_score_range")
            ),
            "overall_score_range": deepcopy(score_summary.get("overall_score_range")),
            "pending_score_max": score_summary.get("pending_score_max"),
            "potential_gain_range": deepcopy(score_summary.get("potential_gain_range")),
            "confirmed_score_item_count": score_summary.get("confirmed_item_count"),
            "estimated_score_item_count": score_summary.get("estimated_item_count"),
            "pending_score_item_count": score_summary.get("pending_item_count"),
            "missing_fact_count": queue_counts["missing_project_fact"],
            "evidence_query_not_executed_count": queue_counts[
                "evidence_query_not_executed"
            ],
            "verified_material_count": int(
                (scoring.get("evidence_state") or {}).get("verified_evidence_count")
                or 0
            ),
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        try:
            schema = load_json_object(self.schema_path, label="review package schema")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextReviewPackageError(
                f"review package schema is unavailable: {exc}"
            ) from exc
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda item: list(item.path),
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors[:8]
            )
            raise TechnicalFulltextReviewPackageError(
                f"review package schema validation failed: {detail}"
            )

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        summary = payload["summary"]
        lines = [
            "# 全文人工审核包",
            "",
            f"> {MARKDOWN_NOTICE}",
            f"> 审核正文 SHA256：`{payload['reviewed_content_sha256']}`",
            "",
            "## 总览",
            "",
            f"- Requirement：{summary['requirement_count']}",
            f"- 人工待审核：{summary['review_item_count']}（FAI {summary['fai_count']} / uncertain FSF {summary['uncertain_fsf_count']} / pending FCA {summary['pending_fca_count']}）",
            f"- Machine issue：blocking {summary['blocking_count']} / warning {summary['warning_count']}",
            f"- 技术评分：满分 {summary['technical_score_max']}；confirmed {summary['confirmed_score']}；estimated {summary['estimated_score_range']['min']}-{summary['estimated_score_range']['max']}；pending {summary['pending_score_max']}",
            f"- 待补事实：{summary['missing_fact_count']}；未执行材料查询：{summary['evidence_query_not_executed_count']}；已验证材料：{summary['verified_material_count']}",
            "",
            "## 人工审核队列",
            "",
        ]
        for item in payload["review_items"]:
            lines.extend(
                [
                    f"### {item['source_type']} · {item['source_id']} · {item['display_title']}",
                    "",
                    f"- 机器结论：{item['machine_issue_summary']}",
                    f"- severity：{item['machine_severity']} / release_blocking={item['release_blocking']}",
                    f"- 招标要求：{item['requirement_text'] or '-'}",
                    f"- 当前位置：{cls_location(item.get('current_response_location'))}",
                    f"- 当前响应：{item['current_response_excerpt'] or '-'}",
                    f"- 建议：{(item.get('machine_recommended_action') or {}).get('text') or '-'}",
                    f"- Stage13 语义：{item['handoff_mode']} / must_decide={item['must_decide_before_stage13']}",
                    f"- 显式依赖：facts {', '.join(item['required_fact_refs']) or '-'}；materials {', '.join(item['required_material_refs']) or '-'}；confirmations {', '.join(item['required_confirmation_refs']) or '-'}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Requirement Response Matrix",
                "",
                "| TR | 招标要求 | 必须响应 | 当前状态 | 当前响应位置 | 当前具体响应内容 | Finding |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in payload["response_matrix"]:
            locations = "、".join(
                f"{value['chapter_id']}/{value['section_id']}"
                for value in item["response_locations"]
            )
            excerpt = item["response_excerpts"][0]["excerpt"] if item["response_excerpts"] else "-"
            lines.append(
                f"| {item['requirement_id']} | {_compact(item['requirement_text'], 260)} | "
                f"{'是' if item['mandatory_response'] else '否'} | {item['response_status']} | "
                f"{locations or '-'} | {_compact(excerpt, 360)} | {', '.join(item['finding_refs']) or '-'} |"
            )
        lines.extend(
            [
                "",
                "## Scoring Matrix",
                "",
                "| SCORE | 评分细则 | 方法 | 状态 | 满分 | confirmed | estimated | 区间 | potential |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for item in payload["scoring_matrix"]:
            lines.append(
                f"| {item['score_item_id']} | {_compact(item['criteria_text'], 240)} | "
                f"{item['assessment_method']} | {item['assessment_status']} | {item['max_score']} | "
                f"{item['confirmed_score']} | {item['estimated_score']} | "
                f"{item['score_range']['min']}-{item['score_range']['max']} | "
                f"{item['potential_gain_range']['min']}-{item['potential_gain_range']['max']} |"
            )
        lines.extend(
            [
                "",
                "## Fact / Material Queue",
                "",
                "| 类型 | 来源 | 状态 | 待办说明 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in payload["fact_material_queue"]:
            lines.append(
                f"| {item['queue_type']} | {item['source_id']} · {item['display_title']} | "
                f"{item['status']} | {item['description']} |"
            )
        lines.extend(
            [
                "",
                "## 机器风险摘要",
                "",
                f"- Blocking：{len(payload['machine_risk_summary']['blocking_machine_issues'])}",
                f"- Warning：{len(payload['machine_risk_summary']['warning_machine_issues'])}",
                f"- Uncertain：{len(payload['machine_risk_summary']['uncertain_items'])}",
                f"- Mandatory response gaps：{len(payload['machine_risk_summary']['mandatory_response_gaps'])}",
                f"- Pending score items：{payload['machine_risk_summary']['score_pending_exposure']['pending_item_count']}",
                "",
                "## Stage13 前置依赖",
                "",
                "- 完整 Stage11.2 iterative replay 稳定性仍须在 Stage13 新 revision 前解决；本轮未放宽任何运行时校验。",
                "",
            ]
        )
        return "\n".join(lines)


def cls_location(value: dict[str, Any] | None) -> str:
    if not value:
        return "-"
    return "/".join(
        str(item or "-")
        for item in (
            value.get("chapter_title") or value.get("chapter_id"),
            value.get("section_heading") or value.get("section_id"),
            value.get("paragraph_id"),
        )
    )


def inspect_technical_fulltext_review_package(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    payload = load_json_object(target, label="technical fulltext review package")
    TechnicalFulltextReviewPackageAssembler()._validate_payload(payload)
    return {
        "schema_version": payload["schema_version"],
        "artifact_identity": deepcopy(payload["artifact_identity"]),
        "reviewed_content_sha256": payload["reviewed_content_sha256"],
        "summary": deepcopy(payload["summary"]),
        "source_lineage": deepcopy(payload["source_lineage"]),
    }

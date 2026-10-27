from __future__ import annotations

import os
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

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
    SCHEMA_VERSION as SCORING_ASSESSMENT_SCHEMA_VERSION,
    TechnicalFulltextScoringAssessor,
)


FINALIZATION_POLICY_VERSION = "fulltext_audit_severity_policy_v1"
FINAL_MARKDOWN_NOTICE = "Stage11.4 全文机器审计收口已完成（历史别名 Stage11-D）。"


class TechnicalFulltextAuditFinalizationError(RuntimeError):
    """Raised when an existing Stage11.3 handoff cannot be finalized safely."""


# No fallback exists by design. New semantic issue codes must be reviewed here first.
AUDIT_SEVERITY_POLICY: dict[tuple[str, str], tuple[str, bool]] = {
    ("global_consistency", "technical_parameter_conflict"): ("blocking", True),
    ("global_consistency", "commitment_conflict"): ("blocking", True),
    ("global_consistency", "duration_conflict"): ("blocking", True),
    ("global_consistency", "numeric_value_conflict"): ("blocking", True),
    ("global_consistency", "product_identity_conflict"): ("blocking", True),
    ("global_consistency", "person_identity_conflict"): ("blocking", True),
    ("global_consistency", "stable_fact_conflict"): ("blocking", True),
    ("content_ownership", "coverage_content_mismatch"): ("blocking", True),
    ("content_ownership", "misplaced_content"): ("warning", False),
    ("content_ownership", "misplaced_requirement_content"): ("warning", False),
    ("content_ownership", "redundant_response"): ("warning", False),
    ("content_ownership", "redundant_requirement_response"): ("warning", False),
    ("cross_content_duplication", "exact_cross_chapter_duplicate"): ("warning", False),
    ("cross_content_duplication", "semantic_cross_chapter_duplicate"): ("warning", False),
    ("confirmation_governance", "confirmation_alias_fragmentation"): ("warning", False),
    ("global_consistency", "term_alias_inconsistency"): ("warning", False),
    ("chapter_coherence", "repeated_reintroduction"): ("warning", False),
    ("chapter_coherence", "context_reset"): ("warning", False),
    ("chapter_coherence", "logical_jump"): ("warning", False),
    ("chapter_coherence", "weak_transition"): ("info", False),
    ("score_distribution", "redundant_score_response"): ("warning", False),
    ("score_distribution", "misplaced_score_response"): ("warning", False),
}


DECISION_VERDICTS: dict[str, str] = {
    "same_fact": "issue",
    "different_fact": "acceptable",
    "uncertain": "uncertain",
    "acceptable_reference": "acceptable",
    "misplaced_content": "issue",
    "redundant_response": "issue",
    "coverage_content_mismatch": "issue",
    "acceptable": "acceptable",
    "different_attribute": "acceptable",
    "commitment_conflict": "issue",
    "value_conflict": "issue",
    "consistent": "acceptable",
    "context_different": "acceptable",
    "stable_fact_conflict": "issue",
    "same_entity_variant": "issue",
    "different_entity": "acceptable",
    "acceptable_context": "acceptable",
    "meaningful_duplicate": "issue",
    "necessary_restatement": "acceptable",
    "different_content": "acceptable",
    "appropriate_distribution": "acceptable",
    "redundant_score_response": "issue",
    "misplaced_score_response": "issue",
    "coherent": "acceptable",
    "repeated_reintroduction": "issue",
    "context_reset": "issue",
    "weak_transition": "issue",
    "logical_jump": "issue",
}

PASS_DECISIONS: dict[str, set[str]] = {
    "placeholder_alias": {"same_fact", "different_fact", "uncertain"},
    "requirement_location": {
        "acceptable_reference", "misplaced_content", "redundant_response",
        "coverage_content_mismatch", "uncertain",
    },
    "numeric_consistency": {
        "acceptable", "different_attribute", "commitment_conflict",
        "value_conflict", "uncertain",
    },
    "cross_section_stable_fact_consistency": {
        "consistent", "context_different", "stable_fact_conflict", "uncertain",
    },
    "terminology_consistency": {
        "same_entity_variant", "different_entity", "acceptable_context", "uncertain",
    },
    "near_duplicate": {
        "meaningful_duplicate", "necessary_restatement", "different_content", "uncertain",
    },
    "score_distribution": {
        "appropriate_distribution", "redundant_score_response",
        "misplaced_score_response", "uncertain",
    },
    "adjacent_chapter_coherence": {
        "coherent", "repeated_reintroduction", "context_reset",
        "weak_transition", "logical_jump", "uncertain",
    },
}


def _unique(values: Iterable[Any]) -> list[str]:
    return sorted({str(value or "").strip() for value in values if str(value or "").strip()})


def _stable_id(prefix: str, identity: Any, length: int = 16) -> str:
    return prefix + stable_json_sha256(identity)[:length].upper()


def _location_anchor(location: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(location.get("chapter_id") or ""),
        str(location.get("section_id") or ""),
        str(location.get("paragraph_id") or ""),
        str(location.get("text_sha256") or ""),
    )


def semantic_finding_id(finding: dict[str, Any]) -> str:
    """Recompute the Stage 11-C FSF identity without natural-language fields."""
    return _stable_id("FSF-", {
        "pass_type": finding.get("pass_type"),
        "source_candidate_ids": sorted(finding.get("source_candidate_ids") or []),
        "audit_unit_id": finding.get("audit_unit_id"),
        "verdict": finding.get("verdict"),
        "subject": (finding.get("subject") or {}).get("canonical_key"),
        "locations": sorted(_location_anchor(item) for item in finding.get("locations") or []),
    })


def _canonical_issue_identity(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_type": issue["audit_type"],
        "code": issue["code"],
        "canonical_subject": issue["subject"],
        "location_anchors": sorted(_location_anchor(item) for item in issue["locations"]),
        "requirement_ids": sorted(issue.get("requirement_ids") or []),
        "score_item_ids": sorted(issue.get("score_item_ids") or []),
    }


def _issue_id(issue: dict[str, Any]) -> str:
    return _stable_id("FAI-", _canonical_issue_identity(issue))


class TechnicalFulltextAuditFinalizer:
    """Deterministically close Stage 11 without calling any model or changing prose."""

    def finalize(self, *, audit_json_path: str | Path) -> dict[str, Any]:
        target = Path(audit_json_path).expanduser().resolve()
        try:
            payload = load_json_object(target, label="technical fulltext audit")
            original_sha = sha256_file(target)
            TechnicalFulltextAuditor()._validate_audit_payload(payload)
        except (FileNotFoundError, OSError, ValueError, TechnicalFulltextAuditError) as exc:
            raise TechnicalFulltextAuditFinalizationError(
                f"Stage11.3 audit handoff is unavailable or invalid: {exc}"
            ) from exc

        self._validate_preconditions(payload)
        self._revalidate_source_snapshot(payload)
        scoring_assessment = self._revalidate_scoring_assessment(payload)
        findings = self._validate_semantic_findings(payload)

        finalization_completed = (
            (payload.get("audit_passes") or {}).get("finalization", {}).get("status")
            == "completed"
        )
        if finalization_completed:
            deterministic_source = deepcopy(payload.get("deterministic_source_issues") or [])
        else:
            deterministic_source = deepcopy(payload.get("issues") or [])
        self._validate_source_issues(deterministic_source)

        promoted = [
            self._promote_finding(finding)
            for finding in findings
            if finding["verdict"] == "issue"
        ]
        pre_merge_count = len(deterministic_source) + len(promoted)
        final_issues = self._merge_issues(deterministic_source + promoted)
        issue_by_finding = {
            finding_id: issue["issue_id"]
            for issue in final_issues
            for finding_id in issue.get("source_finding_ids") or []
        }
        dispositions = [
            {
                "finding_id": finding["finding_id"],
                "verdict": finding["verdict"],
                "disposition": (
                    "promoted"
                    if finding["verdict"] == "issue"
                    else "accepted"
                    if finding["verdict"] == "acceptable"
                    else "human_review"
                ),
                "issue_id": issue_by_finding.get(finding["finding_id"]),
            }
            for finding in sorted(findings, key=lambda item: item["finding_id"])
        ]
        if any(
            item["disposition"] == "promoted" and not item["issue_id"]
            for item in dispositions
        ):
            raise TechnicalFulltextAuditFinalizationError(
                "one or more semantic issue findings were not traceably promoted"
            )

        relationships = self._confirmation_relationships(
            findings=findings,
            confirmations=payload.get("confirmation_register") or [],
        )
        issue_counts = {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
        for issue in final_issues:
            issue_counts[issue["severity"]] += 1
        uncertain_count = sum(item["verdict"] == "uncertain" for item in findings)
        if issue_counts["fatal"] or issue_counts["blocking"]:
            audit_result = "blocked"
        elif final_issues or uncertain_count:
            audit_result = "reviewable"
        else:
            audit_result = "clean"

        disposition_counts = Counter(item["disposition"] for item in dispositions)
        updated = deepcopy(payload)
        updated["deterministic_source_issues"] = deterministic_source
        updated["issues"] = final_issues
        updated["issue_counts"] = issue_counts
        updated["semantic_finding_dispositions"] = dispositions
        updated["confirmation_relationships"] = relationships
        updated["audit_passes"]["finalization"] = {"status": "completed"}
        updated["audit_result"] = audit_result
        updated["release_ready"] = False
        updated["finalization"] = {
            "status": "completed",
            "policy_version": FINALIZATION_POLICY_VERSION,
            "model_call_count": 0,
            "semantic_findings_total": len(findings),
            "promoted_findings": disposition_counts["promoted"],
            "accepted_findings": disposition_counts["accepted"],
            "human_review_findings": disposition_counts["human_review"],
            "deterministic_source_issue_count": len(deterministic_source),
            "semantic_promoted_issue_count": len(promoted),
            "pre_merge_issue_count": pre_merge_count,
            "merge_reduction_count": pre_merge_count - len(final_issues),
            "merged_issue_count": len(final_issues),
            "final_issue_count": len(final_issues),
            "uncertain_count": uncertain_count,
            "scoring_assessment_id": scoring_assessment["assessment_id"],
            "scoring_item_count": len(scoring_assessment["score_items"]),
            "technical_score_max": scoring_assessment["summary"]["technical_score_max"],
            "finalized_at": utc_now(),
        }
        updated["generated_at"] = utc_now()
        TechnicalFulltextAuditor()._validate_audit_payload(updated)

        if sha256_file(target) != original_sha:
            raise TechnicalFulltextAuditFinalizationError(
                "Stage11.2 audit artifact changed during finalization"
            )
        self._revalidate_source_snapshot(payload)
        markdown = self._render_markdown(updated)
        markdown_path = target.with_suffix(".md")
        self._persist_pair(
            audit_path=target,
            audit_payload=updated,
            markdown_path=markdown_path,
            markdown=markdown,
        )
        return {
            "status": "completed",
            "audit_result": audit_result,
            "release_ready": False,
            "issue_counts": issue_counts,
            "finalization": deepcopy(updated["finalization"]),
            "issues": deepcopy(final_issues),
            "semantic_finding_dispositions": deepcopy(dispositions),
            "confirmation_relationships": deepcopy(relationships),
            "scoring_assessment_ref": deepcopy(updated["scoring_assessment_ref"]),
            "outputs": {
                "technical_fulltext_audit_json": str(target),
                "technical_fulltext_audit_json_sha256": sha256_file(target),
                "technical_fulltext_audit_markdown": str(markdown_path),
                "technical_fulltext_audit_markdown_sha256": sha256_file(markdown_path),
            },
        }

    @staticmethod
    def _validate_preconditions(payload: dict[str, Any]) -> None:
        passes = payload.get("audit_passes") or {}
        semantic = payload.get("semantic_audit") or {}
        if payload.get("status") != "completed":
            raise TechnicalFulltextAuditFinalizationError("audit status must be completed")
        if passes.get("deterministic", {}).get("status") != "completed":
            raise TechnicalFulltextAuditFinalizationError(
                "deterministic pass must be completed"
            )
        if passes.get("semantic", {}).get("status") != "completed":
            raise TechnicalFulltextAuditFinalizationError(
                "semantic pass must be completed"
            )
        if semantic.get("status") != "completed":
            raise TechnicalFulltextAuditFinalizationError(
                "semantic_audit status must be completed"
            )
        if passes.get("scoring", {}).get("status") != "completed":
            raise TechnicalFulltextAuditFinalizationError(
                "Stage11.3 scoring assessment must be completed before Stage11.4"
            )
        scoring_ref = payload.get("scoring_assessment_ref") or {}
        if scoring_ref.get("status") != "completed":
            raise TechnicalFulltextAuditFinalizationError(
                "scoring_assessment_ref must be completed before Stage11.4"
            )
        if payload.get("release_ready") is not False:
            raise TechnicalFulltextAuditFinalizationError(
                "Stage 11 finalization input must remain release_ready=false"
            )

    @staticmethod
    def _revalidate_scoring_assessment(payload: dict[str, Any]) -> dict[str, Any]:
        reference = payload.get("scoring_assessment_ref") or {}
        target = Path(str(reference.get("path") or "")).expanduser().resolve()
        try:
            assessment = load_json_object(target, label="technical fulltext scoring assessment")
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise TechnicalFulltextAuditFinalizationError(
                f"Stage11.3 scoring assessment is unavailable: {exc}"
            ) from exc
        if reference.get("schema_version") != SCORING_ASSESSMENT_SCHEMA_VERSION:
            raise TechnicalFulltextAuditFinalizationError(
                "scoring assessment reference schema is unsupported"
            )
        if sha256_file(target) != reference.get("sha256"):
            raise TechnicalFulltextAuditFinalizationError(
                "scoring assessment SHA does not match the Stage11 handoff"
            )
        try:
            TechnicalFulltextScoringAssessor()._validate_payload(assessment)
        except Exception as exc:
            raise TechnicalFulltextAuditFinalizationError(
                f"scoring assessment contract is invalid: {exc}"
            ) from exc
        audited = (payload.get("source_lineage") or {}).get("audited_fulltext") or {}
        scoring_audited = assessment.get("audited_fulltext") or {}
        if (
            assessment.get("document_id") != payload.get("document_id")
            or assessment.get("output_key") != payload.get("output_key")
            or scoring_audited.get("document_id") != audited.get("document_id")
            or scoring_audited.get("content_sha256") != audited.get("content_sha256")
            or scoring_audited.get("file_sha256") != audited.get("file_sha256")
        ):
            raise TechnicalFulltextAuditFinalizationError(
                "scoring assessment is not bound to the audited fulltext"
            )
        return assessment

    @staticmethod
    def _revalidate_source_snapshot(payload: dict[str, Any]) -> None:
        try:
            resolved = TechnicalFulltextAuditor.revalidate_audit_source(payload)
        except TechnicalFulltextAuditError as exc:
            raise TechnicalFulltextAuditFinalizationError(
                f"audit source snapshot is no longer trusted: {exc}"
            ) from exc
        audited_content_sha = (
            (payload.get("source_lineage") or {}).get("audited_fulltext") or {}
        ).get("content_sha256")
        if audited_content_sha != stable_json_sha256(resolved["fulltext"]):
            raise TechnicalFulltextAuditFinalizationError(
                "finalization source content identity mismatch"
            )

    @staticmethod
    def _validate_semantic_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
        findings = payload.get("semantic_findings")
        if not isinstance(findings, list):
            raise TechnicalFulltextAuditFinalizationError(
                "semantic_findings must be an array"
            )
        semantic = payload.get("semantic_audit") or {}
        if semantic.get("finding_count") != len(findings):
            raise TechnicalFulltextAuditFinalizationError(
                "semantic finding_count does not match semantic_findings"
            )
        if semantic.get("audit_unit_count") != len(findings):
            raise TechnicalFulltextAuditFinalizationError(
                "semantic audit_unit_count does not match semantic_findings"
            )
        expected_verdicts = Counter(item.get("verdict") for item in findings)
        recorded_verdicts = semantic.get("verdict_counts") or {}
        if any(
            int(recorded_verdicts.get(name) or 0) != expected_verdicts[name]
            for name in ("issue", "acceptable", "uncertain")
        ):
            raise TechnicalFulltextAuditFinalizationError(
                "semantic verdict counts do not match semantic_findings"
            )
        findings_by_pass: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            findings_by_pass.setdefault(str(finding.get("pass_type") or ""), []).append(
                finding
            )
        pass_summaries = semantic.get("passes") or {}
        for pass_type, summary in pass_summaries.items():
            pass_findings = findings_by_pass.get(pass_type) or []
            pass_verdicts = Counter(item.get("verdict") for item in pass_findings)
            if summary.get("finding_count") != len(pass_findings) or summary.get(
                "audit_unit_count"
            ) != len(pass_findings):
                raise TechnicalFulltextAuditFinalizationError(
                    f"semantic pass counts do not match findings: {pass_type}"
                )
            if any(
                int((summary.get("verdict_counts") or {}).get(name) or 0)
                != pass_verdicts[name]
                for name in ("issue", "acceptable", "uncertain")
            ):
                raise TechnicalFulltextAuditFinalizationError(
                    f"semantic pass verdict counts do not match findings: {pass_type}"
                )
        if set(findings_by_pass) - set(pass_summaries):
            raise TechnicalFulltextAuditFinalizationError(
                "semantic findings contain an unknown pass type"
            )

        normalized = payload.get("normalized_document") or {}
        paragraph_by_id = {
            item["paragraph_id"]: item for item in normalized.get("paragraphs") or []
        }
        requirement_ids = {
            item["requirement_id"] for item in normalized.get("requirements") or []
        }
        score_item_ids = {
            item["score_item_id"] for item in normalized.get("score_items") or []
        }
        placeholder_ids = {
            item["placeholder_id"] for item in normalized.get("placeholders") or []
        }
        candidate_ids = {
            item["candidate_id"]
            for values in (payload.get("semantic_candidates") or {}).values()
            for item in values
        }
        seen: set[str] = set()
        for finding in findings:
            finding_id = str(finding.get("finding_id") or "")
            if not finding_id or finding_id in seen:
                raise TechnicalFulltextAuditFinalizationError(
                    "semantic finding identity is missing or duplicated"
                )
            seen.add(finding_id)
            if finding_id != semantic_finding_id(finding):
                raise TechnicalFulltextAuditFinalizationError(
                    f"semantic finding identity mismatch: {finding_id}"
                )
            decision = str(finding.get("decision") or "")
            verdict = str(finding.get("verdict") or "")
            pass_type = str(finding.get("pass_type") or "")
            if (
                decision not in PASS_DECISIONS.get(pass_type, set())
                or DECISION_VERDICTS.get(decision) != verdict
            ):
                raise TechnicalFulltextAuditFinalizationError(
                    f"semantic decision/verdict contract mismatch: {finding_id}"
                )
            unknown_candidates = set(finding.get("source_candidate_ids") or []) - candidate_ids
            unknown_requirements = set(finding.get("requirement_ids") or []) - requirement_ids
            unknown_scores = set(finding.get("score_item_ids") or []) - score_item_ids
            unknown_placeholders = set(finding.get("placeholder_ids") or []) - placeholder_ids
            if unknown_candidates or unknown_requirements or unknown_scores or unknown_placeholders:
                raise TechnicalFulltextAuditFinalizationError(
                    f"semantic finding contains unknown source IDs: {finding_id}"
                )
            for location in finding.get("locations") or []:
                paragraph = paragraph_by_id.get(location.get("paragraph_id"))
                if not paragraph or any(
                    location.get(field) != paragraph.get(field)
                    for field in (
                        "chapter_id",
                        "section_id",
                        "paragraph_id",
                        "paragraph_index",
                        "text_sha256",
                    )
                ):
                    raise TechnicalFulltextAuditFinalizationError(
                        f"semantic finding location is not traceable: {finding_id}"
                    )
                if not str(paragraph.get("text") or "").startswith(
                    str(location.get("excerpt") or "")
                ):
                    raise TechnicalFulltextAuditFinalizationError(
                        f"semantic finding excerpt is not traceable: {finding_id}"
                    )
        return deepcopy(findings)

    @staticmethod
    def _validate_source_issues(issues: list[dict[str, Any]]) -> None:
        for issue in issues:
            policy_key = (str(issue.get("audit_type") or ""), str(issue.get("code") or ""))
            if policy_key not in AUDIT_SEVERITY_POLICY:
                raise TechnicalFulltextAuditFinalizationError(
                    f"unsupported deterministic issue policy: {policy_key}"
                )

    @staticmethod
    def _promote_finding(finding: dict[str, Any]) -> dict[str, Any]:
        if finding.get("verdict") != "issue":
            raise TechnicalFulltextAuditFinalizationError(
                "only verdict=issue may be promoted"
            )
        policy_key = (finding["audit_type"], finding["code"])
        policy = AUDIT_SEVERITY_POLICY.get(policy_key)
        if policy is None:
            raise TechnicalFulltextAuditFinalizationError(
                f"unsupported semantic promotion policy: {policy_key}"
            )
        severity, release_blocking = policy
        locations = sorted(deepcopy(finding.get("locations") or []), key=_location_anchor)
        subject = str((finding.get("subject") or {}).get("canonical_key") or "").strip()
        if not subject or not locations:
            raise TechnicalFulltextAuditFinalizationError(
                f"semantic issue finding lacks promotion identity: {finding.get('finding_id')}"
            )
        issue = {
            "issue_id": "",
            "audit_type": finding["audit_type"],
            "code": finding["code"],
            "severity": severity,
            "release_blocking": release_blocking,
            "subject": subject,
            "chapter_ids": _unique(item.get("chapter_id") for item in locations),
            "section_ids": _unique(item.get("section_id") for item in locations),
            "locations": locations,
            "summary": finding["summary"],
            "rationale": finding["rationale"],
            "details": {
                "semantic_subject": deepcopy(finding["subject"]),
                "semantic_details": deepcopy(finding.get("details") or {}),
                "decision": finding["decision"],
                "confidence": finding["confidence"],
            },
            "requirement_ids": _unique(finding.get("requirement_ids") or []),
            "score_item_ids": _unique(finding.get("score_item_ids") or []),
            "source_finding_ids": [finding["finding_id"]],
            "source_candidate_ids": _unique(finding.get("source_candidate_ids") or []),
            "detection": {
                "methods": ["semantic_finding_promotion"],
                "pass_types": [finding["pass_type"]],
                "policy_version": FINALIZATION_POLICY_VERSION,
            },
            "recommended_action": deepcopy(finding["recommended_action"]),
            "requires_human_review": True,
        }
        issue["issue_id"] = _issue_id(issue)
        return issue

    @staticmethod
    def _normalize_source_issue(issue: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(issue)
        policy_key = (result["audit_type"], result["code"])
        severity, release_blocking = AUDIT_SEVERITY_POLICY[policy_key]
        result["severity"] = severity
        result["release_blocking"] = release_blocking
        result["subject"] = str(result.get("subject") or "")
        result["locations"] = sorted(result.get("locations") or [], key=_location_anchor)
        result["chapter_ids"] = _unique(item.get("chapter_id") for item in result["locations"])
        result["section_ids"] = _unique(item.get("section_id") for item in result["locations"])
        result["requirement_ids"] = _unique(result.get("requirement_ids") or [])
        result["score_item_ids"] = _unique(result.get("score_item_ids") or [])
        result["source_finding_ids"] = _unique(result.get("source_finding_ids") or [])
        result["source_candidate_ids"] = _unique(result.get("source_candidate_ids") or [])
        detection = result.get("detection") if isinstance(result.get("detection"), dict) else {}
        methods = detection.get("methods") or [detection.get("method") or "deterministic_audit"]
        result["detection"] = {
            "methods": _unique(methods),
            "pass_types": _unique(detection.get("pass_types") or []),
            "policy_version": FINALIZATION_POLICY_VERSION,
        }
        result["issue_id"] = _issue_id(result)
        return result

    def _merge_issues(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = [self._normalize_source_issue(issue) for issue in issues]
        groups: list[list[dict[str, Any]]] = []
        for issue in sorted(normalized, key=lambda item: item["issue_id"]):
            anchors = {_location_anchor(item) for item in issue["locations"]}
            core = (
                issue["audit_type"],
                issue["code"],
                issue["subject"],
                tuple(issue["requirement_ids"]),
                tuple(issue["score_item_ids"]),
            )
            destination: list[dict[str, Any]] | None = None
            for group in groups:
                representative = group[0]
                representative_core = (
                    representative["audit_type"],
                    representative["code"],
                    representative["subject"],
                    tuple(representative["requirement_ids"]),
                    tuple(representative["score_item_ids"]),
                )
                group_anchors = {
                    _location_anchor(location)
                    for member in group
                    for location in member["locations"]
                }
                if core == representative_core and (
                    anchors == group_anchors
                    or anchors.issubset(group_anchors)
                    or group_anchors.issubset(anchors)
                ):
                    destination = group
                    break
            if destination is None:
                groups.append([issue])
            else:
                destination.append(issue)

        merged: list[dict[str, Any]] = []
        for group in groups:
            primary = sorted(
                group,
                key=lambda item: (
                    0 if not item.get("source_finding_ids") else 1,
                    item["issue_id"],
                ),
            )[0]
            result = deepcopy(primary)
            location_by_anchor = {
                _location_anchor(location): deepcopy(location)
                for item in group
                for location in item["locations"]
            }
            result["locations"] = [
                location_by_anchor[anchor] for anchor in sorted(location_by_anchor)
            ]
            result["chapter_ids"] = _unique(item.get("chapter_id") for item in result["locations"])
            result["section_ids"] = _unique(item.get("section_id") for item in result["locations"])
            result["source_finding_ids"] = _unique(
                value for item in group for value in item.get("source_finding_ids") or []
            )
            result["source_candidate_ids"] = _unique(
                value for item in group for value in item.get("source_candidate_ids") or []
            )
            result["requirement_ids"] = _unique(
                value for item in group for value in item.get("requirement_ids") or []
            )
            result["score_item_ids"] = _unique(
                value for item in group for value in item.get("score_item_ids") or []
            )
            result["detection"] = {
                "methods": _unique(
                    value
                    for item in group
                    for value in (item.get("detection") or {}).get("methods") or []
                ),
                "pass_types": _unique(
                    value
                    for item in group
                    for value in (item.get("detection") or {}).get("pass_types") or []
                ),
                "policy_version": FINALIZATION_POLICY_VERSION,
            }
            severity, release_blocking = AUDIT_SEVERITY_POLICY[
                (result["audit_type"], result["code"])
            ]
            result["severity"] = severity
            result["release_blocking"] = release_blocking
            result["issue_id"] = _issue_id(result)
            merged.append(result)
        return sorted(merged, key=lambda item: item["issue_id"])

    @staticmethod
    def _confirmation_relationships(
        *, findings: list[dict[str, Any]], confirmations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        confirmation_by_placeholder = {
            placeholder_id: confirmation["confirmation_id"]
            for confirmation in confirmations
            for placeholder_id in confirmation.get("member_placeholder_ids") or []
        }
        relationships: list[dict[str, Any]] = []
        for finding in findings:
            if finding.get("pass_type") != "placeholder_alias":
                continue
            placeholder_ids = _unique(finding.get("placeholder_ids") or [])
            confirmation_ids = _unique(
                confirmation_by_placeholder.get(value) for value in placeholder_ids
            )
            if len(confirmation_ids) != len(placeholder_ids):
                raise TechnicalFulltextAuditFinalizationError(
                    f"alias finding cannot be related to every FCA: {finding.get('finding_id')}"
                )
            relationships.append({
                "relationship_id": _stable_id("FCR-", {
                    "placeholder_ids": placeholder_ids,
                    "confirmation_ids": confirmation_ids,
                    "relationship_type": "possible_alias",
                }),
                "finding_id": finding["finding_id"],
                "relationship_type": (
                    "possible_alias"
                    if finding["verdict"] == "uncertain"
                    else "confirmed_fragmentation"
                    if finding["verdict"] == "issue"
                    else "adjudicated_distinct"
                ),
                "verdict": finding["verdict"],
                "placeholder_ids": placeholder_ids,
                "confirmation_ids": confirmation_ids,
                "requires_human_review": finding["verdict"] != "acceptable",
            })
        return sorted(relationships, key=lambda item: item["relationship_id"])

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        finalization = payload["finalization"]
        semantic = payload["semantic_audit"]
        issues = payload["issues"]
        uncertain = [
            item for item in payload["semantic_findings"] if item["verdict"] == "uncertain"
        ]
        confirmations = payload["confirmation_register"]
        relationships = payload["confirmation_relationships"]
        candidates = payload["semantic_candidates"]
        upstream = payload["upstream_status"]
        lines = [
            "# 全文机器审计报告",
            "",
            f"> {FINAL_MARKDOWN_NOTICE}",
            "> Model finding 只有经过 Runtime policy 晋升后才构成正式 FAI。",
            "",
            "## 1. 审计结论",
            "",
            f"- status：{payload['status']}",
            f"- audit_result：{payload['audit_result']}",
            "- release_ready：false",
            "- Deterministic Audit：completed",
            "- Semantic Audit：completed",
            "- Scoring Assessment：completed",
            "- Finalization：completed",
            "- Stage11.4（历史 Stage11-D）模型调用：0",
            "",
            "## 2. 正式全文问题",
            "",
            f"- FAI 总数：{len(issues)}",
            f"- fatal：{payload['issue_counts']['fatal']}",
            f"- blocking：{payload['issue_counts']['blocking']}",
            f"- warning：{payload['issue_counts']['warning']}",
            f"- info：{payload['issue_counts']['info']}",
            "",
        ]
        for severity in ("fatal", "blocking", "warning", "info"):
            selected = [item for item in issues if item["severity"] == severity]
            if not selected:
                continue
            lines.extend([f"### {severity}", ""])
            for issue in selected:
                evidence_excerpt = TechnicalFulltextAuditFinalizer._human_evidence_excerpt(
                    issue, payload
                )
                lines.extend([
                    f"#### {issue['issue_id']} · {issue['code']}",
                    "",
                    f"- Subject：{issue['subject']}",
                    f"- Chapter：{', '.join(issue['chapter_ids'])}",
                    f"- Section：{', '.join(issue['section_ids'])}",
                    f"- Requirement：{', '.join(issue['requirement_ids']) or '-'}",
                    f"- Source FSF：{', '.join(issue.get('source_finding_ids') or []) or '-'}",
                    f"- 原文片段：{evidence_excerpt}",
                    f"- 问题说明：{issue['summary']}",
                    f"- 判定依据：{issue['rationale']}",
                    f"- 建议处理：{issue['recommended_action']['text']}",
                    "",
                ])
        if not issues:
            lines.extend(["- 没有正式 FAI。", ""])
        lines.extend([
            "## 3. 未决语义事项",
            "",
            f"- uncertain findings：{len(uncertain)}",
            "- 以下事项尚未确认构成全文问题，需要人工审核；它们未进入 issues[]。",
            "",
        ])
        for finding in uncertain:
            related = [
                item for item in relationships if item["finding_id"] == finding["finding_id"]
            ]
            relation_text = ", ".join(
                value for item in related for value in item["confirmation_ids"]
            ) or ", ".join(finding.get("requirement_ids") or []) or "-"
            lines.append(
                f"- {finding['finding_id']} [{finding['pass_type']}] "
                f"关联={relation_text}：{finding['summary']}"
            )
        lines.extend([
            "",
            "## 4. 待确认事实治理",
            "",
            f"- FCA 总数：{len(confirmations)}",
            f"- Placeholder occurrences：{payload['metrics']['placeholder_occurrence_count']}",
            f"- Alias 关系：{len(relationships)}",
            "- FCA 全部保持 pending；pending 不等于 confirmed，关系表不执行合并。",
            "",
            "## 5. Semantic Audit 统计",
            "",
            f"- issue verdict：{semantic['verdict_counts']['issue']}",
            f"- acceptable：{semantic['verdict_counts']['acceptable']}",
            f"- uncertain：{semantic['verdict_counts']['uncertain']}",
            f"- promoted：{finalization['promoted_findings']}",
            f"- accepted disposition：{finalization['accepted_findings']}",
            f"- human review disposition：{finalization['human_review_findings']}",
            f"- Scoring item：{finalization['scoring_item_count']}",
            f"- Technical score max：{finalization['technical_score_max']}",
            "",
            "## 6. Coverage / Candidate 摘要",
            "",
            f"- Requirement unique：{payload['metrics']['unique_requirement_count']}",
            f"- Requirement location candidates：{len(candidates['requirement_location_mismatches'])}",
            f"- Score distribution candidates：{len(candidates['score_distribution'])}",
            f"- Numeric candidates：{len(candidates['numeric_facts'])}",
            f"- Cross-section stable fact candidates：{len(candidates.get('stable_fact_consistency') or [])}",
            f"- Terminology candidates：{len(candidates['terminology'])}",
            f"- Near duplicate candidates：{len(candidates['duplicate_pairs'])}",
            "",
            "## 7. Stage ⑩ 上游状态",
            "",
            f"- production_status：{upstream.get('production_status')}",
            f"- composition_status：{upstream.get('composition_status')}",
            f"- chapter_count_blocked：{upstream.get('chapter_count_blocked')}（仅为上游背景）",
            "",
            "Stage ⑩ blocker、pending FCA 与 placeholder 数量不参与 Stage ⑪ audit_result；release_ready 仍固定为 false。",
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _human_evidence_excerpt(
        issue: dict[str, Any], payload: dict[str, Any]
    ) -> str:
        sections = {
            item["section_id"]: item
            for item in (payload.get("normalized_document") or {}).get("sections") or []
        }
        paragraphs = (
            (payload.get("normalized_document") or {}).get("paragraphs") or []
        )
        paragraphs_by_section: dict[str, list[dict[str, Any]]] = {}
        for paragraph in paragraphs:
            paragraphs_by_section.setdefault(paragraph["section_id"], []).append(paragraph)

        locations = issue.get("locations") or []
        selected = next(
            (
                item
                for item in locations
                if str(item.get("excerpt") or "").strip()
                and not str(item.get("excerpt") or "").lstrip().startswith("#")
            ),
            locations[0] if locations else {},
        )
        section_id = str(selected.get("section_id") or "")
        excerpt = str(selected.get("excerpt") or "").strip()
        if excerpt.lstrip().startswith("#") or not excerpt:
            paragraph_index = int(selected.get("paragraph_index") or 0)
            following = next(
                (
                    item
                    for item in paragraphs_by_section.get(section_id, [])
                    if int(item.get("paragraph_index") or 0) > paragraph_index
                    and str(item.get("text") or "").strip()
                    and not str(item.get("text") or "").lstrip().startswith("#")
                ),
                None,
            )
            if following is not None:
                excerpt = str(following.get("text") or "").strip()
        heading = str((sections.get(section_id) or {}).get("heading") or "").strip()
        label = f"{section_id} {heading}".strip()
        if label and excerpt:
            return f"[{label}] {excerpt}"
        return excerpt or label or "-"

    @staticmethod
    def _persist_pair(
        *,
        audit_path: Path,
        audit_payload: dict[str, Any],
        markdown_path: Path,
        markdown: str,
    ) -> None:
        temp_json = audit_path.with_name(audit_path.name + ".finalization.tmp")
        temp_markdown = markdown_path.with_name(
            markdown_path.name + ".finalization.tmp"
        )
        original_json = audit_path.read_bytes()
        original_markdown = markdown_path.read_bytes() if markdown_path.exists() else None
        try:
            atomic_write_json(
                temp_json,
                audit_payload,
                required_fields=(
                    "schema_version",
                    "issues",
                    "semantic_finding_dispositions",
                    "finalization",
                ),
            )
            atomic_write_markdown(
                temp_markdown,
                markdown,
                required_text=FINAL_MARKDOWN_NOTICE,
            )
            os.replace(temp_json, audit_path)
            os.replace(temp_markdown, markdown_path)
        except Exception as exc:
            audit_path.write_bytes(original_json)
            if original_markdown is not None:
                markdown_path.write_bytes(original_markdown)
            temp_json.unlink(missing_ok=True)
            temp_markdown.unlink(missing_ok=True)
            raise TechnicalFulltextAuditFinalizationError(
                f"final audit persistence failed: {type(exc).__name__}: {exc}"
            ) from exc

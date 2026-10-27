from __future__ import annotations

import re
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
from app.technical_writing_outline_adapter import _shorten, extract_score_items


SCHEMA_VERSION = "technical_fulltext_scoring_assessment_v1"
POLICY_VERSION = "technical_fulltext_scoring_policy_v1"
MARKDOWN_NOTICE = "Stage11.3 逐评分细则机器评估已完成。"
VERIFIED_STATUSES = {"verified", "system_verified", "human_confirmed"}
PENDING_EVIDENCE_STATUSES = {
    "query_not_executed",
    "candidate_found",
    "confirmed_missing",
    "invalid",
    "expired",
}


class TechnicalFulltextScoringAssessmentError(RuntimeError):
    """Raised when Stage11.3 scoring cannot be produced from trusted inputs."""


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _number(value: Any) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not match:
        raise TechnicalFulltextScoringAssessmentError(
            f"score value is not numeric: {value!r}"
        )
    return float(match.group())


def _clean_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(float(value), 4)


def _score_bands(criteria: str, max_score: float) -> list[float]:
    values = [_number(value) for value in re.findall(r"\d+(?:\.\d+)?\s*分", criteria)]
    values.append(max_score)
    values.append(0.0)
    return sorted(set(values), reverse=True)


def classify_assessment_method(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('criteria_summary', '')}"
    semantic = any(term in text for term in ("综合评", "瑕疵", "清晰合理", "完整、合理"))
    evidence = any(
        term in text
        for term in (
            "证书", "扫描件", "证明材料", "合同原件", "认证", "环境标志产品",
            "节能产品", "业绩", "绿色供应链",
        )
    )
    response_rule = any(term in text for term in ("未做应答", "负偏离", "每条扣", "响应性"))
    if "绿色供应链" in text and "证明材料" in text:
        return "mixed"
    if semantic and evidence:
        return "mixed"
    if response_rule:
        return "mixed"
    if semantic:
        return "semantic"
    if evidence:
        return "evidence_dependent"
    if any(term in text for term in ("每项", "每份", "每条", "数量", "有/无")):
        return "deterministic"
    return "semantic"


def _semantic_expectations(item: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    text = f"{item.get('title', '')} {item.get('criteria_summary', '')}"
    if "需求理解" in text or "需求分析" in text:
        return [
            ("项目现状", ("现状", "建设基础")),
            ("应用环境", ("应用环境",)),
            ("功能需求", ("功能需求",)),
            ("性能要求", ("性能需求", "性能要求")),
            ("统一运维需求", ("统一运维",)),
            ("实施要求", ("实施要求", "实施计划")),
        ]
    if "系统架构" in text or "方案完整性" in text:
        return [
            ("产品或系统架构", ("架构", "系统设计")),
            ("部署设计", ("部署",)),
            ("运维设计", ("运维",)),
            ("功能设计", ("功能",)),
        ]
    if "实施部署" in text:
        return [
            ("项目建设技术方案", ("技术方案", "部署方案")),
            ("设计实施周期", ("周期", "进度", "里程碑")),
            ("人员架构", ("人员", "组织架构", "项目经理")),
        ]
    if "培训" in text:
        return [
            ("培训目标与对象", ("培训目标", "培训对象")),
            ("培训内容", ("培训内容", "课程")),
            ("培训计划", ("培训计划", "培训时间")),
            ("培训考核与保障", ("培训考核", "效果保障", "考核")),
        ]
    return []


def _evidence_requirements(item: dict[str, Any]) -> list[str]:
    text = f"{item.get('title', '')} {item.get('criteria_summary', '')}"
    result: list[str] = []
    if any(term in text for term in ("环境标志产品", "节能产品")):
        result.append("product_certificate")
    if "投标人" in text and any(term in text for term in ("证书", "资质", "认证")):
        result.append("company_qualification")
    if "制造商" in text and any(term in text for term in ("证书", "资质", "认证", "履约能力")):
        result.append("manufacturer_qualification")
    if "业绩" in text:
        result.append("company_performance")
    if "绿色供应链" in text:
        result.append("green_supply_chain_evidence")
    if any(term in text for term in ("技术要求响应性", "技术指标", "偏离")):
        result.extend(["product_parameter_proof", "product_certificate"])
    return _unique(result)


def _source_order_number(value: Any) -> int:
    """Return a stable sortable source-order value without inventing an ID."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10**9


def _technical_source_order(item: dict[str, Any], original_index: int) -> tuple[int, int, int, int]:
    """Use score-table position first, then the stable selected-item order."""
    source_table = item.get("source_table") if isinstance(item.get("source_table"), dict) else {}
    source_row = item.get("source_row") if isinstance(item.get("source_row"), dict) else {}
    source_refs = item.get("source_refs") if isinstance(item.get("source_refs"), list) else []
    first_ref = next((ref for ref in source_refs if isinstance(ref, dict)), {})
    return (
        _source_order_number(source_table.get("table_index", first_ref.get("table_index"))),
        _source_order_number(source_row.get("data_row_index", source_row.get("table_row_index"))),
        _source_order_number(source_row.get("table_row_index")),
        original_index,
    )


def _assessment_source_refs(item: dict[str, Any], source_score_item_id: str) -> list[dict[str, Any]]:
    """Preserve selected-score provenance inside the schema's existing source_refs."""
    source_refs = [
        deepcopy(ref) for ref in item.get("source_refs") or [] if isinstance(ref, dict)
    ]
    if not source_refs:
        source_refs = [{}]
    for ref in source_refs:
        ref["source_score_item_id"] = source_score_item_id
        for field in ("source_template_name", "source_table", "source_row"):
            if item.get(field) is not None:
                ref.setdefault(field, deepcopy(item[field]))
    return source_refs


def materialize_assessment_score_items(
    response_outline: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Create Stage11.3-local SCORE identities for v2 selected technical rows.

    The project/source identity remains untouched in all upstream artifacts.  The
    returned mapping is only used to correlate the copied Stage11 audit with the
    assessment-local ID namespace.
    """
    selected: list[tuple[int, dict[str, Any], str]] = []
    for original_index, item in enumerate(response_outline.get("score_items") or []):
        if not isinstance(item, dict):
            continue
        source_score_item_id = str(item.get("score_item_id") or item.get("id") or "")
        if not source_score_item_id.lower().startswith("technical_"):
            continue
        selected.append((original_index, item, source_score_item_id))

    if not selected:
        legacy_items = extract_score_items(response_outline)
        return legacy_items, {
            str(item.get("score_item_id") or ""): str(item.get("score_item_id") or "")
            for item in legacy_items
            if item.get("score_item_id")
        }

    selected.sort(key=lambda entry: _technical_source_order(entry[1], entry[0]))
    materialized: list[dict[str, Any]] = []
    source_to_canonical: dict[str, str] = {}
    for ordinal, (_, item, source_score_item_id) in enumerate(selected, start=1):
        canonical_id = f"SCORE-TECH-{ordinal:03d}"
        source_to_canonical[source_score_item_id] = canonical_id
        response_point_ids = _unique(item.get("response_point_ids") or [])
        materialized.append(
            {
                "score_item_id": canonical_id,
                "title": str(item.get("title") or item.get("item_name") or source_score_item_id),
                "criteria_summary": _shorten(
                    str(item.get("criteria_summary") or item.get("score_rule") or ""), 360
                ),
                "score": item.get("score"),
                "response_point_ids": response_point_ids,
                "source_refs": _assessment_source_refs(item, source_score_item_id),
                "_source_score_item_id": source_score_item_id,
            }
        )
    return materialized, source_to_canonical


def _project_score_identities(value: Any, source_to_canonical: dict[str, str]) -> Any:
    """Project score-reference fields in an in-memory audit copy only."""
    if isinstance(value, list):
        return [_project_score_identities(item, source_to_canonical) for item in value]
    if not isinstance(value, dict):
        return value
    score_scalar_fields = {"score_item_id"}
    score_list_fields = {"score_item_ids", "score_item_refs", "covered_score_item_ids"}
    projected: dict[str, Any] = {}
    for key, item in value.items():
        if key in score_scalar_fields and isinstance(item, str):
            projected[key] = source_to_canonical.get(item, item)
        elif key in score_list_fields and isinstance(item, list):
            projected[key] = [
                source_to_canonical.get(entry, entry)
                if isinstance(entry, str)
                else _project_score_identities(entry, source_to_canonical)
                for entry in item
            ]
        else:
            projected[key] = _project_score_identities(item, source_to_canonical)
    return projected


class TechnicalFulltextScoringAssessor:
    """Assess every parsed technical score rule against one audited fulltext."""

    def __init__(self, *, schema_path: str | Path | None = None):
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path is not None
            else Path(__file__).resolve().parent.parent
            / "schemas"
            / "technical_fulltext_scoring_assessment_v1.json"
        )

    def assess(self, *, audit_json_path: str | Path) -> dict[str, Any]:
        audit_path = Path(audit_json_path).expanduser().resolve()
        try:
            audit = load_json_object(audit_path, label="technical fulltext audit")
            audit_sha = sha256_file(audit_path)
            TechnicalFulltextAuditor()._validate_audit_payload(audit)
        except (FileNotFoundError, OSError, ValueError, TechnicalFulltextAuditError) as exc:
            raise TechnicalFulltextScoringAssessmentError(
                f"Stage11.2 audit artifact is unavailable or invalid: {exc}"
            ) from exc
        self._validate_preconditions(audit)
        self._revalidate_audited_content(audit)

        response_outline_path, response_outline = self._load_context_artifact(
            audit, "response_outline"
        )
        writing_outline_path, writing_outline = self._load_context_artifact(
            audit, "writing_outline"
        )
        score_items, source_to_canonical = materialize_assessment_score_items(response_outline)
        self._validate_score_catalog(score_items, writing_outline)
        total_max = sum(_number(item.get("score")) for item in score_items)
        assessment_audit = _project_score_identities(audit, source_to_canonical)
        evidence_state = self._load_evidence_state(
            audit=audit,
            writing_outline_path=writing_outline_path,
        )
        assessments = [
            self._assess_item(
                item=item,
                audit=assessment_audit,
                writing_outline=writing_outline,
                evidence_state=evidence_state,
            )
            for item in score_items
        ]
        summary = self._summary(assessments, total_max)
        audited = deepcopy((audit.get("source_lineage") or {}).get("audited_fulltext") or {})
        payload = {
            "schema_version": SCHEMA_VERSION,
            "assessment_id": "TSA-" + stable_json_sha256({
                "document_id": audit.get("document_id"),
                "audited_content_sha256": audited.get("content_sha256"),
                "score_item_ids": [item["score_item_id"] for item in assessments],
                "policy_version": POLICY_VERSION,
            })[:16].upper(),
            "document_id": audit["document_id"],
            "output_key": audit["output_key"],
            "status": "completed",
            "policy_version": POLICY_VERSION,
            "source_audit": {
                "path": str(audit_path),
                "sha256": audit_sha,
                "schema_version": audit["schema_version"],
            },
            "audited_fulltext": audited,
            "score_catalog": {
                "response_outline": {
                    "path": str(response_outline_path),
                    "sha256": sha256_file(response_outline_path),
                    "schema_version": response_outline.get("schema_version"),
                },
                "writing_outline": {
                    "path": str(writing_outline_path),
                    "sha256": sha256_file(writing_outline_path),
                    "schema_version": writing_outline.get("schema_version"),
                },
                "score_item_count": len(score_items),
                "total_max_score": _clean_number(total_max),
            },
            "evidence_state": evidence_state["public"],
            "score_items": assessments,
            "summary": summary,
            "generated_at": utc_now(),
        }
        self._validate_payload(payload)
        if sha256_file(audit_path) != audit_sha:
            raise TechnicalFulltextScoringAssessmentError(
                "Stage11.2 audit artifact changed during scoring assessment"
            )

        assessment_path = audit_path.with_name("technical_fulltext.scoring_assessment.json")
        markdown_path = audit_path.with_name("technical_fulltext.scoring_assessment.md")
        assessment_sha = atomic_write_json(
            assessment_path,
            payload,
            required_fields=("schema_version", "audited_fulltext", "score_items", "summary"),
        )
        markdown_sha = atomic_write_markdown(
            markdown_path,
            self._render_markdown(payload),
            required_text=MARKDOWN_NOTICE,
        )

        updated = deepcopy(audit)
        updated.setdefault("audit_passes", {})["scoring"] = {"status": "completed"}
        updated["scoring_assessment_ref"] = {
            "path": str(assessment_path.resolve()),
            "sha256": assessment_sha,
            "schema_version": SCHEMA_VERSION,
            "status": "completed",
        }
        updated["generated_at"] = utc_now()
        TechnicalFulltextAuditor()._validate_audit_payload(updated)
        atomic_write_json(
            audit_path,
            updated,
            required_fields=("schema_version", "audit_passes", "scoring_assessment_ref"),
        )
        return {
            "status": "completed",
            "assessment": payload,
            "summary": deepcopy(summary),
            "outputs": {
                "technical_fulltext_scoring_assessment_json": str(assessment_path.resolve()),
                "technical_fulltext_scoring_assessment_json_sha256": assessment_sha,
                "technical_fulltext_scoring_assessment_markdown": str(markdown_path.resolve()),
                "technical_fulltext_scoring_assessment_markdown_sha256": markdown_sha,
                "technical_fulltext_audit_json": str(audit_path),
                "technical_fulltext_audit_json_sha256": sha256_file(audit_path),
            },
        }

    @staticmethod
    def _validate_preconditions(audit: dict[str, Any]) -> None:
        passes = audit.get("audit_passes") or {}
        if audit.get("status") != "completed":
            raise TechnicalFulltextScoringAssessmentError("audit status must be completed")
        if passes.get("deterministic", {}).get("status") != "completed":
            raise TechnicalFulltextScoringAssessmentError(
                "Stage11.1 deterministic audit must be completed"
            )
        if passes.get("semantic", {}).get("status") != "completed":
            raise TechnicalFulltextScoringAssessmentError(
                "Stage11.2 semantic audit must be completed"
            )
        if (audit.get("semantic_audit") or {}).get("status") != "completed":
            raise TechnicalFulltextScoringAssessmentError(
                "semantic_audit status must be completed"
            )
        if audit.get("release_ready") is not False:
            raise TechnicalFulltextScoringAssessmentError(
                "Stage11.3 input must remain release_ready=false"
            )

    @staticmethod
    def _revalidate_audited_content(audit: dict[str, Any]) -> None:
        try:
            resolved = TechnicalFulltextAuditor.revalidate_audit_source(audit)
        except TechnicalFulltextAuditError as exc:
            raise TechnicalFulltextScoringAssessmentError(
                f"audited source snapshot is no longer trusted: {exc}"
            ) from exc
        recorded = (
            (audit.get("source_lineage") or {}).get("audited_fulltext") or {}
        ).get("content_sha256")
        actual = stable_json_sha256(resolved["fulltext"])
        if not recorded or recorded != actual:
            raise TechnicalFulltextScoringAssessmentError(
                "scoring source content identity mismatch"
            )

    @staticmethod
    def _load_context_artifact(
        audit: dict[str, Any], name: str
    ) -> tuple[Path, dict[str, Any]]:
        record = (audit.get("input_context") or {}).get(name) or {}
        path = Path(str(record.get("path") or "")).expanduser().resolve()
        try:
            payload = load_json_object(path, label=name.replace("_", " "))
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise TechnicalFulltextScoringAssessmentError(
                f"trusted {name} is unavailable: {exc}"
            ) from exc
        if sha256_file(path) != record.get("sha256") or record.get("sha256_matches_run") is not True:
            raise TechnicalFulltextScoringAssessmentError(
                f"trusted {name} SHA does not match Stage11 input context"
            )
        return path, payload

    @staticmethod
    def _validate_score_catalog(
        score_items: list[dict[str, Any]], writing_outline: dict[str, Any]
    ) -> None:
        if not score_items:
            raise TechnicalFulltextScoringAssessmentError("no technical score items were normalized")
        ids = [str(item.get("score_item_id") or "") for item in score_items]
        if len(ids) != len(set(ids)):
            raise TechnicalFulltextScoringAssessmentError("normalized SCORE identities are duplicated")
        by_id = {item["score_item_id"]: item for item in score_items}
        by_source_id = {
            str(item.get("_source_score_item_id") or item["score_item_id"]): item
            for item in score_items
        }
        for existing in writing_outline.get("score_items") or []:
            item_id = str(existing.get("score_item_id") or "")
            current = by_source_id.get(item_id) or by_id.get(item_id)
            if current is None:
                raise TechnicalFulltextScoringAssessmentError(
                    f"existing SCORE identity disappeared during normalization: {item_id}"
                )
            for field in ("title", "criteria_summary", "score", "response_point_id"):
                if current.get(field) != existing.get(field):
                    raise TechnicalFulltextScoringAssessmentError(
                        f"existing SCORE field changed during normalization: {item_id}/{field}"
                    )

    @staticmethod
    def _load_evidence_state(
        *, audit: dict[str, Any], writing_outline_path: Path
    ) -> dict[str, Any]:
        task_dir = writing_outline_path.parent / "tasks"
        query_path = task_dir / "local_evidence_query_plan.json"
        fact_request_path = task_dir / "project_fact_request.json"
        query_payload = load_json_object(query_path, label="local evidence query plan") if query_path.is_file() else {}
        fact_payload = load_json_object(fact_request_path, label="project fact request") if fact_request_path.is_file() else {}
        queries = list(query_payload.get("queries") or [])
        query_status_by_type: dict[str, list[str]] = {}
        for query in queries:
            query_status_by_type.setdefault(str(query.get("evidence_type") or ""), []).append(
                str(query.get("execution_status") or "")
            )

        project_root = writing_outline_path.parent.parent
        catalog_path = project_root / "evidence" / "enterprise_evidence_catalog.json"
        catalog = load_json_object(catalog_path, label="enterprise evidence catalog") if catalog_path.is_file() else {}
        evidence_items = list(catalog.get("evidence_items") or [])
        verified = [
            item for item in evidence_items
            if str(item.get("verification_status") or "") in VERIFIED_STATUSES
        ]
        candidates = [item for item in evidence_items if item not in verified]
        slots = list(fact_payload.get("slots") or [])
        public = {
            "query_plan": {
                "path": str(query_path.resolve()) if query_path.is_file() else None,
                "schema_version": query_payload.get("schema_version"),
                "execution_status": query_payload.get("execution_status") or "unavailable",
                "local_kb_queried": bool(query_payload.get("local_kb_queried")),
                "query_count": len(queries),
            },
            "candidate_evidence_count": len(candidates),
            "verified_evidence_count": len(verified),
            "confirmed_project_facts_available": bool(
                (audit.get("input_context") or {}).get("confirmed_project_facts")
            ),
            "fact_slot_count": len(slots),
            "missing_fact_slot_count": sum(item.get("status") == "missing" for item in slots),
        }
        return {
            "public": public,
            "query_status_by_type": query_status_by_type,
            "evidence_items": evidence_items,
            "verified": verified,
            "candidates": candidates,
        }

    def _assess_item(
        self,
        *,
        item: dict[str, Any],
        audit: dict[str, Any],
        writing_outline: dict[str, Any],
        evidence_state: dict[str, Any],
    ) -> dict[str, Any]:
        method = classify_assessment_method(item)
        max_score = _number(item.get("score"))
        locations = self._response_locations(item, audit, writing_outline)
        section_by_id = {
            section["section_id"]: section
            for section in (audit.get("normalized_document") or {}).get("sections") or []
        }
        response_locations: list[dict[str, Any]] = []
        response_excerpts: list[dict[str, Any]] = []
        requirement_ids: list[str] = []
        placeholder_ids: list[str] = []
        combined: list[str] = []
        for location in locations:
            section = section_by_id.get(str(location.get("section_id") or ""), {})
            content = str(section.get("content") or "")
            heading = str(section.get("heading") or "")
            response_locations.append({
                "chapter_id": str(location.get("chapter_id") or section.get("chapter_id") or ""),
                "section_id": str(location.get("section_id") or ""),
                "heading": heading,
            })
            if content:
                response_excerpts.append({
                    "chapter_id": str(location.get("chapter_id") or section.get("chapter_id") or ""),
                    "section_id": str(location.get("section_id") or ""),
                    "excerpt": re.sub(r"\s+", " ", content).strip()[:600],
                })
            requirement_ids.extend(section.get("covered_requirement_ids") or [])
            placeholder_ids.extend(section.get("placeholder_ids") or [])
            combined.extend([heading, content])
        requirement_ids = _unique(requirement_ids)
        placeholder_ids = _unique(placeholder_ids)
        assessment_requirement_ids = [
            value for value in requirement_ids
            if value != str(item.get("response_point_id") or "")
        ]
        relevant_issues = self._relevant_records(
            audit.get("issues") or [], item["score_item_id"], assessment_requirement_ids, "issue_id"
        )
        relevant_findings = self._relevant_records(
            [
                record for record in audit.get("semantic_findings") or []
                if record.get("verdict") in {"issue", "uncertain"}
            ],
            item["score_item_id"],
            assessment_requirement_ids,
            "finding_id",
        )
        finding_refs = _unique(
            [record.get("issue_id") for record in relevant_issues]
            + [record.get("finding_id") for record in relevant_findings]
        )
        evidence_requirements = _evidence_requirements(item)
        verified_refs, pending_evidence = self._evidence_for_item(
            evidence_requirements, evidence_state
        )

        base = {
            "score_item_id": item["score_item_id"],
            "criteria_title": item.get("title"),
            "criteria_text": item.get("criteria_summary"),
            "max_score": _clean_number(max_score),
            "source_refs": deepcopy(item.get("source_refs") or []),
            "assessment_method": method,
            "response_locations": response_locations,
            "response_excerpts": response_excerpts,
            "requirement_ids": requirement_ids,
            "finding_refs": finding_refs,
            "evidence_requirements": evidence_requirements,
            "verified_evidence_refs": verified_refs,
            "pending_evidence": pending_evidence,
        }
        if method == "semantic":
            result = self._semantic_assessment(
                item=item,
                max_score=max_score,
                combined_text="\n".join(combined),
                relevant_issues=relevant_issues,
                relevant_findings=relevant_findings,
                has_locations=bool(locations),
                pending_fact_ids=placeholder_ids,
            )
        elif method == "evidence_dependent":
            result = self._evidence_assessment(
                item=item,
                max_score=max_score,
                verified_refs=verified_refs,
                pending_evidence=pending_evidence,
                has_locations=bool(locations),
            )
        elif method == "deterministic":
            result = self._deterministic_assessment(
                item=item,
                max_score=max_score,
                relevant_issues=relevant_issues,
                relevant_findings=relevant_findings,
                has_locations=bool(locations),
            )
        else:
            result = self._mixed_assessment(
                item=item,
                max_score=max_score,
                relevant_issues=relevant_issues,
                relevant_findings=relevant_findings,
                pending_evidence=pending_evidence,
                has_locations=bool(locations),
                pending_fact_ids=placeholder_ids,
            )
        merged = {**base, **result}
        low = float(merged["score_range"]["min"])
        high = float(merged["score_range"]["max"])
        merged["potential_gain_range"] = {
            "min": _clean_number(max(0.0, max_score - high)),
            "max": _clean_number(max(0.0, max_score - low)),
        }
        if merged["assessment_status"] == "confirmed":
            merged["potential_gain"] = _clean_number(
                max(0.0, max_score - float(merged["confirmed_score"] or 0))
            )
        elif merged["assessment_status"] == "estimated":
            merged["potential_gain"] = _clean_number(
                max(0.0, max_score - float(merged["estimated_score"] or 0))
            )
        else:
            merged["potential_gain"] = None
        return merged

    @staticmethod
    def _response_locations(
        item: dict[str, Any], audit: dict[str, Any], writing_outline: dict[str, Any]
    ) -> list[dict[str, Any]]:
        coverage = {
            entry.get("score_item_id"): entry
            for entry in (audit.get("coverage_analysis") or {}).get("score_items") or []
        }
        direct = coverage.get(item.get("score_item_id")) or {}
        locations = list(direct.get("actual_locations") or direct.get("locations") or [])
        if locations:
            return locations
        sibling_ids = [
            existing.get("score_item_id")
            for existing in writing_outline.get("score_items") or []
            if existing.get("title") == item.get("title")
        ]
        inherited: list[dict[str, Any]] = []
        for sibling_id in sibling_ids:
            entry = coverage.get(sibling_id) or {}
            inherited.extend(entry.get("actual_locations") or entry.get("locations") or [])
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for location in inherited:
            key = (str(location.get("chapter_id") or ""), str(location.get("section_id") or ""))
            unique[key] = location
        return list(unique.values())

    @staticmethod
    def _relevant_records(
        records: list[dict[str, Any]],
        score_item_id: str,
        requirement_ids: list[str],
        identity_field: str,
    ) -> list[dict[str, Any]]:
        requirement_set = set(requirement_ids)
        selected = []
        for record in records:
            if score_item_id in set(record.get("score_item_ids") or []):
                selected.append(record)
            elif requirement_set & set(record.get("requirement_ids") or []):
                selected.append(record)
        return list({str(item.get(identity_field) or index): item for index, item in enumerate(selected)}.values())

    @staticmethod
    def _evidence_for_item(
        requirements: list[str], evidence_state: dict[str, Any]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        verified_refs: list[str] = []
        pending: list[dict[str, Any]] = []
        for requirement in requirements:
            verified = [
                item for item in evidence_state["verified"]
                if str(item.get("evidence_type") or item.get("material_type") or "") == requirement
            ]
            verified_refs.extend(
                str(item.get("evidence_id") or item.get("material_id") or "") for item in verified
            )
            if verified:
                continue
            candidates = [
                item for item in evidence_state["candidates"]
                if str(item.get("evidence_type") or item.get("material_type") or "") == requirement
            ]
            statuses = evidence_state["query_status_by_type"].get(requirement, [])
            if candidates:
                status = "candidate_found"
            elif statuses and all(value == "planned_not_executed" for value in statuses):
                status = "query_not_executed"
            elif "confirmed_missing" in statuses:
                status = "confirmed_missing"
            else:
                status = "query_not_executed"
            pending.append({
                "evidence_type": requirement,
                "status": status,
                "reason": (
                    "evidence query has not been executed; absence is not established"
                    if status == "query_not_executed"
                    else "candidate evidence requires verification"
                    if status == "candidate_found"
                    else "executed evidence process confirmed the material is missing"
                ),
            })
        return _unique(verified_refs), pending

    @staticmethod
    def _semantic_assessment(
        *, item: dict[str, Any], max_score: float, combined_text: str,
        relevant_issues: list[dict[str, Any]], relevant_findings: list[dict[str, Any]],
        has_locations: bool, pending_fact_ids: list[str],
    ) -> dict[str, Any]:
        expectations = _semantic_expectations(item)
        satisfied = [label for label, terms in expectations if any(term in combined_text for term in terms)]
        missing = [label for label, terms in expectations if not any(term in combined_text for term in terms)]
        issue_reasons = _unique(record.get("summary") for record in relevant_issues)
        uncertain = _unique(
            record.get("summary") for record in relevant_findings
            if record.get("verdict") == "uncertain"
        )
        pending_facts = [f"fact_not_confirmed:{value}" for value in pending_fact_ids]
        defects = len(missing) + len(issue_reasons) + (0 if has_locations else 1)
        bands = _score_bands(str(item.get("criteria_summary") or ""), max_score)
        selected_index = min(defects, max(0, len(bands) - 1))
        selected = bands[selected_index]
        if selected_index == 0 and len(bands) > 1:
            low, high = bands[1], bands[0]
        elif selected_index + 1 < len(bands):
            low, high = bands[selected_index + 1], selected
        else:
            low = high = selected
        deduction_reasons = _unique(
            [f"未观察到：{value}" for value in missing] + issue_reasons
        )
        return {
            "assessment_status": "estimated",
            "confirmed_score": None,
            "estimated_score": _clean_number(selected),
            "score_range": {"min": _clean_number(low), "max": _clean_number(high)},
            "satisfied_conditions": satisfied,
            "missing_conditions": missing,
            "pending_conditions": _unique(uncertain + pending_facts),
            "deduction_reasons": deduction_reasons,
            "machine_rationale": (
                "The subjective band is estimated from audited response coverage, visible rubric components, "
                "and linked Stage11 findings; evaluator discretion prevents confirmation."
            ),
            "confidence": "medium" if has_locations and not uncertain and not pending_facts else "low",
        }

    @staticmethod
    def _evidence_assessment(
        *, item: dict[str, Any], max_score: float, verified_refs: list[str],
        pending_evidence: list[dict[str, Any]], has_locations: bool,
    ) -> dict[str, Any]:
        if pending_evidence:
            return {
                "assessment_status": "pending",
                "confirmed_score": None,
                "estimated_score": None,
                "score_range": {"min": 0, "max": _clean_number(max_score)},
                "satisfied_conditions": ["response location identified"] if has_locations else [],
                "missing_conditions": [],
                "pending_conditions": [
                    f"{item['evidence_type']}:{item['status']}" for item in pending_evidence
                ],
                "deduction_reasons": [],
                "machine_rationale": (
                    "The score depends on enterprise or product evidence. Unexecuted queries do not prove "
                    "absence, so no score is confirmed or estimated."
                ),
                "confidence": "high",
            }
        score = TechnicalFulltextScoringAssessor._verified_formula_score(
            str(item.get("criteria_summary") or ""), max_score, len(verified_refs)
        )
        return {
            "assessment_status": "confirmed",
            "confirmed_score": _clean_number(score),
            "estimated_score": None,
            "score_range": {"min": _clean_number(score), "max": _clean_number(score)},
            "satisfied_conditions": [f"verified evidence count={len(verified_refs)}"],
            "missing_conditions": [],
            "pending_conditions": [],
            "deduction_reasons": [] if score == max_score else ["verified evidence does not reach the rubric maximum"],
            "machine_rationale": "Verified evidence was evaluated with the explicit count/value rule.",
            "confidence": "high",
        }

    @staticmethod
    def _verified_formula_score(criteria: str, max_score: float, count: int) -> float:
        per_item = re.search(r"(?:每(?:提供)?一份|每份|1个业绩|每项)[^\d]{0,8}(\d+(?:\.\d+)?)分", criteria)
        if per_item:
            return min(max_score, count * float(per_item.group(1)))
        return max_score if count else 0.0

    @staticmethod
    def _deterministic_assessment(
        *, item: dict[str, Any], max_score: float, relevant_issues: list[dict[str, Any]],
        relevant_findings: list[dict[str, Any]], has_locations: bool,
    ) -> dict[str, Any]:
        uncertain = [record for record in relevant_findings if record.get("verdict") == "uncertain"]
        if uncertain or not has_locations:
            return {
                "assessment_status": "pending",
                "confirmed_score": None,
                "estimated_score": None,
                "score_range": {"min": 0, "max": _clean_number(max_score)},
                "satisfied_conditions": [],
                "missing_conditions": [],
                "pending_conditions": _unique(record.get("summary") for record in uncertain) or ["response mapping unavailable"],
                "deduction_reasons": [],
                "machine_rationale": "The rule is deterministic, but one or more required inputs are unresolved.",
                "confidence": "high",
            }
        deduction = 1.0 if "每条扣1分" in str(item.get("criteria_summary") or "") else 0.0
        score = max(0.0, max_score - deduction * len(relevant_issues))
        return {
            "assessment_status": "confirmed",
            "confirmed_score": _clean_number(score),
            "estimated_score": None,
            "score_range": {"min": _clean_number(score), "max": _clean_number(score)},
            "satisfied_conditions": ["all deterministic inputs are available"],
            "missing_conditions": [],
            "pending_conditions": [],
            "deduction_reasons": _unique(record.get("summary") for record in relevant_issues),
            "machine_rationale": "The explicit arithmetic rule was applied to confirmed machine findings.",
            "confidence": "high",
        }

    @staticmethod
    def _mixed_assessment(
        *, item: dict[str, Any], max_score: float, relevant_issues: list[dict[str, Any]],
        relevant_findings: list[dict[str, Any]], pending_evidence: list[dict[str, Any]],
        has_locations: bool, pending_fact_ids: list[str],
    ) -> dict[str, Any]:
        issue_reasons = _unique(record.get("summary") for record in relevant_issues)
        uncertain = _unique(
            record.get("summary") for record in relevant_findings
            if record.get("verdict") == "uncertain"
        )
        pending = uncertain + [
            f"{record['evidence_type']}:{record['status']}" for record in pending_evidence
        ]
        pending.extend(f"fact_not_confirmed:{value}" for value in pending_fact_ids)
        if not has_locations:
            pending.append("response mapping unavailable")
        deduction = len(issue_reasons) if "每条扣1分" in str(item.get("criteria_summary") or "") else 0
        upper = max(0.0, max_score - deduction)
        lower = max(0.0, upper - len(pending)) if pending else upper
        status = "pending" if pending else "estimated"
        return {
            "assessment_status": status,
            "confirmed_score": None,
            "estimated_score": _clean_number(upper) if status == "estimated" else None,
            "score_range": {"min": _clean_number(lower), "max": _clean_number(upper)},
            "satisfied_conditions": ["response location identified"] if has_locations else [],
            "missing_conditions": [],
            "pending_conditions": _unique(pending),
            "deduction_reasons": issue_reasons,
            "machine_rationale": (
                "Observable response quality and machine findings were evaluated separately from unresolved "
                "facts or evidence; unresolved components remain in the score range."
            ),
            "confidence": "low" if pending else "medium",
        }

    @staticmethod
    def _summary(items: list[dict[str, Any]], total_max: float) -> dict[str, Any]:
        confirmed = [item for item in items if item["assessment_status"] == "confirmed"]
        estimated = [item for item in items if item["assessment_status"] == "estimated"]
        pending = [item for item in items if item["assessment_status"] == "pending"]
        confirmed_subtotal = sum(float(item["confirmed_score"] or 0) for item in confirmed)
        estimated_low = confirmed_subtotal + sum(float(item["score_range"]["min"]) for item in estimated)
        estimated_high = confirmed_subtotal + sum(float(item["score_range"]["max"]) for item in estimated)
        overall_low = confirmed_subtotal + sum(float(item["score_range"]["min"]) for item in estimated + pending)
        overall_high = confirmed_subtotal + sum(float(item["score_range"]["max"]) for item in estimated + pending)
        potential_low = sum(float(item["potential_gain_range"]["min"]) for item in items)
        potential_high = sum(float(item["potential_gain_range"]["max"]) for item in items)
        return {
            "technical_score_max": _clean_number(total_max),
            "confirmed_score_subtotal": _clean_number(confirmed_subtotal),
            "estimated_score_range": {
                "min": _clean_number(estimated_low),
                "max": _clean_number(estimated_high),
            },
            "overall_score_range": {
                "min": _clean_number(overall_low),
                "max": _clean_number(overall_high),
            },
            "pending_score_max": _clean_number(sum(float(item["max_score"]) for item in pending)),
            "potential_gain_range": {
                "min": _clean_number(potential_low),
                "max": _clean_number(potential_high),
            },
            "confirmed_item_count": len(confirmed),
            "estimated_item_count": len(estimated),
            "pending_item_count": len(pending),
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        try:
            schema = load_json_object(self.schema_path, label="scoring assessment schema")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextScoringAssessmentError(
                f"scoring assessment schema is unavailable: {exc}"
            ) from exc
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda item: list(item.path),
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors[:5]
            )
            raise TechnicalFulltextScoringAssessmentError(
                f"scoring assessment schema validation failed: {detail}"
            )

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        summary = payload["summary"]
        lines = [
            "# 全文逐评分细则机器评估",
            "",
            f"> {MARKDOWN_NOTICE}",
            "> 主观评分均为 estimated；未核验材料对应分值保持 pending。",
            "",
            "## 总览",
            "",
            f"- 技术评分满分：{summary['technical_score_max']}",
            f"- 已确认得分：{summary['confirmed_score_subtotal']}",
            f"- 机器估计区间：{summary['estimated_score_range']['min']}-{summary['estimated_score_range']['max']}",
            f"- 总评分区间：{summary['overall_score_range']['min']}-{summary['overall_score_range']['max']}",
            f"- 待确认分值：{summary['pending_score_max']}",
            f"- 潜在提升区间：{summary['potential_gain_range']['min']}-{summary['potential_gain_range']['max']}",
            "",
            "## 逐项评估",
            "",
            "| SCORE | 评分项 | 方法 | 状态 | 满分 | confirmed | estimated | 区间 | potential |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
        for item in payload["score_items"]:
            lines.append(
                f"| {item['score_item_id']} | {item['criteria_title']} | {item['assessment_method']} | "
                f"{item['assessment_status']} | {item['max_score']} | {item['confirmed_score']} | "
                f"{item['estimated_score']} | {item['score_range']['min']}-{item['score_range']['max']} | "
                f"{item['potential_gain_range']['min']}-{item['potential_gain_range']['max']} |"
            )
        for item in payload["score_items"]:
            lines.extend([
                "",
                f"### {item['score_item_id']} · {item['criteria_title']}",
                "",
                f"- 规则：{item['criteria_text']}",
                f"- 状态：{item['assessment_status']} / {item['assessment_method']}",
                f"- 响应位置：{', '.join(value['section_id'] for value in item['response_locations']) or '-'}",
                f"- 已满足：{'；'.join(item['satisfied_conditions']) or '-'}",
                f"- 缺失：{'；'.join(item['missing_conditions']) or '-'}",
                f"- 待确认：{'；'.join(item['pending_conditions']) or '-'}",
                f"- 扣分原因：{'；'.join(item['deduction_reasons']) or '-'}",
                f"- 机器依据：{item['machine_rationale']}",
            ])
        lines.append("")
        return "\n".join(lines)


def inspect_technical_fulltext_scoring_assessment(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    payload = load_json_object(target, label="technical fulltext scoring assessment")
    TechnicalFulltextScoringAssessor()._validate_payload(payload)
    return {
        "schema_version": payload.get("schema_version"),
        "assessment_id": payload.get("assessment_id"),
        "document_id": payload.get("document_id"),
        "output_key": payload.get("output_key"),
        "status": payload.get("status"),
        "audited_fulltext": deepcopy(payload.get("audited_fulltext") or {}),
        "score_catalog": deepcopy(payload.get("score_catalog") or {}),
        "summary": deepcopy(payload.get("summary") or {}),
        "assessment_status_counts": {
            status: sum(item.get("assessment_status") == status for item in payload.get("score_items") or [])
            for status in ("confirmed", "estimated", "pending")
        },
    }

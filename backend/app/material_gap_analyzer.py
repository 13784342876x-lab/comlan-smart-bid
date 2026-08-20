from __future__ import annotations

import re
from typing import Any

from app.task_material_matcher import EVIDENCE_ALIASES


REQUIRED_QUOTE_EVIDENCE = ["开标一览表", "报价明细表", "报价一致性核对表"]


def analyze_material_gaps(project: dict[str, Any], match_result: dict[str, Any]) -> dict[str, Any]:
    """Analyze evidence coverage for each generation task."""

    tasks = list(project.get("generation_tasks") or [])
    matches_by_task_id = {
        str(item.get("task_id") or ""): item for item in list(match_result.get("results") or [])
    }

    items: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        match_item = matches_by_task_id.get(task_id, {})
        matched_materials = list(match_item.get("matched_materials") or [])
        usable_materials, supporting_materials = split_materials_by_usability(matched_materials)
        evidence_needed = list(task.get("evidence_needed") or [])

        if not evidence_needed:
            covered_evidence: list[str] = []
            missing_evidence: list[str] = []
            readiness = "not_specified"
            risk_level = "low"
        else:
            covered_evidence, missing_evidence = split_evidence_coverage(evidence_needed, usable_materials)
            readiness_basis = [str(item) for item in evidence_needed]
            if requires_quote_materials(task):
                covered_evidence, missing_evidence = enforce_quote_evidence(
                    covered_evidence,
                    missing_evidence,
                    usable_materials,
                )
                readiness_basis = dedupe_texts(readiness_basis + REQUIRED_QUOTE_EVIDENCE)
            readiness = determine_readiness(readiness_basis, covered_evidence)
            risk_level = determine_risk_level(readiness, task)

        items.append(
            {
                "task_id": task_id,
                "task_type": str(task.get("task_type") or ""),
                "target_section": str(task.get("target_section") or ""),
                "evidence_needed": evidence_needed,
                "covered_evidence": covered_evidence,
                "missing_evidence": missing_evidence,
                "matched_materials": matched_materials,
                "usable_materials": usable_materials,
                "supporting_materials": supporting_materials,
                "readiness": readiness,
                "risk_level": risk_level,
                "suggestion": build_suggestion(readiness, missing_evidence),
            }
        )

    return {
        "status": "success",
        "total_tasks": len(tasks),
        "items": items,
        "summary": build_summary(items),
    }


def split_evidence_coverage(
    evidence_needed: list[Any],
    matched_materials: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    covered: list[str] = []
    missing: list[str] = []

    for evidence in evidence_needed:
        text = str(evidence or "").strip()
        if not text:
            continue
        if evidence_is_covered_by_matches(text, matched_materials):
            covered.append(text)
        else:
            missing.append(text)

    return covered, missing


def split_materials_by_usability(
    matched_materials: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    usable: list[dict[str, Any]] = []
    supporting: list[dict[str, Any]] = []
    for material in matched_materials:
        if str(material.get("usability") or "").lower() == "usable":
            usable.append(material)
        else:
            supporting.append(material)
    return usable, supporting


def evidence_is_covered_by_matches(evidence: str, matched_materials: list[dict[str, Any]]) -> bool:
    for material in usable_coverage_materials(matched_materials):
        matched_evidence = [str(item).strip() for item in list(material.get("matched_evidence") or [])]
        if evidence in matched_evidence:
            return True
        if any(evidence_terms_overlap(evidence, matched) for matched in matched_evidence):
            return True
        if evidence_is_covered(evidence, build_material_haystack([material])):
            return True
    return False


def usable_coverage_materials(matched_materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable: list[dict[str, Any]] = []
    for material in matched_materials:
        if str(material.get("usability") or "").lower() == "usable":
            usable.append(material)
    return usable


def build_material_haystack(matched_materials: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for material in matched_materials:
        for key in ("original_name", "category", "tags", "matched_text"):
            value = material.get(key)
            if value:
                parts.append(str(value))
    return "\n".join(parts)


def evidence_is_covered(evidence: str, haystack: str) -> bool:
    normalized_evidence = normalize_for_match(evidence)
    normalized_haystack = normalize_for_match(haystack)
    if not normalized_evidence or not normalized_haystack:
        return False

    if normalized_evidence in normalized_haystack:
        return True

    terms = evidence_terms(evidence)
    for term in terms:
        normalized_term = normalize_for_match(term)
        if normalized_term and normalized_term in normalized_haystack:
            return True

    tokens = significant_tokens(evidence)
    if not tokens:
        return False

    matched_tokens = [token for token in tokens if normalize_for_match(token) in normalized_haystack]
    required = 1 if len(tokens) <= 2 else max(2, len(tokens) // 2)
    return len(matched_tokens) >= required


def significant_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text)
    return [token for token in tokens if len(normalize_for_match(token)) >= 2]


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+|[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def evidence_terms(evidence: str) -> list[str]:
    terms = [evidence]
    terms.extend(EVIDENCE_ALIASES.get(evidence, []))
    for key, aliases in EVIDENCE_ALIASES.items():
        if key in evidence or evidence in key:
            terms.extend(aliases)
    return dedupe_texts(terms)


def evidence_terms_overlap(left: str, right: str) -> bool:
    left_terms = {normalize_for_match(term) for term in evidence_terms(left)}
    right_terms = {normalize_for_match(term) for term in evidence_terms(right)}
    left_terms.discard("")
    right_terms.discard("")
    return bool(left_terms & right_terms)


def enforce_quote_evidence(
    covered_evidence: list[str],
    missing_evidence: list[str],
    matched_materials: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    covered = list(covered_evidence)
    missing = [item for item in missing_evidence if item not in REQUIRED_QUOTE_EVIDENCE]

    for evidence in REQUIRED_QUOTE_EVIDENCE:
        if evidence in covered:
            continue
        if evidence_is_covered_by_matches(evidence, matched_materials):
            covered.append(evidence)
        else:
            missing.append(evidence)

    return dedupe_texts(covered), dedupe_texts(missing)


def requires_quote_materials(task: dict[str, Any]) -> bool:
    task_type = str(task.get("task_type") or "").lower()
    text = "\n".join(
        [
            str(task.get("target_section") or ""),
            str(task.get("generation_instruction") or ""),
            "\n".join(str(item) for item in list(task.get("evidence_needed") or [])),
            "\n".join(str(item) for item in list(task.get("related_requirements") or [])),
        ]
    )
    if task_type == "price":
        return True
    if task_type == "manual" and contains_any(text, ["报价", "价格", "开标", "投标总价"]):
        return True
    return contains_any(str(task.get("target_section") or ""), ["报价文件", "价格标"])


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def dedupe_texts(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        normalized = normalize_for_match(text)
        if normalized and normalized not in seen:
            cleaned.append(text)
            seen.add(normalized)
    return cleaned


def determine_readiness(
    evidence_needed: list[Any],
    covered_evidence: list[str],
) -> str:
    required = {
        normalize_for_match(item)
        for item in evidence_needed
        if str(item or "").strip()
    }
    covered = {
        normalize_for_match(item)
        for item in covered_evidence
        if str(item or "").strip()
    }
    if not required:
        return "not_specified"
    if required.issubset(covered):
        return "ready"
    if covered:
        return "partial"
    return "missing"


def determine_risk_level(readiness: str, task: dict[str, Any]) -> str:
    task_risk = str(task.get("risk_level") or "").lower()
    if readiness == "ready":
        return "low"
    if readiness == "partial":
        return "high" if task_risk == "high" else "medium"
    if readiness == "missing":
        return "high"
    return "low"


def build_suggestion(readiness: str, missing_evidence: list[str]) -> str:
    if readiness == "ready":
        return "Evidence appears covered by matched materials."
    if readiness == "partial":
        return "Add or verify missing evidence: " + ", ".join(missing_evidence)
    if readiness == "missing":
        return "No matched material covers the required evidence; collect materials before writing."
    return "No explicit evidence requirement was found; review manually before writing."


def build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    readiness_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for item in items:
        readiness = str(item.get("readiness") or "unknown")
        risk = str(item.get("risk_level") or "unknown")
        readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1
        risk_counts[risk] = risk_counts.get(risk, 0) + 1

    return {
        "readiness_counts": readiness_counts,
        "risk_counts": risk_counts,
        "ready_count": readiness_counts.get("ready", 0),
        "needs_materials_count": readiness_counts.get("partial", 0) + readiness_counts.get("missing", 0),
        "not_specified_count": readiness_counts.get("not_specified", 0),
    }

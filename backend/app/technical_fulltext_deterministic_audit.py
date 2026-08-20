from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.technical_fulltext_composer import stable_json_sha256


EXACT_DUPLICATE_MIN_LENGTH = 80
NEAR_DUPLICATE_MIN_LENGTH = 80
NEAR_DUPLICATE_THRESHOLD = 0.88
NEAR_DUPLICATE_NGRAM_THRESHOLD = 0.62
SCORE_DISTRIBUTION_CHAPTER_THRESHOLD = 3
ALIAS_ENTITY_SCOPE_TOKENS = {
    "HYPERCONVERGED",
    "SWITCH",
    "NODE",
    "SERVER",
    "STORAGE",
    "NETWORK",
}


@dataclass(frozen=True)
class DeterministicAuditResult:
    issues: list[dict[str, Any]]
    confirmation_register: list[dict[str, Any]]
    semantic_candidates: dict[str, list[dict[str, Any]]]
    coverage_analysis: dict[str, Any]
    deterministic_checks: dict[str, dict[str, Any]]


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _excerpt(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _paragraph_location(paragraph: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_id": paragraph["chapter_id"],
        "section_id": paragraph["section_id"],
        "paragraph_id": paragraph["paragraph_id"],
        "paragraph_index": paragraph["paragraph_index"],
        "excerpt": _excerpt(paragraph["text"]),
        "text_sha256": paragraph["text_sha256"],
    }


def _location_anchor(location: dict[str, Any]) -> str:
    return "|".join([
        str(location.get("chapter_id") or ""),
        str(location.get("section_id") or ""),
        str(location.get("paragraph_id") or ""),
        str(location.get("text_sha256") or ""),
    ])


def _stable_id(prefix: str, identity: dict[str, Any]) -> str:
    return prefix + stable_json_sha256(identity)[:16].upper()


def _exact_duplicate_issues(
    paragraphs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for paragraph in paragraphs:
        text = str(paragraph.get("normalized_text") or "")
        if len(text) < EXACT_DUPLICATE_MIN_LENGTH:
            continue
        groups.setdefault(str(paragraph.get("text_sha256") or ""), []).append(paragraph)

    issues: list[dict[str, Any]] = []
    duplicate_group_count = 0
    for text_sha, group in groups.items():
        section_ids = {str(item.get("section_id") or "") for item in group}
        chapter_ids = {str(item.get("chapter_id") or "") for item in group}
        if len(section_ids) < 2 or len(chapter_ids) < 2:
            continue
        duplicate_group_count += 1
        locations = sorted(
            (_paragraph_location(item) for item in group), key=_location_anchor
        )
        identity = {
            "audit_type": "cross_content_duplication",
            "code": "exact_cross_chapter_duplicate",
            "canonical_subject": text_sha,
            "location_anchors": sorted(_location_anchor(item) for item in locations),
        }
        issues.append({
            "issue_id": _stable_id("FAI-", identity),
            "audit_type": "cross_content_duplication",
            "code": "exact_cross_chapter_duplicate",
            "severity": "warning",
            "release_blocking": False,
            "subject": text_sha,
            "chapter_ids": sorted(chapter_ids),
            "section_ids": sorted(section_ids),
            "locations": locations,
            "summary": "不同章节存在完全相同的长段正文。",
            "rationale": "归一化后正文及其 SHA256 完全一致，且位置跨越不同章节。",
            "details": {
                "duplicate_text_sha256": text_sha,
                "normalized_length": len(str(group[0].get("normalized_text") or "")),
                "occurrence_count": len(group),
            },
            "requirement_ids": [],
            "score_item_ids": [],
            "detection": {
                "method": "normalized_text_exact_sha256",
                "minimum_length": EXACT_DUPLICATE_MIN_LENGTH,
            },
            "recommended_action": {
                "resolution_kind": "review_duplicate",
                "text": "人工确认各处内容是否均需保留；仅保留必要的主响应，并将其他位置改为引用或简述。",
            },
            "requires_human_review": True,
        })
    return sorted(issues, key=lambda item: item["issue_id"]), duplicate_group_count


def _ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _near_duplicate_candidates(
    paragraphs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eligible = [
        item
        for item in paragraphs
        if len(str(item.get("normalized_text") or "")) >= NEAR_DUPLICATE_MIN_LENGTH
    ]
    ngrams = {item["paragraph_id"]: _ngrams(item["normalized_text"]) for item in eligible}
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(eligible):
        for right in eligible[index + 1 :]:
            if left["section_id"] == right["section_id"]:
                continue
            if left["text_sha256"] == right["text_sha256"]:
                continue
            left_text = left["normalized_text"]
            right_text = right["normalized_text"]
            length_ratio = min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
            if length_ratio < 0.72:
                continue
            left_grams = ngrams[left["paragraph_id"]]
            right_grams = ngrams[right["paragraph_id"]]
            union = left_grams | right_grams
            overlap = len(left_grams & right_grams) / len(union) if union else 0.0
            if overlap < NEAR_DUPLICATE_NGRAM_THRESHOLD:
                continue
            similarity = SequenceMatcher(
                None, left_text, right_text, autojunk=False
            ).ratio()
            if similarity < NEAR_DUPLICATE_THRESHOLD:
                continue
            location_a = _paragraph_location(left)
            location_b = _paragraph_location(right)
            anchors = sorted([_location_anchor(location_a), _location_anchor(location_b)])
            candidates.append({
                "candidate_id": _stable_id("SAC-DUP-", {
                    "candidate_type": "near_duplicate",
                    "location_anchors": anchors,
                }),
                "candidate_type": "near_duplicate",
                "similarity": round(similarity, 6),
                "ngram_overlap": round(overlap, 6),
                "location_a": location_a,
                "location_b": location_b,
                "text_a": left["text"],
                "text_b": right["text"],
            })
    return sorted(candidates, key=lambda item: item["candidate_id"])


def _enrich_occurrence_location(
    location: dict[str, Any], paragraph_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    paragraph_id = location.get("paragraph_id")
    paragraph = paragraph_by_id.get(str(paragraph_id or ""))
    if paragraph is not None:
        return _paragraph_location(paragraph)
    return {
        "chapter_id": location.get("chapter_id"),
        "section_id": location.get("section_id"),
        "paragraph_id": None,
        "paragraph_index": None,
        "excerpt": None,
        "text_sha256": None,
    }


def _confirmation_register(
    placeholders: list[dict[str, Any]], paragraphs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    paragraph_by_id = {item["paragraph_id"]: item for item in paragraphs}
    result: list[dict[str, Any]] = []
    for placeholder in placeholders:
        placeholder_id = placeholder["placeholder_id"]
        member_ids = [placeholder_id]
        confirmation_id = _stable_id("FCA-", {
            "member_placeholder_ids": sorted(member_ids),
            "canonicalization": "exact_placeholder_id",
        })
        locations = [
            _enrich_occurrence_location(item, paragraph_by_id)
            for item in placeholder.get("locations") or []
        ]
        result.append({
            "confirmation_id": confirmation_id,
            "status": "pending",
            "member_placeholder_ids": member_ids,
            "affected_locations": locations,
            "occurrence_count": len(locations),
            "canonicalization": "exact_placeholder_id",
            "requires_human_review": True,
        })
    return sorted(result, key=lambda item: item["confirmation_id"])


def _placeholder_tokens(placeholder_id: str) -> tuple[str, ...]:
    value = placeholder_id.upper()
    if value.startswith("PH-FIELD-"):
        value = value[len("PH-FIELD-") :]
    elif value.startswith("PH-"):
        value = value[len("PH-") :]
    return tuple(token for token in value.split("-") if token)


def _manual_fact_types(draft_units: dict[str, Any] | None) -> list[str]:
    if not isinstance(draft_units, dict):
        return []
    values: list[Any] = []
    for unit in draft_units.get("draft_units") or []:
        if isinstance(unit, dict):
            values.extend(unit.get("shared_manual_fact_types") or [])
    return _unique_strings(values)


def _candidate_fact_type(
    shared_tokens: set[str], fact_types: list[str]
) -> str | None:
    matches: list[str] = []
    for fact_type in fact_types:
        fact_tokens = {token.upper() for token in fact_type.split("_") if token}
        if fact_tokens and fact_tokens.issubset(shared_tokens):
            matches.append(fact_type)
    return sorted(matches, key=lambda item: (-len(item), item))[0] if matches else None


def _placeholder_alias_candidates(
    placeholders: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    *,
    draft_units: dict[str, Any] | None,
    draft_units_trusted: bool,
) -> list[dict[str, Any]]:
    paragraph_by_id = {item["paragraph_id"]: item for item in paragraphs}
    fact_types = _manual_fact_types(draft_units) if draft_units_trusted else []
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(placeholders):
        for right in placeholders[index + 1 :]:
            left_id = left["placeholder_id"]
            right_id = right["placeholder_id"]
            left_tokens = set(_placeholder_tokens(left_id))
            right_tokens = set(_placeholder_tokens(right_id))
            if not left_tokens or not right_tokens or left_tokens == right_tokens:
                continue
            if not (left_tokens < right_tokens or right_tokens < left_tokens):
                continue
            shared = left_tokens & right_tokens
            extras = sorted((left_tokens | right_tokens) - shared)
            if not extras or not set(extras).issubset(ALIAS_ENTITY_SCOPE_TOKENS):
                continue
            locations = [
                _enrich_occurrence_location(item, paragraph_by_id)
                for item in (left.get("locations") or []) + (right.get("locations") or [])
            ]
            ids = sorted([left_id, right_id])
            reason_codes = ["placeholder_token_strict_subset"]
            fact_type = _candidate_fact_type(shared, fact_types)
            if fact_type:
                reason_codes.append("trusted_draft_unit_fact_type_match")
            candidates.append({
                "candidate_id": _stable_id("SAC-ALIAS-", {
                    "candidate_type": "placeholder_alias",
                    "candidate_placeholder_ids": ids,
                }),
                "candidate_type": "placeholder_alias",
                "candidate_placeholder_ids": ids,
                "candidate_canonical_fact_type": fact_type,
                "entity_scope": "-".join(extras).lower() if extras else None,
                "reason_codes": reason_codes,
                "locations": locations,
            })
    return sorted(candidates, key=lambda item: item["candidate_id"])


def _simple_location(location: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_id": location.get("chapter_id"),
        "section_id": location.get("section_id"),
    }


def _title_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _planning_location(
    value: Any,
    *,
    chapter_by_title: dict[str, str],
    section_by_titles: dict[tuple[str, str], str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    target_path = [_title_key(item) for item in value.get("target_path") or [] if _title_key(item)]
    chapter_id: str | None = None
    section_id: str | None = None
    if target_path:
        chapter_id = chapter_by_title.get(target_path[0])
        if chapter_id and len(target_path) >= 2:
            section_id = section_by_titles.get((chapter_id, target_path[-1]))
    resolved = bool(chapter_id and (len(target_path) == 1 or section_id))
    return {
        "task_id": value.get("task_id"),
        "target_path": target_path,
        "mapping_role": value.get("mapping_role"),
        "chapter_id": chapter_id,
        "section_id": section_id,
        "resolved": resolved,
    }


def _planned_locations(
    values: Iterable[Any],
    *,
    chapter_by_title: dict[str, str],
    section_by_titles: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        location = _planning_location(
            value,
            chapter_by_title=chapter_by_title,
            section_by_titles=section_by_titles,
        )
        if location is None:
            continue
        identity = stable_json_sha256(location)
        if identity not in seen:
            seen.add(identity)
            result.append(location)
    return result


def _actual_is_planned(
    actual: dict[str, Any], planned: list[dict[str, Any]]
) -> bool:
    for location in planned:
        if not location.get("resolved"):
            continue
        if actual.get("chapter_id") != location.get("chapter_id"):
            continue
        planned_section = location.get("section_id")
        if planned_section is None or actual.get("section_id") == planned_section:
            return True
    return False


def _requirement_analysis(
    normalized: dict[str, Any],
    *,
    draft_units: dict[str, Any] | None,
    draft_units_trusted: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    requirements = normalized["requirements"]
    if not draft_units_trusted or not isinstance(draft_units, dict):
        analysis = [{
            "requirement_id": item["requirement_id"],
            "occurrence_count": item["occurrence_count"],
            "chapter_ids": item["chapter_ids"],
            "section_ids": item["section_ids"],
            "actual_locations": [_simple_location(value) for value in item["locations"]],
            "planned_primary_locations": [],
            "planned_supporting_locations": [],
            "planned_table_locations": [],
            "planned_all_locations": [],
            "outside_planned_locations": [],
            "planning_comparison_status": "skipped",
        } for item in requirements]
        return analysis, [], {"status": "skipped", "reason": "input_hash_mismatch"}

    chapter_by_title = {
        _title_key(item["chapter_title"]): item["chapter_id"]
        for item in normalized["chapters"]
    }
    chapter_title_by_id = {
        item["chapter_id"]: _title_key(item["chapter_title"])
        for item in normalized["chapters"]
    }
    section_by_titles = {
        (item["chapter_id"], _title_key(item["heading"])): item["section_id"]
        for item in normalized["sections"]
    }
    plan = draft_units.get("content_location_plan")
    plan = plan if isinstance(plan, dict) else {}
    analysis: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    unresolved_count = 0
    requirement_ids = _unique_strings(
        list(item["requirement_id"] for item in requirements) + list(plan.keys())
    )
    occurrence_by_id = {item["requirement_id"]: item for item in requirements}
    for requirement_id in requirement_ids:
        occurrence = occurrence_by_id.get(requirement_id) or {
            "occurrence_count": 0, "chapter_ids": [], "section_ids": [], "locations": []
        }
        plan_item = plan.get(requirement_id)
        plan_item = plan_item if isinstance(plan_item, dict) else {}
        primary_value = plan_item.get("primary_content_location")
        primary = _planned_locations(
            [primary_value] if primary_value else [],
            chapter_by_title=chapter_by_title,
            section_by_titles=section_by_titles,
        )
        supporting = _planned_locations(
            plan_item.get("supporting_reference_locations") or [],
            chapter_by_title=chapter_by_title,
            section_by_titles=section_by_titles,
        )
        table = _planned_locations(
            plan_item.get("table_response_locations") or [],
            chapter_by_title=chapter_by_title,
            section_by_titles=section_by_titles,
        )
        all_planned = _planned_locations(
            plan_item.get("all_locations") or [],
            chapter_by_title=chapter_by_title,
            section_by_titles=section_by_titles,
        )
        if not all_planned:
            all_planned = _planned_locations(
                [*primary, *supporting, *table],
                chapter_by_title=chapter_by_title,
                section_by_titles=section_by_titles,
            )
        unresolved = sum(not item["resolved"] for item in all_planned)
        unresolved_count += unresolved
        resolved_planned = [item for item in all_planned if item["resolved"]]
        actual_locations = [_simple_location(value) for value in occurrence["locations"]]
        outside = (
            [item for item in actual_locations if not _actual_is_planned(item, resolved_planned)]
            if resolved_planned else []
        )
        comparison_status = "completed" if resolved_planned or not all_planned else "unresolved"
        record = {
            "requirement_id": requirement_id,
            "occurrence_count": occurrence["occurrence_count"],
            "chapter_ids": occurrence["chapter_ids"],
            "section_ids": occurrence["section_ids"],
            "actual_locations": actual_locations,
            "planned_primary_locations": primary,
            "planned_supporting_locations": supporting,
            "planned_table_locations": table,
            "planned_all_locations": all_planned,
            "outside_planned_locations": outside,
            "planning_comparison_status": comparison_status,
        }
        analysis.append(record)
        if outside:
            candidates.append({
                "candidate_id": _stable_id("SAC-REQ-", {
                    "candidate_type": "requirement_location_mismatch",
                    "requirement_id": requirement_id,
                    "outside_locations": sorted(
                        f"{item.get('chapter_id')}|{item.get('section_id')}" for item in outside
                    ),
                }),
                "candidate_type": "requirement_location_mismatch",
                "requirement_id": requirement_id,
                "outside_planned_locations": outside,
                "planned_all_locations": all_planned,
                "reason_codes": ["actual_location_outside_resolved_plan"],
            })
    check = {
        "status": "completed",
        "unresolved_planning_location_count": unresolved_count,
        "candidate_count": len(candidates),
    }
    return analysis, sorted(candidates, key=lambda item: item["candidate_id"]), check


def _score_analysis(
    score_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    analysis: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for item in score_items:
        record = {
            "score_item_id": item["score_item_id"],
            "occurrence_count": item["occurrence_count"],
            "chapter_ids": item["chapter_ids"],
            "section_ids": item["section_ids"],
            "actual_locations": [_simple_location(value) for value in item["locations"]],
        }
        analysis.append(record)
        if len(item["chapter_ids"]) >= SCORE_DISTRIBUTION_CHAPTER_THRESHOLD:
            candidates.append({
                "candidate_id": _stable_id("SAC-SCORE-", {
                    "candidate_type": "score_distribution",
                    "score_item_id": item["score_item_id"],
                    "chapter_ids": sorted(item["chapter_ids"]),
                }),
                "candidate_type": "score_distribution",
                **record,
                "reason_codes": ["score_item_spans_many_chapters"],
            })
    return analysis, sorted(candidates, key=lambda item: item["candidate_id"])


NUMERIC_PATTERNS = (
    (
        "compound_time",
        re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*[×xX]\s*\d+(?:\.\d+)?\s*(?:小时|時|时|h)(?![A-Za-z])", re.I),
    ),
    ("standard", re.compile(r"\bGB\s*/\s*T\s*\d+(?:[.\-]\d+)*\b", re.I)),
    ("version", re.compile(r"(?:软件版本|版本|version|v)\s*[:：]?\s*\d+(?:\.\d+)+", re.I)),
    ("percent", re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*%")),
    (
        "number_unit",
        re.compile(
            r"(?<![\d.])\d+(?:\.\d+)?\s*(?:Gbps|Mbps|Kbps|TB|GB|MB|KB|小时|分钟|秒|个月|工作日|日|天|年|月|套|台|个|节点|核|路)(?![A-Za-z])",
            re.I,
        ),
    ),
)


def _numeric_unit(raw: str, kind: str) -> str:
    compact = re.sub(r"\s+", "", raw)
    if kind == "compound_time":
        return "小时"
    if kind == "standard":
        return "GB/T"
    if kind == "version":
        return "version"
    if kind == "percent":
        return "%"
    match = re.search(r"[A-Za-z%/]+|[\u4e00-\u9fff]+$", compact)
    return match.group(0) if match else ""


def _normalized_numeric(raw: str, kind: str) -> str:
    value = re.sub(r"\s+", "", raw)
    value = re.sub(r"[xX]", "×", value)
    if kind == "standard":
        value = re.sub(r"(?i)GB/T", "GB/T", value)
    return value


def _numeric_candidates(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        occupied: list[tuple[int, int]] = []
        for kind, pattern in NUMERIC_PATTERNS:
            for match in pattern.finditer(paragraph["text"]):
                span = match.span()
                if any(span[0] < end and start < span[1] for start, end in occupied):
                    continue
                occupied.append(span)
                raw = match.group(0)
                candidates.append({
                    "candidate_id": _stable_id("SAC-NUM-", {
                        "candidate_type": "numeric_fact",
                        "paragraph_id": paragraph["paragraph_id"],
                        "span": list(span),
                        "raw_value": raw,
                    }),
                    "candidate_type": "numeric_fact",
                    "raw_value": raw,
                    "normalized_value": _normalized_numeric(raw, kind),
                    "unit": _numeric_unit(raw, kind),
                    "paragraph_id": paragraph["paragraph_id"],
                    "chapter_id": paragraph["chapter_id"],
                    "section_id": paragraph["section_id"],
                    "excerpt": _excerpt(paragraph["text"]),
                })
    return sorted(candidates, key=lambda item: item["candidate_id"])


def _terminology_candidates(
    paragraphs: list[dict[str, Any]], draft_units: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if not isinstance(draft_units, dict):
        return []
    terms = _unique_strings(draft_units.get("terminology_constraints") or [])
    # Imperative policy sentences are constraints, not preferred terms.
    terms = [
        term for term in terms
        if len(term) <= 30 and not any(marker in term for marker in ("不得", "禁止", "应当", "必须"))
    ]
    result: list[dict[str, Any]] = []
    for term in terms:
        locations: list[dict[str, Any]] = []
        for paragraph in paragraphs:
            count = paragraph["normalized_text"].count(term)
            locations.extend(_paragraph_location(paragraph) for _ in range(count))
        if not locations:
            continue
        result.append({
            "candidate_id": _stable_id("SAC-TERM-", {
                "candidate_type": "terminology_occurrence",
                "preferred_term": term,
            }),
            "candidate_type": "terminology_occurrence",
            "preferred_term": term,
            "occurrence_count": len(locations),
            "locations": locations,
            "suspected_variants": [],
            "reason_codes": ["trusted_preferred_term_occurrence"],
        })
    return sorted(result, key=lambda item: item["candidate_id"])


def run_deterministic_audit(
    normalized: dict[str, Any],
    *,
    draft_units: dict[str, Any] | None,
    draft_units_trusted: bool,
) -> DeterministicAuditResult:
    paragraphs = normalized["paragraphs"]
    issues, exact_group_count = _exact_duplicate_issues(paragraphs)
    duplicate_pairs = _near_duplicate_candidates(paragraphs)
    confirmations = _confirmation_register(normalized["placeholders"], paragraphs)
    alias_candidates = _placeholder_alias_candidates(
        normalized["placeholders"],
        paragraphs,
        draft_units=draft_units,
        draft_units_trusted=draft_units_trusted,
    )
    requirement_analysis, requirement_candidates, requirement_check = _requirement_analysis(
        normalized,
        draft_units=draft_units,
        draft_units_trusted=draft_units_trusted,
    )
    score_analysis, score_candidates = _score_analysis(normalized["score_items"])
    numeric_candidates = _numeric_candidates(paragraphs)
    terminology = (
        _terminology_candidates(paragraphs, draft_units)
        if draft_units_trusted else []
    )
    semantic_candidates = {
        "duplicate_pairs": duplicate_pairs,
        "confirmation_aliases": alias_candidates,
        "requirement_location_mismatches": requirement_candidates,
        "score_distribution": score_candidates,
        "numeric_facts": numeric_candidates,
        "terminology": terminology,
    }
    checks = {
        "exact_cross_content_duplication": {
            "status": "completed",
            "issue_count": len(issues),
            "duplicate_group_count": exact_group_count,
        },
        "near_duplicate_candidates": {
            "status": "completed",
            "candidate_count": len(duplicate_pairs),
        },
        "placeholder_confirmation_aggregation": {
            "status": "completed",
            "confirmation_count": len(confirmations),
        },
        "placeholder_alias_candidates": {
            "status": "completed",
            "candidate_count": len(alias_candidates),
            "planning_enrichment": "completed" if draft_units_trusted else "skipped",
        },
        "requirement_location_comparison": requirement_check,
        "score_item_distribution": {
            "status": "completed",
            "candidate_count": len(score_candidates),
        },
        "numeric_fact_extraction": {
            "status": "completed",
            "candidate_count": len(numeric_candidates),
        },
        "terminology_candidates": (
            {"status": "completed", "candidate_count": len(terminology)}
            if draft_units_trusted
            else {"status": "skipped", "reason": "input_hash_mismatch"}
        ),
    }
    return DeterministicAuditResult(
        issues=issues,
        confirmation_register=confirmations,
        semantic_candidates=semantic_candidates,
        coverage_analysis={
            "requirement_ids": [item["requirement_id"] for item in normalized["requirements"]],
            "score_item_ids": [item["score_item_id"] for item in normalized["score_items"]],
            "placeholder_ids": [item["placeholder_id"] for item in normalized["placeholders"]],
            "requirements": requirement_analysis,
            "score_items": score_analysis,
        },
        deterministic_checks=checks,
    )

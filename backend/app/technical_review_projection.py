from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


_TECHNICAL_REQUIREMENT_TYPES = {"technical_mandatory_requirement"}
_CROSS_DOMAIN_REQUIREMENT_TYPES = {
    "mandatory_substantive_response",
    "delivery_acceptance_commitment",
    "confidentiality_security_requirement",
}
_NON_TECHNICAL_REQUIREMENT_TYPES = {
    "potential_invalidity_risk",
    "qualification_requirement",
    "signature_seal_requirement",
    "format_submission_requirement",
    "deviation_response_requirement",
    "special_table_requirement",
    "reference_only",
    "needs_review",
}

_INTERNAL_ID = re.compile(
    r"\b(?:TR-\d+|WC-[A-Z0-9-]+|TASK-[A-Z0-9-]+|FAI-[A-Z0-9-]+|"
    r"FCA-[A-Z0-9-]+|FSF-[A-Z0-9-]+|PH-[A-Z0-9-]+)\b",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\{*\bPH-(?:FIELD-)?[A-Z0-9-]+\b\}*", re.IGNORECASE)
_RUN_ID = re.compile(r"\bbid-\d{8}T\d{6}Z-[a-z0-9]+\b", re.IGNORECASE)
_SOURCE_ID = re.compile(r"\bsource_[a-z0-9]+\b", re.IGNORECASE)
_SHA_LABEL = re.compile(r"\b(?:sha(?:256)?\s*[:=]\s*)?[a-f0-9]{64}\b", re.IGNORECASE)
_PLACEHOLDER_TITLES = {
    "PH-MANUFACTURER": "制造商",
    "PH-ORIGIN": "产地",
    "PH-ACTUAL-PARAMETERS": "实际技术参数",
    "PH-ACTUAL-CONFIGURATION": "实际产品配置",
    "PH-PROJECT-TEAM": "项目团队",
    "PH-STAFFING-PLAN": "人员配置计划",
}


def is_technical_writing_requirement(
    requirement: dict[str, Any],
    *,
    mapped_requirement_ids: set[str],
    technical_score_ids: set[str],
    technical_score_requirement_ids: set[str],
) -> bool:
    """The shared structured technical-writing scope rule."""
    requirement_id = str(requirement.get("requirement_id") or "")
    requirement_type = str(requirement.get("requirement_type") or "")
    scope_type = str((requirement.get("applicable_scope") or {}).get("scope_type") or "")
    if requirement_type in _NON_TECHNICAL_REQUIREMENT_TYPES:
        return False
    if requirement_type in _TECHNICAL_REQUIREMENT_TYPES:
        return True
    if requirement_type == "score_related_requirement":
        related = {str(value) for value in requirement.get("related_score_item_ids") or [] if value}
        return requirement_id in technical_score_requirement_ids or bool(related & technical_score_ids)
    if requirement_type in _CROSS_DOMAIN_REQUIREMENT_TYPES:
        return scope_type == "technical_response" or requirement_id in mapped_requirement_ids
    return False


def active_technical_review_items(
    package: dict[str, Any], package_path: str | Path
) -> list[dict[str, Any]]:
    """Return the active technical-body review set; retain unknown legacy items conservatively."""
    context = _scope_context(Path(package_path))
    if context is None:
        return list(package.get("review_items") or [])
    requirements, response_points, mapped_points, technical_score_ids = context
    active: list[dict[str, Any]] = []
    for item in package.get("review_items") or []:
        ids = [str(value) for value in item.get("requirement_ids") or [] if value]
        if not ids:
            active.append(item)
            continue
        classifications: list[bool] = []
        for point_id in ids:
            matches = _requirements_for_point(response_points.get(point_id), requirements)
            if not matches:
                continue
            matrix_mapped_ids = {
                str(match.get("requirement_id") or "")
                for match in matches
                if point_id in mapped_points
            }
            classifications.extend(
                is_technical_writing_requirement(
                    match,
                    mapped_requirement_ids=matrix_mapped_ids,
                    technical_score_ids=technical_score_ids,
                    technical_score_requirement_ids=set(),
                )
                for match in matches
            )
        if not classifications or any(classifications):
            active.append(item)
    return active


def active_issue_keys(package: dict[str, Any], package_path: str | Path) -> set[tuple[str, str]]:
    return {
        (str(item.get("source_type") or ""), str(item.get("source_id") or ""))
        for item in active_technical_review_items(package, package_path)
    }


def confirmation_groups(
    package: dict[str, Any], human: dict[str, Any], package_path: str | Path
) -> list[dict[str, Any]]:
    """Group confirmation occurrences only by reliable canonical identity."""
    audit = _nearby_json(Path(package_path), "technical_fulltext.audit.json") or {}
    register = {
        str(item.get("confirmation_id") or ""): item
        for item in audit.get("confirmation_register") or []
    }
    queue = {
        (str(item.get("source_type") or ""), str(item.get("source_id") or "")): item
        for item in package.get("fact_material_queue") or []
    }
    review_items = list(package.get("review_items") or [])
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in human.get("fact_confirmations") or []:
        source_type = str(record.get("source_type") or "")
        source_id = str(record.get("source_id") or "")
        if source_type == "FACT":
            semantic = str(record.get("fact_id") or record.get("fact_key") or source_id).strip()
            key = ("FACT", semantic or source_id)
        else:
            entry = register.get(source_id) or {}
            members = entry.get("member_placeholder_ids") or []
            reliable = str(entry.get("canonicalization") or "") in {
                "exact_placeholder_id", "registered_placeholder_id"
            }
            placeholder = _canonical_placeholder(members[0]) if reliable and len(members) == 1 else None
            key = ("PLACEHOLDER", placeholder) if placeholder else ("FCA", source_id)
        buckets[key].append(record)

    result: list[dict[str, Any]] = []
    for semantic_key, records in buckets.items():
        representative = records[0]
        locations: list[dict[str, Any]] = []
        occurrence_count = 0
        for record in records:
            source_type = str(record.get("source_type") or "")
            source_id = str(record.get("source_id") or "")
            entry = register.get(source_id) or {}
            registered_locations = list(entry.get("affected_locations") or [])
            locations.extend(registered_locations)
            occurrence_count += int(entry.get("occurrence_count") or len(registered_locations) or 0)
            for review_item in review_items:
                refs = (
                    list(review_item.get("required_fact_refs") or [])
                    + list(review_item.get("required_confirmation_refs") or [])
                    + list(review_item.get("deferred_confirmation_refs") or [])
                )
                if source_id in refs or str(review_item.get("source_id") or "") == source_id:
                    locations.extend(review_item.get("current_response_locations") or [])
        locations = _unique_locations(locations)
        if not occurrence_count:
            occurrence_count = len(locations) or 1
        statuses = {str(record.get("confirmation_status") or "pending") for record in records}
        effective = "confirmed" if statuses == {"confirmed"} else "rejected" if statuses == {"rejected"} else "pending"
        result.append(
            {
                "semantic_key": semantic_key,
                "representative": representative,
                "records": records,
                "queue": queue.get(
                    (str(representative.get("source_type") or ""), str(representative.get("source_id") or "")),
                    {},
                ),
                "status": effective,
                "locations": locations,
                "occurrence_count": max(occurrence_count, len(locations)),
            }
        )
    return result


def semantic_confirmation_source_ids(
    package: dict[str, Any], human: dict[str, Any], package_path: str | Path, source_id: str
) -> list[str]:
    for group in confirmation_groups(package, human, package_path):
        ids = [str(item.get("source_id") or "") for item in group["records"]]
        if source_id in ids:
            return ids
    return [source_id]


def confirmation_display_title(
    group: dict[str, Any], raw_title: Any, identity_titles: dict[str, str]
) -> str:
    semantic_type, semantic_id = group["semantic_key"]
    if semantic_type == "PLACEHOLDER" and semantic_id in _PLACEHOLDER_TITLES:
        return _PLACEHOLDER_TITLES[semantic_id]
    projected = user_facing_text(raw_title, identity_titles) or ""
    if "待确认信息" in projected or "待确认占位事实" in projected:
        return "待确认信息（名称未记录）"
    return projected or "待确认信息（名称未记录）"


def user_facing_text(value: Any, identity_titles: dict[str, str] | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    titles = identity_titles or {}
    for identity, title in sorted(titles.items(), key=lambda item: -len(item[0])):
        if not identity or identity not in text:
            continue
        natural = str(title or "").strip()
        if natural:
            leaf = natural.split(" > ")[-1]
            titled = re.compile(
                re.escape(identity)
                + r"\s*[：:]?\s*[\u201c\"']?"
                + re.escape(leaf)
                + r"[\u201d\"']?",
                re.IGNORECASE,
            )
            text = titled.sub(natural, text)
            duplicate = re.compile(
                re.escape(identity) + r"\s*[：:]?\s*(?=[\u201c\u201d\"']?" + re.escape(natural) + r")",
                re.IGNORECASE,
            )
            text = duplicate.sub("", text)
            text = re.sub(re.escape(identity), natural, text, flags=re.IGNORECASE)
        else:
            text = re.sub(re.escape(identity), "", text, flags=re.IGNORECASE)
    text = _PLACEHOLDER.sub("待确认信息", text)
    text = _INTERNAL_ID.sub("", text)
    text = _RUN_ID.sub("", text)
    text = _SOURCE_ID.sub("", text)
    text = _SHA_LABEL.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([，。；：,.!])", r"\1", text)
    text = re.sub(r"^[：:、/\-\s]+", "", text)
    return text.strip() or None


def identity_title_map(package: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in package.get("response_matrix") or []:
        identity = str(row.get("requirement_id") or "")
        title = str(row.get("requirement_text") or "").strip()
        if identity and title:
            result[identity] = title
    for item in package.get("review_items") or []:
        identity = str(item.get("source_id") or "")
        title = str(item.get("display_title") or "").strip()
        if identity and title:
            result[identity] = re.sub(r"^TR-\d+\s*[：:]?\s*", "", title)
        for location in (
            list(item.get("current_response_locations") or [])
            + list(item.get("recommended_revision_locations") or [])
        ):
            _add_location_titles(result, location)
    return result


def _add_location_titles(result: dict[str, str], location: Any) -> None:
    if not isinstance(location, dict):
        return
    chapter_id = str(location.get("chapter_id") or "")
    section_id = str(location.get("section_id") or "")
    chapter = str(location.get("chapter_title") or "").strip()
    section = str(location.get("section_heading") or location.get("heading") or "").strip()
    if chapter_id and chapter:
        result[chapter_id] = chapter
    if section_id and section:
        result[section_id] = " > ".join(value for value in (chapter, section) if value)


def _scope_context(package_path: Path):
    matrix = _nearby_json(package_path, "tender_requirement_matrix.json")
    outline = _nearby_json(package_path, "technical_response_outline.json")
    tasks = _nearby_json(package_path, "section_writing_tasks.json")
    if not matrix or not outline:
        return None
    points = {
        str(point.get("point_id") or ""): point
        for chapter in outline.get("chapters") or []
        for point in chapter.get("response_points") or []
        if point.get("point_id")
    }
    mapped = {
        str(mapping.get("response_point_id") or "")
        for task in (tasks or {}).get("tasks") or []
        for mapping in task.get("response_point_mappings") or []
        if isinstance(mapping, dict) and mapping.get("response_point_id")
    }
    technical_scores = {
        str(item.get("score_item_id") or "")
        for item in outline.get("score_items") or []
        if item.get("score_item_id")
    }
    return list(matrix.get("requirements") or []), points, mapped, technical_scores


def _requirements_for_point(point: Any, requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(point, dict):
        return []
    text = _normal(point.get("original_text"))
    path = [_normal(value) for value in point.get("section_path") or [] if _normal(value)]
    source_names = {
        Path(str(ref.get("file_name") or "")).name
        for ref in point.get("source_refs") or []
        if ref.get("file_name")
    }
    primary_matches: list[dict[str, Any]] = []
    contextual_matches: list[dict[str, Any]] = []
    path_matches: list[dict[str, Any]] = []
    for requirement in requirements:
        source = requirement.get("source_ref") or {}
        req_name = Path(str(source.get("source_file_name") or "")).name
        if source_names and req_name and req_name not in source_names:
            continue
        primary_candidates = [
            _normal(requirement.get("raw_text")),
            _normal(source.get("excerpt")),
        ]
        clause_path = _normal(source.get("clause_path"))
        context_candidates = [
            _normal(source.get("context_before")),
            _normal(source.get("context_after")),
        ]
        primary_match = bool(text and any(text in value or value in text for value in primary_candidates if value))
        contextual_match = bool(text and any(text == value for value in context_candidates if value))
        path_match = bool(
            clause_path
            and (
                clause_path == text
                or (path and any(clause_path in value or value in clause_path for value in path))
            )
        )
        if primary_match:
            primary_matches.append(requirement)
        elif contextual_match:
            contextual_matches.append(requirement)
        elif path_match:
            path_matches.append(requirement)
    selected = primary_matches or contextual_matches or path_matches
    score_matches = [
        item
        for item in [*primary_matches, *contextual_matches, *path_matches]
        if item.get("requirement_type") == "score_related_requirement"
    ]
    return score_matches or selected


def _nearby_json(start: Path, filename: str) -> dict[str, Any] | None:
    resolved = start.resolve()
    parents = list(resolved.parents)
    roots = [resolved.parent]
    fulltext_root = next((root for root in parents if root.name == "fulltext"), None)
    if fulltext_root is not None:
        roots.extend([fulltext_root, fulltext_root.parent])
    run_root = next(
        (root for root in parents[3:5] if root.name.startswith("bid-")), None
    )
    if run_root is not None:
        roots.append(run_root)
    for root in roots:
        candidates = sorted(root.rglob(filename)) if root.is_dir() else []
        for candidate in candidates:
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
    return None


def _canonical_placeholder(value: Any) -> str | None:
    text = str(value or "").strip().strip("{}").strip().upper()
    return text if re.fullmatch(r"PH-(?:FIELD-)?[A-Z0-9-]+", text) else None


def _normal(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _unique_locations(values: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        key = tuple(value.get(name) for name in ("chapter_id", "section_id", "paragraph_id", "paragraph_index"))
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result

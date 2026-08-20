from __future__ import annotations

from typing import Any


def build_response_matrix(
    project: dict[str, Any],
    match_result: dict[str, Any],
    gap_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic response matrix from tasks, scores, matches, and gaps."""

    tasks = list(project.get("generation_tasks") or [])
    score_items_by_id = {
        str(item.get("id") or ""): item for item in list(project.get("score_items") or [])
    }
    matches_by_task_id = {
        str(item.get("task_id") or ""): item for item in list(match_result.get("results") or [])
    }
    gaps_by_task_id = {
        str(item.get("task_id") or ""): item for item in list(gap_result.get("items") or [])
    }

    items: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        task_type = str(task.get("task_type") or "")
        related_score_item_ids = [str(item) for item in list(task.get("related_score_item_ids") or [])]
        related_score_items = [
            score_items_by_id[item_id] for item_id in related_score_item_ids if item_id in score_items_by_id
        ]
        match_item = matches_by_task_id.get(task_id, {})
        gap_item = gaps_by_task_id.get(task_id, {})
        readiness = str(gap_item.get("readiness") or "missing")
        risk_level = str(gap_item.get("risk_level") or "high")
        matched_materials = list(match_item.get("matched_materials") or [])
        usable_materials, supporting_materials = matrix_material_groups(matched_materials, gap_item)

        items.append(
            {
                "task_id": task_id,
                "task_type": task_type,
                "target_section": str(task.get("target_section") or ""),
                "related_score_item_ids": related_score_item_ids,
                "score_items": related_score_items,
                "related_requirements": list(task.get("related_requirements") or []),
                "evidence_needed": list(task.get("evidence_needed") or []),
                "matched_materials": matched_materials,
                "usable_materials": usable_materials,
                "supporting_materials": supporting_materials,
                "covered_evidence": list(gap_item.get("covered_evidence") or []),
                "missing_evidence": list(gap_item.get("missing_evidence") or []),
                "readiness": readiness,
                "risk_level": risk_level,
                "response_strategy": build_response_strategy(task, related_score_items, readiness),
                "generation_instruction": str(task.get("generation_instruction") or ""),
                "recommended_writer_agent": recommend_writer_agent(task_type),
                "recommended_review_agent": "chapter_review",
                "status": determine_matrix_status(task_type, readiness),
            }
        )

    return {
        "status": "success",
        "total_items": len(items),
        "items": items,
        "summary": build_summary(items),
    }


def recommend_writer_agent(task_type: str) -> str:
    if task_type == "business":
        return "business_writer"
    if task_type == "technical":
        return "technical_writer"
    return "manual"


def matrix_material_groups(
    matched_materials: list[dict[str, Any]],
    gap_item: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    usable_from_gap = list(gap_item.get("usable_materials") or [])
    supporting_from_gap = list(gap_item.get("supporting_materials") or [])
    if usable_from_gap or supporting_from_gap:
        return usable_from_gap, supporting_from_gap

    usable: list[dict[str, Any]] = []
    supporting: list[dict[str, Any]] = []
    for material in matched_materials:
        if str(material.get("usability") or "").lower() == "usable":
            usable.append(material)
        else:
            supporting.append(material)
    return usable, supporting


def determine_matrix_status(task_type: str, readiness: str) -> str:
    if readiness == "ready":
        return "ready_for_writing"
    if readiness == "partial":
        return "needs_review"
    if readiness == "missing":
        return "needs_materials"
    return "needs_review"


def build_response_strategy(
    task: dict[str, Any],
    score_items: list[dict[str, Any]],
    readiness: str,
) -> str:
    names = [str(item.get("item_name") or item.get("id") or "") for item in score_items]
    names = [name for name in names if name]
    base = "Address the related scoring requirement"
    if names:
        base += ": " + ", ".join(names)
    if readiness == "ready":
        return base + "; cite matched evidence and prepare for writing."
    if readiness == "partial":
        return base + "; fill missing evidence before drafting final content."
    if readiness == "missing":
        return base + "; collect source materials before writing."
    return base + "; evidence requirement is not explicit, so review manually."


def build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    readiness_counts: dict[str, int] = {}
    writer_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}

    for item in items:
        increment(status_counts, item.get("status"))
        increment(readiness_counts, item.get("readiness"))
        increment(writer_counts, item.get("recommended_writer_agent"))
        increment(risk_counts, item.get("risk_level"))

    return {
        "status_counts": status_counts,
        "readiness_counts": readiness_counts,
        "writer_counts": writer_counts,
        "risk_counts": risk_counts,
        "ready_for_writing_count": status_counts.get("ready_for_writing", 0),
        "needs_materials_count": status_counts.get("needs_materials", 0),
        "needs_review_count": status_counts.get("needs_review", 0),
        "unsupported_count": status_counts.get("unsupported", 0),
    }


def increment(counts: dict[str, int], value: Any) -> None:
    key = str(value or "unknown")
    counts[key] = counts.get(key, 0) + 1

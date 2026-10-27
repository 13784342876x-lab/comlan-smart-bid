from __future__ import annotations

from typing import Any


def build_tender_project_graph(
    tender_file_id: str,
    score_file_id: str,
    lot_name: str | None,
    tender_parse: dict[str, Any],
    score_parse: dict[str, Any],
) -> dict[str, Any]:
    """Build a Tender Task Graph from parsed tender and score table data.

    This builder does not invent score items. Generation tasks are derived only
    from score_parse["score_items"], so the downstream document workflow stays
    traceable to the score table parser output.
    """

    score_items = list(score_parse.get("score_items") or [])
    weights = score_parse.get("weights") or {}

    return {
        "project_name": extract_project_name(tender_parse),
        "project_summary": tender_parse.get("project_summary") or "",
        "lot_name": lot_name,
        "tender_file_id": tender_file_id,
        "score_file_id": score_file_id,
        "sections": tender_parse.get("sections") or [],
        "qualification_requirements": tender_parse.get("qualification_requirements") or [],
        "disqualification_rules": merge_disqualification_rules(tender_parse, score_parse),
        "score_items": score_items,
        "business_score_items": score_parse.get("business_score_items") or [],
        "technical_score_items": score_parse.get("technical_score_items") or [],
        "price_score_rule": score_parse.get("price_score_rule") or {},
        "score_scale": score_parse.get("score_scale") or {},
        "document_structure": build_document_structure(weights),
        "generation_tasks": build_generation_tasks(score_items),
    }


def extract_project_name(tender_parse: dict[str, Any]) -> str:
    project_info = tender_parse.get("project_info") or {}
    if isinstance(project_info, dict):
        return str(project_info.get("project_name") or project_info.get("name") or "")
    return ""


def merge_disqualification_rules(tender_parse: dict[str, Any], score_parse: dict[str, Any]) -> list[Any]:
    merged: list[Any] = []
    for key in ("disqualification_rules", "rejection_clauses"):
        value = tender_parse.get(key) or []
        if isinstance(value, list):
            merged.extend(value)
    value = score_parse.get("disqualification_rules") or []
    if isinstance(value, list):
        merged.extend(value)
    return merged


def build_document_structure(weights: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "section_name": "\u5546\u52a1\u6807",
            "type": "business",
            "weight": normalize_weight(weights.get("business")),
        },
        {
            "section_name": "\u6280\u672f\u6807",
            "type": "technical",
            "weight": normalize_weight(weights.get("technical")),
        },
        {
            "section_name": "\u4ef7\u683c\u6807",
            "type": "price",
            "weight": normalize_weight(weights.get("price")),
        },
    ]


def build_generation_tasks(score_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(score_items, start=1):
        item_id = str(item.get("id") or f"score_item_{index:03d}")
        tasks.append(
            {
                "task_id": f"task_{index:03d}_{slugify(item_id)}",
                "target_section": item.get("target_section") or "",
                "task_type": normalize_task_type(item.get("category")),
                "related_score_item_ids": [item_id],
                "related_requirements": collect_related_requirements(item),
                "evidence_needed": list(item.get("evidence_needed") or []),
                "kb_mapping_hint": list(item.get("kb_mapping_hint") or []),
                "generation_instruction": item.get("generation_instruction") or "",
                "status": "pending",
            }
        )
    return tasks


def collect_related_requirements(item: dict[str, Any]) -> list[str]:
    requirements: list[str] = []
    for key in ("requirement", "score_rule", "raw_text"):
        value = item.get(key)
        if value:
            requirements.append(str(value))
    return requirements


def normalize_task_type(category: Any) -> str:
    value = str(category or "technical")
    if value in {"business", "technical", "price", "qualification"}:
        return value
    if value == "compliance":
        return "qualification"
    return "technical"


def normalize_weight(value: Any) -> float:
    if value is None or value == "":
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if number.is_integer() else number


def slugify(value: str) -> str:
    cleaned = []
    for char in value:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            cleaned.append(char)
        else:
            cleaned.append("_")
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:48] or "item"

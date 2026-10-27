from __future__ import annotations

import copy
from typing import Any


def refine_writing_outline(
    original_chapters: list[dict[str, Any]],
    format_analysis: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Apply generic normalization without redefining the business outline."""
    chapters = copy.deepcopy(original_chapters)
    required_forms = {str(item.get("title") or "") for item in format_analysis.get("required_forms") or []}
    module_adaptations: list[dict[str, Any]] = []
    for chapter in chapters:
        module_ids = _module_ids(chapter)
        chapter["module_ids"] = module_ids
        chapter["chapter_type"] = _chapter_type(chapter, module_ids, required_forms)
        _merge_descendant_metadata(chapter)
        for index, child in enumerate(chapter.get("children") or [], start=1):
            child["chapter_id"] = str(child.get("chapter_id") or f"{chapter['chapter_id']}-{index:02d}")
            child["module_ids"] = list(module_ids)
            child["chapter_type"] = chapter["chapter_type"]
        for module_id in module_ids:
            module_adaptations.append(
                {
                    "module_id": module_id,
                    "activation_status": "triggered" if chapter.get("triggered_by") else "default",
                    "placement_role": "form" if chapter["chapter_type"] == "table" else "top_level",
                    "parent_chapter_id": None,
                    "chapter_id": chapter.get("chapter_id"),
                    "placement_reason": "保留 Adapter 基于公司通用模块和当前项目证据形成的章节。",
                }
            )
    report = {
        "original_top_level_count": len(original_chapters),
        "refined_top_level_count": len(chapters),
        "normalization": "generic_metadata_only",
        "top_level_chapters": [
            {
                "chapter_id": chapter.get("chapter_id"),
                "title": chapter.get("title"),
                "module_ids": chapter.get("module_ids") or [],
            }
            for chapter in chapters
        ],
        "nested_modules": [],
        "conditional_modules": [
            item for item in module_adaptations if item["activation_status"] == "triggered"
        ],
        "excluded_modules": [],
        "module_placement_map": module_adaptations,
    }
    return chapters, module_adaptations, report


def _module_ids(chapter: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value)
            for value in chapter.get("module_ids") or chapter.get("company_outline_node_ids") or []
            if str(value).startswith("MODULE-")
        )
    )


def _chapter_type(chapter: dict[str, Any], module_ids: list[str], required_forms: set[str]) -> str:
    if "MODULE-PARAMETER-RESPONSE" in module_ids:
        return "table"
    if "MODULE-TECHNICAL-DEVIATION" in module_ids and "技术偏差表" in required_forms:
        return "table"
    return str(chapter.get("chapter_type") or "narrative")


def _merge_descendant_metadata(chapter: dict[str, Any]) -> None:
    """Expose leaf coverage on the root while preserving leaf mappings."""
    for child in chapter.get("children") or []:
        for key in ("tender_structure_refs", "display_block_ids", "score_item_refs", "triggered_by"):
            chapter.setdefault(key, [])
            for value in child.get(key) or []:
                if value not in chapter[key]:
                    chapter[key].append(copy.deepcopy(value))
        chapter.setdefault("response_point_mappings", [])
        for mapping in child.get("response_point_mappings") or []:
            if mapping not in chapter["response_point_mappings"]:
                chapter["response_point_mappings"].append(copy.deepcopy(mapping))

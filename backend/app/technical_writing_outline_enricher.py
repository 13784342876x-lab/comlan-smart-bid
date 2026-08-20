from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


NEED_ORDER = {
    "manual_project_facts": {"none": 0, "optional": 1, "required": 2},
    "local_kb": {"none": 0, "optional": 1, "required": 2},
    "web": {"forbidden": 0, "optional": 1, "required": 2},
}
DRAFT_ORDER = {"can_draft": 0, "can_draft_with_placeholders": 1, "cannot_finalize": 2}


def merge_information_requirements_into_outline(
    writing_outline: dict[str, Any],
    task_information: dict[str, Any],
    writing_tasks: dict[str, Any],
) -> dict[str, Any]:
    """Return an enriched copy without changing any title, node, or response mapping."""
    result = copy.deepcopy(writing_outline)
    info_by_section = {
        str(item.get("section_id") or ""): item
        for item in task_information.get("tasks") or []
        if item.get("section_id")
    }
    task_by_section = {
        str(item.get("section_id") or ""): item
        for item in writing_tasks.get("tasks") or []
        if item.get("section_id")
    }
    score_titles = {
        str(item.get("score_item_id") or ""): str(item.get("title") or "")
        for item in result.get("score_items") or []
    }
    matched_sections: list[str] = []
    for chapter in result.get("chapters") or []:
        _enrich_node(chapter, info_by_section, task_by_section, score_titles, matched_sections)

    formal_inputs = [
        "outputs/<output_key>/outline/technical_response_outline.json",
        "outputs/<output_key>/writing/technical_writing_outline.json",
    ]
    result["agent_input_contract"] = {
        "schema_version": "technical_writing_agent_input_contract_v0",
        "formal_planning_inputs": formal_inputs,
        "input_roles": {
            formal_inputs[0]: "tender_requirement_fact_source",
            formal_inputs[1]: "final_article_structure_and_writing_plan",
            "confirmed_project_facts_path": "optional_confirmed_project_facts",
        },
        "target_selectors": ["target_chapter_id", "target_section_id"],
        "internal_analysis_artifacts": [
            "section_writing_tasks.json",
            "task_information_requirements.json",
            "draft_units.json",
            "task_readiness_matrix.json",
        ],
        "internal_artifacts_required_from_user": False,
        "task_id_is_external_input": False,
        "draft_unit_id_is_external_input": False,
    }
    result["writing_requirement_merge"] = {
        "schema_version": "writing_requirements_merge_v0",
        "matched_atomic_task_count": len(matched_sections),
        "matched_section_ids": sorted(matched_sections),
        "tender_text_embedded": False,
        "response_text_resolution": "resolve response_point_ids from technical_response_outline.json on demand",
    }
    return result


def enrich_writing_outline_files(
    writing_outline_path: str | Path,
    task_information_path: str | Path,
    writing_tasks_path: str | Path,
    *,
    markdown_renderer,
) -> dict[str, Any]:
    writing_path = Path(writing_outline_path).expanduser().resolve()
    information_path = Path(task_information_path).expanduser().resolve()
    tasks_path = Path(writing_tasks_path).expanduser().resolve()
    writing = json.loads(writing_path.read_text(encoding="utf-8-sig"))
    information = json.loads(information_path.read_text(encoding="utf-8-sig"))
    tasks = json.loads(tasks_path.read_text(encoding="utf-8-sig"))
    enriched = merge_information_requirements_into_outline(writing, information, tasks)
    writing_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = writing_path.with_suffix(".md")
    markdown_path.write_text(markdown_renderer(enriched), encoding="utf-8")
    return {
        "outline": enriched,
        "files": {
            "technical_writing_outline_json": str(writing_path),
            "technical_writing_outline_markdown": str(markdown_path),
        },
    }


def _enrich_node(
    node: dict[str, Any],
    info_by_section: dict[str, dict[str, Any]],
    task_by_section: dict[str, dict[str, Any]],
    score_titles: dict[str, str],
    matched_sections: list[str],
) -> dict[str, Any]:
    children = list(node.get("children") or [])
    for child in children:
        _enrich_node(child, info_by_section, task_by_section, score_titles, matched_sections)
    section_id = str(node.get("chapter_id") or "")
    info = info_by_section.get(section_id)
    task = task_by_section.get(section_id)
    if info is not None:
        node["writing_requirements"] = _leaf_requirements(node, info, task or {}, score_titles)
        matched_sections.append(section_id)
    elif children:
        child_requirements = [child.get("writing_requirements") for child in children if child.get("writing_requirements")]
        node["writing_requirements"] = _aggregate_requirements(node, child_requirements, score_titles)
    else:
        node["writing_requirements"] = _requirements_from_node(node, score_titles)
    return node["writing_requirements"]


def _leaf_requirements(
    node: dict[str, Any],
    info: dict[str, Any],
    task: dict[str, Any],
    score_titles: dict[str, str],
) -> dict[str, Any]:
    requirements = info.get("information_requirements") or {}
    manual = requirements.get("manual_project_fact") or {}
    local = requirements.get("local_kb") or {}
    web = requirements.get("web") or {}
    response_ids = list(dict.fromkeys(info.get("response_point_ids") or []))
    score_refs = list(dict.fromkeys(info.get("score_item_refs") or []))
    points = [f"围绕“{node.get('title', '')}”完成章节目标"]
    if response_ids:
        points.append("按response_point_ids逐项覆盖招标要求，原文从技术响应大纲按ID解析")
    for score_ref in score_refs:
        title = score_titles.get(str(score_ref), "")
        if title:
            points.append(f"满足评分主题：{title}")
    objective = str(task.get("writing_objective") or "").strip()
    if objective:
        points.append(objective)
    return {
        "response_point_ids": response_ids,
        "score_item_refs": score_refs,
        "required_content_points": list(dict.fromkeys(points)),
        "manual_project_facts": {
            "need": str(manual.get("need") or "none"),
            "fact_types": list(dict.fromkeys(manual.get("fact_types") or [])),
        },
        "local_kb": {
            "need": str(local.get("need") or "none"),
            "evidence_types": list(dict.fromkeys(local.get("evidence_types") or [])),
        },
        "web": {
            "need": str(web.get("need") or "forbidden"),
            "allowed_source_types": list(dict.fromkeys(web.get("allowed_source_types") or [])),
            "purposes": list(dict.fromkeys(web.get("purposes") or [])),
        },
        "draftability": str((info.get("draftability") or {}).get("status") or "can_draft"),
    }


def _aggregate_requirements(
    node: dict[str, Any],
    requirements: list[dict[str, Any]],
    score_titles: dict[str, str],
) -> dict[str, Any]:
    response_ids = _union(requirements, "response_point_ids")
    score_refs = _union(requirements, "score_item_refs")
    points = [f"统筹“{node.get('title', '')}”下属标题，保持各子节点内容边界"]
    if response_ids:
        points.append("按子节点response_point_ids从技术响应大纲加载相关原文")
    for score_ref in score_refs:
        title = score_titles.get(str(score_ref), "")
        if title:
            points.append(f"统筹评分主题：{title}")
    return {
        "response_point_ids": response_ids,
        "score_item_refs": score_refs,
        "required_content_points": list(dict.fromkeys(points)),
        "manual_project_facts": {
            "need": _max_need(requirements, "manual_project_facts"),
            "fact_types": _union_nested(requirements, "manual_project_facts", "fact_types"),
        },
        "local_kb": {
            "need": _max_need(requirements, "local_kb"),
            "evidence_types": _union_nested(requirements, "local_kb", "evidence_types"),
        },
        "web": {
            "need": _max_need(requirements, "web"),
            "allowed_source_types": _union_nested(requirements, "web", "allowed_source_types"),
            "purposes": _union_nested(requirements, "web", "purposes"),
        },
        "draftability": max(
            (str(item.get("draftability") or "can_draft") for item in requirements),
            key=lambda value: DRAFT_ORDER[value],
            default="can_draft",
        ),
    }


def _requirements_from_node(node: dict[str, Any], score_titles: dict[str, str]) -> dict[str, Any]:
    response_ids = [
        str(item.get("response_point_id") or "")
        for item in node.get("response_point_mappings") or []
        if item.get("response_point_id")
    ]
    score_refs = list(node.get("score_item_refs") or [])
    return {
        "response_point_ids": list(dict.fromkeys(response_ids)),
        "score_item_refs": list(dict.fromkeys(score_refs)),
        "required_content_points": [f"围绕“{node.get('title', '')}”完成章节目标"],
        "manual_project_facts": {"need": "none", "fact_types": []},
        "local_kb": {"need": "none", "evidence_types": []},
        "web": {"need": "forbidden", "allowed_source_types": [], "purposes": []},
        "draftability": "can_draft",
    }


def _union(requirements: list[dict[str, Any]], key: str) -> list[str]:
    return list(dict.fromkeys(str(value) for item in requirements for value in item.get(key) or []))


def _union_nested(requirements: list[dict[str, Any]], group: str, key: str) -> list[str]:
    return list(
        dict.fromkeys(
            str(value)
            for item in requirements
            for value in (item.get(group) or {}).get(key) or []
        )
    )


def _max_need(requirements: list[dict[str, Any]], group: str) -> str:
    default = "forbidden" if group == "web" else "none"
    order = NEED_ORDER[group]
    return max(
        (str((item.get(group) or {}).get("need") or default) for item in requirements),
        key=lambda value: order[value],
        default=default,
    )

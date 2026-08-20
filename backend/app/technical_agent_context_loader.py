from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from app.tender_writing_format_analyzer import load_response_outline


SCHEMA_VERSION = "technical_agent_section_context_v0"
PRODUCT_ENTITIES = ("超融合系统", "超融合互联交换机", "超融合节点")


def load_technical_agent_context(
    response_outline: dict[str, Any],
    writing_outline: dict[str, Any],
    *,
    target_chapter_id: str | None = None,
    target_section_id: str | None = None,
    confirmed_project_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if bool(target_chapter_id) == bool(target_section_id):
        raise ValueError("provide exactly one of target_chapter_id or target_section_id")
    target_id = str(target_section_id or target_chapter_id or "")
    target, root, target_path = _find_target(writing_outline.get("chapters") or [], target_id)
    if target is None or root is None:
        raise ValueError(f"writing outline target not found: {target_id}")
    requirements = target.get("writing_requirements") or {}
    response_ids = list(requirements.get("response_point_ids") or [])
    point_index = {
        str(point.get("point_id") or ""): point
        for chapter in response_outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    }
    target_title = str(target.get("title") or "")
    tender_requirements = [
        _minimal_requirement(point_index[point_id], target_title, bool(target_section_id))
        for point_id in response_ids
        if point_id in point_index
    ]
    score_refs = set(requirements.get("score_item_refs") or [])
    score_items = [
        {
            "score_item_id": item.get("score_item_id"),
            "title": item.get("title"),
            "criteria_summary": item.get("criteria_summary"),
            "score": item.get("score"),
            "response_point_id": item.get("response_point_id"),
        }
        for item in writing_outline.get("score_items") or []
        if item.get("score_item_id") in score_refs
    ]
    terminology = _terminology(writing_outline)
    facts = confirmed_project_facts if isinstance(confirmed_project_facts, dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "formal_inputs": {
            "technical_response_outline": "tender_requirement_fact_source",
            "technical_writing_outline": "article_structure_and_writing_plan_source",
            "confirmed_project_facts": "optional",
        },
        "target_selector": {
            "target_chapter_id": target_chapter_id,
            "target_section_id": target_section_id,
            "task_id_required": False,
            "draft_unit_id_required": False,
        },
        "target_outline": _minimal_outline_node(target),
        "tender_requirements": tender_requirements,
        "score_items": score_items,
        "information_requirements": _collect_information_requirements(target, target_path),
        "generation_constraints": {
            "terminology": terminology,
            "background_policy": _background_policy(root, target),
            "parameter_consistency_required": _parameter_consistency_required(target),
            "resolve_response_text_by_id": True,
            "do_not_infer_offered_values_from_tender_minimums": True,
            "internal_draft_unit_optimization": {
                "available_as_optional_internal_advice": True,
                "required_for_context_loading": False,
                "loaded": False,
            },
        },
        "confirmed_project_facts": {
            "available": bool(facts),
            "values": facts,
        },
        "execution_guard": {
            "body_generated": False,
            "local_kb_queried": False,
            "web_research_executed": False,
            "docx_generated": False,
            "reference_only_files_read": False,
        },
    }


def build_technical_agent_context_files(
    response_outline_path: str | Path,
    writing_outline_path: str | Path,
    *,
    output_key: str,
    output_dir: str | Path = "outputs",
    target_chapter_id: str | None = None,
    target_section_id: str | None = None,
    confirmed_project_facts_path: str | Path | None = None,
) -> dict[str, Any]:
    response_path = Path(response_outline_path).expanduser().resolve()
    writing_path = Path(writing_outline_path).expanduser().resolve()
    response = load_response_outline(response_path)
    writing = json.loads(writing_path.read_text(encoding="utf-8-sig"))
    confirmed_facts: dict[str, Any] = {}
    facts_path = None
    if confirmed_project_facts_path:
        facts_path = Path(confirmed_project_facts_path).expanduser().resolve()
        if facts_path.exists():
            confirmed_facts = json.loads(facts_path.read_text(encoding="utf-8-sig"))
    context = load_technical_agent_context(
        response,
        writing,
        target_chapter_id=target_chapter_id,
        target_section_id=target_section_id,
        confirmed_project_facts=confirmed_facts,
    )
    context["source_paths"] = {
        "response_outline_path": str(response_path),
        "writing_outline_path": str(writing_path),
        "confirmed_project_facts_path": str(facts_path) if facts_path else "",
    }
    target_id = str(target_section_id or target_chapter_id or "")
    context_dir = Path(output_dir).expanduser().resolve() / _safe_key(output_key) / "agent_context"
    context_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "agent_context_json": context_dir / f"{_safe_key(target_id)}.json",
        "agent_context_markdown": context_dir / f"{_safe_key(target_id)}.md",
    }
    files["agent_context_json"].write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["agent_context_markdown"].write_text(render_agent_context_markdown(context), encoding="utf-8")
    return {"context": context, "files": {key: str(path) for key, path in files.items()}}


def render_agent_context_markdown(context: dict[str, Any]) -> str:
    target = context["target_outline"]
    lines = [
        f"# 技术写作Agent章节上下文：{target['title']}",
        "",
        f"- 目标节点：{target['chapter_id']}",
        f"- 项目背景策略：{context['generation_constraints']['background_policy']}",
        f"- 参数一致性：{str(context['generation_constraints']['parameter_consistency_required']).lower()}",
        "- 本文件是最小章节上下文，不包含正文，也未执行RAG或联网。",
        "",
        "## 目标目录",
        "",
    ]
    lines.extend(_outline_markdown(target, 0))
    lines.extend(["", "## 关联招标要求", ""])
    for item in context["tender_requirements"]:
        summary = item.get("requirement_text") or item.get("table_access", {}).get("instruction") or "按响应点ID解析"
        lines.append(f"- {item['response_point_id']} {item.get('heading') or ''}：{summary}")
    lines.extend(["", "## 评分项", ""])
    for item in context["score_items"]:
        lines.append(f"- {item['score_item_id']} {item['title']}：{item['criteria_summary']}")
    lines.extend(["", "## 信息需求", ""])
    for group in ("manual_project_facts", "local_kb", "web"):
        lines.append(f"### {group}")
        lines.append("")
        for item in context["information_requirements"][group]:
            values = item.get("fact_types") or item.get("evidence_types") or item.get("purposes") or []
            lines.append(f"- {item['node_id']} {item['need']}：{'、'.join(values) or '无'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _find_target(
    chapters: list[dict[str, Any]],
    target_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    for chapter in chapters:
        if chapter.get("chapter_id") == target_id:
            return chapter, chapter, [str(chapter.get("title") or "")]
        found, path = _find_in_children(chapter.get("children") or [], target_id, [str(chapter.get("title") or "")])
        if found is not None:
            return found, chapter, path
    return None, None, []


def _find_in_children(
    children: list[dict[str, Any]],
    target_id: str,
    parent_path: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    for child in children:
        path = [*parent_path, str(child.get("title") or "")]
        if child.get("chapter_id") == target_id:
            return child, path
        found, found_path = _find_in_children(child.get("children") or [], target_id, path)
        if found is not None:
            return found, found_path
    return None, []


def _minimal_outline_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_id": node.get("chapter_id"),
        "title": node.get("title"),
        "module_ids": list(node.get("module_ids") or []),
        "chapter_type": node.get("chapter_type", "narrative"),
        "writing_requirements": copy.deepcopy(node.get("writing_requirements") or {}),
        "children": [_minimal_outline_node(child) for child in node.get("children") or []],
    }


def _minimal_requirement(point: dict[str, Any], target_title: str, is_section: bool) -> dict[str, Any]:
    point_id = str(point.get("point_id") or "")
    content_type = str(point.get("content_type") or "")
    table = point.get("table") if isinstance(point.get("table"), dict) else None
    result = {
        "response_point_id": point_id,
        "heading": str(point.get("original_heading") or ""),
        "mandatory_marker": str(point.get("mandatory_marker") or ""),
        "content_type": content_type,
        "source_refs": [_minimal_source_ref(item) for item in point.get("source_refs") or []],
    }
    if table is None:
        result["requirement_text"] = str(point.get("original_text") or "")
        return result
    rows = list(table.get("rows") or [])
    entity = next((item for item in PRODUCT_ENTITIES if item in target_title), "")
    include_form = is_section and "点对点应答表" in target_title and len(rows) <= 50
    if entity:
        excerpt = _product_table_excerpt(rows, entity)
        result["table_excerpt"] = {
            "entity": entity,
            "row_count": len(excerpt),
            "rows": excerpt,
        }
    elif include_form:
        result["table_excerpt"] = {"entity": "", "row_count": len(rows), "rows": rows}
    else:
        result["table_access"] = {
            "row_count": len(rows),
            "column_count": table.get("column_count"),
            "instruction": f"按response_point_id={point_id}从technical_response_outline.json按需读取表格；当前最小上下文不内嵌整表。",
        }
    return result


def _product_table_excerpt(rows: list[dict[str, Any]], entity: str) -> list[dict[str, Any]]:
    start = None
    end = len(rows)
    for index, row in enumerate(rows):
        cells = {int(cell.get("column") or 0): str(cell.get("text") or "") for cell in row.get("cells") or []}
        name = re.sub(r"^[★▲■*＊\s]+", "", cells.get(2, "")).strip()
        if start is None and name == entity:
            start = index
            continue
        if start is not None and cells.get(1, "").strip().isdigit() and name in PRODUCT_ENTITIES and name != entity:
            end = index
            break
    if start is None:
        return []
    return copy.deepcopy(rows[start:end])


def _minimal_source_ref(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": source.get("file_id"),
        "file_name": source.get("file_name"),
        "clause_path": source.get("clause_path") or [],
        "page_start": source.get("page_start"),
        "page_end": source.get("page_end"),
        "row_or_cell": source.get("row_or_cell"),
    }


def _collect_information_requirements(target: dict[str, Any], target_path: list[str]) -> dict[str, list[dict[str, Any]]]:
    result = {"manual_project_facts": [], "local_kb": [], "web": []}
    _collect_node_information(target, target_path[:-1], result)
    return result


def _collect_node_information(
    node: dict[str, Any],
    parent_path: list[str],
    result: dict[str, list[dict[str, Any]]],
) -> None:
    path = [*parent_path, str(node.get("title") or "")]
    requirements = node.get("writing_requirements") or {}
    manual = requirements.get("manual_project_facts") or {}
    local = requirements.get("local_kb") or {}
    web = requirements.get("web") or {}
    if manual.get("need") != "none":
        result["manual_project_facts"].append(
            {"node_id": node.get("chapter_id"), "target_path": path, "need": manual.get("need"), "fact_types": manual.get("fact_types") or []}
        )
    if local.get("need") != "none":
        result["local_kb"].append(
            {"node_id": node.get("chapter_id"), "target_path": path, "need": local.get("need"), "evidence_types": local.get("evidence_types") or []}
        )
    if web.get("need") != "forbidden":
        result["web"].append(
            {
                "node_id": node.get("chapter_id"),
                "target_path": path,
                "need": web.get("need"),
                "allowed_source_types": web.get("allowed_source_types") or [],
                "purposes": web.get("purposes") or [],
            }
        )
    for child in node.get("children") or []:
        _collect_node_information(child, path, result)


def _background_policy(root: dict[str, Any], target: dict[str, Any]) -> str:
    if target.get("title") == "项目背景" or (root.get("chapter_id") == "WC-001" and target is root):
        return "full"
    if target is root:
        return "brief_reference"
    return "forbidden"


def _parameter_consistency_required(target: dict[str, Any]) -> bool:
    serialized = json.dumps(target.get("writing_requirements") or {}, ensure_ascii=False)
    title = str(target.get("title") or "")
    return any(
        term in f"{title}\n{serialized}"
        for term in ("超融合", "参数", "点对点", "actual_product_configuration", "actual_technical_parameters")
    )


def _terminology(writing_outline: dict[str, Any]) -> list[str]:
    serialized = json.dumps(writing_outline.get("chapters") or [], ensure_ascii=False)
    terms = [term for term in (*PRODUCT_ENTITIES, "技术要求点对点应答表", "绿色供应链管理") if term in serialized]
    terms.append("不得引入未经确认的产品简称、品牌简称或技术别名")
    return terms


def _outline_markdown(node: dict[str, Any], depth: int) -> list[str]:
    lines = [f"{'  ' * depth}- {node['chapter_id']} {node['title']}"]
    for child in node.get("children") or []:
        lines.extend(_outline_markdown(child, depth + 1))
    return lines


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("value must contain a safe path component")
    return cleaned

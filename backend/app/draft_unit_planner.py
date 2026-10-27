from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "technical_draft_units_v0"
STATUS_ORDER = {"can_draft": 0, "can_draft_with_placeholders": 1, "cannot_finalize": 2}


def plan_draft_units(
    writing_outline: dict[str, Any],
    writing_tasks: dict[str, Any],
    information_requirements: dict[str, Any],
) -> dict[str, Any]:
    info_by_task = {
        str(item.get("task_id") or ""): item
        for item in information_requirements.get("tasks") or []
    }
    task_by_chapter: dict[str, list[dict[str, Any]]] = {}
    for task in writing_tasks.get("tasks") or []:
        task_by_chapter.setdefault(str(task.get("chapter_id") or ""), []).append(task)
    content_locations = _build_content_locations(writing_tasks.get("tasks") or [])
    terminology = _terminology_constraints(writing_outline)

    grouped: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    for chapter in writing_outline.get("chapters") or []:
        chapter_id = str(chapter.get("chapter_id") or "")
        chapter_tasks = task_by_chapter.get(chapter_id, [])
        current_key = ""
        current_label = ""
        current_tasks: list[dict[str, Any]] = []
        for index, task in enumerate(chapter_tasks):
            key, label = _semantic_group(chapter_id, task, index)
            if current_tasks and (key != current_key or not _unit_compatible(current_tasks, task, info_by_task)):
                grouped.append((chapter, current_label, current_tasks))
                current_tasks = []
            current_key, current_label = key, label
            current_tasks.append(task)
        if current_tasks:
            grouped.append((chapter, current_label, current_tasks))

    units = []
    first_unit_by_chapter: set[str] = set()
    for index, (chapter, label, tasks) in enumerate(grouped, start=1):
        chapter_id = str(chapter.get("chapter_id") or "")
        infos = [info_by_task[task["task_id"]] for task in tasks]
        paths = [list(task.get("target_path") or []) for task in tasks]
        response_ids = sorted(
            {
                str(mapping.get("response_point_id") or "")
                for task in tasks
                for mapping in task.get("response_point_mappings") or []
                if mapping.get("response_point_id")
            }
        )
        score_refs = sorted({ref for task in tasks for ref in task.get("score_item_refs") or []})
        modes = {str(task.get("output_constraints", {}).get("content_type") or "narrative") for task in tasks}
        content_mode = next(iter(modes)) if len(modes) == 1 else "mixed"
        shared_manual = _shared_values(
            [info["information_requirements"]["manual_project_fact"]["fact_types"] for info in infos]
        )
        shared_local = _shared_values(
            [info["information_requirements"]["local_kb"]["evidence_types"] for info in infos]
        )
        shared_web = _shared_values(
            [info["information_requirements"]["web"]["purposes"] for info in infos]
        )
        status = max(
            (info["draftability"]["status"] for info in infos),
            key=lambda value: STATUS_ORDER[value],
        )
        if (
            "MODULE-PROJECT-UNDERSTANDING" in {module for task in tasks for module in task.get("module_ids") or []}
            and chapter_id not in first_unit_by_chapter
        ):
            background_policy = "full"
        elif chapter_id not in first_unit_by_chapter:
            background_policy = "brief_reference"
        else:
            background_policy = "forbidden"
        first_unit_by_chapter.add(chapter_id)
        unit_paths = {tuple(path) for path in paths}
        unit_response_locations = {
            response_id: content_locations.get(response_id, {}) for response_id in response_ids
        }
        parameter_related = any(
            any(term in path[-1] for term in ("参数", "点对点", "可靠", "兼容"))
            for path in paths
        ) or any(item in shared_manual for item in ("actual_product_configuration", "actual_technical_parameters"))
        units.append(
            {
                "draft_unit_id": f"DU-{index:03d}",
                "title": label,
                "target_paths": paths,
                "chapter_id": chapter_id,
                "task_ids": [task["task_id"] for task in tasks],
                "response_point_ids": response_ids,
                "score_item_refs": score_refs,
                "content_mode": content_mode,
                "shared_manual_fact_types": shared_manual,
                "shared_local_evidence_types": shared_local,
                "shared_web_purposes": shared_web,
                "project_background_policy": background_policy,
                "generation_constraints": {
                    "project_background_full_introduction_allowed": background_policy == "full",
                    "avoid_repeating_topics": sorted(
                        response_id
                        for response_id, locations in unit_response_locations.items()
                        if len(locations.get("all_locations", [])) > 1
                    ),
                    "primary_content_locations": [
                        locations["primary_content_location"]
                        for locations in unit_response_locations.values()
                        if locations.get("primary_content_location")
                        and tuple(locations["primary_content_location"]["target_path"]) in unit_paths
                    ],
                    "supporting_reference_locations": [
                        location
                        for locations in unit_response_locations.values()
                        for location in locations.get("supporting_reference_locations", [])
                        if tuple(location["target_path"]) in unit_paths
                    ],
                    "table_response_locations": [
                        location
                        for locations in unit_response_locations.values()
                        for location in locations.get("table_response_locations", [])
                        if tuple(location["target_path"]) in unit_paths
                    ],
                    "terminology_constraints": terminology,
                    "parameter_consistency_required": parameter_related,
                    "transition_required": len(tasks) > 1 and content_mode in {"narrative", "mixed"},
                },
                "draftability": {
                    "status": status,
                    "reason": _unit_draftability_reason(status, infos),
                },
            }
        )

    all_task_ids = [str(task.get("task_id") or "") for task in writing_tasks.get("tasks") or []]
    planned_task_ids = [task_id for unit in units for task_id in unit["task_ids"]]
    counts_by_chapter = {
        str(chapter.get("chapter_id") or ""): sum(
            unit["chapter_id"] == chapter.get("chapter_id") for unit in units
        )
        for chapter in writing_outline.get("chapters") or []
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "separation_guards": {
            "writing_outline_modified": False,
            "atomic_tasks_modified_or_removed": False,
            "draft_units_are_directory_nodes": False,
            "body_generated": False,
            "docx_generated": False,
            "local_kb_queried": False,
            "web_research_executed": False,
            "reference_only_files_read": False,
        },
        "directory_snapshot": [
            {
                "chapter_id": chapter.get("chapter_id"),
                "title": chapter.get("title"),
                "child_titles": [child.get("title") for child in chapter.get("children") or []],
            }
            for chapter in writing_outline.get("chapters") or []
        ],
        "terminology_constraints": terminology,
        "content_location_plan": content_locations,
        "draft_units": units,
        "coverage": {
            "all_task_ids": all_task_ids,
            "planned_task_ids": planned_task_ids,
            "unplanned_task_ids": sorted(set(all_task_ids) - set(planned_task_ids)),
            "duplicate_task_ids": sorted(
                task_id for task_id in set(planned_task_ids) if planned_task_ids.count(task_id) > 1
            ),
            "all_tasks_planned_once": sorted(all_task_ids) == sorted(planned_task_ids),
        },
        "summary": {
            "draft_unit_count": len(units),
            "task_count": len(all_task_ids),
            "draft_units_by_chapter": counts_by_chapter,
            "draftability": _count_status(units),
        },
    }


def build_draft_unit_files(
    writing_outline_path: str | Path,
    writing_tasks_path: str | Path,
    information_requirements_path: str | Path,
    *,
    output_key: str,
    output_dir: str | Path = "outputs",
) -> dict[str, Any]:
    paths = {
        "writing": Path(writing_outline_path).expanduser().resolve(),
        "tasks": Path(writing_tasks_path).expanduser().resolve(),
        "information": Path(information_requirements_path).expanduser().resolve(),
    }
    hashes_before = {key: _sha256(path) for key, path in paths.items()}
    writing = json.loads(paths["writing"].read_text(encoding="utf-8-sig"))
    tasks = json.loads(paths["tasks"].read_text(encoding="utf-8-sig"))
    information = json.loads(paths["information"].read_text(encoding="utf-8-sig"))
    result = plan_draft_units(writing, tasks, information)
    result["sources"] = {key + "_path": str(path) for key, path in paths.items()}
    task_dir = Path(output_dir).expanduser().resolve() / _safe_key(output_key) / "writing" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "draft_units_json": task_dir / "draft_units.json",
        "draft_units_markdown": task_dir / "draft_units.md",
    }
    files["draft_units_json"].write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["draft_units_markdown"].write_text(render_draft_units_markdown(result), encoding="utf-8")
    if any(value != _sha256(paths[key]) for key, value in hashes_before.items()):
        raise RuntimeError("a draft-unit planning source file changed during processing")
    return {"draft_units": result, "files": {key: str(path) for key, path in files.items()}}


def render_draft_units_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 正文生成单元规划",
        "",
        "正文生成单元只规划后续调用边界，不改变投标文件目录，也不包含正文。",
        "",
    ]
    current_chapter = ""
    chapter_titles = {item["chapter_id"]: item["title"] for item in result["directory_snapshot"]}
    for unit in result.get("draft_units") or []:
        if unit["chapter_id"] != current_chapter:
            lines.extend([f"## {chapter_titles[unit['chapter_id']]}", ""])
            current_chapter = unit["chapter_id"]
        lines.extend(
            [
                f"### {unit['draft_unit_id']} {unit['title']}",
                "",
                f"- 原子任务：{'、'.join(unit['task_ids'])}",
                "- 保留标题：" + "；".join(" / ".join(path) for path in unit["target_paths"]),
                f"- 内容模式：{unit['content_mode']}",
                f"- 项目背景策略：{unit['project_background_policy']}",
                f"- 当前可生成程度：{unit['draftability']['status']}",
                f"- 参数一致性：{str(unit['generation_constraints']['parameter_consistency_required']).lower()}",
                f"- 防重复响应点：{'、'.join(unit['generation_constraints']['avoid_repeating_topics']) or '无'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _semantic_group(chapter_id: str, task: dict[str, Any], index: int) -> tuple[str, str]:
    title = str((task.get("target_path") or [""])[-1])
    modules = {str(value) for value in task.get("module_ids") or []}
    if modules & {"MODULE-PARAMETER-RESPONSE", "MODULE-TECHNICAL-DEVIATION"}:
        return f"form:{title}", title
    if "MODULE-EVIDENCE-APPENDIX" in modules:
        return f"appendix:{title}", title
    product = _product_entity(task)
    if product:
        return f"product:{product}", product
    if "MODULE-TRAINING" in modules:
        return ("training-design", "培训方案") if index < 5 else ("training-delivery", "培训实施与保障")
    if "MODULE-SERVICE-WARRANTY" in modules:
        return ("service-system", "服务体系与响应机制") if index < 5 else ("service-operation", "运行维护与服务保障")
    if "MODULE-PROJECT-UNDERSTANDING" in modules:
        return f"understanding:{index // 4}", str((task.get("target_path") or ["项目需求理解"])[0])
    if modules & {"MODULE-OVERALL-SOLUTION", "MODULE-DETAILED-SOLUTION"}:
        return f"solution:{index // 4}", str((task.get("target_path") or ["技术方案"])[0])
    if modules & {"MODULE-IMPLEMENTATION", "MODULE-PROJECT-MANAGEMENT", "MODULE-QUALITY-ACCEPTANCE", "MODULE-RISK-CONTROL"}:
        return f"delivery:{index // 4}", str((task.get("target_path") or ["实施与保障"])[0])
    return f"generic:{chapter_id}:{index // 4}", str((task.get("target_path") or ["正文生成单元"])[0])


def _unit_compatible(
    current_tasks: list[dict[str, Any]],
    candidate: dict[str, Any],
    info_by_task: dict[str, dict[str, Any]],
) -> bool:
    if len(current_tasks) >= 6:
        return False
    current_modes = {
        str(task.get("output_constraints", {}).get("content_type") or "narrative") for task in current_tasks
    }
    candidate_mode = str(candidate.get("output_constraints", {}).get("content_type") or "narrative")
    if candidate_mode not in current_modes and not (current_modes | {candidate_mode} <= {"narrative", "mixed"}):
        return False
    current_products = {_product_entity(task) for task in current_tasks} - {""}
    candidate_product = _product_entity(candidate)
    if candidate_product and (not current_products or current_products != {candidate_product}):
        return False
    if current_products and not candidate_product:
        return False
    if candidate_mode in {"table", "appendix"}:
        return False
    return True


def _product_entity(task: dict[str, Any]) -> str:
    title = str((task.get("target_path") or [""])[-1])
    if "MODULE-DETAILED-SOLUTION" in set(task.get("module_ids") or []) and title.endswith("技术方案"):
        return title
    return ""


def _build_content_locations(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    by_response: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        for mapping in task.get("response_point_mappings") or []:
            point_id = str(mapping.get("response_point_id") or "")
            by_response.setdefault(point_id, []).append(
                {
                    "task_id": task.get("task_id"),
                    "target_path": list(task.get("target_path") or []),
                    "mapping_role": mapping.get("mapping_role"),
                }
            )
    result = {}
    for point_id, locations in by_response.items():
        primary = next((item for item in locations if item["mapping_role"] == "primary"), None)
        if primary is None:
            primary = next((item for item in locations if item["mapping_role"] == "supporting"), None)
        result[point_id] = {
            "primary_content_location": primary,
            "supporting_reference_locations": [
                item for item in locations if item["mapping_role"] == "supporting" and item is not primary
            ],
            "table_response_locations": [item for item in locations if item["mapping_role"] == "table_response"],
            "all_locations": locations,
        }
    return result


def _terminology_constraints(writing_outline: dict[str, Any]) -> list[str]:
    serialized = json.dumps(writing_outline.get("chapters") or [], ensure_ascii=False)
    terms = [
        term
        for term in (
            "超融合系统",
            "超融合互联交换机",
            "超融合节点",
            "技术要求点对点应答表",
            "绿色供应链管理",
        )
        if term in serialized
    ]
    terms.append("不得自行引入未经确认的产品简称、品牌简称或技术别名")
    return terms


def _shared_values(value_lists: list[list[str]]) -> list[str]:
    if not value_lists or any(not values for values in value_lists):
        return []
    return sorted(set.intersection(*(set(values) for values in value_lists)))


def _unit_draftability_reason(status: str, infos: list[dict[str, Any]]) -> str:
    if status == "cannot_finalize":
        return "单元内至少一个原子任务缺少最终版本必需的项目事实或本地证据；仍可规划结构和占位。"
    if status == "can_draft_with_placeholders":
        return "可形成连贯框架，项目化事实或证据引用位置需使用明确占位符。"
    return "单元内任务当前均可依据招标文件事实形成初稿。"


def _count_status(units: list[dict[str, Any]]) -> dict[str, int]:
    result = {key: 0 for key in STATUS_ORDER}
    for unit in units:
        result[unit["draftability"]["status"]] += 1
    return result


def _safe_key(value: str) -> str:
    import re

    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("output_key must contain a safe path component")
    return cleaned


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

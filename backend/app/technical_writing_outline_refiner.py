from __future__ import annotations

import copy
from typing import Any


MODULE_PLACEMENT_RULES = {
    "MODULE-PROJECT-UNDERSTANDING": ("triggered", "top_level", None),
    "MODULE-OVERALL-SOLUTION": ("triggered", "top_level", None),
    "MODULE-DETAILED-SOLUTION": ("triggered", "nested", "MODULE-OVERALL-SOLUTION"),
    "MODULE-IMPLEMENTATION": ("triggered", "top_level", None),
    "MODULE-PROJECT-MANAGEMENT": ("supporting", "nested", "MODULE-IMPLEMENTATION"),
    "MODULE-QUALITY-ACCEPTANCE": ("triggered", "nested", "MODULE-IMPLEMENTATION"),
    "MODULE-TRAINING": ("triggered", "top_level", None),
    "MODULE-SERVICE-WARRANTY": ("triggered", "top_level", None),
    "MODULE-RISK-CONTROL": ("supporting", "nested", "MODULE-IMPLEMENTATION"),
    "MODULE-SPECIAL-TOPICS": ("triggered", "top_level", None),
    "MODULE-PARAMETER-RESPONSE": ("required", "form", None),
    "MODULE-TECHNICAL-DEVIATION": ("conditional", "form", "MODULE-PARAMETER-RESPONSE"),
    "MODULE-EVIDENCE-APPENDIX": ("required", "appendix", None),
}


def refine_writing_outline(
    original_chapters: list[dict[str, Any]],
    format_analysis: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Converge module-oriented chapters into a score- and tender-oriented structure."""
    by_module = {
        module_id: chapter
        for chapter in original_chapters
        for module_id in chapter.get("company_outline_node_ids") or []
        if str(module_id).startswith("MODULE-")
    }
    required_forms = {str(item.get("title") or "") for item in format_analysis.get("required_forms") or []}
    independent_deviation = "技术偏差表" in required_forms

    groups = [
        {
            "title": "需求理解及分析",
            "modules": ["MODULE-PROJECT-UNDERSTANDING"],
            "mapping_type": "renamed",
            "source_type": "主观评分项/公司通用",
            "chapter_type": "narrative",
            "reason": "主观评分项明确评价需求理解及分析，作为独立一级主章节。",
            "children": [
                ("项目背景", "项目背景"),
                ("现状及建设基础", "项目现状及建设基础"),
                ("应用环境", None),
                ("功能需求", None),
                ("性能需求", None),
                ("统一运维需求", None),
                ("实施要求", None),
                ("项目重点与难点", "项目重点与难点分析"),
                ("建设目标", "项目建设目标"),
            ],
        },
        {
            "title": "方案完整性及系统架构设计",
            "modules": ["MODULE-OVERALL-SOLUTION", "MODULE-DETAILED-SOLUTION"],
            "mapping_type": "merged",
            "source_type": "主观评分项/公司通用归并",
            "chapter_type": "mixed",
            "reason": "主观评分项将方案完整性和系统架构作为同一评价主题，总体方案与详细方案在此归并。",
            "children": [
                ("总体设计原则", "总体设计原则"),
                ("总体技术路线", "总体技术路线"),
                ("总体方案架构", "方案总体架构"),
                ("部署架构", None),
                ("统一运维架构", None),
                ("主要功能设计", "主要功能设计"),
                ("可靠性设计", "性能与可靠性设计"),
                ("安全设计", "安全设计"),
                ("兼容与扩展设计", "兼容性与扩展性设计"),
                ("超融合系统详细技术方案", "超融合系统详细技术方案"),
                ("超融合互联交换机技术方案", "超融合互联交换机技术方案"),
                ("超融合节点技术方案", "超融合节点技术方案"),
            ],
        },
        {
            "title": "实施部署方案",
            "modules": [
                "MODULE-IMPLEMENTATION",
                "MODULE-PROJECT-MANAGEMENT",
                "MODULE-QUALITY-ACCEPTANCE",
                "MODULE-RISK-CONTROL",
            ],
            "mapping_type": "merged",
            "source_type": "主观评分项/公司通用归并",
            "chapter_type": "mixed",
            "reason": "实施评分主题统领项目团队、质量验收及风险保障，支持模块下沉为实施子章节。",
            "children": [
                ("实施总体规划", "实施总体安排"),
                ("项目建设技术方案", "实施范围"),
                ("项目设计实施周期", "进度计划与里程碑"),
                ("项目组织架构", "项目组织架构"),
                ("项目负责人及主要人员", "主要技术人员"),
                ("安装部署", "安装部署方案"),
                ("测试与试运行", "系统测试与试运行"),
                ("质量控制", "质量控制流程"),
                ("项目验收", "验收流程"),
                ("交付成果", "交付成果"),
                ("风险控制与应急保障", "风险控制与应急保障"),
            ],
        },
        _preserved_group("培训方案", "MODULE-TRAINING", "narrative", "主观评分项要求独立培训方案。"),
        _preserved_group(
            "售后服务与运行保障方案",
            "MODULE-SERVICE-WARRANTY",
            "narrative",
            "服务、保修和响应要求构成独立履约主题。",
        ),
        _preserved_group(
            "绿色供应链管理方案",
            "MODULE-SPECIAL-TOPICS",
            "narrative",
            "技术要求和主观评分项均明确绿色供应链主题。",
        ),
        _preserved_group(
            "技术参数及点对点应答",
            "MODULE-PARAMETER-RESPONSE",
            "table",
            "招标文件明确要求独立填写技术要求点对点应答表。",
            mapping_type="form_only",
            source_type="强制独立表格",
        ),
        _preserved_group(
            "技术证明材料及附件索引",
            "MODULE-EVIDENCE-APPENDIX",
            "appendix",
            "技术资料、产品证明、资质和评分支撑材料集中作为附件索引。",
            source_type="公司必要附件",
        ),
    ]
    if independent_deviation:
        groups.insert(
            -1,
            _preserved_group(
                "技术偏差表",
                "MODULE-TECHNICAL-DEVIATION",
                "table",
                "招标文件明确提供独立技术偏差表，按强制表格单独生成。",
                mapping_type="form_only",
                source_type="强制独立表格",
            ),
        )

    refined: list[dict[str, Any]] = []
    top_id_by_module: dict[str, str] = {}
    for index, group in enumerate(groups, start=1):
        source_roots = [by_module[module_id] for module_id in group["modules"] if module_id in by_module]
        chapter = _make_refined_root(f"WC-{index:03d}", group, source_roots)
        if group.get("preserve_children"):
            for child_index, source_child in enumerate(source_roots[0].get("children") or [], start=1):
                child = copy.deepcopy(source_child)
                child["chapter_id"] = f"WC-{index:03d}-{child_index:02d}"
                child["module_ids"] = list(group["modules"])
                chapter["children"].append(child)
        else:
            for child_index, (title, source_title) in enumerate(group["children"], start=1):
                source_node = _find_descendant(source_roots, source_title) if source_title else None
                child = _make_refined_child(
                    f"WC-{index:03d}-{child_index:02d}",
                    title,
                    list(group["modules"]),
                    source_node,
                    chapter,
                )
                chapter["children"].append(child)
        refined.append(chapter)
        for module_id in group["modules"]:
            top_id_by_module[module_id] = chapter["chapter_id"]

    if not independent_deviation and "MODULE-TECHNICAL-DEVIATION" in by_module:
        parameter = next(
            chapter for chapter in refined if "MODULE-PARAMETER-RESPONSE" in chapter.get("module_ids", [])
        )
        _merge_node_metadata(parameter, [by_module["MODULE-TECHNICAL-DEVIATION"]])

    module_adaptations = _build_module_adaptations(
        by_module,
        top_id_by_module,
        independent_deviation=independent_deviation,
    )
    report = {
        "original_top_level_count": len(original_chapters),
        "refined_top_level_count": len(refined),
        "top_level_chapters": [
            {
                "chapter_id": chapter["chapter_id"],
                "title": chapter["title"],
                "module_ids": chapter["module_ids"],
            }
            for chapter in refined
        ],
        "nested_modules": [
            item for item in module_adaptations if item["placement_role"] == "nested"
        ],
        "conditional_modules": [
            item for item in module_adaptations if item["activation_status"] == "conditional"
        ],
        "excluded_modules": [
            item for item in module_adaptations if item["activation_status"] == "excluded"
        ],
        "module_placement_map": module_adaptations,
    }
    return refined, module_adaptations, report


def _preserved_group(
    title: str,
    module_id: str,
    chapter_type: str,
    reason: str,
    *,
    mapping_type: str = "direct",
    source_type: str = "主观评分项/公司通用",
) -> dict[str, Any]:
    return {
        "title": title,
        "modules": [module_id],
        "mapping_type": mapping_type,
        "source_type": source_type,
        "chapter_type": chapter_type,
        "reason": reason,
        "preserve_children": True,
        "children": [],
    }


def _make_refined_root(
    chapter_id: str,
    group: dict[str, Any],
    source_roots: list[dict[str, Any]],
) -> dict[str, Any]:
    node = {
        "chapter_id": chapter_id,
        "title": group["title"],
        "mapping_type": group["mapping_type"],
        "source_type": group["source_type"],
        "company_outline_node_ids": list(group["modules"]),
        "module_ids": list(group["modules"]),
        "company_outline_path": " + ".join(
            str(root.get("original_company_title") or root.get("title") or "") for root in source_roots
        ),
        "tender_structure_refs": [],
        "response_point_mappings": [],
        "display_block_ids": [],
        "score_item_refs": [],
        "triggered_by": [],
        "adaptation_reason": group["reason"],
        "chapter_type": group["chapter_type"],
        "children": [],
    }
    _merge_node_metadata(node, source_roots)
    return node


def _make_refined_child(
    chapter_id: str,
    title: str,
    module_ids: list[str],
    source_node: dict[str, Any] | None,
    parent: dict[str, Any],
) -> dict[str, Any]:
    if source_node:
        node = copy.deepcopy(source_node)
        original_title = str(source_node.get("title") or "")
        node["mapping_type"] = "direct" if title == original_title else "renamed"
        node["source_type"] = "公司通用" if title == original_title else "公司通用/评分项细化"
        node["adaptation_reason"] = (
            "继承公司通用大纲小节。"
            if title == original_title
            else f"将公司小节“{original_title}”调整为项目评分主题下的目录标题。"
        )
        node["original_company_title"] = original_title
    else:
        node = {
            "mapping_type": "inserted",
            "source_type": "评分项结构补充",
            "company_outline_node_ids": list(module_ids),
            "company_outline_path": parent["title"],
            "tender_structure_refs": [],
            "response_point_mappings": [
                copy.deepcopy(item)
                for item in parent.get("response_point_mappings") or []
                if item.get("mapping_role") == "supporting"
            ],
            "display_block_ids": list(parent.get("display_block_ids") or []),
            "score_item_refs": list(parent.get("score_item_refs") or []),
            "triggered_by": list(parent.get("triggered_by") or []),
            "adaptation_reason": "评分标准明确要求覆盖该内容，但公司默认小节未单列，作为子章节补充。",
            "children": [],
        }
    node["chapter_id"] = chapter_id
    node["title"] = title
    node["module_ids"] = module_ids
    node["chapter_type"] = "narrative"
    node["children"] = []
    return node


def _merge_node_metadata(target: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    for source in sources:
        for node in _iter_nodes([source]):
            for key in ("tender_structure_refs", "display_block_ids", "score_item_refs", "triggered_by"):
                for value in node.get(key) or []:
                    if value not in target[key]:
                        target[key].append(copy.deepcopy(value))
            for mapping in node.get("response_point_mappings") or []:
                if mapping not in target["response_point_mappings"]:
                    target["response_point_mappings"].append(copy.deepcopy(mapping))


def _find_descendant(roots: list[dict[str, Any]], title: str | None) -> dict[str, Any] | None:
    if not title:
        return None
    return next((node for node in _iter_nodes(roots) if node.get("title") == title), None)


def _iter_nodes(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        yield from _iter_nodes(list(node.get("children") or []))


def _build_module_adaptations(
    by_module: dict[str, dict[str, Any]],
    top_id_by_module: dict[str, str],
    *,
    independent_deviation: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for module_id in by_module:
        status, role, parent_module = MODULE_PLACEMENT_RULES[module_id]
        if module_id == "MODULE-TECHNICAL-DEVIATION" and independent_deviation:
            status, role, parent_module = "required", "form", None
        parent_id = top_id_by_module.get(parent_module) if parent_module else None
        if role in {"top_level", "form", "appendix"} and module_id in top_id_by_module:
            placement_reason = "形成项目专属一级章节。"
        elif status == "conditional":
            placement_reason = "仅有点对点应答表的偏离说明列，不重复生成独立技术偏差表。"
        else:
            placement_reason = f"作为支持模块嵌入{parent_module or '相关主章节'}。"
        activation_reason = {
            "required": "招标强制表格或必要附件要求激活。",
            "triggered": "技术要求或主观评分项触发。",
            "supporting": "用于补充主章节完整性，不独立占用一级目录。",
            "conditional": "当前未发现独立技术偏差表或已确认偏离事实。",
            "excluded": "当前项目不适用。",
        }[status]
        result.append(
            {
                "module_id": module_id,
                "activation_status": status,
                "placement_role": role,
                "parent_chapter_id": parent_id,
                "target_chapter_id": top_id_by_module.get(module_id) or parent_id,
                "activation_reasons": [activation_reason],
                "placement_reasons": [placement_reason],
            }
        )
    return result

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.company_writing_outline import (
    load_company_outline,
    normalize_company_outline,
    validate_company_outline,
)
from app.tender_writing_format_analyzer import (
    analyze_tender_writing_format,
    load_response_outline,
)
from app.technical_writing_outline_refiner import refine_writing_outline


SCHEMA_VERSION = "technical_writing_outline_v0"


def adapt_technical_writing_outline(
    company_outline: dict[str, Any],
    response_outline: dict[str, Any],
    *,
    company_source: str = "",
    response_source: str = "",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    validation = validate_company_outline(company_outline)
    if not validation["valid"]:
        raise ValueError("invalid company outline: " + "; ".join(validation["errors"]))
    normalized = normalize_company_outline(company_outline)
    format_analysis = analyze_tender_writing_format(response_outline)
    score_items = extract_score_items(response_outline)
    product_names = extract_procurement_targets(response_outline)
    project_type = infer_project_type(response_outline, product_names)
    source_modules = _select_company_modules(
        normalized,
        project_type,
        response_outline=response_outline,
        score_items=score_items,
        format_analysis=format_analysis,
    )

    chapters, module_chapters = _build_company_skeleton(source_modules, score_items, product_names)
    point_context = _build_point_context(response_outline)
    score_targets = _apply_score_items(score_items, module_chapters, point_context)
    _map_response_points(response_outline, module_chapters, point_context, score_targets)
    _sort_and_dedupe_mappings(chapters)
    chapters, module_adaptations, refinement = refine_writing_outline(chapters, format_analysis)
    _sort_and_dedupe_mappings(chapters)

    all_ids = sorted(point_context)
    mapped_ids = sorted(_collect_mapped_point_ids(chapters))
    unmapped_ids = sorted(set(all_ids) - set(mapped_ids))
    mandatory_ids = sorted(
        point_id
        for point_id, context in point_context.items()
        if "★" in str(context["point"].get("mandatory_marker") or "")
    )
    mandatory_unmapped = sorted(set(mandatory_ids) & set(unmapped_ids))
    unmapped_details = [
        {
            "response_point_id": point_id,
            "reason": "未命中可审计的结构映射规则，需人工指定章节",
        }
        for point_id in unmapped_ids
    ]

    coverage = {
        "all_response_point_ids": all_ids,
        "mapped_response_point_ids": mapped_ids,
        "unmapped_response_point_ids": unmapped_ids,
        "mandatory_response_point_ids": mandatory_ids,
        "mandatory_unmapped_ids": mandatory_unmapped,
        "unmapped_reasons": unmapped_details,
        "response_point_coverage_rate": _rate(len(mapped_ids), len(all_ids)),
        "mandatory_coverage_rate": _rate(len(mandatory_ids) - len(mandatory_unmapped), len(mandatory_ids)),
    }
    outline = {
        "schema_version": SCHEMA_VERSION,
        "format_analysis": format_analysis,
        "source_company_outline": {
            "path": company_source,
            "schema_version": str(company_outline.get("schema_version") or ""),
            "outline_id": str(company_outline.get("outline_id") or ""),
            "normalized_schema_version": normalized["schema_version"],
            "project_type_profile": project_type,
        },
        "source_response_outline": {
            "path": response_source,
            "schema_version": str(response_outline.get("schema_version") or ""),
        },
        "score_items": score_items,
        "module_adaptations": module_adaptations,
        "refinement": refinement,
        "chapters": chapters,
        "coverage": coverage,
        "content_generation": {
            "body_generated": False,
            "facts_populated": False,
            "external_research_used": False,
            "enterprise_knowledge_used": False,
        },
    }
    report = _build_adaptation_report(outline, unmapped_details, validation)
    return outline, report, validation


def build_technical_writing_outline_files(
    company_outline_path: str | Path,
    response_outline_path: str | Path,
    *,
    output_key: str,
    output_dir: str | Path = "outputs",
) -> dict[str, Any]:
    company_path = Path(company_outline_path).expanduser().resolve()
    response_path = Path(response_outline_path).expanduser().resolve()
    company_hash_before = _sha256(company_path)
    response_hash = _sha256(response_path)
    company = load_company_outline(company_path)
    response = load_response_outline(response_path)
    outline, report, validation = adapt_technical_writing_outline(
        company,
        response,
        company_source=str(company_path),
        response_source=str(response_path),
    )
    outline["source_company_outline"]["sha256"] = company_hash_before
    outline["source_response_outline"]["sha256"] = response_hash

    output_root = Path(output_dir).expanduser().resolve() / _safe_key(output_key) / "writing"
    output_root.mkdir(parents=True, exist_ok=True)
    files = {
        "technical_writing_outline_json": output_root / "technical_writing_outline.json",
        "technical_writing_outline_markdown": output_root / "technical_writing_outline.md",
        "outline_adaptation_report": output_root / "outline_adaptation_report.json",
        "outline_refinement_report": output_root / "outline_refinement_report.json",
        "company_outline_validation": output_root / "company_outline_validation.json",
    }
    validation_output = {
        **validation,
        "source_path": str(company_path),
        "source_schema_version": str(company.get("schema_version") or ""),
        "top_level_fields": list(company.keys()),
        "source_structure": "modules with default_sections",
        "stable_root_node_ids": all(bool(item.get("module_id")) for item in company.get("modules") or []),
        "generated_child_node_ids": len(normalize_company_outline(company)["normalization"]["generated_node_ids"]),
        "has_required_by_default": any("required_by_default" in item for item in company.get("modules") or []),
        "has_optional_nodes": any(not item.get("required_by_default", False) for item in company.get("modules") or []),
        "has_project_type_profiles": bool(company.get("project_type_profiles")),
        "duplicate_ids": [error for error in validation["errors"] if error.startswith("duplicate node_id")],
        "empty_titles": [error for error in validation["errors"] if error.startswith("empty title")],
        "orphan_parent_ids": [error for error in validation["errors"] if error.startswith("orphan parent_id")],
        "stable_traversal": validation["valid"],
        "normalized_in_memory": True,
        "normalized_file_written": False,
        "source_sha256_before": company_hash_before,
        "source_sha256_after": _sha256(company_path),
        "source_unchanged": company_hash_before == _sha256(company_path),
    }
    files["technical_writing_outline_json"].write_text(
        json.dumps(outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["technical_writing_outline_markdown"].write_text(
        render_outline_markdown(outline), encoding="utf-8"
    )
    files["outline_adaptation_report"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["outline_refinement_report"].write_text(
        json.dumps(outline["refinement"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files["company_outline_validation"].write_text(
        json.dumps(validation_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if company_hash_before != _sha256(company_path):
        raise RuntimeError("company outline source changed during adaptation")
    return {
        "outline": outline,
        "adaptation_report": report,
        "company_validation": validation_output,
        "files": {key: str(path) for key, path in files.items()},
    }


def extract_score_items(response_outline: dict[str, Any]) -> list[dict[str, Any]]:
    structured = _extract_structured_score_items(response_outline)
    if structured:
        return structured
    result: list[dict[str, Any]] = []
    for chapter in response_outline.get("chapters") or []:
        if "评分" not in str(chapter.get("title") or ""):
            continue
        for point in chapter.get("response_points") or []:
            table = point.get("table") if isinstance(point.get("table"), dict) else {}
            for row, by_column in _rows_with_merged_cell_values(table):
                number = by_column.get(1, "")
                title = by_column.get(2, "")
                criteria = by_column.get(3, "")
                score = by_column.get(4, "")
                if (
                    not number.isdigit()
                    or not title
                    or title in {"评分项", "评审因素"}
                    or not criteria
                    or not score
                ):
                    continue
                row_number = int(row.get("row") or 0)
                point_id = str(point.get("point_id") or "")
                result.append(
                    {
                        "score_item_id": f"SCORE-{point_id}-R{row_number:03d}",
                        "title": title,
                        "criteria_summary": _shorten(criteria, 360),
                        "score": score,
                        "response_point_id": point_id,
                        "source_refs": list(point.get("source_refs") or []),
                    }
                )
    return result


def _extract_structured_score_items(response_outline: dict[str, Any]) -> list[dict[str, Any]]:
    """Consume v2's project-selected score items without reparsing score tables."""
    result: list[dict[str, Any]] = []
    for item in response_outline.get("score_items") or []:
        if not isinstance(item, dict):
            continue
        score_item_id = str(item.get("score_item_id") or item.get("id") or "")
        response_ids = [str(value) for value in item.get("response_point_ids") or [] if value]
        if not score_item_id or not response_ids:
            continue
        result.append(
            {
                "score_item_id": score_item_id,
                "title": str(item.get("title") or item.get("item_name") or score_item_id),
                "criteria_summary": _shorten(str(item.get("criteria_summary") or item.get("score_rule") or ""), 360),
                "score": item.get("score"),
                "response_point_ids": response_ids,
                "source_refs": list(item.get("source_refs") or []),
            }
        )
    return result


def _rows_with_merged_cell_values(
    table: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[int, str]]]:
    """Return table rows with active vertical merge anchors materialized.

    Parsed DOCX tables store a vertically merged cell only on its anchor row.
    Continuation rows therefore need the anchor value inherited according to
    ``rowspan``.  The physical row number remains unchanged and continues to
    define the stable SCORE identity.
    """
    active: dict[int, tuple[int, str]] = {}
    resolved: list[tuple[dict[str, Any], dict[int, str]]] = []
    rows = sorted(
        (row for row in table.get("rows") or [] if isinstance(row, dict)),
        key=lambda item: int(item.get("row") or 0),
    )
    for row in rows:
        row_number = int(row.get("row") or 0)
        by_column = {
            column: text
            for column, (last_row, text) in active.items()
            if row_number <= last_row
        }
        for column in [column for column, (last_row, _) in active.items() if row_number > last_row]:
            active.pop(column, None)
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            column = int(cell.get("column") or 0)
            if column <= 0:
                continue
            text = str(cell.get("text") or "").strip()
            by_column[column] = text
            rowspan = max(1, int(cell.get("rowspan") or 1))
            if rowspan > 1:
                active[column] = (row_number + rowspan - 1, text)
            else:
                active.pop(column, None)
        resolved.append((row, by_column))
    return resolved


def extract_procurement_targets(response_outline: dict[str, Any]) -> list[str]:
    """Return leaf procurement targets for detailed-solution insertion.

    A procurement-subject table commonly names the package as a whole and is
    followed by product/service sub-items.  The whole-subject is useful
    context, but must not become a third peer of its own leaves.
    """
    details = _extract_procurement_target_details(response_outline)
    return details["leaf_targets"] or details["overall_subjects"]


def _extract_procurement_target_details(response_outline: dict[str, Any]) -> dict[str, list[str]]:
    overall_subjects: list[str] = []
    leaf_targets: list[str] = []
    for chapter in response_outline.get("chapters") or []:
        for point in chapter.get("response_points") or []:
            heading = str(point.get("original_heading") or "")
            text = str(point.get("original_text") or "")
            combined = f"{heading}\n{text}"
            table = point.get("table") if isinstance(point.get("table"), dict) else {}
            if any(term in combined for term in ("采购清单", "采购内容", "采购标的", "服务项目", "技术规范分项")):
                for row in table.get("rows") or []:
                    by_column = {
                        int(cell.get("column") or 0): str(cell.get("text") or "").strip()
                        for cell in row.get("cells") or []
                    }
                    if not by_column.get(1, "").isdigit():
                        continue
                    values = list(by_column.values())
                    name = next(
                        (
                            value for value in values[1:]
                            if value and value not in {"标的名称", "产品名称", "服务名称", "采购内容"}
                        ),
                        "",
                    )
                    _append_procurement_target(overall_subjects, name)
            candidate = re.sub(r"^\s*\d+(?:\.\d+)*[.、]?\s*", "", heading)
            candidate = re.sub(r"(?:产品|服务|软件|系统)部分$", lambda match: match.group(0)[:-2], candidate).strip()
            if candidate and any(term in heading for term in ("产品", "服务", "软件", "系统")) and "部分" in heading:
                _append_procurement_target(leaf_targets, candidate)
    return {"overall_subjects": overall_subjects, "leaf_targets": leaf_targets}


def _append_procurement_target(targets: list[str], value: str) -> None:
    name = re.sub(r"^[★▲■*＊\s]+", "", str(value or "")).strip()
    if name and len(name) <= 80 and name not in targets:
        targets.append(name)


def infer_project_type(response_outline: dict[str, Any], product_names: list[str]) -> str:
    text = "\n".join(
        str(point.get("original_text") or "")
        for chapter in response_outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    )
    if any(term in text for term in ("软件开发", "信息化建设", "系统开发")):
        return "software_development"
    service_signals = ("服务", "订阅", "授权", "咨询", "研究", "运维", "技术支持", "维护")
    goods_signals = ("设备", "硬件", "服务器", "交换机", "存储", "终端", "货物", "供货", "到货", "系统集成")
    goods_hits = sum(term in text for term in goods_signals)
    service_hits = sum(term in text for term in service_signals)
    # “安装”在软件交付中同样常见，只能作为辅助信号，不能单独定类。
    if "系统集成" in text or (goods_hits >= 2 and goods_hits > service_hits):
        return "goods_system_integration"
    return "technical_service_research"


def render_outline_markdown(outline: dict[str, Any]) -> str:
    lines = [
        "# 项目专属技术标文章结构大纲",
        "",
        f"- 格式模式：{outline['format_analysis']['format_mode']}",
        f"- 顺序锁定：{str(outline['format_analysis']['order_locked']).lower()}",
        f"- 响应点覆盖：{outline['coverage']['response_point_coverage_rate']:.2%}",
        f"- ★条款覆盖：{outline['coverage']['mandatory_coverage_rate']:.2%}",
        "- 本文件仅用于结构审核，不包含投标正文或未经确认的项目事实。",
        "",
    ]
    for index, chapter in enumerate(outline.get("chapters") or [], start=1):
        lines.extend(_render_markdown_node(chapter, str(index), level=2))
        for child_index, child in enumerate(chapter.get("children") or [], start=1):
            lines.extend(_render_markdown_node(child, f"{index}.{child_index}", level=3))
    if outline["coverage"]["unmapped_response_point_ids"]:
        lines.extend(["## 未映射响应点", ""])
        for item in outline["coverage"]["unmapped_reasons"]:
            lines.append(f"- {item['response_point_id']}：{item['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_company_skeleton(
    modules: list[dict[str, Any]],
    score_items: list[dict[str, Any]],
    product_names: list[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    score_by_term = {item["title"]: item["score_item_id"] for item in score_items}
    chapters: list[dict[str, Any]] = []
    module_chapters: dict[str, dict[str, Any]] = {}
    for root_index, module in enumerate(modules, start=1):
        module_id = str(module.get("module_id") or module.get("node_id") or "")
        title = str(module.get("title") or "")
        mapping_type = "direct"
        reason = "公司通用大纲默认章节，按当前项目适用性保留。"
        source_type = "公司通用"
        if module_id == "MODULE-IMPLEMENTATION":
            title = "实施部署方案"
            mapping_type = "renamed"
            source_type = "公司通用/评分项改名"
            reason = "评分项明确评价实施部署，沿用公司实施模块并改为目录化标题。"
        elif module_id == "MODULE-DETAILED-SOLUTION" and product_names:
            mapping_type = "split"
            reason = "依据公司拆分策略，按采购标的补充项目专属技术方案子章节。"
        elif module_id == "MODULE-PARAMETER-RESPONSE":
            mapping_type = "form_only"
            source_type = "强制表格"
            reason = "招标文件要求独立填写技术要求点对点应答表。"
        chapter = _new_chapter(
            chapter_id=f"WC-{root_index:03d}",
            title=title,
            mapping_type=mapping_type,
            company_ids=[str(module.get("node_id") or module_id)],
            source_type=source_type,
            reason=reason,
            company_path=str(module.get("title") or ""),
        )
        chapter["required_by_default"] = bool(module.get("required_by_default"))
        chapter["chapter_type"] = str(module.get("chapter_type") or "narrative")
        chapter["original_company_title"] = str(module.get("title") or "")
        for child_index, child in enumerate(module.get("children") or [], start=1):
            child_title = str(child.get("title") or "")
            child_mapping = "direct"
            child_source = "公司通用"
            child_reason = "继承公司通用大纲默认小节。"
            if module_id == "MODULE-DETAILED-SOLUTION" and child_title == "系统或产品架构":
                child_title = "系统架构设计"
                child_mapping = "renamed"
                child_source = "公司通用/评分项改名"
                child_reason = "评分项明确要求系统架构设计，保留原义并使用适合目录的标题。"
            elif module_id == "MODULE-PARAMETER-RESPONSE" and child_title == "技术参数点对点应答表":
                child_title = "技术要求点对点应答表"
                child_mapping = "form_only"
                child_source = "强制表格"
                child_reason = "招标文件提供并要求完整填写同名表格，按招标名称插入。"
            child_chapter = _new_chapter(
                chapter_id=f"WC-{root_index:03d}-{child_index:02d}",
                title=child_title,
                mapping_type=child_mapping,
                company_ids=[str(child.get("node_id") or "")],
                source_type=child_source,
                reason=child_reason,
                company_path=f"{module.get('title', '')} / {child.get('title', '')}",
            )
            child_chapter["original_company_title"] = str(child.get("title") or "")
            chapter["children"].append(child_chapter)
        if module_id == "MODULE-DETAILED-SOLUTION":
            source_split_id = next(
                (
                    str(child.get("node_id") or "")
                    for child in module.get("children") or []
                    if "分项产品" in str(child.get("title") or "")
                ),
                str(module.get("node_id") or module_id),
            )
            for product in product_names:
                child_index = len(chapter["children"]) + 1
                product_title = f"{product}详细技术方案" if product.endswith("系统") else f"{product}技术方案"
                chapter["children"].append(
                    _new_chapter(
                        chapter_id=f"WC-{root_index:03d}-{child_index:02d}",
                        title=product_title,
                        mapping_type="inserted",
                        company_ids=[source_split_id],
                        source_type="招标规定/技术要求新增",
                        reason=f"采购清单包含“{product}”，按公司大纲的采购标的拆分策略新增。",
                        company_path=f"{module.get('title', '')} / 分项产品、子系统或研究任务方案",
                    )
                )
        chapters.append(chapter)
        module_chapters[module_id] = chapter
    return chapters, module_chapters


def _apply_score_items(
    score_items: list[dict[str, Any]],
    modules: dict[str, dict[str, Any]],
    point_context: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    targets: dict[str, list[dict[str, Any]]] = {}
    for item in score_items:
        text = f"{item['title']} {item['criteria_summary']}"
        for node in _score_target_nodes(item, text, modules, point_context):
            _append_unique(node["score_item_refs"], item["score_item_id"])
            _append_unique(node["triggered_by"], f"评分项：{item['title']}")
            response_ids = item.get("response_point_ids") or [item.get("response_point_id")]
            for point_id in response_ids:
                point_id = str(point_id or "")
                if point_id not in point_context:
                    continue
                _add_point_mapping(node, point_id, "supporting", point_context)
                targets.setdefault(point_id, []).append(node)
    return targets


def _score_target_nodes(
    item: dict[str, Any],
    text: str,
    modules: dict[str, dict[str, Any]],
    point_context: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose the smallest meaningful writing sections for a score item."""
    def children(module_id: str, *keywords: str) -> list[dict[str, Any]]:
        root = modules.get(module_id)
        if not root:
            return []
        return [node for keyword in keywords if (node := _child_containing(root, keyword))]

    if any(term in text for term in ("总体评价", "总体技术", "整体评价")):
        return children("MODULE-OVERALL-SOLUTION", "技术要求总体响应") + children(
            "MODULE-PROJECT-UNDERSTANDING", "总体响应说明"
        )
    if "项目负责人" in text:
        return children("MODULE-PROJECT-MANAGEMENT", "项目负责人")
    if any(term in text for term in ("项目团队", "团队人员", "人员结构", "技术人员")):
        return children("MODULE-PROJECT-MANAGEMENT", "主要技术人员", "岗位职责", "人员投入")
    if any(term in text for term in ("业绩", "类似项目")):
        return children("MODULE-EVIDENCE-APPENDIX", "类似项目业绩")
    if any(term in text for term in ("工作进度", "进度计划", "进度保证")):
        return children("MODULE-IMPLEMENTATION", "实施总体安排", "实施阶段", "进度计划") + children(
            "MODULE-RISK-CONTROL", "进度风险控制"
        )
    if any(term in text for term in ("服务方案", "履约能力", "质量保证", "服务承诺", "售后服务")):
        return children(
            "MODULE-SERVICE-WARRANTY",
            "服务目标",
            "服务范围",
            "服务组织体系",
            "服务响应机制",
            "服务质量监督",
            "服务承诺",
        )
    if any(term in text for term in ("专利", "绩效评价", "绩效")):
        if _has_score_supporting_material(item, point_context):
            evidence = modules.get("MODULE-EVIDENCE-APPENDIX")
            return [_ensure_score_supporting_material_node(evidence)] if evidence else []
        return children("MODULE-EVIDENCE-APPENDIX", "产品技术资料")
    if "需求理解" in text or "需求分析" in text:
        return children("MODULE-PROJECT-UNDERSTANDING", "项目需求理解")
    if "方案完整性" in text or "系统架构" in text:
        return children("MODULE-OVERALL-SOLUTION", "总体方案说明", "方案总体架构")
    return children("MODULE-EVIDENCE-APPENDIX", "产品技术资料")


def _has_score_supporting_material(item: dict[str, Any], point_context: dict[str, dict[str, Any]]) -> bool:
    for point_id in item.get("response_point_ids") or []:
        point = (point_context.get(str(point_id)) or {}).get("point") or {}
        text = f"{point.get('original_heading') or ''} {point.get('original_text') or ''}"
        if "技术评分标准" in text and "支撑材料" in text:
            return True
    return False


def _ensure_score_supporting_material_node(evidence: dict[str, Any]) -> dict[str, Any]:
    existing = _child_containing(evidence, "技术评分支撑材料")
    if existing:
        return existing
    child_index = len(evidence.get("children") or []) + 1
    node = _new_chapter(
        chapter_id=f"{evidence['chapter_id']}-{child_index:02d}",
        title="技术评分支撑材料",
        mapping_type="inserted",
        company_ids=evidence.get("company_outline_node_ids") or [],
        source_type="采购文件技术评分支撑要求",
        reason="当前采购文件明确要求提交技术评分标准涉及的支撑材料。",
        company_path=f"{evidence.get('company_outline_path') or evidence.get('title') or ''} / 技术评分支撑材料",
    )
    evidence.setdefault("children", []).append(node)
    return node


def _map_response_points(
    response_outline: dict[str, Any],
    modules: dict[str, dict[str, Any]],
    point_context: dict[str, dict[str, Any]],
    score_targets: dict[str, list[dict[str, Any]]],
) -> None:
    for point_id, context in point_context.items():
        point = context["point"]
        chapter_title = context["chapter_title"]
        heading = str(point.get("original_heading") or "")
        text = str(point.get("original_text") or "")
        combined = f"{heading} {text}"
        targets: list[tuple[dict[str, Any], str, str]] = []

        if "技术履约" in chapter_title:
            if "服务要求" in heading:
                if "培训" in text:
                    targets.append((_best_child(modules, "MODULE-TRAINING", "培训计划"), "primary", "服务要求中的现场培训"))
                elif "服务机构" in text:
                    targets.append((_best_child(modules, "MODULE-SERVICE-WARRANTY", "服务机构"), "primary", "服务机构要求"))
                elif "响应" in text or "维修工程师" in text:
                    targets.append((_best_child(modules, "MODULE-SERVICE-WARRANTY", "服务响应"), "primary", "保修响应要求"))
                else:
                    targets.append((_best_child(modules, "MODULE-SERVICE-WARRANTY", "保修与维修"), "primary", "保修维修要求"))
            elif "交货" in heading:
                keyword = "进度计划" if any(term in text for term in ("时间", "交货期", "安装完成")) else "交付成果"
                targets.append((_best_child(modules, "MODULE-IMPLEMENTATION", keyword), "primary", "交货及安装要求"))
            elif "验收" in heading:
                targets.append((_best_child(modules, "MODULE-QUALITY-ACCEPTANCE", "验收流程"), "primary", "验收方法及标准"))
        elif "总体技术要求" in chapter_title:
            procurement_leaf = _procurement_leaf_from_heading(heading)
            product_nodes = [
                child
                for child in (modules.get("MODULE-DETAILED-SOLUTION") or {}).get("children") or []
                if child.get("mapping_type") == "inserted"
            ]
            matching_products = [
                child for child in product_nodes if procurement_leaf and procurement_leaf in str(child.get("title") or "")
            ]
            if matching_products:
                targets.extend((child, "primary", "采购分项技术要求") for child in matching_products)
            elif any(term in text for term in ("绿色供应链", "节能", "环保")):
                special = modules.get("MODULE-SPECIAL-TOPICS")
                if special:
                    targets.append((special, "primary", "专项技术要求"))
            elif any(term in text for term in ("证明材料", "技术资料", "认证")):
                evidence = modules.get("MODULE-EVIDENCE-APPENDIX")
                if evidence:
                    targets.append((evidence, "primary", "技术证明材料要求"))
            elif "具体需求" in text:
                detailed = modules.get("MODULE-DETAILED-SOLUTION")
                if detailed:
                    targets.append((detailed, "primary", "具体需求引用"))
            else:
                targets.append((_best_child(modules, "MODULE-OVERALL-SOLUTION", "技术要求总体响应"), "primary", "总体技术要求"))
        elif "项目需求书" in chapter_title:
            if "项目背景" in heading:
                targets.append((_best_child(modules, "MODULE-PROJECT-UNDERSTANDING", "项目背景"), "primary", "项目背景"))
            elif point.get("content_type") == "table":
                product_nodes = [
                    child
                    for child in (modules.get("MODULE-DETAILED-SOLUTION") or {}).get("children") or []
                    if child["mapping_type"] == "inserted"
                ]
                targets.extend((node, "primary", "采购标的技术要求") for node in product_nodes)
                parameter = modules.get("MODULE-PARAMETER-RESPONSE")
                form_node = _child_containing(parameter, "技术要求点对点应答表") if parameter else None
                if form_node or parameter:
                    targets.append((form_node or parameter, "table_response", "采购参数须逐条应答"))
            elif "★" in str(point.get("mandatory_marker") or "") or "负偏离" in text:
                deviation = modules.get("MODULE-TECHNICAL-DEVIATION")
                if deviation:
                    targets.append((deviation, "primary", "实质性条款偏离规则"))
            elif "核心产品" in text:
                detailed = modules.get("MODULE-DETAILED-SOLUTION")
                if detailed:
                    targets.append((detailed, "supporting", "核心产品说明"))
            elif "节能产品" in text or "环境标志" in text:
                special = modules.get("MODULE-SPECIAL-TOPICS")
                if special:
                    targets.append((special, "primary", "节能环保要求"))
        elif "评分" in chapter_title:
            if point_id in score_targets:
                targets.extend((node, "supporting", "技术评分项") for node in score_targets[point_id])
        elif "格式" in chapter_title or "应答要求" in chapter_title:
            role = "table_response" if any(name in combined for name in ("点对点应答表", "逐条")) else "supporting"
            parameter = modules.get("MODULE-PARAMETER-RESPONSE")
            form_node = _child_containing(parameter, "技术要求点对点应答表") if parameter else None
            if form_node or parameter:
                targets.append((form_node or parameter, role, "招标技术应答格式要求"))
            if "偏离" in combined:
                deviation = modules.get("MODULE-TECHNICAL-DEVIATION")
                if deviation:
                    targets.append((deviation, "supporting", "偏离填写要求"))

        if not targets:
            targets.append((modules["MODULE-OVERALL-SOLUTION"], "supporting", "未命中专项规则，纳入总体响应"))
        for node, role, trigger in targets:
            if node is None:
                continue
            _add_point_mapping(node, point_id, role, point_context)
            _append_unique(node["triggered_by"], trigger)


def _procurement_leaf_from_heading(heading: str) -> str:
    candidate = re.sub(r"^\s*\d+(?:\.\d+)*[.、]?\s*", "", str(heading or ""))
    if "部分" not in candidate or not any(term in candidate for term in ("产品", "服务", "软件", "系统")):
        return ""
    return re.sub(r"(?:产品|服务|软件|系统)部分$", lambda match: match.group(0)[:-2], candidate).strip()


def _build_point_context(response_outline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    block_ids: dict[str, list[str]] = {}
    for block in response_outline.get("display_blocks") or []:
        for point_id in block.get("response_point_ids") or []:
            block_ids.setdefault(str(point_id), []).append(str(block.get("block_id") or ""))
    result: dict[str, dict[str, Any]] = {}
    for chapter in response_outline.get("chapters") or []:
        section_by_point: dict[str, dict[str, Any]] = {}
        for section in chapter.get("sections") or []:
            for point_id in section.get("response_point_ids") or []:
                section_by_point[str(point_id)] = section
        for point in chapter.get("response_points") or []:
            point_id = str(point.get("point_id") or "")
            section = section_by_point.get(point_id, {})
            ref = str(chapter.get("chapter_id") or "")
            if section.get("section_id"):
                ref += f"/{section['section_id']}"
            ref += f"/{point_id}"
            result[point_id] = {
                "point": point,
                "chapter_id": str(chapter.get("chapter_id") or ""),
                "chapter_title": str(chapter.get("title") or ""),
                "section_id": str(section.get("section_id") or ""),
                "tender_ref": ref,
                "display_block_ids": block_ids.get(point_id, []),
            }
    return result


def _build_adaptation_report(
    outline: dict[str, Any],
    unmapped: list[dict[str, Any]],
    validation: dict[str, Any],
) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = {
        "retained_company_nodes": [],
        "renamed_company_nodes": [],
        "merged_company_nodes": [],
        "split_company_nodes": [],
        "inserted_nodes": [],
        "form_only_nodes": [],
        "excluded_company_nodes": [],
    }
    mapping_keys = {
        "direct": "retained_company_nodes",
        "renamed": "renamed_company_nodes",
        "merged": "merged_company_nodes",
        "split": "split_company_nodes",
        "inserted": "inserted_nodes",
        "form_only": "form_only_nodes",
        "excluded": "excluded_company_nodes",
    }
    for node in _iter_target_nodes(outline.get("chapters") or []):
        key = mapping_keys.get(node["mapping_type"])
        if key:
            categories[key].append(
                {
                    "chapter_id": node["chapter_id"],
                    "title": node["title"],
                    "company_outline_node_ids": node["company_outline_node_ids"],
                    "original_company_title": node.get("original_company_title", ""),
                    "score_item_refs": node["score_item_refs"],
                    "reason": node["adaptation_reason"],
                }
            )
    return {
        "format_mode": outline["format_analysis"]["format_mode"],
        **categories,
        "unmapped_response_points": unmapped,
        "coverage": outline["coverage"],
        "warnings": list(validation.get("warnings") or []),
    }


def _new_chapter(
    *,
    chapter_id: str,
    title: str,
    mapping_type: str,
    company_ids: list[str],
    source_type: str,
    reason: str,
    company_path: str,
) -> dict[str, Any]:
    return {
        "chapter_id": chapter_id,
        "title": title,
        "mapping_type": mapping_type,
        "source_type": source_type,
        "company_outline_node_ids": [value for value in company_ids if value],
        "company_outline_path": company_path,
        "tender_structure_refs": [],
        "response_point_mappings": [],
        "display_block_ids": [],
        "score_item_refs": [],
        "triggered_by": [],
        "adaptation_reason": reason,
        "children": [],
    }


def _add_point_mapping(
    node: dict[str, Any],
    point_id: str,
    role: str,
    contexts: dict[str, dict[str, Any]],
) -> None:
    mapping = {"response_point_id": point_id, "mapping_role": role}
    if mapping not in node["response_point_mappings"]:
        node["response_point_mappings"].append(mapping)
    context = contexts[point_id]
    _append_unique(node["tender_structure_refs"], context["tender_ref"])
    for block_id in context["display_block_ids"]:
        _append_unique(node["display_block_ids"], block_id)


def _select_company_modules(
    normalized: dict[str, Any],
    project_type: str,
    *,
    response_outline: dict[str, Any],
    score_items: list[dict[str, Any]],
    format_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    profiles = normalized.get("project_type_profiles") or []
    selected_ids = next(
        (list(profile.get("default_module_ids") or []) for profile in profiles if profile.get("project_type") == project_type),
        [],
    )
    modules = list(normalized.get("chapters") or [])
    if not selected_ids:
        return modules
    by_id = {str(module.get("module_id") or module.get("node_id")): module for module in modules}
    return [
        by_id[module_id]
        for module_id in selected_ids
        if module_id in by_id
        and (
            bool(by_id[module_id].get("required_by_default"))
            or _optional_module_has_evidence(module_id, response_outline, score_items, format_analysis)
        )
    ]


def _optional_module_has_evidence(
    module_id: str,
    response_outline: dict[str, Any],
    score_items: list[dict[str, Any]],
    format_analysis: dict[str, Any],
) -> bool:
    text = "\n".join(
        str(point.get("original_heading") or "") + "\n" + str(point.get("original_text") or "")
        for chapter in response_outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    )
    score_text = "\n".join(
        f"{item.get('title') or ''} {item.get('criteria_summary') or ''}" for item in score_items
    )
    evidence = f"{text}\n{score_text}"
    if module_id == "MODULE-TRAINING":
        return "培训" in evidence
    if module_id == "MODULE-SPECIAL-TOPICS":
        return any(term in evidence for term in ("绿色供应链", "环境标志", "节能", "信息安全专项", "国产化", "数据迁移"))
    if module_id == "MODULE-TECHNICAL-DEVIATION":
        forms = format_analysis.get("required_forms") or []
        return "技术偏差" in evidence or any("偏差" in str(item.get("title") or "") for item in forms)
    if module_id == "MODULE-PARAMETER-RESPONSE":
        return any(term in evidence for term in ("点对点应答", "技术参数", "逐条应答"))
    return False


def _best_child(modules: dict[str, dict[str, Any]], module_id: str, keyword: str) -> dict[str, Any]:
    root = modules.get(module_id)
    return _child_containing(root, keyword) or root if root else None


def _child_containing(root: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    return next((child for child in root.get("children") or [] if keyword in child["title"]), None)


def _collect_mapped_point_ids(chapters: list[dict[str, Any]]) -> set[str]:
    return {
        mapping["response_point_id"]
        for node in _iter_target_nodes(chapters)
        for mapping in node.get("response_point_mappings") or []
    }


def _iter_target_nodes(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        yield from _iter_target_nodes(list(node.get("children") or []))


def _sort_and_dedupe_mappings(chapters: list[dict[str, Any]]) -> None:
    for node in _iter_target_nodes(chapters):
        node["tender_structure_refs"] = sorted(set(node["tender_structure_refs"]))
        node["display_block_ids"] = sorted(set(node["display_block_ids"]))
        node["score_item_refs"] = sorted(set(node["score_item_refs"]))
        node["triggered_by"] = list(dict.fromkeys(node["triggered_by"]))
        node["response_point_mappings"] = sorted(
            {f"{item['response_point_id']}|{item['mapping_role']}": item for item in node["response_point_mappings"]}.values(),
            key=lambda item: (item["response_point_id"], item["mapping_role"]),
        )


def _render_markdown_node(node: dict[str, Any], number: str, *, level: int) -> list[str]:
    score_refs = "、".join(node.get("score_item_refs") or []) or "无"
    response_refs = "、".join(
        f"{item['response_point_id']}({item['mapping_role']})"
        for item in node.get("response_point_mappings") or []
    ) or "无"
    company_ids = "、".join(node.get("company_outline_node_ids") or []) or "无（项目专属新增）"
    lines = [
        f"{'#' * level} {number} {node['title']}",
        "",
        f"- 来源类型：{node['source_type']}",
        f"- 公司大纲对应位置：{node.get('company_outline_path') or company_ids}",
        f"- 公司节点ID：{company_ids}",
        f"- 关联评分项：{score_refs}",
        f"- 关联响应点：{response_refs}",
        f"- 适配方式：{node['mapping_type']}",
        f"- 保留、改名或新增原因：{node['adaptation_reason']}",
    ]
    requirements = node.get("writing_requirements") or {}
    if requirements:
        manual = requirements.get("manual_project_facts") or {}
        local = requirements.get("local_kb") or {}
        web = requirements.get("web") or {}
        lines.extend(
            [
                f"- 当前可生成程度：{requirements.get('draftability', 'can_draft')}",
                f"- 人工项目事实：{manual.get('need', 'none')}；{'、'.join(manual.get('fact_types') or []) or '无'}",
                f"- 本地RAG需求：{local.get('need', 'none')}；{'、'.join(local.get('evidence_types') or []) or '无'}",
                f"- 联网需求：{web.get('need', 'forbidden')}；{'、'.join(web.get('purposes') or []) or '无'}",
            ]
        )
    lines.append("")
    return lines


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else round(numerator / denominator, 6)


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("._")
    if not cleaned:
        raise ValueError("output_key must contain a safe path component")
    return cleaned


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shorten(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"

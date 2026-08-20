from __future__ import annotations

import hashlib
import html
import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.document_clause_indexer import build_clause_index
from app.document_page_mapper import map_docx_blocks_to_pages
from app.document_parser import (
    parse_docx,
    parse_pdf,
    parse_pptx,
    parse_text_file,
    parse_via_libreoffice,
    parse_xlsx,
)
from app.document_structure_normalizer import normalize_document_structure


SCHEMA_VERSION = "technical_response_outline_v2"
SUPPORTED_EXTENSIONS = {".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz"}

CATEGORY_ORDER = {
    "procurement_document": 1,
    "project_requirement": 2,
    "technical_specification": 3,
    "technical_attachment": 4,
    "technical_score": 5,
    "technical_format": 6,
}
CATEGORY_LABELS = {
    "procurement_document": "招标文件",
    "project_requirement": "项目需求书",
    "technical_specification": "技术规范书",
    "technical_score": "技术评分标准",
    "technical_format": "技术文件格式",
    "technical_attachment": "技术附件",
}
CATEGORY_METADATA = {
    "procurement_document": "招标文件",
    "project_requirement": "招标需求一览表",
    "technical_specification": "技术规范书",
    "technical_score": "评分表",
    "technical_format": "招标文件",
    "technical_attachment": "招标附件",
}

TECHNICAL_TERMS = [
    "技术文件",
    "技术响应",
    "技术应答",
    "技术要求",
    "技术规范",
    "技术标准",
    "技术参数",
    "技术指标",
    "技术偏差",
    "项目需求",
    "采购内容",
    "服务内容",
    "功能要求",
    "性能要求",
    "配置要求",
    "兼容性",
    "研究内容",
    "实施要求",
    "安装部署",
    "培训要求",
    "技术支持",
    "售后服务",
    "质量保证",
    "验收要求",
    "交付成果",
    "技术文档",
    "项目团队",
    "项目负责人",
    "人员要求",
    "服务期限",
    "授权期限",
    "工期",
    "进度",
    "安全",
    "保密",
    "数据管理",
    "技术方案",
    "服务方案",
    "偏差表",
    "参数表",
]
TECHNICAL_SCORE_TERMS = [
    "技术评分",
    "技术部分",
    "技术方案",
    "服务方案",
    "实施方案",
    "项目负责人",
    "项目团队",
    "人员结构",
    "业绩",
    "资源实力",
    "工作基础",
    "专利",
    "技术成果",
    "绩效评价",
    "质量",
    "绿色制造",
    "进度及保证措施",
]
SCORE_TERMS = ["评分", "分值", "得分", "扣分", "优：", "良：", "一般：", "满分", "每项", "最高得"]
PRICE_TERMS = ["价格评分", "报价得分", "评标价", "基准价", "最高限价", "投标报价", "报价表", "税率"]
BUSINESS_TERMS = [
    "商务评分",
    "商务文件",
    "法定代表人授权",
    "授权委托书",
    "投标保证金",
    "响应保证金",
    "信用报告",
    "财务状况",
    "付款比例",
    "合同价款",
]
OPERATION_TERMS = [
    "投标工具操作手册",
    "平台解密",
    "电子招标投标交易平台",
    "文件获取流程",
    "响应文件递交",
    "投标文件递交",
    "CA证书",
    "电子钥匙",
    "新手指引",
]
FORMAT_TERMS = ["文件组成", "编制要求", "技术文件格式", "技术应答格式", "专项应答", "技术偏差表", "技术附件", "应提交的文件"]
TABLE_HEADER_TERMS = ["序号", "评分项", "分值", "评分标准", "技术参数", "要求", "项目", "内容", "条款号", "投标响应"]
MANDATORY_MARKERS = ["*", "＊", "★", "▲"]

DOMAIN_ORDER = {
    "technical_fulfillment": 1,
    "overall_technical": 2,
    "project_requirement": 3,
    "technical_score": 4,
    "technical_format": 5,
    "technical_other": 6,
}
DOMAIN_TITLES = {
    "technical_fulfillment": "技术履约相关要求",
    "overall_technical": "总体技术要求",
    "project_requirement": "项目需求书",
    "technical_score": "技术相关评分标准",
    "technical_format": "技术投标文件格式与应答要求",
    "technical_other": "其他技术要求",
}
TECHNICAL_FULFILLMENT_TERMS = [
    "保修", "维修", "零配件", "技术响应", "工程师到达", "服务机构", "技术培训", "交货期", "货到时间",
    "安装完成", "交货地点", "随机资料", "随机技术资料", "使用和维修手册", "验收方法", "验收标准", "安装调试", "售后服务",
]
BUYER_RESPONSIBILITY_TERMS = ["采购人职责", "招标人职责", "采购代理机构职责", "招标代理机构职责"]
PURE_COMMERCIAL_TERMS = ["投标人的报价", "投标报价", "货款", "报价要求", "付款方式", "投标保证金", "履约保证金", "税率"]


def build_technical_outline(
    input_dir: str | Path,
    *,
    output_dir: str | Path = "outputs",
    output_key: str | None = None,
    enable_page_mapping: bool = True,
) -> dict[str, Any]:
    selected_dir = Path(input_dir).expanduser().resolve()
    if not selected_dir.is_dir():
        raise ValueError(f"input directory not found: {selected_dir}")
    files = discover_selected_files(selected_dir)
    if not files:
        raise ValueError(f"no supported documents found in input directory: {selected_dir}")

    source_files: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    warnings: list[str] = []
    for file_order, path in enumerate(files, start=1):
        parsed_file = parse_selected_file(path, selected_dir, enable_page_mapping=enable_page_mapping)
        source_files.append(parsed_file["source_file"])
        warnings.extend(parsed_file["warnings"])
        if not parsed_file["parsed"].get("ok"):
            continue
        points.extend(extract_file_points(parsed_file, file_order, warnings))

    deduped = exact_dedupe(points)
    chapters = organize_chapters(deduped)
    assign_stable_ids(chapters)
    apply_continuation_relationships(chapters)
    display_blocks = build_display_blocks(chapters)
    display_ids_by_chapter: dict[str, list[str]] = {}
    for block in display_blocks:
        display_ids_by_chapter.setdefault(block["chapter_id"], []).append(block["block_id"])
    for chapter in chapters:
        chapter["display_block_ids"] = display_ids_by_chapter.get(chapter["chapter_id"], [])
    key = safe_path_name(output_key or selected_dir.name)
    outline = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "technical_only",
        "source_mode": "manually_selected_files",
        "input_dir": str(selected_dir),
        "project": {"project_name": "", "package_code": "", "package_name": ""},
        "source_files": source_files,
        "chapters": chapters,
        "display_blocks": display_blocks,
        "build_warnings": unique_strings(warnings),
        "statistics": build_statistics(source_files, points, deduped, chapters, display_blocks),
    }
    validate_outline(outline)
    markdown = render_technical_outline_markdown(outline)
    preview_html = render_technical_outline_html(outline)
    validate_clean_output(outline, markdown + preview_html)

    outline_dir = Path(output_dir) / key / "outline"
    outline_dir.mkdir(parents=True, exist_ok=True)
    json_path = outline_dir / "technical_response_outline.json"
    markdown_path = outline_dir / "technical_response_outline.md"
    html_path = outline_dir / "technical_response_outline.preview.html"
    json_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(preview_html, encoding="utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "output_key": key,
        "input_dir": str(selected_dir),
        "files": {"json": str(json_path), "markdown": str(markdown_path), "html_preview": str(html_path)},
        "statistics": outline["statistics"],
        "build_warnings": outline["build_warnings"],
    }


def discover_selected_files(input_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in input_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and path.suffix.lower() not in ARCHIVE_EXTENSIONS
            and not any(part.startswith(".") for part in path.relative_to(input_dir).parts)
        ],
        key=lambda path: str(path.relative_to(input_dir)).lower(),
    )


def parse_selected_file(path: Path, input_dir: Path, *, enable_page_mapping: bool = True) -> dict[str, Any]:
    relative_path = str(path.relative_to(input_dir))
    file_hash = sha256_file(path)
    file_id = f"selected_{file_hash[:16]}"
    extension = path.suffix.lower().lstrip(".")
    record = {
        "id": file_id,
        "file_id": file_id,
        "file_ext": extension,
        "original_name": path.name,
        "extract_assets": False,
    }
    if extension == "docx":
        parsed = parse_docx(path, record)
    elif extension == "doc":
        parsed = parse_via_libreoffice(path, record, "docx", parse_docx)
    elif extension == "pdf":
        parsed = parse_pdf(path, record)
    elif extension == "xlsx":
        parsed = parse_xlsx(path, record)
    elif extension == "xls":
        parsed = parse_via_libreoffice(path, record, "xlsx", parse_xlsx)
    elif extension == "pptx":
        parsed = parse_pptx(path, record)
    elif extension == "ppt":
        parsed = parse_via_libreoffice(path, record, "pptx", parse_pptx)
    else:
        parsed = parse_text_file(path)
    page_warnings: list[str] = []
    if parsed.get("ok") and extension in {"doc", "docx"}:
        blocks = list(parsed.get("blocks") or [])
        if enable_page_mapping and extension == "docx":
            blocks, page_warnings = map_docx_blocks_to_pages(path, blocks)
        parsed["blocks"] = normalize_document_structure(blocks)
    category = infer_source_category(path.name, str(parsed.get("parsed_text") or ""))
    warnings = [f"{path.name}: {item}" for item in list(parsed.get("warnings") or []) + page_warnings]
    if not parsed.get("ok"):
        warnings.append(f"{path.name}: 文件未成功解析，未进入正式大纲")
    return {
        "path": path,
        "file_id": file_id,
        "file_name": path.name,
        "relative_path": relative_path,
        "category": category,
        "parsed": parsed,
        "warnings": warnings,
        "source_file": {
            "file_id": file_id,
            "file_name": path.name,
            "relative_path": relative_path,
            "sha256": file_hash,
            "source_category": category,
            "parse_status": "parsed" if parsed.get("ok") else "failed",
            "parse_warnings": list(parsed.get("warnings") or []),
            "page_mapping": "libreoffice_rendered_pdf" if any(block.get("page_source") for block in parsed.get("blocks") or []) else "unavailable",
        },
    }


def infer_source_category(file_name: str, parsed_text: str) -> str:
    name = file_name.lower()
    sample = parsed_text[:12000]
    if any(term in name for term in ["技术规范", "技术标准", "技术要求书"]):
        return "technical_specification"
    if any(term in name for term in ["评标办法", "评分表", "评分标准"]):
        return "technical_score"
    if any(term in name for term in ["需求书", "采购需求", "需求清单", "需求一览"]):
        return "project_requirement"
    if any(term in name for term in ["技术偏差", "技术参数", "技术附件", "配置表"]):
        return "technical_attachment"
    if any(term in name for term in ["技术格式", "响应格式", "应答格式", "格式模板"]):
        return "technical_format"
    if any(term in name for term in ["招标文件", "采购文件", "招标公告", "采购公告"]):
        return "procurement_document"
    if "技术评分标准" in sample or ("评分标准" in sample and has_any(sample, TECHNICAL_SCORE_TERMS)):
        return "technical_score"
    if "技术规范" in sample or "技术标准" in sample:
        return "technical_specification"
    if has_any(sample, FORMAT_TERMS):
        return "technical_format"
    return "technical_attachment"


def extract_file_points(parsed_file: dict[str, Any], file_order: int, warnings: list[str]) -> list[dict[str, Any]]:
    if any("heading_path" in block for block in parsed_file["parsed"].get("blocks") or []):
        return extract_structured_file_points(parsed_file, file_order, warnings)
    paragraph_text, line_locations, page_ranges = paragraph_source(parsed_file["parsed"])
    metadata = {
        "id": parsed_file["file_id"],
        "original_name": parsed_file["file_name"],
        "category": CATEGORY_METADATA[parsed_file["category"]],
        "parsed_text": paragraph_text,
    }
    clauses = build_clause_index(
        parsed_file["file_id"],
        parsed_file["file_name"],
        paragraph_text,
        metadata,
    ) if paragraph_text.strip() else []
    points: list[dict[str, Any]] = []
    for clause_order, clause in enumerate(clauses, start=1):
        raw = paragraph_text[int(clause.get("start_offset") or 0): int(clause.get("end_offset") or 0)]
        original_text = clean_original_text(raw)
        if not original_text or not is_technical_clause(parsed_file["category"], clause, original_text):
            continue
        paragraph_index = int(clause.get("source_location", {}).get("paragraph_index") or 0)
        source_location = line_locations[paragraph_index] if paragraph_index < len(line_locations) else ""
        points.append(
            build_text_point(
                parsed_file,
                clause,
                original_text,
                file_order,
                clause_order,
                source_location,
                page_for_offset(page_ranges, int(clause.get("start_offset") or 0)),
            )
        )

    table_candidates = extract_table_points(parsed_file, file_order)
    if parsed_file["category"] == "technical_score":
        explicit = [item for item in table_candidates if item.get("score_table_explicit")]
        if len(table_candidates) > 1 and len(explicit) != 1:
            warnings.append(
                f"{parsed_file['file_name']}: 检测到多套可能适用的技术评分表，无法从人工筛选目录内唯一确认，未将这些表写入正式大纲"
            )
            table_candidates = []
        elif len(explicit) == 1:
            table_candidates = explicit
    points.extend(table_candidates)
    return sorted(points, key=lambda item: (item["file_order"], item["source_order"], item["content_type"] == "table"))


def extract_structured_file_points(
    parsed_file: dict[str, Any],
    file_order: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    blocks = list(parsed_file["parsed"].get("blocks") or [])
    table_candidates: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("block_type") == "paragraph":
            if block.get("is_heading"):
                continue
            original_text = clean_original_text(str(block.get("text") or ""))
            domain = technical_domain_for_block(parsed_file["category"], block, original_text)
            if not original_text or domain is None:
                continue
            path = list(block.get("heading_path") or [])
            point = {
                "domain": domain,
                "section_path": outline_section_path(domain, path),
                "source_category": source_category_for_domain(domain, parsed_file["category"]),
                "original_heading": path[-1] if path else Path(parsed_file["file_name"]).stem,
                "node_type": str(block.get("node_type") or "paragraph"),
                "original_text": original_text,
                "content_type": infer_structured_content_type(original_text),
                "mandatory_marker": detect_mandatory_marker(original_text),
                "source_refs": [source_ref_for_block(parsed_file, block, path, original_text)],
                "file_order": file_order,
                "source_order": int(block.get("block_index") or 0) * 10,
            }
            points.append(point)
            continue
        if block.get("block_type") != "table":
            continue
        table_point = build_structured_table_point(parsed_file, block, file_order)
        if table_point:
            table_candidates.append(table_point)

    score_tables = [item for item in table_candidates if item["domain"] == "technical_score"]
    if parsed_file["category"] == "technical_score" and len(score_tables) > 1:
        explicit = [item for item in score_tables if item.get("score_table_explicit")]
        if len(explicit) != 1:
            warnings.append(
                f"{parsed_file['file_name']}: 检测到多套可能适用的技术评分表，无法从人工筛选目录内唯一确认，未将这些表写入正式大纲"
            )
            table_candidates = [item for item in table_candidates if item["domain"] != "technical_score"]
        else:
            table_candidates = [item for item in table_candidates if item["domain"] != "technical_score"] + explicit
    points.extend(table_candidates)
    return sorted(points, key=lambda item: (item["file_order"], item["source_order"]))


def technical_domain_for_block(category: str, block: dict[str, Any], text: str) -> str | None:
    path = list(block.get("heading_path") or [])
    path_text = " > ".join(path)
    combined = f"{path_text} {text}"
    if text.rstrip("：:") == "注":
        return None
    if has_any(path_text, BUYER_RESPONSIBILITY_TERMS):
        return None
    if has_any(text, OPERATION_TERMS) and not has_any(text, ["技术要求点对点应答表"]):
        return None
    if has_any(text, PRICE_TERMS + PURE_COMMERCIAL_TERMS + ["财政专项资金"]):
        return None
    if "一、商务要求" in path_text and has_any(combined, TECHNICAL_FULFILLMENT_TERMS):
        return "technical_fulfillment"
    if "二、技术要求" in path_text:
        return "overall_technical"
    if "项目需求书" in path_text:
        if "本项目所属行业" in text:
            return None
        return "project_requirement"
    if "评审因素及评标标准" in path_text or category == "technical_score":
        return "technical_score" if has_any(combined, TECHNICAL_SCORE_TERMS + SCORE_TERMS) else None
    if has_any(text, ["技术要求点对点应答表", "项目需求书要求", "不得仅填写", "只注明“符合”", "具体数值或内容"]):
        return "technical_format"
    if "技术要求点对点应答表" in path_text and re.match(r"^\d+[.、]", text) and not has_any(text, ["项目名称", "项目编号", "包号", "投标人名称", "日期"]):
        return "technical_format"
    if "投标文件内容要求" in path_text and has_any(combined, ["投标文件", "编制", "格式"]):
        return "technical_format"
    if category == "technical_specification":
        return "overall_technical"
    if category == "project_requirement":
        return "project_requirement"
    if category == "technical_format" and has_any(combined, TECHNICAL_TERMS + FORMAT_TERMS):
        return "technical_format"
    if category == "technical_attachment" and has_any(combined, TECHNICAL_TERMS + FORMAT_TERMS):
        return "technical_other"
    if category == "procurement_document" and has_any(path_text, ["技术文件", "技术规范", "技术附件", "技术偏差"]):
        return "technical_format" if has_any(path_text, ["技术文件", "技术偏差"]) else "technical_other"
    return None


def source_category_for_domain(domain: str, fallback: str) -> str:
    return {
        "project_requirement": "project_requirement",
        "technical_score": "technical_score",
        "technical_format": "technical_format",
        "overall_technical": "technical_specification",
        "technical_fulfillment": "procurement_document",
    }.get(domain, fallback)


def outline_section_path(domain: str, source_path: list[str]) -> list[str]:
    if not source_path:
        return []
    anchors = {
        "technical_fulfillment": ["一、商务要求"],
        "overall_technical": ["二、技术要求"],
        "project_requirement": ["项目需求书"],
        "technical_score": ["三、评审因素及评标标准", "技术评分标准"],
        "technical_format": ["技术要求点对点应答表", "四、投标文件内容要求"],
    }
    for anchor in anchors.get(domain, []):
        for index, item in enumerate(source_path):
            if anchor in item:
                return source_path[index + 1:]
    return source_path[-3:]


def source_ref_for_block(
    parsed_file: dict[str, Any],
    block: dict[str, Any],
    path: list[str],
    original_text: str,
) -> dict[str, Any]:
    return {
        "file_id": parsed_file["file_id"],
        "file_name": parsed_file["file_name"],
        "clause_path": path,
        "original_number": extract_original_number(original_text),
        "page_start": block.get("page_start"),
        "page_end": block.get("page_end"),
        "page_source": block.get("page_source") or "unavailable",
        "rendered_page": block.get("rendered_page"),
        "printed_page_label": None,
        "sheet_name": "",
        "table_name": "",
        "table_range": "",
        "row_or_cell": str(block.get("source_location") or ""),
        "block_index": int(block.get("block_index") or 0),
    }


def infer_structured_content_type(text: str) -> str:
    if "附件" in text or "偏差表" in text:
        return "attachment_requirement"
    if "填写" in text and "表" in text:
        return "form"
    if extract_original_number(text):
        return "numbered_list"
    return "paragraph"


def paragraph_source(parsed: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    blocks = list(parsed.get("blocks") or [])
    lines: list[str] = []
    locations: list[str] = []
    page_ranges: list[dict[str, Any]] = []
    cursor = 0
    for block in blocks:
        if block.get("block_type") == "table":
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if lines:
            cursor += 1
        start = cursor
        lines.append(text)
        locations.extend([str(block.get("source_location") or "")] * max(1, len(text.splitlines())))
        cursor += len(text)
        page = block.get("page_number")
        if page is not None:
            page_ranges.append({"start": start, "end": cursor, "page": int(page)})
    return "\n".join(lines), locations, page_ranges


def build_text_point(
    parsed_file: dict[str, Any],
    clause: dict[str, Any],
    original_text: str,
    file_order: int,
    clause_order: int,
    source_location: str,
    page: int | None,
) -> dict[str, Any]:
    section_path = list(clause.get("section_path") or [])
    heading = str(clause.get("heading") or "").strip()
    return {
        "chapter_group": heading or (section_path[-1] if section_path else Path(parsed_file["file_name"]).stem),
        "source_category": parsed_file["category"],
        "original_heading": heading or (section_path[-1] if section_path else ""),
        "original_text": original_text,
        "content_type": infer_content_type(original_text, clause),
        "mandatory_marker": detect_mandatory_marker(original_text),
        "source_refs": [
            {
                "file_id": parsed_file["file_id"],
                "file_name": parsed_file["file_name"],
                "clause_path": " > ".join(section_path),
                "original_number": clause.get("clause_no") or extract_original_number(original_text),
                "page": page,
                "sheet_name": "",
                "table_name": "",
                "row_or_cell": source_location or str((clause.get("source_location") or {}).get("location_text") or ""),
            }
        ],
        "file_order": file_order,
        "source_order": clause_order * 10,
    }


def extract_table_points(parsed_file: dict[str, Any], file_order: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    parsed = parsed_file["parsed"]
    blocks = list(parsed.get("blocks") or [])
    table_titles = table_titles_by_index(blocks)
    for table_order, table in enumerate(list(parsed.get("tables") or []), start=1):
        normalized = normalize_table(table)
        title = table_titles.get(table_order) or str(table.get("sheet_name") or f"表{table_order}")
        selected = select_technical_table(parsed_file["category"], normalized, title)
        if not selected:
            continue
        rows = selected["rows"]
        row_numbers = selected["row_numbers"]
        original_text = "\n".join("\t".join(row) for row in rows)
        result.append(
            {
                "chapter_group": title,
                "source_category": parsed_file["category"],
                "original_heading": title,
                "original_text": original_text,
                "content_type": "table",
                "mandatory_marker": detect_mandatory_marker(original_text),
                "table": {
                    "rows": rows,
                    "row_numbers": row_numbers,
                    "merged_cells": list(normalized.get("merged_cells") or []),
                },
                "source_refs": [
                    {
                        "file_id": parsed_file["file_id"],
                        "file_name": parsed_file["file_name"],
                        "clause_path": title,
                        "original_number": "",
                        "page": None,
                        "sheet_name": str(table.get("sheet_name") or ""),
                        "table_name": title,
                        "row_or_cell": table_range(rows, row_numbers),
                    }
                ],
                "file_order": file_order,
                "source_order": table_order * 10 + 5,
                "score_table_explicit": "技术评分" in title or any("技术评分" in "".join(row) for row in rows[:2]),
            }
        )
    return result


def build_structured_table_point(
    parsed_file: dict[str, Any],
    block: dict[str, Any],
    file_order: int,
) -> dict[str, Any] | None:
    path = list(block.get("heading_path") or [])
    path_text = " > ".join(path)
    normalized = normalize_table(block)
    rows = list(normalized.get("rows") or [])
    if not rows:
        return None
    title = path[-1] if path else f"表{block.get('table_index') or ''}"
    table_text = "\n".join(" ".join(row) for row in rows)
    if "评审因素及评标标准" in path_text or parsed_file["category"] == "technical_score":
        domain = "technical_score"
        selected = select_technical_score_rows(rows, list(normalized["row_numbers"]))
    elif "项目需求书" in path_text or parsed_file["category"] == "project_requirement":
        domain = "project_requirement"
        selected = {"rows": rows, "row_numbers": list(normalized["row_numbers"]), "preserve_structure": True}
    elif "技术要求点对点应答表" in path_text or "技术要求点对点应答表" in table_text:
        domain = "technical_format"
        selected = {"rows": rows, "row_numbers": list(normalized["row_numbers"]), "preserve_structure": True}
    elif parsed_file["category"] == "technical_specification" and not is_price_or_business_title(title):
        domain = "overall_technical"
        selected = {"rows": rows, "row_numbers": list(normalized["row_numbers"]), "preserve_structure": True}
    elif parsed_file["category"] in {"technical_attachment", "technical_format"} and has_any(
        f"{title} {table_text}", TECHNICAL_TERMS + FORMAT_TERMS
    ):
        domain = "technical_format" if parsed_file["category"] == "technical_format" else "technical_other"
        selected = remove_price_columns(rows, list(normalized["row_numbers"]))
    else:
        return None
    if not selected or not selected.get("rows"):
        return None

    selected_rows = list(selected["rows"])
    selected_numbers = list(selected["row_numbers"])
    if selected.get("preserve_structure"):
        keep = set(selected_numbers)
        structured_rows = [row for row in normalized["structured_rows"] if int(row.get("row") or 0) in keep]
    else:
        structured_rows = matrix_to_structured_rows(selected_rows, selected_numbers)
    has_merges = any(
        int(cell.get("rowspan") or 1) > 1 or int(cell.get("colspan") or 1) > 1
        for row in structured_rows
        for cell in row.get("cells") or []
    )
    original_text = "\n".join("\t".join(row) for row in selected_rows)
    source = source_ref_for_block(parsed_file, block, path, original_text)
    source.update(
        {
            "table_name": clean_heading_number(title) or f"表{block.get('table_index') or ''}",
            "table_range": table_range(selected_rows, selected_numbers),
            "row_or_cell": str(block.get("source_location") or f"table_{block.get('table_index') or ''}"),
        }
    )
    return {
        "domain": domain,
        "section_path": outline_section_path(domain, path),
        "source_category": source_category_for_domain(domain, parsed_file["category"]),
        "original_heading": title,
        "node_type": "table",
        "original_text": original_text,
        "content_type": "table",
        "mandatory_marker": detect_mandatory_marker(original_text),
        "table": {
            "rows": structured_rows,
            "row_numbers": selected_numbers,
            "column_count": int(normalized.get("column_count") or max(len(row) for row in selected_rows)),
            "has_merged_cells": has_merges,
        },
        "source_refs": [source],
        "file_order": file_order,
        "source_order": int(block.get("block_index") or 0) * 10 + 5,
        "score_table_explicit": "评审因素及评标标准" in path_text or "技术评分" in title,
    }


def normalize_table(table: dict[str, Any]) -> dict[str, Any]:
    if table.get("structured_rows"):
        structured_rows = []
        for row in table.get("structured_rows") or []:
            structured_rows.append(
                {
                    "row": int(row.get("row") or len(structured_rows) + 1),
                    "cells": [
                        {
                            "text": clean_cell(cell.get("text")),
                            "row": int(cell.get("row") or row.get("row") or 0),
                            "column": int(cell.get("column") or 0),
                            "rowspan": int(cell.get("rowspan") or 1),
                            "colspan": int(cell.get("colspan") or 1),
                            "is_merge_anchor": True,
                        }
                        for cell in row.get("cells") or []
                    ],
                }
            )
        column_count = int(table.get("column_count") or 0) or max(
            (int(cell["column"]) + int(cell["colspan"]) - 1 for row in structured_rows for cell in row["cells"]),
            default=1,
        )
        rows = structured_rows_to_matrix(structured_rows, column_count)
        return {
            "rows": rows,
            "row_numbers": [int(row["row"]) for row in structured_rows],
            "structured_rows": structured_rows,
            "column_count": column_count,
            "has_merged_cells": bool(table.get("has_merged_cells")),
            "merged_cells": [],
        }
    raw_rows = list(table.get("rows") or [])
    rows: list[list[str]] = []
    row_numbers: list[int] = []
    if raw_rows and isinstance(raw_rows[0], dict):
        max_col = max(
            [int(cell.get("col") or 0) for row in raw_rows for cell in list(row.get("cells") or [])] or [0]
        )
        for row in raw_rows:
            values = [""] * max_col
            for cell in list(row.get("cells") or []):
                col = int(cell.get("col") or 0)
                if col > 0:
                    values[col - 1] = clean_cell(cell.get("value"))
            rows.append(values)
            row_numbers.append(int(row.get("row") or len(row_numbers) + 1))
    else:
        for index, row in enumerate(raw_rows, start=1):
            rows.append([clean_cell(value) for value in list(row or [])])
            row_numbers.append(index)
    rows, row_numbers = remove_empty_table_edges(rows, row_numbers)
    structured_rows = matrix_to_structured_rows(rows, row_numbers)
    return {
        "rows": rows,
        "row_numbers": row_numbers,
        "structured_rows": structured_rows,
        "column_count": max((len(row) for row in rows), default=0),
        "has_merged_cells": False,
        "merged_cells": list(table.get("merged_cells") or []),
    }


def select_technical_table(category: str, table: dict[str, Any], title: str) -> dict[str, Any] | None:
    rows = list(table.get("rows") or [])
    row_numbers = list(table.get("row_numbers") or [])
    if not rows:
        return None
    table_text = "\n".join(" ".join(row) for row in rows)
    title_text = f"{title} {table_text}"
    if category == "technical_score":
        return select_technical_score_rows(rows, row_numbers) if has_any(title_text, TECHNICAL_SCORE_TERMS) else None
    if category == "technical_specification":
        if is_price_or_business_title(title):
            return None
        return {"rows": rows, "row_numbers": row_numbers}
    if category in {"technical_format", "technical_attachment", "project_requirement", "procurement_document"}:
        if is_price_or_business_title(title) or not has_any(title_text, TECHNICAL_TERMS + FORMAT_TERMS):
            return None
        return remove_price_columns(rows, row_numbers)
    return None


def select_technical_score_rows(rows: list[list[str]], row_numbers: list[int]) -> dict[str, Any] | None:
    selected: list[list[str]] = []
    selected_numbers: list[int] = []
    in_technical_section = False
    for row, row_number in zip(rows, row_numbers):
        text = " ".join(row)
        if "第一部分价格" in text or has_any(text, PRICE_TERMS) or ("商务评分" in text and not has_any(text, TECHNICAL_SCORE_TERMS)):
            in_technical_section = False
            continue
        if "技术评分" in text or "技术部分" in text or "客观分" in text or "主观分" in text:
            in_technical_section = True
        is_header = has_any(text, TABLE_HEADER_TERMS) and len(selected) < 2
        is_technical = has_any(text, TECHNICAL_SCORE_TERMS)
        is_score_detail = in_technical_section and (has_any(text, SCORE_TERMS) or any(cell.strip() for cell in row))
        if is_header or is_technical or is_score_detail:
            selected.append(row)
            selected_numbers.append(row_number)
    if not selected or not any(
        has_any(" ".join(row), TECHNICAL_SCORE_TERMS + ["客观分", "主观分"])
        for row in selected
    ):
        return None
    return {"rows": selected, "row_numbers": selected_numbers, "preserve_structure": True}


def remove_price_columns(rows: list[list[str]], row_numbers: list[int]) -> dict[str, Any] | None:
    if not rows:
        return None
    width = max(len(row) for row in rows)
    header = rows[0] + [""] * (width - len(rows[0]))
    keep = [index for index, value in enumerate(header) if not has_any(value, PRICE_TERMS + ["报价", "税率", "限价"])]
    if not keep:
        return None
    filtered = [[(row + [""] * (width - len(row)))[index] for index in keep] for row in rows]
    return {"rows": filtered, "row_numbers": row_numbers}


def is_technical_clause(category: str, clause: dict[str, Any], text: str) -> bool:
    if clause.get("is_invalid") or clause.get("is_heading_only"):
        return False
    path = " > ".join(clause.get("section_path") or [])
    heading = str(clause.get("heading") or "")
    region = str(clause.get("document_region") or "")
    clause_type = str(clause.get("clause_type") or "")
    direct = f"{heading} {text}"
    text_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(text_lines) == 1 and normalize_layout(text_lines[0]) == normalize_layout(heading):
        return False
    if region in {"price_file_format", "business_file_format"}:
        return False
    if is_price_or_business_title(path) or is_price_or_business_title(heading):
        return False
    if clause_type in {"business_authorization", "business_license", "business_quality_commitment", "price_file_or_bid_letter_commitment"}:
        return False
    if clause_type == "operation_only" or (has_any(direct, OPERATION_TERMS) and not has_any(direct, FORMAT_TERMS)):
        return False
    if category == "technical_specification":
        return True
    if category == "technical_score":
        return has_any(direct, TECHNICAL_SCORE_TERMS) and (has_any(direct, SCORE_TERMS) or clause_type == "score_item")
    if category == "technical_format":
        return has_any(direct, TECHNICAL_TERMS + FORMAT_TERMS)
    if category == "project_requirement":
        return has_any(direct, TECHNICAL_TERMS)
    if category == "procurement_document":
        return region in {"technical_file_format", "technical_spec"} or has_any(direct, TECHNICAL_TERMS + FORMAT_TERMS)
    return has_any(direct, TECHNICAL_TERMS + TECHNICAL_SCORE_TERMS + FORMAT_TERMS)


def exact_dedupe(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for point in sorted(points, key=lambda item: (item["file_order"], item["source_order"])):
        key = exact_content_key(point)
        existing = seen.get(key)
        if existing is None:
            copied = dict(point)
            copied["source_refs"] = list(point.get("source_refs") or [])
            result.append(copied)
            seen[key] = copied
            continue
        existing["source_refs"] = unique_source_refs(existing["source_refs"] + list(point.get("source_refs") or []))
    return result


def organize_chapters(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for point in sorted(
        points,
        key=lambda item: (
            DOMAIN_ORDER.get(item.get("domain") or domain_from_category(item["source_category"]), 99),
            item["file_order"],
            item["source_order"],
        ),
    ):
        domain = point.get("domain") or domain_from_category(point["source_category"])
        groups.setdefault(domain, []).append(point)
    chapters: list[dict[str, Any]] = []
    for domain, response_points in groups.items():
        clean_points = []
        for point in response_points:
            clean_points.append(
                {
                    key: value
                    for key, value in point.items()
                    if key not in {"chapter_group", "domain", "file_order", "source_order", "score_table_explicit"}
                }
            )
        chapters.append(
            {
                "chapter_id": "",
                "title": DOMAIN_TITLES.get(domain, DOMAIN_TITLES["technical_other"]),
                "source_category": clean_points[0]["source_category"],
                "original_heading": "",
                "sections": [],
                "response_points": clean_points,
            }
        )
    return chapters


def assign_stable_ids(chapters: list[dict[str, Any]]) -> None:
    point_number = 0
    for chapter_number, chapter in enumerate(chapters, start=1):
        chapter["chapter_id"] = f"CH-{chapter_number:03d}"
        for point in chapter["response_points"]:
            point_number += 1
            point["point_id"] = f"TR-{point_number:03d}"
            point_order = ["point_id", "source_category", "original_heading", "section_path", "node_type", "original_text", "content_type", "mandatory_marker", "source_refs"]
            if "table" in point:
                point_order.append("table")
            reordered = {key: point[key] for key in point_order if key in point}
            point.clear()
            point.update(reordered)
        chapter["sections"] = build_chapter_sections(chapter["response_points"])


def apply_continuation_relationships(chapters: list[dict[str, Any]]) -> None:
    for chapter in chapters:
        points = list(chapter.get("response_points") or [])
        for point in points:
            point.pop("continuation_point_ids", None)
            point.pop("continuation_of_point_id", None)
        for index, point in enumerate(points):
            text = str(point.get("original_text") or "").strip()
            if not re.match(r"^\d+(?:\.\d+)*[.、]\s*.+[：:]$", text):
                continue
            continuation_ids: list[str] = []
            previous = point
            for candidate in points[index + 1:]:
                if extract_original_number(str(candidate.get("original_text") or "")):
                    break
                if not display_points_are_contiguous(previous, candidate):
                    break
                continuation_ids.append(candidate["point_id"])
                candidate["continuation_of_point_id"] = point["point_id"]
                previous = candidate
            if continuation_ids:
                point["continuation_point_ids"] = continuation_ids


def build_display_blocks(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for chapter in chapters:
        current: list[dict[str, Any]] = []
        for point in chapter.get("response_points") or []:
            if not current:
                current = [point]
                continue
            if should_merge_display_points(current[-1], point):
                current.append(point)
                continue
            blocks.append(make_display_block(chapter, current, len(blocks) + 1))
            current = [point]
        if current:
            blocks.append(make_display_block(chapter, current, len(blocks) + 1))
    return blocks


def should_merge_display_points(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous.get("content_type") == "table" or current.get("content_type") == "table":
        return False
    return display_points_are_contiguous(previous, current)


def display_points_are_contiguous(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_ref = primary_source_ref(previous)
    current_ref = primary_source_ref(current)
    if not previous_ref or not current_ref:
        return False
    if previous_ref.get("file_id") != current_ref.get("file_id"):
        return False
    if normalize_clause_path(previous_ref.get("clause_path")) != normalize_clause_path(current_ref.get("clause_path")):
        return False
    previous_index = int(previous_ref.get("block_index") or 0)
    current_index = int(current_ref.get("block_index") or 0)
    return bool(previous_index and current_index and 0 < current_index - previous_index <= 3)


def make_display_block(chapter: dict[str, Any], points: list[dict[str, Any]], number: int) -> dict[str, Any]:
    source = aggregate_display_source(points)
    texts = [str(point.get("original_text") or "") for point in points]
    if texts and all(text.startswith("加注“") for text in texts):
        title = "采购清单标记说明"
        block_type = "list_group"
    elif any("技术要求点对点应答表" in " ".join(normalize_clause_path(primary_source_ref(point).get("clause_path"))) for point in points):
        title = "技术要求点对点应答表" if points[0].get("content_type") == "table" else "技术要求点对点应答表填写说明"
        block_type = "table" if points[0].get("content_type") == "table" else "paragraph_group"
    elif any("18.3 投标文件中设备的性能指标" in text for text in texts):
        title = "技术应答编制要求"
        block_type = "paragraph_group"
    elif points[0].get("content_type") == "table":
        title = str(points[0].get("original_heading") or "表格")
        block_type = "table"
    else:
        section_path = list(points[0].get("section_path") or [])
        title = str((section_path[-1] if section_path else points[0].get("original_heading")) or chapter.get("title"))
        block_type = "paragraph_group" if len(points) > 1 else "paragraph"
    return {
        "block_id": f"BLOCK-{number:03d}",
        "chapter_id": chapter["chapter_id"],
        "title": title,
        "block_type": block_type,
        "response_point_ids": [point["point_id"] for point in points],
        "source_ref": source,
    }


def aggregate_display_source(points: list[dict[str, Any]]) -> dict[str, Any]:
    refs = [primary_source_ref(point) for point in points]
    refs = [ref for ref in refs if ref]
    first = refs[0] if refs else {}
    located_starts = [int(ref["page_start"]) for ref in refs if ref.get("page_start") is not None]
    located_ends = [int(ref.get("page_end") or ref["page_start"]) for ref in refs if ref.get("page_start") is not None]
    block_locations = [str(ref.get("row_or_cell") or "") for ref in refs if ref.get("row_or_cell")]
    source = {
        "file_id": first.get("file_id") or "",
        "file_name": first.get("file_name") or "",
        "clause_path": normalize_clause_path(first.get("clause_path")),
        "page_start": min(located_starts) if located_starts else None,
        "page_end": max(located_ends) if located_ends else None,
        "page_source": first.get("page_source") or "unavailable",
        "page_unlocated_count": sum(ref.get("page_start") is None for ref in refs),
        "block_start": block_locations[0] if block_locations else "",
        "block_end": block_locations[-1] if block_locations else "",
        "table_name": first.get("table_name") or "",
        "table_range": first.get("table_range") or "",
        "sheet_name": first.get("sheet_name") or "",
    }
    return source


def primary_source_ref(point: dict[str, Any]) -> dict[str, Any]:
    refs = list(point.get("source_refs") or [])
    return refs[0] if refs else {}


def normalize_clause_path(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [item.strip() for item in str(value or "").split(">") if item.strip()]


def render_technical_outline_markdown(outline: dict[str, Any]) -> str:
    lines = ["# 技术标总响应大纲", ""]
    point_index = {
        point["point_id"]: point
        for chapter in outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    }
    blocks_by_chapter: dict[str, list[dict[str, Any]]] = {}
    for block in outline.get("display_blocks") or []:
        blocks_by_chapter.setdefault(block["chapter_id"], []).append(block)
    for chapter_index, chapter in enumerate(outline.get("chapters") or [], start=1):
        lines.extend([f"## {chapter_index}. {chapter.get('title')}", ""])
        for block in blocks_by_chapter.get(chapter["chapter_id"], []):
            lines.extend([f"### {block.get('title')}", ""])
            lines.extend(render_display_block_markdown(block, point_index))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_display_block_markdown(block: dict[str, Any], point_index: dict[str, dict[str, Any]]) -> list[str]:
    points = [point_index[point_id] for point_id in block.get("response_point_ids") or []]
    lines: list[str] = []
    if block.get("block_type") == "table":
        table = points[0].get("table") or {}
        table_rows = table.get("rows") or []
        if table.get("has_merged_cells"):
            lines.extend(render_html_table(table_rows))
        else:
            matrix = structured_rows_to_matrix(table_rows, int(table.get("column_count") or 0)) if table_rows and isinstance(table_rows[0], dict) else table_rows
            lines.extend(render_markdown_table(matrix))
    elif block.get("block_type") == "list_group":
        lines.extend(f"- {point.get('original_text')}" for point in points)
    else:
        for point in points:
            text = str(point.get("original_text") or "")
            if point.get("continuation_of_point_id"):
                lines.extend(f"   {line}" if line else "" for line in text.splitlines())
            else:
                lines.extend(text.splitlines())
            lines.append("")
        while lines and not lines[-1]:
            lines.pop()
    lines.extend(["", "**来源**", ""])
    lines.extend(render_display_source_markdown(block.get("source_ref") or {}))
    return lines


def render_display_source_markdown(source: dict[str, Any]) -> list[str]:
    lines = [f"- 文件：{source.get('file_name')}"]
    if source.get("clause_path"):
        lines.append(f"- 章节路径：{' > '.join(source.get('clause_path') or [])}")
    lines.append(f"- 页码：{format_display_page_location(source)}")
    if source.get("sheet_name"):
        lines.append(f"- 工作表：{source.get('sheet_name')}")
    if source.get("table_name"):
        lines.append(f"- 表格：{source.get('table_name')}")
    if source.get("table_range"):
        lines.append(f"- 表格范围：{source.get('table_range')}")
    start = str(source.get("block_start") or "")
    end = str(source.get("block_end") or "")
    if start:
        label = "表格定位" if start.startswith("table_") and start == end else "段落范围"
        lines.append(f"- {label}：{start if not end or end == start else f'{start}—{end}'}")
    return lines


def render_technical_outline_html(outline: dict[str, Any]) -> str:
    point_index = {
        point["point_id"]: point
        for chapter in outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    }
    blocks_by_chapter: dict[str, list[dict[str, Any]]] = {}
    for block in outline.get("display_blocks") or []:
        blocks_by_chapter.setdefault(block["chapter_id"], []).append(block)
    body = ["<h1>技术标总响应大纲</h1>"]
    for chapter_number, chapter in enumerate(outline.get("chapters") or [], start=1):
        body.append(f"<h2>{chapter_number}. {html.escape(str(chapter.get('title') or ''))}</h2>")
        for block in blocks_by_chapter.get(chapter["chapter_id"], []):
            body.append(f"<section><h3>{html.escape(str(block.get('title') or ''))}</h3>")
            body.extend(render_display_block_html(block, point_index))
            body.append("</section>")
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>技术标总响应大纲预览</title>",
            "<style>",
            "body { font-family: 'Microsoft YaHei', sans-serif; line-height: 1.65; margin: 24px auto; max-width: 1280px; padding: 0 20px; color: #222; }",
            "table { border-collapse: collapse; width: 100%; }",
            "th, td { border: 1px solid #999; padding: 6px; vertical-align: top; }",
            ".table-wrapper { overflow-x: auto; margin: 12px 0; }",
            ".continuation { margin-left: 2em; }",
            ".source { background: #f6f6f6; border-left: 3px solid #999; padding: 8px 12px; margin: 12px 0 24px; }",
            ".source ul { margin: 0; }",
            "</style>",
            "</head>",
            "<body>",
            *body,
            "</body>",
            "</html>",
        ]
    ) + "\n"


def render_display_block_html(block: dict[str, Any], point_index: dict[str, dict[str, Any]]) -> list[str]:
    points = [point_index[point_id] for point_id in block.get("response_point_ids") or []]
    lines: list[str] = []
    if block.get("block_type") == "table":
        table = points[0].get("table") or {}
        rows = table.get("rows") or []
        if rows and not isinstance(rows[0], dict):
            rows = matrix_to_structured_rows(rows, list(range(1, len(rows) + 1)))
        lines.extend(["<div class=\"table-wrapper\">", *render_html_table(rows), "</div>"])
    elif block.get("block_type") == "list_group":
        lines.append("<ul>")
        lines.extend(f"<li>{html.escape(str(point.get('original_text') or ''))}</li>" for point in points)
        lines.append("</ul>")
    else:
        for point in points:
            css_class = ' class="continuation"' if point.get("continuation_of_point_id") else ""
            content = html.escape(str(point.get("original_text") or "")).replace("\n", "<br>")
            lines.append(f"<p{css_class}>{content}</p>")
    source_lines = render_display_source_markdown(block.get("source_ref") or {})
    lines.extend(["<div class=\"source\"><strong>来源</strong><ul>"])
    lines.extend(f"<li>{html.escape(line.removeprefix('- '))}</li>" for line in source_lines)
    lines.append("</ul></div>")
    return lines


def format_display_page_location(source: dict[str, Any]) -> str:
    base = format_page_location(source)
    if int(source.get("page_unlocated_count") or 0) and base != "未可靠定位":
        return f"部分未可靠定位，{base}"
    return base


def validate_outline(outline: dict[str, Any]) -> None:
    if outline.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected technical outline schema version")
    for chapter in outline.get("chapters") or []:
        if chapter.get("source_category") not in CATEGORY_ORDER:
            raise ValueError(f"invalid source category: {chapter.get('source_category')}")
        for point in chapter.get("response_points") or []:
            if not str(point.get("original_text") or "").strip():
                raise ValueError(f"empty original_text: {point.get('point_id')}")
            refs = list(point.get("source_refs") or [])
            if not refs or any(not ref.get("file_name") for ref in refs):
                raise ValueError(f"missing source file: {point.get('point_id')}")
            if any(not (ref.get("clause_path") or ref.get("row_or_cell") or ref.get("page")) for ref in refs):
                raise ValueError(f"source location is too vague: {point.get('point_id')}")
    points = {
        point["point_id"]: point
        for chapter in outline.get("chapters") or []
        for point in chapter.get("response_points") or []
    }
    for block in outline.get("display_blocks") or []:
        point_ids = list(block.get("response_point_ids") or [])
        if not point_ids or any(point_id not in points for point_id in point_ids):
            raise ValueError(f"display block references unknown response point: {block.get('block_id')}")
        files = {primary_source_ref(points[point_id]).get("file_id") for point_id in point_ids}
        paths = {tuple(normalize_clause_path(primary_source_ref(points[point_id]).get("clause_path"))) for point_id in point_ids}
        if len(files) != 1 or len(paths) != 1:
            raise ValueError(f"display block crosses source boundaries: {block.get('block_id')}")


def validate_clean_output(outline: dict[str, Any], markdown: str) -> None:
    prohibited = [
        "当前知识库状态",
        "候选人员",
        "推荐部门",
        "缺失材料",
        "材料准备清单",
        "人工复核问题",
        "technical_solution_inputs",
        "project_leader_resume",
        "company_contract",
        "评分项行动分析",
        "建议写作结构",
        "评分关注",
        "最高限价",
        "价格评分",
    ]
    rendered = json.dumps(outline, ensure_ascii=False) + markdown
    found = [term for term in prohibited if term in rendered]
    if found:
        raise ValueError(f"technical outline contains prohibited content: {found}")


def build_statistics(
    source_files: list[dict[str, Any]],
    raw_points: list[dict[str, Any]],
    deduped: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    display_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source_file_count": len(source_files),
        "parsed_source_file_count": sum(item.get("parse_status") == "parsed" for item in source_files),
        "chapter_count": len(chapters),
        "response_point_count": len(deduped),
        "display_block_count": len(display_blocks or []),
        "table_point_count": sum(item.get("content_type") == "table" for item in deduped),
        "exact_duplicate_count": len(raw_points) - len(deduped),
        "response_points_by_source_category": {
            category: sum(item.get("source_category") == category for item in deduped)
            for category in CATEGORY_ORDER
        },
    }


def clean_original_text(value: str) -> str:
    lines: list[str] = []
    for raw in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \u3000]+", " ", raw).strip()
        if not line or re.fullmatch(r"===\s*(?:Page|Table|Sheet)[^=]*===", line, flags=re.I):
            continue
        if re.fullmatch(r"(?:第\s*)?\d+\s*(?:页)?", line):
            continue
        if re.search(r"\.{4,}\s*\d+\s*$", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def infer_content_type(text: str, clause: dict[str, Any]) -> str:
    if "偏差表" in text or "应提交的文件" in text or "附件" in str(clause.get("heading") or ""):
        return "attachment_requirement"
    if len(text.splitlines()) > 1 and any(re.match(r"^(?:\d+[.、]|（[一二三四五六七八九十\d]+）|\([0-9]+\))", line) for line in text.splitlines()[1:]):
        return "numbered_list"
    if has_any(text, ["格式", "填写", "表格"]):
        return "form"
    return "paragraph"


def detect_mandatory_marker(text: str) -> str:
    return "".join(marker for marker in MANDATORY_MARKERS if marker in text)


def extract_original_number(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    match = re.match(r"^(\*?[0-9]+(?:\.[0-9]+)*|第[一二三四五六七八九十百\d]+[章节条]|[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十\d]+）)", first)
    return match.group(1) if match else ""


def table_titles_by_index(blocks: list[dict[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    last_short_text = ""
    for block in sorted(blocks, key=lambda item: int(item.get("block_index") or 0)):
        if block.get("block_type") == "paragraph":
            text = str(block.get("text") or "").strip()
            if text and len(text) <= 160:
                last_short_text = text
        elif block.get("block_type") == "table":
            index = int(block.get("table_index") or 0)
            if index:
                result[index] = last_short_text or f"表{index}"
    return result


def remove_empty_table_edges(rows: list[list[str]], row_numbers: list[int]) -> tuple[list[list[str]], list[int]]:
    kept = [(row, number) for row, number in zip(rows, row_numbers) if any(cell.strip() for cell in row)]
    if not kept:
        return [], []
    width = max(len(row) for row, _ in kept)
    padded = [row + [""] * (width - len(row)) for row, _ in kept]
    nonempty_columns = [index for index in range(width) if any(row[index].strip() for row in padded)]
    return [[row[index] for index in nonempty_columns] for row in padded], [number for _, number in kept]


def expand_merged_cells(rows: list[list[str]], row_numbers: list[int], merged_cells: list[str]) -> None:
    if not rows or not merged_cells:
        return
    try:
        from openpyxl.utils.cell import range_boundaries
    except ImportError:
        return
    row_lookup = {number: index for index, number in enumerate(row_numbers)}
    for cell_range in merged_cells:
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        source_index = row_lookup.get(min_row)
        if source_index is None or min_col - 1 >= len(rows[source_index]):
            continue
        value = rows[source_index][min_col - 1]
        for row_number in range(min_row, max_row + 1):
            target_index = row_lookup.get(row_number)
            if target_index is None:
                continue
            for column in range(min_col, max_col + 1):
                if column - 1 < len(rows[target_index]) and not rows[target_index][column - 1]:
                    rows[target_index][column - 1] = value


def render_markdown_table(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    escaped = [[markdown_cell(cell) for cell in row] for row in padded]
    return [
        "| " + " | ".join(escaped[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *["| " + " | ".join(row) + " |" for row in escaped[1:]],
    ]


def render_html_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["<table>", "  <tbody>"]
    for row in rows:
        lines.append("    <tr>")
        for cell in row.get("cells") or []:
            attributes = []
            if int(cell.get("rowspan") or 1) > 1:
                attributes.append(f'rowspan="{int(cell["rowspan"])}"')
            if int(cell.get("colspan") or 1) > 1:
                attributes.append(f'colspan="{int(cell["colspan"])}"')
            attribute_text = f" {' '.join(attributes)}" if attributes else ""
            value = html.escape(str(cell.get("text") or "")).replace("\n", "<br>")
            lines.append(f"      <td{attribute_text}>{value}</td>")
        lines.append("    </tr>")
    lines.extend(["  </tbody>", "</table>"])
    return lines


def structured_rows_to_matrix(rows: list[dict[str, Any]], column_count: int) -> list[list[str]]:
    if not rows:
        return []
    width = column_count or max(
        (int(cell.get("column") or 0) + int(cell.get("colspan") or 1) - 1 for row in rows for cell in row.get("cells") or []),
        default=1,
    )
    matrix: list[list[str]] = []
    for row in rows:
        values = [""] * width
        for cell in row.get("cells") or []:
            column = int(cell.get("column") or 0)
            if 1 <= column <= width:
                values[column - 1] = clean_cell(cell.get("text"))
        matrix.append(values)
    return matrix


def matrix_to_structured_rows(rows: list[list[str]], row_numbers: list[int]) -> list[dict[str, Any]]:
    return [
        {
            "row": row_number,
            "cells": [
                {
                    "text": clean_cell(value),
                    "row": row_number,
                    "column": column,
                    "rowspan": 1,
                    "colspan": 1,
                    "is_merge_anchor": True,
                }
                for column, value in enumerate(row, start=1)
            ],
        }
        for row_number, row in zip(row_numbers, rows)
    ]


def format_page_location(source: dict[str, Any]) -> str:
    start = source.get("page_start")
    end = source.get("page_end")
    if start is None and source.get("page") is not None:
        start = end = source.get("page")
    if start is None:
        return "未可靠定位"
    if end is None or int(end) == int(start):
        return f"第{int(start)}页"
    return f"第{int(start)}—{int(end)}页"


def clean_heading_number(value: str) -> str:
    return re.sub(r"^(?:第[一二三四五六七八九十百\d]+部分|[一二三四五六七八九十]+、)\s*", "", str(value or "")).strip()


def render_quote(text: str) -> list[str]:
    return [">" if not line else f"> {line}" for line in text.splitlines()]


def markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def exact_content_key(point: dict[str, Any]) -> str:
    if point.get("content_type") == "table":
        source = json.dumps((point.get("table") or {}).get("rows") or [], ensure_ascii=False, separators=(",", ":"))
    else:
        source = str(point.get("original_text") or "")
    return normalize_layout(source)


def normalize_layout(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def unique_source_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for ref in refs:
        key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def table_range(rows: list[list[str]], row_numbers: list[int]) -> str:
    if not rows or not row_numbers:
        return ""
    return f"A{min(row_numbers)}:{column_name(max(len(row) for row in rows))}{max(row_numbers)}"


def column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def page_for_offset(ranges: list[dict[str, Any]], offset: int) -> int | None:
    for item in ranges:
        if int(item["start"]) <= offset <= int(item["end"]):
            return int(item["page"])
    return None


def domain_from_category(category: str) -> str:
    return {
        "project_requirement": "project_requirement",
        "technical_score": "technical_score",
        "technical_format": "technical_format",
        "technical_specification": "overall_technical",
        "procurement_document": "technical_other",
        "technical_attachment": "technical_other",
    }.get(category, "technical_other")


def build_chapter_sections(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    indexes: dict[tuple[str, ...], dict[str, Any]] = {}
    for point in points:
        path = [str(item) for item in point.get("section_path") or [] if str(item).strip()]
        for level in range(1, len(path) + 1):
            key = tuple(path[:level])
            section = indexes.get(key)
            if section is None:
                section = {
                    "section_id": f"SEC-{len(sections) + 1:03d}",
                    "title": path[level - 1],
                    "level": level,
                    "path": list(key),
                    "response_point_ids": [],
                }
                indexes[key] = section
                sections.append(section)
            if level == len(path):
                section["response_point_ids"].append(point["point_id"])
    return sections


def chapter_title(category: str, original_heading: str) -> str:
    label = CATEGORY_LABELS[category]
    heading = re.sub(r"\s+", " ", str(original_heading or "")).strip()
    return f"{label}—{heading}" if heading and heading != label else label


def is_price_or_business_title(value: str) -> bool:
    return has_any(str(value or ""), PRICE_TERMS + BUSINESS_TERMS)


def has_any(value: str, terms: list[str]) -> bool:
    text = str(value or "")
    return any(term in text for term in terms)


def clean_cell(value: Any) -> str:
    return re.sub(r"[ \u3000]+", " ", str(value or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def unique_strings(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]", "_", value) or "selected_package"

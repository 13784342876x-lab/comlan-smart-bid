from __future__ import annotations

import re
from typing import Any

from app.structured_clause_index import (
    build_applicability_hint,
    build_source_location,
    build_table_context_from_line,
    classify_structured_clause_type,
    detect_sheet_name,
    infer_document_region,
    quality_flags_for_clause,
)
from app.tender_source_guard import tender_source_type


HEADING_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("chapter", re.compile(r"^第[一二三四五六七八九十百\d]+章[、\s]*(.+)?$"), 1),
    ("section", re.compile(r"^第[一二三四五六七八九十百\d]+节[、\s]*(.+)?$"), 2),
    ("article", re.compile(r"^第[一二三四五六七八九十百\d]+条[、\s]*(.+)?$"), 3),
    ("roman", re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[、.．]\s*(.+)$"), 1),
    ("chinese", re.compile(r"^[一二三四五六七八九十]+[、.．]\s*(.+)$"), 2),
    ("paren_chinese", re.compile(r"^（[一二三四五六七八九十]+）\s*(.+)$"), 3),
    ("appendix", re.compile(r"^附件[一二三四五六七八九十\dA-Za-z-]*[：:、\s]*(.+)?$"), 1),
    ("decimal", re.compile(r"^\d+(?:\.\d+){1,4}[、.．]?\s*(.+)$"), 4),
    ("number", re.compile(r"^\d+[、.．]\s*(.+)$"), 4),
]

HEADING_ONLY_PATTERNS = [
    re.compile(r"结束\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[、.．]"),
    re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[、.．].{0,40}[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[、.．]"),
    re.compile(r"^目\s*录$"),
]

INVALID_CLAUSE_PATTERNS = [
    re.compile(r"封\s*面"),
    re.compile(r"目\s*录"),
    re.compile(r"附件\d*[：:、\s]*项目招标文件模板"),
    re.compile(r"特别声明及免责条款"),
    re.compile(r"免责条款"),
    re.compile(r"解释权声明"),
    re.compile(r"最终解释权"),
    re.compile(r"详见技术规范书"),
    re.compile(r"详见技术标准"),
    re.compile(r"详见附件"),
    re.compile(r"^\s*第\s*\d+\s*页\s*$"),
]

REQUIREMENT_HINTS = [
    "应",
    "须",
    "需",
    "要求",
    "包括",
    "提供",
    "负责",
    "完成",
    "不得",
    "服务",
    "交付",
    "验收",
    "人员",
    "业绩",
]


def build_clause_index(
    file_id: str,
    file_name: str,
    parsed_text: str,
    file_meta: dict[str, Any] | None = None,
    lot_name: str = "",
) -> list[dict[str, Any]]:
    file_meta = file_meta or {"id": file_id, "original_name": file_name, "parsed_text": parsed_text}
    source_type = tender_source_type(file_meta)
    if source_type == "procurement_package_table":
        table_clauses = build_procurement_package_table_index(file_id, file_name, parsed_text, source_type, lot_name)
        if table_clauses:
            return table_clauses

    lines = split_lines_with_offsets(parsed_text)
    if not lines:
        return []

    heading_rows: list[dict[str, Any]] = []
    for row in lines:
        if is_toc_heading_line(row["text"]):
            continue
        heading = detect_heading(row["text"])
        if heading:
            heading_rows.append({**row, **heading})

    if not heading_rows:
        text = parsed_text.strip()
        return [
            build_clause(
                file_id=file_id,
                file_name=file_name,
                source_type=source_type,
                clause_index=1,
                heading_path=[],
                clause_title=file_name,
                clause_text=text,
                start_offset=0,
                end_offset=len(parsed_text),
                paragraph_index=0,
            )
        ]

    clauses: list[dict[str, Any]] = []
    heading_path: list[str] = []
    for index, heading in enumerate(heading_rows):
        start = heading["start"]
        end = heading_rows[index + 1]["start"] if index + 1 < len(heading_rows) else len(parsed_text)
        level = int(heading["level"])
        title = str(heading["title"])
        heading_path = heading_path[: max(level - 1, 0)]
        while len(heading_path) < level - 1:
            heading_path.append("")
        heading_path.append(title)
        clause_text = parsed_text[start:end].strip()
        clauses.append(
            build_clause(
                file_id=file_id,
                file_name=file_name,
                source_type=source_type,
                clause_index=len(clauses) + 1,
                heading_path=[item for item in heading_path if item],
                clause_title=title,
                clause_text=clause_text,
                start_offset=start,
                end_offset=end,
                paragraph_index=heading.get("paragraph_index"),
            )
        )
    return clauses


def build_procurement_package_table_index(
    file_id: str,
    file_name: str,
    parsed_text: str,
    source_type: str,
    lot_name: str = "",
) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    sheet_name = ""
    for row_index, row in enumerate(split_lines_with_offsets(parsed_text), start=1):
        line = row["text"]
        detected_sheet = detect_sheet_name(line)
        if detected_sheet:
            sheet_name = detected_sheet
            continue
        if not should_index_table_line(line, lot_name):
            continue
        table_context = build_table_context_from_line(line, sheet_name, row_index, lot_name)
        title = table_context.get("matched_lot_name") or table_context.get("matched_package_no") or f"表格行{row_index}"
        clauses.append(
            build_clause(
                file_id=file_id,
                file_name=file_name,
                source_type=source_type,
                clause_index=len(clauses) + 1,
                heading_path=[sheet_name] if sheet_name else [],
                clause_title=str(title),
                clause_text=line,
                start_offset=row["start"],
                end_offset=row["end"],
                paragraph_index=row_index,
                table_context=table_context,
            )
        )
    return clauses


def should_index_table_line(line: str, lot_name: str = "") -> bool:
    text = normalize_text(line)
    if len(text) < 8:
        return False
    if lot_name and lot_name in text:
        return True
    if re.search(r"042513\d{7}-\d{4}", text):
        return True
    if any(term in text for term in ["专用资格要求", "最高限价", "包名称", "分标编号", "分标名称", "施工招标计划表"]):
        return True
    return False


def build_heading_outline(file_id: str, file_name: str, parsed_text: str, limit: int = 200) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for row in split_lines_with_offsets(parsed_text):
        heading = detect_heading(row["text"])
        if not heading:
            continue
        outline.append(
            {
                "file_id": file_id,
                "file_name": file_name,
                "level": heading["level"],
                "heading_type": heading["heading_type"],
                "title": heading["title"],
                "offset": row["start"],
            }
        )
        if len(outline) >= limit:
            break
    return outline


def detect_heading(line: str) -> dict[str, Any] | None:
    text = normalize_line(line)
    if not text or len(text) > 260:
        return None
    for heading_type, pattern, level in HEADING_PATTERNS:
        match = pattern.match(text)
        if match:
            title = clean_heading_title(match.group(0).strip())
            return {"heading_type": heading_type, "level": level, "title": title}
    return None


def split_lines_with_offsets(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        start = cursor
        cursor += len(raw_line)
        clean = raw_line.strip()
        if clean:
            rows.append({"text": clean, "start": start, "end": cursor, "paragraph_index": len(rows)})
    if not rows and text.strip():
        rows.append({"text": text.strip(), "start": 0, "end": len(text)})
    return rows


def build_clause(
    file_id: str,
    file_name: str,
    source_type: str,
    clause_index: int,
    heading_path: list[str],
    clause_title: str,
    clause_text: str,
    start_offset: int,
    end_offset: int,
    paragraph_index: int | None = None,
    table_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = clean_clause_text(normalize_text(clause_text))
    is_invalid = detect_invalid_clause(normalized, clause_title)
    is_heading_only = detect_heading_only(normalized)
    evidence_quality = "invalid_clause" if is_invalid else "heading_only" if is_heading_only else "candidate_clause"
    section_path = [item for item in heading_path if item]
    clause_type = classify_structured_clause_type(normalized, clause_title, section_path, source_type)
    if clause_type == "unknown":
        clause_type = classify_clause_type(normalized, clause_title)
    section_path = normalize_section_path_for_clause(section_path, clause_type)
    heading_path = list(section_path)
    document_region = infer_document_region(section_path, source_type)
    source_location = build_source_location(
        paragraph_index=paragraph_index,
        table_index=(table_context or {}).get("table_index") if table_context else None,
        row_index=(table_context or {}).get("row_index") if table_context else None,
        sheet_name=(table_context or {}).get("sheet_name") if table_context else None,
    )
    return {
        "clause_id": f"{file_id}::c{clause_index:04d}",
        "file_id": file_id,
        "file_name": file_name,
        "source_file_id": file_id,
        "source_file_name": file_name,
        "source_type": source_type,
        "document_region": document_region,
        "section_path": section_path,
        "heading_path": heading_path,
        "heading": clause_title,
        "clause_no": extract_clause_no(clause_title),
        "clause_title": clause_title,
        "text": normalized,
        "normalized_text": normalized,
        "clause_text": normalized,
        "table_context": table_context,
        "source_location": source_location,
        "quality_flags": quality_flags_for_clause(normalized, document_region, clause_type),
        "applicability_hint": build_applicability_hint(clause_type),
        "start_offset": start_offset,
        "end_offset": end_offset,
        "text_len": len(normalized),
        "is_heading_only": is_heading_only,
        "is_invalid": is_invalid,
        "evidence_quality": evidence_quality,
        "clause_type": clause_type,
    }


def normalize_section_path_for_clause(section_path: list[str], clause_type: str) -> list[str]:
    if clause_type != "business_quality_commitment" or not section_path:
        return section_path
    chapter = next((item for item in section_path if "第六章" in str(item)), section_path[0])
    supplement = next((item for item in section_path if "补充文件" in str(item)), "（三）补充文件")
    title = section_path[-1]
    return [chapter, "二、商务文件", supplement, title]


def extract_clause_no(title: str) -> str:
    text = normalize_text(title)
    match = re.match(r"^(\d+(?:\.\d+)*|第[一二三四五六七八九十百\d]+[章节条]|[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十]+）)", text)
    return match.group(1) if match else ""


def detect_heading_only(text: str) -> bool:
    compact = normalize_text(text)
    if len(compact) < 45 and not has_requirement_hint(compact):
        return True
    if any(pattern.search(compact) for pattern in HEADING_ONLY_PATTERNS):
        if not has_requirement_hint(compact):
            return True
    heading_count = len(re.findall(r"(第[一二三四五六七八九十百\d]+[章节条]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[、.．]|附件[一二三四五六七八九十\dA-Za-z-]*)", compact))
    if heading_count >= 2 and len(compact) < 120 and not has_requirement_hint(compact):
        return True
    return False


def detect_invalid_clause(text: str, title: str = "") -> bool:
    compact = normalize_text(f"{title} {text}")
    if any(pattern.search(compact) for pattern in INVALID_CLAUSE_PATTERNS):
        return True
    if len(compact) < 160 and ("国网" in compact or "有限公司" in compact) and "目 录" in compact:
        return True
    if "详见技术规范书" in compact and len(compact) < 220:
        return True
    return False


def classify_clause_type(text: str, title: str = "") -> str:
    value = f"{title} {text}"
    if any(key in value for key in ["评分", "评标", "得分", "扣分", "分值"]):
        return "scoring_clause"
    if any(key in value for key in ["报价", "价格", "评标价", "基准价"]):
        return "price_clause"
    if any(key in value for key in ["合同", "委托人", "专用条款", "通用条款"]):
        return "contract_clause"
    if any(key in value for key in ["响应文件", "应答文件", "投标文件", "技术部分"]):
        return "response_file_requirement"
    if any(key in value for key in ["项目负责人", "项目经理", "项目团队", "人员", "简历"]):
        return "personnel_requirement"
    if any(key in value for key in ["业绩", "案例", "合同证明", "验收证明", "项目经验"]):
        return "performance_requirement"
    if any(key in value for key in ["服务范围", "服务内容", "服务要求", "售后", "运维", "培训"]):
        return "service_requirement"
    if any(key in value for key in ["技术规范", "技术要求", "技术标准", "实施方案", "质量", "进度", "安全", "保密"]):
        return "technical_requirement"
    if "附件" in value:
        return "appendix"
    return "unknown"


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def clean_heading_title(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"^(\d+(?:\.\d+)+)([^\s.、．])", r"\1 \2", text)
    text = re.sub(r"^(\d+)[.．](?!\d)\s*", r"\1. ", text)
    text = re.sub(r"\s+\d+\s*$", "", text)
    return normalize_text(text)


def clean_clause_text(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"^(\d+(?:\.\d+)+)([^\s.、．])", r"\1 \2", text)
    text = re.sub(r"^(\d+[.．]\s*.{1,80}?)\s+\d+(\s+)", lambda m: clean_heading_title(m.group(1)) + m.group(2), text)
    text = re.sub(r"^(\d+(?:\.\d+)+\s*.{1,80}?)\s+\d+(\s+)", lambda m: clean_heading_title(m.group(1)) + m.group(2), text)
    return normalize_text(text)


def is_toc_heading_line(value: str) -> bool:
    text = normalize_line(value)
    if not text:
        return False
    if text in {"目 录", "目录"}:
        return True
    if re.match(r"^\d+(?:\.\d+)*[^\n]{1,60}\s+\d+$", text):
        return True
    if re.match(r"^第[一二三四五六七八九十\d]+[章节条][^\n]{0,60}\s+\d+$", text):
        return True
    return False


def has_requirement_hint(text: str) -> bool:
    return any(hint in text for hint in REQUIREMENT_HINTS)

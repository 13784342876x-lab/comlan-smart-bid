from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.material_matcher import TENDER_PROJECTS_PATH, find_project_by_lot_name, load_tender_projects, save_tender_projects
from app.metadata_store import MetadataStore
from app.tender_source_guard import is_full_tender_document, is_tender_notice_file, is_tender_source_file, tender_source_type
from config import METADATA_PATH


FIELD_NAMES = [
    "tender_no",
    "project_name",
    "lot_no",
    "lot_name",
    "package_no",
    "package_name",
    "technical_topic_name",
    "tenderee",
    "tender_agency",
    "service_period",
    "max_price",
    "special_qualification_requirement",
]

FIELD_LABELS = {
    "tender_no": "招标编号",
    "project_name": "项目名称",
    "lot_no": "分标编号",
    "lot_name": "分标名称",
    "package_no": "包号",
    "package_name": "包名称",
    "technical_topic_name": "包/技术专题名称",
    "tenderee": "招标人",
    "tender_agency": "招标代理机构",
    "service_period": "服务期限",
    "max_price": "最高限价",
    "special_qualification_requirement": "专用资格要求",
}

FIELD_PATTERNS = {
    "tender_no": [r"(?:招标编号|采购编号|项目编号)\s*[:：]\s*([A-Za-z0-9_\-（）()\/.]{4,80})"],
    "project_name": [r"(?:招标项目名称|采购项目名称|项目名称)\s*[:：]\s*([^\n\r；;]{4,100})"],
    "lot_no": [r"(?:分标编号|标段号|标段编号)\s*[:：]\s*([A-Za-z0-9_\-（）()\/.]{1,80})"],
    "lot_name": [r"(?:分标名称|标段名称)\s*[:：]\s*([^\n\r；;]{2,100})"],
    "package_no": [r"(?:包号|包件号|包编号)\s*[:：]\s*([A-Za-z0-9_\-（）()\/.]{1,80})"],
    "package_name": [r"(?:包名称|包件名称)\s*[:：]\s*([^\n\r；;]{2,100})"],
    "technical_topic_name": [r"(?:技术专题名称|专题名称|服务内容名称)\s*[:：]\s*([^\n\r；;]{4,100})"],
    "tenderee": [r"(?:招标人|采购人|委托人)\s*[:：]\s*([^\n\r；;]{2,100})", r"招标人为([^\n\r，。；;]{2,100})"],
    "tender_agency": [r"(?:招标代理机构|采购代理机构)\s*[:：]\s*([^\n\r；;]{2,100})", r"委托([^\n\r，。；;]{2,100})为招标代理机构"],
    "service_period": [r"(?:服务期限|工期)\s*[:：]\s*([^\n\r；;]{2,100})"],
    "max_price": [r"(?:最高限价|最高投标限价)\s*[:：]\s*([^\n\r；;]{1,80})"],
    "special_qualification_requirement": [r"(?:专用资格要求|资质业绩要求)\s*[:：]\s*([^\n\r；;]{2,160})"],
}

PROJECT_NAME_FALLBACK = "未定位到项目总名称，需从招标公告或招标文件正文确认"

REDIRECT_REFERENCE_VALUES = {
    "详见招标公告附件",
    "见招标公告附件",
    "详见附件",
    "见附件",
    "详见招标需求一览表",
    "见招标需求一览表",
    "详见投标人须知前附表",
    "见投标人须知前附表",
}


def extract_tender_metadata(
    lot_name: str,
    metadata: dict[str, Any] | None = None,
    metadata_path: Path = METADATA_PATH,
    tender_projects_path: Path = TENDER_PROJECTS_PATH,
    save: bool = True,
) -> dict[str, Any]:
    metadata = metadata or MetadataStore(metadata_path).load()
    sources = inspect_tender_source_files(lot_name, metadata)
    candidates = collect_field_candidates(sources, lot_name, metadata)
    fields = {field: choose_field(field, candidates.get(field, [])) for field in FIELD_NAMES}
    manual_confirm_items = build_manual_confirm_items(fields)
    result = {
        "version": "v0",
        "lot_name": lot_name,
        "fields": fields,
        "candidate_sources": sources,
        "field_candidates": candidates,
        "manual_confirm_items": manual_confirm_items,
    }

    if save:
        write_tender_metadata(lot_name, result, metadata, metadata_path, tender_projects_path)
    return result


def inspect_tender_metadata(
    lot_name: str,
    tender_projects_path: Path = TENDER_PROJECTS_PATH,
) -> dict[str, Any]:
    projects_data = load_tender_projects(tender_projects_path)
    project = find_project_by_lot_name(projects_data, lot_name)
    value = project.get("tender_project_metadata")
    if not isinstance(value, dict):
        raise ValueError(f"tender_project_metadata not found for lot_name={lot_name}; run extract-tender-metadata first")
    return value


def inspect_tender_source_files(lot_name: str, metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    metadata = metadata or MetadataStore().load()
    rows: list[dict[str, Any]] = []
    for record in list(metadata.get("files") or []):
        if not isinstance(record, dict) or not is_tender_source_file(record):
            continue
        source_type, priority = source_type_and_priority(record)
        parsed_text_len = len(str(record.get("parsed_text") or ""))
        tables_count = len(list(record.get("tables") or []))
        if source_type == "procurement_package_table" and parsed_text_len == 0 and tables_count == 0:
            continue
        rows.append(
            {
                "file_id": record.get("id") or record.get("file_id"),
                "source_file_name": record.get("original_name") or record.get("name"),
                "source_type": source_type,
                "priority": priority,
                "parsed_text_len": parsed_text_len,
                "tables_count": tables_count,
                "is_full_tender_document": is_full_tender_document(record),
                "is_tender_notice": is_tender_notice_file(record),
                "category": record.get("category"),
                "tags": list(record.get("tags") or []),
            }
        )
    return sorted(rows, key=lambda item: (priority_value(item), -int(item.get("parsed_text_len") or 0), str(item.get("source_file_name") or "")))


def collect_field_candidates(sources: list[dict[str, Any]], lot_name: str, metadata: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    metadata = metadata or MetadataStore().load()
    records = {str(item.get("id") or item.get("file_id") or ""): item for item in list(metadata.get("files") or []) if isinstance(item, dict)}
    by_field: dict[str, list[dict[str, Any]]] = {field: [] for field in FIELD_NAMES}
    for source in sources:
        record = records.get(str(source.get("file_id") or ""))
        if not record:
            continue
        text = str(record.get("parsed_text") or "")
        for field, patterns in FIELD_PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text[:30000]):
                    value = clean_value(match.group(1))
                    if not valid_field_value(field, value):
                        continue
                    by_field[field].append(candidate(field, value, source, match_evidence(field, value, match.group(0)), "parsed_text"))
        if source.get("source_type") == "tender_notice":
            notice_title = project_name_from_tender_notice_title(text)
            if notice_title:
                by_field["project_name"].append(candidate("project_name", notice_title["value"], source, notice_title["evidence"], "notice_title"))
        topic = technical_topic_from_filename(str(record.get("original_name") or ""))
        if topic and source.get("source_type") == "technical_spec":
            by_field["technical_topic_name"].append(candidate("technical_topic_name", topic, source, str(record.get("original_name") or ""), "filename"))
            by_field["package_name"].append(candidate("package_name", topic, source, str(record.get("original_name") or ""), "filename"))
        for field, item in extract_lot_package_fields(record, source, lot_name).items():
            by_field[field].append(item)

    if lot_name and not by_field["package_no"]:
        source = {"file_id": None, "source_file_name": None, "source_type": "cli_lot_name", "priority": 8}
        by_field["package_no"].append(candidate("package_no", lot_name, source, f"--lot-name {lot_name}", "cli"))
    return {field: dedupe_candidates(items) for field, items in by_field.items()}


def choose_field(field: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return empty_field(True)

    allowed = list(candidates)
    if field == "project_name":
        allowed = [
            item for item in candidates
            if priority_value(item) <= 4 and str(item.get("source_type") or "") != "technical_spec"
        ]
        notice_items = [item for item in allowed if item.get("source_type") == "tender_notice"]
        if notice_items:
            allowed = notice_items
        if not allowed:
            return empty_field(True)
    if field == "special_qualification_requirement":
        concrete = [item for item in allowed if not is_redirect_reference(item.get("value"))]
        if concrete:
            allowed = concrete

    ordered = sorted(allowed, key=lambda item: (priority_value(item), -float(item.get("confidence") or 0.0)))
    selected = dict(ordered[0])
    selected_priority = priority_value(selected)
    distinct_values = {
        normalize_compare(item.get("value"))
        for item in ordered
        if item.get("value") and priority_value(item) <= selected_priority
    }
    if len(distinct_values) > 1:
        selected["requires_manual_confirm"] = True
        selected["confidence"] = min(float(selected.get("confidence") or 0.0), 0.72)
    return normalize_field(selected)


def write_tender_metadata(
    lot_name: str,
    tender_metadata: dict[str, Any],
    metadata: dict[str, Any],
    metadata_path: Path,
    tender_projects_path: Path,
) -> None:
    projects_data = load_tender_projects(tender_projects_path)
    project = find_project_by_lot_name(projects_data, lot_name)
    project["tender_project_metadata"] = tender_metadata
    save_tender_projects(projects_data, tender_projects_path)

    metadata.setdefault("tender_project_metadata", {})[lot_name] = tender_metadata
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def source_type_and_priority(record: dict[str, Any]) -> tuple[str, int]:
    name = str(record.get("original_name") or "")
    text = str(record.get("parsed_text") or "")[:5000]
    source_type = tender_source_type(record)
    if source_type == "tender_notice":
        return "tender_notice", 0
    if source_type == "tender_notice_attachment":
        return "tender_notice_attachment", 1
    if source_type == "procurement_package_table":
        return "procurement_package_table", 3
    if is_full_tender_document(record):
        return "full_tender_document", 1
    if "投标人须知前附表" in text:
        return "bidder_instructions_front_table", 2
    if any(term in name or term in text for term in ["采购需求一览表", "分标分包表", "包件清单"]):
        return "procurement_package_table", 3
    if any(term in name for term in ["评标办法", "评分表"]):
        return "score_table", 4
    if "招标文件附件" in name:
        return "tender_attachment", 5
    if any(term in name for term in ["技术规范书", "技术标准"]):
        return "technical_spec", 6
    return "tender_source", 5


def candidate(field: str, value: str, source: dict[str, Any], evidence: str, method: str) -> dict[str, Any]:
    priority = priority_value(source)
    source_type = str(source.get("source_type") or "")
    confidence = confidence_for_source(priority, method)
    requires_manual_confirm = priority >= 5 or method in {"filename", "cli"} or (field == "project_name" and source_type == "technical_spec")
    return {
        "value": value,
        "source_file_id": source.get("file_id"),
        "source_file_name": source.get("source_file_name"),
        "source_type": source_type,
        "extraction_method": method,
        "priority": priority,
        "evidence": preview(evidence, 120 if source_type == "tender_notice" else 200),
        "confidence": confidence,
        "requires_manual_confirm": requires_manual_confirm,
    }


def match_evidence(field: str, value: str, matched_text: str) -> str:
    text = " ".join(str(matched_text or "").split())
    if text and len(text) <= 120:
        return text
    label = FIELD_LABELS.get(field, field)
    return f"{label}：{value}"


def confidence_for_source(priority: int, method: str) -> float:
    base = {0: 0.9, 1: 0.86, 2: 0.82, 3: 0.78, 4: 0.68, 5: 0.55, 6: 0.42, 8: 0.4}.get(priority, 0.45)
    if method == "notice_title":
        base = min(base, 0.85)
    if method == "filename":
        base = min(base, 0.45)
    if method == "cli":
        base = min(base, 0.4)
    return round(base, 2)


def priority_value(item: dict[str, Any]) -> int:
    value = item.get("priority")
    if value is None:
        return 99
    try:
        return int(value)
    except Exception:
        return 99


def empty_field(requires_manual_confirm: bool) -> dict[str, Any]:
    return {
        "value": None,
        "source_file_id": None,
        "source_file_name": None,
        "source_type": None,
        "evidence": None,
        "confidence": 0.0,
        "requires_manual_confirm": requires_manual_confirm,
    }


def normalize_field(value: dict[str, Any]) -> dict[str, Any]:
    field = empty_field(True)
    field.update({key: value.get(key) for key in field.keys()})
    field["confidence"] = round(float(field.get("confidence") or 0.0), 2)
    field["requires_manual_confirm"] = bool(field.get("requires_manual_confirm"))
    return field


def build_manual_confirm_items(fields: dict[str, dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for field_name, field in fields.items():
        if field.get("requires_manual_confirm"):
            label = FIELD_LABELS.get(field_name, field_name)
            if field.get("value"):
                items.append(f"{label}：需人工确认（当前候选：{field.get('value')}）")
            else:
                items.append(f"{label}：未定位，需人工确认")
    return items


DEMAND_HEADER_ALIASES = {
    "lot_no": ["分标编号", "标段编号", "分标号", "标段"],
    "lot_name": ["分标名称", "分标名称（公告版）", "标段名称"],
    "package_no": ["包号", "包编号", "包件号"],
    "package_name": ["包名称", "包件名称", "工程规模", "网省项目名称", "大工程名称", "招标项目名称", "采购内容", "服务名称"],
    "project_name": ["项目名称", "工程名称", "服务名称", "网省项目名称", "大工程名称"],
    "technical_topic_name": ["工程规模", "服务名称", "采购内容", "招标项目名称"],
    "service_period": ["服务期限", "工期"],
    "max_price": ["最高限价", "最高投标限价"],
    "special_qualification_requirement": ["专用资格要求", "资质业绩要求", "资质要求", "业绩要求", "人员要求"],
}


def extract_lot_package_fields(record: dict[str, Any], source: dict[str, Any], lot_name: str) -> dict[str, dict[str, Any]]:
    if not lot_name or source.get("source_type") not in {"procurement_package_table", "tender_notice_attachment"}:
        return {}
    rows = table_like_rows(record)
    matched = match_lot_row(rows, lot_name)
    if matched:
        row, headers = matched
        return candidates_from_lot_row(row, headers, source, lot_name)
    text = str(record.get("parsed_text") or "")
    index = text.find(lot_name)
    if index < 0:
        return {}
    evidence = evidence_context(text, index, index + len(lot_name), window=220)
    return {"package_no": candidate("package_no", lot_name, source, evidence, "lot_text_match")}


def table_like_rows(record: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for table in list(record.get("tables") or []):
        raw_rows: Any
        if isinstance(table, dict):
            raw_rows = table.get("rows") or table.get("data") or table.get("cells") or []
        else:
            raw_rows = table
        if not isinstance(raw_rows, list):
            continue
        for raw_row in raw_rows:
            if isinstance(raw_row, dict):
                if isinstance(raw_row.get("cells"), list):
                    rows.append([cell_text(cell) for cell in raw_row.get("cells") or []])
                    continue
                rows.append([str(key) for key in raw_row.keys()])
                rows.append([str(value or "") for value in raw_row.values()])
            elif isinstance(raw_row, list):
                rows.append([cell_text(cell) for cell in raw_row])
    return [row for row in rows if any(cell.strip() for cell in row)]


def match_lot_row(rows: list[list[str]], lot_name: str) -> tuple[list[str], list[str]] | None:
    if not rows:
        return None
    headers: list[str] = []
    for index, row in enumerate(rows):
        if count_header_hits(row) >= 2:
            headers = row
            continue
        row_text = normalize_compare("".join(row))
        if normalize_compare(lot_name) in row_text:
            return row, headers
        if headers:
            values = row_to_mapping(row, headers)
            combo = normalize_compare(str(values.get("分标编号") or "") + "-" + str(values.get("包号") or ""))
            combo2 = normalize_compare(str(values.get("分标编号") or "") + str(values.get("包号") or ""))
            target = normalize_compare(lot_name)
            if combo == target or combo2 == target:
                return row, headers
    return None


def candidates_from_lot_row(row: list[str], headers: list[str], source: dict[str, Any], lot_name: str) -> dict[str, dict[str, Any]]:
    values = row_to_mapping(row, headers) if headers else {}
    evidence = " | ".join(cell for cell in row if cell.strip())
    result: dict[str, dict[str, Any]] = {}
    for field, aliases in DEMAND_HEADER_ALIASES.items():
        if field == "special_qualification_requirement":
            value = "；".join(all_alias_values(values, aliases))
        else:
            value = first_alias_value(values, aliases)
        if not value and field == "package_no":
            value = lot_name
        if value and valid_field_value(field, value):
            result[field] = candidate(field, clean_value(value), source, evidence, "lot_table_match")
    return result


def row_to_mapping(row: list[str], headers: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = clean_value(header)
        if key:
            result[key] = clean_value(row[index] if index < len(row) else "")
    return result


def first_alias_value(values: dict[str, str], aliases: list[str]) -> str:
    for alias in aliases:
        for key, value in values.items():
            if alias in key and value:
                return value
    return ""


def all_alias_values(values: dict[str, str], aliases: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        for key, value in values.items():
            cleaned = clean_value(value)
            if alias in key and cleaned and cleaned not in {"/", "无"} and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
    return result


def count_header_hits(row: list[str]) -> int:
    joined = " ".join(row)
    aliases = [alias for values in DEMAND_HEADER_ALIASES.values() for alias in values]
    return sum(1 for alias in aliases if alias in joined)


def cell_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ["text", "value", "content"]:
            if value.get(key) is not None:
                return str(value.get(key) or "")
        return " ".join(str(item or "") for item in value.values())
    return str(value or "")


def technical_topic_from_filename(name: str) -> str:
    stem = re.sub(r"\.(docx?|pdf|xlsx?)$", "", name, flags=re.IGNORECASE).strip()
    match = re.search(r"[（(]([^()（）]{4,100})[）)]", stem)
    if match:
        return clean_value(match.group(1))
    stem = re.sub(r"^技术规范书[-_ ]*", "", stem)
    return clean_value(stem) if "技术规范书" not in stem and len(stem) >= 4 else ""


def valid_field_value(field: str, value: str) -> bool:
    if not value or len(value) > 120:
        return False
    if field == "special_qualification_requirement" and is_redirect_reference(value):
        return False
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", value):
        return False
    if value.isdigit() and field not in {"package_no", "lot_no", "tender_no", "max_price"}:
        return False
    bad_terms = [
        "目录",
        "页码",
        "模板",
        "见投标人须知前附表",
        "详见技术规范书",
        "技术标准详见技术规范书",
        "招标文件的全部内容",
        "投标邀请",
        "投标保函",
        "联合体",
        "技术偏差表",
        "按钮",
        "上传",
    ]
    if any(term in value for term in bad_terms):
        return False
    if field == "project_name" and any(label in value for label in ["招标编号", "分标名称", "分标编号", "包名称", "包号"]):
        return False
    if field in {"lot_name", "package_name"} and any(label in value for label in ["招标编号", "项目名称", "分标编号", "包号"]):
        return False
    if field in {"lot_no", "package_no"} and len(value) == 1 and not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", value):
        return False
    return True


def is_redirect_reference(value: Any) -> bool:
    normalized = normalize_compare(value)
    return normalized in {normalize_compare(item) for item in REDIRECT_REFERENCE_VALUES}


def project_name_from_tender_notice_title(text: str) -> dict[str, str] | None:
    lines = [clean_value(line) for line in str(text or "").splitlines()]
    for index, line in enumerate(lines[:120]):
        if line != "招标公告":
            continue
        candidates = [
            item for item in lines[max(0, index - 3):index]
            if is_valid_notice_project_title(item)
        ]
        if not candidates:
            continue
        value = max(candidates, key=len)
        evidence_lines = [item for item in lines[max(0, index - 1): min(len(lines), index + 2)] if item]
        if value not in evidence_lines:
            evidence_lines.insert(0, value)
        return {"value": value, "evidence": "\n".join(evidence_lines[:3])}
    return None


def is_valid_notice_project_title(value: str) -> bool:
    if not value or len(value) < 8:
        return False
    excluded = [
        "范本编号",
        "采购文件范本",
        "服务公开招标文件分册",
        "2025年版",
        "目录",
        "第一章",
        "招标公告",
        "国网河北省电力有限公司",
    ]
    if any(term in value for term in excluded):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def clean_value(value: Any) -> str:
    text = " ".join(str(value or "").replace("\u3000", " ").split())
    text = re.sub(r"\s+\d+$", "", text)
    text = text.strip(" ：:;；，,。|")
    return text[:120]


def evidence_context(text: str, start: int, end: int, window: int = 80) -> str:
    return " ".join(text[max(0, start - window): min(len(text), end + window)].split())


def preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def normalize_compare(value: Any) -> str:
    return re.sub(r"\s+|[^\w\u4e00-\u9fff]+", "", str(value or "").lower())


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in candidates:
        key = (normalize_compare(item.get("value")), str(item.get("source_file_id") or ""), str(item.get("source_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.tender_metadata_extractor import clean_value, normalize_compare


TABLE_ALIASES = {
    "lot_name": ("分标名称", "分标名称（公告版）", "标段名称", "标段", "lot name"),
    "lot_no": ("分标编号", "分标号", "标段编号", "标段号", "lot no", "lot number"),
    "package_name": ("包名称", "包件名称", "采购包名称", "分包名称", "package name"),
    "package_no": (
        "包号",
        "包编号",
        "包件号",
        "包件编号",
        "采购包号",
        "采购包编号",
        "分包编号",
        "包件",
        "采购包",
        "分包",
        "package no",
        "package number",
    ),
}
DESCRIPTION_ALIASES = (
    "项目名称",
    "采购项目名称",
    "采购内容",
    "采购需求",
    "服务名称",
    "标包名称",
    "网省项目名称",
)
DEMAND_STRUCTURE_MARKERS = (
    "项目采购需求清单",
    "采购需求清单",
    "采购包划分表",
    "包划分表",
    "标段划分表",
    "分包明细表",
    "package list",
    "lot list",
)
GENERIC_VALUES = {
    "不允许",
    "允许",
    "不适用",
    "适用",
    "无",
    "有",
    "是",
    "否",
    "序号",
    "项目",
    "项目名称",
    "采购内容",
    "采购需求",
    "响应文件条款",
    "采购文件条款",
    "企业资质等级",
    "工程师",
    "员工总数",
    "技术人员数",
    "参加工作时间",
    "工作年限",
    "姓名",
    "职务",
    "职称",
    "出资金额",
    "出资比例",
}
SENTENCE_TERMS = (
    "响应供应商",
    "采购文件的全部内容",
    "评审委员会",
    "将予以拒收",
    "提出异议",
    "不作评审",
    "按如下要求",
    "详见附件",
    "签订框架协议",
    "本条款",
)
PLACEHOLDER_VALUES = {"", "/", "-", "--", "—", "xx", "xxx", "待填写", "请填写"}

STRICT_TEXT_LABELS = sorted(
    ((alias, field) for field, aliases in TABLE_ALIASES.items() for alias in aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)
STRICT_TEXT_PATTERN = re.compile(
    r"(?P<label>"
    + "|".join(re.escape(label) for label, _ in STRICT_TEXT_LABELS)
    + r")\s*[:：]\s*(?P<value>[^,，;；\n\r]{1,100})",
    flags=re.IGNORECASE,
)


def resolve_project_lot(
    metadata: dict[str, Any],
    *,
    explicit_lot_name: str | None = None,
) -> dict[str, Any]:
    explicit = str(explicit_lot_name or "").strip()
    if explicit:
        return {
            "lot_name": explicit,
            "lot_resolution_status": "explicit",
            "lot_candidates": [
                {
                    "value": explicit,
                    "display_name": explicit,
                    "fields": {"lot_name": explicit},
                    "source_file_id": None,
                    "source_location": "cli:--lot-name",
                    "label": "--lot-name",
                    "source_file_ids": [],
                    "source_file_names": [],
                    "method": "cli",
                    "structure_priority": -1,
                    "evidence": [
                        {
                            "source_file_id": None,
                            "source_file_name": None,
                            "source_location": "cli:--lot-name",
                            "label": "--lot-name",
                            "value": explicit,
                        }
                    ],
                }
            ],
            "lot_resolution_reason": "explicit --lot-name supplied by user",
        }

    candidates: list[dict[str, Any]] = []
    for record in metadata.get("files") or []:
        if isinstance(record, dict):
            candidates.extend(_record_candidates(record))
    candidates = _merge_compatible_candidates(candidates)
    candidates = _prefer_demand_structures(candidates)
    candidates = sorted(candidates, key=_candidate_sort_key)

    if not candidates:
        return {
            "lot_name": None,
            "lot_resolution_status": "not_applicable",
            "lot_candidates": [],
            "lot_resolution_reason": "no validated lot/package structure found in scoped buyer sources",
        }
    if len(candidates) == 1:
        selected = candidates[0]
        return {
            "lot_name": selected["value"],
            "lot_resolution_status": "resolved_unique",
            "lot_candidates": candidates,
            "lot_resolution_reason": "one validated lot/package identity found in scoped buyer sources",
        }
    return {
        "lot_name": None,
        "lot_resolution_status": "ambiguous",
        "lot_candidates": candidates,
        "lot_resolution_reason": "multiple validated lot/package identities found; manual selection required",
    }


def _record_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    return _dedupe_candidates(_table_candidates(record) + _text_candidates(record))


def _table_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for table_position, table in enumerate(record.get("tables") or [], start=1):
        rows = _rows_for_table(table)
        if not rows:
            continue
        source_location = _table_source_location(table, table_position)
        structure_priority = _table_structure_priority(record, table, rows)
        headers: list[str] | None = None
        header_map: dict[int, tuple[str, str]] = {}
        description_header: tuple[int, str] | None = None
        for row_index, row in enumerate(rows, start=1):
            current_map = _header_map(row)
            if current_map:
                headers = row
                header_map = current_map
                description_header = _description_header(row)
                continue
            if not headers or not header_map:
                continue
            fields, labels = _fields_from_structured_row(
                row,
                header_map,
                description_header=description_header,
            )
            if not fields:
                continue
            evidence = {
                "source_file_id": _record_id(record),
                "source_file_name": _record_name(record),
                "source_location": f"{source_location}:row_{row_index}",
                "label": " | ".join(labels),
                "value": deepcopy(fields),
                "header": deepcopy(headers),
                "row": deepcopy(row),
            }
            candidates.append(
                _candidate(
                    record,
                    fields,
                    method=(
                        "procurement_demand_list"
                        if structure_priority == 0
                        else "lot_package_table"
                    ),
                    evidence=evidence,
                    structure_priority=structure_priority,
                )
            )
    return candidates


def _text_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(record.get("parsed_text") or "")[:100000]
    candidates: list[dict[str, Any]] = []
    for match in STRICT_TEXT_PATTERN.finditer(text):
        label = clean_value(match.group("label"))
        field = _field_for_label(label)
        value = clean_value(match.group("value"))
        if field is None or not _valid_candidate_value(field, value):
            continue
        fields = {field: value}
        evidence = {
            "source_file_id": _record_id(record),
            "source_file_name": _record_name(record),
            "source_location": f"parsed_text:{match.start()}-{match.end()}",
            "label": label,
            "value": value,
            "raw_text": clean_value(match.group(0)),
        }
        candidates.append(
            _candidate(
                record,
                fields,
                method="explicit_labeled_text",
                evidence=evidence,
                structure_priority=2,
            )
        )
    return candidates


def _rows_for_table(table: Any) -> list[list[str]]:
    if isinstance(table, dict):
        raw_rows = table.get("rows") or table.get("data") or table.get("cells") or []
    else:
        raw_rows = table
    if not isinstance(raw_rows, list):
        return []
    rows: list[list[str]] = []
    for raw_row in raw_rows:
        if isinstance(raw_row, dict):
            raw_cells = raw_row.get("cells")
            if isinstance(raw_cells, list):
                rows.append([_cell_text(cell) for cell in raw_cells])
            else:
                rows.append([_cell_text(value) for value in raw_row.values()])
        elif isinstance(raw_row, list):
            rows.append([_cell_text(cell) for cell in raw_row])
    return [row for row in rows if any(clean_value(cell) for cell in row)]


def _cell_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "value", "content"):
            if value.get(key) is not None:
                return str(value.get(key) or "")
        return " ".join(str(item or "") for item in value.values())
    return str(value or "")


def _header_map(row: list[str]) -> dict[int, tuple[str, str]]:
    result: dict[int, tuple[str, str]] = {}
    for index, cell in enumerate(row):
        label = clean_value(cell)
        field = _field_for_label(label)
        if field:
            result[index] = (field, label)
    return result


def _field_for_label(label: str) -> str | None:
    normalized = normalize_compare(label)
    for field, aliases in TABLE_ALIASES.items():
        if any(normalized == normalize_compare(alias) for alias in aliases):
            return field
    return None


def _description_header(row: list[str]) -> tuple[int, str] | None:
    for index, cell in enumerate(row):
        normalized = normalize_compare(clean_value(cell))
        if any(normalized == normalize_compare(alias) for alias in DESCRIPTION_ALIASES):
            return index, clean_value(cell)
    return None


def _fields_from_structured_row(
    row: list[str],
    header_map: dict[int, tuple[str, str]],
    *,
    description_header: tuple[int, str] | None,
) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    labels: list[str] = []
    for index, (field, label) in header_map.items():
        value = clean_value(row[index] if index < len(row) else "")
        if _valid_candidate_value(field, value):
            fields[field] = value
            labels.append(label)
    if not fields:
        return {}, []
    if (
        description_header is not None
        and "package_name" not in fields
        and "lot_name" not in fields
        and description_header[0] < len(row)
    ):
        description = clean_value(row[description_header[0]])
        if _valid_candidate_value("package_name", description):
            fields["package_name"] = description
            labels.append(description_header[1])
    return fields, labels


def _valid_candidate_value(field: str, value: str) -> bool:
    cleaned = clean_value(value)
    normalized = normalize_compare(cleaned)
    if not cleaned or normalized in {normalize_compare(item) for item in PLACEHOLDER_VALUES}:
        return False
    if normalized in {normalize_compare(item) for item in GENERIC_VALUES}:
        return False
    if any(term in cleaned for term in SENTENCE_TERMS):
        return False
    if any(normalize_compare(alias) in normalized for aliases in TABLE_ALIASES.values() for alias in aliases):
        return False
    if re.fullmatch(r"[-−]\s*\d+(?:\.\d+)?", cleaned):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[-~〜]\s*\d+(?:\.\d+)?(?:\s*分)?", cleaned):
        return False
    max_length = 48 if field in {"lot_no", "package_no"} else 80
    if len(cleaned) > max_length:
        return False
    if field in {"lot_no", "package_no"}:
        if not re.fullmatch(r"[A-Za-z0-9一-鿿_./()（）\-]+", cleaned):
            return False
        if not re.search(r"\d", cleaned) and not re.fullmatch(
            r"(?:第)?[一二三四五六七八九十百]+(?:包|标段)?|包[一二三四五六七八九十百]+",
            cleaned,
        ):
            return False
        return True
    if re.search(r"[。；;！？!?]", cleaned):
        return False
    return bool(re.search(r"[A-Za-z0-9一-鿿]", cleaned))


def _table_structure_priority(record: dict[str, Any], table: Any, rows: list[list[str]]) -> int:
    table_text = (
        " ".join(str(table.get(key) or "") for key in ("title", "caption", "name", "sheet_name"))
        if isinstance(table, dict)
        else ""
    )
    header_text = " ".join(rows[0]) if rows else ""
    if any(marker.lower() in table_text.lower() for marker in DEMAND_STRUCTURE_MARKERS):
        return 0
    if any(marker.lower() in header_text.lower() for marker in DEMAND_STRUCTURE_MARKERS):
        return 0
    identity_headers = max((len(_header_map(row)) for row in rows), default=0)
    nearby_context = _table_nearby_context(record, table)
    if identity_headers >= 2 and any(marker in nearby_context for marker in DEMAND_STRUCTURE_MARKERS):
        return 0
    return 1


def _table_nearby_context(record: dict[str, Any], table: Any) -> str:
    if not isinstance(table, dict):
        return ""
    table_index = table.get("table_index")
    if table_index is None:
        return ""
    text = str(record.get("parsed_text") or "")
    marker = f"=== Table {table_index} ==="
    position = text.find(marker)
    if position < 0:
        return ""
    return text[max(0, position - 360) : min(len(text), position + len(marker) + 80)]


def _table_source_location(table: Any, table_position: int) -> str:
    if isinstance(table, dict):
        return str(
            table.get("source_location")
            or table.get("sheet_name")
            or table.get("title")
            or f"table_{table.get('table_index') or table_position}"
        )
    return f"table_{table_position}"


def _candidate(
    record: dict[str, Any],
    fields: dict[str, str],
    *,
    method: str,
    evidence: dict[str, Any],
    structure_priority: int,
) -> dict[str, Any]:
    file_id = _record_id(record)
    file_name = _record_name(record)
    return {
        "value": _preferred_value(fields),
        "display_name": _display_name(fields),
        "fields": deepcopy(fields),
        "source_file_id": file_id,
        "source_location": evidence["source_location"],
        "label": evidence["label"],
        "source_file_ids": [file_id] if file_id else [],
        "source_file_names": [file_name] if file_name else [],
        "method": method,
        "structure_priority": structure_priority,
        "evidence": [deepcopy(evidence)],
    }


def _preferred_value(fields: dict[str, str]) -> str:
    lot_no = str(fields.get("lot_no") or "").strip()
    package_no = str(fields.get("package_no") or "").strip()
    if lot_no and package_no:
        return f"{lot_no}-{package_no}"
    if package_no:
        return package_no
    for field in ("lot_name", "package_name", "lot_no"):
        if fields.get(field):
            return fields[field]
    return ""


def _display_name(fields: dict[str, str]) -> str:
    lot_no = str(fields.get("lot_no") or "").strip()
    lot_name = str(fields.get("lot_name") or "").strip()
    package_no = str(fields.get("package_no") or "").strip()
    package_name = str(fields.get("package_name") or "").strip()
    if package_no and package_name:
        prefix = package_no if re.search(r"包|package", package_no, re.IGNORECASE) else f"包{package_no}"
        return f"{prefix}：{package_name}"
    if lot_no and lot_name:
        return f"标段{lot_no}：{lot_name}"
    return package_name or lot_name or package_no or lot_no


def _merge_compatible_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        target = next((item for item in merged if _compatible(item, candidate)), None)
        if target is None:
            merged.append(deepcopy(candidate))
            continue
        target["fields"].update(
            {key: value for key, value in candidate["fields"].items() if not target["fields"].get(key)}
        )
        target["value"] = _preferred_value(target["fields"])
        target["display_name"] = _display_name(target["fields"])
        target["source_file_ids"] = _unique(target["source_file_ids"] + candidate["source_file_ids"])
        target["source_file_names"] = _unique(target["source_file_names"] + candidate["source_file_names"])
        target["method"] = "+".join(_unique(target["method"].split("+") + candidate["method"].split("+")))
        target["structure_priority"] = min(target["structure_priority"], candidate["structure_priority"])
        target["evidence"].extend(item for item in candidate["evidence"] if item not in target["evidence"])
    return _dedupe_candidates(merged)


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    shared_fields = set(left["fields"]) & set(right["fields"])
    if shared_fields:
        return all(
            normalize_compare(left["fields"][field]) == normalize_compare(right["fields"][field])
            for field in shared_fields
        )
    return normalize_compare(left.get("value")) == normalize_compare(right.get("value"))


def _prefer_demand_structures(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if any(item.get("structure_priority") == 0 for item in candidates):
        return [item for item in candidates if item.get("structure_priority") == 0]
    return candidates


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for item in candidates:
        signature = tuple(
            sorted((field, normalize_compare(value)) for field, value in item.get("fields", {}).items())
        )
        if not signature or signature in seen:
            continue
        seen.add(signature)
        result.append(item)
    return result


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(candidate.get("structure_priority", 9)),
        normalize_compare(candidate.get("display_name") or candidate.get("value")),
        tuple(candidate.get("source_file_names") or []),
    )


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("file_id") or "")


def _record_name(record: dict[str, Any]) -> str:
    return str(record.get("original_name") or record.get("name") or "")


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result

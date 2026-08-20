from __future__ import annotations

import re
from typing import Any


FULL_TENDER_TYPES = {
    "full_tender_document",
    "tender_document",
    "tender_main",
    "tender_notice_or_procurement_document",
}

TENDER_SOURCE_GROUP = "tender_source"
TENDER_SOURCE_CATEGORIES = {"招标文件", "采购文件", "招标公告", "采购公告", "招标公告附件", "评标办法", "评分表", "技术规范书", "招标需求一览表"}
FULL_TENDER_NEGATIVE_NAME_TERMS = {"招标文件附件", "招标公告", "公告附件", "评标办法", "评分表", "技术规范书", "技术标准详见技术规范书"}


def is_tender_source_file(file_meta: dict[str, Any]) -> bool:
    """Return True for files that are tender/procurement sources, not enterprise proof material."""
    profile = file_meta.get("material_profile") if isinstance(file_meta.get("material_profile"), dict) else {}
    tags = _as_lower_set(file_meta.get("tags")) | _as_lower_set(profile.get("material_types"))
    category = str(file_meta.get("category") or "")
    name = _record_name(file_meta)
    text_head = str(file_meta.get("parsed_text") or "")[:4000]

    if str(profile.get("material_group") or "") == TENDER_SOURCE_GROUP:
        return True
    if "tender_demand_list" in tags:
        return True
    if tags & {item.lower() for item in FULL_TENDER_TYPES}:
        return True
    if category in TENDER_SOURCE_CATEGORIES or any(term in category for term in TENDER_SOURCE_CATEGORIES):
        return True
    if any(term in name for term in ["招标文件", "采购文件", "招标公告", "采购公告", "评标办法", "评分表", "技术规范书"]):
        return True
    if any(term in text_head for term in ["投标人须知前附表", "响应文件组成", "投标文件组成", "否决", "无效响应", "采购需求一览表", "分标分包表"]):
        return True
    return False


def is_tender_notice_file(file_meta: dict[str, Any]) -> bool:
    name = _record_name(file_meta)
    category = str(file_meta.get("category") or "")
    tags = _as_lower_set(file_meta.get("tags"))
    return (
        "tender_notice" in tags
        or category == "招标公告"
        or "招标公告" in name
        or "采购公告" in name
    ) and not is_tender_notice_attachment_file(file_meta)


def is_tender_notice_attachment_file(file_meta: dict[str, Any]) -> bool:
    name = _record_name(file_meta)
    category = str(file_meta.get("category") or "")
    tags = _as_lower_set(file_meta.get("tags"))
    return (
        "tender_notice_attachment" in tags
        or category == "招标公告附件"
        or "招标公告附件" in name
        or ("公告附件" in name)
    )


def tender_source_type(file_meta: dict[str, Any]) -> str:
    name = _record_name(file_meta)
    category = str(file_meta.get("category") or "")
    tags = _as_lower_set(file_meta.get("tags"))
    if "tender_demand_list" in tags or category == "招标需求一览表" or "需求一览表" in name:
        return "procurement_package_table"
    if is_tender_notice_attachment_file(file_meta):
        return "tender_notice_attachment"
    if is_tender_notice_file(file_meta):
        return "tender_notice"
    if is_full_tender_document(file_meta):
        return "full_tender_document"
    if "评标办法" in name or "评分表" in name:
        return "score_table"
    if "招标文件附件" in name:
        return "tender_attachment"
    if "技术规范书" in name or "技术标准" in name:
        return "technical_spec"
    return "tender_source"


def is_full_tender_document(file_meta: dict[str, Any]) -> bool:
    """Return True only for the complete tender/procurement body, excluding attachments and specs."""
    if is_tender_notice_file(file_meta) or is_tender_notice_attachment_file(file_meta):
        return False
    name = _record_name(file_meta)
    if any(term in name for term in FULL_TENDER_NEGATIVE_NAME_TERMS):
        return False

    profile = file_meta.get("material_profile") if isinstance(file_meta.get("material_profile"), dict) else {}
    tags = _as_lower_set(file_meta.get("tags")) | _as_lower_set(profile.get("material_types"))
    category = str(file_meta.get("category") or "")
    text = str(file_meta.get("parsed_text") or "")

    if "full_tender_document" in tags:
        return True
    if category == "招标文件":
        return True
    if re.search(r"招标文件[-_][^\\/]+\.docx?$", name, flags=re.IGNORECASE):
        return True

    strong_terms = ["投标人须知前附表", "响应文件组成", "投标文件组成", "签字盖章", "无效响应", "否决", "资格条件", "服务期限", "采购需求一览表", "分标分包表"]
    positive_terms = ["招标文件", "采购文件", "招标公告", "采购公告", *strong_terms]
    combined = f"{name}\n{text[:12000]}"
    strong_count = sum(1 for term in strong_terms if term in combined)
    positive_count = sum(1 for term in positive_terms if term in combined)
    return strong_count >= 3 or (positive_count >= 5 and len(text) >= 1200)


def tender_source_material_types(file_meta: dict[str, Any]) -> list[str]:
    name = _record_name(file_meta)
    text = str(file_meta.get("parsed_text") or "")
    combined = f"{name}\n{text[:12000]}"
    types: list[str] = []
    if tender_source_type(file_meta) == "procurement_package_table":
        types.extend(["tender_demand_list", "procurement_package_table", "procurement_requirements"])
        return sorted_unique(types)
    if is_tender_notice_file(file_meta):
        types.extend(["tender_notice", "tender_notice_or_procurement_document", "procurement_requirements", "qualification_requirement"])
        return sorted_unique(types)
    if is_tender_notice_attachment_file(file_meta):
        types.extend(["tender_notice_attachment", "tender_notice_or_procurement_document", "procurement_requirements"])
        return sorted_unique(types)
    if is_full_tender_document(file_meta):
        types.extend(["full_tender_document", "tender_document", "tender_notice_or_procurement_document"])
    if any(term in combined for term in ["响应格式", "投标文件格式", "应答文件格式", "格式"]):
        types.append("response_format")
    if any(term in combined for term in ["否决", "无效响应", "无效投标"]):
        types.append("invalid_response_clause")
    if any(term in combined for term in ["资格条件", "资格要求", "资质要求"]):
        types.append("qualification_requirement")
    if any(term in combined for term in ["合同条款", "合同条款及格式", "合同"]):
        types.append("contract_terms")
    if any(term in combined for term in ["采购需求", "服务内容", "服务要求", "委托人要求"]):
        types.append("procurement_requirements")
    if any(term in combined for term in ["技术规范书", "技术标准"]):
        types.append("technical_specification")
    if any(term in combined for term in ["评标办法", "评分表", "评分标准"]):
        types.extend(["scoring_method", "score_table"])
    if any(term in combined for term in ["签字", "盖章", "签章"]):
        types.append("signature_seal_requirement")
    return sorted_unique(types or ["tender_document"])


def tender_source_profile(file_meta: dict[str, Any]) -> dict[str, Any]:
    types = tender_source_material_types(file_meta)
    return {
        "material_group": TENDER_SOURCE_GROUP,
        "material_group_name": "招标文件与评分依据",
        "source_type": tender_source_type(file_meta),
        "material_types": types,
        "usable_for": [
            "tender_requirement",
            "scoring_reference",
            "response_format_reference",
            "tender_project_metadata",
            "technical_outline_tender_basis",
        ],
        "not_usable_for": [
            "company_qualification",
            "company_performance",
            "patent",
            "supplier_performance",
            "intellectual_property",
            "enterprise_proof_material",
        ],
        "confidence": 0.95 if is_full_tender_document(file_meta) else 0.88,
        "classification_source": "tender_source_guard",
        "requires_manual_confirm": False,
        "notes": ["tender_source_only: do not use as enterprise proof material"],
    }


def sorted_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return sorted(result)


def _as_lower_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _record_name(file_meta: dict[str, Any]) -> str:
    return str(file_meta.get("original_name") or file_meta.get("name") or file_meta.get("file_name") or "")

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


KNOWN_PLACEHOLDER_IDS = {
    "投标人名称": "PH-BIDDER-NAME",
    "项目名称": "PH-PROJECT-NAME",
    "项目编号": "PH-PROJECT-NUMBER",
    "拟投品牌": "PH-PRODUCT-BRAND",
    "产品品牌": "PH-PRODUCT-BRAND",
    "拟投型号": "PH-PRODUCT-MODEL",
    "产品型号": "PH-PRODUCT-MODEL",
    "制造商": "PH-MANUFACTURER",
    "产地": "PH-ORIGIN",
    "实际产品配置": "PH-ACTUAL-CONFIGURATION",
    "实际技术参数": "PH-ACTUAL-PARAMETERS",
    "项目负责人": "PH-PROJECT-MANAGER",
    "项目团队成员": "PH-PROJECT-TEAM",
    "人员投入计划": "PH-STAFFING-PLAN",
    "最终交货承诺": "PH-DELIVERY-COMMITMENT",
    "最终安装周期": "PH-INSTALLATION-PERIOD",
    "实际项目进度计划": "PH-PROJECT-SCHEDULE",
    "最终服务承诺": "PH-SERVICE-COMMITMENT",
    "技术偏离状态": "PH-TECHNICAL-DEVIATION",
}
PLACEHOLDER_ID_PATTERN = re.compile(r"\bPH-[A-Z0-9][A-Z0-9-]{2,80}\b")
PLACEHOLDER_TOKEN_PATTERN = re.compile(r"\{\{(PH-[A-Z0-9][A-Z0-9-]{2,80})\}\}")
LEGACY_PLACEHOLDER_PATTERN = re.compile(
    r"【(?:待人工确认|待补充企业材料|待核验外部来源)：[^】]+】"
)
_COMBINATION_PATTERN = re.compile(r"(?:、|/|；|;|，|,|和|及|以及|与)")


def normalize_placeholder_field(field: str) -> str:
    value = unicodedata.normalize("NFKC", str(field or "")).strip()
    return re.sub(r"\s+", " ", value).casefold()


def placeholder_id_for_field(field: str) -> str:
    display = unicodedata.normalize("NFKC", str(field or "")).strip()
    if not display:
        raise ValueError("pending confirmation field must not be empty")
    for known, placeholder_id in KNOWN_PLACEHOLDER_IDS.items():
        if normalize_placeholder_field(known) == normalize_placeholder_field(display):
            return placeholder_id
    normalized = normalize_placeholder_field(display)
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-").upper()
    if ascii_slug:
        return f"PH-FIELD-{ascii_slug[:48]}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()
    return f"PH-FIELD-{digest}"


def placeholder_token(placeholder_id: str) -> str:
    if not PLACEHOLDER_ID_PATTERN.fullmatch(str(placeholder_id or "")):
        raise ValueError("invalid placeholder_id")
    return "{{" + str(placeholder_id) + "}}"


def extract_placeholder_ids(content: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER_TOKEN_PATTERN.findall(str(content or ""))))


def extract_placeholder_references(content: str) -> list[str]:
    """Return every supported placeholder token without inventing an ID."""
    text = str(content or "")
    references = [
        match.group(0)
        for match in re.finditer(
            rf"{PLACEHOLDER_TOKEN_PATTERN.pattern}|{LEGACY_PLACEHOLDER_PATTERN.pattern}",
            text,
        )
    ]
    return list(dict.fromkeys(references))


def is_combined_placeholder_field(field: str) -> bool:
    return bool(_COMBINATION_PATTERN.search(str(field or "")))


def attach_placeholder_identity(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    field = str(result.get("field") or "").strip()
    if is_combined_placeholder_field(field):
        raise ValueError("one pending confirmation item must describe exactly one field")
    expected = placeholder_id_for_field(field)
    supplied = str(result.get("placeholder_id") or "")
    if supplied and supplied != expected:
        raise ValueError("placeholder_id does not match the stable field mapping")
    result["placeholder_id"] = expected
    result["placeholder_token"] = placeholder_token(expected)
    result["category"] = str(
        result.get("category") or placeholder_category_for_field(field)
    )
    result["field_name"] = field
    result["required_confirmation"] = str(
        result.get("required_confirmation")
        or result.get("question")
        or field
    )
    result["candidate_ids"] = list(result.get("candidate_ids") or [])
    result["recommended_candidate"] = result.get("recommended_candidate")
    result["recommendation_reason"] = str(
        result.get("recommendation_reason") or ""
    )
    result["source_references"] = list(result.get("source_references") or [])
    result["severity"] = str(
        result.get("severity")
        or ("blocking" if result.get("blocking", True) else "warning")
    )
    result["release_blocking"] = bool(
        result.get("release_blocking", result.get("blocking", True))
    )
    return result


def placeholder_category_for_field(field: str) -> str:
    value = normalize_placeholder_field(field)
    groups = (
        ("personnel", ("person", "manager", "staff", "team", "人员", "经理", "团队")),
        ("performance", ("performance", "project case", "业绩", "同类项目")),
        ("qualification", ("qualification", "certificate", "资质", "证书", "等级")),
        ("product", ("product", "brand", "model", "manufacturer", "产品", "品牌", "型号", "厂商")),
        ("service", ("service", "warranty", "network", "response", "服务", "网点", "保修", "响应")),
    )
    for category, terms in groups:
        if any(term in value for term in terms):
            return category
    return "other"


def attach_placeholder_identities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [attach_placeholder_identity(item) for item in items]

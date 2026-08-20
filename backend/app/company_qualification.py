from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.metadata_store import MetadataStore


QUALIFICATION_NAMES = ["CMMI", "DCMM"]
CHINESE_LEVELS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
}
OR_ABOVE_PATTERNS = [
    "及以上",
    "以上",
    "不低于",
    "不少于",
    "至少",
    "及其以上",
]


def parse_company_qualification_requirements(text: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, bool]] = set()
    normalized = normalize_text(text)

    for name in QUALIFICATION_NAMES:
        for match in re.finditer(rf"\b{re.escape(name)}\b", normalized, re.I):
            clause = qualification_clause(normalized, match.start(), match.end())
            level = extract_level(clause)
            if level is None:
                continue
            requirement = {
                "name": name,
                "level": level,
            }
            if has_or_above(clause):
                requirement["or_above"] = True
            key = (requirement["name"], requirement["level"], bool(requirement.get("or_above")))
            if key in seen:
                continue
            seen.add(key)
            requirements.append(requirement)

    return sort_requirements(requirements)


def qualification_clause(text: str, start: int, end: int) -> str:
    left = text[:start]
    right = text[end:]
    previous_parts = re.split(r"[。；;\n，,]", left)
    next_parts = re.split(r"[。；;\n，,]", right)
    return f"{previous_parts[-1]} {text[start:end]} {next_parts[0]}".strip()


def parse_company_qualification(text: str) -> dict[str, Any]:
    return {"requirements": parse_company_qualification_requirements(text)}


def build_company_qualification_model(text: str) -> dict[str, Any]:
    return {"company_certificates": parse_company_qualification_requirements(text)}


def parse_company_qualification_file(tender_file: str | Path) -> dict[str, Any]:
    source, text = resolve_tender_text(tender_file)
    result = parse_company_qualification(text)
    result["source"] = source
    return result


def resolve_tender_text(tender_file: str | Path) -> tuple[str, str]:
    value = str(tender_file)
    path = Path(value)
    if path.exists() and path.is_file():
        return str(path), path.read_text(encoding="utf-8", errors="ignore")

    try:
        record = MetadataStore().get_file(value)
    except KeyError as exc:
        raise FileNotFoundError(f"tender file or file_id not found: {value}") from exc

    text = record_text(record)
    if not text.strip():
        raise ValueError(f"parsed_text is empty for tender file_id: {value}")
    return f"metadata:{value}", text


def record_text(record: dict[str, Any]) -> str:
    for key in ["parsed_text", "text", "content"]:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    parse_result = record.get("parse_result")
    if isinstance(parse_result, dict):
        for key in ["parsed_text", "text", "content"]:
            value = parse_result.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def extract_level(text: str) -> int | None:
    patterns = [
        r"(?:level|等级|级别|成熟度等级)\s*(?P<level>[1-5一二三四五壹贰叁肆伍])\s*(?:级|等级)?",
        r"(?P<level>[1-5一二三四五壹贰叁肆伍])\s*(?:级|等级)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            level = normalize_level(match.group("level"))
            if level is not None:
                return level
    return None


def normalize_level(value: Any) -> int | None:
    text = str(value).strip()
    if text.isdigit():
        level = int(text)
        return level if 1 <= level <= 5 else None
    return CHINESE_LEVELS.get(text)


def has_or_above(text: str) -> bool:
    return any(pattern in text for pattern in OR_ABOVE_PATTERNS)


def sort_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {name: index for index, name in enumerate(QUALIFICATION_NAMES)}
    return sorted(requirements, key=lambda item: (order.get(str(item.get("name")), 99), int(item.get("level") or 0)))


def normalize_text(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\u3000]+", " ", value)
    return value.strip()

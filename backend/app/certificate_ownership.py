from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any


def resolve_certificate_owner(asset: dict[str, Any]) -> dict[str, Any]:
    file_id = clean_value(asset.get("file_id") or asset.get("source_file_id"))
    resume_person_name = clean_value(
        asset.get("resume_person_name")
        or asset.get("resume_name")
        or asset.get("owner_person_name")
        or asset.get("person_name")
    )
    if file_id and resume_person_name:
        return {
            "person_name": resume_person_name,
            "file_id": file_id,
            "confidence": 1.0,
            "source": "file_id",
        }

    filename = first_text(
        asset,
        [
            "original_name",
            "source_file_name",
            "file_name",
            "filename",
            "source_path",
            "path",
            "file_path",
            "asset_key",
            "source_location",
        ],
    )
    filename_name = extract_person_name_from_filename(filename)
    if filename_name:
        return {
            "person_name": filename_name,
            "file_id": file_id,
            "confidence": 0.8,
            "source": "filename",
        }

    ocr_text = first_text(asset, ["ocr_text", "evidence_text", "text", "parsed_text"])
    ocr_name = extract_person_name_from_ocr_text(ocr_text)
    if ocr_name:
        return {
            "person_name": ocr_name,
            "file_id": file_id,
            "confidence": 0.6,
            "source": "ocr",
        }

    return {
        "person_name": "unknown",
        "file_id": file_id,
        "confidence": 0.0,
        "source": "unknown",
    }


def batch_resolve_certificates(certificates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved = []
    for certificate in certificates:
        owner = resolve_certificate_owner(certificate)
        item = dict(certificate)
        item["person_name"] = owner["person_name"]
        item["ownership_confidence"] = owner["confidence"]
        item["ownership_source"] = owner["source"]
        item["ownership_file_id"] = owner.get("file_id")
        resolved.append(item)
    return resolved


def extract_person_name_from_filename(filename: str | None) -> str | None:
    text = clean_value(filename)
    if not text:
        return None
    stem = PurePath(text.replace("\\", "/")).name
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", stem)
    stem = re.sub(r"\d{4,}.*$", "", stem)
    parts = [part for part in re.split(r"[-_—\s]+", stem) if part]
    candidates = parts[::-1] or [stem]
    for candidate in candidates:
        candidate = re.sub(r"(简历|证书|工程师|云服务部|数字化办公|云事业部)", "", candidate)
        match = re.search(r"[\u4e00-\u9fff]{2,4}", candidate)
        if match:
            return match.group(0)
    return None


def extract_person_name_from_ocr_text(text: str | None) -> str | None:
    value = clean_value(text)
    if not value:
        return None

    patterns = [
        r"Issued\s+to\s+(?P<value>[\u4e00-\u9fffA-Za-z .-]{2,40})",
        r"Presented\s+to\s+(?P<value>[\u4e00-\u9fffA-Za-z .-]{2,40})",
        r"授予\s*(?P<value>[\u4e00-\u9fffA-Za-z .-]{2,20})",
        r"持证人\s*[:：]?\s*(?P<value>[\u4e00-\u9fffA-Za-z .-]{2,20})",
        r"姓名\s*[:：]?\s*(?P<value>[\u4e00-\u9fffA-Za-z .-]{2,20})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return clean_name(match.group("value"))

    chinese = re.search(r"[\u4e00-\u9fff]{2,4}", value)
    if chinese:
        return chinese.group(0)

    english = re.search(r"\b([A-Z][a-z]{2,20}\s+[A-Z][a-z]{2,20})\b", value)
    if english:
        return clean_name(english.group(1))
    return None


def first_text(asset: dict[str, Any], fields: list[str]) -> str | None:
    for field in fields:
        value = clean_value(asset.get(field))
        if value:
            return value
    return None


def clean_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_name(value: str) -> str:
    text = re.split(r"[\n\r,，;；|]", value.strip())[0].strip()
    text = re.sub(r"\s{2,}", " ", text)
    return text

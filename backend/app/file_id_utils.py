from __future__ import annotations

from typing import Any


FILE_ID_KEYS = {"id", "file_id", "source_file_id"}


def existing_file_ids(metadata: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id") or item.get("file_id") or "")
        for item in list(metadata.get("files") or [])
        if isinstance(item, dict) and str(item.get("id") or item.get("file_id") or "").strip()
    }


def is_existing_file_id(metadata: dict[str, Any], file_id: Any) -> bool:
    value = str(file_id or "").strip()
    return bool(value) and value in existing_file_ids(metadata)


def record_has_existing_file_id(record: dict[str, Any], allowed_ids: set[str]) -> bool:
    file_id = str(record.get("file_id") or record.get("source_file_id") or record.get("id") or "").strip()
    return not file_id or file_id in allowed_ids


def filter_records_to_existing_file_ids(records: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_ids = existing_file_ids(metadata)
    return [item for item in records if isinstance(item, dict) and record_has_existing_file_id(item, allowed_ids)]

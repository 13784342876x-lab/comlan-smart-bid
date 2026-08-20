import json
from pathlib import Path
from typing import Any

from config import METADATA_PATH, ensure_directories


class MetadataStore:
    def __init__(self, path: Path = METADATA_PATH):
        ensure_directories()
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"files": []}
        data = json.loads(self.path.read_text(encoding="utf-8") or '{"files": []}')
        data.setdefault("files", [])
        data.setdefault("pending_items", [])
        data.setdefault("resumes", {})
        data.setdefault("company_qualifications", {})
        data.setdefault("company_performances", {})
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_files(self) -> list[dict[str, Any]]:
        return self.load().get("files", [])

    def list_pending_items(self) -> list[dict[str, Any]]:
        return self.load().get("pending_items", [])

    def add_file(self, record: dict[str, Any]) -> None:
        data = self.load()
        data.setdefault("files", []).append(record)
        self.save(data)

    def get_file(self, file_id: str) -> dict[str, Any]:
        for item in self.list_files():
            if item["id"] == file_id:
                return item
        raise KeyError(f"file_id not found: {file_id}")

    def update_file(self, file_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        files = data.setdefault("files", [])
        for index, item in enumerate(files):
            if item["id"] == file_id:
                files[index] = {**item, **patch}
                self.save(data)
                return files[index]
        raise KeyError(f"file_id not found: {file_id}")

    def upsert_company_qualification(self, file_id: str, qualification: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        files = data.setdefault("files", [])
        stored: dict[str, Any] | None = None
        for index, item in enumerate(files):
            if item["id"] == file_id:
                stored = {
                    **qualification,
                    "source_file_id": file_id,
                    "original_name": item.get("original_name"),
                }
                files[index] = {**item, "company_qualification": stored}
                break
        if stored is None:
            raise KeyError(f"file_id not found: {file_id}")
        data.setdefault("company_qualifications", {})[file_id] = stored
        self.save(data)
        return stored

    def upsert_company_performance(self, file_id: str, performance: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        files = data.setdefault("files", [])
        stored: dict[str, Any] | None = None
        for index, item in enumerate(files):
            if item["id"] == file_id:
                stored = {
                    **performance,
                    "source_file_id": file_id,
                    "original_name": item.get("original_name"),
                }
                files[index] = {**item, "company_performance": stored}
                break
        if stored is None:
            raise KeyError(f"file_id not found: {file_id}")
        data.setdefault("company_performances", {})[file_id] = stored
        self.save(data)
        return stored

    def upsert_pending_items_for_file(self, file_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        data = self.load()
        existing = [item for item in data.setdefault("pending_items", []) if item.get("source_file_id") != file_id]
        data["pending_items"] = existing + items
        self.save(data)
        return items

    def update_pending_item_status(self, item_id: str, status: str) -> dict[str, Any]:
        data = self.load()
        for item in data.setdefault("pending_items", []):
            if item["id"] == item_id:
                item["status"] = status
                self.save(data)
                return item
        raise KeyError(f"pending item not found: {item_id}")

    def pending_items_for_file(self, file_id: str, status: str | None = None) -> list[dict[str, Any]]:
        items = [item for item in self.list_pending_items() if item.get("source_file_id") == file_id]
        if status:
            items = [item for item in items if item.get("status") == status]
        return items

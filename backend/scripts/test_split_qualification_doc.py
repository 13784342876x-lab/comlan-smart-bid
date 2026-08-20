from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.document_splitter import split_document


FILE_ID = "file_2308aa547dd9"

FORBIDDEN_NAMES = [
    "专利证书",
    "软件著作权",
    "检测报告",
    "合同",
    "AAA信用等级证书",
    "营业执照",
]

EXPECTED_KEYWORDS = [
    "ISO9001",
    "ISO14001",
    "ISO20000",
    "ISO27001",
    "ISO27701",
    "ITSS",
    "CS3",
    "CCRC",
    "代理资质证明",
]


def main() -> int:
    record = load_qualification_record()
    items = [dump_item(item) for item in split_document(record)]
    names = [item["name"] for item in items]

    print("split_count=", len(items))
    for item in items:
        print(
            "-",
            item["name"],
            "| evidence_level=",
            item.get("evidence_level"),
            "| evidence_text=",
            item.get("evidence_text", "")[:120],
        )

    failures: list[str] = []
    for forbidden in FORBIDDEN_NAMES:
        if forbidden in names:
            failures.append(f"forbidden item generated: {forbidden}")

    joined_names = "\n".join(names)
    for expected in EXPECTED_KEYWORDS:
        if expected not in joined_names:
            failures.append(f"expected item missing keyword: {expected}")

    for item in items:
        if not str(item.get("evidence_text") or "").strip():
            failures.append(f"{item['name']} missing evidence_text")
        if item.get("evidence_level") not in {"explicit", "title_only"}:
            failures.append(f"{item['name']} invalid evidence_level: {item.get('evidence_level')}")

    if failures:
        print("FAIL")
        for failure in failures:
            print("-", failure)
        return 1

    print("PASS")
    return 0


def load_qualification_record() -> dict:
    metadata_path = BACKEND_ROOT / "data" / "metadata.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    for record in data.get("files", []):
        if record.get("id") == FILE_ID or record.get("original_name") == "资质+代理证书.docx":
            return record
    raise AssertionError(f"qualification record not found in metadata: {FILE_ID}")


def dump_item(item) -> dict:
    return item.model_dump() if hasattr(item, "model_dump") else item.dict()


if __name__ == "__main__":
    raise SystemExit(main())

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.resume_parser import parse_resume
from config import METADATA_PATH
from scripts.find_resume_files import find_candidates, first_assets, first_text


def load_records() -> dict[str, dict[str, Any]]:
    if not METADATA_PATH.exists():
        return {}
    data = json.loads(METADATA_PATH.read_text(encoding="utf-8") or "{}")
    return {record.get("id"): record for record in data.get("files", []) if record.get("id")}


def main() -> None:
    records = load_records()
    candidates = [
        item
        for item in find_candidates()
        if item.get("has_parsed_text") and int(item.get("guessed_resume_score") or 0) >= 6
    ]
    if not candidates:
        print("SKIP: no high-confidence suspected resume file with parsed_text was found in metadata.json")
        return

    tested = []
    for candidate in candidates[:3]:
        record = records[candidate["file_id"]]
        structured_resume = parse_resume(
            parsed_text=first_text(record),
            original_name=record.get("original_name", ""),
            source_path=record.get("source_path"),
            source_folder=record.get("source_folder"),
            department=record.get("department"),
            assets=first_assets(record),
        )
        assert structured_resume.get("person_name") or structured_resume.get("parse_warnings"), candidate
        assert isinstance(structured_resume.get("work_history"), list), candidate
        assert isinstance(structured_resume.get("skills"), list), candidate
        assert isinstance(structured_resume.get("projects"), list), candidate
        assert isinstance(structured_resume.get("certificates"), list), candidate
        has_asset_ocr_text = any(str(asset.get("ocr_text") or "").strip() for asset in first_assets(record))
        embedded_certificates = [
            item for item in structured_resume.get("certificates", []) if item.get("source_type") == "embedded_image"
        ]
        if has_asset_ocr_text:
            assert any(item.get("evidence_level") == "ocr_text" for item in embedded_certificates), candidate
        else:
            assert any(item.get("needs_ocr") for item in embedded_certificates) or not embedded_certificates, candidate
        tested.append(
            {
                "file_id": candidate["file_id"],
                "original_name": candidate["original_name"],
                "person_name": structured_resume.get("person_name"),
                "work_history_count": len(structured_resume.get("work_history", [])),
                "skill_count": len(structured_resume.get("skills", [])),
                "project_count": len(structured_resume.get("projects", [])),
                "certificate_count": len(structured_resume.get("certificates", [])),
                "embedded_ocr_certificate_count": len(
                    [item for item in embedded_certificates if item.get("evidence_level") == "ocr_text"]
                ),
                "needs_ocr_count": len([item for item in embedded_certificates if item.get("needs_ocr")]),
                "parse_warnings": structured_resume.get("parse_warnings", []),
            }
        )

    print(json.dumps({"tested_count": len(tested), "tested": tested}, ensure_ascii=False, indent=2))
    print("resume cli roundtrip tests passed")


if __name__ == "__main__":
    main()

import json
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.company_qualification import (
    build_company_qualification_model,
    parse_company_qualification,
    parse_company_qualification_file,
    parse_company_qualification_requirements,
)
import app.company_qualification as company_qualification_module
from app.metadata_store import MetadataStore


def main() -> None:
    text = """
投标人须具备 CMMI 5级 认证。
投标人应具有 DCMM 二级及以上能力。
重复描述：DCMM 2级及以上。
年份 2023 不应被识别为等级。
"""
    requirements = parse_company_qualification_requirements(text)
    assert {"name": "CMMI", "level": 5} in requirements, requirements
    assert {"name": "DCMM", "level": 2, "or_above": True} in requirements, requirements
    assert not any(item.get("level") == 3 and item.get("name") == "CMMI" for item in requirements), requirements

    parsed = parse_company_qualification(text)
    assert parsed["requirements"] == requirements, parsed

    model = build_company_qualification_model(text)
    assert model["company_certificates"] == requirements, model

    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
        handle.write("企业资质要求：CMMI Level 5，DCMM 3级。")
        temp_path = Path(handle.name)
    try:
        file_result = parse_company_qualification_file(temp_path)
        assert {"name": "CMMI", "level": 5} in file_result["requirements"], file_result
        assert {"name": "DCMM", "level": 3} in file_result["requirements"], file_result
    finally:
        temp_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        metadata_path = Path(temp_dir) / "metadata.json"
        store = MetadataStore(metadata_path)
        store.save(
            {
                "files": [
                    {
                        "id": "file_company_001",
                        "original_name": "company-requirements.txt",
                        "parsed_text": "Enterprise qualification: CMMI Level 5.\nEnterprise qualification: DCMM Level 2.",
                    }
                ]
            }
        )
        original_metadata_store = company_qualification_module.MetadataStore
        company_qualification_module.MetadataStore = lambda: store
        try:
            metadata_result = parse_company_qualification_file("file_company_001")
        finally:
            company_qualification_module.MetadataStore = original_metadata_store
        assert {"name": "CMMI", "level": 5} in metadata_result["requirements"], metadata_result
        assert {"name": "DCMM", "level": 2} in metadata_result["requirements"], metadata_result

        stored = store.upsert_company_qualification(
            "file_company_001",
            {
                "requirements": metadata_result["requirements"],
                "source": metadata_result["source"],
                "parsed_at": "2026-06-30T00:00:00",
                "parser_version": "company_qualification.v1",
            },
        )
        data = store.load()
        record = data["files"][0]
        assert record["company_qualification"]["requirements"] == metadata_result["requirements"], data
        assert data["company_qualifications"]["file_company_001"]["requirements"] == metadata_result["requirements"], data
        assert stored["source_file_id"] == "file_company_001", stored
        assert stored["original_name"] == "company-requirements.txt", stored

    print(
        json.dumps(
            {
                "requirements": requirements,
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("company qualification tests passed")


if __name__ == "__main__":
    main()

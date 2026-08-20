import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.enterprise_knowledge_query import query_enterprise_knowledge


def sample_metadata() -> dict:
    return {
        "files": [
            {
                "id": "file_resume_001",
                "original_name": "冯振书简历.docx",
                "source_folder": "工程师简历/云服务部",
                "parsed_text": "个人简历 VMware Azure ACP",
                "assets": [
                    {
                        "id": "asset_001",
                        "ocr_text": "Alibaba Cloud ACP certificate",
                    }
                ],
                "resume_parse": {
                    "person_name": "冯振书",
                    "skills": [
                        {"name": "Python", "years": "3年"},
                        {"name": "VMware", "years": "5年"},
                    ],
                    "certificates": [
                        {
                            "canonical_id": "alibaba_cloud_acp",
                            "family": "alibaba_cloud",
                            "standard_name": "Alibaba Cloud Certified Professional",
                            "display_name": "ACP",
                            "sources": ["text", "ocr"],
                            "persons": ["冯振书"],
                            "confidence": 0.95,
                        }
                    ],
                    "capability_profile": {
                        "alibaba_cloud_level": "strong",
                    },
                },
            },
            {
                "id": "file_tender_001",
                "original_name": "招标文件-企业资质要求.txt",
                "source_folder": "投标项目/一标段",
                "parsed_text": "Enterprise qualification draft: CMMI Level 4 and DCMM Level 1.",
                "assets": [
                    {
                        "id": "asset_ocr_001",
                        "ocr_text": "企业资质 CMMI DCMM",
                    }
                ],
                "company_qualification": {
                    "requirements": [
                        {"name": "CMMI", "level": 5},
                        {"name": "DCMM", "level": 2, "or_above": True},
                    ],
                    "source_file_id": "file_tender_001",
                    "original_name": "招标文件-企业资质要求.txt",
                    "parsed_at": "2026-06-30T00:00:00",
                    "parser_version": "company_qualification.v1",
                },
            },
        ],
        "company_qualifications": {
            "file_tender_001": {
                "requirements": [
                    {"name": "CMMI", "level": 5},
                    {"name": "DCMM", "level": 2, "or_above": True},
                ],
                "source_file_id": "file_tender_001",
                "original_name": "招标文件-企业资质要求.txt",
                "parsed_at": "2026-06-30T00:00:00",
                "parser_version": "company_qualification.v1",
            }
        },
    }


def main() -> None:
    metadata = sample_metadata()

    cmmi = query_enterprise_knowledge("CMMI", metadata)
    assert cmmi["hits"]["qualifications"], cmmi
    assert any(item.get("name") == "CMMI" and item.get("level") == 5 for item in cmmi["hits"]["qualifications"]), cmmi
    assert not any(item.get("name") == "CMMI" and item.get("level") == 4 for item in cmmi["hits"]["qualifications"]), cmmi

    dcmm = query_enterprise_knowledge("DCMM", metadata)
    assert dcmm["hits"]["qualifications"], dcmm
    assert any(item.get("name") == "DCMM" and item.get("level") == 2 for item in dcmm["hits"]["qualifications"]), dcmm
    assert not any(item.get("name") == "DCMM" and item.get("level") == 1 for item in dcmm["hits"]["qualifications"]), dcmm

    acp = query_enterprise_knowledge("ACP", metadata)
    assert acp["hits"]["certificates"], acp
    assert acp["hits"]["certificates"][0]["canonical_id"] == "alibaba_cloud_acp", acp

    skill = query_enterprise_knowledge("Python", metadata)
    assert skill["hits"]["personnel"], skill
    assert skill["hits"]["personnel"][0]["person_name"] == "冯振书", skill

    file_result = query_enterprise_knowledge("招标文件", metadata)
    assert file_result["hits"]["files"], file_result
    assert file_result["hits"]["files"][0]["file_id"] == "file_tender_001", file_result

    print(
        json.dumps(
            {
                "cmmi_hits": len(cmmi["hits"]["qualifications"]),
                "dcmm_hits": len(dcmm["hits"]["qualifications"]),
                "acp_hits": len(acp["hits"]["certificates"]),
                "skill_hits": len(skill["hits"]["personnel"]),
                "file_hits": len(file_result["hits"]["files"]),
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("enterprise knowledge query tests passed")


if __name__ == "__main__":
    main()

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.certificate_ownership import batch_resolve_certificates, resolve_certificate_owner
from app.resume_parser import parse_resume


def main() -> None:
    file_owner = resolve_certificate_owner(
        {
            "file_id": "file_xxx",
            "resume_person_name": "冯振书",
            "original_name": "云服务部-其他人20230322.docx",
            "ocr_text": "Issued to Someone Else",
        }
    )
    assert file_owner["person_name"] == "冯振书", file_owner
    assert file_owner["file_id"] == "file_xxx", file_owner
    assert file_owner["confidence"] == 1.0, file_owner
    assert file_owner["source"] == "file_id", file_owner

    filename_owner = resolve_certificate_owner({"original_name": "云服务部-冯振书20230322.docx"})
    assert filename_owner["person_name"] == "冯振书", filename_owner
    assert filename_owner["confidence"] == 0.8, filename_owner
    assert filename_owner["source"] == "filename", filename_owner

    ocr_owner = resolve_certificate_owner({"ocr_text": "VMware Certified Professional Issued to Zhenshu Feng"})
    assert ocr_owner["person_name"] == "Zhenshu Feng", ocr_owner
    assert ocr_owner["confidence"] == 0.6, ocr_owner
    assert ocr_owner["source"] == "ocr", ocr_owner

    unknown = resolve_certificate_owner({"evidence_text": ""})
    assert unknown["person_name"] == "unknown", unknown
    assert unknown["confidence"] == 0.0, unknown
    assert unknown["source"] == "unknown", unknown

    batch = batch_resolve_certificates(
        [
            {"file_id": "file_1", "resume_person_name": "张三", "name": "ACP"},
            {"ocr_text": "Issued to Li Si", "name": "VCP"},
        ]
    )
    assert batch[0]["person_name"] == "张三", batch
    assert batch[0]["ownership_confidence"] == 1.0, batch
    assert batch[1]["person_name"] == "Li Si", batch
    assert batch[1]["ownership_confidence"] == 0.6, batch

    resume = parse_resume(
        """
工程师简介
姓名：冯振书
性别：男
工作年限：6年
相关证书
VMware VCP
        """,
        original_name="云服务部-冯振书20230322.docx",
        department="云服务部",
        file_id="file_resume_001",
    )
    cert = resume["certificates"][0]
    assert cert["canonical_id"] == "vmware_vcp", cert
    assert cert["persons"] == ["冯振书"], cert
    assert cert["sources"] == ["text"], cert
    assert cert["confidence"] == 0.95, cert

    print(
        json.dumps(
            {
                "file_owner": file_owner,
                "filename_owner": filename_owner,
                "ocr_owner": ocr_owner,
                "unknown": unknown,
                "resume_certificate_owner": {
                    "persons": cert["persons"],
                    "sources": cert["sources"],
                    "confidence": cert["confidence"],
                },
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("certificate ownership tests passed")


if __name__ == "__main__":
    main()

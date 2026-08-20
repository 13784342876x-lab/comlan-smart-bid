import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.certificate_canonical import merge_canonical_certificates


def by_id(certificates: list[dict], canonical_id: str) -> dict:
    matches = [item for item in certificates if item.get("canonical_id") == canonical_id]
    assert len(matches) == 1, (canonical_id, certificates)
    return matches[0]


def test_acp_text_ocr_file_merge() -> None:
    certificates = merge_canonical_certificates(
        [
            {
                "family": "alibaba_cloud",
                "standard_name": "Alibaba Cloud Certified Professional",
                "short_name": "ACP",
                "display_name": "Alibaba Cloud ACP",
                "source_type": "text",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
            {
                "source_type": "embedded_image",
                "evidence_level": "ocr_text",
                "ocr_text": "AlibabaCloudCertifiedProfessional ACP CertificateID: ACP01211100405165",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
            {
                "source_type": "file",
                "evidence_text": "Alibaba Cloud ACP certificate file",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
        ]
    )
    acp = by_id(certificates, "alibaba_cloud_acp")
    assert acp["sources"] == ["text", "ocr", "file"], acp
    assert acp["persons"] == ["冯振书"], acp
    assert acp["display_name"] == "ACP", acp
    assert acp["confidence"] == 0.95, acp


def test_vcp_multi_source_merge() -> None:
    certificates = merge_canonical_certificates(
        [
            {
                "family": "vmware",
                "standard_name": "VMware Certified Professional",
                "short_name": "VCP",
                "source_type": "text",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
            {
                "source_type": "embedded_image",
                "evidence_level": "ocr_text",
                "ocr_text": "VMware Certified Professional Data Center Virtualization VCP-DCV",
                "person_name": "吴云畅",
                "family_confidence": 0.9,
            },
            {
                "source_type": "file",
                "evidence_text": "VMware VCP certificate",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
        ]
    )
    vcp = by_id(certificates, "vmware_vcp")
    assert vcp["sources"] == ["text", "ocr", "file"], vcp
    assert vcp["persons"] == ["冯振书", "吴云畅"], vcp
    assert vcp["confidence"] == 0.95, vcp


def test_az104_multi_source_merge() -> None:
    certificates = merge_canonical_certificates(
        [
            {
                "family": "microsoft_azure",
                "standard_name": "Microsoft Azure Administrator",
                "short_name": "AZ-104",
                "source_type": "text",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
            {
                "source_type": "embedded_image",
                "evidence_level": "ocr_text",
                "ocr_text": "Microsoft Azure Administrator AZ-104 certification",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
            {
                "source_type": "file",
                "evidence_text": "AZ-104 certificate file",
                "person_name": "冯振书",
                "family_confidence": 0.9,
            },
        ]
    )
    az104 = by_id(certificates, "microsoft_azure_az_104")
    assert az104["sources"] == ["text", "ocr", "file"], az104
    assert az104["persons"] == ["冯振书"], az104
    assert az104["display_name"] == "AZ-104", az104
    assert az104["confidence"] == 0.95, az104


def main() -> None:
    test_acp_text_ocr_file_merge()
    test_vcp_multi_source_merge()
    test_az104_multi_source_merge()
    print(
        json.dumps(
            {
                "canonical_cases": ["alibaba_cloud_acp", "vmware_vcp", "microsoft_azure_az_104"],
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("certificate canonical tests passed")


if __name__ == "__main__":
    main()

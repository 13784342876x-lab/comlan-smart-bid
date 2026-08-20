import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.certificate_family import match_certificate_family


def assert_family(text: str, family: str, canonical: str, confidence: float) -> None:
    result = match_certificate_family(text)
    assert result["family"] == family, (text, result)
    assert result["canonical"] == canonical, (text, result)
    assert result["confidence"] == confidence, (text, result)
    assert result["matched_alias"], (text, result)


def main() -> None:
    assert_family("VCP-DCV", "vmware", "VMware Certified Professional", 0.9)
    assert_family("Azure Administrator AZ-104", "microsoft_azure", "Microsoft Azure Administrator", 0.9)
    assert_family("Alibaba Cloud ACP", "alibaba_cloud", "Alibaba Cloud Certified Professional", 0.9)
    assert_family("HCIP", "huawei", "Huawei Certified", 0.9)
    assert_family("AWS Certified Solutions Architect", "aws", "AWS Certified", 0.6)

    mixed = match_certificate_family("HAS OBTAINED THE CERTIFICATE AlibabaCloudCertifiedProfessional ACP")
    assert mixed["family"] == "alibaba_cloud", mixed
    assert mixed["confidence"] == 0.9, mixed

    nutanix = match_certificate_family("NUTANIX NCP Nutanix Certified Professional")
    assert nutanix["family"] is None, nutanix
    assert nutanix["confidence"] == 0.0, nutanix

    print(
        json.dumps(
            {
                "cases": [
                    "VCP-DCV",
                    "Azure Administrator AZ-104",
                    "Alibaba Cloud ACP",
                    "HCIP",
                    "AWS Certified Solutions Architect",
                    "AlibabaCloudCertifiedProfessional ACP",
                    "NUTANIX NCP is not forced into another family",
                ],
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("certificate family tests passed")


if __name__ == "__main__":
    main()

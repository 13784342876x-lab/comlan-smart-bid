import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.certificate_family import match_certificate_family
from app.certificate_standardizer import build_capability_profile, standardize_certificate
from app.resume_parser import parse_resume


def standardize_from_text(text: str) -> dict:
    family = match_certificate_family(text)
    standardized = standardize_certificate(family["family"], family["matched_alias"])
    return {**family, **standardized}


def main() -> None:
    vcp = standardize_from_text("VCP")
    assert vcp["family"] == "vmware", vcp
    assert vcp["standard_name"] == "VMware Certified Professional", vcp
    assert vcp["display_name"] == "VCP-DCV", vcp

    az104 = standardize_from_text("Azure Administrator AZ-104")
    assert az104["family"] == "microsoft_azure", az104
    assert az104["standard_name"] == "Microsoft Azure Administrator", az104
    assert az104["display_name"] == "AZ-104", az104

    acp = standardize_from_text("Alibaba Cloud ACP")
    assert acp["family"] == "alibaba_cloud", acp
    assert acp["standard_name"] == "Alibaba Cloud Certified Professional", acp
    assert acp["display_name"] == "Alibaba Cloud ACP", acp

    profile = build_capability_profile(
        [
            {
                "usable": True,
                "family": "vmware",
                "family_matched_alias": "VCP",
                "family_confidence": 0.9,
            },
            {
                "usable": True,
                "family": "microsoft_azure",
                "family_matched_alias": "Azure",
                "family_confidence": 0.6,
            },
            {
                "usable": True,
                "family": "alibaba_cloud",
                "family_matched_alias": "ACP",
                "family_confidence": 0.9,
            },
        ]
    )
    assert profile["vmware_level"] == "strong", profile
    assert profile["azure_level"] == "medium", profile
    assert profile["alibaba_cloud_level"] == "strong", profile

    resume = parse_resume(
        """
工程师简介
姓名：测试人员
性别：男
工作年限：6年
相关证书
VMware VCP
Alibaba Cloud ACP
Azure Administrator
        """,
        original_name="标准化测试.docx",
        department="云服务部",
    )
    certs = resume["certificates"]
    assert any(item.get("standard_name") == "VMware Certified Professional" for item in certs), certs
    assert any(item.get("canonical_id") == "alibaba_cloud_acp" and item.get("display_name") == "ACP" for item in certs), certs
    assert resume["capability_profile"]["vmware_level"] == "strong", resume["capability_profile"]
    assert resume["capability_profile"]["alibaba_cloud_level"] == "strong", resume["capability_profile"]
    assert resume["capability_profile"]["azure_level"] == "medium", resume["capability_profile"]

    print(
        json.dumps(
            {
                "standardization_cases": {
                    "VCP": vcp,
                    "AZ-104": az104,
                    "ACP": acp,
                },
                "capability_profile": profile,
                "status": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("certificate standardization tests passed")


if __name__ == "__main__":
    main()

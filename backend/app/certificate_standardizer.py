from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


STANDARD_CERT_MAP = {
    "vmware": {
        "standard_name": "VMware Certified Professional",
        "short_name": "VCP",
        "display_name": "VCP-DCV",
    },
    "alibaba_cloud": {
        "standard_name": "Alibaba Cloud Certified Professional",
        "short_name": "ACP",
        "display_name": "Alibaba Cloud ACP",
    },
    "microsoft_azure": {
        "standard_name": "Microsoft Azure Administrator",
        "short_name": "AZ-104",
        "display_name": "AZ-104",
    },
    "aws": {
        "standard_name": "AWS Certified",
        "short_name": "AWS",
        "display_name": "AWS Certified",
    },
    "huawei": {
        "standard_name": "Huawei Certified",
        "short_name": "HCIP",
        "display_name": "HCIP",
    },
}

CAPABILITY_KEYS = {
    "vmware": "vmware_level",
    "alibaba_cloud": "alibaba_cloud_level",
    "microsoft_azure": "azure_level",
    "aws": "aws_level",
    "huawei": "huawei_level",
}

CORE_ALIASES = {
    "vmware": {"VCP", "VCP-DCV"},
    "alibaba_cloud": {"ACP"},
    "microsoft_azure": {"AZ-104"},
}


def standardize_certificate(family: str | None, alias: str | None = None) -> dict[str, Any]:
    if not family or family not in STANDARD_CERT_MAP:
        return {
            "family": family,
            "standard_name": None,
            "short_name": None,
            "display_name": None,
            "source_alias": alias,
        }

    standard = STANDARD_CERT_MAP[family]
    return {
        "family": family,
        "standard_name": standard["standard_name"],
        "short_name": standard["short_name"],
        "display_name": standard["display_name"],
        "source_alias": alias,
    }


def build_capability_profile(certificates: list[dict[str, Any]]) -> dict[str, str]:
    counts: Counter[str] = Counter()
    has_core: defaultdict[str, bool] = defaultdict(bool)
    has_weak: defaultdict[str, bool] = defaultdict(bool)

    for certificate in certificates:
        if not certificate.get("usable"):
            continue
        family = certificate.get("family")
        if family not in STANDARD_CERT_MAP:
            continue

        counts[family] += 1
        alias_values = {
            str(certificate.get("family_matched_alias") or "").upper(),
            str(certificate.get("certificate_short_name") or "").upper(),
            str(certificate.get("name") or "").upper(),
        }
        if alias_values & CORE_ALIASES.get(family, set()):
            has_core[family] = True
        if float(certificate.get("family_confidence") or 0.0) < 0.6:
            has_weak[family] = True

    profile: dict[str, str] = {}
    for family, count in counts.items():
        key = CAPABILITY_KEYS[family]
        if count >= 2 or has_core[family]:
            profile[key] = "strong"
        elif count == 1:
            profile[key] = "low" if has_weak[family] else "medium"
    return profile

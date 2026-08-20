from __future__ import annotations

import re
from typing import Any


CERTIFICATE_FAMILIES = {
    "vmware": {
        "canonical": "VMware Certified Professional",
        "aliases": [
            "VCP",
            "VCP-DCV",
            "VMware VCP",
            "VMware Certified Professional",
        ],
    },
    "alibaba_cloud": {
        "canonical": "Alibaba Cloud Certified Professional",
        "aliases": [
            "ACP",
            "Alibaba Cloud ACP",
            "Alibaba Cloud Certified Professional",
            "阿里云认证",
            "阿里云ACP",
        ],
    },
    "microsoft_azure": {
        "canonical": "Microsoft Azure Administrator",
        "aliases": [
            "AZ-104",
            "Azure Administrator",
            "Azure Admin",
            "Microsoft Azure",
        ],
    },
    "aws": {
        "canonical": "AWS Certified",
        "aliases": [
            "AWS Certified",
            "AWS",
            "Solutions Architect",
        ],
    },
    "huawei": {
        "canonical": "Huawei Certified",
        "aliases": [
            "HCIA",
            "HCIP",
            "HCIE",
            "华为认证",
        ],
    },
}


STRONG_ALIASES = {
    "vmware": ["VCP", "VCP-DCV"],
    "alibaba_cloud": ["ACP"],
    "microsoft_azure": ["AZ-104"],
    "huawei": ["HCIA", "HCIP", "HCIE"],
}

MEDIUM_ALIASES = {
    "vmware": ["VMware"],
    "microsoft_azure": ["Azure"],
    "alibaba_cloud": ["Alibaba Cloud", "阿里云"],
    "aws": ["AWS"],
    "huawei": ["Huawei", "华为"],
}

WEAK_ALIASES = {
    "vmware": ["Certified Professional"],
    "alibaba_cloud": ["Cloud Certified"],
    "microsoft_azure": ["Administrator"],
}

NON_FAMILY_WEAK_BLOCKLIST = [
    "Nutanix",
    "NCP",
    "NCSC",
    "SCTP",
    "Sangfor",
    "Citrix",
]


def match_certificate_family(text: str | None) -> dict[str, Any]:
    value = " ".join(str(text or "").split())
    if not value:
        return _empty_result()

    for family, aliases in STRONG_ALIASES.items():
        matched = _first_alias_match(value, aliases)
        if matched:
            return _result(family, 0.9, matched)

    for family, aliases in MEDIUM_ALIASES.items():
        matched = _first_alias_match(value, aliases)
        if matched:
            return _result(family, 0.6, matched)

    if _first_alias_match(value, NON_FAMILY_WEAK_BLOCKLIST):
        return _empty_result()

    for family, aliases in WEAK_ALIASES.items():
        matched = _first_alias_match(value, aliases)
        if matched:
            return _result(family, 0.3, matched)

    return _empty_result()


def _result(family: str, confidence: float, matched_alias: str) -> dict[str, Any]:
    config = CERTIFICATE_FAMILIES[family]
    return {
        "family": family,
        "canonical": config["canonical"],
        "confidence": confidence,
        "matched_alias": matched_alias,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "family": None,
        "canonical": None,
        "confidence": 0.0,
        "matched_alias": None,
    }


def _first_alias_match(text: str, aliases: list[str]) -> str | None:
    for alias in aliases:
        if _alias_matches(text, alias):
            return alias
    return None


def _alias_matches(text: str, alias: str) -> bool:
    if re.search(r"^[A-Za-z0-9-]+$", alias):
        return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I))
    return alias.lower() in text.lower()

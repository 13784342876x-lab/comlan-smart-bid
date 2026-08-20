from __future__ import annotations

import re
from typing import Any

from app.certificate_family import match_certificate_family
from app.certificate_standardizer import standardize_certificate


FUZZY_ALIASES = ["ACP", "VCP", "AZ-104"]
SOURCE_ORDER = {"text": 0, "ocr": 1, "file": 2}


def merge_canonical_certificates(certificates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical_items: list[dict[str, Any]] = []
    state_by_id: dict[str, dict[str, Any]] = {}

    for certificate in certificates:
        enriched = enrich_certificate(certificate)
        canonical_id = build_canonical_id(enriched)
        if not canonical_id:
            continue

        matched_id = find_matching_canonical_id(enriched, canonical_items)
        target_id = matched_id or canonical_id
        if target_id not in state_by_id:
            state_by_id[target_id] = new_canonical_state(enriched, target_id)
            canonical_items.append(state_by_id[target_id])
            continue
        merge_into_state(state_by_id[target_id], enriched)

    return [finalize_canonical_state(item) for item in canonical_items]


def enrich_certificate(certificate: dict[str, Any]) -> dict[str, Any]:
    item = dict(certificate)
    family = clean_value(item.get("family"))
    matched_alias = clean_value(item.get("family_matched_alias"))
    text = certificate_text(item)

    if not family:
        family_result = match_certificate_family(text)
        family = clean_value(family_result.get("family"))
        item["family"] = family
        item["family_matched_alias"] = matched_alias or clean_value(family_result.get("matched_alias"))
        item["family_confidence"] = item.get("family_confidence") or family_result.get("confidence", 0.0)

    if family and not clean_value(item.get("standard_name")):
        standard = standardize_certificate(family, clean_value(item.get("family_matched_alias")))
        item["standard_name"] = standard.get("standard_name")
        item["short_name"] = item.get("short_name") or standard.get("short_name")
        item["display_name"] = item.get("display_name") or standard.get("display_name")

    if not clean_value(item.get("short_name")):
        item["short_name"] = (
            clean_value(item.get("certificate_short_name"))
            or clean_value(item.get("family_matched_alias"))
            or extract_fuzzy_alias(text)
        )

    return item


def build_canonical_id(certificate: dict[str, Any]) -> str | None:
    family = clean_value(certificate.get("family"))
    short_name = clean_value(certificate.get("short_name")) or clean_value(certificate.get("certificate_short_name"))
    if not family or not short_name:
        return None
    return f"{slugify(family)}_{slugify(short_name)}"


def find_matching_canonical_id(certificate: dict[str, Any], canonical_items: list[dict[str, Any]]) -> str | None:
    for item in canonical_items:
        if should_merge(certificate, item):
            return str(item["canonical_id"])
    return None


def should_merge(certificate: dict[str, Any], canonical_item: dict[str, Any]) -> bool:
    family = clean_value(certificate.get("family"))
    standard_name = clean_value(certificate.get("standard_name"))
    if family and family == canonical_item.get("family"):
        return True
    if standard_name and standard_name == canonical_item.get("standard_name"):
        return True

    aliases = aliases_for(certificate)
    if aliases & set(canonical_item.get("_aliases", set())):
        return True

    fuzzy = fuzzy_aliases_for(certificate_text(certificate))
    return bool(fuzzy & set(canonical_item.get("_fuzzy_aliases", set())))


def new_canonical_state(certificate: dict[str, Any], canonical_id: str) -> dict[str, Any]:
    short_name = clean_value(certificate.get("short_name")) or clean_value(certificate.get("certificate_short_name"))
    return {
        "canonical_id": canonical_id,
        "family": clean_value(certificate.get("family")),
        "standard_name": clean_value(certificate.get("standard_name")),
        "display_name": short_name or clean_value(certificate.get("display_name")) or clean_value(certificate.get("name")),
        "_persons": ordered_set_values(person_values(certificate)),
        "_sources": ordered_set_values([source_value(certificate)]),
        "_confidences": confidence_values(certificate),
        "_aliases": aliases_for(certificate),
        "_fuzzy_aliases": fuzzy_aliases_for(certificate_text(certificate)),
    }


def merge_into_state(state: dict[str, Any], certificate: dict[str, Any]) -> None:
    state["family"] = state.get("family") or clean_value(certificate.get("family"))
    state["standard_name"] = state.get("standard_name") or clean_value(certificate.get("standard_name"))
    short_name = clean_value(certificate.get("short_name")) or clean_value(certificate.get("certificate_short_name"))
    state["display_name"] = state.get("display_name") or short_name or clean_value(certificate.get("display_name"))
    state["_persons"] = ordered_set_values([*state.get("_persons", []), *person_values(certificate)])
    state["_sources"] = ordered_set_values([*state.get("_sources", []), source_value(certificate)])
    state["_confidences"].extend(confidence_values(certificate))
    state["_aliases"].update(aliases_for(certificate))
    state["_fuzzy_aliases"].update(fuzzy_aliases_for(certificate_text(certificate)))


def finalize_canonical_state(state: dict[str, Any]) -> dict[str, Any]:
    sources = sorted(state.get("_sources", []), key=lambda value: SOURCE_ORDER.get(value, 99))
    confidence = canonical_confidence(state.get("_confidences", []), sources)
    return {
        "canonical_id": state.get("canonical_id"),
        "family": state.get("family"),
        "standard_name": state.get("standard_name"),
        "display_name": state.get("display_name"),
        "persons": state.get("_persons", []),
        "sources": sources,
        "confidence": confidence,
    }


def canonical_confidence(values: list[float], sources: list[str]) -> float:
    base = max(values) if values else 0.0
    if len(sources) > 1:
        base += 0.05 * (len(sources) - 1)
    return round(min(base, 0.95), 2)


def aliases_for(certificate: dict[str, Any]) -> set[str]:
    aliases = {
        clean_alias(certificate.get("short_name")),
        clean_alias(certificate.get("certificate_short_name")),
        clean_alias(certificate.get("family_matched_alias")),
        clean_alias(certificate.get("display_name")),
    }
    aliases.update(fuzzy_aliases_for(certificate_text(certificate)))
    return {alias for alias in aliases if alias}


def fuzzy_aliases_for(text: str) -> set[str]:
    return {alias for alias in FUZZY_ALIASES if alias_matches(text, alias)}


def extract_fuzzy_alias(text: str) -> str | None:
    aliases = fuzzy_aliases_for(text)
    for alias in FUZZY_ALIASES:
        if alias in aliases:
            return alias
    return None


def source_value(certificate: dict[str, Any]) -> str | None:
    source_type = clean_value(certificate.get("source_type"))
    evidence_level = clean_value(certificate.get("evidence_level"))
    if source_type == "embedded_image" or evidence_level == "ocr_text" or clean_value(certificate.get("ocr_text")):
        return "ocr"
    if source_type == "text" or evidence_level == "explicit_text":
        return "text"
    if source_type == "file" or clean_value(certificate.get("file_id")) or clean_value(certificate.get("ownership_file_id")):
        return "file"
    return source_type


def person_values(certificate: dict[str, Any]) -> list[str]:
    values = [
        certificate.get("person_name"),
        certificate.get("holder_name"),
        certificate.get("owner_person_name"),
    ]
    return [value for value in (clean_value(item) for item in values) if value and value != "unknown"]


def confidence_values(certificate: dict[str, Any]) -> list[float]:
    values = [
        safe_float(certificate.get("family_confidence")),
        safe_float(certificate.get("standardization_confidence")),
        safe_float(certificate.get("ownership_confidence")),
    ]
    return [value for value in values if value > 0]


def certificate_text(certificate: dict[str, Any]) -> str:
    values = [
        certificate.get("name"),
        certificate.get("normalized_name"),
        certificate.get("certificate_full_name"),
        certificate.get("certificate_short_name"),
        certificate.get("standard_name"),
        certificate.get("display_name"),
        certificate.get("family_matched_alias"),
        certificate.get("evidence_text"),
        certificate.get("ocr_text"),
    ]
    return "\n".join(str(value) for value in values if value)


def ordered_set_values(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_value(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def clean_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_alias(value: Any) -> str | None:
    text = clean_value(value)
    return text.upper() if text else None


def alias_matches(text: str, alias: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I))


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    return text.strip("_")


def safe_float(value: Any) -> float:
    if isinstance(value, str):
        value = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(value.lower(), value)
    try:
        return float(value)
    except Exception:
        return 0.0

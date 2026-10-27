from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.tender_source_guard import (
    is_tender_source_file,
    tender_source_material_types,
    tender_source_type,
)


SOURCE_MANIFEST_SCHEMA_VERSION = "bid_source_manifest_v2"

TRANSLATION_TERMS = (
    "translated",
    "translation",
    "translated copy",
    "翻译版",
    "译文",
    "中译",
    "中文译本",
)
COPY_TERMS = ("copy", "duplicate", "副本", "复件", "备份")
WORKING_NAME_TERMS = (
    "supplier suggestion",
    "supplier recommendation",
    "vendor suggestion",
    "vendor recommendation",
    "bidder working",
    "filled response",
    "服务商建议",
    "供应商建议",
    "投标人建议",
    "已填写",
    "工作稿",
    "内部稿",
    "加工版",
)
GENERIC_MARKER_WORDS = {
    "appendix",
    "attachment",
    "criteria",
    "document",
    "file",
    "material",
    "recommendation",
    "response",
    "service",
    "suggestion",
    "supplier",
    "technical",
    "template",
    "vendor",
    "working",
}


def resolve_bid_sources(
    manifest_entries: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    inspected_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve project-local source ownership, logical lineage, roles, and anchors.

    This resolver deliberately consumes parser/classifier output.  It never opens a
    document with a second parser and never queries the global knowledge base.
    """

    records = {
        str(item.get("id") or item.get("file_id") or ""): item
        for item in metadata.get("files") or []
        if isinstance(item, dict)
    }
    inspected_by_id = {
        str(item.get("file_id") or ""): item
        for item in inspected_sources or []
        if isinstance(item, dict)
    }
    files: list[dict[str, Any]] = []
    features: dict[str, dict[str, Any]] = {}
    for original_entry in manifest_entries:
        entry = deepcopy(original_entry)
        file_id = str(entry.get("file_id") or "")
        record = records.get(file_id)
        if record is None:
            raise ValueError(f"source manifest record is missing from run metadata: {file_id}")
        feature = _build_feature(entry, record, inspected_by_id.get(file_id))
        features[file_id] = feature
        files.append(entry)

    bidder_markers = _infer_bidder_markers(features.values())
    for file_id, feature in features.items():
        role, origin, reasons = _classify_source(feature, bidder_markers)
        feature["logical_document_role"] = role
        feature["document_role"] = role
        feature["source_origin"] = origin
        feature["ownership_role"] = _ownership_role(origin)
        feature["classification_reasons"] = reasons

    _fold_exact_duplicates(features)
    _fold_translations(features)
    _fold_bidder_derivatives(features)
    _fold_semantic_tender_derivatives(features)

    tender_candidates: list[dict[str, Any]] = []
    score_candidates: list[dict[str, Any]] = []
    for feature in features.values():
        if _eligible_buyer_canonical(feature):
            tender_score, tender_reasons = _tender_anchor_score(feature)
            if tender_score > 0:
                tender_candidates.append(_candidate(feature, tender_score, tender_reasons))
            score_score, score_reasons = _score_anchor_score(feature)
            if score_score > 0:
                score_candidates.append(_candidate(feature, score_score, score_reasons))

    tender_candidates = _sorted_candidates(tender_candidates)
    score_candidates = _sorted_candidates(score_candidates)
    tender_file_id, tender_error = _select_anchor(
        tender_candidates,
        kind="tender",
        minimum_score=50,
    )
    score_file_id: str | None = None
    score_error: str | None = None
    if tender_file_id:
        score_file_id, score_error = _select_anchor(
            score_candidates,
            kind="score",
            minimum_score=45,
        )
        if not score_file_id and score_error and "no viable" in score_error:
            score_file_id = tender_file_id
            score_error = None

    entry_by_id = {str(item.get("file_id") or ""): item for item in files}
    for file_id, feature in features.items():
        entry = entry_by_id[file_id]
        selected_as: list[str] = []
        if file_id == tender_file_id:
            selected_as.append("tender")
        if file_id == score_file_id:
            selected_as.append("score")
        tender_candidate = next(
            (item for item in tender_candidates if item["file_id"] == file_id), None
        )
        score_candidate = next(
            (item for item in score_candidates if item["file_id"] == file_id), None
        )
        relation_reason = feature.get("relation_reason")
        reasons = list(feature.get("classification_reasons") or [])
        if relation_reason:
            reasons.append(str(relation_reason))
        if file_id == tender_file_id:
            reasons.append("selected_as_tender_anchor:highest_confidence_buyer_source")
        if file_id == score_file_id:
            reasons.append(
                "selected_as_score_anchor:highest_confidence_scoring_or_criteria_source"
                if score_candidate
                else "selected_as_score_anchor:fallback_to_tender_source"
            )
        relation = bool(feature.get("duplicate_of") or feature.get("derivative_of"))
        origin = str(feature["source_origin"])
        entry.update(
            {
                "source_origin": origin,
                "ownership_role": feature["ownership_role"],
                "document_role": feature["document_role"],
                "logical_document_role": feature["logical_document_role"],
                "detected_role": feature["document_role"],
                "logical_source_id": feature.get("logical_source_id") or file_id,
                "duplicate_of": feature.get("duplicate_of"),
                "derivative_of": feature.get("derivative_of"),
                "selected_as": selected_as,
                "selection_score": tender_candidate["score"] if tender_candidate else 0,
                "score_selection_score": score_candidate["score"] if score_candidate else 0,
                "selection_reason": "; ".join(_unique(reasons)),
                "included_for_requirements": origin == "buyer_tender_source" and not relation,
                "included_for_writing_context": origin != "unresolved" and not bool(feature.get("duplicate_of")),
                "file_sha256": feature.get("file_sha256"),
                "normalized_content_sha256": feature.get("normalized_content_sha256"),
                "normalized_basename": feature.get("normalized_basename"),
                "classification": deepcopy(feature.get("classification")),
                "tender_guard": deepcopy(feature.get("tender_guard")),
            }
        )

    logical_sources = _logical_sources(files)
    errors = [item for item in (tender_error, score_error) if item]
    selection_status = "selected" if tender_file_id and score_file_id and not errors else "ambiguous"
    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "files": files,
        "logical_sources": logical_sources,
        "tender_file_id": tender_file_id,
        "score_file_id": score_file_id,
        "selection_status": selection_status,
        "anchor_candidates": {
            "tender": tender_candidates,
            "score": score_candidates,
        },
        "selection_failure": "; ".join(errors) if errors else None,
    }


def _build_feature(
    entry: dict[str, Any],
    record: dict[str, Any],
    inspected: dict[str, Any] | None,
) -> dict[str, Any]:
    file_id = str(entry.get("file_id") or "")
    name = str(entry.get("original_name") or record.get("original_name") or "")
    text = str(record.get("parsed_text") or "")
    normalized_text = _normalize_content(text)
    classification = record.get("classification") if isinstance(record.get("classification"), dict) else {}
    material_types = tender_source_material_types(record)
    guard_role = str((inspected or {}).get("source_type") or tender_source_type(record))
    return {
        "file_id": file_id,
        "name": name,
        "name_lower": unicodedata.normalize("NFKC", name).lower(),
        "file_ext": str(record.get("file_ext") or Path(name).suffix.lstrip(".")).lower(),
        "text": text,
        "text_lower": unicodedata.normalize("NFKC", text).lower(),
        "parsed_length": len(text.strip()),
        "table_count": len(record.get("tables") or []),
        "classification": deepcopy(classification),
        "tender_guard": {
            "is_tender_source": is_tender_source_file(record),
            "source_type": guard_role,
            "material_types": list(material_types),
            "inspected": bool(inspected),
        },
        "guard_role": guard_role,
        "material_types": set(material_types),
        "is_translated": _contains_any(name, TRANSLATION_TERMS),
        "has_copy_marker": _has_copy_marker(name),
        "normalized_basename": _normalized_basename(name),
        "file_sha256": _source_file_hash(entry),
        "normalized_content_sha256": (
            hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
            if normalized_text
            else None
        ),
        "ascii_signatures": _ascii_signatures(text),
    }


def _classify_source(
    feature: dict[str, Any],
    bidder_markers: set[str],
) -> tuple[str, str, list[str]]:
    name = feature["name_lower"]
    text_head = feature["text_lower"][:16000]
    combined = f"{name}\n{text_head}"
    guard_role = feature["guard_role"]
    classification = feature["classification"]
    reasons: list[str] = []

    matched_bidder_marker = next(
        (marker for marker in bidder_markers if marker and marker in _compact_name(name)), None
    )
    explicit_working = _contains_any(name, WORKING_NAME_TERMS)
    if explicit_working or matched_bidder_marker:
        reasons.append(
            "ownership:explicit_bidder_working_name"
            if explicit_working
            else f"ownership:run_inferred_bidder_marker:{matched_bidder_marker}"
        )
        return "bidder_working_material", "bidder_working_material", reasons

    if feature["is_translated"]:
        reasons.append("lineage:translation_name_marker")
        return "translated_copy", "derived_copy", reasons

    role = "unresolved"
    if guard_role not in {"", "tender_source"}:
        role = guard_role
        reasons.append(f"tender_source_guard:{guard_role}")

    if role == "unresolved" and _is_full_tender_text(name, text_head):
        role = "full_tender_document"
        reasons.append("content_role:rfp_or_tender_body")
    elif role == "unresolved" and _contains_any(
        name,
        ("score table", "scoring table", "evaluation matrix", "evaluation criteria", "评分表", "评标办法", "评审标准"),
    ):
        role = "score_table"
        reasons.append("content_role:explicit_scoring_filename")
    elif role == "unresolved" and _contains_any(
        name,
        ("quotation template", "pricing template", "price schedule", "报价模板", "报价表", "分项报价"),
    ):
        role = "quotation_template"
        reasons.append("content_role:quotation_template")
    elif role == "unresolved" and _contains_any(
        name,
        ("general terms", "gtc", "contract terms", "terms and conditions", "合同条款", "通用条款"),
    ):
        role = "contract_terms"
        reasons.append("content_role:contract_terms")
    elif role == "unresolved" and _contains_any(
        name,
        ("technical criteria", "technical specification", "technical spec", "技术规范", "技术标准", "技术指标", "技术参数"),
    ):
        role = "technical_spec"
        reasons.append("content_role:technical_specification_or_criteria")
    elif role == "unresolved" and _contains_any(
        combined,
        ("compliance commitment", "commitment letter", "disclosure and compliance", "合规承诺", "承诺书", "披露"),
    ):
        role = "compliance_document"
        reasons.append("content_role:buyer_compliance_document")
    elif role == "unresolved" and _contains_any(
        name,
        (
            "appendix",
            "attachment",
            "clarification",
            "exhibit",
            "q&a",
            "questions and answers",
            "附件",
            "附录",
            "答疑",
            "澄清",
        ),
    ):
        role = "tender_attachment"
        reasons.append("content_role:tender_attachment")

    classified_group = str(classification.get("material_group") or "")
    if classified_group:
        reasons.append(f"existing_classification:{classified_group}")
    if feature["tender_guard"]["is_tender_source"]:
        reasons.append("tender_source_guard:admitted")
    if role == "unresolved":
        reasons.append("ownership:unresolved_no_buyer_authority_signal")
        return role, "unresolved", reasons
    return role, "buyer_tender_source", reasons


def _fold_exact_duplicates(features: dict[str, dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for feature in features.values():
        digest = feature.get("file_sha256") or feature.get("normalized_content_sha256")
        if digest:
            groups.setdefault(str(digest), []).append(feature)
    for group in groups.values():
        if len(group) < 2:
            continue
        canonical = min(group, key=_canonical_rank)
        canonical["logical_source_id"] = canonical["file_id"]
        for feature in group:
            if feature is canonical:
                continue
            feature["duplicate_of"] = canonical["file_id"]
            feature["logical_source_id"] = canonical["file_id"]
            feature["source_origin"] = "derived_copy"
            feature["ownership_role"] = "derived"
            feature["logical_document_role"] = canonical["logical_document_role"]
            feature["document_role"] = "duplicate_copy"
            feature["relation_reason"] = "lineage:exact_file_or_normalized_content_hash"


def _fold_translations(features: dict[str, dict[str, Any]]) -> None:
    canonical_by_base: dict[str, list[dict[str, Any]]] = {}
    for feature in features.values():
        if feature.get("duplicate_of") or feature["is_translated"]:
            continue
        canonical_by_base.setdefault(feature["normalized_basename"], []).append(feature)
    for feature in features.values():
        if feature.get("duplicate_of") or not feature["is_translated"]:
            continue
        candidates = canonical_by_base.get(feature["normalized_basename"]) or []
        if not candidates:
            continue
        canonical = min(candidates, key=_canonical_rank)
        feature["derivative_of"] = canonical["file_id"]
        feature["logical_source_id"] = canonical["file_id"]
        feature["source_origin"] = "derived_copy"
        feature["ownership_role"] = "derived"
        feature["logical_document_role"] = canonical["logical_document_role"]
        feature["document_role"] = "translated_copy"
        feature["relation_reason"] = "lineage:translated_name_and_normalized_basename"


def _fold_bidder_derivatives(features: dict[str, dict[str, Any]]) -> None:
    buyers = [
        item
        for item in features.values()
        if item["source_origin"] == "buyer_tender_source"
        and not item.get("duplicate_of")
        and not item.get("derivative_of")
    ]
    for feature in features.values():
        if feature["source_origin"] != "bidder_working_material":
            continue
        base = feature["normalized_basename"]
        related = [
            item
            for item in buyers
            if len(item["normalized_basename"]) >= 8
            and base.startswith(item["normalized_basename"])
            and base != item["normalized_basename"]
        ]
        if not related:
            continue
        canonical = max(related, key=lambda item: len(item["normalized_basename"]))
        feature["derivative_of"] = canonical["file_id"]
        feature["logical_source_id"] = canonical["file_id"]
        feature["logical_document_role"] = canonical["logical_document_role"]
        feature["relation_reason"] = "lineage:bidder-filled_variant_of_buyer_template"


def _fold_semantic_tender_derivatives(features: dict[str, dict[str, Any]]) -> None:
    full_sources = [
        item
        for item in features.values()
        if item["source_origin"] == "buyer_tender_source"
        and item["logical_document_role"] == "full_tender_document"
        and not item.get("duplicate_of")
        and not item.get("derivative_of")
    ]
    if len(full_sources) < 2:
        return
    canonical = min(full_sources, key=_canonical_rank)
    for feature in full_sources:
        if feature is canonical:
            continue
        shared = canonical["ascii_signatures"] & feature["ascii_signatures"]
        if len(shared) < 8 or min(canonical["parsed_length"], feature["parsed_length"]) < 4000:
            continue
        feature["derivative_of"] = canonical["file_id"]
        feature["logical_source_id"] = canonical["file_id"]
        feature["source_origin"] = "derived_copy"
        feature["ownership_role"] = "derived"
        feature["document_role"] = "derived_copy"
        feature["relation_reason"] = f"lineage:same_rfp_project_signatures:{len(shared)}"


def _tender_anchor_score(feature: dict[str, Any]) -> tuple[int, list[str]]:
    role = feature["logical_document_role"]
    base_scores = {
        "full_tender_document": 100,
        "tender_document": 95,
        "tender_main": 95,
        "tender_notice": 78,
        "procurement_package_table": 68,
        "technical_spec": 58,
        "score_table": 52,
        "tender_attachment": 38,
        "compliance_document": 34,
        "contract_terms": 30,
        "quotation_template": 25,
    }
    score = base_scores.get(role, 0)
    reasons = [f"role_priority:{role}:{score}"] if score else []
    if role in {"full_tender_document", "tender_document", "tender_main"}:
        if "rfp" in feature["name_lower"] or "request for proposal" in feature["name_lower"]:
            score += 8
            reasons.append("filename_explicit_rfp:+8")
        if feature["parsed_length"] >= 10000:
            score += 5
            reasons.append("substantial_parsed_body:+5")
    return score, reasons


def _score_anchor_score(feature: dict[str, Any]) -> tuple[int, list[str]]:
    role = feature["logical_document_role"]
    base_scores = {
        "score_table": 100,
        "technical_spec": 55,
        "full_tender_document": 45,
        "tender_document": 42,
        "procurement_package_table": 35,
    }
    score = base_scores.get(role, 0)
    reasons = [f"role_priority:{role}:{score}"] if score else []
    combined = f"{feature['name_lower']}\n{feature['text_lower'][:20000]}"
    name = feature["name_lower"]
    if _contains_any(
        name,
        ("score", "scoring", "evaluation matrix", "evaluation criteria", "评分", "评标", "评审标准", "分值", "权重"),
    ):
        score += 35
        reasons.append("filename_explicit_scoring_or_evaluation:+35")
    elif _contains_any(
        combined,
        ("evaluation criteria", "scoring criteria", "评分标准", "评审标准", "评标办法", "权重", "分值"),
    ):
        score += 18
        reasons.append("parsed_scoring_or_evaluation_content:+18")
    if role == "technical_spec" and _contains_any(
        combined,
        ("mandatory requirement", "mandatory requirements", "must be achieved", "必须满足", "必须实现", "强制要求"),
    ):
        score += 25
        reasons.append("mandatory_technical_criteria:+25")
    if feature["table_count"]:
        score += 5
        reasons.append("structured_tables:+5")
    return score, reasons


def _candidate(feature: dict[str, Any], score: int, reasons: list[str]) -> dict[str, Any]:
    return {
        "file_id": feature["file_id"],
        "original_name": feature["name"],
        "source_origin": feature["source_origin"],
        "role": feature["logical_document_role"],
        "score": score,
        "reasons": reasons,
    }


def _select_anchor(
    candidates: list[dict[str, Any]],
    *,
    kind: str,
    minimum_score: int,
) -> tuple[str | None, str | None]:
    viable = [item for item in candidates if int(item.get("score") or 0) >= minimum_score]
    if not viable:
        return None, f"no viable {kind} anchor among buyer-owned canonical sources"
    top = viable[0]
    if len(viable) > 1:
        second = viable[1]
        same_role = str(top.get("role")) == str(second.get("role"))
        if same_role and int(top["score"]) - int(second["score"]) <= 3:
            summary = ", ".join(
                f"{item['original_name']} ({item['file_id']}, role={item['role']}, score={item['score']})"
                for item in viable[:5]
            )
            return None, f"ambiguous {kind} anchor; manual selection required: {summary}"
    return str(top["file_id"]), None


def _eligible_buyer_canonical(feature: dict[str, Any]) -> bool:
    return (
        feature["source_origin"] == "buyer_tender_source"
        and not feature.get("duplicate_of")
        and not feature.get("derivative_of")
        and feature["logical_document_role"] != "unresolved"
    )


def _infer_bidder_markers(features: Any) -> set[str]:
    markers: set[str] = set()
    for feature in features:
        name = feature["name_lower"]
        term = next((item for item in WORKING_NAME_TERMS if item in name), None)
        if not term:
            continue
        prefix = name.split(term, 1)[0]
        for token in re.findall(r"[a-z][a-z0-9_-]{3,}", prefix):
            if token not in GENERIC_MARKER_WORDS:
                markers.add(_compact_name(token))
        chinese_groups = re.findall(r"[\u4e00-\u9fff]{2,}", prefix)
        if chinese_groups:
            group = chinese_groups[-1]
            for size in range(2, min(6, len(group)) + 1):
                markers.add(group[:size])
    return markers


def _logical_sources(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        groups.setdefault(str(item.get("logical_source_id") or item["file_id"]), []).append(item)
    output: list[dict[str, Any]] = []
    for logical_source_id, members in groups.items():
        canonical = next(
            (item for item in members if item["file_id"] == logical_source_id), members[0]
        )
        output.append(
            {
                "logical_source_id": logical_source_id,
                "canonical_file_id": canonical["file_id"],
                "canonical_name": canonical["original_name"],
                "source_origin": canonical["source_origin"],
                "document_role": canonical["logical_document_role"],
                "member_file_ids": [item["file_id"] for item in members],
            }
        )
    return sorted(output, key=lambda item: (item["canonical_name"].lower(), item["canonical_file_id"]))


def _sorted_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (-int(item.get("score") or 0), str(item.get("original_name") or "").lower(), item["file_id"]),
    )


def _canonical_rank(feature: dict[str, Any]) -> tuple[Any, ...]:
    return (
        bool(feature.get("is_translated")),
        bool(feature.get("has_copy_marker")),
        feature.get("file_ext") not in {"pdf", "xlsx", "docx"},
        feature.get("file_ext") != "pdf",
        -int(feature.get("parsed_length") or 0),
        str(feature.get("name") or "").lower(),
        feature["file_id"],
    )


def _source_file_hash(entry: dict[str, Any]) -> str | None:
    # Prefer the immutable ingested copy so resume is stable even if source-dir changes.
    for key in ("stored_path", "path"):
        value = entry.get(key)
        if not value:
            continue
        path = Path(str(value))
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    return None


def _normalized_basename(name: str) -> str:
    stem = unicodedata.normalize("NFKC", Path(name).stem).lower()
    for term in TRANSLATION_TERMS:
        stem = stem.replace(term, "")
    stem = stem.strip(" ._-")
    stem = re.sub(r"(?:^|[\s_.-])(?:copy|duplicate|副本|复件|备份)(?:\s*\d+)?$", "", stem)
    stem = re.sub(r"\(\s*\d+\s*\)$", "", stem)
    return _compact_name(stem)


def _normalize_content(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _compact_name(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", value).lower())


def _has_copy_marker(name: str) -> bool:
    stem = unicodedata.normalize("NFKC", Path(name).stem).lower()
    for term in TRANSLATION_TERMS:
        stem = stem.replace(term, "")
    stem = stem.strip(" ._-")
    return bool(re.search(r"\(\s*\d+\s*\)$", stem)) or _contains_any(stem, COPY_TERMS)


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return any(term in normalized for term in terms)


def _is_full_tender_text(name: str, text_head: str) -> bool:
    if _contains_any(
        name,
        ("request for proposal", "rfp", "方案征询书", "招标文件", "采购文件"),
    ):
        return True
    combined = f"{name}\n{text_head[:5000]}"
    english_terms = (
        "request for proposal",
        "response instructions",
        "supplier qualification",
        "evaluation / selection process",
        "response specifications",
    )
    chinese_terms = ("投标人须知", "响应文件组成", "方案征询书", "资格条件", "采购需求")
    return sum(term in combined for term in english_terms) >= 3 or sum(
        term in combined for term in chinese_terms
    ) >= 3


def _ascii_signatures(text: str) -> set[str]:
    signatures: set[str] = set()
    signatures.update(item.lower() for item in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text))
    signatures.update(item.lower() for item in re.findall(r"\b[A-Z][A-Z0-9_/-]{2,14}\b", text))
    signatures.update(item.lower() for item in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_/-]*\d[a-zA-Z0-9_/-]{2,}\b", text))
    for item in re.findall(r"\b[A-Za-z][A-Za-z0-9._@-]{3,}\b", text):
        lowered = item.lower()
        if lowered in GENERIC_MARKER_WORDS or lowered in {
            "confidential",
            "document",
            "project",
            "proposal",
            "request",
            "supplier",
            "table",
            "that",
            "this",
            "with",
            "word",
        }:
            continue
        if "@" in lowered or "." in lowered:
            signatures.add(lowered)
    return signatures


def _ownership_role(source_origin: str) -> str:
    return {
        "buyer_tender_source": "buyer",
        "bidder_working_material": "bidder",
        "derived_copy": "derived",
        "unresolved": "unresolved",
    }[source_origin]


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.company_qualification import parse_company_qualification_requirements
from app.file_id_utils import existing_file_ids
from app.tender_source_guard import is_tender_source_file
from config import STORAGE_DIR


QUALIFICATION_KEYWORDS = ["cmmi", "dcmm", "iso", "资质"]
CERTIFICATE_KEYWORDS = ["vcp", "azure", "acp", "aws"]
FILE_KEYWORDS = ["file", "文件", "文档", "材料", "原文", "ocr", "parsed_text", "source_folder", "original_name"]
SKILL_HINTS = ["技能", "skill", "python", "vmware", "citrix", "m365", "powerplatform", "power platform", "linux", "sql"]

EVIDENCE_SUBJECT_ALIASES = {
    "enterprise_qualification": ["资质", "营业执照", "企业证书", "高新技术企业"],
    "management_system_certificate": ["管理体系", "ISO9001", "ISO14001", "ISO45001", "ISO50001", "质量管理体系", "环境管理体系", "职业健康安全管理体系", "能源管理体系"],
    "project_performance": ["业绩", "项目案例", "合同", "中标通知书", "验收", "客户证明"],
    "contract": ["合同", "合同关键页"],
    "award_notice": ["中标通知书", "成交通知书"],
    "acceptance_certificate": ["验收证明", "验收报告"],
    "customer_certificate": ["客户证明", "用户证明"],
    "personnel_resume": ["人员简历", "简历", "工作年限"],
    "personnel_qualification": ["项目负责人", "项目团队", "职称", "人员资格"],
    "personnel_certificate": ["人员证书", "职称证书", "资格证书"],
    "personnel_project_experience": ["人员项目经验", "项目经历", "工作经验"],
    "patent": ["专利", "发明专利", "实用新型"],
    "software_copyright": ["软件著作权", "软著"],
    "technical_achievement": ["科技成果", "技术成果", "创新成果"],
    "financial_evidence": ["财务", "审计", "研发投入", "科研经费"],
    "credit_evidence": ["信用报告", "企业信用", "失信", "行政处罚"],
    "service_capability": ["服务能力", "售后服务", "绩效评价"],
    "equipment_or_resource": ["设备", "设施", "资源清单"],
    "organization_structure": ["组织结构", "人员名单", "研发团队"],
    "other_enterprise_evidence": ["企业材料", "证明材料", "报告", "制度"],
}

EVIDENCE_GROUPS = {
    "enterprise_qualification": {"company_qualification"},
    "management_system_certificate": {"company_qualification"},
    "project_performance": {"company_performance", "winning_case"},
    "contract": {"company_performance"},
    "award_notice": {"company_performance", "winning_case"},
    "acceptance_certificate": {"company_performance"},
    "customer_certificate": {"company_performance"},
    "personnel_resume": {"personnel"},
    "personnel_qualification": {"personnel"},
    "personnel_certificate": {"personnel"},
    "personnel_project_experience": {"personnel"},
    "patent": {"intellectual_property"},
    "software_copyright": {"intellectual_property"},
    "technical_achievement": {"intellectual_property", "company_product"},
    "financial_evidence": {"business_compliance"},
    "credit_evidence": {"business_compliance", "company_qualification"},
    "service_capability": {"supplier_performance", "company_performance"},
    "equipment_or_resource": {"company_product", "technical_writing_asset"},
    "organization_structure": {"personnel", "business_compliance"},
    "other_enterprise_evidence": {"business_compliance", "company_product", "technical_writing_asset"},
}


def query_enterprise_knowledge(query: str, metadata: dict) -> dict:
    query_text = str(query or "").strip()
    categories = detect_query_categories(query_text)
    files = list(metadata.get("files") or [])
    allowed_ids = existing_file_ids(metadata)

    return {
        "query": query_text,
        "hits": {
            "certificates": query_certificates(query_text, files) if should_query(categories, "certificate") else [],
            "personnel": query_personnel(query_text, files) if should_query(categories, "resume") else [],
            "qualifications": query_qualifications(query_text, metadata, files, allowed_ids) if should_query(categories, "company_qualification") else [],
            "files": query_files(query_text, files) if should_query(categories, "file") else [],
        },
    }


def query_enterprise_evidence_candidates(
    query: str,
    metadata: dict[str, Any],
    *,
    evidence_subject_types: list[str] | None = None,
    expected_evidence_types: list[str] | None = None,
) -> dict[str, Any]:
    """Return enterprise candidate records; evidence usability is decided downstream."""
    subjects = [str(item) for item in list(evidence_subject_types or []) if str(item).strip()]
    expected = [str(item) for item in list(expected_evidence_types or []) if str(item).strip()]
    terms = evidence_query_terms(query, subjects, expected)
    allowed_groups = {
        group
        for subject in subjects
        for group in EVIDENCE_GROUPS.get(subject, set())
    }
    candidates: list[dict[str, Any]] = []
    for record in list(metadata.get("files") or []):
        if not isinstance(record, dict) or is_tender_source_file(record):
            continue
        profile = record.get("material_profile") if isinstance(record.get("material_profile"), dict) else {}
        group = str(profile.get("material_group") or infer_record_group(record))
        searchable = enterprise_record_text(record)
        matched_terms = [term for term in terms if normalize(term) in normalize(searchable)]
        group_match = bool(group and group in allowed_groups)
        if not matched_terms and not group_match:
            continue
        candidates.append(
            {
                "file_id": record.get("id"),
                "original_name": record.get("original_name"),
                "material_group": group or "unknown",
                "material_types": list(profile.get("material_types") or []),
                "matched_terms": matched_terms,
                "group_match": group_match,
                "storage_key": record.get("storage_key"),
                "physical_file_exists": enterprise_file_exists(record),
                "record": record,
            }
        )
    return {
        "query": str(query or ""),
        "evidence_subject_types": subjects,
        "expected_evidence_types": expected,
        "query_terms": terms,
        "candidates": candidates,
    }


def evidence_query_terms(query: str, subjects: list[str], expected: list[str]) -> list[str]:
    values = [str(query or ""), *expected]
    for subject in subjects:
        values.extend(EVIDENCE_SUBJECT_ALIASES.get(subject, []))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for term in [value, *re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}|[\u4e00-\u9fff]{2,}", value)]:
            normalized = normalize(term)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            result.append(term)
    return result[:80]


def enterprise_record_text(record: dict[str, Any]) -> str:
    profile = record.get("material_profile") if isinstance(record.get("material_profile"), dict) else {}
    resume = resume_parse(record)
    return "\n".join(
        [
            str(record.get("original_name") or ""),
            str(record.get("category") or ""),
            " ".join(str(item) for item in list(record.get("tags") or [])),
            " ".join(str(item) for item in list(profile.get("material_types") or [])),
            str(record_text(record)),
            str(asset_ocr_text(record)),
            join_mapping(resume),
            join_mapping(record.get("company_performance") or {}),
        ]
    )


def infer_record_group(record: dict[str, Any]) -> str:
    category = str(record.get("category") or "")
    name = str(record.get("original_name") or "")
    if resume_parse(record):
        return "personnel"
    if record.get("company_performance") or "业绩" in category or "业绩" in name:
        return "company_performance"
    if any(term in enterprise_record_text(record) for term in ["ISO 9001", "ISO9001", "资质证书", "管理体系认证"]):
        return "company_qualification"
    return "unknown"


def enterprise_file_exists(record: dict[str, Any]) -> bool:
    storage_key = str(record.get("storage_key") or "").replace("\\", "/").lstrip("/")
    if not storage_key:
        return False
    path = Path(storage_key)
    if path.parts and path.parts[0].lower() == "storage":
        path = Path(*path.parts[1:])
    return (STORAGE_DIR / path).exists()


def detect_query_categories(query: str) -> set[str]:
    value = normalize(query)
    categories: set[str] = set()
    if any(keyword in value for keyword in QUALIFICATION_KEYWORDS):
        categories.add("company_qualification")
    if any(keyword in value for keyword in CERTIFICATE_KEYWORDS):
        categories.add("certificate")
    if any(keyword in value for keyword in SKILL_HINTS):
        categories.add("resume")
    if any(keyword in value for keyword in FILE_KEYWORDS):
        categories.add("file")
    return categories or {"certificate", "resume", "company_qualification", "file"}


def should_query(categories: set[str], category: str) -> bool:
    return category in categories


def query_certificates(query: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in files:
        resume = resume_parse(record)
        for certificate in list(resume.get("certificates") or []):
            if not text_matches(query, certificate_text(certificate)):
                continue
            hit = {
                "file_id": record.get("id"),
                "person_name": resume.get("person_name"),
                "canonical_id": certificate.get("canonical_id"),
                "standard_name": certificate.get("standard_name"),
                "family": certificate.get("family"),
                "display_name": certificate.get("display_name"),
                "sources": list(certificate.get("sources") or []),
                "persons": list(certificate.get("persons") or []),
                "confidence": certificate.get("confidence"),
            }
            key = (str(hit.get("file_id")), str(hit.get("canonical_id") or hit.get("standard_name") or hit.get("display_name")))
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
    return hits


def query_personnel(query: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for record in files:
        resume = resume_parse(record)
        if not resume:
            continue
        searchable = "\n".join(
            [
                str(resume.get("person_name") or ""),
                join_dicts(resume.get("skills")),
                join_dicts(resume.get("certificates")),
                join_mapping(resume.get("capability_profile")),
            ]
        )
        if not text_matches(query, searchable):
            continue
        hits.append(
            {
                "file_id": record.get("id"),
                "original_name": record.get("original_name"),
                "person_name": resume.get("person_name"),
                "skills": list(resume.get("skills") or []),
                "certificates": list(resume.get("certificates") or []),
                "capability_profile": resume.get("capability_profile") or {},
            }
        )
    return hits


def query_qualifications(query: str, metadata: dict, files: list[dict[str, Any]], allowed_ids: set[str] | None = None) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()
    allowed_ids = allowed_ids or existing_file_ids(metadata)

    for item in qualification_items_from_metadata(metadata):
        file_id = requirement_file_id(item)
        if file_id and file_id not in allowed_ids:
            continue
        add_qualification_hit(query, hits, seen, item, None)

    for record in files:
        if is_tender_source_file(record):
            continue
        for requirement in qualification_items_from_record(record):
            add_qualification_hit(query, hits, seen, requirement, record)

    return hits


def add_qualification_hit(
    query: str,
    hits: list[dict[str, Any]],
    seen: set[tuple[str, str, int | None]],
    requirement: dict[str, Any],
    record: dict[str, Any] | None,
) -> None:
    if not text_matches(query, join_mapping(requirement)):
        return
    name = str(requirement.get("name") or "")
    level = safe_int(requirement.get("level"))
    file_id = str(record.get("id") if record else requirement_file_id(requirement) or "")
    key = (file_id, name, level)
    if key in seen:
        return
    seen.add(key)
    hit = dict(requirement)
    if record:
        hit["file_id"] = record.get("id")
        hit["original_name"] = record.get("original_name")
    elif file_id:
        hit["file_id"] = file_id
    hits.append(hit)


def query_files(query: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for record in files:
        searchable = "\n".join(
            [
                str(record.get("original_name") or ""),
                str(record.get("source_folder") or ""),
                str(record.get("parsed_text") or ""),
                asset_ocr_text(record),
            ]
        )
        if not text_matches(query, searchable):
            continue
        hits.append(
            {
                "file_id": record.get("id"),
                "original_name": record.get("original_name"),
                "source_folder": record.get("source_folder"),
                "parsed_text_preview": preview(record.get("parsed_text")),
                "ocr_text_preview": preview(asset_ocr_text(record)),
            }
        )
    return hits


def qualification_items_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    value = metadata.get("company_qualification")
    items.extend(qualification_items_from_value(value))

    value = metadata.get("company_qualifications")
    if isinstance(value, dict):
        if value.get("requirements") or value.get("company_certificates"):
            items.extend(qualification_items_from_value(value))
        else:
            for file_id, qualification in value.items():
                for item in qualification_items_from_value(qualification):
                    normalized_item = dict(item)
                    normalized_item.setdefault("source_file_id", file_id)
                    if isinstance(qualification, dict):
                        normalized_item.setdefault("original_name", qualification.get("original_name"))
                    items.append(normalized_item)
    else:
        items.extend(qualification_items_from_value(value))
    return [item for item in items if isinstance(item, dict)]


def qualification_items_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    if is_tender_source_file(record):
        return []
    items: list[dict[str, Any]] = []
    for key in ["company_qualification", "company_qualifications"]:
        for item in qualification_items_from_value(record.get(key)):
            normalized_item = dict(item)
            normalized_item.setdefault("source_file_id", record.get("id"))
            normalized_item.setdefault("original_name", record.get("original_name"))
            items.append(normalized_item)
    if items:
        return [item for item in items if isinstance(item, dict)]

    parsed_text = record_text(record)
    if parsed_text.strip():
        for item in parse_company_qualification_requirements(parsed_text):
            normalized_item = dict(item)
            normalized_item.setdefault("source_file_id", record.get("id"))
            normalized_item.setdefault("original_name", record.get("original_name"))
            items.append(normalized_item)
    return [item for item in items if isinstance(item, dict)]


def qualification_items_from_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        items = value.get("requirements") or value.get("company_certificates") or []
        return list(items) if isinstance(items, list) else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def requirement_file_id(requirement: dict[str, Any]) -> str | None:
    for key in ["file_id", "source_file_id"]:
        value = requirement.get(key)
        if value:
            return str(value)
    return None


def resume_parse(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("resume_parse")
    return value if isinstance(value, dict) else {}


def record_text(record: dict[str, Any]) -> str:
    for key in ["parsed_text", "text", "content"]:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    parse_result = record.get("parse_result")
    if isinstance(parse_result, dict):
        for key in ["parsed_text", "text", "content"]:
            value = parse_result.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def certificate_text(certificate: dict[str, Any]) -> str:
    return "\n".join(
        str(value)
        for value in [
            certificate.get("canonical_id"),
            certificate.get("standard_name"),
            certificate.get("family"),
            certificate.get("display_name"),
            " ".join(str(item) for item in certificate.get("sources") or []),
            " ".join(str(item) for item in certificate.get("persons") or []),
        ]
        if value
    )


def asset_ocr_text(record: dict[str, Any]) -> str:
    texts: list[str] = []
    for asset in list(record.get("assets") or []):
        if isinstance(asset, dict) and asset.get("ocr_text"):
            texts.append(str(asset.get("ocr_text")))
    return "\n".join(texts)


def text_matches(query: str, text: str) -> bool:
    query_value = normalize(query)
    text_value = normalize(text)
    if not query_value:
        return False
    if query_value in text_value:
        return True
    tokens = [token for token in query_value.replace("_", " ").split() if len(token) >= 2]
    return bool(tokens and all(token in text_value for token in tokens))


def normalize(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def join_dicts(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "\n".join(join_mapping(item) for item in value if isinstance(item, dict))


def join_mapping(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value or "")
    return " ".join(str(item) for item in flatten_values(value) if item is not None)


def flatten_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        result: list[Any] = []
        for item in value.values():
            result.extend(flatten_values(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(flatten_values(item))
        return result
    return [value]


def preview(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None

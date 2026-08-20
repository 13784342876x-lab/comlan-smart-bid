from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.department_profiles import (
    STANDARD_DEPARTMENTS,
    build_project_requirement_profile,
    load_department_profiles,
    match_project_to_departments,
)
from config import METADATA_PATH


DEPARTMENTS = [
    "云服务部",
    "安全事业部",
    "数字化办公事业部",
    "数据管理事业部",
    "服务运营管理部",
    "硬件维保事业部",
    "网络事业部",
]

DEPARTMENT_KEYWORDS = {
    "云服务部": ["云服务", "云平台", "Azure", "阿里云", "AWS", "VMware", "Citrix", "虚拟化", "云迁移", "云运维"],
    "安全事业部": ["安全", "网络安全", "数据安全", "等保", "隐私保护", "访问控制", "安全评估", "应急响应", "CCRC", "漏洞", "安全分析"],
    "数字化办公事业部": ["数字化办公", "M365", "Microsoft 365", "PowerPlatform", "PowerApps", "PowerAutomate", "Teams", "SharePoint", "Exchange", "OA", "流程自动化"],
    "数据管理事业部": ["数据管理", "数据治理", "工业数据", "区块链", "大数据", "数据资产", "数据监管", "数据存证", "隐私计算", "联邦学习", "智能合约"],
    "服务运营管理部": ["服务运营", "服务台", "ITIL", "SLA", "运维管理", "服务流程", "质量管理", "项目运营"],
    "硬件维保事业部": ["硬件维保", "设备维护", "服务器", "存储", "备件", "现场维修", "巡检", "终端"],
    "网络事业部": ["网络", "交换机", "路由器", "防火墙", "网络规划", "网络集成", "网络运维", "无线网络"],
}

ROLE_KEYWORDS = {
    "project_manager": ["项目经理", "项目负责人", "负责人", "PM", "PMP"],
    "developer": ["开发", "研发", "Python", "PowerApps", "智能合约", "数据开发"],
    "security_engineer": ["安全", "漏洞", "等保", "应急响应", "安全评估"],
    "cloud_engineer": ["云", "虚拟化", "VMware", "Citrix", "Azure", "阿里云", "AWS"],
    "data_engineer": ["数据", "数据治理", "数据管理", "区块链", "智能合约"],
    "ops_engineer": ["运维", "巡检", "SLA", "服务台", "ITIL"],
    "network_engineer": ["网络", "交换机", "路由器", "防火墙"],
}

PROJECT_DEPARTMENT_RULES = {
    "数据管理事业部": ["区块链", "工业数据", "数据管理", "数据治理", "数据监管", "数据存证", "隐私计算", "联邦学习", "智能合约"],
    "安全事业部": ["数据安全", "隐私保护", "访问控制", "安全分析", "监管", "安全性证明", "安全评估", "漏洞"],
    "云服务部": ["云平台", "虚拟化", "云服务", "Azure", "阿里云", "VMware", "Citrix", "AWS"],
    "数字化办公事业部": ["M365", "Microsoft 365", "PowerPlatform", "PowerApps", "PowerAutomate", "OA", "流程自动化", "SharePoint", "Teams"],
    "服务运营管理部": ["运维", "SLA", "服务台", "ITIL", "服务流程", "项目运营"],
    "硬件维保事业部": ["硬件", "服务器", "存储", "备件", "巡检", "设备维护"],
    "网络事业部": ["网络", "交换机", "路由器", "防火墙", "网络集成", "无线网络"],
}


def load_metadata(path: Path = METADATA_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"metadata file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8") or '{"files": []}')


def save_metadata(metadata: dict[str, Any], path: Path = METADATA_PATH) -> None:
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def find_file(metadata: dict[str, Any], file_id: str) -> dict[str, Any]:
    for item in list(metadata.get("files") or []):
        if item.get("id") == file_id or item.get("file_id") == file_id:
            return item
    raise ValueError(f"file not found in metadata: {file_id}")


def classify_personnel_file(file_id: str, path: Path = METADATA_PATH, force: bool = False) -> dict[str, Any]:
    metadata = load_metadata(path)
    record = find_file(metadata, file_id)
    profile = apply_personnel_profile(record, force=force)
    save_metadata(metadata, path)
    return {"file_id": record.get("id"), "original_name": record.get("original_name"), "personnel_profile": profile}


def classify_all_personnel(path: Path = METADATA_PATH, force: bool = False) -> dict[str, Any]:
    metadata = load_metadata(path)
    updated = 0
    skipped_manual_confirmed = 0
    department_counts: dict[str, int] = {}
    for record in list(metadata.get("files") or []):
        material_profile = record.get("material_profile") if isinstance(record.get("material_profile"), dict) else {}
        if material_profile.get("material_group") != "personnel" and not isinstance(record.get("resume_parse"), dict):
            continue
        existing = record.get("personnel_profile")
        if (
            isinstance(existing, dict)
            and existing.get("department_confirm_status") == "manual_confirmed"
            and not force
        ):
            profile = dict(existing)
            skipped_manual_confirmed += 1
        else:
            profile = apply_personnel_profile(record, force=force)
            updated += 1
        department = str(profile.get("department") or "unknown")
        department_counts[department] = department_counts.get(department, 0) + 1
    save_metadata(metadata, path)
    return {
        "updated": updated,
        "skipped_manual_confirmed": skipped_manual_confirmed,
        "department_counts": department_counts,
    }


def set_person_department(
    file_id: str,
    department: str,
    path: Path = METADATA_PATH,
) -> dict[str, Any]:
    if department not in available_department_names():
        raise ValueError(f"unknown department: {department}")
    metadata = load_metadata(path)
    record = find_file(metadata, file_id)
    profile = build_personnel_profile(record)
    profile["department"] = department
    profile["department_confirm_status"] = "manual_confirmed"
    profile["confidence"] = 1.0
    profile["requires_manual_confirm"] = False
    profile.setdefault("notes", []).append(f"人工设置部门：{department}")
    record["personnel_profile"] = profile
    save_metadata(metadata, path)
    return {"file_id": record.get("id"), "personnel_profile": profile}


def list_personnel(
    department: str | None = None,
    path: Path = METADATA_PATH,
) -> list[dict[str, Any]]:
    metadata = load_metadata(path)
    rows: list[dict[str, Any]] = []
    for record in list(metadata.get("files") or []):
        profile = record.get("personnel_profile")
        if not isinstance(profile, dict):
            material_profile = record.get("material_profile") if isinstance(record.get("material_profile"), dict) else {}
            if material_profile.get("material_group") != "personnel" and not isinstance(record.get("resume_parse"), dict):
                continue
            profile = build_personnel_profile(record)
        if department and profile.get("department") != department:
            continue
        rows.append(
            {
                "file_id": record.get("id"),
                "original_name": record.get("original_name"),
                "person_name": profile.get("person_name"),
                "department": profile.get("department"),
                "department_confirm_status": profile.get("department_confirm_status"),
                "work_years": profile.get("work_years"),
                "skill_tags": profile.get("skill_tags", []),
                "certificate_tags": profile.get("certificate_tags", []),
                "project_keywords": profile.get("project_keywords", []),
                "confidence": profile.get("confidence"),
            }
        )
    return rows


def inspect_personnel(file_id: str, path: Path = METADATA_PATH) -> dict[str, Any]:
    metadata = load_metadata(path)
    record = find_file(metadata, file_id)
    profile = record.get("personnel_profile") if isinstance(record.get("personnel_profile"), dict) else build_personnel_profile(record)
    resume = resume_parse(record)
    return {
        "file_id": record.get("id"),
        "original_name": record.get("original_name"),
        "category": record.get("category"),
        "source_folder": record.get("source_folder"),
        "tags": record.get("tags", []),
        "personnel_profile": profile,
        "resume_summary": {
            "person_name": first_value(resume, ["person_name", "name"]),
            "work_years": resume.get("work_years"),
            "skills": extract_resume_skills(resume),
            "certificates": extract_resume_certificates(resume),
            "project_experience": extract_resume_projects(resume),
        },
    }


def apply_personnel_profile(record: dict[str, Any], force: bool = False) -> dict[str, Any]:
    existing = record.get("personnel_profile")
    if (
        isinstance(existing, dict)
        and existing.get("department_confirm_status") == "manual_confirmed"
        and not force
    ):
        return dict(existing)
    profile = build_personnel_profile(record)
    record["personnel_profile"] = profile
    return profile


def build_personnel_profile(record: dict[str, Any]) -> dict[str, Any]:
    resume = resume_parse(record)
    skills = extract_resume_skills(resume)
    certificates = extract_resume_certificates(resume)
    projects = extract_resume_projects(resume)
    project_keywords = extract_project_keywords(projects)
    department, status, confidence, dept_notes = infer_department(record, resume, skills, certificates, projects)
    role_tags = infer_role_tags(skills, certificates, projects)
    domain_tags = infer_domain_tags(skills, certificates, projects)
    person_name = str(first_value(resume, ["person_name", "name"]) or extract_person_name_from_filename(str(record.get("original_name") or "")) or "")
    work_years = normalize_work_years(resume.get("work_years"))
    notes = list(dept_notes)
    notes.append("人员项目经验仅作为人员经历画像，不作为公司业绩证明。")
    notes.append("个人证书仅作为个人能力材料，不作为企业资质。")
    return {
        "person_name": person_name,
        "department": department,
        "department_confirm_status": status,
        "role_tags": sorted_unique(role_tags),
        "domain_tags": sorted_unique(domain_tags),
        "skill_tags": sorted_unique(skills),
        "certificate_tags": sorted_unique(certificates),
        "project_keywords": sorted_unique(project_keywords),
        "work_years": work_years,
        "matched_resume_fields": {
            "skills": skills,
            "certificates": certificates,
            "project_experience": projects,
        },
        "usable_for": personnel_usable_for(department, role_tags, domain_tags),
        "not_usable_for": ["company_performance", "company_qualification", "supplier_performance"],
        "confidence": confidence,
        "requires_manual_confirm": status != "manual_confirmed" and confidence < 0.9,
        "notes": notes,
    }


def infer_department(
    record: dict[str, Any],
    resume: dict[str, Any],
    skills: list[str],
    certificates: list[str],
    projects: list[str],
) -> tuple[str, str, float, list[str]]:
    name_hits = explicit_department_hits(str(record.get("original_name") or ""))
    if name_hits:
        department = name_hits[0][0]
        return department, "rule_inferred", 0.9, [f"从文件名识别部门：{department}"]

    source_text = " ".join(
        [
            str(record.get("source_folder") or ""),
            " ".join(str(tag) for tag in list(record.get("tags") or [])),
            str(resume.get("source_folder") or ""),
            str(resume.get("department") or ""),
        ]
    )
    explicit_hits = explicit_department_hits(source_text)
    if explicit_hits:
        department = explicit_hits[0][0]
        return department, "rule_inferred", 0.88, [f"从文件名/source_folder/tags/resume.department 识别部门：{department}"]

    return "", "unknown", 0.35, ["未找到明确部门线索。"]


def department_hits(text: str) -> list[tuple[str, int]]:
    normalized = normalize_text(text)
    hits: list[tuple[str, int]] = []
    for department, keywords in DEPARTMENT_KEYWORDS.items():
        score = 0
        if department in text:
            score += 5
        for keyword in keywords:
            if normalize_text(keyword) in normalized:
                score += 1
        if score:
            hits.append((department, score))
    return sorted(hits, key=lambda item: (-item[1], DEPARTMENTS.index(item[0]) if item[0] in DEPARTMENTS else 999))


def explicit_department_hits(text: str) -> list[tuple[str, int]]:
    normalized = normalize_text(text)
    hits: list[tuple[str, int]] = []
    departments = available_department_names()
    for department in departments:
        score = 0
        for alias in department_aliases(department):
            alias_text = normalize_text(alias)
            if alias_text and alias_text in normalized:
                score = max(score, 5 if alias == department else 3)
        if score:
            hits.append((department, score))
    return sorted(hits, key=lambda item: (-item[1], departments.index(item[0]) if item[0] in departments else 999))


def available_department_names() -> list[str]:
    try:
        names = [str(item.get("department") or "") for item in load_department_profiles()]
        return [name for name in names if name]
    except Exception:
        return list(STANDARD_DEPARTMENTS)


def department_aliases(department: str) -> list[str]:
    aliases = {
        "云服务部": ["云服务部", "云服务"],
        "安全事业部": ["安全事业部"],
        "数字化办公事业部": ["数字化办公事业部", "数字化办公"],
        "数据管理事业部": ["数据管理事业部", "数据管理"],
        "服务运营管理部": ["服务运营管理部", "服务运营"],
        "硬件维保事业部": ["硬件维保事业部", "硬件维保"],
        "网络事业部": ["网络事业部"],
    }
    return aliases.get(department, [department])


def infer_required_departments_from_tender(
    tender_text: str,
    score_items: list[dict[str, Any]] | None = None,
    technical_spec_text: str = "",
) -> dict[str, Any]:
    requirement_profile = build_project_requirement_profile(tender_text, score_items, technical_spec_text)
    return match_project_to_departments(requirement_profile, load_department_profiles())


def infer_role_tags(skills: list[str], certificates: list[str], projects: list[str]) -> list[str]:
    text = " ".join(skills + certificates + projects)
    tags: list[str] = []
    normalized = normalize_text(text)
    for role, keywords in ROLE_KEYWORDS.items():
        if any(normalize_text(keyword) in normalized for keyword in keywords):
            tags.append(role)
    return tags


def infer_domain_tags(skills: list[str], certificates: list[str], projects: list[str]) -> list[str]:
    text = " ".join(skills + certificates + projects)
    tags: list[str] = []
    for department, keywords in DEPARTMENT_KEYWORDS.items():
        hits = [keyword for keyword in keywords if normalize_text(keyword) in normalize_text(text)]
        if hits:
            tags.append(department)
            tags.extend(hits[:5])
    return tags


def personnel_usable_for(department: str, role_tags: list[str], domain_tags: list[str]) -> list[str]:
    values = ["project_team_candidate", "personnel_materials"]
    if department:
        values.append(f"department:{department}")
    if "project_manager" in role_tags:
        values.append("project_owner_candidate")
    if "data_engineer" in role_tags or "security_engineer" in role_tags:
        values.append("technical_core_candidate")
    return sorted_unique(values)


def resume_parse(record: dict[str, Any]) -> dict[str, Any]:
    resume = record.get("resume_parse")
    if isinstance(resume, dict):
        return resume
    structured = record.get("structured_facts")
    if isinstance(structured, dict) and isinstance(structured.get("resume_parse"), dict):
        return structured["resume_parse"]
    return {}


def extract_resume_skills(resume: dict[str, Any]) -> list[str]:
    skills: list[str] = []
    for item in list(resume.get("skills") or []):
        if isinstance(item, dict):
            skills.append(str(item.get("name") or item.get("skill") or item.get("raw_text") or ""))
        else:
            skills.append(str(item))
    profile = resume.get("capability_profile")
    if isinstance(profile, dict):
        skills.extend(str(key) for key, value in profile.items() if value)
    return sorted_unique([value for value in skills if value])


def extract_resume_certificates(resume: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ["certificates", "embedded_certificate_candidates"]:
        for item in list(resume.get(key) or []):
            if isinstance(item, dict):
                values.append(
                    str(
                        item.get("display_name")
                        or item.get("standard_name")
                        or item.get("normalized_name")
                        or item.get("certificate_full_name")
                        or item.get("name")
                        or item.get("evidence_text")
                        or ""
                    )
                )
            else:
                values.append(str(item))
    return sorted_unique([value for value in values if value])


def extract_resume_projects(resume: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ["projects", "project_experience"]:
        for item in list(resume.get(key) or []):
            if isinstance(item, dict):
                values.append(
                    " ".join(
                        str(item.get(field) or "")
                        for field in ["name", "description", "products_or_technologies", "role_or_work_content", "raw_text"]
                    ).strip()
                )
            else:
                values.append(str(item))
    return [value for value in values if value]


def extract_project_keywords(projects: list[str]) -> list[str]:
    keywords: list[str] = []
    text = " ".join(projects)
    for terms in DEPARTMENT_KEYWORDS.values():
        for term in terms:
            if normalize_text(term) in normalize_text(text):
                keywords.append(term)
    return keywords[:40]


def first_value(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if data.get(key):
            return data.get(key)
    return None


def normalize_work_years(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def extract_person_name_from_filename(name: str) -> str:
    stem = Path(name).stem
    if "-" in stem:
        return stem.split("-")[-1]
    return stem


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def sorted_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result

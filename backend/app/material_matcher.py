from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.file_id_utils import existing_file_ids, record_has_existing_file_id
from app.tender_source_guard import is_tender_source_file
from app.enterprise_knowledge_query import (
    asset_ocr_text,
    qualification_items_from_metadata,
    qualification_items_from_record,
    record_text,
    resume_parse,
)
from config import DATA_DIR


TENDER_PROJECTS_PATH = DATA_DIR / "tender_projects.json"

PERSONNEL_KEYWORDS = [
    "项目负责人",
    "项目经理",
    "团队人员",
    "人员结构",
    "研发团队",
    "研发团队规模",
    "工作年限",
    "工作经验",
    "技能",
    "简历",
]
PERSONAL_CERTIFICATE_KEYWORDS = [
    "ACP",
    "Python",
    "VMware",
    "Citrix",
    "Azure",
    "AWS",
    "华为",
    "阿里云",
    "Microsoft",
    "认证工程师",
    "个人证书",
    "资格证书",
    "职称证书",
]
COMPANY_QUALIFICATION_KEYWORDS = [
    "CMMI",
    "DCMM",
    "ISO",
    "ITSS",
    "CCRC",
    "CNAS",
    "企业资质",
    "管理体系认证",
    "信用报告",
    "企业信用",
    "绿色管理体系认证",
    "质量管理体系",
    "环境管理体系",
    "职业健康安全管理体系",
    "能源管理体系",
    "高新技术企业",
]
PROJECT_CASE_KEYWORDS = [
    "业绩",
    "项目经验",
    "案例",
    "合同",
    "验收",
    "验收证明",
    "中标通知书",
    "绩效评价",
    "工作基础",
]
PATENT_KEYWORDS = [
    "专利",
    "创新成果",
    "企业创新",
    "科研经费",
    "创新激励",
    "研发团队规模",
    "知识产权",
    "科技成果",
    "软件著作权",
    "软著",
]
WRITING_KEYWORDS = [
    "服务方案",
    "工作进度及保证措施",
    "应答文件技术部分总体评价",
    "商务投标文件整体响应",
    "绿色发展规划",
    "环境影响评价",
]
PRICE_KEYWORDS = ["报价质量", "价格评分", "报价规则", "报价文件", "报价明细表", "开标一览表", "价格核对"]

MATERIAL_TYPE_LABELS = {
    "person_resume": "人员简历",
    "personal_certificate": "个人证书",
    "company_qualification": "企业资质",
    "project_case": "业绩/案例材料",
    "patent": "专利/创新成果材料",
    "generic_document": "通用证明材料",
}

WEAK_COMPANY_QUALIFICATION_TERMS = [
    "ISO9001",
    "ISO14001",
    "ISO45001",
    "ISO50001",
    "ISO27001",
    "质量管理体系",
    "环境管理体系",
    "职业健康安全管理体系",
    "能源管理体系",
    "信息安全管理体系",
    "信用等级",
    "企业信用",
    "绿色管理体系认证",
    "高新技术企业",
]
SIGNIFICANT_STOP_TERMS = {
    "评分",
    "材料",
    "证明",
    "证明材料",
    "响应",
    "进行",
    "情况",
    "相关",
    "项目",
    "本项目",
    "投标",
    "投标人",
    "供应商",
    "评价",
    "生成",
    "覆盖",
    "规则",
    "页码",
    "索引",
    "企业",
}


def load_tender_projects(path: Path = TENDER_PROJECTS_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"tender projects file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8") or '{"projects": []}')


def save_tender_projects(data: dict[str, Any], path: Path = TENDER_PROJECTS_PATH) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_project_by_lot_name(data: dict[str, Any], lot_name: str) -> dict[str, Any]:
    for project in reversed(list(data.get("projects") or [])):
        if str(project.get("lot_name") or "") == lot_name:
            return project
    raise KeyError(f"tender project not found by lot_name: {lot_name}")


def save_material_analysis_to_project(
    lot_name: str,
    material_analysis: dict[str, Any],
    path: Path = TENDER_PROJECTS_PATH,
) -> dict[str, Any]:
    data = load_tender_projects(path)
    project = find_project_by_lot_name(data, lot_name)
    project["material_analysis"] = {
        "summary": material_analysis.get("summary", {}),
        "task_material_map": material_analysis.get("task_material_map", []),
        "matched_materials": material_analysis.get("matched_materials", []),
        "missing_materials": material_analysis.get("missing_materials", []),
    }
    save_tender_projects(data, path)
    return project["material_analysis"]


def analyze_project_materials(project: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    tasks = list(project.get("generation_tasks") or [])
    score_items = score_items_by_id(project)
    task_material_map = [
        match_generation_task(task, score_items, project, metadata)
        for task in tasks
    ]
    result = {
        "lot_name": project.get("lot_name"),
        "project_id": project.get("project_id"),
        "summary": build_summary(task_material_map),
        "task_material_map": task_material_map,
        "matched_materials": flatten_materials(task_material_map, "matched_materials"),
        "missing_materials": flatten_materials(task_material_map, "missing_materials"),
    }
    return filter_material_analysis_to_existing_files(result, metadata)


def match_generation_task(
    task: dict[str, Any],
    score_items: dict[str, dict[str, Any]],
    project: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    score_item = first_related_score_item(task, score_items)
    task_name = task_display_name(task, score_item)
    task_type = str(task.get("task_type") or "unknown")
    task_text = build_task_text(task, score_item)
    classification_text = build_task_classification_text(task, score_item)
    status_text = build_task_status_text(task, score_item)
    score_range = normalize_score_range(score_item)
    required_types = classify_required_material_types(classification_text, task_type, status_text=status_text)
    base = {
        "task_id": str(task.get("task_id") or ""),
        "task_name": task_name,
        "task_type": task_type,
        "score_range": score_range,
        "required_material_types": required_types,
        "matched_materials": [],
        "missing_materials": [],
        "match_status": "missing",
        "confidence": 0.0,
        "notes": [],
    }

    if is_price_task(status_text, task_type):
        base["match_status"] = "not_applicable"
        base["confidence"] = 1.0
        base["notes"].append("报价测算与价格策略不在 material_matcher V1 范围内。")
        return base

    if is_writing_task(status_text):
        base["match_status"] = "writing_required"
        base["confidence"] = 1.0
        base["notes"].append("该任务主要需要后续 writing_orchestrator 生成章节内容，V1 不作为硬材料缺口。")
        return base

    matched = collect_task_matches(required_types, task_text, task, project, metadata)
    matched = dedupe_materials(matched)[:12]
    primary_types = primary_required_types(required_types)
    covered_types = covered_material_types(matched)
    missing = build_missing_materials(primary_types, covered_types)

    base["matched_materials"] = matched
    base["missing_materials"] = missing
    base["match_status"] = determine_match_status(primary_types, covered_types, matched)
    base["confidence"] = task_confidence(matched, base["match_status"])
    if "generic_document" in required_types and primary_types != ["generic_document"]:
        base["notes"].append("generic_document 用作弱证据补充，不替代结构化材料。")
    return base


def collect_task_matches(
    required_types: list[str],
    task_text: str,
    task: dict[str, Any],
    project: dict[str, Any],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    files = candidate_files(project, metadata)
    if "person_resume" in required_types:
        matches.extend(match_resume_materials(task_text, files))
    if "personal_certificate" in required_types:
        matches.extend(match_personal_certificate_materials(task_text, files))
    if "company_qualification" in required_types:
        matches.extend(match_company_qualification_materials(task_text, metadata, files))
    if "project_case" in required_types:
        matches.extend(match_generic_file_materials(task_text, files, "project_case", PROJECT_CASE_KEYWORDS))
        matches.extend(match_person_project_experience(task_text, files))
    if "patent" in required_types:
        matches.extend(match_generic_file_materials(task_text, files, "patent", PATENT_KEYWORDS))
    if "generic_document" in required_types:
        matches.extend(match_generic_file_materials(task_text, files, "generic_document", generic_terms_for_task(task_text, task)))
    return matches


def match_resume_materials(task_text: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    terms = dedupe_terms(PERSONNEL_KEYWORDS + PERSONAL_CERTIFICATE_KEYWORDS + significant_terms(task_text))
    for record in files:
        resume = resume_parse(record)
        if not resume:
            continue
        searchable = "\n".join(
            [
                str(resume.get("person_name") or ""),
                join_values(resume.get("skills")),
                join_values(resume.get("certificates")),
                join_values(resume.get("projects")),
                join_values(resume.get("capability_profile")),
            ]
        )
        if not contains_any(searchable, terms) and not contains_any(task_text, PERSONNEL_KEYWORDS):
            continue
        matches.append(
            material(
                material_type="person_resume",
                fact_type="resume_parse",
                record=record,
                source="metadata.files[].resume_parse",
                title=record.get("original_name"),
                person=resume.get("person_name"),
                snippet=resume_snippet(resume),
                confidence=0.82,
            )
        )
    return matches


def match_personal_certificate_materials(task_text: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    terms = dedupe_terms(PERSONAL_CERTIFICATE_KEYWORDS + significant_terms(task_text))
    for record in files:
        resume = resume_parse(record)
        if resume:
            for certificate in list(resume.get("certificates") or []):
                cert_text = join_values(certificate)
                if not contains_any(cert_text, terms):
                    continue
                matches.append(
                    material(
                        material_type="personal_certificate",
                        fact_type="canonical_personal_certificate" if certificate.get("canonical_id") else "resume_certificate",
                        record=record,
                        source="metadata.files[].resume_parse.certificates",
                        title=certificate.get("display_name") or certificate.get("standard_name") or certificate.get("name") or record.get("original_name"),
                        person=certificate.get("person_name") or resume.get("person_name"),
                        snippet=preview(cert_text),
                        confidence=safe_confidence(certificate.get("confidence"), 0.86),
                    )
                )
        for asset in list(record.get("assets") or []):
            ocr_text = str(asset.get("ocr_text") or "")
            if not ocr_text or not contains_any(ocr_text, terms):
                continue
            matches.append(
                material(
                    material_type="personal_certificate",
                    fact_type="ocr_personal_certificate",
                    record=record,
                    source="metadata.files[].assets[].ocr_text",
                    title=record.get("original_name"),
                    person=resume.get("person_name") if resume else None,
                    snippet=preview(ocr_text),
                    confidence=safe_confidence(asset.get("ocr_confidence"), 0.68),
                )
            )
    return matches


def match_company_qualification_materials(
    task_text: str,
    metadata: dict[str, Any],
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    terms = qualification_terms_for_task(task_text)
    seen_requirements: set[tuple[str, str, str]] = set()

    for requirement in qualification_items_from_metadata(metadata):
        requirement_text = join_values(requirement)
        if not contains_any(requirement_text, terms):
            continue
        file_id = str(requirement.get("source_file_id") or requirement.get("file_id") or "")
        if file_id and file_id not in existing_file_ids(metadata):
            continue
        key = (file_id, str(requirement.get("name") or ""), str(requirement.get("level") or ""))
        if key in seen_requirements:
            continue
        seen_requirements.add(key)
        matches.append(
            {
                "material_type": "company_qualification",
                "fact_type": "company_qualification",
                "file_id": file_id or None,
                "source": "metadata.company_qualifications",
                "title": requirement.get("original_name") or requirement.get("name") or "企业资质",
                "person": None,
                "snippet": preview(requirement_text),
                "confidence": 0.9,
            }
        )

    for record in files:
        for requirement in qualification_items_from_record(record):
            requirement_text = join_values(requirement)
            if not contains_any(requirement_text, terms):
                continue
            matches.append(
                material(
                    material_type="company_qualification",
                    fact_type="company_qualification",
                    record=record,
                    source="metadata.files[].company_qualification",
                    title=requirement.get("name") or record.get("original_name"),
                    person=None,
                    snippet=preview(requirement_text),
                    confidence=0.9,
                )
            )

    matches.extend(
        match_generic_file_materials(
            task_text,
            files,
            "generic_document",
            terms,
            fact_type="weak_company_qualification_document",
            include_task_terms=False,
        )
    )
    return matches


def qualification_terms_for_task(task_text: str) -> list[str]:
    text = str(task_text or "")
    aliases = {
        "CMMI": ["CMMI"],
        "DCMM": ["DCMM"],
        "ITSS": ["ITSS"],
        "CCRC": ["CCRC"],
        "CNAS": ["CNAS"],
        "ISO9001": ["ISO9001", "质量管理体系"],
        "ISO14001": ["ISO14001", "环境管理体系"],
        "ISO45001": ["ISO45001", "职业健康安全管理体系"],
        "ISO50001": ["ISO50001", "能源管理体系"],
        "ISO27001": ["ISO27001", "信息安全管理体系"],
    }
    terms: list[str] = []
    for key, values in aliases.items():
        if contains_any(text, [key]):
            terms.extend(values)
    if contains_any(text, ["绿色管理体系认证"]):
        terms.extend(["绿色管理体系认证", "ISO9001", "ISO14001", "ISO45001", "ISO50001", "质量管理体系", "环境管理体系", "职业健康安全管理体系", "能源管理体系"])
    if contains_any(text, ["管理体系认证"]) and not terms:
        terms.extend(WEAK_COMPANY_QUALIFICATION_TERMS)
    if contains_any(text, ["信用报告", "企业信用"]):
        terms.extend(["信用报告", "企业信用", "信用等级", "公共信用信息报告", "企业信用信息公示报告"])
    if contains_any(text, ["高新技术企业"]):
        terms.append("高新技术企业")
    if not terms:
        terms.extend([term for term in COMPANY_QUALIFICATION_KEYWORDS if contains_any(text, [term])])
    return dedupe_terms(terms or significant_terms(task_text))


def match_person_project_experience(task_text: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    terms = dedupe_terms(PROJECT_CASE_KEYWORDS + significant_terms(task_text))
    for record in files:
        resume = resume_parse(record)
        if not resume:
            continue
        for project in list(resume.get("projects") or []):
            project_text = join_values(project)
            if not contains_any(project_text, terms):
                continue
            matches.append(
                material(
                    material_type="project_case",
                    fact_type="person_project_experience",
                    record=record,
                    source="metadata.files[].resume_parse.projects",
                    title=project.get("name") or record.get("original_name"),
                    person=resume.get("person_name"),
                    snippet=preview(project_text),
                    confidence=0.58,
                )
            )
    return matches


def match_generic_file_materials(
    task_text: str,
    files: list[dict[str, Any]],
    material_type: str,
    terms: list[str],
    fact_type: str | None = None,
    include_task_terms: bool = True,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    search_terms = dedupe_terms(terms + (significant_terms(task_text) if include_task_terms else []))
    for record in files:
        if is_tender_source_file(record):
            continue
        searchable = "\n".join(
            [
                str(record.get("original_name") or ""),
                str(record.get("source_folder") or ""),
                str(record.get("category") or ""),
                record_text(record),
                asset_ocr_text(record),
            ]
        )
        if not contains_any(searchable, search_terms):
            continue
        matches.append(
            material(
                material_type=material_type,
                fact_type=fact_type or f"{material_type}_document",
                record=record,
                source=best_text_source(record, searchable),
                title=record.get("original_name"),
                person=None,
                snippet=best_snippet(searchable, search_terms),
                confidence=generic_confidence(material_type, searchable, search_terms),
            )
        )
    return matches


def classify_required_material_types(task_text: str, task_type: str = "unknown", status_text: str | None = None) -> list[str]:
    status_source = status_text if status_text is not None else task_text
    if is_price_task(status_source, task_type):
        return []
    if is_writing_task(status_source):
        return ["writing_required"]

    required: list[str] = []
    if contains_any(task_text, PERSONNEL_KEYWORDS):
        required.extend(["person_resume", "personal_certificate"])
    if contains_any(task_text, PERSONAL_CERTIFICATE_KEYWORDS):
        required.extend(["personal_certificate", "person_resume"])
    if contains_any(task_text, COMPANY_QUALIFICATION_KEYWORDS):
        required.extend(["company_qualification", "generic_document"])
    if contains_any(task_text, PROJECT_CASE_KEYWORDS):
        required.extend(["project_case", "generic_document"])
    if contains_any(task_text, PATENT_KEYWORDS):
        required.extend(["patent", "generic_document"])
    if not required:
        required.append("generic_document")
    return dedupe_terms(required)


def is_writing_task(task_text: str) -> bool:
    return contains_any(task_text, WRITING_KEYWORDS)


def is_price_task(task_text: str, task_type: str = "unknown") -> bool:
    return str(task_type or "").lower() == "price" or contains_any(task_text, PRICE_KEYWORDS)


def score_items_by_id(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in list(project.get("score_items") or [])
        if isinstance(item, dict)
    }


def first_related_score_item(task: dict[str, Any], score_items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for score_item_id in list(task.get("related_score_item_ids") or []):
        item = score_items.get(str(score_item_id))
        if item:
            return item
    return {}


def task_display_name(task: dict[str, Any], score_item: dict[str, Any]) -> str:
    return str(
        score_item.get("item_name")
        or score_item.get("name")
        or (list(task.get("related_score_item_ids") or [])[:1] or [""])[0]
        or task.get("target_section")
        or task.get("task_id")
        or ""
    )


def normalize_score_range(score_item: dict[str, Any]) -> dict[str, Any]:
    score_range = score_item.get("score_range")
    if isinstance(score_range, dict):
        return {"min": score_range.get("min"), "max": score_range.get("max")}
    score = score_item.get("score")
    return {"min": None, "max": score if score is not None else None}


def build_task_text(task: dict[str, Any], score_item: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in [
        task.get("task_id"),
        task.get("task_type"),
        task.get("target_section"),
        task.get("generation_instruction"),
        score_item.get("item_name"),
        score_item.get("score_rule"),
        score_item.get("raw_text"),
    ]:
        append_text(parts, value)
    append_list(parts, task.get("related_requirements"))
    append_list(parts, task.get("evidence_needed"))
    append_list(parts, task.get("kb_mapping_hint"))
    return "\n".join(parts)


def build_task_classification_text(task: dict[str, Any], score_item: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in [
        task.get("task_id"),
        task.get("task_type"),
        task.get("target_section"),
        task.get("generation_instruction"),
        score_item.get("item_name"),
        score_item.get("score_rule"),
        score_item.get("raw_text"),
    ]:
        append_text(parts, value)
    append_list(parts, task.get("related_requirements"))
    append_list(parts, task.get("evidence_needed"))
    return "\n".join(parts)


def build_task_status_text(task: dict[str, Any], score_item: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in [
        task.get("task_id"),
        task.get("task_type"),
        task.get("target_section"),
        task.get("generation_instruction"),
        score_item.get("item_name"),
        score_item.get("score_rule"),
        score_item.get("raw_text"),
    ]:
        append_text(parts, value)
    return "\n".join(parts)


def candidate_files(project: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    excluded_ids = {str(project.get("tender_file_id") or ""), str(project.get("score_file_id") or "")}
    allowed_ids = existing_file_ids(metadata)
    excluded_categories = {"招标文件", "评分表"}
    files = []
    for record in list(metadata.get("files") or []):
        if not isinstance(record, dict) or not record_has_existing_file_id(record, allowed_ids):
            continue
        if is_tender_source_file(record):
            continue
        if str(record.get("id") or "") in excluded_ids:
            continue
        if str(record.get("category") or "") in excluded_categories:
            continue
        files.append(record)
    return files


def filter_material_analysis_to_existing_files(material_analysis: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    allowed_ids = existing_file_ids(metadata)
    result = dict(material_analysis)
    task_map: list[dict[str, Any]] = []
    ignored: list[str] = []
    for task in list(result.get("task_material_map") or []):
        if not isinstance(task, dict):
            continue
        task_copy = dict(task)
        matched: list[dict[str, Any]] = []
        for item in list(task_copy.get("matched_materials") or []):
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or item.get("source_file_id") or "")
            if file_id and file_id not in allowed_ids:
                ignored.append(file_id)
                continue
            matched.append(item)
        task_copy["matched_materials"] = matched
        task_map.append(task_copy)
    result["task_material_map"] = task_map
    result["matched_materials"] = flatten_materials(task_map, "matched_materials")
    result["missing_materials"] = flatten_materials(task_map, "missing_materials")
    result["summary"] = build_summary(task_map)
    if ignored:
        result["orphaned_file_ids_ignored"] = sorted(set(ignored))
    return result


def primary_required_types(required_types: list[str]) -> list[str]:
    primary = [item for item in required_types if item not in {"generic_document", "writing_required"}]
    if primary:
        return primary
    return [item for item in required_types if item != "writing_required"]


def build_missing_materials(primary_types: list[str], covered_types: set[str]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for material_type in primary_types:
        if material_type in covered_types:
            continue
        missing.append(
            {
                "material_type": material_type,
                "reason": f"未在当前知识库结构化事实或文件文本中找到{MATERIAL_TYPE_LABELS.get(material_type, material_type)}证据。",
                "suggested_action": f"补充或复核{MATERIAL_TYPE_LABELS.get(material_type, material_type)}，并完成 parse-file/OCR/结构化解析后重新匹配。",
            }
        )
    return missing


def covered_material_types(matched_materials: list[dict[str, Any]]) -> set[str]:
    covered = {str(item.get("material_type") or "") for item in matched_materials}
    if any(str(item.get("fact_type") or "") == "weak_company_qualification_document" for item in matched_materials):
        covered.add("company_qualification")
    return covered


def determine_match_status(
    primary_types: list[str],
    covered_types: set[str],
    matched_materials: list[dict[str, Any]],
) -> str:
    if not primary_types:
        return "not_applicable"
    if not matched_materials:
        return "missing"
    primary_set = set(primary_types)
    if primary_set.issubset(covered_types):
        return "matched"
    if primary_set & covered_types:
        return "partial"
    return "missing"


def task_confidence(matched_materials: list[dict[str, Any]], match_status: str) -> float:
    if match_status in {"writing_required", "not_applicable"}:
        return 1.0
    if not matched_materials:
        return 0.0
    confidence = max(float(item.get("confidence") or 0.0) for item in matched_materials)
    if match_status == "partial":
        confidence = min(confidence, 0.75)
    return round(confidence, 2)


def build_summary(task_material_map: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total_tasks": len(task_material_map),
        "matched": 0,
        "partial": 0,
        "missing": 0,
        "writing_required": 0,
        "not_applicable": 0,
    }
    for item in task_material_map:
        status = str(item.get("match_status") or "")
        if status in summary:
            summary[status] += 1
    return summary


def flatten_materials(task_material_map: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for task in task_material_map:
        for item in list(task.get(key) or []):
            item_with_task = {"task_id": task.get("task_id"), "task_name": task.get("task_name"), **item}
            dedupe_key = (
                str(item_with_task.get("task_id") or ""),
                str(item_with_task.get("material_type") or ""),
                str(item_with_task.get("file_id") or ""),
                str(item_with_task.get("snippet") or ""),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            flattened.append(item_with_task)
    return flattened


def material(
    material_type: str,
    fact_type: str,
    record: dict[str, Any],
    source: str,
    title: Any,
    person: Any,
    snippet: Any,
    confidence: float,
) -> dict[str, Any]:
    return {
        "material_type": material_type,
        "fact_type": fact_type,
        "file_id": record.get("id"),
        "source": source,
        "title": title or record.get("original_name") or record.get("id"),
        "person": person,
        "snippet": preview(snippet),
        "confidence": round(float(confidence), 2),
    }


def dedupe_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in sorted(materials, key=lambda value: float(value.get("confidence") or 0.0), reverse=True):
        key = (
            str(item.get("material_type") or ""),
            str(item.get("fact_type") or ""),
            str(item.get("file_id") or ""),
            str(item.get("snippet") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def resume_snippet(resume: dict[str, Any]) -> str:
    skills = ", ".join(
        str(item.get("name") or "")
        for item in list(resume.get("skills") or [])[:5]
        if isinstance(item, dict) and item.get("name")
    )
    certs = ", ".join(
        str(item.get("display_name") or item.get("standard_name") or item.get("name") or "")
        for item in list(resume.get("certificates") or [])[:5]
        if isinstance(item, dict)
    )
    projects = ", ".join(
        str(item.get("name") or "")
        for item in list(resume.get("projects") or [])[:3]
        if isinstance(item, dict) and item.get("name")
    )
    return "；".join(
        part
        for part in [
            f"姓名：{resume.get('person_name')}" if resume.get("person_name") else "",
            f"工作年限：{resume.get('work_years')}年" if resume.get("work_years") is not None else "",
            f"技能：{skills}" if skills else "",
            f"证书：{certs}" if certs else "",
            f"项目经验：{projects}" if projects else "",
        ]
        if part
    )


def generic_terms_for_task(task_text: str, task: dict[str, Any]) -> list[str]:
    terms = []
    append_list(terms, task.get("evidence_needed"))
    append_list(terms, task.get("kb_mapping_hint"))
    terms.extend(significant_terms(task_text))
    return dedupe_terms(terms)


def significant_terms(text: Any) -> list[str]:
    raw = str(text or "")
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}|[\u4e00-\u9fff]{2,}", raw)
    return [
        token
        for token in tokens
        if len(normalize_for_match(token)) >= 2 and token not in SIGNIFICANT_STOP_TERMS
    ][:40]


def contains_any(text: Any, terms: list[str]) -> bool:
    normalized = normalize_for_match(text)
    return any(normalize_for_match(term) in normalized for term in terms if normalize_for_match(term))


def best_snippet(text: str, terms: list[str], limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    normalized = normalize_for_match(compact)
    for term in terms:
        normalized_term = normalize_for_match(term)
        if not normalized_term:
            continue
        index = normalized.find(normalized_term)
        if index < 0:
            continue
        raw_index = max(0, min(len(compact), index))
        return preview(compact[max(0, raw_index - 50) : raw_index + limit], limit)
    return preview(compact, limit)


def best_text_source(record: dict[str, Any], searchable: str) -> str:
    if asset_ocr_text(record) and contains_any(asset_ocr_text(record), significant_terms(searchable)):
        return "metadata.files[].assets[].ocr_text"
    if record_text(record):
        return "metadata.files[].parsed_text"
    return "metadata.files[].original_name"


def generic_confidence(material_type: str, searchable: str, terms: list[str]) -> float:
    matched = sum(1 for term in terms[:20] if contains_any(searchable, [term]))
    base = {"project_case": 0.72, "patent": 0.74, "generic_document": 0.62}.get(material_type, 0.6)
    return min(0.86, base + min(matched, 3) * 0.03)


def safe_confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return number if number > 0 else default


def join_values(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(join_values(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(join_values(item) for item in value)
    return str(value or "")


def append_text(parts: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        parts.append(text)


def append_list(parts: list[str], value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            append_text(parts, item)
        return
    append_text(parts, value)


def dedupe_terms(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        normalized = normalize_for_match(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return result


def normalize_for_match(value: Any) -> str:
    return re.sub(r"\s+|[^\w\u4e00-\u9fff]+", "", str(value or "").lower())


def preview(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

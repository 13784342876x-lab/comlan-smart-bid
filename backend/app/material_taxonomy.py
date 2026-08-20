from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config import METADATA_PATH
from app.tender_source_guard import is_tender_source_file, tender_source_profile


GROUP_NAMES = {
    "personnel": "团队候选与人员材料",
    "company_performance": "公司业绩材料",
    "company_product": "公司产品材料",
    "intellectual_property": "知识产权材料",
    "company_qualification": "资质证书材料",
    "winning_case": "中标成功案例",
    "supplier_performance": "供应商绩效评价材料",
    "business_compliance": "商务信用与合规材料",
    "technical_writing_asset": "技术方案素材",
    "tender_source": "招标文件与评分依据",
    "unknown": "未分类材料",
}

TAXONOMY = {
    "personnel": [
        "person_resume",
        "personal_certificate",
        "person_project_experience",
        "team_roster",
        "organization_structure",
        "title_certificate",
        "labor_relation",
    ],
    "company_performance": [
        "company_contract",
        "acceptance_report",
        "winning_notice",
        "customer_certificate",
        "performance_case",
        "service_certificate",
    ],
    "company_product": [
        "product_manual",
        "product_whitepaper",
        "product_screenshot",
        "solution_document",
        "test_report",
        "deployment_document",
        "platform_intro",
    ],
    "intellectual_property": [
        "patent_certificate",
        "patent_record",
        "software_copyright",
        "standard_draft",
        "research_report",
        "science_award",
        "paper",
    ],
    "company_qualification": [
        "iso_certificate",
        "itss_certificate",
        "cs_certificate",
        "ccrc_certificate",
        "cmmi_certificate",
        "dcmm_certificate",
        "cnas_certificate",
        "xinchuang_certificate",
        "agency_authorization",
        "partner_authorization",
        "business_license",
    ],
    "winning_case": [
        "winning_notice",
        "award_notice",
        "bid_result_announcement",
        "framework_shortlist",
        "procurement_platform_record",
    ],
    "supplier_performance": [
        "supplier_performance_evaluation",
        "platform_rating",
        "purchaser_evaluation",
        "supplier_grade_certificate",
    ],
    "business_compliance": [
        "credit_report",
        "enterprise_credit_report",
        "no_violation_certificate",
        "tax_certificate",
        "social_security_certificate",
        "audit_report",
        "legal_authorization",
        "commitment_letter",
    ],
    "technical_writing_asset": [
        "service_plan_template",
        "implementation_plan_template",
        "quality_assurance_template",
        "risk_control_template",
        "training_plan_template",
        "acceptance_plan_template",
        "after_sales_template",
        "project_management_method",
    ],
    "tender_source": [
        "full_tender_document",
        "tender_document",
        "tender_notice_or_procurement_document",
        "tender_main",
        "technical_specification",
        "client_requirement",
        "service_requirement",
        "scoring_method",
        "score_table",
        "contract_terms",
        "response_format",
        "invalid_response_clause",
        "signature_seal_requirement",
    ],
}

STALE_OR_MOCK_SUMMARY_FILE_IDS = {"file_a5eee523007a", "file_a0a571b442b0"}


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


def classify_material_record(record: dict[str, Any], force: bool = False) -> dict[str, Any]:
    existing = record.get("material_profile")
    if (
        isinstance(existing, dict)
        and existing.get("classification_source") == "manual_override"
        and not force
    ):
        return dict(existing)
    return build_material_profile(record)


def apply_material_profile(record: dict[str, Any], force: bool = False) -> dict[str, Any]:
    profile = classify_material_record(record, force=force)
    record["material_profile"] = profile
    return profile


def classify_file_in_metadata(file_id: str, path: Path = METADATA_PATH, force: bool = False) -> dict[str, Any]:
    metadata = load_metadata(path)
    record = find_file(metadata, file_id)
    profile = apply_material_profile(record, force=force)
    save_metadata(metadata, path)
    return {"file_id": record.get("id"), "original_name": record.get("original_name"), "material_profile": profile}


def classify_all_files(path: Path = METADATA_PATH, force: bool = False) -> dict[str, Any]:
    metadata = load_metadata(path)
    updated = 0
    skipped_manual_override = 0
    group_counts: dict[str, int] = {}
    for record in list(metadata.get("files") or []):
        existing = record.get("material_profile")
        if (
            isinstance(existing, dict)
            and existing.get("classification_source") == "manual_override"
            and not force
        ):
            skipped_manual_override += 1
            group = str(existing.get("material_group") or "unknown")
        else:
            profile = apply_material_profile(record, force=force)
            updated += 1
            group = str(profile.get("material_group") or "unknown")
        group_counts[group] = group_counts.get(group, 0) + 1
    save_metadata(metadata, path)
    return {
        "total_files": len(list(metadata.get("files") or [])),
        "updated": updated,
        "skipped_manual_override": skipped_manual_override,
        "group_counts": group_counts,
    }


def inspect_material_profile(file_id: str, path: Path = METADATA_PATH) -> dict[str, Any]:
    metadata = load_metadata(path)
    record = find_file(metadata, file_id)
    profile = record.get("material_profile")
    if not isinstance(profile, dict):
        profile = build_material_profile(record)
    return {
        "file_id": record.get("id"),
        "original_name": record.get("original_name"),
        "category": record.get("category"),
        "source_folder": record.get("source_folder"),
        "parsed_text_len": len(str(record.get("parsed_text") or "")),
        "material_profile": profile,
        "usable_for": profile.get("usable_for", []),
        "not_usable_for": profile.get("not_usable_for", []),
        "notes": profile.get("notes", []),
    }


def list_material_profiles(
    group: str | None = None,
    material_type: str | None = None,
    category: str | None = None,
    path: Path = METADATA_PATH,
) -> list[dict[str, Any]]:
    metadata = load_metadata(path)
    rows: list[dict[str, Any]] = []
    for record in list(metadata.get("files") or []):
        profile = record.get("material_profile")
        if not isinstance(profile, dict):
            profile = build_material_profile(record)
        if group and profile.get("material_group") != group:
            continue
        if material_type and material_type not in list(profile.get("material_types") or []):
            continue
        if category and category not in str(record.get("category") or ""):
            continue
        rows.append(
            {
                "file_id": record.get("id"),
                "original_name": record.get("original_name"),
                "category": record.get("category"),
                "material_group": profile.get("material_group"),
                "material_types": profile.get("material_types", []),
                "confidence": profile.get("confidence"),
                "requires_manual_confirm": profile.get("requires_manual_confirm"),
            }
        )
    return rows


def build_material_profile(record: dict[str, Any]) -> dict[str, Any]:
    if is_tender_source_file(record):
        return tender_source_profile(record)
    group, types, source, confidence, requires_manual_confirm, notes = infer_group_and_types(record)
    if is_stale_or_mock_summary_record(record):
        confidence = min(confidence, 0.45)
        requires_manual_confirm = True
        notes.append("stale_or_mock_summary / weak_evidence / unusable_summary：旧泛化摘要不能作为 confirmed evidence。")
    return {
        "material_group": group,
        "material_group_name": GROUP_NAMES.get(group, GROUP_NAMES["unknown"]),
        "material_types": sorted_unique(types),
        "usable_for": usable_for(group, types),
        "not_usable_for": not_usable_for(group, types),
        "confidence": confidence,
        "classification_source": source,
        "requires_manual_confirm": requires_manual_confirm,
        "notes": notes,
    }


def infer_group_and_types(record: dict[str, Any]) -> tuple[str, list[str], str, float, bool, list[str]]:
    file_id = str(record.get("id") or record.get("file_id") or "")
    name = str(record.get("original_name") or "")
    category = str(record.get("category") or "")
    parsed_text = str(record.get("parsed_text") or "")
    text = normalize_text(" ".join([file_id, name, category, parsed_text[:8000]]))
    notes: list[str] = []

    if is_tender_source_file(record):
        profile = tender_source_profile(record)
        notes.extend(list(profile.get("notes") or []))
        return "tender_source", list(profile.get("material_types") or []), "tender_source_guard", float(profile.get("confidence") or 0.9), False, notes

    if file_id in STALE_OR_MOCK_SUMMARY_FILE_IDS or has_any(text, ["mock word", "mock word解析文本", "泛化摘要"]):
        notes.append("检测到旧 Mock 或泛化摘要；仅作为弱线索，不作为确认材料。")

    if has_resume_parse(record):
        types = ["person_resume", "person_project_experience"]
        if has_any(text, ["证书", "认证", "pmp", "acp", "python", "vmware", "azure", "aws", "华为", "microsoft"]):
            types.append("personal_certificate")
        notes.append("存在 resume_parse，归入人员材料；其中项目经验仅为人员项目经验。")
        return "personnel", types, "structured_parser", 0.95, False, notes

    if is_tender_source_text(text):
        types = tender_source_types(text)
        notes.append("文件名、分类或文本指向招标正文/技术规范/评分依据。")
        return "tender_source", types, "rule", 0.9, False, notes

    if is_company_qualification_text(text):
        types = qualification_types(text)
        notes.append("检测到 ISO/ITSS/CS/CCRC/CMMI/DCMM/CNAS/信创/代理授权等企业资质线索。")
        return "company_qualification", types, "rule", 0.9, False, notes

    if has_any(text, ["绩效评价", "供应商评价", "评价结果", "履约评价"]):
        return "supplier_performance", supplier_performance_types(text), "rule", 0.85, True, notes

    if has_any(text, ["中标", "成交通知", "入围", "结果公告"]):
        return "winning_case", winning_case_types(text), "rule", 0.8, True, notes

    if has_any(text, ["业绩", "合同", "验收", "客户证明", "案例"]):
        notes.append("命中公司业绩关键词；需人工确认是否为公司层面的合同/验收/客户证明。")
        return "company_performance", company_performance_types(text), "rule", 0.78, True, notes

    if has_any(text, ["专利", "软著", "知识产权", "著作权", "标准", "成果", "论文"]):
        notes.append("命中知识产权关键词；专利 confirmed 仍需专利号/申请号、权利人、状态等要素。")
        return "intellectual_property", intellectual_property_types(text), "rule", 0.78, True, notes

    if has_any(text, ["产品", "平台", "方案", "白皮书", "说明书", "部署", "测试报告"]):
        return "company_product", company_product_types(text), "rule", 0.72, True, notes

    if has_any(text, ["信用", "合规", "无违法", "纳税", "社保", "审计", "授权"]):
        return "business_compliance", business_compliance_types(text), "rule", 0.72, True, notes

    if has_any(text, ["技术方案素材", "实施方案模板", "质量保障", "培训方案", "验收方案", "售后", "项目管理"]):
        return "technical_writing_asset", technical_asset_types(text), "rule", 0.7, True, notes

    if has_any(text, ["人员", "简历", "团队"]):
        notes.append("文件名或分类命中人员/简历/团队。")
        return "personnel", ["person_resume"], "rule", 0.75, True, notes

    notes.append("未命中稳定分类规则，暂归入 unknown。")
    return "unknown", [], "rule", 0.4, True, notes


def has_resume_parse(record: dict[str, Any]) -> bool:
    if isinstance(record.get("resume_parse"), dict):
        return True
    structured = record.get("structured_facts")
    return isinstance(structured, dict) and isinstance(structured.get("resume_parse"), dict)


def is_stale_or_mock_summary_record(record: dict[str, Any]) -> bool:
    file_id = str(record.get("id") or record.get("file_id") or "")
    text = normalize_text(
        " ".join(
            [
                file_id,
                str(record.get("original_name") or ""),
                str(record.get("category") or ""),
                str(record.get("parsed_text") or "")[:2000],
            ]
        )
    )
    return file_id in STALE_OR_MOCK_SUMMARY_FILE_IDS or has_any(text, ["mock word", "mock word解析文本", "泛化摘要"])


def is_tender_source_text(text: str) -> bool:
    return has_any(
        text,
        [
            "招标",
            "技术规范",
            "技术标准",
            "委托人要求",
            "服务要求",
            "评标",
            "评分",
            "合同条款",
            "响应格式",
            "投标文件格式",
        ],
    )


def is_company_qualification_text(text: str) -> bool:
    return has_any(
        text,
        [
            "资质",
            "证书",
            "认证",
            "代理",
            "iso",
            "itss",
            "cs3",
            "信息系统建设和服务能力",
            "ccrc",
            "cmmi",
            "dcmm",
            "cnas",
            "信创",
            "授权",
        ],
    )


def tender_source_types(text: str) -> list[str]:
    types: list[str] = []
    if has_any(text, ["技术规范", "技术标准"]):
        types.append("technical_specification")
    if "委托人要求" in text:
        types.append("client_requirement")
    if "服务要求" in text:
        types.append("service_requirement")
    if has_any(text, ["评标", "评分", "评审"]):
        types.extend(["scoring_method", "score_table"])
    if "合同" in text:
        types.append("contract_terms")
    if has_any(text, ["响应格式", "投标文件格式", "应答文件格式"]):
        types.append("response_format")
    if has_any(text, ["无效", "否决"]):
        types.append("invalid_response_clause")
    if has_any(text, ["签字", "盖章", "签章"]):
        types.append("signature_seal_requirement")
    if has_any(text, ["招标文件", "招标公告"]) and not types:
        types.append("tender_main")
    return types or ["tender_main"]


def qualification_types(text: str) -> list[str]:
    types: list[str] = []
    if "iso" in text:
        types.append("iso_certificate")
    if "itss" in text:
        types.append("itss_certificate")
    if "cs3" in text or "信息系统建设和服务能力" in text or re.search(r"\bcs\s*[0-9]", text):
        types.append("cs_certificate")
    if "ccrc" in text:
        types.append("ccrc_certificate")
    if "cmmi" in text:
        types.append("cmmi_certificate")
    if "dcmm" in text:
        types.append("dcmm_certificate")
    if "cnas" in text:
        types.append("cnas_certificate")
    if "信创" in text:
        types.append("xinchuang_certificate")
    if has_any(text, ["代理", "代理资质"]):
        types.append("agency_authorization")
    if has_any(text, ["伙伴", "合作伙伴", "授权"]):
        types.append("partner_authorization")
    if "营业执照" in text:
        types.append("business_license")
    return types or ["iso_certificate"]


def company_performance_types(text: str) -> list[str]:
    types: list[str] = []
    if "合同" in text:
        types.append("company_contract")
    if "验收" in text:
        types.append("acceptance_report")
    if "中标" in text:
        types.append("winning_notice")
    if "客户证明" in text:
        types.append("customer_certificate")
    if "服务证明" in text:
        types.append("service_certificate")
    if "案例" in text or "业绩" in text:
        types.append("performance_case")
    return types or ["performance_case"]


def company_product_types(text: str) -> list[str]:
    types: list[str] = []
    if "说明书" in text:
        types.append("product_manual")
    if "白皮书" in text:
        types.append("product_whitepaper")
    if "截图" in text:
        types.append("product_screenshot")
    if "方案" in text:
        types.append("solution_document")
    if "测试报告" in text:
        types.append("test_report")
    if "部署" in text:
        types.append("deployment_document")
    if "平台" in text:
        types.append("platform_intro")
    return types or ["solution_document"]


def intellectual_property_types(text: str) -> list[str]:
    types: list[str] = []
    if "专利" in text and has_any(text, ["专利号", "申请号", "授权公告号", "权利人"]):
        types.append("patent_certificate")
    elif "专利" in text:
        types.append("patent_record")
    if has_any(text, ["软著", "软件著作权", "著作权"]):
        types.append("software_copyright")
    if "标准" in text:
        types.append("standard_draft")
    if "研究报告" in text:
        types.append("research_report")
    if has_any(text, ["科技成果", "奖"]):
        types.append("science_award")
    if "论文" in text:
        types.append("paper")
    return types or ["patent_record"]


def winning_case_types(text: str) -> list[str]:
    if "入围" in text:
        return ["framework_shortlist"]
    if "公告" in text:
        return ["bid_result_announcement"]
    if "平台" in text:
        return ["procurement_platform_record"]
    if "奖" in text:
        return ["award_notice"]
    return ["winning_notice"]


def supplier_performance_types(text: str) -> list[str]:
    if "平台" in text:
        return ["platform_rating"]
    if "招标人" in text or "采购人" in text:
        return ["purchaser_evaluation"]
    if "等级" in text or "评级" in text:
        return ["supplier_grade_certificate"]
    return ["supplier_performance_evaluation"]


def business_compliance_types(text: str) -> list[str]:
    types: list[str] = []
    if "信用报告" in text:
        types.append("enterprise_credit_report")
    elif "信用" in text:
        types.append("credit_report")
    if "无违法" in text or "无违规" in text:
        types.append("no_violation_certificate")
    if "纳税" in text:
        types.append("tax_certificate")
    if "社保" in text:
        types.append("social_security_certificate")
    if "审计" in text:
        types.append("audit_report")
    if "授权" in text:
        types.append("legal_authorization")
    if "承诺" in text:
        types.append("commitment_letter")
    return types or ["credit_report"]


def technical_asset_types(text: str) -> list[str]:
    types: list[str] = []
    if "服务方案" in text:
        types.append("service_plan_template")
    if "实施方案" in text:
        types.append("implementation_plan_template")
    if "质量" in text:
        types.append("quality_assurance_template")
    if "风险" in text:
        types.append("risk_control_template")
    if "培训" in text:
        types.append("training_plan_template")
    if "验收" in text:
        types.append("acceptance_plan_template")
    if "售后" in text:
        types.append("after_sales_template")
    if "项目管理" in text:
        types.append("project_management_method")
    return types or ["service_plan_template"]


def usable_for(group: str, types: list[str]) -> list[str]:
    mapping = {
        "personnel": ["project_owner", "project_team", "personnel_materials"],
        "company_performance": ["company_performance", "similar_project_experience"],
        "company_product": ["work_foundation", "product_capability"],
        "intellectual_property": ["patent", "innovation", "work_foundation"],
        "company_qualification": ["company_qualification", "qualification_response"],
        "winning_case": ["winning_case", "successful_case_reference"],
        "supplier_performance": ["supplier_performance"],
        "business_compliance": ["business_compliance"],
        "technical_writing_asset": ["technical_writing_reference"],
        "tender_source": ["tender_requirement", "scoring_reference", "response_format_reference"],
    }
    values = list(mapping.get(group, []))
    if group == "winning_case":
        values.append("not_complete_company_performance_without_contract_acceptance")
    return sorted_unique(values)


def not_usable_for(group: str, types: list[str]) -> list[str]:
    if group == "tender_source":
        return ["company_performance", "company_qualification", "patent", "supplier_performance", "intellectual_property", "enterprise_proof_material"]
    if group == "personnel":
        return ["company_performance", "company_qualification"]
    if group == "company_qualification":
        return ["company_performance", "patent", "supplier_performance", "intellectual_property"]
    if group == "technical_writing_asset":
        return ["company_performance", "company_qualification", "intellectual_property", "supplier_performance"]
    if group == "winning_case":
        return ["complete_company_performance_without_contract_acceptance"]
    if group == "intellectual_property":
        return ["company_qualification", "supplier_performance"]
    if group == "supplier_performance":
        return ["company_performance", "company_qualification", "patent"]
    return []


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def sorted_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return sorted(result)

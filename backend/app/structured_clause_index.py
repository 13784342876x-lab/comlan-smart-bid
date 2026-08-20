from __future__ import annotations

import re
from collections import Counter
from typing import Any


REGION_BY_CHAPTER = [
    ("第一章", "tender_notice"),
    ("第二章", "bidder_instruction"),
    ("第三章", "evaluation_method"),
    ("第四章", "contract_terms"),
    ("第五章", "technical_spec"),
    ("第六章", "response_format"),
]

TECHNICAL_SECTIONS = {"technical_overall", "personnel", "team", "work_basis", "intellectual_property", "supplier_performance", "technical_solution"}

OPERATION_ONLY_TERMS = [
    "开标时间和地点",
    "投标截止时间",
    "投标文件递交",
    "电子招标投标交易平台",
    "ECP",
    "CA证书",
    "电子钥匙",
    "投标工具",
    "平台解密",
    "逾期送达",
    "投标文件修改与撤回",
    "开标纪律",
    "唱标人",
    "记录人",
    "监标人",
    "投标保证金递交",
    "电子病毒",
    "弃标函",
    "邮寄工程量清单",
    "递交方式",
    "下载专区",
    "新手指引",
]


def infer_document_region(section_path: list[str], source_type: str = "") -> str:
    joined = " > ".join(section_path)
    if source_type == "tender_notice":
        return "tender_notice"
    if source_type == "technical_spec":
        return "technical_spec"
    if source_type == "score_table":
        return "evaluation_method"
    if source_type == "procurement_package_table":
        return "procurement_package_table"
    root_region = ""
    for part in section_path:
        for chapter, region in REGION_BY_CHAPTER:
            if chapter in str(part):
                root_region = region
                break
        if root_region:
            break
    if root_region:
        if root_region == "response_format":
            if "价格文件" in joined:
                return "price_file_format"
            if "商务文件" in joined:
                return "business_file_format"
            if "技术文件" in joined:
                return "technical_file_format"
        return root_region
    if any(term in joined for term in ["技术规范", "技术标准", "委托人要求"]):
        return "technical_spec"
    if any(term in joined for term in ["投标文件格式", "应答文件格式", "响应文件格式"]):
        return "response_format"
    return "unknown"


def classify_structured_clause_type(text: str, title: str = "", section_path: list[str] | None = None, source_type: str = "") -> str:
    value = " ".join([title or "", " ".join(section_path or []), text or ""])
    direct_value = " ".join([title or "", text or ""])
    if source_type == "procurement_package_table":
        return "procurement_package_row"
    if "服务类供应商" in value and "重质量" in value and "讲诚信" in value:
        return "business_quality_commitment"
    if any(key in value for key in ["一、价格文件", "投标函", "如我方中标", "履约担保", "中标代理服务费", "投标保证金", "投标函附表"]):
        return "price_file_or_bid_letter_commitment"
    if any(key in direct_value for key in OPERATION_ONLY_TERMS):
        return "operation_only"
    if any(key in value for key in ["企业营业执照", "营业执照", "事业单位法人证书", "组织登记证书"]):
        return "business_license"
    if any(key in value for key in ["企业名称变更", "名称变更证明", "资格资质变更证明"]):
        return "identity_change"
    if any(key in value for key in ["授权委托书", "法定代表人授权", "被授权人"]):
        return "business_authorization"
    if any(key in value for key in ["拟委任的主要人员汇总表", "项目团队情况", "项目团队", "人员结构"]):
        return "team_summary_form"
    if any(key in value for key in ["主要人员简历表", "项目负责人简历", "人员简历", "简历表"]):
        return "personnel_resume_form"
    if any(key in value for key in ["项目负责人", "项目经理", "主要人员", "职称证", "学历证", "社保", "工作年限", "工作经验"]):
        return "personnel_requirement"
    if any(key in value for key in ["供应商绩效", "绩效评价", "最近一年度评价结果"]):
        return "supplier_performance_requirement"
    if any(key in value for key in ["发明专利", "实用新型", "专利数量", "知识产权", "软著"]):
        return "patent_requirement"
    if any(key in value for key in ["近年完成的类似项目", "类似项目情况表", "公司业绩", "合同证明", "验收证明", "中标通知书"]):
        return "company_performance_requirement"
    if any(key in value for key in ["工作进度", "进度计划", "保证措施", "里程碑", "服务期限", "工期"]):
        return "schedule_requirement"
    if any(key in value for key in ["服务方案", "技术方案", "实施方案", "质量保证", "技术培训", "验收测试", "研究内容", "技术指标"]):
        return "technical_solution_requirement"
    if any(key in value for key in ["技术偏差表", "商务偏差表", "偏差表"]):
        return "deviation_form_requirement"
    if any(key in value for key in ["签字", "盖章", "签章"]):
        return "signature_seal_requirement"
    if any(key in value for key in ["资格要求", "资格条件", "资质要求", "专用资格要求"]):
        return "qualification_requirement"
    if any(key in value for key in ["评分", "评标", "得分", "扣分", "分值"]):
        return "score_item"
    if any(key in value for key in ["响应文件", "应答文件", "投标文件", "文件格式"]):
        return "response_format_requirement"
    return "unknown"


def build_applicability_hint(clause_type: str) -> dict[str, list[str]]:
    blocked_all = sorted(TECHNICAL_SECTIONS)
    mapping: dict[str, dict[str, list[str]]] = {
        "business_license": {"allowed_section_types": [], "blocked_section_types": blocked_all},
        "identity_change": {"allowed_section_types": [], "blocked_section_types": blocked_all},
        "business_authorization": {"allowed_section_types": [], "blocked_section_types": blocked_all},
        "business_quality_commitment": {"allowed_section_types": [], "blocked_section_types": blocked_all},
        "price_file_or_bid_letter_commitment": {"allowed_section_types": [], "blocked_section_types": blocked_all},
        "operation_only": {"allowed_section_types": [], "blocked_section_types": blocked_all},
        "personnel_requirement": {"allowed_section_types": ["personnel", "team"], "blocked_section_types": []},
        "personnel_resume_form": {"allowed_section_types": ["personnel", "team"], "blocked_section_types": []},
        "team_summary_form": {"allowed_section_types": ["personnel", "team"], "blocked_section_types": []},
        "patent_requirement": {"allowed_section_types": ["intellectual_property"], "blocked_section_types": []},
        "supplier_performance_requirement": {"allowed_section_types": ["supplier_performance"], "blocked_section_types": []},
        "company_performance_requirement": {"allowed_section_types": ["work_basis"], "blocked_section_types": ["personnel", "team", "intellectual_property", "supplier_performance"]},
        "technical_solution_requirement": {"allowed_section_types": ["technical_solution"], "blocked_section_types": []},
        "schedule_requirement": {"allowed_section_types": ["technical_solution"], "blocked_section_types": []},
    }
    return mapping.get(clause_type, {"allowed_section_types": [], "blocked_section_types": []})


def quality_flags_for_clause(text: str, document_region: str, clause_type: str) -> list[str]:
    flags: list[str] = []
    if document_region == "unknown":
        flags.append("unknown_document_region")
    if clause_type == "unknown":
        flags.append("unknown_clause_type")
    if len(str(text or "")) < 30:
        flags.append("short_text")
    return flags


def build_source_location(
    paragraph_index: int | None = None,
    table_index: int | None = None,
    row_index: int | None = None,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    return {
        "page": None,
        "paragraph_index": paragraph_index,
        "table_index": table_index,
        "row_index": row_index,
        "sheet_name": sheet_name,
    }


def build_table_context_from_line(line: str, sheet_name: str, row_index: int, lot_name: str = "") -> dict[str, Any]:
    values = split_table_like_values(line)
    package_codes = re.findall(r"042513\d{7}-\d{4}", line)
    package_no_match = re.search(r"包\s*0?\d+|包\d+", line)
    matched_lot = lot_name if lot_name and lot_name in line else (package_codes[0] if package_codes else "")
    is_current_lot = bool(lot_name and lot_name in line)
    if lot_name and package_codes and lot_name not in package_codes:
        is_current_lot = False
    return {
        "sheet_name": sheet_name,
        "table_index": 1,
        "row_index": row_index,
        "headers": [],
        "row_values": values,
        "matched_lot_name": matched_lot,
        "matched_package_no": package_no_match.group(0).replace(" ", "") if package_no_match else "",
        "is_current_lot": is_current_lot if lot_name else None,
    }


def split_table_like_values(line: str) -> list[str]:
    parts = re.split(r"\t+|\s{2,}|[|｜]", str(line or "").strip())
    return [part.strip() for part in parts if part.strip()]


def detect_sheet_name(line: str) -> str:
    match = re.search(r"=+\s*Sheet:\s*(.+?)\s*=+", line)
    return match.group(1).strip() if match else ""


def summarize_structured_clauses(clause_indexes: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    clauses = [clause for values in clause_indexes.values() for clause in values]
    regions = Counter(str(clause.get("document_region") or "unknown") for clause in clauses)
    types = Counter(str(clause.get("clause_type") or "unknown") for clause in clauses)
    return {
        "indexed_files": len(clause_indexes),
        "indexed_clause_count": len(clauses),
        "document_regions": dict(sorted(regions.items())),
        "clause_types": dict(sorted(types.items())),
        "table_context_count": len([clause for clause in clauses if clause.get("table_context")]),
    }

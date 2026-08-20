from __future__ import annotations

import re
from typing import Any


DEFAULT_MISSING_CLAUSE_MESSAGE = "未定位到本章节专属招标/技术条款，按评分点依据和通用响应要求组织。"

SECTION_CLAUSE_RULES = {
    "technical_overall": {
        "positive": ["技术内容", "规范性", "完整性", "逻辑性", "响应文件", "技术文件", "技术偏差表"],
        "negative": ["企业名称变更", "营业执照", "事业单位法人证书", "组织登记证书"],
        "missing": DEFAULT_MISSING_CLAUSE_MESSAGE,
    },
    "personnel": {
        "positive": ["项目负责人", "主要人员", "简历", "工作年限", "工作经验", "专业", "职称证", "学历证", "社保"],
        "negative": ["企业名称变更", "营业执照", "企业资质", "公司业绩", "事业单位法人证书", "组织登记证书"],
        "missing": "未定位到本章节专属人员条款，按评分点要求整理项目负责人专业、工作年限、工作经验等材料。",
        "compliance_missing": "未定位到本章节专属合规条件；未定位到本章节专属人员合规条件，按评分点要求整理项目负责人/团队材料。",
    },
    "team": {
        "positive": ["项目团队", "主要人员", "人员结构", "专业", "工作年限", "工作经验", "简历表", "拟委任的主要人员汇总表"],
        "negative": ["企业名称变更", "营业执照", "企业资质", "公司业绩", "事业单位法人证书", "组织登记证书"],
        "missing": "未定位到本章节专属团队条款，按评分点要求整理团队人员结构、专业、工作年限、工作经验等材料。",
        "compliance_missing": "未定位到本章节专属合规条件；未定位到本章节专属人员合规条件，按评分点要求整理项目负责人/团队材料。",
    },
    "work_basis": {
        "positive": ["产品开发", "产品化", "科技成果", "专题研究", "研发能力", "工作基础"],
        "negative": ["企业名称变更", "营业执照", "人员简历", "事业单位法人证书", "组织登记证书"],
        "missing": DEFAULT_MISSING_CLAUSE_MESSAGE,
    },
    "intellectual_property": {
        "positive": ["专利", "发明专利", "实用新型", "知识产权", "软著", "科技成果"],
        "negative": ["企业名称变更", "营业执照", "施工招标计划表", "事业单位法人证书", "组织登记证书"],
        "missing": "未定位到本章节专属专利合规条款，按评分点要求补充采购相关发明专利或实用新型专利证明。",
        "compliance_missing": "未定位到本章节专属专利合规条件，按评分点要求补充专利证书、专利清单或官方查询记录。",
    },
    "supplier_performance": {
        "positive": ["绩效评价", "供应商绩效", "评价结果", "最近一年度"],
        "negative": ["企业名称变更", "营业执照", "人员简历", "资质证书", "项目经验", "事业单位法人证书", "组织登记证书", "十不准", "绩效评价人员", "招标采购相关从业人员"],
        "missing": DEFAULT_MISSING_CLAUSE_MESSAGE,
        "compliance_missing": "未定位到本章节专属供应商绩效评价条款，按评分点要求补充最近一年度供应商绩效评价结果证明。",
    },
    "technical_solution": {
        "positive": ["服务方案", "实施方案", "技术要求", "服务内容", "研究内容", "技术指标", "质量保证", "验收测试"],
        "negative": ["企业名称变更", "营业执照", "事业单位法人证书", "组织登记证书"],
        "missing": DEFAULT_MISSING_CLAUSE_MESSAGE,
    },
}

BUSINESS_COMPLIANCE_ONLY_TERMS = ["营业执照", "事业单位法人证书", "组织登记证书"]
BUSINESS_IDENTITY_CHANGE_TERMS = ["企业名称变更"]
SECTION_GUARDED_TYPES = set(SECTION_CLAUSE_RULES)


def section_type_for_task(task_name: str) -> str:
    text = str(task_name or "")
    if "总体评价" in text or "应答文件技术部分" in text:
        return "technical_overall"
    if "负责人" in text or "项目经理" in text:
        return "personnel"
    if "团队" in text or "人员结构" in text:
        return "team"
    if "工作基础" in text:
        return "work_basis"
    if "专利" in text or "知识产权" in text or "创新" in text:
        return "intellectual_property"
    if "绩效" in text:
        return "supplier_performance"
    if any(term in text for term in ["服务方案", "实施方案", "质量", "进度", "风险", "沟通"]):
        return "technical_solution"
    return "generic"


def clause_text(clause: dict[str, Any]) -> str:
    return " ".join(
        [
            str(clause.get("file_name") or ""),
            " ".join(str(item) for item in list(clause.get("heading_path") or [])),
            str(clause.get("clause_title") or ""),
            str(clause.get("clause_text") or ""),
        ]
    )


def clause_guard_reason(clause: dict[str, Any], section_type: str, *, for_compliance: bool = False) -> str:
    text = clause_text(clause)
    clause_type = str(clause.get("clause_type") or "")
    table_context = clause.get("table_context") if isinstance(clause.get("table_context"), dict) else {}
    if table_context.get("is_current_lot") is False:
        return "not_current_lot"
    if contains_non_current_lot_package_table(clause):
        return "not_current_lot"
    hint = clause.get("applicability_hint") if isinstance(clause.get("applicability_hint"), dict) else {}
    blocked = {str(item) for item in list(hint.get("blocked_section_types") or [])}
    allowed = {str(item) for item in list(hint.get("allowed_section_types") or [])}
    if section_type in blocked:
        if clause_type == "operation_only":
            return "operation_only_not_applicable_to_technical_section"
        if clause_type == "business_quality_commitment":
            return "business_quality_commitment_not_applicable_to_technical_section"
        if clause_type == "price_file_or_bid_letter_commitment":
            return "price_file_or_bid_letter_commitment_not_applicable_to_technical_section"
        if clause_type == "business_license":
            return "business_compliance_only"
        if clause_type == "identity_change":
            return "business_identity_change_clause_not_applicable"
        return f"{clause_type or 'clause'}_not_applicable_to_{section_type}"
    if allowed and section_type not in allowed and section_type != "generic":
        return "section_type_not_allowed_by_clause_hint"
    if clause_type == "business_license" and section_type in SECTION_GUARDED_TYPES:
        return "business_compliance_only"
    if clause_type == "identity_change" and section_type in SECTION_GUARDED_TYPES:
        return "business_identity_change_clause_not_applicable"
    if clause_type == "operation_only" and section_type in SECTION_GUARDED_TYPES:
        return "operation_only_not_applicable_to_technical_section"
    if clause_type == "business_quality_commitment" and section_type in SECTION_GUARDED_TYPES:
        return "business_quality_commitment_not_applicable_to_technical_section"
    if clause_type == "price_file_or_bid_letter_commitment" and section_type in SECTION_GUARDED_TYPES:
        return "price_file_or_bid_letter_commitment_not_applicable_to_technical_section"
    if any(term in text for term in BUSINESS_IDENTITY_CHANGE_TERMS) and section_type in SECTION_GUARDED_TYPES:
        return "business_identity_change_clause_not_applicable"
    if any(term in text for term in BUSINESS_COMPLIANCE_ONLY_TERMS) and section_type in SECTION_GUARDED_TYPES:
        return "business_compliance_only"
    if section_type == "supplier_performance" and any(term in text for term in ["十不准", "绩效评价人员", "招标采购相关从业人员"]):
        return "supplier_performance_context_not_applicable"
    rule = SECTION_CLAUSE_RULES.get(section_type)
    if not rule:
        return ""
    if any(term in text for term in list(rule.get("negative") or [])):
        return "negative_section_clause_term"
    positives = [term for term in list(rule.get("positive") or []) if term in text]
    min_hits = 1 if for_compliance or section_type != "generic" else 0
    if len(positives) < min_hits:
        return "positive_section_clause_terms_insufficient"
    return ""


def is_clause_applicable(clause: dict[str, Any], section_type: str, *, for_compliance: bool = False) -> bool:
    return not clause_guard_reason(clause, section_type, for_compliance=for_compliance)


def missing_clause_message(section_type: str) -> str:
    rule = SECTION_CLAUSE_RULES.get(section_type) or {}
    return str(rule.get("missing") or DEFAULT_MISSING_CLAUSE_MESSAGE)


def missing_compliance_message(section_type: str) -> str:
    rule = SECTION_CLAUSE_RULES.get(section_type) or {}
    return str(rule.get("compliance_missing") or "未定位到本章节专属合规条件，按评分点要求和通用响应要求组织材料。")


def contains_non_current_lot_package_table(clause: dict[str, Any]) -> bool:
    text = clause_text(clause)
    if "施工18个" in text or "施工招标计划表" in text:
        return True
    package_codes = re.findall(r"042513\d{7}-\d{4}", text)
    return bool(package_codes and "0425139003001-6582" not in package_codes)

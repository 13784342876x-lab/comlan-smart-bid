from __future__ import annotations

from app.clause_relevance_guard import (
    clause_guard_reason,
    contains_non_current_lot_package_table,
    is_clause_applicable,
    missing_clause_message,
)


def clause(text: str, title: str = "") -> dict:
    return {
        "file_id": "file_tender",
        "file_name": "招标文件.docx",
        "heading_path": ["第六章 投标文件格式", title],
        "clause_title": title,
        "clause_text": text,
    }


def test_business_identity_change_clause_is_rejected_for_technical_sections() -> None:
    item = clause("10. 企业名称变更：如证明材料名称不一致，应提供企业名称变更证明。", "10. 企业名称变更")

    for section_type in ["technical_overall", "personnel", "team", "work_basis", "intellectual_property", "supplier_performance", "technical_solution"]:
        assert not is_clause_applicable(item, section_type)
        assert clause_guard_reason(item, section_type) == "business_identity_change_clause_not_applicable"


def test_business_license_clause_is_business_compliance_only() -> None:
    item = clause("企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）")

    assert not is_clause_applicable(item, "personnel", for_compliance=True)
    assert clause_guard_reason(item, "team", for_compliance=True) == "business_compliance_only"


def test_non_current_procurement_table_is_rejected() -> None:
    item = clause("=== Sheet: 施工18个 === 2025年第3批输变电工程施工招标计划表 0425139001002-2004 专利证书")
    item["clause_type"] = "procurement_package_row"
    item["table_context"] = {"is_current_lot": False, "sheet_name": "施工18个"}

    assert contains_non_current_lot_package_table(item)
    assert not is_clause_applicable(item, "intellectual_property")
    assert clause_guard_reason(item, "intellectual_property") == "not_current_lot"


def test_structured_business_types_are_rejected_without_keyword_fallback() -> None:
    business_license = clause("主体资格证明材料。")
    business_license["clause_type"] = "business_license"
    business_license["applicability_hint"] = {"allowed_section_types": [], "blocked_section_types": ["personnel", "team"]}
    identity_change = clause("证明材料名称不一致时补充说明。")
    identity_change["clause_type"] = "identity_change"
    identity_change["applicability_hint"] = {"allowed_section_types": [], "blocked_section_types": ["technical_overall"]}

    assert clause_guard_reason(business_license, "personnel", for_compliance=True) == "business_compliance_only"
    assert clause_guard_reason(identity_change, "technical_overall") == "business_identity_change_clause_not_applicable"


def test_current_lot_procurement_row_is_not_filtered_by_lot_guard() -> None:
    item = clause("0425139003001-6582 包01 近5年，投标人具有技术开发或技术服务业绩")
    item["clause_type"] = "procurement_package_row"
    item["table_context"] = {"is_current_lot": True, "sheet_name": "服务"}

    assert clause_guard_reason(item, "generic") == ""


def test_supplier_performance_does_not_accept_procurement_staff_discipline_clause() -> None:
    item = clause("供应商参与国家电网有限公司投标行为十不准，招标采购相关从业人员包括供应商绩效评价人员。")

    assert clause_guard_reason(item, "supplier_performance") == "supplier_performance_context_not_applicable"
    assert not is_clause_applicable(item, "supplier_performance")


def test_operation_only_clause_is_rejected_for_technical_sections() -> None:
    item = clause("5.1 开标时间和地点：投标人通过电子招标投标交易平台递交电子投标文件。")
    item["clause_type"] = "operation_only"
    item["applicability_hint"] = {"allowed_section_types": [], "blocked_section_types": ["technical_overall", "personnel", "team"]}

    assert clause_guard_reason(item, "technical_overall") == "operation_only_not_applicable_to_technical_section"
    assert not is_clause_applicable(item, "personnel")


def test_business_quality_commitment_is_rejected_for_technical_sections() -> None:
    item = clause("14. 服务类供应商“重质量、讲诚信”承诺书：服务类供应商应提交承诺书。")
    item["clause_type"] = "business_quality_commitment"
    item["applicability_hint"] = {"allowed_section_types": [], "blocked_section_types": ["personnel", "team", "technical_solution"]}

    assert clause_guard_reason(item, "personnel") == "business_quality_commitment_not_applicable_to_technical_section"
    assert not is_clause_applicable(item, "team")


def test_price_file_or_bid_letter_commitment_is_rejected_for_technical_sections() -> None:
    item = clause("5. 如我方中标，我方承诺按要求提交履约担保并支付中标代理服务费。")
    item["clause_type"] = "price_file_or_bid_letter_commitment"
    item["applicability_hint"] = {"allowed_section_types": [], "blocked_section_types": ["work_basis", "technical_solution"]}

    assert clause_guard_reason(item, "work_basis") == "price_file_or_bid_letter_commitment_not_applicable_to_technical_section"
    assert not is_clause_applicable(item, "technical_solution")


def test_missing_messages_are_section_specific() -> None:
    assert "专属人员条款" in missing_clause_message("personnel")
    assert "专属专利合规条款" in missing_clause_message("intellectual_property")

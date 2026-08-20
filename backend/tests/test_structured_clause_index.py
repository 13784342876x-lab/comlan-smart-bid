from __future__ import annotations

from app.document_clause_indexer import build_clause_index
from app.structured_clause_index import summarize_structured_clauses


def test_docx_technical_file_section_path_is_structured() -> None:
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "三、技术文件",
            "3.2 项目团队情况",
            "应提供拟委任的主要人员汇总表，说明项目团队人员结构、专业和工作经验。",
        ]
    )

    clauses = build_clause_index("file_tender", "招标文件-服务.docx", text, {"id": "file_tender", "original_name": "招标文件-服务.docx", "category": "招标文件"})
    team = next(item for item in clauses if item["heading"] == "3.2 项目团队情况")

    assert team["section_path"] == ["第六章 投标文件格式", "三、技术文件", "3.2 项目团队情况"]
    assert team["document_region"] == "technical_file_format"
    assert team["clause_type"] == "team_summary_form"
    assert team["applicability_hint"]["allowed_section_types"] == ["personnel", "team"]


def test_business_license_and_identity_change_clause_types() -> None:
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "二、商务文件",
            "1. 企业营业执照",
            "企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）。",
            "10. 企业名称变更",
            "如企业名称变更，应提供名称变更证明。",
        ]
    )

    clauses = build_clause_index("file_tender", "招标文件-服务.docx", text, {"id": "file_tender", "original_name": "招标文件-服务.docx", "category": "招标文件"})

    assert any(item["clause_type"] == "business_license" and item["document_region"] == "business_file_format" for item in clauses)
    assert any(item["clause_type"] == "identity_change" for item in clauses)


def test_procurement_package_table_rows_include_current_lot_context() -> None:
    text = "\n".join(
        [
            "=== Sheet: 服务 ===",
            "265 国网河北省电力有限公司 综合服务-科技检修技术标准 0425139003001-6582 包01 50 近5年，投标人具有技术开发或技术服务业绩",
            "=== Sheet: 施工18个 ===",
            "1 施工招标计划表 0425139001002-2004 包03 专利证书",
        ]
    )
    meta = {"id": "file_demand", "original_name": "需求一览表042513_3.xlsx", "category": "招标需求一览表", "tags": ["tender_demand_list"]}

    clauses = build_clause_index("file_demand", "需求一览表042513_3.xlsx", text, meta, "0425139003001-6582")

    current = next(item for item in clauses if "0425139003001-6582" in item["text"])
    other = next(item for item in clauses if "0425139001002-2004" in item["text"])
    summary = summarize_structured_clauses({"file_demand": clauses})

    assert current["clause_type"] == "procurement_package_row"
    assert current["table_context"]["is_current_lot"] is True
    assert current["table_context"]["sheet_name"] == "服务"
    assert other["table_context"]["is_current_lot"] is False
    assert other["table_context"]["sheet_name"] == "施工18个"
    assert summary["table_context_count"] == 2


def test_long_chinese_parenthetical_heading_is_kept_in_technical_path() -> None:
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "三、技术文件",
            "（二）专项投标文件（上传投标工具路径：技术文件-选择分标-选择分包-按钮“编辑”-页签“批量维护其他技术应答文件”-“专项应答文件批量管理”）",
            "9. 招标文件“第五章 技术规范（技术标准和要求）”中技术规范书规定的其他应提交的文件",
            "应提交技术规范书规定的其他专项应答文件。",
        ]
    )

    clauses = build_clause_index("file_tender", "招标文件-服务.docx", text, {"id": "file_tender", "original_name": "招标文件-服务.docx", "category": "招标文件"})
    clause = next(item for item in clauses if item["heading"].startswith("9. 招标文件"))

    assert clause["section_path"] == [
        "第六章 投标文件格式",
        "三、技术文件",
        "（二）专项投标文件（上传投标工具路径：技术文件-选择分标-选择分包-按钮“编辑”-页签“批量维护其他技术应答文件”-“专项应答文件批量管理”）",
        "9. 招标文件“第五章 技术规范（技术标准和要求）”中技术规范书规定的其他应提交的文件",
    ]
    assert clause["document_region"] == "technical_file_format"
    assert clause["clause_type"] != "operation_only"


def test_business_commitment_keeps_business_supplement_path() -> None:
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "二、商务文件",
            "（三）补充文件（上传投标工具路径：商务文件-选择分标-按钮“编辑”-“补充文件批量管理”-按钮“文件上传”）",
            "14. 服务类供应商“重质量、讲诚信”承诺书",
            "服务类供应商应提交“重质量、讲诚信”承诺书。",
        ]
    )

    clauses = build_clause_index("file_tender", "招标文件-服务.docx", text, {"id": "file_tender", "original_name": "招标文件-服务.docx", "category": "招标文件"})
    clause = next(item for item in clauses if item["heading"].startswith("14. 服务类供应商"))

    assert clause["section_path"] == [
        "第六章 投标文件格式",
        "二、商务文件",
        "（三）补充文件（上传投标工具路径：商务文件-选择分标-按钮“编辑”-“补充文件批量管理”-按钮“文件上传”）",
        "14. 服务类供应商“重质量、讲诚信”承诺书",
    ]
    assert clause["document_region"] == "business_file_format"
    assert clause["clause_type"] == "business_quality_commitment"


def test_empty_other_heading_does_not_swallow_quality_standard() -> None:
    text = "\n".join(
        [
            "第六章 投标文件格式",
            "三、技术文件",
            "10. 其他",
            "第五章 技术标准和要求",
            "1.3.3 质量标准",
            "1.基本要求：严格执行国家、行业、国家电网有限公司有关工程建设质量管理要求。",
        ]
    )

    clauses = build_clause_index("file_tender", "招标文件-服务.docx", text, {"id": "file_tender", "original_name": "招标文件-服务.docx", "category": "招标文件"})
    quality = next(item for item in clauses if item["heading"] == "1.3.3 质量标准")

    assert "10. 其他" not in quality["section_path"]
    assert quality["document_region"] == "technical_spec"

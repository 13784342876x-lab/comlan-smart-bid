from __future__ import annotations

from app.file_classifier import classify_file
from app.tender_metadata_extractor import extract_tender_metadata, inspect_tender_source_files


def tender_notice_record() -> dict:
    return {
        "id": "file_notice",
        "original_name": "招标公告.docx",
        "file_ext": "docx",
        "category": "招标公告",
        "tags": ["tender_notice"],
        "parsed_text": "\n".join(
            [
                "范本编号：SGCC-01-FWGK(2025)",
                "国网河北省电力有限公司采购文件范本",
                "服务公开招标文件分册",
                "（2025年版）",
                "目  录",
                "第一章 招标公告",
                "国网河北电力2025年第三次服务类公开招标采购",
                "招标公告",
                "招标编号：042513",
                "项目建设单位为国网河北省电力有限公司，资金来源已落实，招标人为国网河北省电力有限公司，并委托国网河北招标有限公司为招标代理机构。",
                "招标人: 国网河北省电力有限公司",
                "招标代理机构：国网河北招标有限公司",
            ]
        ),
    }


def test_tender_notice_classification_is_notice_not_full_tender() -> None:
    result = classify_file(tender_notice_record()).model_dump()
    sources = inspect_tender_source_files("0425139003001-6582", {"files": [tender_notice_record()]})

    assert result["category"] == "招标公告"
    assert result["document_type"] == "招标公告"
    assert result["material_group"] == "tender_source"
    assert result["source_type"] == "tender_notice"
    assert "tender_notice" in result["material_types"]
    assert "full_tender_document" not in result["material_types"]
    assert sources[0]["source_type"] == "tender_notice"
    assert sources[0]["priority"] == 0
    assert sources[0]["is_full_tender_document"] is False
    assert sources[0]["is_tender_notice"] is True


def test_tender_notice_extracts_project_level_metadata() -> None:
    result = extract_tender_metadata("0425139003001-6582", metadata={"files": [tender_notice_record()]}, save=False)
    fields = result["fields"]

    assert fields["tender_no"]["value"] == "042513"
    assert fields["tender_no"]["source_type"] == "tender_notice"
    assert fields["tender_no"]["confidence"] >= 0.9
    assert fields["tender_no"]["requires_manual_confirm"] is False
    assert fields["project_name"]["value"] == "国网河北电力2025年第三次服务类公开招标采购"
    assert fields["project_name"]["confidence"] >= 0.85
    assert fields["project_name"]["requires_manual_confirm"] is False
    assert fields["tenderee"]["value"] == "国网河北省电力有限公司"
    assert fields["tenderee"]["confidence"] >= 0.85
    assert fields["tenderee"]["requires_manual_confirm"] is False
    assert fields["tender_agency"]["value"] == "国网河北招标有限公司"
    assert fields["tender_agency"]["confidence"] >= 0.85
    assert fields["tender_agency"]["requires_manual_confirm"] is False


def test_notice_template_titles_are_not_project_name() -> None:
    record = tender_notice_record()
    record["parsed_text"] = "\n".join(["范本编号：SGCC-01-FWGK(2025)", "国网河北省电力有限公司采购文件范本", "服务公开招标文件分册", "招标公告", "招标编号：042513"])

    result = extract_tender_metadata("0425139003001-6582", metadata={"files": [record]}, save=False)

    assert result["fields"]["project_name"]["value"] is None


def test_demand_list_matches_lot_name_for_package_fields() -> None:
    demand_record = {
        "id": "file_demand",
        "original_name": "embedded_attachment_3_2.xlsx",
        "file_ext": "xlsx",
        "category": "招标需求一览表",
        "tags": ["tender_demand_list"],
        "parsed_text": "",
        "tables": [
            {
                "rows": [
                    {
                        "row": 1,
                        "cells": [
                            {"value": "序号"},
                            {"value": "分标名称（公告版）"},
                            {"value": "分标编号"},
                            {"value": "包号"},
                            {"value": "最高限价（万元）"},
                            {"value": "业绩要求（专用资格业绩条件）"},
                            {"value": "网省项目名称"},
                            {"value": "工程规模"},
                            {"value": "工期"},
                        ],
                    },
                    {
                        "row": 2,
                        "cells": [
                            {"value": "265"},
                            {"value": "综合服务-科技检修技术标准"},
                            {"value": "0425139003001-6582"},
                            {"value": "包01"},
                            {"value": "50"},
                            {"value": "近5年，投标人具有技术开发或技术服务业绩。"},
                            {"value": "国网河北超高压公司生产管控中心2025年基于区块链的电力系统数据管理国际标准研究"},
                            {"value": "基于区块链的电力系统数据管理国际标准研究"},
                            {"value": "开工日期20250731,竣工日期20251231"},
                        ],
                    },
                ]
            }
        ],
    }
    metadata = {
        "files": [
            tender_notice_record(),
            demand_record,
        ]
    }

    classification = classify_file(demand_record).model_dump()
    result = extract_tender_metadata("0425139003001-6582", metadata=metadata, save=False)
    fields = result["fields"]

    assert classification["category"] == "招标需求一览表"
    assert classification["source_type"] == "procurement_package_table"
    assert fields["project_name"]["value"] == "国网河北电力2025年第三次服务类公开招标采购"
    assert fields["project_name"]["requires_manual_confirm"] is False
    assert fields["lot_no"]["value"] == "0425139003001-6582"
    assert fields["lot_name"]["value"] == "综合服务-科技检修技术标准"
    assert fields["package_no"]["value"] == "包01"
    assert fields["package_name"]["value"] == "基于区块链的电力系统数据管理国际标准研究"
    assert fields["technical_topic_name"]["value"] == "基于区块链的电力系统数据管理国际标准研究"
    assert fields["service_period"]["value"] == "开工日期20250731,竣工日期20251231"
    assert fields["max_price"]["value"] == "50"
    assert fields["special_qualification_requirement"]["value"] == "近5年，投标人具有技术开发或技术服务业绩"

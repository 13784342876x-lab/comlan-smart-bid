from __future__ import annotations

from app.tender_metadata_extractor import PROJECT_NAME_FALLBACK, extract_tender_metadata


REQUIRED_FIELD_KEYS = {
    "value",
    "source_file_id",
    "source_file_name",
    "source_type",
    "evidence",
    "confidence",
    "requires_manual_confirm",
}


def test_full_tender_project_name_beats_technical_spec_filename() -> None:
    metadata = {
        "files": [
            {
                "id": "file_full",
                "original_name": "招标文件-服务.docx",
                "category": "招标文件",
                "tags": ["full_tender_document"],
                "parsed_text": "招标编号：SG-2026-001\n项目名称：国网工业数据治理服务采购项目\n分标编号：标1\n分标名称：数字化服务\n包号：包1\n包名称：工业数据管理\n招标人：某电力公司\n招标代理机构：某招标代理公司\n投标人须知前附表 响应文件组成 签字盖章 否决 无效响应 资格条件 服务期限 采购需求一览表 分标分包表",
                "tables": [],
            },
            {
                "id": "file_spec",
                "original_name": "技术规范书(基于区块链的工业数据管理技术研究).docx",
                "category": "技术规范书",
                "parsed_text": "项目名称：基于区块链的工业数据管理技术研究\n技术规范书 服务内容",
                "tables": [],
            },
        ]
    }

    result = extract_tender_metadata("0425139003001-6582", metadata=metadata, save=False)
    fields = result["fields"]

    assert fields["project_name"]["value"] == "国网工业数据治理服务采购项目"
    assert fields["project_name"]["source_type"] == "full_tender_document"
    assert fields["technical_topic_name"]["value"] == "基于区块链的工业数据管理技术研究"
    assert fields["technical_topic_name"]["source_type"] == "technical_spec"
    assert fields["project_name"]["confidence"] > 0.8
    assert fields["project_name"]["confidence"] > fields["technical_topic_name"]["confidence"]


def test_technical_spec_only_does_not_confirm_project_name() -> None:
    metadata = {
        "files": [
            {
                "id": "file_spec",
                "original_name": "技术规范书(基于区块链的工业数据管理技术研究).docx",
                "category": "技术规范书",
                "parsed_text": "项目名称：基于区块链的工业数据管理技术研究\n技术规范书 服务内容",
                "tables": [],
            }
        ]
    }

    result = extract_tender_metadata("0425139003001-6582", metadata=metadata, save=False)
    fields = result["fields"]

    assert fields["project_name"]["value"] is None
    assert fields["project_name"]["requires_manual_confirm"] is True
    assert fields["technical_topic_name"]["value"] == "基于区块链的工业数据管理技术研究"
    assert any("项目名称" in item for item in result["manual_confirm_items"])


def test_score_table_project_name_is_candidate_lower_than_full_tender() -> None:
    metadata = {
        "files": [
            {
                "id": "file_score",
                "original_name": "评标办法-服务.doc",
                "category": "评标办法",
                "parsed_text": "项目名称：评分表中的项目名称\n评标办法 评分表",
                "tables": [],
            }
        ]
    }

    result = extract_tender_metadata("0425139003001-6582", metadata=metadata, save=False)
    project_name = result["fields"]["project_name"]

    assert project_name["value"] == "评分表中的项目名称"
    assert project_name["source_type"] == "score_table"
    assert project_name["confidence"] < 0.8


def test_field_objects_have_uniform_shape() -> None:
    result = extract_tender_metadata("0425139003001-6582", metadata={"files": []}, save=False)

    for field in result["fields"].values():
        assert REQUIRED_FIELD_KEYS.issubset(set(field.keys()))


def test_project_name_fallback_constant() -> None:
    assert PROJECT_NAME_FALLBACK == "未定位到项目总名称，需从招标公告或招标文件正文确认"


def test_redirect_special_qualification_is_ignored_and_demand_row_wins() -> None:
    metadata = {
        "files": [
            {
                "id": "file_notice",
                "original_name": "招标公告.docx",
                "category": "招标公告",
                "tags": ["tender_notice"],
                "parsed_text": "国网河北电力2025年第三次服务类公开招标采购\n招标公告\n招标编号：042513\n专用资格要求：详见招标公告附件\n招标人: 国网河北省电力有限公司",
            },
            {
                "id": "file_demand",
                "original_name": "embedded_attachment_3_2.xlsx",
                "category": "招标需求一览表",
                "tags": ["tender_demand_list"],
                "parsed_text": "",
                "tables": [
                    {
                        "rows": [
                            ["分标编号", "分标名称（公告版）", "包号", "最高限价（万元）", "业绩要求（专用资格业绩条件）", "工程规模", "工期"],
                            [
                                "0425139003001-6582",
                                "综合服务-科技检修技术标准",
                                "包01",
                                "50",
                                "近5年，投标人具有技术开发或技术服务业绩。",
                                "基于区块链的电力系统数据管理国际标准研究",
                                "开工日期20250731,竣工日期20251231",
                            ],
                        ]
                    }
                ],
            },
        ]
    }

    result = extract_tender_metadata("0425139003001-6582", metadata=metadata, save=False)
    field = result["fields"]["special_qualification_requirement"]
    candidate_values = [
        item["value"]
        for item in result["field_candidates"]["special_qualification_requirement"]
    ]

    assert field["value"] == "近5年，投标人具有技术开发或技术服务业绩"
    assert field["source_type"] == "procurement_package_table"
    assert field["source_file_id"] == "file_demand"
    assert "详见招标公告附件" not in candidate_values

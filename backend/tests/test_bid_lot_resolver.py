from __future__ import annotations

from app.bid_lot_resolver import resolve_project_lot


def record(*, text: str = "", tables: list[dict] | None = None) -> dict:
    return {
        "id": "file_tender_001",
        "original_name": "采购文件.docx",
        "parsed_text": text,
        "tables": tables or [],
    }


def test_tables_do_not_inherit_lot_headers_across_table_boundaries() -> None:
    metadata = {
        "files": [
            record(
                tables=[
                    {
                        "table_index": 1,
                        "source_location": "table_1",
                        "rows": [["包名称"], ["软件订阅服务"]],
                    },
                    {
                        "table_index": 2,
                        "source_location": "table_2",
                        "rows": [
                            ["评审要素", "最低分", "最高分"],
                            ["诚信评价", "-30", "0"],
                            ["项目团队", "工程师", "20"],
                        ],
                    },
                ]
            )
        ]
    }

    result = resolve_project_lot(metadata)

    assert result["lot_resolution_status"] == "resolved_unique"
    assert result["lot_name"] == "软件订阅服务"
    assert [item["display_name"] for item in result["lot_candidates"]] == ["软件订阅服务"]


def test_noise_rows_and_empty_response_templates_are_not_candidates() -> None:
    metadata = {
        "files": [
            record(
                text=(
                    "项目名称：，采购编号：，分标名称：，分标编号：，包名称：，包号：，\n"
                    "响应文件说明：采购文件的全部内容，愿按合同约定完成各项工作。"
                ),
                tables=[
                    {
                        "table_index": 1,
                        "rows": [["包号", "包名称"], ["", ""]],
                    },
                    {
                        "table_index": 2,
                        "rows": [
                            ["最低分", "最高分"],
                            ["-10", "0"],
                            ["-20", "0"],
                            ["-30", "0"],
                        ],
                    },
                    {
                        "table_index": 3,
                        "rows": [["序号", "企业资质等级", "工程师", "不允许"]],
                    },
                ],
            )
        ]
    }

    result = resolve_project_lot(metadata)

    assert result["lot_resolution_status"] == "not_applicable"
    assert result["lot_candidates"] == []


def test_procurement_demand_list_produces_clean_ambiguous_packages_with_evidence() -> None:
    metadata = {
        "files": [
            record(
                text="附件1：项目采购需求清单",
                tables=[
                    {
                        "table_index": 4,
                        "source_location": "table_4",
                        "title": "项目采购需求清单",
                        "rows": [
                            ["分标编号", "包号", "包名称", "服务期限"],
                            ["FF1", "1", "图形图像编辑软件订阅服务", "一年"],
                            ["FF1", "2", "便携式文档编辑软件订阅服务", "一年"],
                        ],
                    }
                ],
            )
        ]
    }

    result = resolve_project_lot(metadata)

    assert result["lot_resolution_status"] == "ambiguous"
    assert [item["value"] for item in result["lot_candidates"]] == ["FF1-1", "FF1-2"]
    assert {item["display_name"] for item in result["lot_candidates"]} == {
        "包1：图形图像编辑软件订阅服务",
        "包2：便携式文档编辑软件订阅服务",
    }
    for candidate in result["lot_candidates"]:
        assert candidate["method"] == "procurement_demand_list"
        assert candidate["source_file_id"] == "file_tender_001"
        assert candidate["source_location"].startswith("table_4:row_")
        assert candidate["label"] == "分标编号 | 包号 | 包名称"
        assert candidate["evidence"][0]["source_file_id"] == "file_tender_001"
        assert candidate["evidence"][0]["source_location"].startswith("table_4:row_")
        assert candidate["evidence"][0]["header"] == ["分标编号", "包号", "包名称", "服务期限"]


def test_procurement_demand_list_has_priority_over_unrelated_labeled_text() -> None:
    metadata = {
        "files": [
            record(
                text="标段名称：历史模板名称\n附件：项目采购需求清单",
                tables=[
                    {
                        "title": "项目采购需求清单",
                        "source_location": "sheet:需求清单",
                        "rows": [["采购包号", "采购包名称"], ["1", "软件订阅服务"]],
                    }
                ],
            )
        ]
    }

    result = resolve_project_lot(metadata)

    assert result["lot_resolution_status"] == "resolved_unique"
    assert result["lot_name"] == "1"
    assert result["lot_candidates"][0]["display_name"] == "包1：软件订阅服务"
    assert result["lot_candidates"][0]["method"] == "procurement_demand_list"


def test_explicit_labeled_lot_remains_supported_with_structured_evidence() -> None:
    result = resolve_project_lot(
        {"files": [record(text="标段名称：软件许可服务\n其他说明：无")]}
    )

    assert result["lot_resolution_status"] == "resolved_unique"
    candidate = result["lot_candidates"][0]
    assert candidate["value"] == "软件许可服务"
    assert candidate["source_file_id"] == "file_tender_001"
    assert candidate["source_location"].startswith("parsed_text:")
    assert candidate["label"] == "标段名称"
    assert candidate["evidence"][0]["value"] == "软件许可服务"

from __future__ import annotations

from pathlib import Path

from app.kb_service import KnowledgeBaseService
from app.metadata_store import MetadataStore
from app.score_table_locator import build_score_items_from_location, locate_score_table


def score_tables() -> list[dict]:
    return [
        {
            "table_index": 2,
            "rows": [
                ["评审要素", "评审内容"],
                ["技术部分总体评价（6-10）", "优：9-10分；良：7-8分；一般：6分"],
                ["项目团队（12-20）", "项目负责人优：10分；良：8-9分；一般：6-7分"],
                ["服务方案（18-30）", "优秀27-30分；良好22-26分；一般18-21分"],
            ],
        },
        {
            "table_index": 3,
            "rows": [
                ["项目", "评分项", "评审细则"],
                ["管理方式（6-10）", "管理方案（6-10）", "优秀5分，良好4分，一般3分"],
                ["业绩情况（7-15）", "以往业绩（7-15）", "基础7分，每增加一个业绩加2分，最高15分"],
                ["技术方案（12-25）", "技术方案（12-25）", "优秀13-15分，良好10-12分，一般7-9分"],
            ],
        },
        {
            "table_index": 4,
            "rows": [
                ["评审要素", "评审内容"],
                ["应答文件技术部分总体评价（6-10）", "优：9-10分；良：7-8分；一般：6分"],
                ["工作基础（0-5）", "已经产品化5分，有产品3分，无产品0分"],
                ["项目团队（12-20）", "优：10分；良：8-9分；一般：6-7分"],
            ],
        },
        {
            "table_index": 5,
            "rows": [
                ["项目", "评审要素", "评审内容", "最低分", "最高分"],
                ["诚信评价（-60-0）", "不良行为（-30-0分）", "存在不良行为扣分", "-30", "0"],
                ["绿色低碳评价（0-3）", "绿电绿证（0-3分）", "取得证书得3分", "0", "3"],
                ["综合评价（67-90）", "综合实力（26-36分）", "对能力、人员、资质综合评审", "26", "36"],
            ],
        },
        {
            "table_index": 6,
            "rows": [
                ["报价评分标准"],
                [
                    "区间平均价浮动法\n"
                    "1.评标价格在算术平均值A1的[1+W1,1+W2]区间内的投标人为有效投标人。\n"
                    "注：W1、W2为有效报价区间参数。\n"
                    "计算基准价\n1.计算有效投标人的评标价算术平均值A2。\n"
                    "2.基准价=A2*（1-C）。\n计算价格部分得分\n"
                    "价格部分得分=100-100×n×|投标人的评标总价-基准价|/基准价；\n"
                    "n1=1；n2=0.5；W1=-15%；W2=10%；C=1.0%"
                ],
            ],
        },
    ]


def score_text() -> str:
    return "\n".join(
        [
            "技术评分标准（项目具体技术评分标准选用见附件：项目采购需求清单）",
            "服务类技术评分：",
            "=== Table 2 ===",
            "产品加工服务技术评分：",
            "=== Table 3 ===",
            "科技技术服务技术评分：",
            "=== Table 4 ===",
            "商务评分标准",
            "=== Table 5 ===",
            "报价评分办法",
            "=== Table 6 ===",
        ]
    )


def test_structural_extraction_keeps_all_score_rules_without_template_whitelist() -> None:
    location = locate_score_table(score_text(), score_tables(), None)
    parsed = build_score_items_from_location(location, score_tables())

    assert {
        item["heading"] for item in location["technical_score_table_candidates"]
    } == {"服务类技术评分", "产品加工服务技术评分", "科技技术服务技术评分"}
    assert location["business_score_table"]["table_index"] == 5
    assert location["price_scoring_method"]["method_name"] == "区间平均价浮动法"
    assert location["score_rules_extracted"] is True
    assert location["technical_template_resolution_status"] == "unresolved"
    assert location["technical_template_unresolved"] is True
    assert location["component_weights_unresolved"] is True
    assert location["unsupported_score_format"] is False
    assert len(parsed["technical_score_table_candidates"]) == 3
    assert all(item["score_items"] for item in parsed["technical_score_table_candidates"])
    assert parsed["technical_score_items"] == []
    assert parsed["business_score_items"]


def test_price_parameters_are_not_component_weights() -> None:
    location = locate_score_table(score_text(), score_tables(), None)
    price = location["price_scoring_method"]

    assert location["weights"] == {}
    assert price["benchmark_price_algorithm"].startswith("1.计算有效投标人的评标价算术平均值A2")
    assert price["score_formula"].startswith("价格部分得分=100-100×n")
    assert price["parameters"] == {
        "n1": {"value": 1, "unit": "coefficient"},
        "n2": {"value": 0.5, "unit": "coefficient"},
        "W1": {"value": -15, "unit": "percent"},
        "W2": {"value": 10, "unit": "percent"},
        "C": {"value": 1, "unit": "percent"},
    }


def test_scoped_mapping_resolves_one_technical_candidate() -> None:
    sources = [
        {
            "file_id": "file_package_list",
            "parsed_text": "",
            "tables": [
                {
                    "table_index": 1,
                    "rows": [
                        ["包号", "项目名称", "技术评分标准"],
                        ["包1", "软件订阅服务", "科技技术服务技术评分"],
                    ],
                }
            ],
        }
    ]

    location = locate_score_table(score_text(), score_tables(), "包1", applicability_texts=sources)

    assert location["technical_template_resolution_status"] == "resolved_mapping"
    assert location["selected_technical_candidate_id"] == "technical_table_4"
    assert location["technical_template_unresolved"] is False
    assert location["technical_template_evidence"][0]["source_file_id"] == "file_package_list"


def test_parse_service_preserves_rules_at_technical_applicability_gate(tmp_path: Path) -> None:
    metadata = MetadataStore(tmp_path / "metadata.json")
    metadata.save(
        {
            "files": [
                {
                    "id": "file_score",
                    "original_name": "采购文件.docx",
                    "parsed_text": score_text(),
                    "tables": score_tables(),
                }
            ]
        }
    )

    result = KnowledgeBaseService(metadata=metadata).parse_score_table("file_score", None)

    assert result["schema_version"] == "score_table_parse_v2"
    assert result["status"] == "needs_review"
    assert result["parsed"] is True
    assert result["review_gate"] == "technical_template_unresolved"
    assert result["score_rules_extracted"] is True
    assert len(result["technical_score_table_candidates"]) == 3
    assert result["business_score_table"]["table_index"] == 5
    assert result["price_scoring_method"]["table_index"] == 6


def test_unique_technical_template_still_preserves_rules_when_weights_unresolved(tmp_path: Path) -> None:
    tables = [score_tables()[0], score_tables()[3], score_tables()[4]]
    text = score_text().replace("产品加工服务技术评分：\n=== Table 3 ===\n", "").replace(
        "科技技术服务技术评分：\n=== Table 4 ===\n", ""
    )
    metadata = MetadataStore(tmp_path / "metadata.json")
    metadata.save({"files": [{"id": "file_score", "parsed_text": text, "tables": tables}]})

    result = KnowledgeBaseService(metadata=metadata).parse_score_table("file_score", None)

    assert result["technical_template_resolution_status"] == "resolved_unique"
    assert result["technical_template_unresolved"] is False
    assert result["component_weights_unresolved"] is True
    assert result["review_gate"] == "component_weights_unresolved"
    assert result["technical_score_items"]
    assert result["business_score_items"]
    assert result["price_scoring_method"]


def test_explicit_mapping_and_component_weights_allow_success(tmp_path: Path) -> None:
    weighted_text = score_text() + "\n分值权重构成 商务部分：20% 技术部分：50% 投标报价：30%"
    metadata = MetadataStore(tmp_path / "metadata.json")
    metadata.save(
        {
            "files": [
                {"id": "file_score", "parsed_text": weighted_text, "tables": score_tables()},
                {
                    "id": "file_package_list",
                    "parsed_text": "项目采购需求清单",
                    "tables": [
                        {
                            "table_index": 1,
                            "rows": [
                                ["项目名称", "技术评分标准"],
                                ["软件订阅服务", "服务类技术评分"],
                            ],
                        }
                    ],
                },
            ]
        }
    )

    result = KnowledgeBaseService(metadata=metadata).parse_score_table("file_score", None)

    assert result["status"] == "success"
    assert result["review_gate"] is None
    assert result["technical_template_resolution_status"] == "resolved_mapping"
    assert result["matched_technical_template"] == "服务类技术评分"
    assert result["weights"] == {"business": 20, "technical": 50, "price": 30}
    assert result["technical_score_items"]


def test_unsupported_score_format_has_distinct_status(tmp_path: Path) -> None:
    metadata = MetadataStore(tmp_path / "metadata.json")
    metadata.save(
        {
            "files": [
                {
                    "id": "file_score",
                    "parsed_text": "本文件仅包含一般采购说明。",
                    "tables": [{"table_index": 1, "rows": [["序号", "名称"], ["1", "服务"]]}],
                }
            ]
        }
    )

    result = KnowledgeBaseService(metadata=metadata).parse_score_table("file_score", None)

    assert result["status"] == "needs_review"
    assert result["parsed"] is False
    assert result["review_gate"] == "unsupported_score_format"
    assert result["unsupported_score_format"] is True
    assert result["score_rules_extracted"] is False

import hashlib
import json
from pathlib import Path

from app.company_writing_outline import load_company_outline, validate_company_outline
from app.response_outline_builder import apply_project_score_mappings_to_technical_outline
from app.score_table_locator import build_score_items_from_location
from app.source_package_service import apply_project_profile_to_score_parse
from app.technical_writing_outline_adapter import (
    adapt_technical_writing_outline,
    extract_procurement_targets,
    infer_project_type,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPANY_PATH = BACKEND_DIR / "resources" / "writing" / "company_master_outline_v0.json"


def _point(point_id: str, heading: str, text: str, *, content_type: str = "paragraph", table=None):
    point = {
        "point_id": point_id,
        "original_heading": heading,
        "original_text": text,
        "content_type": content_type,
        "mandatory_marker": "",
        "source_refs": [{"file_name": "current.docx", "clause_path": heading}],
    }
    if table is not None:
        point["table"] = table
    return point


def _response() -> dict:
    procurement = _point(
        "TR-001",
        "*2.采购内容",
        "采购内容",
        content_type="table",
        table={
            "rows": [
                {"row": 1, "cells": [{"column": 1, "text": "序号"}, {"column": 2, "text": "服务名称"}]},
                {"row": 2, "cells": [{"column": 1, "text": "1"}, {"column": 2, "text": "软件订阅服务"}]},
            ]
        },
    )
    software = _point("TR-002", "1.软件服务部分", "提供远程安装支持、订阅授权及技术支持服务。", content_type="table")
    personnel = _point("TR-003", "人员要求", "提供专业技术人员完成服务。")
    return {
        "schema_version": "technical_response_outline_v2",
        "chapters": [
            {
                "chapter_id": "CH-001",
                "title": "总体技术要求",
                "source_category": "technical_specification",
                "sections": [],
                "response_points": [procurement, software, personnel],
            }
        ],
        "display_blocks": [],
    }


def _project(count: int = 3) -> dict:
    scores = [
        {
            "id": f"technical_{index:02d}",
            "item_name": f"服务方案要点{index}",
            "score_rule": "服务内容、人员和实施安排应满足采购要求。",
            "score": float(index),
            "category": "technical",
            "source_template_name": "服务类技术评分",
        }
        for index in range(1, count + 1)
    ]
    return {
        "score_items": scores,
        "generation_tasks": [
            {
                "task_id": f"task_{index:03d}",
                "task_type": "technical",
                "task_name": item["item_name"],
                "related_score_item_ids": [item["id"]],
                "related_requirements": [item["score_rule"]],
            }
            for index, item in enumerate(scores, start=1)
        ],
    }


def test_company_outline_is_unchanged_and_valid_after_adaptation():
    before = hashlib.sha256(COMPANY_PATH.read_bytes()).hexdigest()
    company = load_company_outline(COMPANY_PATH)
    response = _response()
    apply_project_score_mappings_to_technical_outline(response, _project())
    adapt_technical_writing_outline(company, response)

    assert hashlib.sha256(COMPANY_PATH.read_bytes()).hexdigest() == before
    assert validate_company_outline(company)["valid"] is True


def test_profile_overlay_rematerializes_dynamic_components_and_combined_score_items():
    score_parse = {
        "technical_score_table_candidates": [
            {"candidate_id": "other", "heading": "其他模板", "score_items": [{"id": "old", "category": "technical"}]},
            {
                "candidate_id": "selected",
                "heading": "服务类技术评分",
                "score_items": [
                    {"id": "technical_1", "category": "technical", "generation_instruction": "a"},
                    {"id": "technical_2", "category": "technical", "generation_instruction": "b"},
                    {"id": "technical_3", "category": "technical", "generation_instruction": "c"},
                ],
            },
        ],
        "business_score_items": [{"id": "business_1", "category": "business", "generation_instruction": "d"}],
        "technical_score_items": [{"id": "old", "category": "technical"}],
        "price_score_rule": {"id": "price_1", "category": "price", "generation_instruction": "e"},
    }
    effective = apply_project_profile_to_score_parse(score_parse, {"technical_score_template": "服务类技术评分"})

    assert [item["id"] for item in effective["technical_score_items"]] == ["technical_1", "technical_2", "technical_3"]
    assert [item["category"] for item in effective["score_items"]] == ["business", "technical", "technical", "technical", "price"]
    assert len(effective["generation_tasks"]) == 5


def test_score_items_preserve_selected_table_and_row_provenance():
    result = build_score_items_from_location(
        {
            "selected_technical_candidate_id": "tech",
            "technical_score_table_candidates": [{"candidate_id": "tech", "table_index": 7, "heading": "服务类技术评分"}],
            "weights": {},
        },
        [
            {
                "table_index": 7,
                "rows": [["评分项", "评分标准"], ["服务方案", "服务承诺完整得5分"]],
            }
        ],
    )

    item = result["technical_score_items"][0]
    assert item["source_table"] == {"table_index": 7, "heading": "服务类技术评分"}
    assert item["source_row"] == {"table_row_index": 2, "data_row_index": 1}
    assert item["source_refs"][0]["row_or_cell"] == "row 2"


def test_v2_response_outline_allows_score_rows_without_forced_response_mapping():
    response = _response()
    apply_project_score_mappings_to_technical_outline(response, _project(3))
    points = [point for chapter in response["chapters"] for point in chapter["response_points"]]

    assert response["statistics"]["technical_score_item_count"] == 3
    assert response["statistics"]["response_points_with_score_refs"] == 0
    assert all("response_point_ids" in item for item in response["score_items"])
    assert any(not item["response_point_ids"] for item in response["score_items"])
    assert not any(point.get("score_item_refs") for point in points)


def test_adapter_uses_generic_service_profile_optional_evidence_and_procurement_targets():
    response = _response()
    apply_project_score_mappings_to_technical_outline(response, _project())
    company = load_company_outline(COMPANY_PATH)
    writing, _, _ = adapt_technical_writing_outline(company, response)
    titles = [
        node["title"]
        for chapter in writing["chapters"]
        for node in [chapter, *(chapter.get("children") or [])]
    ]
    modules = {module for chapter in writing["chapters"] for module in chapter["module_ids"]}

    assert infer_project_type(response, extract_procurement_targets(response)) == "technical_service_research"
    assert extract_procurement_targets(response) == ["软件服务"]
    assert "MODULE-TRAINING" not in modules
    assert "MODULE-SPECIAL-TOPICS" not in modules
    assert "绿色供应链管理方案" not in titles
    assert not any("超融合" in title or "统一运维" in title for title in titles)
    assert any("软件服务技术方案" == title for title in titles)
    assert "软件订阅服务技术方案" not in titles
    assert any(node.get("module_ids") and node.get("response_point_mappings") is not None and node.get("score_item_refs") is not None for chapter in writing["chapters"] for node in [chapter, *(chapter.get("children") or [])])


def test_installation_alone_is_not_goods_but_equipment_integration_is():
    service_response = _response()
    goods_response = {
        "chapters": [
            {
                "response_points": [
                    _point("TR-001", "采购清单", "服务器、交换机和存储设备供货，完成系统集成、到货及安装部署。")
                ]
            }
        ]
    }

    assert infer_project_type(service_response, []) == "technical_service_research"
    assert infer_project_type(goods_response, []) == "goods_system_integration"

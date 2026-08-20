from __future__ import annotations

import json
from pathlib import Path

from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.response_outline_builder import build_response_outline, render_technical_response_outline_from_context
from app.score_maximization_matrix import build_score_maximization_matrix
from tests.test_response_outline_builder import sample_metadata, sample_project


ALLOWED_SOURCE_TYPES = {
    "tender_original_fact",
    "enterprise_existing_fact",
    "rule_derived",
    "system_judgement",
    "human_confirmation_required",
    "suggested_material",
    "suggested_writing",
}


def context_project() -> dict:
    project = sample_project()
    project["lot_name"] = "0425139003001-6582"
    project["score_file_id"] = "file_score_001"
    project["generation_tasks"].append(
        {
            "task_id": "task_overall",
            "task_type": "technical",
            "task_name": "应答文件技术部分总体评价",
            "related_score_item_ids": ["score_task_overall"],
        }
    )
    project["score_items"].append(
        {
            "id": "score_task_overall",
            "category": "technical",
            "item_name": "应答文件技术部分总体评价",
            "score_range": {"min": 6, "max": 10},
            "raw_text": "对应答文件技术内容的规范性、完整性、逻辑性等方面进行评审。",
            "score_rule": "优：9-10分；良：7-8分；一般：6分。",
            "source_template_name": "技术详评模板V1.0",
        }
    )
    project["tender_project_metadata"] = {
        "fields": {
            "tender_no": {"value": "042513", "source_type": "tender_notice"},
            "project_name": {"value": "国网河北电力2025年第三次服务类公开招标采购", "source_type": "tender_notice"},
            "lot_no": {"value": "0425139003001-6582", "source_type": "procurement_package_table"},
            "lot_name": {"value": "综合服务-科技检修技术标准", "source_type": "procurement_package_table"},
            "package_no": {"value": "包01", "source_type": "procurement_package_table"},
            "package_name": {"value": "基于区块链的电力系统数据管理国际标准研究", "source_type": "procurement_package_table"},
            "special_qualification_requirement": {"value": "近5年，投标人具有技术开发或技术服务业绩", "source_type": "procurement_package_table"},
        },
        "field_candidates": {
            "package_name": [
                {
                    "value": "基于区块链的电力系统数据管理国际标准研究",
                    "source_file_id": "file_demand",
                    "source_file_name": "需求一览表.xlsx",
                    "source_type": "procurement_package_table",
                    "confidence": 0.78,
                },
                {
                    "value": "基于区块链的工业数据管理技术研究",
                    "source_file_id": "file_spec_001",
                    "source_file_name": "技术规范书.docx",
                    "source_type": "technical_spec",
                    "confidence": 0.42,
                },
            ]
        },
    }
    return project


def load_context(result: dict) -> dict:
    path = next(Path(item["path"]) for item in result["files"] if item["kind"] == "technical_response_context")
    return json.loads(path.read_text(encoding="utf-8"))


def test_context_has_nine_typed_chapters_and_render_is_context_driven(tmp_path) -> None:
    result = build_response_outline(context_project(), metadata=sample_metadata(), output_dir=tmp_path)
    context = load_context(result)
    markdown_path = next(Path(item["path"]) for item in result["files"] if item["kind"] == "technical_response_outline")

    assert len(context["chapters"]) == 9
    assert all(chapter["score_refs"] for chapter in context["chapters"])
    for chapter in context["chapters"]:
        for key in [
            "score_refs",
            "tender_requirement_refs",
            "technical_spec_refs",
            "response_format_refs",
            "evidence_refs",
            "missing_materials",
            "manual_confirm_items",
            "writing_tasks",
        ]:
            assert all(item["source_type"] in ALLOWED_SOURCE_TYPES for item in chapter[key])
    assert render_technical_response_outline_from_context(context) == markdown_path.read_text(encoding="utf-8")


def test_package_name_conflict_is_global_human_confirmation(tmp_path) -> None:
    context = load_context(build_response_outline(context_project(), metadata=sample_metadata(), output_dir=tmp_path))
    conflict = next(item for item in context["project_conflicts"] if item["field"] == "package_name")
    confirmation = next(item for item in context["global_manual_confirm_items"] if item["related_field"] == "package_name")

    assert conflict["status"] == "needs_human_confirmation"
    assert conflict["selected_value"] == "基于区块链的电力系统数据管理国际标准研究"
    assert "基于区块链的工业数据管理技术研究" in conflict["candidate_values"]
    assert confirmation["source_type"] == "human_confirmation_required"


def test_matched_materials_are_not_unconditionally_confirmed_or_cross_typed(tmp_path) -> None:
    context = load_context(build_response_outline(context_project(), metadata=sample_metadata(), output_dir=tmp_path))
    by_title = {chapter["chapter_title"]: chapter for chapter in context["chapters"]}

    owner_statuses = {item["evidence_status"] for item in by_title["项目负责人情况"]["evidence_refs"]}
    assert owner_statuses.intersection({"candidate", "needs_review"})
    assert "confirmed" not in owner_statuses
    for title in ["业绩与项目经验", "专利 / 知识产权 / 创新成果", "绩效评价材料"]:
        assert all(item["evidence_status"] != "confirmed" for item in by_title[title]["evidence_refs"])


def test_context_does_not_invent_page_or_cell_locations(tmp_path) -> None:
    context = load_context(build_response_outline(context_project(), metadata=sample_metadata(), output_dir=tmp_path))
    payload = json.dumps(context, ensure_ascii=False)
    precisions = {
        location["location_precision"]
        for chapter in context["chapters"]
        for key in ["score_refs", "tender_requirement_refs", "technical_spec_refs", "response_format_refs", "evidence_refs"]
        for item in chapter[key]
        for location in [item.get("source_location") or {}]
        if location
    }

    assert precisions <= {"page_exact", "table_cell_exact", "paragraph_exact", "clause_approximate", "snippet_only", "file_only", "unknown"}
    assert '"page_number"' not in payload
    assert '"cell_coordinate"' not in payload


def test_score_matrix_runs_without_markdown_and_declares_context_source(tmp_path) -> None:
    result = build_response_outline(context_project(), metadata=sample_metadata(), output_dir=tmp_path)
    markdown_path = next(Path(item["path"]) for item in result["files"] if item["kind"] == "technical_response_outline")
    markdown_path.unlink()

    build_score_maximization_matrix("0425139003001-6582", output_dir=tmp_path)
    matrix_path = tmp_path / "0425139003001-6582" / "outline" / "score_maximization_matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

    assert "technical_response_context" in matrix["generated_from"]
    assert "technical_response_outline" not in matrix["generated_from"]


def test_context_and_markdown_have_no_bid_decision_language(tmp_path) -> None:
    result = build_response_outline(context_project(), metadata=sample_metadata(), output_dir=tmp_path)
    text = "\n".join(Path(item["path"]).read_text(encoding="utf-8") for item in result["files"])

    for term in FORBIDDEN_DECISION_TERMS:
        assert term.lower() not in text.lower()

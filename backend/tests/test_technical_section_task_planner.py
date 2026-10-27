from pathlib import Path

from app.company_writing_outline import load_company_outline
from app.draft_unit_planner import plan_draft_units
from app.response_outline_builder import apply_project_score_mappings_to_technical_outline
from app.technical_section_task_planner import build_section_task_plan
from app.technical_task_information_planner import plan_task_information_requirements
from app.technical_writing_outline_adapter import adapt_technical_writing_outline


BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPANY_PATH = BACKEND_DIR / "resources" / "writing" / "company_master_outline_v0.json"


def _inputs():
    response = {
        "schema_version": "technical_response_outline_v2",
        "chapters": [
            {
                "chapter_id": "CH-001",
                "title": "总体技术要求",
                "source_category": "technical_specification",
                "sections": [],
                "response_points": [
                    {
                        "point_id": "TR-001",
                        "original_heading": "服务内容",
                        "original_text": "提供订阅服务、远程安装支持、人员服务和验收材料。",
                        "content_type": "paragraph",
                        "mandatory_marker": "",
                        "source_refs": [{"file_name": "current.docx", "clause_path": "服务内容"}],
                    }
                ],
            }
        ],
        "display_blocks": [],
    }
    project = {
        "score_items": [
            {
                "id": "technical_service",
                "item_name": "服务方案",
                "score_rule": "服务内容和实施安排满足要求。",
                "score": 10,
                "category": "technical",
            }
        ],
        "generation_tasks": [
            {
                "task_id": "task_001",
                "task_type": "technical",
                "task_name": "服务方案",
                "related_score_item_ids": ["technical_service"],
                "related_requirements": ["服务内容和实施安排满足要求。"],
            }
        ],
    }
    apply_project_score_mappings_to_technical_outline(response, project)
    company = load_company_outline(COMPANY_PATH)
    writing, _, _ = adapt_technical_writing_outline(company, response)
    tasks = build_section_task_plan(response, writing, company)
    information, _ = plan_task_information_requirements(response, writing, tasks, company)
    return response, writing, tasks, information


def test_generic_normalizer_preserves_adapter_structure_and_required_metadata():
    _, writing, _, _ = _inputs()
    serialized_titles = "\n".join(
        node["title"]
        for chapter in writing["chapters"]
        for node in [chapter, *(chapter.get("children") or [])]
    )

    assert writing["refinement"]["normalization"] == "generic_metadata_only"
    assert "超融合" not in serialized_titles
    assert "统一运维" not in serialized_titles
    assert all(chapter["module_ids"] for chapter in writing["chapters"])
    assert writing["coverage"]["response_point_coverage_rate"] == 1.0


def test_section_tasks_and_draft_units_use_module_metadata_not_fixed_wc_semantics():
    _, writing, tasks, information = _inputs()
    units = plan_draft_units(writing, tasks, information)

    assert tasks["tasks"]
    assert all(task["module_ids"] for task in tasks["tasks"])
    assert any(task["score_item_refs"] for task in tasks["tasks"])
    assert not any(
        task["score_item_refs"]
        for task in tasks["tasks"]
        if "实施部署方案" in task["target_path"]
    )
    assert units["coverage"]["all_tasks_planned_once"] is True
    assert not any("超融合" in value for value in units["terminology_constraints"])

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.markdown_draft_writer import export_markdown_drafts, save_markdown_drafts_index


def sample_project() -> dict:
    evidence_ref = {
        "file_id": "file_resume_001",
        "source": "agent_drafts",
        "title": "项目负责人简历",
        "snippet": "项目负责人具备相关项目经验和技术背景。",
        "material_type": "person_resume",
        "confidence": 0.86,
    }
    return {
        "lot_name": "lot-001",
        "project_name": "测试项目",
        "material_analysis": {"summary": {"total_tasks": 4}},
        "writing_plan": {
            "summary": {"total_tasks": 4},
            "draft_plan": [
                {
                    "task_id": "t-tech",
                    "task_name": "项目负责人情况",
                    "task_type": "technical",
                    "section_type": "evidence_backed",
                    "writer_agent": "technical_writer",
                    "writing_goal": "围绕负责人经验组织技术响应。",
                    "score_range": {"min": 0, "max": 5},
                    "outline": ["人员基本情况", "相关项目经验"],
                    "evidence_refs": [deepcopy(evidence_ref)],
                    "missing_materials": [],
                },
                {
                    "task_id": "t-business",
                    "task_name": "商务响应",
                    "task_type": "business",
                    "section_type": "evidence_backed",
                    "writer_agent": "business_writer",
                    "writing_goal": "整理商务材料交接信息。",
                    "evidence_refs": [],
                    "missing_materials": [],
                },
                {
                    "task_id": "t-price",
                    "task_name": "报价质量",
                    "task_type": "price",
                    "section_type": "not_applicable",
                    "writer_agent": "none",
                    "writing_goal": "报价事项由财务处理。",
                    "evidence_refs": [],
                    "missing_materials": [],
                },
                {
                    "task_id": "t-missing",
                    "task_name": "专利数量",
                    "task_type": "technical",
                    "section_type": "missing_blocked",
                    "writer_agent": "technical_writer",
                    "writing_goal": "需要专利证明材料。",
                    "evidence_refs": [],
                    "missing_materials": [
                        {
                            "material_type": "patent",
                            "reason": "未找到专利证明材料。",
                            "suggested_action": "补充专利证书或证明文件。",
                        }
                    ],
                },
            ],
            "draft_sections": [
                {
                    "task_id": "t-tech",
                    "task_name": "项目负责人情况",
                    "section_type": "evidence_backed",
                    "writer_agent": "technical_writer",
                    "draft_text": "根据已匹配材料，可围绕负责人经验展开。",
                    "evidence_refs": [deepcopy(evidence_ref)],
                    "missing_materials": [],
                    "status": "draft_skeleton",
                },
                {
                    "task_id": "t-business",
                    "task_name": "商务响应",
                    "section_type": "evidence_backed",
                    "writer_agent": "business_writer",
                    "draft_text": "商务正文不应进入交接文件。",
                    "evidence_refs": [],
                    "missing_materials": [],
                    "status": "draft_skeleton",
                },
                {
                    "task_id": "t-price",
                    "task_name": "报价质量",
                    "section_type": "not_applicable",
                    "writer_agent": "none",
                    "draft_text": "报价事项说明。",
                    "evidence_refs": [],
                    "missing_materials": [],
                    "status": "not_applicable",
                },
                {
                    "task_id": "t-missing",
                    "task_name": "专利数量",
                    "section_type": "missing_blocked",
                    "writer_agent": "technical_writer",
                    "draft_text": "当前缺少支撑材料，建议补充专利证明材料。",
                    "evidence_refs": [],
                    "missing_materials": [
                        {
                            "material_type": "patent",
                            "reason": "未找到专利证明材料。",
                            "suggested_action": "补充专利证书或证明文件。",
                        }
                    ],
                    "status": "blocked_by_missing_material",
                },
            ],
        },
        "agent_drafts": {
            "summary": {"total_sections": 4},
            "agent_draft_sections": [
                {
                    "task_id": "t-tech",
                    "task_name": "项目负责人情况",
                    "task_type": "technical",
                    "section_type": "evidence_backed",
                    "writer_agent": "technical_writer",
                    "status": "generated_mock",
                    "draft_text": "Mock 技术初稿：可依据以下材料展开负责人经验。",
                    "evidence_refs": [deepcopy(evidence_ref)],
                    "missing_materials": [],
                },
                {
                    "task_id": "t-business",
                    "task_name": "商务响应",
                    "task_type": "business",
                    "section_type": "evidence_backed",
                    "writer_agent": "business_writer",
                    "status": "generated_mock",
                    "draft_text": "商务正文不应进入交接文件。",
                    "evidence_refs": [],
                    "missing_materials": [],
                },
                {
                    "task_id": "t-price",
                    "task_name": "报价质量",
                    "task_type": "price",
                    "section_type": "not_applicable",
                    "writer_agent": "none",
                    "status": "not_applicable",
                    "draft_text": "报价事项说明。",
                    "evidence_refs": [],
                    "missing_materials": [],
                },
                {
                    "task_id": "t-missing",
                    "task_name": "专利数量",
                    "task_type": "technical",
                    "section_type": "missing_blocked",
                    "writer_agent": "technical_writer",
                    "status": "blocked_by_missing_material",
                    "draft_text": "当前缺少支撑材料，建议补充专利证明材料。",
                    "evidence_refs": [],
                    "missing_materials": [
                        {
                            "material_type": "patent",
                            "reason": "未找到专利证明材料。",
                            "suggested_action": "补充专利证书或证明文件。",
                        }
                    ],
                },
            ],
        },
    }


def read_file(result: dict, kind: str) -> str:
    item = next(file for file in result["files"] if file["kind"] == kind)
    return open(item["path"], encoding="utf-8").read()


def test_export_uses_agent_drafts_for_technical_draft(tmp_path):
    result = export_markdown_drafts(sample_project(), output_dir=tmp_path, source="agent_drafts")

    assert result["source"] == "agent_drafts"
    text = read_file(result, "technical_draft")
    assert "项目负责人情况" in text
    assert "Mock 技术初稿" in text
    assert "file_resume_001" in text


def test_export_falls_back_to_writing_plan(tmp_path):
    project = sample_project()
    project.pop("agent_drafts")

    result = export_markdown_drafts(project, output_dir=tmp_path, source="auto")

    assert result["source"] == "writing_plan"
    assert "根据已匹配材料" in read_file(result, "technical_draft")


def test_business_tasks_go_to_handoff_without_body(tmp_path):
    result = export_markdown_drafts(sample_project(), output_dir=tmp_path, source="agent_drafts")
    text = read_file(result, "business_handoff")

    assert "商务部门" in text
    assert "商务响应" in text
    assert "商务正文不应进入交接文件" not in text


def test_price_handoff_has_no_pricing_calculation_or_price_advice(tmp_path):
    result = export_markdown_drafts(sample_project(), output_dir=tmp_path, source="agent_drafts")
    text = read_file(result, "price_handoff")

    assert "报价质量" in text
    assert "不做报价测算" in text
    assert "价格建议" not in text
    assert "报价策略" not in text


def test_missing_blocked_goes_to_missing_materials_without_fabrication(tmp_path):
    result = export_markdown_drafts(sample_project(), output_dir=tmp_path, source="agent_drafts")
    text = read_file(result, "missing_materials")

    assert "专利数量" in text
    assert "未找到专利证明材料" in text
    assert "证书编号" not in text
    assert "已取得" not in text


def test_evidence_refs_are_exported_to_evidence_index(tmp_path):
    result = export_markdown_drafts(sample_project(), output_dir=tmp_path, source="agent_drafts")
    text = read_file(result, "evidence_index")

    assert "file_resume_001" in text
    assert "项目负责人简历" in text
    assert "person_resume" in text


def test_markdown_outputs_have_no_decision_language(tmp_path):
    result = export_markdown_drafts(sample_project(), output_dir=tmp_path, source="agent_drafts")
    text = "\n".join(read_file(result, item["kind"]) for item in result["files"])

    for term in FORBIDDEN_DECISION_TERMS:
        assert term.lower() not in text.lower()


def test_markdown_index_can_be_written_back(tmp_path):
    project = sample_project()
    result = export_markdown_drafts(project, output_dir=tmp_path, source="agent_drafts")
    path = tmp_path / "tender_projects.json"
    original_material_analysis = deepcopy(project["material_analysis"])
    original_writing_plan = deepcopy(project["writing_plan"])
    original_agent_drafts = deepcopy(project["agent_drafts"])
    path.write_text(json.dumps({"projects": [project]}, ensure_ascii=False), encoding="utf-8")

    index = save_markdown_drafts_index("lot-001", result, path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))["projects"][0]

    assert index["files"]
    assert saved["markdown_drafts"]["summary"]["total_files"] == 6
    assert saved["material_analysis"] == original_material_analysis
    assert saved["writing_plan"] == original_writing_plan
    assert saved["agent_drafts"] == original_agent_drafts


def test_export_does_not_mutate_existing_structures(tmp_path):
    project = sample_project()
    before = deepcopy(project)

    export_markdown_drafts(project, output_dir=tmp_path, source="agent_drafts")

    assert project["material_analysis"] == before["material_analysis"]
    assert project["writing_plan"] == before["writing_plan"]
    assert project["agent_drafts"] == before["agent_drafts"]


def test_forbidden_decision_language_raises(tmp_path):
    project = sample_project()
    project["agent_drafts"]["agent_draft_sections"][0]["draft_text"] = "建议投标"

    with pytest.raises(ValueError):
        export_markdown_drafts(project, output_dir=tmp_path, source="agent_drafts")

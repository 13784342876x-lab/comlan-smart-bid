from __future__ import annotations

import json

from app.scoring_action_planner import (
    build_scoring_action_plan,
    build_section_scoring_action_plan,
    inspect_scoring_action_plan,
    write_scoring_action_plan,
)


def project_with_scores() -> dict:
    return {
        "lot_name": "0425139003001-6582",
        "project_name": "国网河北电力2025年第三次服务类公开招标采购",
        "score_file_id": "file_829b97fcf9b4",
        "score_items": [
            {
                "id": "score_perf",
                "item_name": "业绩情况",
                "score_rule": "具有1项合格业绩的得12分；每多提供一项合格业绩加2分；最高加8分；总分范围12-20分。",
                "source_template_name": "评标办法-服务.doc",
            }
        ],
        "tender_project_metadata": {
            "fields": {
                "special_qualification_requirement": {
                    "value": "近5年，投标人具有技术开发或技术服务业绩",
                    "source_file_id": "file_5092c306fce3",
                    "source_file_name": "需求一览表042513_3.xlsx",
                    "source_type": "procurement_package_table",
                }
            }
        },
    }


def performance_section() -> dict:
    return {
        "section_title": "业绩与项目经验",
        "task_name": "业绩情况",
        "score_ref": {
            "score_item": "业绩情况",
            "score_range": {"min": 12, "max": 20},
            "score_description": "具有1项合格业绩的得12分；每多提供一项合格业绩加2分；最高加8分；总分范围12-20分。",
            "score_source_file": "评标办法-服务.doc",
        },
        "response_requirement_refs": [
            {
                "requirement_type": "performance_proof",
                "original_excerpt": "业绩证明文件仅提供合同等业绩证明文件中能体现第一章招标公告专用资格要求和第三章评标办法前附表之四“技术评分标准”要求的所有要素点的关键页即可，信息不全的证明材料不予认可。",
            }
        ],
        "evidence_groups": {
            "confirmed": [],
            "candidate": [],
            "weak": [
                {"file_id": "file_person", "title": "人员简历", "material_type": "person_resume", "applicability_reason": "人员项目经验不能替代公司业绩证明。"}
            ],
            "unusable": [
                {"file_id": "file_qual", "title": "资质证书", "material_type": "company_qualification", "applicability_reason": "企业资质证书不能替代公司业绩材料。"}
            ],
        },
        "material_basis": {},
    }


def test_external_reference_resolves_to_score_items_not_tender_body() -> None:
    plan = build_section_scoring_action_plan(project_with_scores(), performance_section())

    ref = next(item for item in plan["external_references"] if "附表之四" in item["reference_text"])
    assert ref["resolved"] is True
    assert ref["resolved_source"] == "score_items"
    assert ref["source_file_id"] == "file_829b97fcf9b4"
    assert ref["matched_score_item"] == "业绩情况"
    assert ref["confidence"] == "probable_reference"


def test_missing_external_reference_is_reported_without_fabrication() -> None:
    project = project_with_scores()
    project["score_items"] = []
    plan = build_section_scoring_action_plan(project, performance_section())

    ref = next(item for item in plan["external_references"] if "附表之四" in item["reference_text"])
    assert ref["resolved"] is False
    assert ref["missing_source_type"] == "score_table_or_evaluation_standard"
    assert "补充评标办法" in ref["action"]


def test_performance_max_score_preparation_is_five_company_cases() -> None:
    plan = build_section_scoring_action_plan(project_with_scores(), performance_section())

    assert plan["max_score_preparation"]["target"] == "达到该评分项上限需准备5项合格公司业绩"
    assert "1+4=5项" in plan["max_score_preparation"]["calculation"]
    assert "近5年" in plan["qualified_performance_criteria"]
    assert "技术开发或技术服务" in plan["qualified_performance_criteria"]
    assert any("人员项目经验" in item for item in plan["manual_review_questions"])
    assert plan["kb_material_findings"]["not_usable"]


def test_patent_plan_separates_delivery_requirement_from_existing_patent_material() -> None:
    project = project_with_scores()
    section = {
        "section_title": "专利 / 知识产权 / 创新成果",
        "task_name": "专利数量",
        "score_ref": {
            "score_item": "专利数量",
            "score_range": {"min": 0, "max": 2},
            "score_description": "提供采购相关内容的发明专利或实用新型专利，每提供1项加1分，上限不超过2分。",
            "score_source_file": "评标办法-服务.doc",
        },
        "response_requirement_refs": [
            {
                "source_file_id": "file_spec",
                "source_file_name": "技术规范书.docx",
                "original_excerpt": "提交技术报告。受理发明专利1项。形成国际标准初稿1份。",
            }
        ],
        "evidence_groups": {"confirmed": [], "candidate": [], "weak": [], "unusable": []},
    }

    plan = build_section_scoring_action_plan(project, section)

    assert plan["max_score_preparation"]["target"] == "达到该项上限需准备2项采购相关专利"
    assert plan["delivery_requirements"][0]["text"] == "受理发明专利1项"
    assert "交付成果要求" in plan["delivery_requirements"][0]["note"]


def test_personnel_plan_does_not_fabricate_certificate_requirement() -> None:
    section = {
        "section_title": "项目负责人情况",
        "task_name": "项目负责人情况",
        "score_ref": {
            "score_item": "项目负责人情况",
            "score_range": {"min": 0, "max": 5},
            "score_description": "项目负责人专业、工作年限、工作经验。",
        },
        "response_requirement_refs": [],
        "evidence_groups": {"confirmed": [], "candidate": [], "weak": [], "unusable": []},
    }

    plan = build_section_scoring_action_plan(project_with_scores(), section)

    assert "未定位到招标文件对项目负责人特定证书/社保/职称的硬性要求" in plan["notes"][0]
    assert any(item["material_type"] == "project_leader_resume" for item in plan["required_evidence_package"])


def test_every_score_item_has_material_list_manual_questions_and_files(tmp_path) -> None:
    sections = [
        performance_section(),
        {
            "section_title": "绩效评价材料",
            "task_name": "绩效评价",
            "score_ref": {"score_item": "绩效评价", "score_range": {"min": 0, "max": 3}, "score_description": "根据最近一年度供应商绩效评价结果进行评分。"},
            "response_requirement_refs": [],
            "evidence_groups": {"confirmed": [], "candidate": [], "weak": [], "unusable": []},
        },
    ]
    plan = build_scoring_action_plan(project_with_scores(), sections)
    files = write_scoring_action_plan(plan, tmp_path / "0425139003001-6582" / "outline")
    inspected = inspect_scoring_action_plan("0425139003001-6582", output_dir=tmp_path)

    assert len(plan["sections"]) == 2
    assert all(item["required_evidence_package"] for item in plan["sections"])
    assert all(item["manual_review_questions"] for item in plan["sections"])
    assert files["json"].endswith("scoring_action_plan.json")
    assert files["markdown"].endswith("scoring_action_plan.md")
    assert inspected["files"]["json_exists"] is True
    assert "业绩与项目经验" in json.loads((tmp_path / "0425139003001-6582" / "outline" / "scoring_action_plan.json").read_text(encoding="utf-8"))["sections"][0]["section_title"]

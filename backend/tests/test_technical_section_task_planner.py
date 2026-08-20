import json
from pathlib import Path

from app.company_writing_outline import load_company_outline
from app.technical_section_task_planner import (
    EVIDENCE_DEFINITIONS,
    FACT_DEFINITIONS,
    SOURCE_PRIORITY,
    build_section_task_plan,
    build_section_task_plan_files,
)
from app.technical_writing_outline_adapter import adapt_technical_writing_outline


BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPANY_PATH = BACKEND_DIR / "resources" / "writing" / "company_master_outline_v0.json"
RESPONSE_PATH = (
    BACKEND_DIR
    / "outputs"
    / "hospital_hci_001"
    / "outline"
    / "technical_response_outline.json"
)


def _inputs():
    company = load_company_outline(COMPANY_PATH)
    response = json.loads(RESPONSE_PATH.read_text(encoding="utf-8"))
    writing, _, _ = adapt_technical_writing_outline(company, response)
    return company, response, writing


def _plan():
    company, response, writing = _inputs()
    return build_section_task_plan(response, writing, company)


def _module_map(writing):
    return {item["module_id"]: item for item in writing["module_adaptations"]}


def test_refinement_converges_thirteen_modules_to_eight_chapters():
    _, _, writing = _inputs()

    assert len(writing["module_adaptations"]) == 13
    assert writing["refinement"]["original_top_level_count"] == 13
    assert writing["refinement"]["refined_top_level_count"] == 8
    assert [chapter["title"] for chapter in writing["chapters"]] == [
        "需求理解及分析",
        "方案完整性及系统架构设计",
        "实施部署方案",
        "培训方案",
        "售后服务与运行保障方案",
        "绿色供应链管理方案",
        "技术参数及点对点应答",
        "技术证明材料及附件索引",
    ]


def test_supporting_modules_are_nested_under_the_expected_main_chapters():
    _, _, writing = _inputs()
    placement = _module_map(writing)

    assert placement["MODULE-DETAILED-SOLUTION"]["parent_chapter_id"] == "WC-002"
    for module_id in (
        "MODULE-PROJECT-MANAGEMENT",
        "MODULE-QUALITY-ACCEPTANCE",
        "MODULE-RISK-CONTROL",
    ):
        assert placement[module_id]["placement_role"] == "nested"
        assert placement[module_id]["parent_chapter_id"] == "WC-003"


def test_solution_and_implementation_children_match_refinement_requirements():
    _, _, writing = _inputs()
    by_title = {chapter["title"]: chapter for chapter in writing["chapters"]}
    solution_titles = {child["title"] for child in by_title["方案完整性及系统架构设计"]["children"]}
    implementation_titles = {child["title"] for child in by_title["实施部署方案"]["children"]}

    assert {
        "超融合系统详细技术方案",
        "超融合互联交换机技术方案",
        "超融合节点技术方案",
    } <= solution_titles
    assert {"项目组织架构", "项目负责人及主要人员", "质量控制", "项目验收", "风险控制与应急保障"} <= implementation_titles


def test_technical_deviation_is_conditional_and_not_a_default_chapter():
    _, _, writing = _inputs()
    placement = _module_map(writing)["MODULE-TECHNICAL-DEVIATION"]

    assert placement["activation_status"] == "conditional"
    assert placement["placement_role"] == "form"
    assert placement["parent_chapter_id"] == "WC-007"
    assert "技术偏差表" not in [chapter["title"] for chapter in writing["chapters"]]
    assert "偏离说明列" in placement["placement_reasons"][0]


def test_refined_outline_preserves_response_and_star_coverage():
    _, _, writing = _inputs()

    assert writing["coverage"]["response_point_coverage_rate"] == 1.0
    assert writing["coverage"]["mandatory_coverage_rate"] == 1.0
    assert writing["coverage"]["unmapped_response_point_ids"] == []
    assert writing["coverage"]["mandatory_unmapped_ids"] == []


def test_every_final_writable_leaf_has_one_stable_task():
    _, _, writing = _inputs()
    plan = _plan()
    expected_sections = sum(len(chapter["children"]) or 1 for chapter in writing["chapters"])

    assert len(plan["tasks"]) == expected_sections == 63
    assert [task["task_id"] for task in plan["tasks"]] == [
        f"TASK-{index:03d}" for index in range(1, 64)
    ]
    assert all(task["section_id"] and task["target_path"] for task in plan["tasks"])
    assert all(task["body_generated"] is False for task in plan["tasks"])


def test_missing_project_facts_block_relevant_tasks_without_inference():
    plan = _plan()
    parameter_task = next(task for task in plan["tasks"] if "点对点应答表" in task["target_path"][-1])

    assert parameter_task["readiness"] == "needs_project_fact"
    assert any(slot["name"] == "实际技术参数" and slot["status"] == "missing" for slot in parameter_task["project_fact_slots"])
    assert parameter_task["output_constraints"]["must_use_specific_values"] is True
    assert parameter_task["output_constraints"]["prohibit_generic_compliance_only"] is True
    assert all(slot["prohibit_tender_reverse_inference"] for slot in parameter_task["project_fact_slots"])


def test_fact_and_evidence_catalogs_cover_required_slot_types():
    plan = _plan()

    assert len(plan["project_fact_requests"]) == len(FACT_DEFINITIONS) == 20
    assert {item["name"] for item in plan["project_fact_requests"]} == {name for _, name in FACT_DEFINITIONS}
    assert len(plan["evidence_catalog"]) == len(EVIDENCE_DEFINITIONS) == 15
    assert {item["evidence_type"] for item in plan["evidence_catalog"]} == set(EVIDENCE_DEFINITIONS)
    assert all(item["source_policy"] == "local_kb_evidence" for item in plan["evidence_catalog"])


def test_enterprise_facts_only_create_local_plans_and_never_web_plans():
    plan = _plan()
    fact_queries = [query for query in plan["local_evidence_query_plan"] if query["query_kind"] == "fact_candidate_for_manual_confirmation"]
    web_text = json.dumps(plan["web_research_plan"], ensure_ascii=False)

    assert fact_queries
    assert all(query["execution_status"] == "planned_not_executed" for query in fact_queries)
    assert not any(term in web_text for term in ("本公司资质", "本公司人员", "本公司业绩", "实际产品配置", "服务承诺"))


def test_web_plans_are_optional_official_source_plans_and_are_not_executed():
    plan = _plan()
    allowed = {
        "government_official",
        "health_authority_official",
        "hospital_official",
        "standards_authority_official",
        "manufacturer_official",
    }

    assert plan["web_research_plan"]
    assert all(item["required"] is False for item in plan["web_research_plan"])
    assert all(set(item["allowed_source_types"]) <= allowed for item in plan["web_research_plan"])
    assert all(item["execution_status"] == "planned_not_executed" for item in plan["web_research_plan"])
    assert plan["execution_guard"]["web_research_executed"] is False
    assert plan["execution_guard"]["local_kb_queried"] is False


def test_source_priority_and_general_knowledge_constraint_are_fixed():
    plan = _plan()

    assert plan["source_priority"] == SOURCE_PRIORITY
    assert SOURCE_PRIORITY == [
        "tender_requirement",
        "confirmed_project_fact",
        "local_kb_evidence",
        "official_web_source",
        "general_explanatory_knowledge",
    ]
    assert "不得用于产品、人员、业绩、资质或承诺事实" in plan["source_usage_constraints"]["general_explanatory_knowledge"]


def test_task_level_response_coverage_remains_complete():
    plan = _plan()

    assert plan["coverage"]["response_point_coverage_rate"] == 1.0
    assert plan["coverage"]["mandatory_coverage_rate"] == 1.0
    assert plan["coverage"]["task_unmapped_response_point_ids"] == []
    assert plan["coverage"]["mandatory_unmapped_ids"] == []
    assert plan["coverage"]["all_response_point_ids"] == plan["coverage"]["task_mapped_response_point_ids"]


def test_planner_does_not_read_reference_only_files(monkeypatch, tmp_path):
    company, response, writing = _inputs()
    writing_path = tmp_path / "writing.json"
    writing_path.write_text(json.dumps(writing, ensure_ascii=False), encoding="utf-8")
    read_paths = []
    original_read_text = Path.read_text

    def monitored_read_text(path, *args, **kwargs):
        read_paths.append(str(path))
        if "reference_only" in str(path):
            raise AssertionError("reference_only file must not be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", monitored_read_text)
    result = build_section_task_plan_files(
        RESPONSE_PATH,
        writing_path,
        COMPANY_PATH,
        output_key="isolated",
        output_dir=tmp_path,
    )

    assert len(result["files"]) == 5
    assert all(Path(path).is_file() for path in result["files"].values())
    assert not any("reference_only" in path for path in read_paths)
    assert result["plan"]["execution_guard"]["reference_only_files_read"] is False


def test_planner_output_is_stable_for_identical_inputs():
    company, response, writing = _inputs()

    first = build_section_task_plan(response, writing, company)
    second = build_section_task_plan(response, writing, company)

    assert first == second

import hashlib
import json
from pathlib import Path

from app.draft_unit_planner import build_draft_unit_files, plan_draft_units
from app.technical_task_information_planner import (
    build_task_information_files,
    plan_task_information_requirements,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
RESPONSE_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "outline" / "technical_response_outline.json"
WRITING_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "writing" / "technical_writing_outline.json"
TASKS_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "writing" / "tasks" / "section_writing_tasks.json"
COMPANY_PATH = BACKEND_DIR / "resources" / "writing" / "company_master_outline_v0.json"


def _sources():
    return (
        json.loads(RESPONSE_PATH.read_text(encoding="utf-8")),
        json.loads(WRITING_PATH.read_text(encoding="utf-8")),
        json.loads(TASKS_PATH.read_text(encoding="utf-8")),
        json.loads(COMPANY_PATH.read_text(encoding="utf-8")),
    )


def _planned():
    response, writing, tasks, company = _sources()
    information, matrix = plan_task_information_requirements(response, writing, tasks, company)
    units = plan_draft_units(writing, tasks, information)
    return response, writing, tasks, information, matrix, units


def _info(information, task_id):
    return next(item for item in information["tasks"] if item["task_id"] == task_id)


def test_writing_directory_and_all_atomic_tasks_remain_unchanged():
    _, writing, tasks, information, _, units = _planned()
    expected_titles = [
        "需求理解及分析",
        "方案完整性及系统架构设计",
        "实施部署方案",
        "培训方案",
        "售后服务与运行保障方案",
        "绿色供应链管理方案",
        "技术参数及点对点应答",
        "技术证明材料及附件索引",
    ]

    assert [chapter["title"] for chapter in writing["chapters"]] == expected_titles
    assert units["directory_snapshot"] == [
        {
            "chapter_id": chapter["chapter_id"],
            "title": chapter["title"],
            "child_titles": [child["title"] for child in chapter["children"]],
        }
        for chapter in writing["chapters"]
    ]
    assert len(tasks["tasks"]) == len(information["tasks"]) == 63
    assert [item["task_id"] for item in information["tasks"]] == [item["task_id"] for item in tasks["tasks"]]


def test_each_task_has_three_information_dimensions_and_draftability():
    _, _, _, information, _, _ = _planned()

    for task in information["tasks"]:
        assert set(task["information_requirements"]) == {"manual_project_fact", "local_kb", "web"}
        assert task["information_requirements"]["manual_project_fact"]["need"] in {"none", "optional", "required"}
        assert task["information_requirements"]["local_kb"]["need"] in {"none", "optional", "required"}
        assert task["information_requirements"]["web"]["need"] in {"forbidden", "optional", "required"}
        assert task["draftability"]["status"] in {"can_draft", "can_draft_with_placeholders", "cannot_finalize"}
        assert "readiness" not in task
        assert task["legacy_status_audit"]["superseded_as_single_overall_status"] is True


def test_project_background_can_draft_from_tender_facts():
    _, _, _, information, _, _ = _planned()
    task = _info(information, "TASK-001")

    assert task["target_path"][-1] == "项目背景"
    assert task["draftability"]["status"] == "can_draft"
    assert task["information_requirements"]["manual_project_fact"]["need"] == "optional"
    assert task["information_requirements"]["web"]["need"] == "optional"


def test_product_tasks_require_confirmed_brand_model_parameters_and_local_proof():
    _, _, _, information, _, _ = _planned()
    for task_id in ("TASK-019", "TASK-020", "TASK-021"):
        task = _info(information, task_id)
        manual = task["information_requirements"]["manual_project_fact"]
        local = task["information_requirements"]["local_kb"]

        assert manual["need"] == "required"
        assert {"offered_product_brand", "offered_product_model", "actual_product_configuration", "actual_technical_parameters"} <= set(manual["fact_types"])
        assert local["need"] == "required"
        assert "product_parameter_proof" in local["evidence_types"]
        assert task["draftability"]["status"] == "cannot_finalize"
        assert not any(isinstance(value, (int, float)) for value in manual["fact_types"])


def test_project_team_requires_manual_facts_and_local_personnel_evidence():
    _, _, _, information, _, _ = _planned()
    task = _info(information, "TASK-026")

    assert task["information_requirements"]["manual_project_fact"]["need"] == "required"
    assert {"project_manager", "project_team", "staffing_plan"} <= set(task["information_requirements"]["manual_project_fact"]["fact_types"])
    assert task["information_requirements"]["local_kb"]["need"] == "required"
    assert {"person_resume", "person_certificate"} <= set(task["information_requirements"]["local_kb"]["evidence_types"])


def test_company_qualifications_and_performance_cannot_be_completed_from_web():
    _, _, _, information, _, _ = _planned()
    for task_id in ("TASK-060", "TASK-062"):
        task = _info(information, task_id)
        assert task["information_requirements"]["web"]["need"] == "forbidden"
        assert task["information_requirements"]["web"]["allowed_source_types"] == []
        assert task["information_requirements"]["local_kb"]["need"] == "required"


def test_policy_and_standard_research_is_optional_and_no_unconfirmed_manufacturer_target_exists():
    _, _, _, information, _, _ = _planned()
    background = _info(information, "TASK-001")
    safety = _info(information, "TASK-017")
    product = _info(information, "TASK-019")

    assert background["information_requirements"]["web"]["need"] == "optional"
    assert safety["information_requirements"]["web"]["need"] == "optional"
    assert "manufacturer_official" not in product["information_requirements"]["web"]["allowed_source_types"]
    assert not any("制造商官网" in purpose for purpose in product["information_requirements"]["web"]["purposes"])


def test_readiness_matrix_counts_every_task_in_each_dimension():
    _, _, _, _, matrix, _ = _planned()

    assert matrix["total_tasks"] == 63
    assert sum(matrix["manual_project_fact"].values()) == 63
    assert sum(matrix["local_kb"].values()) == 63
    assert sum(matrix["web"].values()) == 63
    assert sum(matrix["draftability"].values()) == 63
    assert matrix["draftability"]["can_draft"] > 0
    assert matrix["draftability"]["can_draft_with_placeholders"] > 0
    assert matrix["draftability"]["cannot_finalize"] > 0


def test_draft_units_are_rule_derived_and_cover_each_task_exactly_once():
    _, _, tasks, _, _, units = _planned()
    planned_ids = [task_id for unit in units["draft_units"] for task_id in unit["task_ids"]]

    assert 15 <= len(units["draft_units"]) <= 25
    assert sorted(planned_ids) == sorted(item["task_id"] for item in tasks["tasks"])
    assert len(planned_ids) == len(set(planned_ids)) == 63
    assert units["coverage"]["all_tasks_planned_once"] is True
    assert units["coverage"]["unplanned_task_ids"] == []
    assert units["coverage"]["duplicate_task_ids"] == []


def test_three_product_entities_have_independent_draft_units():
    _, _, _, _, _, units = _planned()
    product_units = [unit for unit in units["draft_units"] if "超融合" in unit["title"]]

    assert [(unit["title"], unit["task_ids"]) for unit in product_units] == [
        ("超融合系统详细技术方案", ["TASK-019"]),
        ("超融合互联交换机技术方案", ["TASK-020"]),
        ("超融合节点技术方案", ["TASK-021"]),
    ]


def test_table_and_each_appendix_task_are_isolated_from_narrative_units():
    _, _, _, _, _, units = _planned()
    by_task = {
        task_id: unit
        for unit in units["draft_units"]
        for task_id in unit["task_ids"]
    }

    assert by_task["TASK-055"]["task_ids"] == ["TASK-055"]
    assert by_task["TASK-055"]["content_mode"] == "table"
    assert by_task["TASK-056"]["task_ids"] == ["TASK-056"]
    for number in range(57, 64):
        unit = by_task[f"TASK-{number:03d}"]
        assert unit["task_ids"] == [f"TASK-{number:03d}"]
        assert unit["content_mode"] == "appendix"


def test_only_the_opening_unit_allows_full_project_background():
    _, _, _, _, _, units = _planned()
    full_units = [unit for unit in units["draft_units"] if unit["project_background_policy"] == "full"]

    assert len(full_units) == 1
    assert full_units[0]["chapter_id"] == "WC-001"
    assert full_units[0]["generation_constraints"]["project_background_full_introduction_allowed"] is True
    assert all(
        not unit["generation_constraints"]["project_background_full_introduction_allowed"]
        for unit in units["draft_units"]
        if unit is not full_units[0]
    )


def test_primary_supporting_and_table_locations_are_distinguished():
    _, _, _, _, _, units = _planned()
    locations = units["content_location_plan"]

    assert locations["TR-018"]["primary_content_location"]["mapping_role"] == "primary"
    assert locations["TR-018"]["table_response_locations"]
    assert locations["TR-022"]["primary_content_location"] is not None
    assert locations["TR-022"]["supporting_reference_locations"]


def test_terminology_and_parameter_consistency_are_planned():
    _, _, _, _, _, units = _planned()
    terms = units["terminology_constraints"]
    by_task = {task_id: unit for unit in units["draft_units"] for task_id in unit["task_ids"]}

    assert {"超融合系统", "超融合互联交换机", "超融合节点", "技术要求点对点应答表", "绿色供应链管理"} <= set(terms)
    assert by_task["TASK-019"]["generation_constraints"]["parameter_consistency_required"] is True
    assert by_task["TASK-055"]["generation_constraints"]["parameter_consistency_required"] is True


def test_original_response_and_star_coverage_remain_complete():
    _, writing, tasks, _, _, _ = _planned()

    assert writing["coverage"]["response_point_coverage_rate"] == 1.0
    assert writing["coverage"]["mandatory_coverage_rate"] == 1.0
    assert tasks["coverage"]["response_point_coverage_rate"] == 1.0
    assert tasks["coverage"]["mandatory_coverage_rate"] == 1.0


def test_planners_do_not_execute_retrieval_drafting_docx_or_reference_reads(monkeypatch, tmp_path):
    read_paths = []
    original_read_text = Path.read_text

    def monitored_read_text(path, *args, **kwargs):
        read_paths.append(str(path))
        if "reference_only" in str(path):
            raise AssertionError("reference_only must not be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", monitored_read_text)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (RESPONSE_PATH, WRITING_PATH, TASKS_PATH, COMPANY_PATH)}
    info_result = build_task_information_files(
        RESPONSE_PATH,
        WRITING_PATH,
        TASKS_PATH,
        COMPANY_PATH,
        output_key="guarded",
        output_dir=tmp_path,
    )
    unit_result = build_draft_unit_files(
        WRITING_PATH,
        TASKS_PATH,
        info_result["files"]["task_information_requirements_json"],
        output_key="guarded",
        output_dir=tmp_path,
    )

    assert len(info_result["files"]) == 3 and len(unit_result["files"]) == 2
    assert not any("reference_only" in path for path in read_paths)
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())
    assert all(path.suffix != ".docx" for path in (tmp_path / "guarded" / "writing" / "tasks").iterdir())
    assert all(value is False for value in info_result["requirements"]["separation_guards"].values())
    assert all(value is False for value in unit_result["draft_units"]["separation_guards"].values())


def test_planner_outputs_are_stable_for_identical_inputs():
    response, writing, tasks, company = _sources()
    first_info, first_matrix = plan_task_information_requirements(response, writing, tasks, company)
    second_info, second_matrix = plan_task_information_requirements(response, writing, tasks, company)

    assert first_info == second_info and first_matrix == second_matrix
    assert plan_draft_units(writing, tasks, first_info) == plan_draft_units(writing, tasks, second_info)

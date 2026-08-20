import json
from pathlib import Path

import pytest

from app.technical_agent_context_loader import (
    build_technical_agent_context_files,
    load_technical_agent_context,
)
from app.technical_writing_outline_enricher import merge_information_requirements_into_outline


BACKEND_DIR = Path(__file__).resolve().parents[1]
RESPONSE_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "outline" / "technical_response_outline.json"
WRITING_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "writing" / "technical_writing_outline.json"
TASKS_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "writing" / "tasks" / "section_writing_tasks.json"
INFO_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "writing" / "tasks" / "task_information_requirements.json"
DRAFT_UNITS_PATH = BACKEND_DIR / "outputs" / "hospital_hci_001" / "writing" / "tasks" / "draft_units.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sources():
    return _load(RESPONSE_PATH), _load(WRITING_PATH), _load(TASKS_PATH), _load(INFO_PATH)


def _title_tree(nodes):
    return [
        {
            "chapter_id": node["chapter_id"],
            "title": node["title"],
            "children": _title_tree(node.get("children") or []),
        }
        for node in nodes
    ]


def _mapping_tree(nodes):
    return [
        {
            "chapter_id": node["chapter_id"],
            "response_point_mappings": node.get("response_point_mappings") or [],
            "children": _mapping_tree(node.get("children") or []),
        }
        for node in nodes
    ]


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def test_formal_inputs_are_two_outlines_and_internal_artifacts_are_not_user_inputs():
    writing = _load(WRITING_PATH)
    contract = writing["agent_input_contract"]

    assert contract["formal_planning_inputs"] == [
        "outputs/<output_key>/outline/technical_response_outline.json",
        "outputs/<output_key>/writing/technical_writing_outline.json",
    ]
    assert contract["internal_artifacts_required_from_user"] is False
    assert contract["task_id_is_external_input"] is False
    assert contract["draft_unit_id_is_external_input"] is False
    assert set(contract["internal_analysis_artifacts"]) == {
        "section_writing_tasks.json",
        "task_information_requirements.json",
        "draft_units.json",
        "task_readiness_matrix.json",
    }


def test_enrichment_preserves_directory_and_response_mappings_exactly():
    _, writing, tasks, information = _sources()
    before_titles = _title_tree(writing["chapters"])
    before_mappings = _mapping_tree(writing["chapters"])

    enriched = merge_information_requirements_into_outline(writing, information, tasks)

    assert _title_tree(enriched["chapters"]) == before_titles
    assert _mapping_tree(enriched["chapters"]) == before_mappings
    assert len(enriched["chapters"]) == 8
    assert enriched["coverage"]["response_point_coverage_rate"] == 1.0
    assert enriched["coverage"]["mandatory_coverage_rate"] == 1.0


def test_every_writing_node_has_compact_information_requirements():
    writing = _load(WRITING_PATH)
    nodes = list(_walk(writing["chapters"]))

    assert len(nodes) == 70
    assert writing["writing_requirement_merge"]["matched_atomic_task_count"] == 63
    for node in nodes:
        requirements = node["writing_requirements"]
        assert set(requirements) == {
            "response_point_ids",
            "score_item_refs",
            "required_content_points",
            "manual_project_facts",
            "local_kb",
            "web",
            "draftability",
        }


def test_writing_outline_does_not_embed_tender_raw_text_or_large_parameter_rows():
    writing = _load(WRITING_PATH)
    serialized = json.dumps(writing, ensure_ascii=False)

    assert writing["writing_requirement_merge"]["tender_text_embedded"] is False
    assert '"original_text"' not in serialized
    assert '"table_excerpt"' not in serialized
    assert "C86-4G" not in serialized
    assert "response_point_ids" in serialized


def test_all_writing_response_ids_resolve_to_response_outline():
    response, writing, _, _ = _sources()
    available = {
        point["point_id"]
        for chapter in response["chapters"]
        for point in chapter.get("response_points") or []
    }
    referenced = {
        point_id
        for node in _walk(writing["chapters"])
        for point_id in node["writing_requirements"]["response_point_ids"]
    }

    assert referenced <= available
    assert set(writing["coverage"]["mapped_response_point_ids"]) == available


def test_context_loads_by_chapter_id_without_task_or_draft_unit():
    response, writing, _, _ = _sources()
    context = load_technical_agent_context(response, writing, target_chapter_id="WC-003")

    assert context["target_outline"]["chapter_id"] == "WC-003"
    assert context["target_selector"]["task_id_required"] is False
    assert context["target_selector"]["draft_unit_id_required"] is False
    assert context["generation_constraints"]["internal_draft_unit_optimization"]["required_for_context_loading"] is False


def test_wc003_contains_only_relevant_delivery_implementation_acceptance_and_team_requirements():
    response, writing, _, _ = _sources()
    context = load_technical_agent_context(response, writing, target_chapter_id="WC-003")
    response_ids = {item["response_point_id"] for item in context["tender_requirements"]}
    titles = [child["title"] for child in context["target_outline"]["children"]]
    serialized = json.dumps(context, ensure_ascii=False)

    assert response_ids == {"TR-005", "TR-006", "TR-007", "TR-008", "TR-009", "TR-010", "TR-022"}
    assert {"实施总体规划", "项目组织架构", "项目负责人及主要人员", "安装部署", "项目验收"} <= set(titles)
    assert "TR-018" not in serialized
    assert "table_excerpt" not in serialized
    assert "C86-4G" not in serialized


@pytest.mark.parametrize(
    ("section_id", "entity"),
    [
        ("WC-002-10", "超融合系统"),
        ("WC-002-11", "超融合互联交换机"),
        ("WC-002-12", "超融合节点"),
    ],
)
def test_product_sections_load_only_their_relevant_parameter_excerpt(section_id, entity):
    response, writing, _, _ = _sources()
    context = load_technical_agent_context(response, writing, target_section_id=section_id)
    parameter_point = next(
        item for item in context["tender_requirements"] if item["response_point_id"] == "TR-018"
    )

    assert parameter_point["table_excerpt"]["entity"] == entity
    assert parameter_point["table_excerpt"]["row_count"] > 0
    assert context["generation_constraints"]["parameter_consistency_required"] is True


def test_draft_units_remain_internal_and_loader_does_not_depend_on_their_file(tmp_path):
    response, writing, _, _ = _sources()
    original_units = _load(DRAFT_UNITS_PATH)

    assert len(original_units["draft_units"]) == 25
    context = load_technical_agent_context(response, writing, target_chapter_id="WC-003")
    assert context["generation_constraints"]["internal_draft_unit_optimization"]["loaded"] is False


def test_missing_optional_confirmed_facts_path_is_allowed_and_only_context_files_are_created(tmp_path):
    missing = tmp_path / "not-created-project-facts.json"
    result = build_technical_agent_context_files(
        RESPONSE_PATH,
        WRITING_PATH,
        target_chapter_id="WC-003",
        confirmed_project_facts_path=missing,
        output_key="hospital_hci_001",
        output_dir=tmp_path,
    )

    assert result["context"]["confirmed_project_facts"]["available"] is False
    assert not missing.exists()
    generated = sorted(path.name for path in (tmp_path / "hospital_hci_001" / "agent_context").iterdir())
    assert generated == ["WC-003.json", "WC-003.md"]
    assert all(value is False for value in result["context"]["execution_guard"].values())


def test_section_context_is_stable_and_selector_validation_is_explicit():
    response, writing, _, _ = _sources()
    first = load_technical_agent_context(response, writing, target_section_id="WC-002-10")
    second = load_technical_agent_context(response, writing, target_section_id="WC-002-10")

    assert first == second
    with pytest.raises(ValueError, match="exactly one"):
        load_technical_agent_context(response, writing)
    with pytest.raises(ValueError, match="exactly one"):
        load_technical_agent_context(
            response,
            writing,
            target_chapter_id="WC-003",
            target_section_id="WC-003-01",
        )


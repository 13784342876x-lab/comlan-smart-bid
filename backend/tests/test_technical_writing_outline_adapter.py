import hashlib
import json
from pathlib import Path

from app.company_writing_outline import (
    load_company_outline,
    normalize_company_outline,
    validate_company_outline,
)
from app.technical_writing_outline_adapter import (
    adapt_technical_writing_outline,
    build_technical_writing_outline_files,
)
from app.tender_writing_format_analyzer import analyze_tender_writing_format


BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPANY_PATH = BACKEND_DIR / "resources" / "writing" / "company_master_outline_v0.json"
RESPONSE_PATH = (
    BACKEND_DIR
    / "outputs"
    / "hospital_hci_001"
    / "outline"
    / "technical_response_outline.json"
)


def _sources():
    return load_company_outline(COMPANY_PATH), json.loads(RESPONSE_PATH.read_text(encoding="utf-8"))


def _adapted():
    company, response = _sources()
    return adapt_technical_writing_outline(company, response)


def _nodes(chapters):
    for chapter in chapters:
        yield chapter
        yield from _nodes(chapter.get("children") or [])


def _module(outline, module_id):
    return next(
        chapter
        for chapter in outline["chapters"]
        if module_id in chapter["company_outline_node_ids"]
    )


def _point_mappings(outline, point_id):
    return [
        (node["title"], mapping["mapping_role"])
        for node in _nodes(outline["chapters"])
        for mapping in node["response_point_mappings"]
        if mapping["response_point_id"] == point_id
    ]


def test_company_outline_loads_and_validates_actual_schema():
    company = load_company_outline(COMPANY_PATH)
    result = validate_company_outline(company)

    assert company["schema_version"] == "company_technical_writing_outline_v0"
    assert result == {
        "valid": True,
        "errors": [],
        "warnings": ["generated 87 stable node_id values from full paths"],
        "chapter_count": 13,
        "node_count": 100,
        "max_depth": 2,
    }


def test_normalization_generates_stable_ids_without_changing_titles():
    source = {"chapters": [{"title": "第一章", "children": [{"title": "第一节"}]}]}
    first = normalize_company_outline(source)
    second = normalize_company_outline(source)

    assert first == second
    assert first["chapters"][0]["node_id"].startswith("CON-")
    assert first["chapters"][0]["children"][0]["title"] == "第一节"
    assert "node_id" not in source["chapters"][0]


def test_validation_reports_duplicate_ids_empty_titles_and_orphans():
    duplicate = validate_company_outline(
        {"nodes": [{"node_id": "N-1", "title": "甲"}, {"node_id": "N-1", "title": "乙"}]}
    )
    empty = validate_company_outline({"nodes": [{"node_id": "N-1", "title": ""}]})
    orphan = validate_company_outline(
        {"nodes": [{"node_id": "N-2", "title": "乙", "parent_id": "MISSING"}]}
    )

    assert "duplicate node_id: N-1" in duplicate["errors"]
    assert "empty title: N-1" in empty["errors"]
    assert "orphan parent_id: MISSING" in orphan["errors"]


def test_flat_parent_and_level_sources_recover_a_tree():
    parent_based = normalize_company_outline(
        {
            "nodes": [
                {"node_id": "ROOT", "title": "根", "level": 1},
                {"node_id": "CHILD", "parent_id": "ROOT", "title": "子"},
            ]
        }
    )
    level_based = normalize_company_outline(
        {"nodes": [{"title": "根", "level": 1}, {"title": "子", "level": 2}]}
    )

    assert parent_based["chapters"][0]["children"][0]["node_id"] == "CHILD"
    assert level_based["chapters"][0]["children"][0]["title"] == "子"


def test_hospital_format_is_forms_only_with_unlocked_order():
    _, response = _sources()
    analysis = analyze_tender_writing_format(response)

    assert analysis["format_mode"] == "forms_only"
    assert analysis["order_locked"] is False
    assert any(form["title"] == "技术要求点对点应答表" for form in analysis["required_forms"])
    assert "GENERIC_FORMAT_REFERENCE_NOT_TREATED_AS_FULL_OUTLINE" in analysis["reasoning_codes"]


def test_company_outline_remains_body_skeleton_and_form_is_inserted():
    outline, report, _ = _adapted()
    traced_module_ids = {
        item["module_id"] for item in outline["module_adaptations"]
    }
    titles = [node["title"] for node in _nodes(outline["chapters"])]

    assert len(outline["chapters"]) == 8
    assert len(traced_module_ids) == 13
    assert "技术要求点对点应答表" in titles
    assert any(item["title"] == "技术要求点对点应答表" for item in report["form_only_nodes"])


def test_score_items_drive_renamed_and_specialized_sections():
    outline, report, _ = _adapted()
    titles = [node["title"] for node in _nodes(outline["chapters"])]

    assert "方案完整性及系统架构设计" in titles
    assert "实施部署方案" in titles
    assert "绿色供应链管理方案" in titles
    assert any(item["title"] == "培训方案评价" for item in outline["score_items"])


def test_service_delivery_and_acceptance_requirements_map_to_expected_modules():
    outline, _, _ = _adapted()

    assert any(
        any(term in title for term in ("售后服务", "服务", "保修", "维修"))
        for title, _ in _point_mappings(outline, "TR-001")
    )
    assert any("实施周期" in title for title, _ in _point_mappings(outline, "TR-006"))
    assert any("实施周期" in title for title, _ in _point_mappings(outline, "TR-007"))
    assert any("验收" in title for title, _ in _point_mappings(outline, "TR-010"))


def test_three_procurement_targets_become_detailed_solution_children():
    outline, report, _ = _adapted()
    detailed = _module(outline, "MODULE-DETAILED-SOLUTION")
    child_titles = {child["title"] for child in detailed["children"]}

    assert {
        "超融合系统详细技术方案",
        "超融合互联交换机技术方案",
        "超融合节点技术方案",
    } <= child_titles


def test_every_response_point_and_every_star_point_is_covered():
    outline, report, _ = _adapted()
    coverage = outline["coverage"]

    assert coverage["all_response_point_ids"] == coverage["mapped_response_point_ids"]
    assert coverage["unmapped_response_point_ids"] == []
    assert coverage["mandatory_unmapped_ids"] == []
    assert coverage["response_point_coverage_rate"] == 1.0
    assert coverage["mandatory_coverage_rate"] == 1.0
    assert report["unmapped_response_points"] == []


def test_procurement_table_has_primary_and_table_response_mappings():
    outline, _, _ = _adapted()
    roles = {role for _, role in _point_mappings(outline, "TR-018")}

    assert {"primary", "table_response"} <= roles


def test_adapter_does_not_generate_facts_parameters_body_or_external_plans():
    outline, _, _ = _adapted()
    serialized = json.dumps(outline, ensure_ascii=False)

    assert outline["content_generation"] == {
        "body_generated": False,
        "facts_populated": False,
        "external_research_used": False,
        "enterprise_knowledge_used": False,
    }
    assert all("body" not in node and "content" not in node for node in _nodes(outline["chapters"]))
    assert "C86-4G" not in serialized
    assert "knowledge_query" not in serialized
    assert ".docx" not in " ".join(outline.keys()).lower()


def test_markdown_and_four_output_files_are_reviewable_and_source_is_unchanged(tmp_path):
    before = hashlib.sha256(COMPANY_PATH.read_bytes()).hexdigest()
    result = build_technical_writing_outline_files(
        COMPANY_PATH,
        RESPONSE_PATH,
        output_key="hospital_test",
        output_dir=tmp_path,
    )
    after = hashlib.sha256(COMPANY_PATH.read_bytes()).hexdigest()
    markdown = Path(result["files"]["technical_writing_outline_markdown"]).read_text(encoding="utf-8")

    assert before == after
    assert len(result["files"]) == 5
    assert all(Path(path).is_file() for path in result["files"].values())
    for label in ("来源类型", "公司大纲对应位置", "关联评分项", "关联响应点", "适配方式", "保留、改名或新增原因"):
        assert label in markdown
    assert "投标正文" in markdown and "技术要求点对点应答表" in markdown


def test_adaptation_is_stable_for_identical_inputs():
    company, response = _sources()
    first = adapt_technical_writing_outline(company, response)
    second = adapt_technical_writing_outline(company, response)

    assert first == second

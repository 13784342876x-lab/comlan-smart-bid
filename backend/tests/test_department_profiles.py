from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.department_profiles import (
    STANDARD_DEPARTMENTS,
    build_project_requirement_profile,
    load_capability_domains,
    load_department_profiles,
    match_project_to_departments,
    validate_department_profiles,
)
from app.personnel_taxonomy import build_personnel_profile
from app.response_outline_builder import build_response_outline
from cli import cli
from tests.test_personnel_taxonomy import cloud_resume_record, digital_resume_record
from tests.test_response_outline_builder import read_output, sample_metadata, sample_project


FORBIDDEN_TERMS = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go"]


def assert_no_decision_terms(value) -> None:
    text = json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in text


def write_profiles(path: Path, profiles: list[dict]) -> Path:
    path.write_text(json.dumps({"departments": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def base_profiles() -> list[dict]:
    return [
        {
            "department": department,
            "description": "",
            "business_scope": [],
            "products_services": [],
            "suitable_project_types": [],
            "keywords": [],
            "typical_roles": [],
            "certificates": [],
            "not_suitable_for": [],
            "notes": ["待用户补充部门画像"],
        }
        for department in STANDARD_DEPARTMENTS
    ]


def profile_for(profiles: list[dict], department: str) -> dict:
    return next(item for item in profiles if item["department"] == department)


def test_loads_seven_standard_department_profiles() -> None:
    profiles = load_department_profiles()

    assert len(profiles) == 7
    assert {item["department"] for item in profiles} == set(STANDARD_DEPARTMENTS)


def test_all_department_profiles_are_non_empty() -> None:
    profiles = load_department_profiles()

    for item in profiles:
        assert item["description"]
        assert item["business_scope"]
        assert item["products_services"]
        assert item["suitable_project_types"]
        assert item["keywords"]
        assert "source: company_website" in item["notes"]
        assert "source: company_intro_ppt" in item["notes"]


def test_non_standard_department_validation_fails() -> None:
    profiles = base_profiles()
    profiles[0]["department"] = "不存在部门"

    try:
        validate_department_profiles(profiles)
    except ValueError as exc:
        assert "non-standard department" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_match_project_to_departments_prefers_suitable_project_types() -> None:
    profiles = base_profiles()
    profile_for(profiles, "云服务部")["suitable_project_types"] = ["区块链工业数据平台"]
    profile_for(profiles, "数据管理事业部")["keywords"] = ["工业数据"]
    requirement = build_project_requirement_profile("本项目为区块链工业数据平台建设。", [], "")

    result = match_project_to_departments(requirement, profiles, capability_domains=[])

    assert result["match_source"] == "department_profile"
    assert result["primary_departments"] == ["云服务部"]
    assert "suitable_project_types" in result["used_profile_fields"]
    assert any(
        evidence["matched_field"] == "suitable_project_types"
        for item in result["candidate_departments"]
        for evidence in item.get("matched_evidence", [])
    )


def test_keywords_are_auxiliary_to_profile_fields() -> None:
    profiles = base_profiles()
    profile_for(profiles, "数据管理事业部")["keywords"] = ["数据安全合规项目"]
    profile_for(profiles, "安全事业部")["products_services"] = ["数据安全合规项目"]
    requirement = build_project_requirement_profile("本项目属于数据安全合规项目。", [], "")

    result = match_project_to_departments(requirement, profiles, capability_domains=[])

    assert result["primary_departments"] == ["安全事业部"]
    assert "products_services" in result["used_profile_fields"]


def test_fallback_keywords_only_used_when_profiles_and_capability_domains_have_no_match() -> None:
    requirement = build_project_requirement_profile("本项目涉及区块链、工业数据、数据存证和智能合约。", [], "")

    result = match_project_to_departments(requirement, base_profiles(), capability_domains=[])

    assert result["match_source"] == "fallback_keywords"
    assert "数据管理事业部" in result["primary_departments"]
    assert result["requires_manual_confirm"] is True


def test_capability_domains_expand_preferred_departments() -> None:
    domains = [
        {
            "domain": "AI与大模型",
            "keywords": ["大模型", "RAG", "模型部署"],
            "preferred_departments": ["数据管理事业部", "数字化办公事业部", "云服务部", "安全事业部"],
            "routing_note": "按AI场景拆分到数据、办公、云和安全。",
        }
    ]
    requirement = build_project_requirement_profile("企业知识库 RAG 大模型模型部署项目。", [], "")

    result = match_project_to_departments(requirement, base_profiles(), capability_domains=domains)

    assert result["match_source"] == "capability_domain"
    for department in ["数据管理事业部", "数字化办公事业部", "云服务部", "安全事业部"]:
        assert department in result["primary_departments"]
    assert result["routing_notes"] == ["按AI场景拆分到数据、办公、云和安全。"]


def test_real_ai_capability_domain_routes_to_collaborative_departments() -> None:
    domains = load_capability_domains()
    requirement = build_project_requirement_profile("大模型、RAG、知识库、模型部署、办公助手与AI权限合规项目。", [], "")

    result = match_project_to_departments(requirement, base_profiles(), capability_domains=domains)

    for department in ["数据管理事业部", "数字化办公事业部", "云服务部", "安全事业部"]:
        assert department in result["primary_departments"]
    assert result["capability_domain_matches"]


def test_real_infra_capability_domain_routes_to_infra_departments() -> None:
    domains = load_capability_domains()
    requirement = build_project_requirement_profile("IT基础架构 Infra 项目，包含云平台、网络、服务器存储、运维和安全体系。", [], "")

    result = match_project_to_departments(requirement, base_profiles(), capability_domains=domains)

    for department in ["云服务部", "网络事业部", "硬件维保事业部", "服务运营管理部"]:
        assert department not in result["primary_departments"]
        assert department not in result["secondary_departments"]
        assert department in result["weak_candidate_departments"]
    assert result["capability_domain_matches"]
    assert result["requires_manual_confirm"] is True


def test_current_blockchain_industrial_data_project_prioritizes_data_management() -> None:
    requirement = build_project_requirement_profile(
        "基于区块链的工业数据管理技术研究，包含数据存证、数据监管、隐私保护、访问控制、智能合约、安全性证明和性能分析。",
        [],
        "",
    )

    result = match_project_to_departments(requirement, load_department_profiles(), load_capability_domains())

    assert result["primary_departments"] == ["数据管理事业部"]
    assert result["secondary_departments"] in ([], ["安全事业部"])
    assert len(result["secondary_departments"]) <= 1
    for department in ["云服务部", "网络事业部", "硬件维保事业部", "服务运营管理部"]:
        assert department not in result["primary_departments"]
        assert department not in result["secondary_departments"]
    assert result["match_source"] == "department_profile"
    assert "keywords" in result["used_profile_fields"]


def test_generic_infra_terms_do_not_create_strong_collaboration() -> None:
    requirement = build_project_requirement_profile(
        "基于区块链的电力系统数据管理国际标准研究，能力域包含基础架构/Infra、服务器、存储、网络、硬件、运维、驻场。",
        [],
        "",
    )

    result = match_project_to_departments(requirement, load_department_profiles(), load_capability_domains())

    assert result["primary_departments"] == ["数据管理事业部"]
    assert result["weak_candidate_count"] >= 1
    for department in ["云服务部", "网络事业部", "硬件维保事业部", "服务运营管理部"]:
        assert department not in result["secondary_departments"]


def test_cli_match_project_departments_outputs_candidates() -> None:
    result = CliRunner().invoke(cli, ["match-project-departments", "--lot-name", "0425139003001-6582"])

    assert result.exit_code == 0
    assert "primary_departments" in result.output
    assert "candidate_departments" in result.output
    assert "routing_notes" in result.output


def test_personnel_taxonomy_does_not_use_department_profile_business_keywords_for_person_department() -> None:
    record = {
        "id": "file_person",
        "original_name": "张三.docx",
        "category": "人员简历库",
        "tags": ["简历"],
        "material_profile": {"material_group": "personnel"},
        "resume_parse": {
            "person_name": "张三",
            "work_years": 5,
            "skills": [{"name": "区块链"}, {"name": "工业数据"}, {"name": "智能合约"}],
            "projects": [{"name": "区块链项目", "description": "工业数据存证和智能合约研发"}],
        },
    }

    profile = build_personnel_profile(record)

    assert profile["department"] == ""
    assert profile["department_confirm_status"] == "unknown"


def test_response_outline_shows_department_profile_match_result(tmp_path: Path) -> None:
    metadata = sample_metadata()
    metadata["files"].append(cloud_resume_record())
    metadata["files"].append(digital_resume_record())
    project = sample_project()
    for item in project["material_analysis"]["task_material_map"]:
        if item["task_id"] in {"task_owner", "task_team"}:
            item["matched_materials"] = [
                {
                    "material_type": "person_resume",
                    "fact_type": "resume_parse",
                    "file_id": "file_cloud",
                    "source": "resume_parse",
                    "title": "云服务部-冯振书20230322.docx",
                    "snippet": "姓名：冯振书；技能：VMware、Citrix。",
                    "confidence": 0.9,
                }
            ]

    result = build_response_outline(project, metadata=metadata, output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")

    assert "本项目建议检索部门" in text
    assert "主责部门" in text
    assert "强协同部门" in text
    assert "弱候选部门数量" in text
    assert "简要原因" in text
    assert "缺少 " in text
    assert "已确认人员简历" in text
    assert_no_decision_terms(text)

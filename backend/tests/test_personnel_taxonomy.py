from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.personnel_taxonomy import (
    build_personnel_profile,
    classify_all_personnel,
    infer_required_departments_from_tender,
    list_personnel,
    set_person_department,
)
from app.response_outline_builder import build_response_outline
from cli import cli
from tests.test_response_outline_builder import debug_sections, read_output, sample_metadata, sample_project


FORBIDDEN_TERMS = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go"]


def assert_no_decision_terms(value) -> None:
    text = json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in text


def cloud_resume_record() -> dict:
    return {
        "id": "file_cloud",
        "original_name": "云服务部-冯振书20230322.docx",
        "category": "人员简历库",
        "tags": ["简历", "云服务部", "证书"],
        "material_profile": {"material_group": "personnel"},
        "resume_parse": {
            "person_name": "冯振书",
            "work_years": 6,
            "source_folder": "工程师简历&证书/云服务部/2023-简历",
            "skills": [{"name": "VMware"}, {"name": "Citrix"}],
            "certificates": [{"display_name": "VCP-DCV"}, {"display_name": "ACP"}],
            "projects": [{"name": "虚拟化运维", "description": "VMware 虚拟化环境长期运维，巡检，故障处理"}],
        },
    }


def digital_resume_record() -> dict:
    return {
        "id": "file_digital",
        "original_name": "数字化办公-吴云畅.docx",
        "category": "人员简历库",
        "tags": ["简历", "数字化办公"],
        "material_profile": {"material_group": "personnel"},
        "resume_parse": {
            "person_name": "吴云畅",
            "work_years": 5,
            "skills": [{"name": "Python"}, {"name": "PowerPlatform"}, {"name": "M365"}],
            "projects": [{"name": "合同审批流程", "description": "PowerApps PowerAutomate SharePoint 流程自动化"}],
        },
    }


def write_metadata(path: Path, files: list[dict]) -> Path:
    path.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_cloud_department_inferred_from_filename() -> None:
    profile = build_personnel_profile(cloud_resume_record())

    assert profile["department"] == "云服务部"
    assert profile["department_confirm_status"] == "rule_inferred"
    assert "VMware" in profile["skill_tags"]


def test_digital_department_inferred_from_filename() -> None:
    profile = build_personnel_profile(digital_resume_record())

    assert profile["department"] == "数字化办公事业部"
    assert profile["department_confirm_status"] == "rule_inferred"
    assert "PowerPlatform" in profile["skill_tags"]


def test_set_person_department_writes_manual_confirmed(tmp_path: Path) -> None:
    metadata_path = write_metadata(tmp_path / "metadata.json", [cloud_resume_record()])

    result = set_person_department("file_cloud", "云服务部", path=metadata_path)
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))["files"][0]["personnel_profile"]

    assert result["personnel_profile"]["department_confirm_status"] == "manual_confirmed"
    assert saved["department"] == "云服务部"


def test_classify_all_personnel_does_not_overwrite_manual_confirmed(tmp_path: Path) -> None:
    record = cloud_resume_record()
    record["personnel_profile"] = build_personnel_profile(record)
    record["personnel_profile"]["department"] = "安全事业部"
    record["personnel_profile"]["department_confirm_status"] = "manual_confirmed"
    metadata_path = write_metadata(tmp_path / "metadata.json", [record])

    result = classify_all_personnel(path=metadata_path)
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))["files"][0]["personnel_profile"]

    assert result["skipped_manual_confirmed"] == 1
    assert saved["department"] == "安全事业部"


def test_blockchain_industrial_data_project_requires_data_management() -> None:
    result = infer_required_departments_from_tender(
        "基于区块链的工业数据管理技术研究，包含数据存证、智能合约、数据监管。",
        [],
        "",
    )

    assert "数据管理事业部" in result["primary_departments"]


def test_data_security_privacy_access_control_requires_security_department() -> None:
    result = infer_required_departments_from_tender(
        "项目涉及数据安全、隐私保护、访问控制、安全分析和安全性证明。",
        [],
        "",
    )

    assert "安全事业部" in result["primary_departments"] or "安全事业部" in result["secondary_departments"]


def test_cli_list_personnel_filters_by_department(tmp_path: Path, monkeypatch) -> None:
    metadata_path = write_metadata(tmp_path / "metadata.json", [cloud_resume_record(), digital_resume_record()])
    classify_all_personnel(path=metadata_path)
    monkeypatch.setattr("cli.list_personnel_profiles", lambda department=None: list_personnel(department=department, path=metadata_path))

    result = CliRunner().invoke(cli, ["list-personnel", "--department", "云服务部"])

    assert result.exit_code == 0
    assert "云服务" in result.output


def test_response_outline_personnel_sections_show_required_departments(tmp_path: Path) -> None:
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
                    "snippet": "姓名：冯振书；技能：VMware、Citrix；项目经验：虚拟化运维。",
                    "confidence": 0.9,
                },
                {
                    "material_type": "person_resume",
                    "fact_type": "resume_parse",
                    "file_id": "file_digital",
                    "source": "resume_parse",
                    "title": "数字化办公-吴云畅.docx",
                    "snippet": "姓名：吴云畅；技能：Python、PowerPlatform、M365。",
                    "confidence": 0.9,
                },
            ]

    result = build_response_outline(project, metadata=metadata, output_dir=tmp_path)
    text = read_output(result, "technical_response_outline")
    owner = next(section for section in debug_sections(result) if section["task_id"] == "task_owner")
    candidates = owner["person_candidates"]

    assert "本项目建议检索部门" in text
    assert "数据管理事业部" in text
    assert "安全事业部" in text
    assert all(candidate["relevance_to_project"] != "matched" for candidate in candidates)
    assert any(candidate["department_match_status"] == "cross_department_candidate" for candidate in candidates)
    assert "缺少 数据管理事业部 已确认人员简历" in json.dumps(owner["missing_materials"], ensure_ascii=False)
    assert "缺少 安全事业部 已确认人员简历" in json.dumps(owner["missing_materials"], ensure_ascii=False)
    assert_no_decision_terms(text)


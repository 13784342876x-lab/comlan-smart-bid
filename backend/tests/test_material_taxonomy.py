from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from typer.testing import CliRunner

from app.material_taxonomy import (
    build_material_profile,
    classify_all_files,
    list_material_profiles,
)
from app.file_classifier import classify_file as classify_legacy_file
from app.response_outline_builder import build_response_outline
from cli import cli
from tests.test_response_outline_builder import sample_metadata, sample_project


FORBIDDEN_TERMS = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go"]


def write_metadata(path: Path, files: list[dict]) -> Path:
    path.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def assert_no_decision_terms(value) -> None:
    text = json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in text


def qualification_record() -> dict:
    return {
        "id": "file_2308aa547dd9",
        "original_name": "资质+代理证书.docx",
        "category": "待分类",
        "parsed_text": "ISO 9001 ISO 14001 ISO 20000 ISO 27001 ISO 27701 ITSS 二级 CS3 信创数智技术服务一级能力 CCRC 信息安全风险评估三级 代理资质证明",
    }


def resume_record() -> dict:
    return {
        "id": "file_resume",
        "original_name": "数字化办公-吴云畅.docx",
        "category": "人员简历库",
        "parsed_text": "姓名：吴云畅；技能：Python；项目经验：数字化办公系统。",
        "resume_parse": {"name": "吴云畅", "project_experience": ["数字化办公系统"]},
    }


def test_qualification_file_classified_as_company_qualification() -> None:
    profile = build_material_profile(qualification_record())

    assert profile["material_group"] == "company_qualification"
    assert {"iso_certificate", "itss_certificate", "cs_certificate", "ccrc_certificate", "xinchuang_certificate", "agency_authorization"}.issubset(
        set(profile["material_types"])
    )
    assert {"company_performance", "patent", "supplier_performance"}.issubset(set(profile["not_usable_for"]))


def test_resume_file_classified_as_personnel() -> None:
    profile = build_material_profile(resume_record())

    assert profile["material_group"] == "personnel"
    assert {"person_resume", "person_project_experience"}.issubset(set(profile["material_types"]))
    assert "company_performance" in profile["not_usable_for"]


def test_technical_spec_and_scoring_files_are_tender_source() -> None:
    spec = build_material_profile(
        {
            "id": "file_spec",
            "original_name": "技术规范书(基于区块链的工业数据管理技术研究).docx",
            "category": "技术规范书",
            "parsed_text": "1.2 招标要求 技术规范 服务内容 验收要求",
        }
    )
    score = build_material_profile(
        {
            "id": "file_score",
            "original_name": "评标办法-服务.doc",
            "category": "评分表",
            "parsed_text": "评标办法 技术评分 商务评分 报价评分",
        }
    )

    assert spec["material_group"] == "tender_source"
    assert "technical_specification" in spec["material_types"]
    assert score["material_group"] == "tender_source"
    assert "scoring_method" in score["material_types"]


def test_full_tender_document_is_tender_source_not_enterprise_material() -> None:
    record = {
        "id": "file_77415c21e6d3",
        "original_name": "招标文件-服务.docx",
        "category": "招标文件",
        "tags": ["full_tender_document"],
        "file_ext": "docx",
        "parsed_text": "招标文件 投标人须知前附表 响应文件组成 签字盖章 否决 无效响应 资格条件 服务期限 采购需求一览表 分标分包表 项目名称：工业数据治理服务采购项目 合同条款",
    }

    profile = build_material_profile(record)
    classification = classify_legacy_file(record).model_dump()

    assert profile["material_group"] == "tender_source"
    assert "full_tender_document" in profile["material_types"]
    assert {"company_qualification", "company_performance", "patent", "supplier_performance"}.issubset(set(profile["not_usable_for"]))
    assert classification["category"] == "招标文件"
    assert classification["material_group"] == "tender_source"
    assert "full_tender_document" in classification["material_types"]


def test_non_substitution_rules_are_encoded() -> None:
    personnel = build_material_profile(resume_record())
    qualification = build_material_profile(qualification_record())
    winning = build_material_profile({"id": "file_win", "original_name": "中标通知书.pdf", "category": "中标", "parsed_text": "中标通知书"})

    assert "company_performance" in personnel["not_usable_for"]
    assert "patent" in qualification["not_usable_for"]
    assert "supplier_performance" in qualification["not_usable_for"]
    assert winning["material_group"] == "winning_case"
    assert "complete_company_performance_without_contract_acceptance" in winning["not_usable_for"]


def test_list_kb_materials_can_filter_by_group(tmp_path: Path) -> None:
    metadata_path = write_metadata(tmp_path / "metadata.json", [qualification_record(), resume_record()])
    classify_all_files(metadata_path)

    rows = list_material_profiles(group="company_qualification", path=metadata_path)

    assert len(rows) == 1
    assert rows[0]["file_id"] == "file_2308aa547dd9"


def test_cli_list_kb_materials_can_filter_by_group(tmp_path: Path, monkeypatch) -> None:
    metadata_path = write_metadata(tmp_path / "metadata.json", [qualification_record(), resume_record()])
    classify_all_files(metadata_path)
    monkeypatch.setattr(
        "cli.list_material_profiles",
        lambda group=None, material_type=None, category=None: list_material_profiles(
            group=group,
            material_type=material_type,
            category=category,
            path=metadata_path,
        ),
    )

    result = CliRunner().invoke(cli, ["list-kb-materials", "--group", "company_qualification"])

    assert result.exit_code == 0
    assert "company" in result.output


def test_classify_all_does_not_overwrite_manual_override(tmp_path: Path) -> None:
    manual = qualification_record()
    manual["material_profile"] = {
        "material_group": "company_product",
        "material_group_name": "公司产品材料",
        "material_types": ["product_manual"],
        "usable_for": ["work_foundation"],
        "not_usable_for": [],
        "confidence": 1.0,
        "classification_source": "manual_override",
        "requires_manual_confirm": False,
        "notes": ["manual"],
    }
    metadata_path = write_metadata(tmp_path / "metadata.json", [manual])

    result = classify_all_files(metadata_path)
    updated = json.loads(metadata_path.read_text(encoding="utf-8"))["files"][0]["material_profile"]

    assert result["skipped_manual_override"] == 1
    assert updated["material_group"] == "company_product"


def test_response_outline_builder_prioritizes_material_profile(tmp_path: Path) -> None:
    metadata = sample_metadata()
    metadata["files"].append(
        {
            "id": "file_person_001",
            "original_name": "张三简历.docx",
            "category": "人员简历库",
            "parsed_text": "姓名：张三；项目经验：工业数据安全平台研发。",
            "resume_parse": {"name": "张三", "project_experience": ["工业数据安全平台研发"]},
            "material_profile": build_material_profile(resume_record()),
        }
    )
    project = sample_project()

    result = build_response_outline(project, metadata=metadata, output_dir=tmp_path)
    debug = json.loads(Path(next(item["path"] for item in result["files"] if item["kind"] == "outline_debug")).read_text(encoding="utf-8"))
    performance = next(section for section in debug["sections"] if section["task_id"] == "task_perf")

    assert performance["evidence_counts"]["confirmed"] == 0
    assert performance["missing_materials"]
    assert "company_contract" in json.dumps(performance["missing_materials"], ensure_ascii=False)
    assert_no_decision_terms(result)
    assert_no_decision_terms(debug)

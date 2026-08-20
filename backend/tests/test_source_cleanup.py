from __future__ import annotations

import json
from pathlib import Path

from app.material_matcher import filter_material_analysis_to_existing_files
from app.response_outline_builder import build_response_outline
from app.source_cleanup import (
    MOCK_FILE_ID_DENYLIST,
    inspect_case_readiness,
    is_full_tender_document_candidate,
    purge_mock_materials,
    scan_residual_references,
)


OLD_1 = "file_a5eee523007a"
OLD_2 = "file_a0a571b442b0"
REAL_QUAL = "file_2308aa547dd9"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_metadata(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    data_dir = tmp_path / "data"
    storage_dir = tmp_path / "storage"
    outputs_dir = tmp_path / "outputs"
    original_dir = storage_dir / "original"
    parsed_dir = storage_dir / "parsed"
    original_dir.mkdir(parents=True)
    parsed_dir.mkdir(parents=True)
    old_1_path = original_dir / f"资质_代理证书_{OLD_1}.docx"
    old_1_path.write_text("mock", encoding="utf-8")
    (parsed_dir / f"{OLD_1}.txt").write_text("mock parsed", encoding="utf-8")
    real_path = original_dir / f"资质_代理证书_{REAL_QUAL}.docx"
    real_path.write_text("real", encoding="utf-8")
    metadata_path = data_dir / "metadata.json"
    write_json(
        metadata_path,
        {
            "files": [
                {
                    "id": OLD_1,
                    "original_name": "资质+代理证书.docx",
                    "file_path": str(old_1_path),
                    "parsed_text": "包含营业执照、ISO9001质量管理体系认证、ISO27001信息安全管理体系认证、AAA信用等级、专利、软件著作权、检测报告、合同等资料",
                    "structured_facts": {"file_id": OLD_1, "requirements": [{"file_id": OLD_1}]},
                },
                {
                    "id": OLD_2,
                    "original_name": "资质+代理证书.docx",
                    "file_path": str(original_dir / f"missing_{OLD_2}.docx"),
                    "parsed_text": "泛化摘要",
                    "structured_facts": {"file_id": OLD_2},
                },
                {
                    "id": REAL_QUAL,
                    "original_name": "资质+代理证书.docx",
                    "file_path": str(real_path),
                    "parsed_text": "ITSS信息技术服务标准符合性证书 证书编号 1TSS-YW-2-110020240062 有效期至2027年07月04日",
                },
                {
                    "id": "file_acb8d90cde1f",
                    "original_name": "招标文件附件-服务.doc",
                    "parsed_text": "技术标准详见技术规范书",
                },
            ],
            "company_qualifications": {
                OLD_1: {"requirements": [{"source_file_id": OLD_1, "name": "mock"}]},
                OLD_2: {"requirements": [{"source_file_id": OLD_2, "name": "mock"}]},
                REAL_QUAL: {"requirements": [{"source_file_id": REAL_QUAL, "name": "ITSS"}]},
            },
            "structured_facts": [
                {"file_id": OLD_1, "name": "mock fact"},
                {"source_file_id": OLD_2, "name": "mock fact"},
                {"file_id": REAL_QUAL, "name": "real fact"},
            ],
        },
    )
    write_json(
        data_dir / "tender_projects.json",
        {
            "projects": [
                {
                    "lot_name": "0425139003001-6582",
                    "material_analysis": {
                        "task_material_map": [
                            {
                                "task_id": "task_1",
                                "matched_materials": [
                                    {"file_id": OLD_1, "title": "mock"},
                                    {"file_id": OLD_2, "title": "mock"},
                                    {"file_id": REAL_QUAL, "title": "real"},
                                ],
                            }
                        ],
                        "matched_materials": [{"file_id": OLD_1}, {"file_id": REAL_QUAL}],
                    },
                    "response_outline": {"debug": [{"file_id": OLD_2}]},
                }
            ]
        },
    )
    output_file = outputs_dir / "0425139003001-6582" / "outline" / "technical_outline.md"
    output_file.parent.mkdir(parents=True)
    output_file.write_text(f"old {OLD_1} {OLD_2}", encoding="utf-8")
    return metadata_path, data_dir, outputs_dir, storage_dir


def test_purge_mock_materials_removes_denylist_and_keeps_real_file(tmp_path: Path) -> None:
    metadata_path, data_dir, outputs_dir, storage_dir = cleanup_metadata(tmp_path)

    report = purge_mock_materials(
        "0425139003001-6582",
        metadata_path=metadata_path,
        data_dir=data_dir,
        outputs_dir=outputs_dir,
        storage_dir=storage_dir,
    )

    metadata = read_json(metadata_path)
    file_ids = {item["id"] for item in metadata["files"]}
    assert OLD_1 not in file_ids
    assert OLD_2 not in file_ids
    assert REAL_QUAL in file_ids
    assert OLD_1 in report["metadata_removed_file_ids"]
    assert OLD_2 in report["metadata_removed_file_ids"]
    assert not (storage_dir / "original" / f"资质_代理证书_{OLD_1}.docx").exists()
    assert (storage_dir / "original" / f"资质_代理证书_{REAL_QUAL}.docx").exists()
    assert report["readiness"]["blocked"] is True


def test_purge_removes_structured_facts_and_material_analysis_refs(tmp_path: Path) -> None:
    metadata_path, data_dir, outputs_dir, storage_dir = cleanup_metadata(tmp_path)
    purge_mock_materials(
        "0425139003001-6582",
        metadata_path=metadata_path,
        data_dir=data_dir,
        outputs_dir=outputs_dir,
        storage_dir=storage_dir,
    )

    metadata_text = metadata_path.read_text(encoding="utf-8")
    project_text = (data_dir / "tender_projects.json").read_text(encoding="utf-8")
    assert OLD_1 not in metadata_text
    assert OLD_2 not in metadata_text
    assert OLD_1 not in project_text
    assert OLD_2 not in project_text
    assert REAL_QUAL in project_text


def test_material_match_main_result_filters_orphaned_file_ids() -> None:
    metadata = {"files": [{"id": REAL_QUAL, "original_name": "资质+代理证书.docx"}]}
    material_analysis = {
        "task_material_map": [
            {
                "task_id": "task",
                "matched_materials": [
                    {"file_id": OLD_1, "material_type": "patent", "snippet": "mock"},
                    {"file_id": REAL_QUAL, "material_type": "company_qualification", "snippet": "real"},
                ],
                "missing_materials": [],
            }
        ]
    }
    cleaned = filter_material_analysis_to_existing_files(material_analysis, metadata)
    matched_text = json.dumps(cleaned["matched_materials"], ensure_ascii=False)
    assert OLD_1 not in matched_text
    assert REAL_QUAL in matched_text
    assert cleaned["orphaned_file_ids_ignored"] == [OLD_1]


def test_response_outline_markdown_filters_orphaned_file_ids(tmp_path: Path) -> None:
    metadata = {
        "files": [
            {
                "id": "file_tender",
                "original_name": "完整招标文件.docx",
                "parsed_text": "投标人须知前附表 响应文件组成 签字盖章 无效响应 服务期限 技术服务要求 项目负责人 服务方案",
            },
            {
                "id": REAL_QUAL,
                "original_name": "资质+代理证书.docx",
                "parsed_text": "ITSS信息技术服务标准符合性证书 证书编号 1TSS-YW-2-110020240062 有效期至2027年07月04日",
            },
        ]
    }
    project = {
        "lot_name": "lot-clean",
        "project_name": "清理测试项目",
        "tender_file_id": "file_tender",
        "score_file_id": "file_score",
        "generation_tasks": [{"task_id": "task_tech", "task_type": "technical", "task_name": "工作基础", "related_score_item_ids": ["score_1"]}],
        "score_items": [{"id": "score_1", "item_name": "工作基础", "score_rule": "根据工作基础评分"}],
        "material_analysis": {
            "task_material_map": [
                {
                    "task_id": "task_tech",
                    "matched_materials": [
                        {"file_id": OLD_1, "material_type": "patent", "fact_type": "patent_document", "title": "mock", "snippet": "mock", "confidence": 0.9},
                        {"file_id": REAL_QUAL, "material_type": "company_qualification", "fact_type": "qualification_certificate", "title": "real", "snippet": "ITSS", "confidence": 0.9},
                    ],
                    "missing_materials": [],
                }
            ]
        },
    }
    result = build_response_outline(project, metadata=metadata, output_dir=tmp_path / "outputs")
    markdown = Path(next(item["path"] for item in result["files"] if item["kind"] == "technical_response_outline")).read_text(encoding="utf-8")
    assert OLD_1 not in markdown


def test_tender_attachment_is_not_full_tender_document() -> None:
    assert not is_full_tender_document_candidate(
        {
            "id": "file_acb8d90cde1f",
            "original_name": "招标文件附件-服务.doc",
            "parsed_text": "招标文件附件 技术标准详见技术规范书 服务内容",
        }
    )


def test_strong_tender_terms_are_full_tender_candidate() -> None:
    assert is_full_tender_document_candidate(
        {
            "id": "file_full",
            "original_name": "采购文件.docx",
            "parsed_text": "投标人须知前附表 响应文件组成 签字盖章 无效响应 资格条件 服务期限",
        }
    )


def test_readiness_blocked_without_full_tender() -> None:
    metadata = {
        "files": [
            {"id": "file_acb8d90cde1f", "original_name": "招标文件附件-服务.doc", "parsed_text": "技术标准详见技术规范书"},
            {"id": "file_bd310be2d205", "original_name": "技术规范书(基于区块链的工业数据管理技术研究).docx", "parsed_text": "技术规范书"},
            {"id": "file_829b97fcf9b4", "original_name": "评标办法-服务.doc", "parsed_text": "评标办法"},
        ]
    }
    readiness = inspect_case_readiness("0425139003001-6582", metadata=metadata)
    assert readiness["blocked"] is True
    assert readiness["allow_metadata_extraction"] is False
    assert "未检测到完整招标文件正文" in readiness["blocking_reasons"]


def test_readiness_uses_tender_source_guard_for_source_types() -> None:
    metadata = {
        "files": [
            {
                "id": "file_77415c21e6d3",
                "original_name": "招标文件-服务.docx",
                "category": "招标文件",
                "tags": ["full_tender_document"],
                "parsed_text": "投标人须知前附表 响应文件组成 签字盖章 无效响应 资格条件 服务期限 采购需求一览表 分标分包表",
            },
            {
                "id": "file_014de4764c31",
                "original_name": "招标公告.docx",
                "category": "招标公告",
                "tags": ["tender_notice"],
                "parsed_text": "国网河北电力2025年第三次服务类公开招标采购\n招标公告\n招标编号：042513",
            },
            {
                "id": "file_8acfa3eee894",
                "original_name": "招标公告附件.doc",
                "category": "招标公告附件",
                "tags": ["tender_notice_attachment"],
                "parsed_text": "需求一览表042513.xlsx",
            },
            {
                "id": "file_5092c306fce3",
                "original_name": "embedded_attachment_3_2.xlsx",
                "category": "招标需求一览表",
                "tags": ["tender_demand_list"],
                "parsed_text": "0425139003001-6582 综合服务-科技检修技术标准 包01",
                "tables": [],
            },
            {
                "id": "file_other_demand",
                "original_name": "其他需求一览表.xlsx",
                "category": "招标需求一览表",
                "tags": ["tender_demand_list"],
                "parsed_text": "0425139001001-8101 其他包",
                "tables": [],
            },
        ]
    }

    readiness = inspect_case_readiness("0425139003001-6582", metadata=metadata)

    assert readiness["full_tender_document"]["file_ids"] == ["file_77415c21e6d3"]
    assert "file_014de4764c31" not in readiness["full_tender_document"]["file_ids"]
    assert "file_8acfa3eee894" not in readiness["full_tender_document"]["file_ids"]
    assert readiness["tender_notice"]["file_ids"] == ["file_014de4764c31"]
    assert readiness["tender_notice_attachment"]["file_ids"] == ["file_8acfa3eee894"]
    assert readiness["procurement_package_table"]["file_ids"] == ["file_5092c306fce3"]
    assert readiness["allow_metadata_extraction"] is True
    assert readiness["allow_formal_outline"] is True
    assert readiness["blocked"] is False


def test_missing_physical_file_does_not_stop_purge(tmp_path: Path) -> None:
    metadata_path = tmp_path / "data" / "metadata.json"
    write_json(metadata_path, {"files": [{"id": OLD_2, "original_name": "资质+代理证书.docx", "file_path": str(tmp_path / "storage" / f"{OLD_2}.docx")}]})
    report = purge_mock_materials(
        "0425139003001-6582",
        metadata_path=metadata_path,
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        storage_dir=tmp_path / "storage",
    )
    assert OLD_2 in report["metadata_removed_file_ids"]
    assert read_json(metadata_path)["files"] == []


def test_residual_scan_finds_output_reference(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "case" / "old.md"
    output.parent.mkdir(parents=True)
    output.write_text(f"still references {OLD_1}", encoding="utf-8")
    result = scan_residual_references({OLD_1}, [tmp_path / "outputs"])
    assert result["residual_references_found"] is True
    assert result["business_residual_references_found"] is True
    assert result["business_residual_reference_locations"][0]["matched_file_id"] == OLD_1

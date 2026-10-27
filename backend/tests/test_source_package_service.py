from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.source_package_service import (
    ProjectPackageProfileBuilder,
    SourcePackageService,
    SourceReferenceResolver,
    apply_project_profile_to_score_parse,
)


class FakeMetadata:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def update_file(self, file_id: str, patch: dict) -> dict:
        self.records[file_id] = {**self.records[file_id], **patch}
        return self.records[file_id]


class FakeService:
    def __init__(self) -> None:
        self.metadata = FakeMetadata()
        self.count = 0

    def ingest_file(self, path: Path, category: str, tags: list[str]) -> dict:
        self.count += 1
        file_id = f"file_{self.count}"
        record = {"id": file_id, "original_name": path.name, "storage_key": str(path)}
        self.metadata.records[file_id] = record
        return record

    def parse_file(self, file_id: str) -> dict:
        return {"ok": True}


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return stream.getvalue()


def test_nested_chinese_zip_preserves_lineage_and_duplicate_folding(tmp_path: Path) -> None:
    inner = zip_bytes({"附件1：项目采购需求清单.xlsx": b"xlsx", "重复.txt": b"same"})
    outer = tmp_path / "采购文件包.zip"
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("05_完整采购文件.zip", inner)
        archive.writestr("重复副本.txt", b"same")
        archive.writestr("忽略.exe", b"no")
    package = SourcePackageService(output_root=tmp_path / "out").discover(outer)
    files = package["files"]
    demand = next(item for item in files if "项目采购需求清单" in item["original_name"])

    assert demand["archive_depth"] == 2
    assert demand["parent_archive_id"]
    assert "05_完整采购文件.zip!/" in demand["archive_internal_path"]
    assert any(item["duplicate_of"] for item in files)
    assert any(item["parsed_status"] == "not_supported" for item in files)
    assert package["summary"]["nested_archives"] == 2

    ingested = SourcePackageService(output_root=tmp_path / "out").ingest_and_parse(package, FakeService())
    assert ingested["summary"]["parsed_files"] == 2


def test_archive_safety_records_zip_slip_corruption_and_size_limit(tmp_path: Path) -> None:
    slip = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip, "w") as archive:
        archive.writestr("../escape.txt", b"bad")
    result = SourcePackageService(output_root=tmp_path / "out").discover(slip)
    assert result["failures"]
    assert "zip_slip_path" in result["failures"][0]["reason"]

    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not zip")
    broken_result = SourcePackageService(output_root=tmp_path / "out").discover(broken)
    assert broken_result["failures"]

    large = tmp_path / "large.zip"
    with zipfile.ZipFile(large, "w") as archive:
        archive.writestr("big.txt", b"x" * 20)
    capped = SourcePackageService(output_root=tmp_path / "out", max_single_file_bytes=10).discover(large)
    assert "max_single_file_size_exceeded" in capped["failures"][0]["reason"]


def test_reference_resolution_profile_and_score_auto_applicability() -> None:
    main = {
        "id": "main",
        "original_name": "采购文件.docx",
        "parsed_text": "项目具体技术评分标准选用见附件1：项目采购需求清单。采购编号：ZX-2026BC305-FF1\n项目编号：ZX-2026BC305-FF105",
    }
    demand = {
        "id": "demand",
        "original_name": "附件1：项目采购需求清单-智芯2026.xlsx",
        "parsed_text": "项目名称：2026年图形图像编辑软件和便携式文档编辑软件订阅服务\n包号：5\n服务类技术评分\n技术：商务：价格=60:10:30\n区间平均价浮动法",
    }
    resolution = SourceReferenceResolver().resolve([main, demand])
    assert resolution["references"][0]["resolved_source_id"] == "demand"
    profile = ProjectPackageProfileBuilder().build([main, demand], resolution)
    assert profile["package_number"] == "5"
    assert profile["technical_score_template"] == "服务类技术评分"
    assert (profile["technical_weight"], profile["business_weight"], profile["price_weight"]) == (60, 10, 30)

    machine = {
        "schema_version": "score_table_parse_v2",
        "status": "needs_review",
        "parsed": True,
        "technical_template_unresolved": True,
        "component_weights_unresolved": True,
        "unsupported_score_format": False,
        "technical_score_table_candidates": [
            {"candidate_id": "technical_table_2", "heading": "服务类技术评分", "score_items": [{"id": "t"}]},
            {"candidate_id": "technical_table_3", "heading": "产品加工服务技术评分", "score_items": []},
        ],
        "business_score_items": [],
        "price_score_rule": {"id": "price"},
        "price_scoring_method": {"method_name": "区间平均价浮动法"},
    }
    effective = apply_project_profile_to_score_parse(machine, profile)
    assert effective["selected_technical_candidate_id"] == "technical_table_2"
    assert effective["weights"] == {"business": 10, "technical": 60, "price": 30}
    assert effective["review_gate"] is None


def test_missing_attachment_reference_is_reported() -> None:
    result = SourceReferenceResolver().resolve(
        [{"id": "main", "original_name": "采购文件.docx", "parsed_text": "详见附件1：项目采购需求清单"}]
    )
    assert result["status"] == "needs_review"
    assert result["missing_references"][0]["status"] == "referenced_source_missing"


def test_profile_selects_one_project_demand_row_from_technical_spec_name() -> None:
    headers = ["采购编号", "采购项目名称（包名称）", "项目编号", "包号", "服务期/交货期", "打分权重（技术：商务：价格）", "技术评分细则模板", "价格评分办法"]
    rows = [
        {"cells": [{"value": value} for value in headers]},
        {"cells": [{"value": value} for value in ["ZX-1", "其他项目", "ZX-101", "1", "12个月", "50:10:40", "科技技术服务技术评分", "区间平均价浮动法"]]},
        {"cells": [{"value": value} for value in ["ZX-2026BC305-FF1", "2026年图形图像编辑软件和便携式文档编辑软件订阅服务", "ZX-2026BC305-FF105", "5", "自合同签订之日起3年", "60:10:30", "服务类技术评分", "区间平均价浮动法"]]},
    ]
    profile = ProjectPackageProfileBuilder().build(
        [
            {"id": "spec", "file_ext": "docx", "document_role": "technical_spec", "original_name": "技术规范书-图形图像编辑软件和便携式文档编辑软件订阅服务.docx", "parsed_text": ""},
            {"id": "demand", "file_ext": "xlsx", "original_name": "附件1：项目采购需求清单.xlsx", "parsed_text": "", "tables": [{"rows": rows}]},
        ],
        {"references": []},
    )
    assert profile["project_number"] == "ZX-2026BC305-FF105"
    assert profile["package_number"] == "5"
    assert profile["technical_score_template"] == "服务类技术评分"
    assert (profile["technical_weight"], profile["business_weight"], profile["price_weight"]) == (60, 10, 30)

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from app.technical_fulltext_composer import sha256_file, stable_json_sha256
from app.technical_fulltext_hygiene import (
    CLEANED_SCHEMA_VERSION,
    HYGIENE_SCHEMA_VERSION,
    TechnicalFulltextHygiene,
    TechnicalFulltextHygieneError,
    inspect_technical_fulltext_hygiene,
)
from cli import cli


def _fulltext(content: str) -> dict[str, Any]:
    return {
        "schema_version": "technical_fulltext_v2",
        "document_id": "technical-fulltext:hygiene-fixture",
        "output_key": "hygiene-fixture",
        "generated_at": "2026-08-11T00:00:00+00:00",
        "composition_status": "completed_with_blockers",
        "preview_only": True,
        "release_ready": False,
        "chapter_order": ["WC-001"],
        "chapters": [{
            "chapter_id": "WC-001",
            "chapter_title": "测试章节",
            "order": 1,
            "sections": [{
                "section_id": "WC-001-01",
                "heading": "测试小节",
                "block_type": "technical_solution",
                "content": content,
                "covered_requirement_ids": ["TR-001"],
                "covered_score_item_ids": ["SCORE-001"],
                "placeholder_ids": ["PH-FIELD-OFFERED-PRODUCT-BRAND"],
            }],
            "claims": [],
            "placeholders_used": ["PH-FIELD-OFFERED-PRODUCT-BRAND"],
            "requirements_covered": ["TR-001"],
            "score_items_covered": ["SCORE-001"],
            "source": {
                "chapter_status": "release_blocked",
                "validation_status": "blocked",
                "submission_ready": False,
            },
        }],
        "coverage_summary": {},
        "composition_issues": [],
        "production": {},
        "standard_outline": {},
        "source_manifest": {},
    }


def _clean(content: str) -> tuple[str, dict[str, Any]]:
    service = TechnicalFulltextHygiene()
    cleaned, findings, repairs, unresolved, validation = service.clean_document(
        _fulltext(content)
    )
    return cleaned["chapters"][0]["sections"][0]["content"], {
        "findings": findings,
        "repairs": repairs,
        "unresolved": unresolved,
        "validation": validation,
        "cleaned": cleaned,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _committed_case(tmp_path: Path, content: str) -> dict[str, Path]:
    fulltext_dir = tmp_path / "outputs" / "hygiene-fixture" / "fulltext"
    fulltext_path = _write_json(
        fulltext_dir / "technical_fulltext.json", _fulltext(content)
    )
    fulltext_sha = sha256_file(fulltext_path)
    preview_path = fulltext_dir / "technical_fulltext.preview.md"
    preview_path.write_text("STAGE-10-PREVIEW-SENTINEL\n", encoding="utf-8")
    manifest_path = _write_json(
        fulltext_dir / "technical_fulltext_build_manifest.json",
        {
            "schema_version": "technical_fulltext_build_manifest_v2",
            "document_id": "technical-fulltext:hygiene-fixture",
            "output_key": "hygiene-fixture",
            "chapters": [],
        },
    )
    production_path = _write_json(
        fulltext_dir / "chapter_production_index.json",
        {
            "schema_version": "technical_fulltext_chapter_production_index_v1",
            "output_key": "hygiene-fixture",
            "chapters": [],
        },
    )
    commit_path = _write_json(
        fulltext_dir / "technical_fulltext.commit.json",
        {
            "schema_version": "technical_fulltext_commit_v1",
            "status": "committed",
            "output_key": "hygiene-fixture",
            "document_id": "technical-fulltext:hygiene-fixture",
            "technical_fulltext_json_path": str(fulltext_path.resolve()),
            "technical_fulltext_json_sha256": fulltext_sha,
            "technical_fulltext_preview_path": str(preview_path.resolve()),
            "technical_fulltext_preview_sha256": sha256_file(preview_path),
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": sha256_file(manifest_path),
            "production_index_path": str(production_path.resolve()),
            "production_index_sha256": sha256_file(production_path),
        },
    )
    audit_path = _write_json(
        fulltext_dir / "audit" / "technical_fulltext.audit.json",
        {
            "schema_version": "technical_fulltext_audit_v1",
            "document_id": "technical-fulltext:hygiene-fixture",
            "output_key": "hygiene-fixture",
            "source_fulltext": {
                "path": str(fulltext_path.resolve()),
                "sha256": fulltext_sha,
                "schema_version": "technical_fulltext_v2",
            },
            "status": "completed",
            "audit_result": "blocked",
            "audit_passes": {
                "deterministic": {"status": "completed"},
                "semantic": {"status": "completed"},
                "finalization": {"status": "completed"},
            },
            "finalization": {"status": "completed"},
            "release_ready": False,
        },
    )
    return {
        "fulltext": fulltext_path,
        "commit": commit_path,
        "audit": audit_path,
        "preview": preview_path,
        "manifest": manifest_path,
        "production": production_path,
    }


def test_jinja_controls_are_removed_but_inner_text_is_preserved() -> None:
    cleaned, result = _clean(
        "安全设计中：{% if applicable %}支持存储加密能力{% endif %}。"
    )
    assert cleaned == "安全设计中：支持存储加密能力。"
    assert [item["repair_type"] for item in result["repairs"]] == [
        "template_control_residue", "template_control_residue"
    ]
    assert not result["unresolved"]


def test_adjacent_exact_phrase_is_removed() -> None:
    cleaned, result = _clean("医院作为医院作为持续运行的机构。")
    assert cleaned == "医院作为持续运行的机构。"
    assert len(result["repairs"]) == 1
    assert result["repairs"][0]["repair_type"] == "adjacent_exact_phrase_duplication"


def test_adjacent_generated_segment_is_removed_without_losing_tail() -> None:
    text = (
        "其次，建立与医疗业务增长相匹配的弹性供给机制，使资源按需扩展，支撑未来"
        "其次，建立与医疗业务增长相匹配的弹性供给机制，使资源按需扩展，支撑未来"
        "医院信息化发展的预期需求。医院信息化发展的预期需求。"
    )
    cleaned, result = _clean(text)
    assert cleaned == (
        "其次，建立与医疗业务增长相匹配的弹性供给机制，使资源按需扩展，支撑未来"
        "医院信息化发展的预期需求。"
    )
    assert any(
        item["repair_type"] == "adjacent_exact_segment_duplication"
        for item in result["repairs"]
    )


def test_normal_repeated_terms_across_paragraphs_are_not_removed() -> None:
    text = "采购人建设超融合系统并组织验收。\n\n采购人负责超融合系统运行监督。"
    cleaned, result = _clean(text)
    assert cleaned == text
    assert result["repairs"] == []


def test_legitimate_parallel_emphasis_is_not_removed() -> None:
    text = "项目执行中应逐项检查、逐项确认、逐项记录。"
    cleaned, result = _clean(text)
    assert cleaned == text
    assert result["repairs"] == []


def test_known_malformed_fragment_is_repaired_deterministically() -> None:
    text = "通过测试验证其集成能力，以k），实现统一告警。"
    cleaned, result = _clean(text)
    assert cleaned == "通过测试验证其集成能力，以实现统一告警。"
    assert result["repairs"][0]["repair_type"] == "malformed_generation_residue"
    assert result["repairs"][0]["method"] == "deterministic"
    assert result["repairs"][0]["model_used"] is None


def test_numeric_time_and_sla_tokens_are_preserved() -> None:
    text = "医院作为医院作为持续运行机构，提供7×24小时响应、24小时到场、30日交货、10日安装。"
    cleaned, result = _clean(text)
    for value in ("7×24小时", "24小时", "30日", "10日"):
        assert cleaned.count(value) == text.count(value)
    assert result["validation"]["protected_value_tokens_unchanged"] is True


def test_placeholder_is_preserved() -> None:
    token = "{{PH-FIELD-OFFERED-PRODUCT-BRAND}}"
    text = f"医院作为医院作为持续运行机构，产品为{token}。"
    cleaned, result = _clean(text)
    assert cleaned.count(token) == 1
    assert result["validation"]["placeholder_tokens_unchanged"] is True


def test_ambiguous_malformed_fragment_is_unresolved_not_rewritten() -> None:
    text = "该技术路径通过a）完成接口适配，但原意无法确定。"
    cleaned, result = _clean(text)
    assert cleaned == text
    assert result["repairs"] == []
    assert len(result["unresolved"]) == 1
    assert result["unresolved"][0]["gate_blocking"] is True


def test_mojibake_marker_is_unresolved_not_rewritten() -> None:
    text = "管理平台提供统一告警�并记录审计日志。"
    cleaned, result = _clean(text)
    assert cleaned == text
    assert result["repairs"] == []
    assert result["unresolved"][0]["finding_type"] == "mojibake_residue"


def test_cleaned_document_is_idempotent() -> None:
    first, first_result = _clean("医院作为医院作为持续运行的机构。")
    second, second_result = _clean(first)
    assert second == first
    assert second_result["repairs"] == []
    assert stable_json_sha256(second_result["cleaned"]) == stable_json_sha256(
        first_result["cleaned"]
    )


def test_adjacent_duplicate_paragraph_is_removed() -> None:
    paragraph = "这是一个足够长且完全重复的机械生成段落，需要只保留一份内容。"
    cleaned, result = _clean(f"{paragraph}\n\n{paragraph}")
    assert cleaned == paragraph
    assert result["repairs"][0]["repair_type"] == "adjacent_duplicate_paragraph"


def test_dry_run_writes_nothing_and_preserves_sources(tmp_path: Path) -> None:
    paths = _committed_case(tmp_path, "医院作为医院作为持续运行的机构。")
    before = {name: sha256_file(path) for name, path in paths.items()}
    result = TechnicalFulltextHygiene().run(
        fulltext_json_path=paths["fulltext"], dry_run=True
    )
    after = {name: sha256_file(path) for name, path in paths.items()}
    assert result["summary"]["repair_count"] == 1
    assert result["outputs"] == {}
    assert before == after
    assert not (paths["fulltext"].parent / "hygiene").exists()


def test_real_run_writes_independent_valid_artifacts(tmp_path: Path) -> None:
    paths = _committed_case(
        tmp_path,
        "{% if applicable %}支持存储加密能力{% endif %}。医院作为医院作为持续运行。",
    )
    before = {name: sha256_file(path) for name, path in paths.items()}
    result = TechnicalFulltextHygiene().run(fulltext_json_path=paths["fulltext"])
    after = {name: sha256_file(path) for name, path in paths.items()}
    assert before == after
    assert result["hygiene_status"] == "completed"
    assert result["human_review_hygiene_ready"] is True
    assert result["release_ready"] is False
    report_path = Path(result["outputs"]["technical_fulltext_hygiene_json"])
    cleaned_path = Path(result["outputs"]["technical_fulltext_cleaned_json"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cleaned = json.loads(cleaned_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == HYGIENE_SCHEMA_VERSION
    assert cleaned["schema_version"] == CLEANED_SCHEMA_VERSION
    assert inspect_technical_fulltext_hygiene(report_path)[
        "human_review_hygiene_ready"
    ] is True


def test_unresolved_residue_closes_human_review_gate(tmp_path: Path) -> None:
    paths = _committed_case(tmp_path, "该技术路径通过a）完成接口适配。")
    result = TechnicalFulltextHygiene().run(fulltext_json_path=paths["fulltext"])
    assert result["hygiene_status"] == "completed_with_unresolved"
    assert result["human_review_hygiene_ready"] is False
    assert result["summary"]["model_call_count"] == 0


def test_hygiene_does_not_require_stage11_finalization(tmp_path: Path) -> None:
    paths = _committed_case(tmp_path, "正常正文。")
    paths["audit"].unlink()
    result = TechnicalFulltextHygiene().run(fulltext_json_path=paths["fulltext"])
    assert result["hygiene_status"] == "completed"


@pytest.mark.parametrize("mutation", ["fulltext", "commit_identity", "manifest"])
def test_hygiene_rejects_invalid_stage10_source(
    tmp_path: Path, mutation: str
) -> None:
    paths = _committed_case(tmp_path, "normal content")
    if mutation == "fulltext":
        payload = json.loads(paths["fulltext"].read_text(encoding="utf-8"))
        payload["chapters"][0]["sections"][0]["content"] = "tampered"
        _write_json(paths["fulltext"], payload)
    elif mutation == "commit_identity":
        payload = json.loads(paths["commit"].read_text(encoding="utf-8"))
        payload["document_id"] = "technical-fulltext:other"
        _write_json(paths["commit"], payload)
    else:
        payload = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        payload["output_key"] = "other"
        _write_json(paths["manifest"], payload)
    with pytest.raises(TechnicalFulltextHygieneError):
        TechnicalFulltextHygiene().run(fulltext_json_path=paths["fulltext"])


def test_cli_and_inspector(tmp_path: Path) -> None:
    paths = _committed_case(tmp_path, "医院作为医院作为持续运行的机构。")
    runner = CliRunner()
    run_result = runner.invoke(cli, [
        "hygiene-technical-fulltext", "--fulltext-json", str(paths["fulltext"])
    ])
    assert run_result.exit_code == 0, run_result.output
    report_path = paths["fulltext"].parent / "hygiene" / "technical_fulltext.hygiene.json"
    inspect_result = runner.invoke(cli, [
        "inspect-technical-fulltext-hygiene", "--hygiene-json", str(report_path)
    ])
    assert inspect_result.exit_code == 0, inspect_result.output
    assert "human_review_hygiene_ready" in inspect_result.output

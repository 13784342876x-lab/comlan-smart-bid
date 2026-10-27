from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from app.technical_fulltext_auditor import (
    AUDIT_SCHEMA_VERSION,
    TechnicalFulltextAuditError,
    TechnicalFulltextAuditor,
    inspect_technical_fulltext_audit,
    normalize_audit_document,
)
from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_composer import stable_json_sha256
from app.technical_fulltext_hygiene import TechnicalFulltextHygiene
from cli import cli


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def build_committed_case(tmp_path: Path) -> dict[str, Any]:
    output_key = "stage11_fixture"
    output_root = tmp_path / "outputs" / output_key
    fulltext_dir = output_root / "fulltext"
    planning_dir = tmp_path / "outputs" / "planning_case"

    planning_payloads = {
        "writing_outline": {"schema_version": "technical_writing_outline_v0", "chapters": []},
        "response_outline": {"schema_version": "technical_response_outline_v1", "chapters": []},
        "task_information": {"schema_version": "task_information_requirements_v1", "items": []},
        "draft_units": {
            "schema_version": "draft_units_v1",
            "terminology_constraints": ["Preferred Platform"],
            "content_location_plan": {},
            "draft_units": [],
        },
    }
    planning_paths = {
        name: write_json(planning_dir / f"{name}.json", payload)
        for name, payload in planning_payloads.items()
    }

    production_index = {
        "schema_version": "technical_fulltext_chapter_production_index_v1",
        "output_key": output_key,
        "production_status": "complete_with_blockers",
        "chapter_count_expected": 2,
        "chapter_count_completed": 2,
        "chapter_count_submission_ready": 0,
        "chapter_count_blocked": 2,
        "chapter_count_failed": 0,
        "chapters": [],
    }
    index_path = write_json(
        fulltext_dir / "chapter_production_index.json", production_index
    )
    manifest_path = write_json(
        fulltext_dir / "technical_fulltext_build_manifest.json",
        {
            "schema_version": "technical_fulltext_build_manifest_v2",
            "document_id": f"technical-fulltext:{output_key}",
            "output_key": output_key,
            "chapters": [],
        },
    )
    preview_path = fulltext_dir / "technical_fulltext.preview.md"
    preview_path.write_text("STAGE-10-PREVIEW-SENTINEL\n", encoding="utf-8")

    fulltext = {
        "schema_version": "technical_fulltext_v2",
        "document_id": f"technical-fulltext:{output_key}",
        "output_key": output_key,
        "generated_at": "2026-08-07T00:00:00+00:00",
        "standard_outline": {
            "resolved_path": str(planning_paths["writing_outline"].resolve()),
            "schema_version": "technical_writing_outline_v0",
            "sha256": sha256_file(planning_paths["writing_outline"]),
        },
        "production": {
            "chapter_production_index_path": str(index_path.resolve()),
            "chapter_production_index_sha256": sha256_file(index_path),
            "production_status": "complete_with_blockers",
            "chapter_count_expected": 2,
            "chapter_count_completed": 2,
            "chapter_count_submission_ready": 0,
            "chapter_count_blocked": 2,
            "chapter_count_failed": 0,
        },
        "composition_status": "completed_with_blockers",
        "preview_only": True,
        "release_ready": False,
        "chapter_order": ["WC-001", "WC-002"],
        "chapters": [
            {
                "chapter_id": "WC-001",
                "chapter_title": "第一章",
                "order": 1,
                "sections": [
                    {
                        "section_id": "WC-001-01",
                        "heading": "背景",
                        "block_type": "introduction",
                        "content": "  Alpha line  \r\n\r\n Beta   {{PH-A}}  ",
                        "covered_requirement_ids": ["REQ-1"],
                        "covered_score_item_ids": ["SCORE-1"],
                        "placeholder_ids": ["PH-A"],
                    },
                    {
                        "section_id": "WC-001-02",
                        "heading": "方案",
                        "block_type": "technical_solution",
                        "content": "Gamma {{PH-SHARED}}",
                        "covered_requirement_ids": ["REQ-2"],
                        "covered_score_item_ids": ["SCORE-2"],
                        "placeholder_ids": ["PH-SHARED"],
                    },
                ],
                "claims": [
                    {
                        "text": "Claim one",
                        "claim_type": "tender_fact",
                        "source_ids": ["REQ-1"],
                        "evidence_ids": ["EV-1"],
                        "human_review_required": False,
                    }
                ],
                "placeholders_used": ["PH-A", "PH-SHARED", "PH-METADATA-ONLY"],
                "requirements_covered": ["REQ-1", "REQ-2"],
                "score_items_covered": ["SCORE-1", "SCORE-2"],
                "source": {
                    "chapter_status": "release_blocked",
                    "validation_status": "blocked",
                    "submission_ready": False,
                },
            },
            {
                "chapter_id": "WC-002",
                "chapter_title": "第二章",
                "order": 2,
                "sections": [
                    {
                        "section_id": "WC-002-01",
                        "heading": "实施",
                        "block_type": "implementation",
                        "content": "Delta {{PH-SHARED}}\n\nEpsilon  \t value",
                        "covered_requirement_ids": ["REQ-1"],
                        "covered_score_item_ids": ["SCORE-1"],
                        "placeholder_ids": ["PH-SHARED"],
                    }
                ],
                "claims": [
                    {
                        "text": "Claim two",
                        "claim_type": "proposal_statement",
                        "source_ids": ["REQ-2"],
                        "evidence_ids": [],
                        "human_review_required": True,
                    }
                ],
                "placeholders_used": ["PH-SHARED"],
                "requirements_covered": ["REQ-1"],
                "score_items_covered": ["SCORE-1"],
                "source": {
                    "chapter_status": "auto_repair_exhausted",
                    "validation_status": "blocked",
                    "submission_ready": False,
                },
            },
        ],
        "coverage_summary": {},
        "composition_issues": [],
        "source_manifest": {
            "path": str(manifest_path.resolve()),
            "sha256": sha256_file(manifest_path),
        },
    }
    fulltext_path = write_json(fulltext_dir / "technical_fulltext.json", fulltext)
    commit = {
        "schema_version": "technical_fulltext_commit_v1",
        "status": "committed",
        "output_key": output_key,
        "document_id": fulltext["document_id"],
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "production_index_path": str(index_path.resolve()),
        "production_index_sha256": sha256_file(index_path),
        "technical_fulltext_json_path": str(fulltext_path.resolve()),
        "technical_fulltext_json_sha256": sha256_file(fulltext_path),
        "technical_fulltext_preview_path": str(preview_path.resolve()),
        "technical_fulltext_preview_sha256": sha256_file(preview_path),
        "committed_at": "2026-08-07T00:00:00+00:00",
    }
    commit_path = write_json(
        fulltext_dir / "technical_fulltext.commit.json", commit
    )
    input_paths = {
        "writing_outline_path": str(planning_paths["writing_outline"].resolve()),
        "response_outline_path": str(planning_paths["response_outline"].resolve()),
        "task_information_path": str(planning_paths["task_information"].resolve()),
        "draft_units_path": str(planning_paths["draft_units"].resolve()),
        "confirmed_project_facts_path": None,
    }
    input_hashes = {
        "writing_outline_sha256": sha256_file(planning_paths["writing_outline"]),
        "response_outline_sha256": sha256_file(planning_paths["response_outline"]),
        "task_information_sha256": sha256_file(planning_paths["task_information"]),
        "draft_units_sha256": sha256_file(planning_paths["draft_units"]),
        "confirmed_project_facts_sha256": None,
    }
    run_path = write_json(
        fulltext_dir / "technical_fulltext.run.json",
        {
            "schema_version": "technical_fulltext_run_v1",
            "status": "completed",
            "production_status": "complete_with_blockers",
            "composition_status": "completed_with_blockers",
            "output_key": output_key,
            "input_paths": input_paths,
            "input_hashes": input_hashes,
            "chapter_count_expected": 2,
            "chapter_count_completed": 2,
            "chapter_count_submission_ready": 0,
            "chapter_count_blocked": 2,
            "chapter_count_failed": 0,
            "chapters": [],
            "outputs": {},
        },
    )
    return {
        "fulltext": fulltext,
        "fulltext_path": fulltext_path,
        "commit_path": commit_path,
        "run_path": run_path,
        "preview_path": preview_path,
        "manifest_path": manifest_path,
        "index_path": index_path,
        "planning_paths": planning_paths,
    }


def recommit_fulltext(case: dict[str, Any]) -> None:
    write_json(case["fulltext_path"], case["fulltext"])
    commit = json.loads(case["commit_path"].read_text(encoding="utf-8"))
    commit["document_id"] = case["fulltext"]["document_id"]
    commit["output_key"] = case["fulltext"]["output_key"]
    commit["technical_fulltext_json_sha256"] = sha256_file(case["fulltext_path"])
    write_json(case["commit_path"], commit)


def save_trusted_draft_units(case: dict[str, Any], payload: dict[str, Any]) -> None:
    draft_path = case["planning_paths"]["draft_units"]
    write_json(draft_path, payload)
    run = json.loads(case["run_path"].read_text(encoding="utf-8"))
    run["input_hashes"]["draft_units_sha256"] = sha256_file(draft_path)
    write_json(case["run_path"], run)


def test_cleaned_fulltext_is_authoritative_audit_source_with_lineage(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    source_text = (
        "医院作为医院作为持续运行机构，响应REQ-1与SCORE-1，"
        "产品为{{PH-A}}，提供7×24小时服务。"
    )
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = source_text
    recommit_fulltext(case)
    source_sha = sha256_file(case["fulltext_path"])
    stage10_before = {
        str(path): sha256_file(path)
        for path in (
            case["fulltext_path"], case["preview_path"], case["commit_path"],
            case["manifest_path"], case["index_path"],
        )
    }

    hygiene = TechnicalFulltextHygiene().run(
        fulltext_json_path=case["fulltext_path"]
    )
    cleaned_path = Path(hygiene["outputs"]["technical_fulltext_cleaned_json"])
    cleaned_wrapper = json.loads(cleaned_path.read_text(encoding="utf-8"))
    result = TechnicalFulltextAuditor().audit(fulltext_json_path=cleaned_path)
    audit = result["audit"]
    lineage = audit["source_lineage"]

    assert "医院作为医院作为" not in cleaned_wrapper["fulltext"]["chapters"][0]["sections"][0]["content"]
    assert lineage["committed_fulltext"]["file_sha256"] == source_sha
    assert lineage["committed_fulltext"]["file_sha256"] != lineage["audited_fulltext"]["file_sha256"]
    assert lineage["hygiene"]["cleaned_content_sha256"] == cleaned_wrapper["cleaned_content_sha256"]
    assert lineage["audited_fulltext"]["content_sha256"] == cleaned_wrapper["cleaned_content_sha256"]
    assert lineage["audited_fulltext"]["content_sha256"] == stable_json_sha256(cleaned_wrapper["fulltext"])
    normalized_text = audit["normalized_document"]["paragraphs"][0]["text"]
    assert "医院作为医院作为" not in normalized_text
    assert "医院作为持续运行机构" in normalized_text
    fca = next(
        item for item in audit["confirmation_register"]
        if item["member_placeholder_ids"] == ["PH-A"]
    )
    paragraph = audit["normalized_document"]["paragraphs"][0]
    assert fca["affected_locations"][0]["paragraph_id"] == paragraph["paragraph_id"]
    assert any(
        item["requirement_id"] == "REQ-1"
        for item in audit["normalized_document"]["requirements"]
    )
    assert any(
        item["score_item_id"] == "SCORE-1"
        for item in audit["normalized_document"]["score_items"]
    )
    assert {
        str(path): sha256_file(path)
        for path in (
            case["fulltext_path"], case["preview_path"], case["commit_path"],
            case["manifest_path"], case["index_path"],
        )
    } == stage10_before


def test_committed_fulltext_completes_deterministic_pass_and_allows_blockers(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)

    result = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )
    audit = result["audit"]

    assert audit["schema_version"] == AUDIT_SCHEMA_VERSION
    assert audit["status"] == "completed"
    assert audit["audit_result"] == "partial"
    assert audit["audit_passes"] == {
        "deterministic": {"status": "completed"},
        "semantic": {"status": "not_run"},
        "scoring": {"status": "not_run"},
    }
    assert audit["release_ready"] is False
    assert audit["issues"] == []
    assert len(audit["confirmation_register"]) == 3
    assert all(item["status"] == "pending" for item in audit["confirmation_register"])
    assert audit["issue_counts"] == {
        "fatal": 0,
        "blocking": 0,
        "warning": 0,
        "info": 0,
    }
    assert audit["upstream_status"]["chapter_count_blocked"] == 2
    assert audit["upstream_status"]["production_status"] == "complete_with_blockers"
    assert audit["upstream_status"]["composition_status"] == "completed_with_blockers"
    assert Path(result["outputs"]["technical_fulltext_audit_json"]).is_file()
    markdown = Path(
        result["outputs"]["technical_fulltext_audit_markdown"]
    ).read_text(encoding="utf-8")
    assert "确定性全文审计已完成" in markdown
    assert "Semantic Audit 尚未执行" in markdown
    assert "不表示全文无问题" in markdown
    assert "可以发布" not in markdown


def test_snapshot_sha_matches_commit_and_stage10_artifacts_remain_unchanged(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    frozen = [
        case["fulltext_path"],
        case["preview_path"],
        case["commit_path"],
        case["manifest_path"],
        case["index_path"],
    ]
    before = {path: sha256_file(path) for path in frozen}

    result = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )

    assert result["audit"]["source_fulltext"]["sha256"] == sha256_file(
        case["fulltext_path"]
    )
    assert {path: sha256_file(path) for path in frozen} == before


def test_modified_fulltext_is_runtime_failure_and_writes_no_success_audit(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    payload = json.loads(case["fulltext_path"].read_text(encoding="utf-8"))
    payload["chapters"][0]["chapter_title"] = "tampered"
    write_json(case["fulltext_path"], payload)

    with pytest.raises(TechnicalFulltextAuditError, match="sha256"):
        TechnicalFulltextAuditor().audit(fulltext_json_path=case["fulltext_path"])

    assert not (
        case["fulltext_path"].parent / "audit" / "technical_fulltext.audit.json"
    ).exists()


@pytest.mark.parametrize("field", ["output_key", "document_id"])
def test_commit_identity_conflict_is_runtime_failure(
    tmp_path: Path, field: str
) -> None:
    case = build_committed_case(tmp_path)
    commit = json.loads(case["commit_path"].read_text(encoding="utf-8"))
    commit[field] = "conflicting-identity"
    write_json(case["commit_path"], commit)

    with pytest.raises(TechnicalFulltextAuditError, match=field):
        TechnicalFulltextAuditor().audit(fulltext_json_path=case["fulltext_path"])


def test_normalized_paragraphs_are_stable_and_conservatively_normalized(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)

    first = normalize_audit_document(case["fulltext"]).to_dict()
    second = normalize_audit_document(case["fulltext"]).to_dict()

    assert first == second
    paragraphs = first["paragraphs"]
    assert len(paragraphs) == 5
    assert paragraphs[0] == {
        "paragraph_id": paragraphs[0]["paragraph_id"],
        "chapter_id": "WC-001",
        "section_id": "WC-001-01",
        "paragraph_index": 1,
        "document_order": 1,
        "text": "Alpha line",
        "normalized_text": "Alpha line",
        "text_sha256": paragraphs[0]["text_sha256"],
    }
    assert paragraphs[-1]["normalized_text"] == "Epsilon value"
    assert all(item["paragraph_id"].startswith("PAR-") for item in paragraphs)
    assert all(len(item["text_sha256"]) == 64 for item in paragraphs)


def test_placeholder_requirement_score_and_claim_indexes_preserve_locations(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    normalized = normalize_audit_document(case["fulltext"]).to_dict()

    placeholders = {
        item["placeholder_id"]: item for item in normalized["placeholders"]
    }
    assert set(placeholders) == {"PH-A", "PH-SHARED", "PH-METADATA-ONLY"}
    assert placeholders["PH-SHARED"]["occurrence_count"] == 2
    assert len(placeholders["PH-SHARED"]["locations"]) == 2
    assert placeholders["PH-SHARED"]["section_ids"] == ["WC-001-02", "WC-002-01"]
    assert placeholders["PH-METADATA-ONLY"]["section_ids"] == []

    requirements = {
        item["requirement_id"]: item for item in normalized["requirements"]
    }
    scores = {item["score_item_id"]: item for item in normalized["score_items"]}
    assert requirements["REQ-1"]["chapter_ids"] == ["WC-001", "WC-002"]
    assert requirements["REQ-1"]["occurrence_count"] == 2
    assert scores["SCORE-1"]["section_ids"] == ["WC-001-01", "WC-002-01"]
    assert scores["SCORE-1"]["occurrence_count"] == 2
    assert normalized["claims"][0]["source_ids"] == ["REQ-1"]
    assert normalized["claims"][1]["human_review_required"] is True


def test_planning_context_is_loaded_from_run_and_optional_facts_null_is_valid(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    context = audit["input_context"]

    assert context["confirmed_project_facts"] is None
    for name in ("writing_outline", "response_outline", "task_information", "draft_units"):
        assert context[name]["path"] == str(case["planning_paths"][name].resolve())
        assert context[name]["sha256"] == sha256_file(case["planning_paths"][name])
        assert context[name]["schema_version"]
        assert context[name]["sha256_matches_run"] is True
    assert context["production_index"]["path"] == str(case["index_path"].resolve())


def test_inspector_and_cli_are_read_only_over_audit_artifact(tmp_path: Path) -> None:
    case = build_committed_case(tmp_path)
    runner = CliRunner()

    audit_result = runner.invoke(
        cli,
        ["audit-technical-fulltext", "--fulltext-json", str(case["fulltext_path"])],
    )
    assert audit_result.exit_code == 0, audit_result.output
    audit_path = (
        case["fulltext_path"].parent / "audit" / "technical_fulltext.audit.json"
    )
    before = sha256_file(audit_path)

    summary = inspect_technical_fulltext_audit(audit_path)
    inspect_result = runner.invoke(
        cli,
        ["inspect-technical-fulltext-audit", "--audit-json", str(audit_path)],
    )

    assert summary["audit_result"] == "partial"
    assert summary["release_ready"] is False
    assert inspect_result.exit_code == 0, inspect_result.output
    assert sha256_file(audit_path) == before


def test_unreadable_fulltext_json_is_runtime_failure(tmp_path: Path) -> None:
    case = build_committed_case(tmp_path)
    case["fulltext_path"].write_text("{broken", encoding="utf-8")

    with pytest.raises(TechnicalFulltextAuditError, match="unavailable"):
        TechnicalFulltextAuditor().audit(fulltext_json_path=case["fulltext_path"])


def test_exact_cross_chapter_duplicate_creates_one_warning_fai_with_all_locations(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    duplicate = (
        "This is a deliberately long deterministic paragraph describing the complete "
        "deployment architecture, operational boundary, acceptance path, and delivery controls."
    )
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = duplicate
    case["fulltext"]["chapters"][1]["sections"][0]["content"] = duplicate
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]

    assert len(audit["issues"]) == 1
    issue = audit["issues"][0]
    assert issue["issue_id"].startswith("FAI-")
    assert issue["code"] == "exact_cross_chapter_duplicate"
    assert issue["severity"] == "warning"
    assert issue["release_blocking"] is False
    assert len(issue["locations"]) == 2
    assert all(
        set(location) >= {
            "chapter_id",
            "section_id",
            "paragraph_id",
            "paragraph_index",
            "excerpt",
            "text_sha256",
        }
        for location in issue["locations"]
    )
    assert audit["issue_counts"] == {
        "fatal": 0,
        "blocking": 0,
        "warning": 1,
        "info": 0,
    }


def test_duplicate_inside_one_section_does_not_create_fulltext_fai(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    duplicate = "A single-section paragraph is intentionally repeated for local quality scope. " * 2
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = (
        duplicate + "\n\n" + duplicate
    )
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]

    assert audit["issues"] == []
    assert audit["deterministic_checks"]["exact_cross_content_duplication"][
        "duplicate_group_count"
    ] == 0


def test_near_duplicate_is_semantic_candidate_and_not_issue(tmp_path: Path) -> None:
    case = build_committed_case(tmp_path)
    left = (
        "The proposed platform uses a unified resource pool with resilient scheduling, "
        "central monitoring, controlled migration, capacity planning, and storage protection."
    )
    right = left.replace("storage protection", "network protection")
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = left
    case["fulltext"]["chapters"][1]["sections"][0]["content"] = right
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]

    assert audit["issues"] == []
    assert len(audit["semantic_candidates"]["duplicate_pairs"]) == 1
    assert audit["semantic_candidates"]["duplicate_pairs"][0][
        "candidate_type"
    ] == "near_duplicate"


def test_exact_placeholder_five_occurrences_create_one_pending_fca(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    section = case["fulltext"]["chapters"][0]["sections"][0]
    section["content"] = " ".join(["{{PH-A}}"] * 5)
    section["placeholder_ids"] = ["PH-A"]
    recommit_fulltext(case)

    first = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    second = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    matches = [
        item
        for item in first["confirmation_register"]
        if item["member_placeholder_ids"] == ["PH-A"]
    ]

    assert len(matches) == 1
    assert matches[0]["status"] == "pending"
    assert matches[0]["occurrence_count"] == 5
    assert len(matches[0]["affected_locations"]) == 5
    assert matches[0]["confirmation_id"] in {
        item["confirmation_id"] for item in second["confirmation_register"]
    }
    assert "value" not in matches[0]
    assert "confirmed_value" not in matches[0]


def test_placeholder_alias_candidate_never_merges_independent_fcas(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    generic = "PH-FIELD-OFFERED-PRODUCT-BRAND"
    scoped = "PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED"
    first_section = case["fulltext"]["chapters"][0]["sections"][0]
    second_section = case["fulltext"]["chapters"][1]["sections"][0]
    first_section["content"] = f"Brand pending {{{{{generic}}}}}"
    first_section["placeholder_ids"] = [generic]
    second_section["content"] = f"Scoped brand pending {{{{{scoped}}}}}"
    second_section["placeholder_ids"] = [scoped]
    case["fulltext"]["chapters"][0]["placeholders_used"].append(generic)
    case["fulltext"]["chapters"][1]["placeholders_used"].append(scoped)
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    aliases = audit["semantic_candidates"]["confirmation_aliases"]
    registers = [
        item
        for item in audit["confirmation_register"]
        if item["member_placeholder_ids"][0] in {generic, scoped}
    ]

    assert any(item["candidate_placeholder_ids"] == sorted([generic, scoped]) for item in aliases)
    assert len(registers) == 2
    assert all(len(item["member_placeholder_ids"]) == 1 for item in registers)
    assert len({item["confirmation_id"] for item in registers}) == 2


def test_fifty_semantic_candidates_do_not_change_issue_counts(tmp_path: Path) -> None:
    case = build_committed_case(tmp_path)
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = " ".join(
        f"{value}日" for value in range(1, 51)
    )
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]

    assert len(audit["semantic_candidates"]["numeric_facts"]) == 50
    assert audit["issues"] == []
    assert audit["issue_counts"] == {
        "fatal": 0,
        "blocking": 0,
        "warning": 0,
        "info": 0,
    }


def test_requirement_outside_trusted_plan_is_candidate_not_issue(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    draft_units = json.loads(
        case["planning_paths"]["draft_units"].read_text(encoding="utf-8")
    )
    planned = {
        "task_id": "TASK-REQ-1",
        "target_path": ["第一章", "背景"],
        "mapping_role": "primary",
    }
    draft_units["content_location_plan"] = {
        "REQ-1": {
            "primary_content_location": planned,
            "supporting_reference_locations": [],
            "table_response_locations": [],
            "all_locations": [planned],
        }
    }
    save_trusted_draft_units(case, draft_units)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    candidates = audit["semantic_candidates"]["requirement_location_mismatches"]
    coverage = next(
        item for item in audit["coverage_analysis"]["requirements"]
        if item["requirement_id"] == "REQ-1"
    )

    assert len(candidates) == 1
    assert candidates[0]["requirement_id"] == "REQ-1"
    assert coverage["outside_planned_locations"] == [
        {"chapter_id": "WC-002", "section_id": "WC-002-01"}
    ]
    assert audit["issues"] == []


def test_draft_units_hash_mismatch_skips_dependent_rules_and_candidates(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    draft_path = case["planning_paths"]["draft_units"]
    draft_units = json.loads(draft_path.read_text(encoding="utf-8"))
    draft_units["content_location_plan"] = {
        "REQ-1": {
            "primary_content_location": {
                "task_id": "TASK-X",
                "target_path": ["第一章", "背景"],
                "mapping_role": "primary",
            },
            "supporting_reference_locations": [],
            "table_response_locations": [],
            "all_locations": [],
        }
    }
    write_json(draft_path, draft_units)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]

    assert audit["input_context"]["draft_units"]["sha256_matches_run"] is False
    assert audit["deterministic_checks"]["requirement_location_comparison"] == {
        "status": "skipped",
        "reason": "input_hash_mismatch",
    }
    assert audit["deterministic_checks"]["terminology_candidates"] == {
        "status": "skipped",
        "reason": "input_hash_mismatch",
    }
    assert audit["semantic_candidates"]["requirement_location_mismatches"] == []
    assert audit["semantic_candidates"]["terminology"] == []


def test_numeric_time_candidates_are_extracted_without_conflict_issue(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = (
        "交货期30日，安装期10日，支持响应时间24小时。"
    )
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    numeric = audit["semantic_candidates"]["numeric_facts"]

    assert {item["normalized_value"] for item in numeric} == {"30日", "10日", "24小时"}
    assert audit["issues"] == []


def test_fai_and_fca_ids_are_stable_across_repeated_runs(tmp_path: Path) -> None:
    case = build_committed_case(tmp_path)
    duplicate = "Stable deterministic duplicate identity must survive repeated audit runs. " * 2
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = duplicate
    case["fulltext"]["chapters"][1]["sections"][0]["content"] = duplicate
    recommit_fulltext(case)

    first = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    second = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]

    assert [item["issue_id"] for item in first["issues"]] == [
        item["issue_id"] for item in second["issues"]
    ]
    assert [item["confirmation_id"] for item in first["confirmation_register"]] == [
        item["confirmation_id"] for item in second["confirmation_register"]
    ]


def test_trusted_terminology_records_exact_occurrences_without_alias_judgement(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    case["fulltext"]["chapters"][0]["sections"][0]["content"] = (
        "Preferred Platform is used here. Preferred Platform remains the preferred term."
    )
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    terminology = audit["semantic_candidates"]["terminology"]

    assert len(terminology) == 1
    assert terminology[0]["preferred_term"] == "Preferred Platform"
    assert terminology[0]["occurrence_count"] == 2
    assert terminology[0]["suspected_variants"] == []


def test_score_item_spanning_many_chapters_is_candidate_not_issue(
    tmp_path: Path,
) -> None:
    case = build_committed_case(tmp_path)
    case["fulltext"]["chapter_order"].append("WC-003")
    case["fulltext"]["chapters"].append({
        "chapter_id": "WC-003",
        "chapter_title": "第三章",
        "order": 3,
        "sections": [{
            "section_id": "WC-003-01",
            "heading": "补充响应",
            "block_type": "supporting_response",
            "content": "A distinct supporting response belongs to the third chapter.",
            "covered_requirement_ids": [],
            "covered_score_item_ids": ["SCORE-1"],
            "placeholder_ids": [],
        }],
        "claims": [],
        "placeholders_used": [],
        "requirements_covered": [],
        "score_items_covered": ["SCORE-1"],
        "source": {
            "chapter_status": "release_blocked",
            "validation_status": "blocked",
            "submission_ready": False,
        },
    })
    recommit_fulltext(case)

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=case["fulltext_path"]
    )["audit"]
    candidates = audit["semantic_candidates"]["score_distribution"]

    assert len(candidates) == 1
    assert candidates[0]["score_item_id"] == "SCORE-1"
    assert candidates[0]["chapter_ids"] == ["WC-001", "WC-002", "WC-003"]
    assert audit["issues"] == []

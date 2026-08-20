from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from app.technical_fulltext_audit_finalizer import (
    TechnicalFulltextAuditFinalizationError,
    TechnicalFulltextAuditFinalizer,
    semantic_finding_id,
)
from app.technical_fulltext_auditor import TechnicalFulltextAuditor
from app.technical_fulltext_composer import sha256_file
from cli import cli


REAL_OUTPUT_KEY = "hospital_hci_001_fulltext_real_20260806_02"


def _real_audit_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "outputs"
        / REAL_OUTPUT_KEY
        / "fulltext"
        / "audit"
        / "technical_fulltext.audit.json"
    )


def _stable_stage_c_fixture_path() -> Path:
    current = _real_audit_path()
    backup = current.with_name("technical_fulltext.audit.pre_uat_fix.json")
    if backup.is_file():
        return backup
    if current.is_file():
        return current
    pytest.skip("frozen Stage 11-C fixture is unavailable")


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_stage_c(tmp_path: Path) -> Path:
    source = _stable_stage_c_fixture_path()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if "source_lineage" not in payload:
        source_path = Path(payload["source_fulltext"]["path"])
        payload["source_lineage"] = TechnicalFulltextAuditor._load_audit_source(
            source_path
        )["source_lineage"]
    deterministic_source = deepcopy(
        payload.get("deterministic_source_issues", payload.get("issues") or [])
    )
    for field in (
        "deterministic_source_issues",
        "semantic_finding_dispositions",
        "confirmation_relationships",
        "finalization",
    ):
        payload.pop(field, None)
    payload["audit_passes"].pop("finalization", None)
    payload["audit_result"] = "partial"
    payload["release_ready"] = False
    payload["issues"] = deterministic_source
    payload["issue_counts"] = {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
    for issue in deterministic_source:
        payload["issue_counts"][issue["severity"]] += 1
    target = tmp_path / "audit" / "technical_fulltext.audit.json"
    _write(target, payload)
    TechnicalFulltextAuditor()._validate_audit_payload(payload)
    return target


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, payload: dict[str, Any]) -> None:
    _write(path, payload)
    TechnicalFulltextAuditor()._validate_audit_payload(payload)


def _set_findings(payload: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    payload["semantic_findings"] = deepcopy(findings)
    semantic = payload["semantic_audit"]
    semantic["finding_count"] = len(findings)
    semantic["audit_unit_count"] = len(findings)
    semantic["verdict_counts"] = {
        name: sum(item["verdict"] == name for item in findings)
        for name in ("issue", "acceptable", "uncertain")
    }
    for pass_type, summary in semantic["passes"].items():
        selected = [item for item in findings if item["pass_type"] == pass_type]
        summary["finding_count"] = len(selected)
        summary["audit_unit_count"] = len(selected)
        summary["verdict_counts"] = {
            name: sum(item["verdict"] == name for item in selected)
            for name in ("issue", "acceptable", "uncertain")
        }
        decisions: dict[str, int] = {}
        for item in selected:
            decisions[item["decision"]] = decisions.get(item["decision"], 0) + 1
        summary["decision_counts"] = decisions


def _finding(payload: dict[str, Any], *, verdict: str = "issue") -> dict[str, Any]:
    source = next(
        item for item in payload["semantic_findings"] if item["verdict"] == verdict
    )
    return deepcopy(source)


def _reidentify(finding: dict[str, Any]) -> dict[str, Any]:
    finding["finding_id"] = semantic_finding_id(finding)
    return finding


def _finalize(path: Path) -> dict[str, Any]:
    return TechnicalFulltextAuditFinalizer().finalize(audit_json_path=path)


def test_only_issue_verdict_is_promoted(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    issue = _finding(payload, verdict="issue")
    acceptable = _finding(payload, verdict="acceptable")
    uncertain = _finding(payload, verdict="uncertain")
    _set_findings(payload, [issue, acceptable, uncertain])
    _save(path, payload)
    result = _finalize(path)
    assert _load(path)["source_lineage"] == payload["source_lineage"]
    assert len(result["issues"]) == 1
    dispositions = {item["verdict"]: item for item in result["semantic_finding_dispositions"]}
    assert dispositions["issue"]["disposition"] == "promoted"
    assert dispositions["issue"]["issue_id"]
    assert dispositions["acceptable"] == {
        "finding_id": acceptable["finding_id"], "verdict": "acceptable",
        "disposition": "accepted", "issue_id": None,
    }
    assert dispositions["uncertain"]["disposition"] == "human_review"
    assert dispositions["uncertain"]["issue_id"] is None


def test_runtime_severity_ignores_model_suggestion(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = next(item for item in payload["semantic_findings"] if item["decision"] == "redundant_response")
    finding["suggested_severity"] = "blocking"
    finding["suggested_release_blocking"] = True
    _set_findings(payload, [finding])
    _save(path, payload)
    issue = _finalize(path)["issues"][0]
    assert issue["severity"] == "warning"
    assert issue["release_blocking"] is False


@pytest.mark.parametrize(
    ("code", "expected_result"),
    [
        ("technical_parameter_conflict", "blocked"),
        ("coverage_content_mismatch", "blocked"),
    ],
)
def test_blocking_policy_codes(tmp_path: Path, code: str, expected_result: str) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = _finding(payload, verdict="issue")
    if code == "technical_parameter_conflict":
        finding["audit_type"] = "global_consistency"
        finding["decision"] = "commitment_conflict"
        finding["pass_type"] = "numeric_consistency"
    else:
        finding["audit_type"] = "content_ownership"
        finding["decision"] = "coverage_content_mismatch"
    finding["code"] = code
    _reidentify(finding)
    _set_findings(payload, [finding])
    _save(path, payload)
    result = _finalize(path)
    assert result["issues"][0]["severity"] == "blocking"
    assert result["issues"][0]["release_blocking"] is True
    assert result["audit_result"] == expected_result


@pytest.mark.parametrize("decision", ["misplaced_content", "redundant_response"])
def test_organization_issues_are_warning(tmp_path: Path, decision: str) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = next(item for item in payload["semantic_findings"] if item["decision"] == decision)
    _set_findings(payload, [finding])
    _save(path, payload)
    result = _finalize(path)
    assert result["issue_counts"] == {"fatal": 0, "blocking": 0, "warning": 1, "info": 0}
    assert result["audit_result"] == "reviewable"


def test_uncertain_only_is_reviewable_without_fai(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    _set_findings(payload, [_finding(payload, verdict="uncertain")])
    _save(path, payload)
    result = _finalize(path)
    assert result["issues"] == []
    assert result["audit_result"] == "reviewable"


def test_clean_ignores_pending_fca_and_stage10_blockers(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    assert payload["confirmation_register"]
    assert payload["upstream_status"]["chapter_count_blocked"] > 0
    _set_findings(payload, [])
    _save(path, payload)
    result = _finalize(path)
    assert result["issues"] == []
    assert result["issue_counts"] == {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
    assert result["audit_result"] == "clean"
    assert result["release_ready"] is False


def test_pending_fca_never_enters_issue_counts(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    result = _finalize(path)
    payload = _load(path)
    assert len(payload["confirmation_register"]) == 32
    assert all(item["status"] == "pending" for item in payload["confirmation_register"])
    assert sum(result["issue_counts"].values()) == len(result["issues"])


def test_alias_uncertain_relates_two_fca_without_merge(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    before = _load(path)["confirmation_register"]
    _finalize(path)
    after = _load(path)
    assert after["confirmation_register"] == before
    assert len(after["confirmation_relationships"]) == 4
    assert all(len(item["confirmation_ids"]) == 2 for item in after["confirmation_relationships"])
    assert all(item["relationship_type"] == "possible_alias" for item in after["confirmation_relationships"])
    assert "confirmed_value" not in path.read_text(encoding="utf-8")


def test_fai_id_is_stable_across_independent_finalizations(tmp_path: Path) -> None:
    first = _copy_stage_c(tmp_path / "one")
    second = _copy_stage_c(tmp_path / "two")
    first_ids = [item["issue_id"] for item in _finalize(first)["issues"]]
    second_ids = [item["issue_id"] for item in _finalize(second)["issues"]]
    assert first_ids == second_ids


def test_duplicate_semantic_findings_merge_and_trace_both_fsf(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    first = _finding(payload, verdict="issue")
    second = deepcopy(first)
    second["audit_unit_id"] = "FSU-DUPLICATE000001"
    _reidentify(second)
    _set_findings(payload, [first, second])
    _save(path, payload)
    result = _finalize(path)
    assert len(result["issues"]) == 1
    assert result["issues"][0]["source_finding_ids"] == sorted(
        [first["finding_id"], second["finding_id"]]
    )
    assert result["finalization"]["merge_reduction_count"] == 1


def test_same_location_different_requirement_does_not_merge(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    first = _finding(payload, verdict="issue")
    second = deepcopy(first)
    second["audit_unit_id"] = "FSU-DIFFERENTREQ001"
    second["subject"] = {
        "subject_type": "requirement",
        "canonical_key": "TR-014",
        "display_name": "Different requirement",
    }
    second["requirement_ids"] = ["TR-014"]
    _reidentify(second)
    _set_findings(payload, [first, second])
    _save(path, payload)
    assert len(_finalize(path)["issues"]) == 2


def test_deterministic_and_semantic_duplicate_merge(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = _finding(payload, verdict="issue")
    deterministic = TechnicalFulltextAuditFinalizer._promote_finding(finding)
    deterministic["source_finding_ids"] = []
    deterministic["source_candidate_ids"] = []
    deterministic["detection"] = {"method": "fixture_deterministic_rule"}
    payload["issues"] = [deterministic]
    payload["issue_counts"] = {"fatal": 0, "blocking": 0, "warning": 1, "info": 0}
    _set_findings(payload, [finding])
    _save(path, payload)
    result = _finalize(path)
    assert len(result["issues"]) == 1
    assert result["issues"][0]["source_finding_ids"] == [finding["finding_id"]]
    assert set(result["issues"][0]["detection"]["methods"]) == {
        "fixture_deterministic_rule", "semantic_finding_promotion"
    }


def test_unsupported_semantic_issue_policy_fails_atomically(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = _finding(payload, verdict="issue")
    finding["code"] = "unknown_new_code"
    _set_findings(payload, [finding])
    _save(path, payload)
    before_sha = sha256_file(path)
    with pytest.raises(TechnicalFulltextAuditFinalizationError, match="unsupported"):
        _finalize(path)
    assert sha256_file(path) == before_sha
    assert not path.with_suffix(".md").exists()


def test_incomplete_semantic_pass_rejects_finalization(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    payload["audit_passes"]["semantic"] = {"status": "not_run"}
    _save(path, payload)
    before_sha = sha256_file(path)
    with pytest.raises(TechnicalFulltextAuditFinalizationError):
        _finalize(path)
    assert sha256_file(path) == before_sha


def test_untraceable_semantic_location_fails_atomically(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = _finding(payload, verdict="issue")
    finding["locations"][0]["paragraph_id"] = "PAR-DOES-NOT-EXIST"
    _reidentify(finding)
    _set_findings(payload, [finding])
    _save(path, payload)
    before_sha = sha256_file(path)
    with pytest.raises(TechnicalFulltextAuditFinalizationError, match="not traceable"):
        _finalize(path)
    assert sha256_file(path) == before_sha


def test_finalizer_is_idempotent_without_issue_accumulation(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    first = _finalize(path)
    first_payload = _load(path)
    second = _finalize(path)
    second_payload = _load(path)
    assert [item["issue_id"] for item in first["issues"]] == [item["issue_id"] for item in second["issues"]]
    assert first["issue_counts"] == second["issue_counts"]
    assert first["audit_result"] == second["audit_result"]
    assert first_payload["semantic_finding_dispositions"] == second_payload["semantic_finding_dispositions"]


def test_stage10_hashes_remain_unchanged(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    fulltext_dir = _real_audit_path().parent.parent
    targets = [
        fulltext_dir / "technical_fulltext.json",
        fulltext_dir / "technical_fulltext.preview.md",
        fulltext_dir / "technical_fulltext.commit.json",
        fulltext_dir / "technical_fulltext_build_manifest.json",
        fulltext_dir / "chapter_production_index.json",
    ]
    before = {str(item): sha256_file(item) for item in targets}
    _finalize(path)
    assert {str(item): sha256_file(item) for item in targets} == before


def test_finalization_records_zero_model_calls_and_cli_works(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["finalize-technical-fulltext-audit", "--audit-json", str(path)],
    )
    assert result.exit_code == 0, result.output
    payload = _load(path)
    assert payload["finalization"]["model_call_count"] == 0
    assert payload["release_ready"] is False


def test_markdown_heading_location_appends_reviewable_body_excerpt(tmp_path: Path) -> None:
    path = _copy_stage_c(tmp_path)
    payload = _load(path)
    finding = next(
        item
        for item in payload["semantic_findings"]
        if item["verdict"] == "issue" and item["requirement_ids"] == ["TR-014"]
    )
    heading = next(
        item
        for item in payload["normalized_document"]["paragraphs"]
        if item["section_id"] == "WC-008-03" and item["text"].startswith("###")
    )
    finding["locations"] = [{
        "chapter_id": heading["chapter_id"],
        "section_id": heading["section_id"],
        "paragraph_id": heading["paragraph_id"],
        "paragraph_index": heading["paragraph_index"],
        "excerpt": heading["text"],
        "text_sha256": heading["text_sha256"],
    }]
    _reidentify(finding)
    _set_findings(payload, [finding])
    _save(path, payload)
    _finalize(path)
    markdown = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "[WC-008-03 制造商资质材料索引]" in markdown
    assert "本次投标所投超融合节点产品的制造商" in markdown

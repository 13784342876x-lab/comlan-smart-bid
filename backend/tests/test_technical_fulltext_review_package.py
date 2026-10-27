from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_review_package import (
    SCHEMA_VERSION,
    TechnicalFulltextReviewPackageAssembler,
    TechnicalFulltextReviewPackageError,
    inspect_technical_fulltext_review_package,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REAL_AUDIT = (
    BACKEND_ROOT
    / "outputs"
    / "hospital_hci_001_fulltext_real_20260806_02"
    / "fulltext"
    / "audit"
    / "technical_fulltext.audit.json"
)
CURRENT_AUDIT = (
    BACKEND_ROOT
    / "outputs"
    / "bid_pipeline_outputs"
    / "bid-20260827T081739Z-66d806a9"
    / "fulltext-bid-20260827T081739Z-66d806a9"
    / "fulltext"
    / "audit"
    / "technical_fulltext.audit.json"
)
EXPECTED_AUDIT_SHA = "9415c365e6856cc67346f3533fa4804b76cc79d0364ec28bbb75a7f2192ff591"
EXPECTED_SCORING_SHA = "1f82376e13fffebd366178615468562547d0570ce46ce3968e6434045e80c77d"
EXPECTED_CONTENT_SHA = "7b2850515aeaecde6d29f8da2f30971fd81d1048533a050c1168fc5f8973a022"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def assembled(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict]:
    target = tmp_path_factory.mktemp("stage12a")
    result = TechnicalFulltextReviewPackageAssembler().assemble(
        audit_json_path=REAL_AUDIT,
        output_dir=target,
    )
    payload = _load(Path(result["outputs"]["technical_fulltext_review_package_json"]))
    return result, payload


def test_stage11_final_audit_sha_binding(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    ref = payload["source_lineage"]["final_stage11_audit"]
    assert sha256_file(REAL_AUDIT) == EXPECTED_AUDIT_SHA
    assert ref["file_sha256"] == EXPECTED_AUDIT_SHA


def test_scoring_assessment_sha_binding(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    ref = payload["source_lineage"]["stage11_scoring_assessment"]
    assert ref["file_sha256"] == EXPECTED_SCORING_SHA
    assert sha256_file(ref["path"]) == EXPECTED_SCORING_SHA


def test_audited_content_identity(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    assert payload["reviewed_content_sha256"] == EXPECTED_CONTENT_SHA
    assert (
        payload["source_lineage"]["audited_response_content"]["content_sha256"]
        == EXPECTED_CONTENT_SHA
    )


def test_cross_output_key_rejection(tmp_path: Path) -> None:
    audit = _load(REAL_AUDIT)
    scoring_path = Path(audit["scoring_assessment_ref"]["path"])
    scoring = _load(scoring_path)
    scoring["output_key"] = "another_output_key"
    copied_scoring = tmp_path / "technical_fulltext.scoring_assessment.json"
    _write(copied_scoring, scoring)
    audit["scoring_assessment_ref"].update(
        path=str(copied_scoring), sha256=sha256_file(copied_scoring)
    )
    copied_audit = tmp_path / "technical_fulltext.audit.json"
    _write(copied_audit, audit)
    with pytest.raises(TechnicalFulltextReviewPackageError, match="output_key mismatch"):
        TechnicalFulltextReviewPackageAssembler().assemble(
            audit_json_path=copied_audit,
            output_dir=tmp_path / "review",
        )


def test_complete_requirement_matrix(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    ids = [item["requirement_id"] for item in payload["response_matrix"]]
    assert len(ids) == 31
    assert ids == [f"TR-{index:03d}" for index in range(1, 32)]


def test_human_readable_requirement_projection(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    assert all(item["requirement_text"].strip() for item in payload["response_matrix"])
    assert all(item["source_file"] for item in payload["response_matrix"])


def test_response_excerpt_projection(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    rows_with_response = [
        item for item in payload["response_matrix"] if item["response_locations"]
    ]
    assert rows_with_response
    assert all(item["response_excerpts"] for item in rows_with_response)
    assert all(
        item["response_excerpts"][0]["excerpt"].strip()
        for item in rows_with_response
    )


def test_historical_scoring_projection_preserves_its_15_item_70_point_fixture(
    assembled: tuple[dict, dict],
) -> None:
    _, payload = assembled
    assert len(payload["scoring_matrix"]) == 15
    assert sum(item["max_score"] for item in payload["scoring_matrix"]) == 70
    assert payload["summary"]["score_item_count"] == 15
    assert payload["summary"]["technical_score_max"] == 70


def test_current_dynamic_scoring_projection_accepts_eight_items_and_one_hundred_points(
    tmp_path: Path,
) -> None:
    if not CURRENT_AUDIT.is_file():
        pytest.skip("current dynamic scoring fixture is unavailable")

    result = TechnicalFulltextReviewPackageAssembler().assemble(
        audit_json_path=CURRENT_AUDIT,
        output_dir=tmp_path / "review",
    )
    payload = _load(Path(result["outputs"]["technical_fulltext_review_package_json"]))

    assert len(payload["scoring_matrix"]) == 8
    assert {item["score_item_id"] for item in payload["scoring_matrix"]} == {
        f"SCORE-TECH-{ordinal:03d}" for ordinal in range(1, 9)
    }
    assert payload["summary"]["score_item_count"] == 8
    assert payload["summary"]["technical_score_max"] == 100
    assert all(
        item["source_refs"][0]["source_score_item_id"].startswith("technical_")
        for item in payload["scoring_matrix"]
    )


def _historical_scoring_binding_inputs() -> tuple[dict, dict]:
    audit = _load(REAL_AUDIT)
    scoring = _load(Path(audit["scoring_assessment_ref"]["path"]))
    return audit, scoring


def test_scoring_binding_rejects_catalog_count_mismatch() -> None:
    audit, scoring = _historical_scoring_binding_inputs()
    scoring["score_catalog"]["score_item_count"] = len(scoring["score_items"]) - 1

    with pytest.raises(TechnicalFulltextReviewPackageError, match="catalog count"):
        TechnicalFulltextReviewPackageAssembler._validate_scoring_binding(
            audit=audit,
            scoring=scoring,
            scoring_matrix=deepcopy(scoring["score_items"]),
        )


def test_scoring_binding_rejects_catalog_total_mismatch() -> None:
    audit, scoring = _historical_scoring_binding_inputs()
    scoring["score_catalog"]["total_max_score"] = 69

    with pytest.raises(TechnicalFulltextReviewPackageError, match="catalog total"):
        TechnicalFulltextReviewPackageAssembler._validate_scoring_binding(
            audit=audit,
            scoring=scoring,
            scoring_matrix=deepcopy(scoring["score_items"]),
        )


def test_scoring_binding_rejects_incomplete_scoring_matrix() -> None:
    audit, scoring = _historical_scoring_binding_inputs()
    incomplete_matrix = deepcopy(scoring["score_items"][:-1])

    with pytest.raises(TechnicalFulltextReviewPackageError, match="scoring_matrix"):
        TechnicalFulltextReviewPackageAssembler._validate_scoring_binding(
            audit=audit,
            scoring=scoring,
            scoring_matrix=incomplete_matrix,
        )


def test_scoring_binding_rejects_duplicate_score_identity() -> None:
    audit, scoring = _historical_scoring_binding_inputs()
    scoring["score_items"][1]["score_item_id"] = scoring["score_items"][0]["score_item_id"]

    with pytest.raises(TechnicalFulltextReviewPackageError, match="duplicate score_item_id"):
        TechnicalFulltextReviewPackageAssembler._validate_scoring_binding(
            audit=audit,
            scoring=scoring,
            scoring_matrix=deepcopy(scoring["score_items"]),
        )


def test_pending_evidence_semantics(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    items = [
        item
        for item in payload["fact_material_queue"]
        if item["queue_type"] == "evidence_query_not_executed"
    ]
    assert len(items) == 22
    assert {item["status"] for item in items} == {"query_not_executed"}
    assert payload["summary"]["verified_material_count"] == 0


def test_planned_not_executed_is_not_material_missing(
    assembled: tuple[dict, dict],
) -> None:
    _, payload = assembled
    legend = payload["fact_material_status_legend"]
    assert "不代表材料不存在" in legend["query_not_executed"]
    evidence_items = [
        item
        for item in payload["fact_material_queue"]
        if item["source_type"] == "EVIDENCE_QUERY"
    ]
    assert not any(item["status"] == "confirmed_missing" for item in evidence_items)


def test_review_queue_includes_all_unresolved_fai(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    audit = _load(REAL_AUDIT)
    expected = {item["issue_id"] for item in audit["issues"]}
    actual = {
        item["source_id"] for item in payload["review_items"] if item["source_type"] == "FAI"
    }
    assert actual == expected


def test_uncertain_human_review_fsf_included(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    audit = _load(REAL_AUDIT)
    dispositions = {
        item["finding_id"]: item["disposition"]
        for item in audit["semantic_finding_dispositions"]
    }
    expected = {
        item["finding_id"]
        for item in audit["semantic_findings"]
        if item["verdict"] == "uncertain"
        and dispositions.get(item["finding_id"]) == "human_review"
    }
    actual = {
        item["source_id"] for item in payload["review_items"] if item["source_type"] == "FSF"
    }
    assert actual == expected
    assert len(actual) == 6


def test_pending_fca_included(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    audit = _load(REAL_AUDIT)
    expected = {
        item["confirmation_id"]
        for item in audit["confirmation_register"]
        if item["status"] == "pending"
    }
    actual = {
        item["source_id"] for item in payload["review_items"] if item["source_type"] == "FCA"
    }
    assert actual == expected
    assert len(actual) == 32


def test_semantic_accepted_excluded_from_review_queue(
    assembled: tuple[dict, dict],
) -> None:
    _, payload = assembled
    audit = _load(REAL_AUDIT)
    accepted = {
        item["finding_id"]
        for item in audit["semantic_finding_dispositions"]
        if item["disposition"] == "accepted"
    }
    queued = {item["source_id"] for item in payload["review_items"]}
    assert accepted.isdisjoint(queued)


def test_machine_severity_is_immutable_projection(
    assembled: tuple[dict, dict],
) -> None:
    _, payload = assembled
    audit = _load(REAL_AUDIT)
    expected = {item["issue_id"]: item["severity"] for item in audit["issues"]}
    actual = {
        item["source_id"]: item["machine_severity"]
        for item in payload["review_items"]
        if item["source_type"] == "FAI"
    }
    assert actual == expected


def test_no_new_finding_id_is_created(assembled: tuple[dict, dict]) -> None:
    _, payload = assembled
    audit = _load(REAL_AUDIT)
    allowed = (
        {item["issue_id"] for item in audit["issues"]}
        | {item["finding_id"] for item in audit["semantic_findings"]}
        | {item["confirmation_id"] for item in audit["confirmation_register"]}
    )
    assert {item["source_id"] for item in payload["review_items"]} <= allowed
    serialized = json.dumps(payload["machine_risk_summary"], ensure_ascii=False)
    assert "RISK-" not in serialized


def test_actionability_projection_is_explicit_and_safe(
    assembled: tuple[dict, dict],
) -> None:
    _, payload = assembled
    items = {item["source_id"]: item for item in payload["review_items"]}
    tr003 = items["FAI-8A16A33D982E3BF8"]
    assert tr003["required_fact_refs"] == ["FACT-013"]
    assert tr003["recommended_revision_locations"][0]["section_id"] == "WC-005-04"
    assert tr003["must_decide_before_stage13"] is True

    alias = items["FAI-C409F5E0B2FB13F2"]
    assert alias["handoff_mode"] == "structural_normalization"
    assert alias["required_fact_refs"] == []
    assert alias["required_confirmation_refs"] == []
    assert alias["deferred_confirmation_refs"]

    table = items["FSF-1F3D4A4E0C87072D"]
    assert table["handoff_mode"] == "revision"
    assert table["shared_dependency"]["dependency_id"] == "response_side_point_to_point_table"
    assert set(table["required_fact_refs"]) == {
        "FACT-003", "FACT-004", "FACT-005", "FACT-006", "FACT-007", "FACT-008", "FACT-020"
    }
    assert "never infer an offered value" in table["default_handoff_instruction"]

    assert items["FAI-8DA425BA8398143F"]["handoff_mode"] == "no_action"
    assert items["FAI-8DA425BA8398143F"]["must_decide_before_stage13"] is False
    assert items["FSF-7CBFD436775B6F94"]["handoff_mode"] == "informational"
    assert items["FSF-7CBFD436775B6F94"]["must_decide_before_stage13"] is False


def test_review_package_inspector_is_bounded(assembled: tuple[dict, dict]) -> None:
    result, payload = assembled
    inspected = inspect_technical_fulltext_review_package(
        result["outputs"]["technical_fulltext_review_package_json"]
    )
    assert inspected["schema_version"] == SCHEMA_VERSION
    assert inspected["summary"] == payload["summary"]
    assert "review_items" not in inspected

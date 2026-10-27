from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from app.technical_fulltext_scoring_assessment import (
    TechnicalFulltextScoringAssessor,
    _project_score_identities,
    classify_assessment_method,
    materialize_assessment_score_items,
)
from cli import cli


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REAL_AUDIT = (
    BACKEND_ROOT
    / "outputs"
    / "hospital_hci_001_fulltext_real_20260806_02"
    / "fulltext"
    / "audit"
    / "technical_fulltext.audit.json"
)
SCHEMA_PATH = BACKEND_ROOT / "schemas" / "technical_fulltext_scoring_assessment_v1.json"
EXPECTED_CONTENT_SHA = "7b2850515aeaecde6d29f8da2f30971fd81d1048533a050c1168fc5f8973a022"


def _copy_audit(target: Path) -> Path:
    if not REAL_AUDIT.is_file():
        pytest.skip("real Stage11 audit fixture is unavailable")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(REAL_AUDIT.read_bytes())
    return target


def _v2_selected_technical_score_items(count: int) -> dict:
    """Synthetic selected rows; row order, not title text, defines Stage11 ordinal."""
    items = []
    for ordinal in range(count, 0, -1):
        source_id = f"technical_{ordinal:02d}_score_rule"
        items.append(
            {
                "score_item_id": source_id,
                "title": f"technical criterion {ordinal}",
                "criteria_summary": f"criterion {ordinal}",
                "score": ordinal,
                "source_template_name": "selected technical template",
                "source_table": {"table_index": 4, "heading": "technical scoring"},
                "source_row": {"table_row_index": ordinal + 1, "data_row_index": ordinal},
                "source_refs": [
                    {
                        "source_type": "score_table",
                        "table_index": 4,
                        "row_or_cell": f"row {ordinal + 1}",
                    }
                ],
                "response_point_ids": [] if ordinal == 1 else [f"TR-{ordinal:03d}"],
            }
        )
    return {"score_items": items}


@pytest.fixture(scope="module")
def scoring_payload(tmp_path_factory: pytest.TempPathFactory) -> dict:
    audit_path = _copy_audit(
        tmp_path_factory.mktemp("stage11_scoring") / "audit" / "technical_fulltext.audit.json"
    )
    result = TechnicalFulltextScoringAssessor().assess(audit_json_path=audit_path)
    return result["assessment"]


def test_historical_scoring_fixture_has_fifteen_items_and_seventy_points(scoring_payload: dict) -> None:
    Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))).validate(
        scoring_payload
    )
    assert scoring_payload["score_catalog"]["score_item_count"] == 15
    assert scoring_payload["score_catalog"]["total_max_score"] == 70
    assert scoring_payload["summary"]["technical_score_max"] == 70
    assert len(scoring_payload["score_items"]) == 15


def test_new_merged_score_ids_and_existing_ids_are_stable(scoring_payload: dict) -> None:
    ids = {item["score_item_id"] for item in scoring_payload["score_items"]}
    assert {
        "SCORE-TR-022-R007", "SCORE-TR-022-R008",
        "SCORE-TR-022-R009", "SCORE-TR-022-R010",
    } <= ids
    assert {
        "SCORE-TR-022-R004", "SCORE-TR-022-R005", "SCORE-TR-022-R006",
        "SCORE-TR-022-R011", "SCORE-TR-022-R012", "SCORE-TR-022-R013",
        "SCORE-TR-022-R015", "SCORE-TR-022-R016", "SCORE-TR-022-R017",
        "SCORE-TR-022-R018", "SCORE-TR-022-R019",
    } <= ids


def test_v2_selected_technical_rows_get_dynamic_local_canonical_ids_and_provenance() -> None:
    response_outline = _v2_selected_technical_score_items(8)

    materialized, mapping = materialize_assessment_score_items(response_outline)

    assert [item["score_item_id"] for item in materialized] == [
        f"SCORE-TECH-{ordinal:03d}" for ordinal in range(1, 9)
    ]
    assert mapping["technical_01_score_rule"] == "SCORE-TECH-001"
    assert mapping["technical_08_score_rule"] == "SCORE-TECH-008"
    assert materialized[0]["response_point_ids"] == []
    provenance = materialized[0]["source_refs"][0]
    assert provenance["source_score_item_id"] == "technical_01_score_rule"
    assert provenance["source_template_name"] == "selected technical template"
    assert provenance["source_table"] == {"table_index": 4, "heading": "technical scoring"}
    assert provenance["source_row"] == {"table_row_index": 2, "data_row_index": 1}


def test_v2_local_canonical_ids_are_stable_and_not_fixed_to_eight() -> None:
    response_outline = _v2_selected_technical_score_items(11)

    first, first_mapping = materialize_assessment_score_items(response_outline)
    second, second_mapping = materialize_assessment_score_items(deepcopy(response_outline))

    assert [item["score_item_id"] for item in first] == [
        f"SCORE-TECH-{ordinal:03d}" for ordinal in range(1, 12)
    ]
    assert first == second
    assert first_mapping == second_mapping


def test_assessment_audit_projection_uses_canonical_ids_without_mutating_source_ids() -> None:
    raw_id = "technical_01_global_quality"
    response_outline = {
        "score_items": [
            {
                "score_item_id": raw_id,
                "title": "global technical quality",
                "criteria_summary": "quality",
                "score": 10,
                "source_row": {"data_row_index": 1},
                "source_refs": [{"source_type": "score_table"}],
                "response_point_ids": [],
            }
        ]
    }
    _, mapping = materialize_assessment_score_items(response_outline)
    audit = {
        "coverage_analysis": {
            "score_item_ids": [raw_id],
            "score_items": [{"score_item_id": raw_id}],
        },
        "issues": [{"issue_id": "ISS-1", "score_item_ids": [raw_id]}],
        "semantic_findings": [{"finding_id": "FND-1", "score_item_ids": [raw_id]}],
        "normalized_document": {"sections": [{"covered_score_item_ids": [raw_id]}]},
    }

    projected = _project_score_identities(audit, mapping)

    assert projected["coverage_analysis"]["score_items"][0]["score_item_id"] == "SCORE-TECH-001"
    assert projected["coverage_analysis"]["score_item_ids"] == ["SCORE-TECH-001"]
    assert projected["issues"][0]["score_item_ids"] == ["SCORE-TECH-001"]
    assert projected["semantic_findings"][0]["score_item_ids"] == ["SCORE-TECH-001"]
    assert projected["normalized_document"]["sections"][0]["covered_score_item_ids"] == ["SCORE-TECH-001"]
    assert audit["coverage_analysis"]["score_items"][0]["score_item_id"] == raw_id


def test_canonical_score_item_id_satisfies_assessment_schema(scoring_payload: dict) -> None:
    payload = deepcopy(scoring_payload)
    payload["score_items"][0]["score_item_id"] = "SCORE-TECH-001"
    payload["score_items"][0]["source_refs"][0]["source_score_item_id"] = "technical_01_score_rule"

    Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))).validate(payload)


def test_assessment_is_bound_to_exact_audited_content_sha(scoring_payload: dict) -> None:
    assert scoring_payload["audited_fulltext"]["content_sha256"] == EXPECTED_CONTENT_SHA
    assert scoring_payload["audited_fulltext"]["schema_version"] == "technical_fulltext_cleaned_v1"


def test_semantic_band_items_are_estimated_with_response_evidence(scoring_payload: dict) -> None:
    items = {
        item["score_item_id"]: item for item in scoring_payload["score_items"]
    }
    for item_id in (
        "SCORE-TR-022-R015", "SCORE-TR-022-R016",
        "SCORE-TR-022-R017", "SCORE-TR-022-R018",
    ):
        item = items[item_id]
        assert item["assessment_method"] == "semantic"
        assert item["assessment_status"] == "estimated"
        assert item["confirmed_score"] is None
        assert item["estimated_score"] is not None
        assert item["response_locations"]
        assert item["response_excerpts"]
        assert item["score_range"]["min"] <= item["estimated_score"] <= item["score_range"]["max"]


def test_unexecuted_evidence_queries_remain_pending_not_missing(scoring_payload: dict) -> None:
    assert scoring_payload["evidence_state"]["query_plan"]["execution_status"] == "planned_not_executed"
    assert scoring_payload["evidence_state"]["verified_evidence_count"] == 0
    evidence_items = [
        item for item in scoring_payload["score_items"]
        if item["assessment_method"] == "evidence_dependent"
    ]
    assert evidence_items
    assert all(item["assessment_status"] == "pending" for item in evidence_items)
    assert all(item["confirmed_score"] is None for item in evidence_items)
    assert all(not item["missing_conditions"] for item in evidence_items)
    assert {
        pending["status"]
        for item in evidence_items
        for pending in item["pending_evidence"]
    } == {"query_not_executed"}


def test_mixed_items_keep_pending_components_and_score_ranges(scoring_payload: dict) -> None:
    mixed = [
        item for item in scoring_payload["score_items"]
        if item["assessment_method"] == "mixed"
    ]
    assert {item["score_item_id"] for item in mixed} >= {
        "SCORE-TR-022-R012", "SCORE-TR-022-R019"
    }
    assert all(item["assessment_status"] in {"pending", "estimated"} for item in mixed)
    assert all(item["confirmed_score"] is None for item in mixed)
    assert all(item["score_range"]["min"] <= item["score_range"]["max"] for item in mixed)


def test_deterministic_rule_confirms_only_when_inputs_are_resolved() -> None:
    item = {
        "title": "数量直接计分",
        "criteria_summary": "满分5分，每缺一项扣1分。",
    }
    assert classify_assessment_method(item) == "deterministic"
    result = TechnicalFulltextScoringAssessor._deterministic_assessment(
        item=item,
        max_score=5,
        relevant_issues=[],
        relevant_findings=[],
        has_locations=True,
    )
    assert result["assessment_status"] == "confirmed"
    assert result["confirmed_score"] == 5
    assert result["score_range"] == {"min": 5, "max": 5}


def test_summary_never_turns_pending_scores_into_a_confirmed_total(scoring_payload: dict) -> None:
    summary = scoring_payload["summary"]
    assert summary["pending_item_count"] > 0
    assert summary["pending_score_max"] > 0
    assert summary["overall_score_range"]["min"] <= summary["overall_score_range"]["max"]
    assert summary["confirmed_item_count"] + summary["estimated_item_count"] + summary["pending_item_count"] == 15


def test_scoring_cli_writes_contract_and_audit_handoff(tmp_path: Path) -> None:
    audit_path = _copy_audit(tmp_path / "audit" / "technical_fulltext.audit.json")
    result = CliRunner().invoke(
        cli,
        ["assess-technical-fulltext-scoring", "--audit-json", str(audit_path)],
    )
    assert result.exit_code == 0, result.output
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    reference = audit["scoring_assessment_ref"]
    assert audit["audit_passes"]["scoring"] == {"status": "completed"}
    assert reference["schema_version"] == "technical_fulltext_scoring_assessment_v1"
    assert Path(reference["path"]).is_file()

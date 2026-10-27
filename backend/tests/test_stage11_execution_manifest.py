from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.technical_fulltext_semantic_audit import (
    REQUIREMENT_DECISION_POLICY_VERSION,
    REQUIREMENT_OBSERVATION_CONTRACT_VERSION,
    REQUIREMENT_PROMPT_VERSION,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = BACKEND_ROOT / "config" / "stage11_execution_manifest_v1.json"
SCHEMA_PATH = BACKEND_ROOT / "schemas" / "stage11_execution_manifest_v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage11_execution_manifest_is_valid_and_canonical() -> None:
    manifest = _load(MANIFEST_PATH)
    Draft202012Validator(_load(SCHEMA_PATH)).validate(manifest)

    steps = manifest["execution_steps"]
    assert manifest["alias_ordering_is_canonical"] is False
    assert [item["canonical_stage"] for item in steps] == [
        "Stage11.0", "Stage11.1", "Stage11.2", "Stage11.3", "Stage11.4"
    ]
    assert [item["execution_order"] for item in steps] == [0, 1, 2, 3, 4]
    assert [item["depends_on"] for item in steps] == [
        ["Stage10"], ["Stage11.0"], ["Stage11.1"], ["Stage11.2"], ["Stage11.3"]
    ]
    assert [item["capability"] for item in steps] == [
        "fulltext_hygiene",
        "deterministic_fulltext_audit",
        "semantic_fulltext_audit",
        "technical_fulltext_scoring_assessment",
        "audit_consolidation_and_finalization",
    ]


def test_historical_aliases_do_not_define_execution_order() -> None:
    steps = _load(MANIFEST_PATH)["execution_steps"]
    aliases = {alias: item["canonical_stage"] for item in steps for alias in item["historical_alias"]}
    assert aliases == {
        "Stage11-E": "Stage11.0",
        "Stage11-A": "Stage11.1",
        "Stage11-B": "Stage11.2",
        "Stage11-C:requirement_observation_calibration_history": "Stage11.2",
        "Stage11-D": "Stage11.4",
    }


def test_requirement_calibration_versions_remain_frozen() -> None:
    calibration = _load(MANIFEST_PATH)["requirement_semantic_calibration"]
    assert calibration["observation_contract"] == REQUIREMENT_OBSERVATION_CONTRACT_VERSION
    assert calibration["prompt"] == REQUIREMENT_PROMPT_VERSION
    assert calibration["runtime_policy"] == REQUIREMENT_DECISION_POLICY_VERSION

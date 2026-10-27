from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.llm_client import ModelCallResult
from app.technical_fulltext_auditor import TechnicalFulltextAuditor
from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_semantic_audit import (
    DECISION_VERDICTS,
    RequirementSemanticDecisionPolicy,
    TechnicalFulltextSemanticAuditError,
    TechnicalFulltextSemanticAuditor,
)


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


def _finalized_audit_fixture_path() -> Path:
    current = _real_audit_path()
    if current.is_file():
        payload = json.loads(current.read_text(encoding="utf-8"))
        if (payload.get("audit_passes") or {}).get("finalization", {}).get("status") == "completed":
            return current
    backup = current.with_name("technical_fulltext.audit.pre_uat_fix.json")
    if backup.is_file():
        return backup
    pytest.skip("frozen finalized Stage 11 audit fixture is unavailable")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_source_lineage(payload: dict[str, Any]) -> None:
    if "source_lineage" not in payload:
        source_path = Path(payload["source_fulltext"]["path"])
        payload["source_lineage"] = TechnicalFulltextAuditor._load_audit_source(
            source_path
        )["source_lineage"]


def _copy_stage_b_audit(tmp_path: Path, *, enhanced: bool = False) -> Path:
    source = _real_audit_path()
    if not source.is_file():
        pytest.skip("frozen Stage 11-B audit fixture is unavailable")
    payload = json.loads(source.read_text(encoding="utf-8"))
    _ensure_source_lineage(payload)
    deterministic_source = deepcopy(
        payload.get("deterministic_source_issues", payload.get("issues") or [])
    )
    payload.pop("semantic_audit", None)
    payload.pop("semantic_findings", None)
    payload.pop("semantic_observation_cache", None)
    for field in (
        "deterministic_source_issues",
        "semantic_finding_dispositions",
        "confirmation_relationships",
        "scoring_assessment_ref",
        "finalization",
    ):
        payload.pop(field, None)
    payload["audit_passes"].pop("finalization", None)
    payload["audit_passes"]["semantic"] = {"status": "not_run"}
    payload["audit_passes"]["scoring"] = {"status": "not_run"}
    payload["issues"] = deterministic_source
    payload["issue_counts"] = {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
    for issue in deterministic_source:
        payload["issue_counts"][issue["severity"]] += 1
    if enhanced:
        paragraphs = payload["normalized_document"]["paragraphs"]
        a, b = paragraphs[0], next(
            item for item in paragraphs if item["chapter_id"] != paragraphs[0]["chapter_id"]
        )

        def issue_location(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "chapter_id": item["chapter_id"],
                "section_id": item["section_id"],
                "paragraph_id": item["paragraph_id"],
                "paragraph_index": item["paragraph_index"],
                "excerpt": item["text"][:400],
                "text_sha256": item["text_sha256"],
            }

        for index in range(2):
            payload["semantic_candidates"]["duplicate_pairs"].append({
                "candidate_id": f"SAC-NEAR-FAKE-{index}",
                "candidate_type": "near_duplicate",
                "similarity": 0.91,
                "ngram_overlap": 0.85,
                "location_a": issue_location(a),
                "location_b": issue_location(b),
                "text_a": a["text"],
                "text_b": b["text"],
            })
        score = payload["coverage_analysis"]["score_items"][0]
        for index in range(3):
            payload["semantic_candidates"]["score_distribution"].append({
                "candidate_id": f"SAC-SCORE-FAKE-{index}",
                "candidate_type": "score_distribution",
                "score_item_id": score["score_item_id"],
                "occurrence_count": score["occurrence_count"],
                "chapter_ids": score["chapter_ids"],
                "section_ids": score["section_ids"],
                "actual_locations": score["actual_locations"],
                "reason_codes": ["fake_distribution_test"],
            })
    target = tmp_path / "audit" / "technical_fulltext.audit.json"
    _write_json(target, payload)
    TechnicalFulltextAuditor()._validate_audit_payload(payload)
    return target


class FakeSemanticLLM:
    def __init__(self, *, invalid: str | None = None, fail_after: int | None = None):
        self.invalid = invalid
        self.fail_after = fail_after
        self.calls: list[dict[str, Any]] = []
        self.pass_offsets: dict[str, int] = {}

    def generate_structured(self, **kwargs: Any) -> ModelCallResult:
        self.calls.append(kwargs)
        call_number = len(self.calls)
        if self.fail_after is not None and call_number > self.fail_after:
            raise RuntimeError("simulated provider failure")
        raw_prompt = kwargs["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0]
        batch = json.loads(raw_prompt)
        pass_type = batch["pass_type"]
        sequences = {
            "placeholder_alias": ["same_fact", "different_fact", "uncertain"],
            "requirement_location": ["acceptable_reference", "misplaced_content", "redundant_response", "coverage_content_mismatch"],
            "numeric_consistency": ["different_attribute", "commitment_conflict", "acceptable"],
            "cross_section_stable_fact_consistency": [
                "consistent", "context_different", "stable_fact_conflict", "uncertain"
            ],
            "terminology_consistency": ["different_entity", "same_entity_variant", "acceptable_context"],
            "near_duplicate": ["necessary_restatement", "meaningful_duplicate"],
            "score_distribution": ["appropriate_distribution", "redundant_score_response", "misplaced_score_response"],
            "adjacent_chapter_coherence": ["coherent", "repeated_reintroduction"],
        }
        offset = self.pass_offsets.get(pass_type, 0)
        results = []
        for index, unit in enumerate(batch["units"]):
            if pass_type == "requirement_location":
                contexts = unit["content"]["all_actual_locations"]
                observation = {
                    "audit_unit_id": unit["audit_unit_id"],
                    "evidence_sufficiency": "sufficient",
                    "primary_response_status": "adequate",
                    "actual_location_assessments": [
                        {
                            "assessment_id": item["assessment_id"],
                            "semantic_role": (
                                "primary_response" if item["is_planned_primary"]
                                else "supporting_reference"
                            ),
                            "substantive_level": "substantive" if item["is_planned_primary"] else "partial",
                            "independent_complete_response": "no",
                        }
                        for item in contexts
                    ],
                    "confidence": "high",
                    "subject": {
                        "subject_type": "requirement",
                        "canonical_key": unit["allowed_requirement_ids"][0],
                        "display_name": "Fake requirement",
                    },
                    "requirement_ids": unit["allowed_requirement_ids"],
                    "summary": "Fake bounded observation",
                    "rationale": "Only supplied evidence was observed.",
                    "details": {"values": [], "comparison": "observation", "evidence_notes": []},
                    "recommended_action": {"resolution_kind": "review", "text": "Review if needed."},
                }
                if self.invalid == "unknown_requirement":
                    observation["requirement_ids"] = ["TR-999"]
                elif self.invalid == "confirmed_value":
                    observation["confirmed_value"] = "联想"
                results.append(observation)
                continue
            decision = sequences[pass_type][(offset + index) % len(sequences[pass_type])]
            verdict = DECISION_VERDICTS[pass_type][decision]
            result = {
                "audit_unit_id": unit["audit_unit_id"],
                "decision": decision,
                "verdict": verdict,
                "confidence": "high" if verdict != "uncertain" else "low",
                "subject": {
                    "subject_type": pass_type,
                    "canonical_key": f"{pass_type}.{unit['audit_unit_id']}",
                    "display_name": f"Fake {pass_type}",
                },
                "location_ids": [item["location_id"] for item in unit["allowed_locations"][:2]],
                "requirement_ids": unit["allowed_requirement_ids"],
                "score_item_ids": unit["allowed_score_item_ids"],
                "placeholder_ids": unit["allowed_placeholder_ids"],
                "summary": f"Fake decision: {decision}",
                "rationale": "Only the supplied bounded evidence was considered.",
                "details": {
                    "values": [],
                    "comparison": decision,
                    "evidence_notes": [],
                },
                "suggested_severity": "warning" if verdict == "issue" else None,
                "suggested_release_blocking": False,
                "recommended_action": {
                    "resolution_kind": "human_review" if verdict != "acceptable" else "none",
                    "text": "Review the supplied evidence." if verdict != "acceptable" else "No action proposed.",
                },
                "requires_human_review": verdict != "acceptable",
            }
            if self.invalid == "unknown_location":
                result["location_ids"] = ["LOC-DOES-NOT-EXIST"]
            elif self.invalid == "unknown_requirement":
                result["requirement_ids"] = ["TR-999"]
            elif self.invalid == "unknown_placeholder":
                result["placeholder_ids"] = ["PH-FIELD-NEW"]
            elif self.invalid == "confirmed_value":
                result["confirmed_value"] = "联想"
            results.append(result)
        self.pass_offsets[pass_type] = offset + len(batch["units"])
        parsed_output = {
            "batch_id": batch["batch_id"],
            "pass_type": pass_type,
            "results": results,
        }
        return ModelCallResult(
            provider="fake",
            protocol="fake",
            profile_name=kwargs.get("profile_name") or "deepseek_draft",
            model="fake-deepseek",
            api_mode="structured",
            request_id=f"fake-{call_number}",
            started_at="2026-08-10T00:00:00+00:00",
            completed_at="2026-08-10T00:00:01+00:00",
            duration_ms=1,
            parsed_output=parsed_output,
            raw_output_available=False,
            finish_status="completed",
            retry_count=0,
            repair_attempted=False,
            usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            warnings=[],
        )


def _run(path: Path, fake: FakeSemanticLLM) -> dict[str, Any]:
    return TechnicalFulltextSemanticAuditor(llm_client=fake).audit(audit_json_path=path)


def _requirement_units(path: Path) -> list[Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    auditor = TechnicalFulltextSemanticAuditor(llm_client=FakeSemanticLLM())
    context, trust = auditor._load_trusted_context(payload)
    units_by_pass, _ = auditor._build_units(
        payload, context=context, trust=trust
    )
    auditor._validate_unit_identities(units_by_pass)
    return units_by_pass["requirement_location"]


def _semantic_units(path: Path) -> dict[str, list[Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    auditor = TechnicalFulltextSemanticAuditor(llm_client=FakeSemanticLLM())
    context, trust = auditor._load_trusted_context(payload)
    units_by_pass, _ = auditor._build_units(
        payload, context=context, trust=trust
    )
    auditor._validate_unit_identities(units_by_pass)
    return units_by_pass


class RequirementIdentityFaultLLM(FakeSemanticLLM):
    def __init__(self, fault: str):
        super().__init__()
        self.fault = fault

    def generate_structured(self, **kwargs: Any) -> ModelCallResult:
        call = super().generate_structured(**kwargs)
        if call.parsed_output["pass_type"] != "requirement_location":
            return call
        if self.fault == "missing":
            call.parsed_output["results"] = []
        elif self.fault == "duplicate":
            call.parsed_output["results"] = [
                deepcopy(call.parsed_output["results"][0]),
                deepcopy(call.parsed_output["results"][0]),
            ]
        return call


def test_requirement_unit_identity_is_deterministic_and_count_is_replayable(
    tmp_path: Path,
) -> None:
    first = _copy_stage_b_audit(tmp_path / "first")
    second = _copy_stage_b_audit(tmp_path / "second")

    first_units = _requirement_units(first)
    second_units = _requirement_units(second)
    first_ids = [unit.audit_unit_id for unit in first_units]
    second_ids = [unit.audit_unit_id for unit in second_units]

    assert first_ids == second_ids
    assert len(first_ids) == len(second_ids)
    assert len(first_ids) == len(set(first_ids))
    assert [unit.requirement_ids for unit in first_units] == [
        unit.requirement_ids for unit in second_units
    ]


def test_requirement_unit_identity_preflight_rejects_duplicate() -> None:
    unit = _requirement_units(_real_audit_path())[0]
    with pytest.raises(TechnicalFulltextSemanticAuditError, match="duplicate="):
        TechnicalFulltextSemanticAuditor._validate_unit_identity_list(
            [unit, unit], pass_type="requirement_location"
        )


def test_requirement_unit_identity_preflight_rejects_missing() -> None:
    unit = _requirement_units(_real_audit_path())[0]
    missing = replace(unit, audit_unit_id="")
    with pytest.raises(TechnicalFulltextSemanticAuditError, match="missing_indexes"):
        TechnicalFulltextSemanticAuditor._validate_unit_identity_list(
            [missing], pass_type="requirement_location"
        )


def test_requirement_provider_replay_uses_atomic_single_unit_calls(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    expected_ids = [unit.audit_unit_id for unit in _requirement_units(path)]
    fake = FakeSemanticLLM()

    _run(path, fake)

    requirement_batches = [
        json.loads(call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0])
        for call in fake.calls
        if json.loads(
            call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0]
        )["pass_type"]
        == "requirement_location"
    ]
    assert requirement_batches
    assert all(len(batch["units"]) == 1 for batch in requirement_batches)
    assert [batch["units"][0]["audit_unit_id"] for batch in requirement_batches] == expected_ids
    requirement_calls = [
        call
        for call in fake.calls
        if json.loads(
            call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0]
        )["pass_type"]
        == "requirement_location"
    ]
    for call, batch in zip(requirement_calls, requirement_batches, strict=True):
        identity_schema = call["response_schema"]["properties"]["results"][
            "items"
        ]["allOf"][1]["properties"]
        assert identity_schema["audit_unit_id"]["const"] == batch["units"][0][
            "audit_unit_id"
        ]
        assert identity_schema["requirement_ids"]["const"] == batch["units"][0][
            "allowed_requirement_ids"
        ]

    replay_ids = [unit.audit_unit_id for unit in _requirement_units(path)]
    assert replay_ids == expected_ids


@pytest.mark.parametrize("fault", ["missing", "duplicate"])
def test_requirement_provider_identity_failure_is_atomic(
    tmp_path: Path, fault: str
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    before_sha = sha256_file(path)

    with pytest.raises(TechnicalFulltextSemanticAuditError):
        _run(path, RequirementIdentityFaultLLM(fault))

    assert sha256_file(path) == before_sha
    assert not path.with_suffix(".md").exists()


def test_all_semantic_passes_produce_findings_without_promoting_fai(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path, enhanced=True)
    before = json.loads(path.read_text(encoding="utf-8"))
    result = _run(path, FakeSemanticLLM())
    after = json.loads(path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert after["audit_result"] == "partial"
    assert after["release_ready"] is False
    assert after["audit_passes"]["semantic"]["status"] == "completed"
    assert after["issues"] == before["issues"]
    assert after["issue_counts"] == before["issue_counts"]
    assert after["confirmation_register"] == before["confirmation_register"]
    assert after["source_lineage"] == before["source_lineage"]
    paragraph_by_id = {
        item["paragraph_id"]: item
        for item in after["normalized_document"]["paragraphs"]
    }
    for finding in after["semantic_findings"]:
        for location in finding["locations"]:
            paragraph = paragraph_by_id[location["paragraph_id"]]
            assert location["text_sha256"] == paragraph["text_sha256"]
            assert location["excerpt"] in paragraph["text"]
    assert after["semantic_audit"]["finding_count"] == len(after["semantic_findings"])
    assert {item["pass_type"] for item in after["semantic_findings"]} == {
        "placeholder_alias", "requirement_location", "numeric_consistency",
        "terminology_consistency", "near_duplicate", "score_distribution",
        "adjacent_chapter_coherence",
    }
    assert after["semantic_audit"]["passes"][
        "cross_section_stable_fact_consistency"
    ]["status"] == "completed"


def test_obvious_cross_chapter_stable_fact_conflict_bypasses_model(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = payload["normalized_document"]["paragraphs"]
    left = paragraphs[0]
    right = next(item for item in paragraphs if item["chapter_id"] != left["chapter_id"])
    for paragraph, text in (
        (left, "本项目服务期限为3年。"),
        (right, "本项目服务期限为5年。"),
    ):
        paragraph["text"] = text
        paragraph["normalized_text"] = text
        paragraph["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _write_json(path, payload)

    fake = FakeSemanticLLM()
    _run(path, fake)
    after = json.loads(path.read_text(encoding="utf-8"))
    findings = [
        item
        for item in after["semantic_findings"]
        if item["pass_type"] == "cross_section_stable_fact_consistency"
    ]
    model_passes = {
        json.loads(call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0])[
            "pass_type"
        ]
        for call in fake.calls
    }

    assert len(findings) == 1
    assert findings[0]["decision"] == "stable_fact_conflict"
    assert findings[0]["verdict"] == "issue"
    assert len(findings[0]["locations"]) == 2
    assert "cross_section_stable_fact_consistency" not in model_passes


def test_bounded_stable_fact_replay_preserves_other_findings_without_model(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = payload["normalized_document"]["paragraphs"]
    left = paragraphs[0]
    right = next(item for item in paragraphs if item["chapter_id"] != left["chapter_id"])
    for paragraph, text in (
        (left, "本项目服务期限为3年。"),
        (right, "本项目服务期限为5年。"),
    ):
        paragraph["text"] = text
        paragraph["normalized_text"] = text
        paragraph["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _write_json(path, payload)
    _run(path, FakeSemanticLLM())
    before = json.loads(path.read_text(encoding="utf-8"))
    preserved = {
        item["finding_id"]
        for item in before["semantic_findings"]
        if item["pass_type"] != "cross_section_stable_fact_consistency"
    }

    bounded_fake = FakeSemanticLLM()
    result = TechnicalFulltextSemanticAuditor(
        llm_client=bounded_fake
    ).audit_stable_fact_consistency(audit_json_path=path)
    after = json.loads(path.read_text(encoding="utf-8"))

    assert result["stable_fact_candidates"] == 1
    assert result["pass_summary"]["decision_counts"] == {
        "stable_fact_conflict": 1
    }
    assert bounded_fake.calls == []
    assert preserved.issubset(
        {item["finding_id"] for item in after["semantic_findings"]}
    )
    assert after["audit_passes"]["scoring"] == {"status": "not_run"}


def test_alias_verdicts_never_merge_or_confirm_fca(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))["confirmation_register"]
    _run(path, FakeSemanticLLM())
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["confirmation_register"] == before
    assert all(item["status"] == "pending" for item in after["confirmation_register"])
    alias_decisions = {item["decision"] for item in after["semantic_findings"] if item["pass_type"] == "placeholder_alias"}
    assert {"same_fact", "different_fact", "uncertain"}.issubset(alias_decisions)
    assert "confirmed_value" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("invalid", ["unknown_location", "unknown_requirement", "unknown_placeholder", "confirmed_value"])
def test_runtime_rejects_model_output_escalation_and_preserves_stage_b(tmp_path: Path, invalid: str) -> None:
    path = _copy_stage_b_audit(tmp_path)
    before_sha = sha256_file(path)
    with pytest.raises(TechnicalFulltextSemanticAuditError):
        _run(path, FakeSemanticLLM(invalid=invalid))
    assert sha256_file(path) == before_sha


def test_second_batch_failure_is_atomic(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    before_sha = sha256_file(path)
    with pytest.raises(TechnicalFulltextSemanticAuditError):
        _run(path, FakeSemanticLLM(fail_after=1))
    assert sha256_file(path) == before_sha
    assert not path.with_suffix(".md").exists()


def test_semantic_finding_ids_are_stable(tmp_path: Path) -> None:
    first = _copy_stage_b_audit(tmp_path / "one")
    second = _copy_stage_b_audit(tmp_path / "two")
    _run(first, FakeSemanticLLM())
    _run(second, FakeSemanticLLM())
    first_ids = [item["finding_id"] for item in json.loads(first.read_text(encoding="utf-8"))["semantic_findings"]]
    second_ids = [item["finding_id"] for item in json.loads(second.read_text(encoding="utf-8"))["semantic_findings"]]
    assert first_ids == second_ids


def test_finding_locations_are_exact_runtime_anchors(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    payload = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = {item["paragraph_id"]: item for item in payload["normalized_document"]["paragraphs"]}
    for finding in payload["semantic_findings"]:
        for location in finding["locations"]:
            paragraph = paragraphs[location["paragraph_id"]]
            assert location["chapter_id"] == paragraph["chapter_id"]
            assert location["section_id"] == paragraph["section_id"]
            assert location["text_sha256"] == paragraph["text_sha256"]


def test_untrusted_planning_context_skips_dependent_requirement_pass(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input_context"]["draft_units"]["sha256"] = "0" * 64
    _write_json(path, payload)
    _run(path, FakeSemanticLLM())
    after = json.loads(path.read_text(encoding="utf-8"))
    requirement_pass = after["semantic_audit"]["passes"]["requirement_location"]
    assert requirement_pass["status"] == "skipped"
    assert requirement_pass["reason"] == "untrusted_planning_context"
    assert not [item for item in after["semantic_findings"] if item["pass_type"] == "requirement_location"]


def test_numeric_different_attribute_and_conflict_remain_findings_only(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    before_issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
    _run(path, FakeSemanticLLM())
    after = json.loads(path.read_text(encoding="utf-8"))
    decisions = {item["decision"] for item in after["semantic_findings"] if item["pass_type"] == "numeric_consistency"}
    assert "different_attribute" in decisions
    assert "commitment_conflict" in decisions
    assert after["issues"] == before_issues


def test_boundary_has_coherent_and_reintroduction_results(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    payload = json.loads(path.read_text(encoding="utf-8"))
    decisions = [item["decision"] for item in payload["semantic_findings"] if item["pass_type"] == "adjacent_chapter_coherence"]
    assert "coherent" in decisions
    assert "repeated_reintroduction" in decisions


def test_token_usage_and_model_metadata_are_aggregated(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    fake = FakeSemanticLLM()
    _run(path, fake)
    semantic = json.loads(path.read_text(encoding="utf-8"))["semantic_audit"]
    assert semantic["provider"] == "fake"
    assert semantic["model"] == "fake-deepseek"
    assert semantic["model_call_count"] == len(fake.calls)
    assert semantic["token_usage"]["total_tokens"] == 120 * len(fake.calls)


def test_stage10_frozen_artifacts_are_not_modified(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    fulltext_dir = _real_audit_path().parent.parent
    targets = [
        fulltext_dir / "technical_fulltext.json",
        fulltext_dir / "technical_fulltext.preview.md",
        fulltext_dir / "technical_fulltext.commit.json",
        fulltext_dir / "technical_fulltext_build_manifest.json",
        fulltext_dir / "chapter_production_index.json",
    ]
    before = {str(item): sha256_file(item) for item in targets}
    _run(path, FakeSemanticLLM())
    after = {str(item): sha256_file(item) for item in targets}
    assert after == before


def test_model_request_uses_structured_output_without_tools(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    fake = FakeSemanticLLM()
    _run(path, fake)
    assert fake.calls
    assert all("response_schema" in call for call in fake.calls)
    assert all("tools" not in call for call in fake.calls)


def test_boundary_prompt_forbids_derived_location_ids() -> None:
    prompt = TechnicalFulltextSemanticAuditor._system_prompt(
        "adjacent_chapter_coherence"
    )
    assert "decision=coherent / verdict=acceptable, return location_ids=[]" in prompt
    assert "Never construct a LOC-* value from a paragraph_id" in prompt


def test_successful_semantic_rerun_invalidates_only_derived_finalization(tmp_path: Path) -> None:
    source = _finalized_audit_fixture_path()
    payload = json.loads(source.read_text(encoding="utf-8"))
    _ensure_source_lineage(payload)
    assert payload["audit_passes"]["finalization"]["status"] == "completed"
    deterministic_source = deepcopy(payload["deterministic_source_issues"])
    target = tmp_path / "audit" / "technical_fulltext.audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _run(target, FakeSemanticLLM())
    after = json.loads(target.read_text(encoding="utf-8"))
    assert after["audit_result"] == "partial"
    assert after["audit_passes"]["semantic"] == {"status": "completed"}
    assert after["audit_passes"]["scoring"] == {"status": "not_run"}
    assert "scoring_assessment_ref" not in after
    assert "finalization" not in after["audit_passes"]
    assert after["issues"] == deterministic_source
    assert after["issue_counts"] == {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
    assert "semantic_finding_dispositions" not in after
    assert "confirmation_relationships" not in after
    assert "finalization" not in after


def _observation(
    *, primary: str, outside_role: str, complete: str,
    evidence: str = "sufficient", outside_level: str = "partial",
) -> dict[str, Any]:
    return {
        "evidence_sufficiency": evidence,
        "primary_response_status": primary,
        "actual_location_assessments": [
            {
                "assessment_id": "RLA-PRIMARY",
                "semantic_role": "primary_response",
                "substantive_level": "substantive" if primary == "adequate" else "none",
                "independent_complete_response": "no",
            },
            {
                "assessment_id": "RLA-OUTSIDE",
                "semantic_role": outside_role,
                "substantive_level": outside_level,
                "independent_complete_response": complete,
            },
        ],
    }


@pytest.mark.parametrize(
    ("observation", "decision", "verdict", "rule"),
    [
        (_observation(primary="adequate", outside_role="contextual_mention", complete="no"), "acceptable_reference", "acceptable", "adequate_primary_without_independent_duplicate"),
        (_observation(primary="adequate", outside_role="evidence_reference", complete="no"), "acceptable_reference", "acceptable", "adequate_primary_without_independent_duplicate"),
        (_observation(primary="adequate", outside_role="supporting_reference", complete="no"), "acceptable_reference", "acceptable", "adequate_primary_without_independent_duplicate"),
        (_observation(primary="adequate", outside_role="independent_response", complete="yes", outside_level="substantive"), "redundant_response", "issue", "adequate_primary_with_independent_duplicate"),
        (_observation(primary="missing", outside_role="independent_response", complete="yes", outside_level="substantive"), "misplaced_content", "issue", "deficient_primary_with_complete_response_elsewhere"),
        (_observation(primary="missing", outside_role="generic_promise", complete="no", outside_level="none"), "coverage_content_mismatch", "issue", "deficient_primary_without_complete_response"),
        (_observation(primary="inadequate", outside_role="evidence_reference", complete="no"), "coverage_content_mismatch", "issue", "deficient_primary_without_complete_response"),
        (_observation(primary="adequate", outside_role="supporting_reference", complete="no", evidence="insufficient"), "uncertain", "uncertain", "evidence_insufficient"),
    ],
)
def test_requirement_runtime_policy_is_replayable(
    observation: dict[str, Any], decision: str, verdict: str, rule: str
) -> None:
    actual = RequirementSemanticDecisionPolicy.decide(
        observation, planned_primary_assessment_ids={"RLA-PRIMARY"}
    )
    assert actual == (decision, verdict, rule)


def test_same_fingerprint_reuses_requirement_observations(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    first = FakeSemanticLLM()
    _run(path, first)
    first_payload = json.loads(path.read_text(encoding="utf-8"))
    first_decisions = {
        item["audit_unit_id"]: item["decision"]
        for item in first_payload["semantic_findings"]
        if item["pass_type"] == "requirement_location"
    }
    second = FakeSemanticLLM()
    _run(path, second)
    second_payload = json.loads(path.read_text(encoding="utf-8"))
    assert not [
        call for call in second.calls
        if json.loads(call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0])["pass_type"] == "requirement_location"
    ]
    assert second_payload["semantic_audit"]["reuse"]["validated_observation_reuse_count"] == len(first_decisions)
    assert {
        item["audit_unit_id"]: item["decision"]
        for item in second_payload["semantic_findings"]
        if item["pass_type"] == "requirement_location"
    } == first_decisions
    assert all(
        item["requirement_decision_provenance"]["observation_reused"]
        for item in second_payload["semantic_findings"]
        if item["pass_type"] == "requirement_location"
    )


def test_same_requirement_id_with_changed_payload_invalidates_reuse(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidate = payload["semantic_candidates"]["requirement_location_mismatches"][0]
    candidate["candidate_id"] += "-CHANGED"
    _write_json(path, payload)
    fake = FakeSemanticLLM()
    _run(path, fake)
    assert [
        call for call in fake.calls
        if json.loads(call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0])["pass_type"] == "requirement_location"
    ]


def test_policy_version_change_invalidates_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    monkeypatch.setattr(
        "app.technical_fulltext_semantic_audit.REQUIREMENT_DECISION_POLICY_VERSION",
        "requirement_semantic_decision_policy_v_next",
    )
    fake = FakeSemanticLLM()
    _run(path, fake)
    assert [
        call for call in fake.calls
        if json.loads(call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0])["pass_type"] == "requirement_location"
    ]


def _semantic_call_pass(call: dict[str, Any]) -> str:
    return json.loads(
        call["user_prompt"].split("\nRUNTIME_VALIDATION_ERROR", 1)[0]
    )["pass_type"]


@pytest.mark.parametrize(
    "pass_type",
    [
        "placeholder_alias",
        "numeric_consistency",
        "terminology_consistency",
        "adjacent_chapter_coherence",
    ],
)
def test_non_requirement_same_input_reuses_each_model_pass(
    tmp_path: Path, pass_type: str
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    first = json.loads(path.read_text(encoding="utf-8"))
    expected = first["semantic_audit"]["passes"][pass_type]["audit_unit_count"]

    fake = FakeSemanticLLM()
    _run(path, fake)
    second = json.loads(path.read_text(encoding="utf-8"))

    assert not [call for call in fake.calls if _semantic_call_pass(call) == pass_type]
    assert second["semantic_audit"]["passes"][pass_type][
        "reused_observation_count"
    ] == expected


def test_second_compatible_run_reuses_all_model_observations_without_calls(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    first = json.loads(path.read_text(encoding="utf-8"))
    first_business = sorted(
        (
            item["finding_id"],
            item["audit_unit_id"],
            item["pass_type"],
            item["decision"],
            item["verdict"],
        )
        for item in first["semantic_findings"]
    )

    fake = FakeSemanticLLM()
    _run(path, fake)
    second = json.loads(path.read_text(encoding="utf-8"))
    second_business = sorted(
        (
            item["finding_id"],
            item["audit_unit_id"],
            item["pass_type"],
            item["decision"],
            item["verdict"],
        )
        for item in second["semantic_findings"]
    )

    assert fake.calls == []
    assert second["semantic_audit"]["model_call_count"] == 0
    assert second["semantic_audit"]["reuse"][
        "validated_observation_reuse_count"
    ] == 14
    assert second["semantic_audit"]["reuse"][
        "non_requirement_validated_observation_reuse_count"
    ] == 22
    assert first_business == second_business


def test_non_requirement_payload_change_invalidates_only_affected_unit(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["semantic_candidates"]["confirmation_aliases"][0]["reason_codes"].append(
        "payload_change_for_cache_test"
    )
    _write_json(path, payload)

    fake = FakeSemanticLLM()
    _run(path, fake)

    alias_calls = [call for call in fake.calls if _semantic_call_pass(call) == "placeholder_alias"]
    assert len(alias_calls) == 1
    alias_batch = json.loads(alias_calls[0]["user_prompt"])
    assert len(alias_batch["units"]) == 1
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["semantic_audit"]["passes"]["placeholder_alias"][
        "reused_observation_count"
    ] == 3
    assert after["semantic_audit"]["passes"]["numeric_consistency"][
        "reused_observation_count"
    ] == 6


@pytest.mark.parametrize(
    ("constant", "replacement"),
    [
        (
            "NON_REQUIREMENT_OBSERVATION_CONTRACT_VERSION",
            "non_requirement_semantic_observation_v_next",
        ),
        (
            "NON_REQUIREMENT_PROMPT_VERSION",
            "non_requirement_semantic_observation_prompt_v_next",
        ),
    ],
)
def test_non_requirement_contract_or_prompt_change_invalidates_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    replacement: str,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    monkeypatch.setattr(
        f"app.technical_fulltext_semantic_audit.{constant}", replacement
    )

    fake = FakeSemanticLLM()
    _run(path, fake)

    assert [
        call
        for call in fake.calls
        if _semantic_call_pass(call) != "requirement_location"
    ]
    assert not [
        call
        for call in fake.calls
        if _semantic_call_pass(call) == "requirement_location"
    ]


def test_non_requirement_policy_version_replays_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    monkeypatch.setattr(
        "app.technical_fulltext_semantic_audit.NON_REQUIREMENT_DECISION_POLICY_VERSION",
        "non_requirement_semantic_decision_policy_v_next",
    )

    fake = FakeSemanticLLM()
    _run(path, fake)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert fake.calls == []
    assert all(
        "verdict" not in entry["observation"]
        for entry in payload["semantic_observation_cache"]
        if entry["pass_type"] != "requirement_location"
    )
    assert all(
        item["verdict"] == DECISION_VERDICTS[item["pass_type"]][item["decision"]]
        for item in payload["semantic_findings"]
        if item["pass_type"] != "requirement_location"
    )
    assert all(
        entry["decision_policy_version"]
        == "non_requirement_semantic_decision_policy_v_next"
        for entry in payload["semantic_observation_cache"]
        if entry["pass_type"] != "requirement_location"
    )


def test_incompatible_model_identity_is_cache_miss(tmp_path: Path) -> None:
    class DifferentFakeSemanticLLM(FakeSemanticLLM):
        pass

    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    fake = DifferentFakeSemanticLLM()
    _run(path, fake)
    assert [
        call
        for call in fake.calls
        if _semantic_call_pass(call) != "requirement_location"
    ]


def test_non_requirement_cache_corruption_fails_atomically(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    payload = json.loads(path.read_text(encoding="utf-8"))
    cache_entry = next(
        item
        for item in payload["semantic_observation_cache"]
        if item["pass_type"] == "adjacent_chapter_coherence"
    )
    cache_entry["observation"]["summary"] += " corrupted"
    _write_json(path, payload)
    before_sha = sha256_file(path)

    with pytest.raises(
        TechnicalFulltextSemanticAuditError, match="cache checksum mismatch"
    ):
        _run(path, FakeSemanticLLM())

    assert sha256_file(path) == before_sha


def test_non_requirement_reuse_preserves_fsu_and_business_set_sha(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    _run(path, FakeSemanticLLM())
    first = json.loads(path.read_text(encoding="utf-8"))
    first_fsu_sha = hashlib.sha256(
        json.dumps(
            sorted(item["audit_unit_id"] for item in first["semantic_findings"]),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    first_business_sha = hashlib.sha256(
        json.dumps(
            sorted(
                (
                    item["finding_id"],
                    item["decision"],
                    item["verdict"],
                    item["suggested_severity"],
                    item["suggested_release_blocking"],
                )
                for item in first["semantic_findings"]
            ),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    _run(path, FakeSemanticLLM())
    second = json.loads(path.read_text(encoding="utf-8"))
    second_fsu_sha = hashlib.sha256(
        json.dumps(
            sorted(item["audit_unit_id"] for item in second["semantic_findings"]),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    second_business_sha = hashlib.sha256(
        json.dumps(
            sorted(
                (
                    item["finding_id"],
                    item["decision"],
                    item["verdict"],
                    item["suggested_severity"],
                    item["suggested_release_blocking"],
                )
                for item in second["semantic_findings"]
            ),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    assert first_fsu_sha == second_fsu_sha
    assert first_business_sha == second_business_sha


def test_other_model_semantic_passes_are_reused_when_present(tmp_path: Path) -> None:
    path = _copy_stage_b_audit(tmp_path, enhanced=True)
    _run(path, FakeSemanticLLM())
    fake = FakeSemanticLLM()

    _run(path, fake)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert not [
        call
        for call in fake.calls
        if _semantic_call_pass(call) in {"near_duplicate", "score_distribution"}
    ]
    assert payload["semantic_audit"]["passes"]["near_duplicate"][
        "reused_observation_count"
    ] == 2
    assert payload["semantic_audit"]["passes"]["score_distribution"][
        "reused_observation_count"
    ] == 3


def test_audited_content_sha_participates_in_non_requirement_fingerprint(
    tmp_path: Path,
) -> None:
    path = _copy_stage_b_audit(tmp_path)
    unit = _semantic_units(path)["placeholder_alias"][0]
    auditor = TechnicalFulltextSemanticAuditor(llm_client=FakeSemanticLLM())
    model_configuration = auditor._model_configuration(auditor.llm_client)

    first = auditor._non_requirement_fingerprint(
        unit=unit,
        audited_content_sha256="0" * 64,
        source_context_sha256="2" * 64,
        model_configuration_sha256=model_configuration["identity_sha256"],
    )
    second = auditor._non_requirement_fingerprint(
        unit=unit,
        audited_content_sha256="1" * 64,
        source_context_sha256="2" * 64,
        model_configuration_sha256=model_configuration["identity_sha256"],
    )

    assert first != second

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_human_review import (
    PATCH_SCHEMA_VERSION,
    TechnicalFulltextHumanReviewService,
)
from app.technical_fulltext_human_review_finalization import (
    TechnicalFulltextHumanReviewFinalizationError,
    TechnicalFulltextHumanReviewFinalizer,
)
from app.technical_fulltext_review_package import TechnicalFulltextReviewPackageAssembler


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REAL_REVIEW_DIR = (
    BACKEND_ROOT
    / "outputs"
    / "hospital_hci_001_fulltext_real_20260806_02"
    / "fulltext"
    / "review"
)
REAL_PACKAGE = REAL_REVIEW_DIR / "technical_fulltext.review_package.json"
REAL_HUMAN = REAL_REVIEW_DIR / "technical_fulltext.human_review.json"
REAL_AUDIT = REAL_REVIEW_DIR.parent / "audit" / "technical_fulltext.audit.json"
REAL_SCORING = (
    REAL_REVIEW_DIR.parent / "audit" / "technical_fulltext.scoring_assessment.json"
)
EXPECTED_PACKAGE_SHA = "ba0ca3f19fdbb0246a644a56e08b2d956ef612999c09e3cfe4e7a2d88dba4601"
EXPECTED_HUMAN_SHA = "7cc61e3f07ccdfe587b65bb945b3bd50c3689e9235e6124e7fccd93fb182cb89"
EXPECTED_AUDIT_SHA = "9415c365e6856cc67346f3533fa4804b76cc79d0364ec28bbb75a7f2192ff591"
EXPECTED_SCORING_SHA = "1f82376e13fffebd366178615468562547d0570ce46ce3968e6434045e80c77d"
EXPECTED_CONTENT_SHA = "7b2850515aeaecde6d29f8da2f30971fd81d1048533a050c1168fc5f8973a022"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    assembled = TechnicalFulltextReviewPackageAssembler().assemble(
        audit_json_path=REAL_AUDIT,
        output_dir=tmp_path,
    )
    package_path = Path(assembled["outputs"]["technical_fulltext_review_package_json"])
    result = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=package_path,
        output_dir=tmp_path,
    )
    return package_path, Path(result["outputs"]["technical_fulltext_human_review_json"])


def _patch(human_path: Path, **sections: list[dict]) -> dict:
    human = _load(human_path)
    payload = {
        "schema_version": PATCH_SCHEMA_VERSION,
        "human_review_id": human["artifact_identity"]["human_review_id"],
        "base_human_review_sha256": sha256_file(human_path),
        "review_package_id": human["review_package_ref"]["review_package_id"],
        "review_package_sha256": human["review_package_ref"]["review_package_sha256"],
        "audited_content_sha256": human["audited_content_sha256"],
        "issue_decisions": [],
        "fact_confirmations": [],
        "material_verifications": [],
        "risk_acceptances": [],
    }
    payload.update(sections)
    return payload


def _apply(human_path: Path, patch: dict, name: str = "patch") -> None:
    patch_path = human_path.parent / f"{name}.json"
    _write(patch_path, patch)
    TechnicalFulltextHumanReviewService().apply(
        human_review_json_path=human_path,
        patch_json_path=patch_path,
    )


def _decide_all(
    human_path: Path,
    *,
    selected_id: str | None = None,
    selected_decision: str = "accepted",
    revision_instruction: str | None = None,
    risk_acceptance_ref: str | None = None,
    risks: list[dict] | None = None,
) -> None:
    human = _load(human_path)
    updates = []
    for item in human["issue_decisions"]:
        if item["source_id"] == selected_id:
            update = {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "decision": selected_decision,
            }
            if selected_decision == "modified":
                update["revision_instruction"] = revision_instruction
            if risk_acceptance_ref:
                update["risk_acceptance_ref"] = risk_acceptance_ref
            updates.append(update)
        else:
            updates.append(
                {
                    "source_type": item["source_type"],
                    "source_id": item["source_id"],
                    "decision": "rejected",
                    "rejection_reason": "synthetic test rejection",
                }
            )
    _apply(
        human_path,
        _patch(
            human_path,
            issue_decisions=updates,
            risk_acceptances=risks or [],
        ),
        "decisions",
    )


def _finalize(package_path: Path, human_path: Path, target: Path) -> tuple[dict, dict]:
    result = TechnicalFulltextHumanReviewFinalizer().finalize(
        review_package_json_path=package_path,
        human_review_json_path=human_path,
        output_dir=target,
    )
    payload = _load(Path(result["outputs"]["technical_fulltext_review_finalization_json"]))
    return result, payload


def test_stage12a_b_lineage_binding(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path / "workspace")
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    lineage = payload["source_lineage"]
    assert lineage["stage12a_review_package"]["sha256"] == sha256_file(package_path)
    assert lineage["stage12b_human_review"]["sha256"] == sha256_file(human_path)
    assert lineage["stage11_audit_sha256"] == EXPECTED_AUDIT_SHA
    assert lineage["stage11_scoring_sha256"] == EXPECTED_SCORING_SHA
    assert lineage["audited_content_sha256"] == EXPECTED_CONTENT_SHA
    assert result["stage13_ready"] is False


def test_stale_human_review_rejection(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    package = _load(package_path)
    package["generated_at"] = "2026-08-21T13:00:00+00:00"
    _write(package_path, package)
    with pytest.raises(TechnicalFulltextHumanReviewFinalizationError, match="stale"):
        _finalize(package_path, human_path, tmp_path / "out")


def test_pending_decision_prevents_handoff(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert result["stage13_ready"] is False
    assert result["stage13_handoff_generated"] is False
    assert payload["stage13_gate"]["pending_issue_source_ids"]
    assert result["outputs"]["technical_fulltext_stage13_handoff_json"] is None


def test_accepted_generates_revision_candidate(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FAI-8A16A33D982E3BF8"
    _decide_all(human_path, selected_id=source_id)
    _apply(
        human_path,
        _patch(
            human_path,
            fact_confirmations=[
                {
                    "source_type": "FACT",
                    "source_id": "FACT-013",
                    "confirmation_status": "confirmed",
                    "confirmed_value": "synthetic manufacturer service network",
                    "source_description": "synthetic human confirmation",
                    "confirmation_basis": "human_provided",
                }
            ],
        ),
        "confirm-fact-013",
    )
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["revision_candidates"] if x["source_id"] == source_id)
    package_item = next(x for x in _load(package_path)["review_items"] if x["source_id"] == source_id)
    assert item["human_decision"] == "accepted"
    assert item["effective_revision_instruction"] == package_item["machine_recommended_action"]["text"]
    assert item["target_locations"][0]["section_id"] == "WC-005-04"
    assert item["approved_fact_refs"] == ["FACT-013"]
    assert result["stage13_ready"] is True


def test_modified_uses_human_instruction(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FAI-5E199F64902608DC"
    instruction = "仅调整目标段落，补充经确认的具体说明。"
    _decide_all(
        human_path,
        selected_id=source_id,
        selected_decision="modified",
        revision_instruction=instruction,
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["revision_candidates"] if x["source_id"] == source_id)
    assert item["effective_revision_instruction"] == instruction
    assert item["acceptance_criteria"] == [instruction]


def test_rejected_is_excluded(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    _decide_all(human_path)
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["revision_candidates"] == []
    assert payload["review_status_summary"]["rejected_issue_decisions"] == 14
    assert payload["stage13_gate"]["stage13_requirement_status"] == "not_required"
    assert result["stage13_handoff_generated"] is False


def test_risk_acceptance_is_preserved(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FAI-8A16A33D982E3BF8"
    risk_id = "HRA-BBBBBBBBBBBBBBBB"
    _decide_all(
        human_path,
        selected_id=source_id,
        risk_acceptance_ref=risk_id,
        risks=[
            {
                "risk_acceptance_id": risk_id,
                "source_refs": [source_id],
                "accepted": True,
                "reason": "synthetic accepted business risk",
                "scope": "synthetic revision",
            }
        ],
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["accepted_risk_items"][0]["risk_acceptance_id"] == risk_id
    assert payload["review_status_summary"]["accepted_risk_count"] == 1


def test_risk_accepted_is_not_resolved_or_revision_task(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FAI-8A16A33D982E3BF8"
    risk_id = "HRA-CCCCCCCCCCCCCCCC"
    _decide_all(
        human_path,
        selected_id=source_id,
        risk_acceptance_ref=risk_id,
        risks=[
            {
                "risk_acceptance_id": risk_id,
                "source_refs": [source_id],
                "accepted": True,
                "reason": "synthetic risk remains open",
                "scope": "current bid",
            }
        ],
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert not any(x["source_id"] == source_id for x in payload["revision_candidates"])
    assert payload["accepted_risk_items"][0]["accepted"] is True
    assert "resolved" not in payload["accepted_risk_items"][0]


def test_confirmed_fact_projection(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    fact = next(x for x in _load(human_path)["fact_confirmations"] if x["source_type"] == "FACT")
    _apply(
        human_path,
        _patch(
            human_path,
            fact_confirmations=[
                {
                    "source_type": "FACT",
                    "source_id": fact["source_id"],
                    "confirmation_status": "confirmed",
                    "confirmed_value": "测试投标人",
                    "source_description": "synthetic human confirmation",
                    "confirmation_basis": "human_provided",
                }
            ],
        ),
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["approved_facts"][0]["source_id"] == fact["source_id"]
    assert payload["approved_facts"][0]["confirmed_value"] == "测试投标人"


def test_pending_fact_is_protected(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["approved_facts"] == []
    pending = [x for x in payload["prohibited_inferences"] if x["source_type"] in {"FACT", "FCA"}]
    assert len(pending) == 52
    assert all("must_not_be_inferred" in x["reason"] for x in pending)


def test_verified_material_projection(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    material = next(x for x in _load(human_path)["material_verifications"] if x["material_or_evidence_ref"] == "KBQ-003")
    _apply(
        human_path,
        _patch(
            human_path,
            material_verifications=[
                {
                    "source_type": material["source_type"],
                    "material_or_evidence_ref": material["material_or_evidence_ref"],
                    "verification_status": "verified",
                    "material_name": "synthetic product certificate",
                    "subject_match": True,
                    "file_reference": "synthetic/product-certificate.pdf",
                    "verification_note": "synthetic verification",
                }
            ],
        ),
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    approved = payload["approved_material_refs"][0]
    assert approved["material_or_evidence_ref"] == "KBQ-003"
    assert "product_certificate" in approved["evidence_types"]


def test_invalid_and_expired_material_protection(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    materials = _load(human_path)["material_verifications"][:2]
    _apply(
        human_path,
        _patch(
            human_path,
            material_verifications=[
                {
                    "source_type": materials[0]["source_type"],
                    "material_or_evidence_ref": materials[0]["material_or_evidence_ref"],
                    "verification_status": "invalid",
                    "verification_note": "synthetic invalid",
                },
                {
                    "source_type": materials[1]["source_type"],
                    "material_or_evidence_ref": materials[1]["material_or_evidence_ref"],
                    "verification_status": "expired",
                    "verification_note": "synthetic expired",
                },
            ],
        ),
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["approved_material_refs"] == []
    reasons = {x["reason"] for x in payload["prohibited_inferences"]}
    assert "material_invalid_must_not_be_used_as_verified_evidence" in reasons
    assert "material_expired_must_not_be_used_as_verified_evidence" in reasons


def test_human_adjusted_deterministic_scoring(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    material = next(x for x in _load(human_path)["material_verifications"] if x["material_or_evidence_ref"] == "KBQ-002")
    _apply(
        human_path,
        _patch(
            human_path,
            material_verifications=[
                {
                    "source_type": material["source_type"],
                    "material_or_evidence_ref": "KBQ-002",
                    "verification_status": "verified",
                    "material_name": "synthetic ISO certificate",
                    "subject_match": True,
                    "file_reference": "synthetic/iso.pdf",
                    "verification_note": "synthetic certificate verification",
                }
            ],
        ),
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["human_adjusted_bid_assessment"]["score_items"] if x["score_item_id"] == "SCORE-TR-022-R006")
    assert item["human_adjusted_score_status"]["confirmed_score_component"] == 1
    assert item["human_adjusted_score_status"]["assessment_status"] == "partially_confirmed"
    assert payload["human_adjusted_bid_assessment"]["score_summary"]["human_adjusted_confirmed_score"] >= 1


def test_semantic_estimated_score_remains_estimated(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    fact = next(x for x in _load(human_path)["fact_confirmations"] if x["source_type"] == "FACT")
    _apply(
        human_path,
        _patch(
            human_path,
            fact_confirmations=[
                {
                    "source_type": "FACT",
                    "source_id": fact["source_id"],
                    "confirmation_status": "confirmed",
                    "confirmed_value": "synthetic fact",
                    "source_description": "synthetic source",
                    "confirmation_basis": "human_provided",
                }
            ],
        ),
    )
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["human_adjusted_bid_assessment"]["score_items"] if x["score_item_id"] == "SCORE-TR-022-R015")
    assert item["machine_score_assessment"]["assessment_status"] == "estimated"
    assert item["human_adjusted_score_status"]["assessment_status"] == "estimated"


def test_machine_score_is_not_overwritten(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    _, payload = _finalize(package_path, human_path, tmp_path / "out")
    package = _load(package_path)
    source = {x["score_item_id"]: x for x in package["scoring_matrix"]}
    for item in payload["human_adjusted_bid_assessment"]["score_items"]:
        original = source[item["score_item_id"]]
        machine = item["machine_score_assessment"]
        assert machine["assessment_status"] == original["assessment_status"]
        assert machine["confirmed_score"] == original["confirmed_score"]
        assert machine["estimated_score"] == original["estimated_score"]
        assert machine["score_range"] == original["score_range"]
    assert payload["human_adjusted_bid_assessment"]["machine_baseline_immutable"] is True


def test_targeted_unresolved_dependency_gate(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FAI-8A16A33D982E3BF8"
    _decide_all(human_path, selected_id=source_id)
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    blocked = payload["stage13_gate"]["blocked_revision_candidates"]
    assert result["stage13_ready"] is False
    assert len(blocked) == 1
    assert blocked[0]["source_id"] == source_id
    assert blocked[0]["pending_fact_refs"] == ["FACT-013"]
    assert blocked[0]["pending_confirmation_refs"] == []


def test_alias_accepted_generates_structural_candidate_without_fact_gate(
    tmp_path: Path,
) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FAI-C409F5E0B2FB13F2"
    _decide_all(human_path, selected_id=source_id)
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["revision_candidates"] if x["source_id"] == source_id)
    assert item["handoff_mode"] == "structural_normalization"
    assert item["required_fact_refs"] == []
    assert item["required_confirmation_refs"] == []
    assert item["unresolved_dependencies"] == {
        "pending_fact_refs": [], "pending_material_refs": [], "pending_confirmation_refs": []
    }
    assert result["stage13_ready"] is True


def test_point_to_point_acceptance_generates_safely_blocked_candidate(
    tmp_path: Path,
) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FSF-1F3D4A4E0C87072D"
    _decide_all(human_path, selected_id=source_id)
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["revision_candidates"] if x["source_id"] == source_id)
    assert item["shared_dependency"]["status"] == "unavailable"
    assert item["required_fact_refs"]
    assert item["approved_facts"] == []
    assert "FACT-003" in item["unresolved_dependencies"]["pending_fact_refs"]
    assert result["stage13_ready"] is False


def test_verified_kbq_cannot_substitute_for_pending_product_facts(
    tmp_path: Path,
) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FSF-1F3D4A4E0C87072D"
    _decide_all(human_path, selected_id=source_id)
    material = next(x for x in _load(human_path)["material_verifications"] if x["material_or_evidence_ref"] == "KBQ-003")
    _apply(
        human_path,
        _patch(
            human_path,
            material_verifications=[
                {
                    "source_type": material["source_type"],
                    "material_or_evidence_ref": "KBQ-003",
                    "verification_status": "verified",
                    "material_name": "synthetic certificate",
                    "subject_match": True,
                    "file_reference": "synthetic/certificate.pdf",
                    "verification_note": "synthetic verification",
                }
            ],
        ),
        "verify-kbq",
    )
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    item = next(x for x in payload["revision_candidates"] if x["source_id"] == source_id)
    assert item["unresolved_dependencies"]["pending_fact_refs"]
    assert result["stage13_ready"] is False


def test_non_action_and_informational_pending_do_not_block_actionable_handoff(
    tmp_path: Path,
) -> None:
    package_path, human_path = _workspace(tmp_path)
    human = _load(human_path)
    non_blocking = {
        "FAI-5E199F64902608DC", "FAI-8DA425BA8398143F", "FAI-B050CE2A5740FA1C",
        "FSF-7CBFD436775B6F94",
    }
    updates = []
    for decision in human["issue_decisions"]:
        if decision["source_id"] in non_blocking:
            continue
        updates.append(
            {
                "source_type": decision["source_type"],
                "source_id": decision["source_id"],
                "decision": "accepted" if decision["source_id"] == "FAI-C409F5E0B2FB13F2" else "rejected",
                **({"rejection_reason": "synthetic resolved"} if decision["source_id"] != "FAI-C409F5E0B2FB13F2" else {}),
            }
        )
    _apply(human_path, _patch(human_path, issue_decisions=updates), "actionability-gate")
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["stage13_gate"]["pending_issue_source_ids"] == []
    assert set(payload["stage13_gate"]["non_blocking_pending_issue_source_ids"]) == non_blocking
    assert result["stage13_ready"] is True


def test_informational_modified_requires_explicit_location_override(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    source_id = "FSF-7CBFD436775B6F94"
    _decide_all(
        human_path,
        selected_id=source_id,
        selected_decision="modified",
        revision_instruction="synthetic scoped revision",
    )
    with pytest.raises(TechnicalFulltextHumanReviewFinalizationError, match="location_override"):
        _finalize(package_path, human_path, tmp_path / "out")


def test_stage13_handoff_generation(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path)
    _decide_all(human_path, selected_id="FAI-8A16A33D982E3BF8")
    _apply(
        human_path,
        _patch(
            human_path,
            fact_confirmations=[
                {
                    "source_type": "FACT", "source_id": "FACT-013",
                    "confirmation_status": "confirmed",
                    "confirmed_value": "synthetic service network",
                    "source_description": "synthetic source",
                    "confirmation_basis": "human_provided",
                }
            ],
        ),
        "confirm-fact-013",
    )
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    handoff_path = Path(result["outputs"]["technical_fulltext_stage13_handoff_json"])
    handoff = _load(handoff_path)
    assert result["stage13_handoff_generated"] is True
    assert handoff["schema_version"] == "technical_fulltext_stage13_handoff_v1"
    assert handoff["summary"]["revision_candidate_count"] == len(payload["revision_candidates"])
    assert handoff["finalization_ref"]["sha256"] == sha256_file(result["outputs"]["technical_fulltext_review_finalization_json"])


def test_real_all_pending_package_returns_not_ready(tmp_path: Path) -> None:
    package_path, human_path = _workspace(tmp_path / "workspace")
    result, payload = _finalize(package_path, human_path, tmp_path / "out")
    assert payload["review_status_summary"]["pending_issue_decisions"] == 14
    assert payload["review_status_summary"]["pending_facts"] == 52
    assert payload["review_status_summary"]["pending_materials"] == 22
    assert "尚未完成人工审核" in payload["stage13_gate"]["human_readable_reason"]
    assert result["stage13_ready"] is False
    assert result["stage13_handoff_generated"] is False


def test_upstream_artifacts_unchanged() -> None:
    assert sha256_file(REAL_PACKAGE) == EXPECTED_PACKAGE_SHA
    assert sha256_file(REAL_HUMAN) == EXPECTED_HUMAN_SHA
    assert sha256_file(REAL_AUDIT) == EXPECTED_AUDIT_SHA
    assert sha256_file(REAL_SCORING) == EXPECTED_SCORING_SHA

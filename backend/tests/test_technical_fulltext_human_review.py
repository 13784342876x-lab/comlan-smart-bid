from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_human_review import (
    PATCH_SCHEMA_VERSION,
    TechnicalFulltextHumanReviewError,
    TechnicalFulltextHumanReviewService,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REVIEW_PACKAGE = (
    BACKEND_ROOT
    / "outputs"
    / "hospital_hci_001_fulltext_real_20260806_02"
    / "fulltext"
    / "review"
    / "technical_fulltext.review_package.json"
)
AUDIT = REVIEW_PACKAGE.parent.parent / "audit" / "technical_fulltext.audit.json"
SCORING = (
    REVIEW_PACKAGE.parent.parent
    / "audit"
    / "technical_fulltext.scoring_assessment.json"
)
EXPECTED_PACKAGE_SHA = "ba0ca3f19fdbb0246a644a56e08b2d956ef612999c09e3cfe4e7a2d88dba4601"
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
def initialized_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("stage12b-template")
    result = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=REVIEW_PACKAGE,
        output_dir=target,
    )
    return Path(result["outputs"]["technical_fulltext_human_review_json"])


@pytest.fixture
def human_review(tmp_path: Path, initialized_template: Path) -> Path:
    target = tmp_path / "technical_fulltext.human_review.json"
    shutil.copy2(initialized_template, target)
    markdown = initialized_template.with_suffix(".md")
    if markdown.exists():
        shutil.copy2(markdown, target.with_suffix(".md"))
    return target


def _patch(review_path: Path, **sections: list[dict]) -> dict:
    review = _load(review_path)
    payload = {
        "schema_version": PATCH_SCHEMA_VERSION,
        "human_review_id": review["artifact_identity"]["human_review_id"],
        "base_human_review_sha256": sha256_file(review_path),
        "review_package_id": review["review_package_ref"]["review_package_id"],
        "review_package_sha256": review["review_package_ref"][
            "review_package_sha256"
        ],
        "audited_content_sha256": review["audited_content_sha256"],
        "issue_decisions": [],
        "fact_confirmations": [],
        "material_verifications": [],
        "risk_acceptances": [],
    }
    payload.update(sections)
    return payload


def _apply(review_path: Path, patch: dict) -> dict:
    patch_path = review_path.parent / "human_review.patch.json"
    _write(patch_path, patch)
    return TechnicalFulltextHumanReviewService().apply(
        human_review_json_path=review_path,
        patch_json_path=patch_path,
    )


def _source_id(review_path: Path, source_type: str) -> str:
    return next(
        item["source_id"]
        for item in _load(review_path)["issue_decisions"]
        if item["source_type"] == source_type
    )


def test_review_package_sha_binding(initialized_template: Path) -> None:
    payload = _load(initialized_template)
    assert sha256_file(REVIEW_PACKAGE) == EXPECTED_PACKAGE_SHA
    assert payload["review_package_ref"] == {
        "path": str(REVIEW_PACKAGE.resolve()),
        "schema_version": "technical_fulltext_review_package_v1",
        "review_package_id": "TRP-20C2F1F4CE09D0B4",
        "review_package_sha256": EXPECTED_PACKAGE_SHA,
    }


def test_audited_content_sha_binding(initialized_template: Path) -> None:
    payload = _load(initialized_template)
    assert payload["audited_content_sha256"] == EXPECTED_CONTENT_SHA


def test_stale_package_rejection(tmp_path: Path) -> None:
    copied_package = tmp_path / "technical_fulltext.review_package.json"
    package = _load(REVIEW_PACKAGE)
    _write(copied_package, package)
    target = tmp_path / "human"
    result = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=copied_package,
        output_dir=target,
    )
    review_path = Path(result["outputs"]["technical_fulltext_human_review_json"])
    package["generated_at"] = "2026-08-21T12:00:00+00:00"
    _write(copied_package, package)
    with pytest.raises(TechnicalFulltextHumanReviewError, match="stale human review"):
        _apply(review_path, _patch(review_path))


def test_stale_overlay_is_archived_not_rebound_on_reinitialize(tmp_path: Path) -> None:
    copied_package = tmp_path / "technical_fulltext.review_package.json"
    package = _load(REVIEW_PACKAGE)
    _write(copied_package, package)
    target = tmp_path / "human"
    first = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=copied_package, output_dir=target
    )
    first_id = first["human_review_id"]
    package["generated_at"] = "2026-08-24T12:00:00+00:00"
    _write(copied_package, package)
    second = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=copied_package, output_dir=target
    )
    assert second["status"] == "initialized"
    assert second["human_review_id"] != first_id
    archived = list((target / "history").glob(f"{first_id}_package_*"))
    assert len(archived) == 1
    assert (archived[0] / "technical_fulltext.human_review.json").exists()


def test_fai_accepted_decision(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    _apply(
        human_review,
        _patch(
            human_review,
            issue_decisions=[
                {"source_type": "FAI", "source_id": source_id, "decision": "accepted"}
            ],
        ),
    )
    item = next(i for i in _load(human_review)["issue_decisions"] if i["source_id"] == source_id)
    assert item["decision"] == "accepted"
    assert item["use_machine_location"] is True


def test_fai_rejected_requires_reason(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    with pytest.raises(TechnicalFulltextHumanReviewError, match="rejection_reason"):
        _apply(
            human_review,
            _patch(
                human_review,
                issue_decisions=[
                    {"source_type": "FAI", "source_id": source_id, "decision": "rejected"}
                ],
            ),
        )


def test_fai_modified_requires_revision_instruction(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    with pytest.raises(TechnicalFulltextHumanReviewError, match="revision_instruction"):
        _apply(
            human_review,
            _patch(
                human_review,
                issue_decisions=[
                    {"source_type": "FAI", "source_id": source_id, "decision": "modified"}
                ],
            ),
        )


def test_machine_severity_is_immutable(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    patch = _patch(
        human_review,
        issue_decisions=[
            {
                "source_type": "FAI",
                "source_id": source_id,
                "decision": "accepted",
                "machine_severity": "info",
            }
        ],
    )
    with pytest.raises(TechnicalFulltextHumanReviewError, match="schema validation"):
        _apply(human_review, patch)


def test_machine_disposition_is_immutable(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    patch = _patch(
        human_review,
        issue_decisions=[
            {
                "source_type": "FAI",
                "source_id": source_id,
                "decision": "accepted",
                "machine_disposition": "accepted",
            }
        ],
    )
    with pytest.raises(TechnicalFulltextHumanReviewError, match="schema validation"):
        _apply(human_review, patch)


def test_fsf_decision(human_review: Path) -> None:
    source_id = _source_id(human_review, "FSF")
    _apply(
        human_review,
        _patch(
            human_review,
            issue_decisions=[
                {"source_type": "FSF", "source_id": source_id, "decision": "accepted"}
            ],
        ),
    )
    item = next(i for i in _load(human_review)["issue_decisions"] if i["source_id"] == source_id)
    assert item["decision"] == "accepted"


def test_fca_has_one_canonical_confirmation(initialized_template: Path) -> None:
    confirmations = [
        item
        for item in _load(initialized_template)["fact_confirmations"]
        if item["source_type"] == "FCA"
    ]
    assert len(confirmations) == 32
    assert len({item["source_id"] for item in confirmations}) == 32


def test_fact_confirmed_requires_value(human_review: Path) -> None:
    fact = next(
        item for item in _load(human_review)["fact_confirmations"] if item["source_type"] == "FACT"
    )
    with pytest.raises(TechnicalFulltextHumanReviewError, match="confirmed_value"):
        _apply(
            human_review,
            _patch(
                human_review,
                fact_confirmations=[
                    {
                        "source_type": "FACT",
                        "source_id": fact["source_id"],
                        "confirmation_status": "confirmed",
                        "source_description": "投标人书面确认",
                        "confirmation_basis": "explicitly_confirmed",
                    }
                ],
            ),
        )


def test_no_tender_minimum_to_project_fact_inference(human_review: Path) -> None:
    fact = next(
        item for item in _load(human_review)["fact_confirmations"] if item["source_type"] == "FACT"
    )
    with pytest.raises(TechnicalFulltextHumanReviewError, match="human source description"):
        _apply(
            human_review,
            _patch(
                human_review,
                fact_confirmations=[
                    {
                        "source_type": "FACT",
                        "source_id": fact["source_id"],
                        "confirmation_status": "confirmed",
                        "confirmed_value": "招标最低要求值",
                    }
                ],
            ),
        )


def test_material_verified(human_review: Path) -> None:
    material = _load(human_review)["material_verifications"][0]
    _apply(
        human_review,
        _patch(
            human_review,
            material_verifications=[
                {
                    "source_type": material["source_type"],
                    "material_or_evidence_ref": material["material_or_evidence_ref"],
                    "verification_status": "verified",
                    "material_name": "测试证书",
                    "subject_match": True,
                    "file_reference": "evidence/test-certificate.pdf",
                    "verification_note": "人工核验主体与有效期。",
                }
            ],
        ),
    )
    updated = _load(human_review)["material_verifications"][0]
    assert updated["verification_status"] == "verified"
    assert updated["upstream_status"] == "query_not_executed"


def test_planned_not_executed_remains_pending(initialized_template: Path) -> None:
    materials = _load(initialized_template)["material_verifications"]
    assert len(materials) == 22
    assert {item["upstream_status"] for item in materials} == {"query_not_executed"}
    assert {item["verification_status"] for item in materials} == {"pending"}


def test_risk_acceptance_requires_reason(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    with pytest.raises(TechnicalFulltextHumanReviewError, match="requires a non-empty reason"):
        _apply(
            human_review,
            _patch(
                human_review,
                risk_acceptances=[
                    {"source_refs": [source_id], "accepted": True, "scope": "本轮投标"}
                ],
            ),
        )


def test_risk_acceptance_is_not_rejected_finding(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    risk_id = "HRA-AAAAAAAAAAAAAAAA"
    patch = _patch(
        human_review,
        issue_decisions=[
            {
                "source_type": "FAI",
                "source_id": source_id,
                "decision": "rejected",
                "rejection_reason": "机器判断不成立",
                "risk_acceptance_ref": risk_id,
            }
        ],
        risk_acceptances=[
            {
                "risk_acceptance_id": risk_id,
                "source_refs": [source_id],
                "accepted": True,
                "reason": "明确承担风险",
                "scope": "本轮投标",
            }
        ],
    )
    with pytest.raises(TechnicalFulltextHumanReviewError, match="not a rejected"):
        _apply(human_review, patch)


def test_duplicate_fca_projection_does_not_duplicate_task(initialized_template: Path) -> None:
    package = _load(REVIEW_PACKAGE)
    projected = [
        item["source_id"]
        for item in package["review_items"]
        if item["source_type"] == "FCA"
    ] + [
        item["source_id"]
        for item in package["fact_material_queue"]
        if item["source_type"] == "FCA"
    ]
    canonical = [
        item["source_id"]
        for item in _load(initialized_template)["fact_confirmations"]
        if item["source_type"] == "FCA"
    ]
    assert len(projected) == 64
    assert len(canonical) == len(set(projected)) == 32


def test_decision_update_preserves_minimum_history(human_review: Path) -> None:
    source_id = _source_id(human_review, "FAI")
    _apply(
        human_review,
        _patch(
            human_review,
            issue_decisions=[
                {"source_type": "FAI", "source_id": source_id, "decision": "accepted"}
            ],
        ),
    )
    _apply(
        human_review,
        _patch(
            human_review,
            issue_decisions=[
                {
                    "source_type": "FAI",
                    "source_id": source_id,
                    "decision": "modified",
                    "revision_instruction": "改为补充可核验事实。",
                    "change_reason": "复核后调整处理方向",
                }
            ],
        ),
    )
    item = next(i for i in _load(human_review)["issue_decisions"] if i["source_id"] == source_id)
    assert item["decision"] == "modified"
    assert len(item["change_history"]) == 2
    assert item["change_history"][-1]["previous"]["decision"] == "accepted"
    assert item["change_history"][-1]["change_reason"] == "复核后调整处理方向"


def test_stage12a_artifact_unchanged() -> None:
    assert sha256_file(REVIEW_PACKAGE) == EXPECTED_PACKAGE_SHA


def test_stage11_artifacts_unchanged() -> None:
    assert sha256_file(AUDIT) == EXPECTED_AUDIT_SHA
    assert sha256_file(SCORING) == EXPECTED_SCORING_SHA

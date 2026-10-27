from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.technical_fulltext_composer import sha256_file, stable_json_sha256
from app.technical_fulltext_human_review import (
    PATCH_SCHEMA_VERSION,
    TechnicalFulltextHumanReviewError,
    TechnicalFulltextHumanReviewService,
)
from app.technical_fulltext_revision_review import review_stage10_revision
from app.technical_fulltext_targeted_revision import execute_targeted_section_revision
from test_technical_fulltext_semantic_audit import FakeSemanticLLM
from test_technical_fulltext_targeted_revision import (
    CANDIDATE_SOURCE_ID,
    CANDIDATE_SOURCE_TYPE,
    _candidate,
    _committed_source,
    _replace_handoff_candidate,
    _replacement_draft,
    _stage9_source,
)
from test_technical_fulltext_revision_materializer import write_json
from test_technical_writing_agent_revision import EmptyMetadataStore, FinalModel


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _install_stage11_inputs(fixture: dict[str, Any], tmp_path: Path) -> None:
    planning_root = tmp_path / "review_planning"
    tasks_root = planning_root / "tasks"
    score_items = [
        {
            "score_item_id": "SCORE-1",
            "title": "技术响应完整性",
            "criteria_summary": "完整响应得10分。",
            "score": 10,
            "response_point_ids": ["TR-1"],
            "source_refs": [{"file": "score.pdf", "page": 1}],
        },
        {
            "score_item_id": "SCORE-2",
            "title": "实施说明",
            "criteria_summary": "实施说明完整得5分。",
            "score": 5,
            "response_point_ids": ["TR-2"],
            "source_refs": [{"file": "score.pdf", "page": 2}],
        },
    ]
    response_path = write_json(
        planning_root / "technical_response_outline.json",
        {
            "schema_version": "technical_response_outline_v2",
            "chapters": [
                {
                    "chapter_id": "SRC-1",
                    "title": "技术要求",
                    "response_points": [
                        {
                            "point_id": requirement_id,
                            "original_text": f"{requirement_id} 对应技术要求。",
                            "mandatory_marker": "required",
                            "source_refs": [{"file": "requirements.pdf", "page": index}],
                        }
                        for index, requirement_id in enumerate(
                            ("TR-1", "TR-2", "TR-3"), start=1
                        )
                    ],
                }
            ],
            "score_items": score_items,
        },
    )
    writing = _load(fixture["case"]["outline_path"])
    writing["schema_version"] = "technical_writing_outline_v0"
    writing["score_items"] = score_items
    writing_path = write_json(planning_root / "technical_writing_outline.json", writing)
    task_path = write_json(
        tasks_root / "task_information.json",
        {"schema_version": "task_information_requirements_v1", "tasks": []},
    )
    draft_path = write_json(
        tasks_root / "draft_units.json",
        {
            "schema_version": "technical_draft_units_v0",
            "content_location_plan": {},
            "draft_units": [],
        },
    )
    write_json(
        tasks_root / "project_fact_request.json",
        {"schema_version": "project_fact_request_v0", "slots": []},
    )
    write_json(
        tasks_root / "local_evidence_query_plan.json",
        {
            "schema_version": "local_evidence_query_plan_v0",
            "execution_status": "planned_not_executed",
            "local_kb_queried": False,
            "queries": [],
        },
    )
    input_paths = {
        "writing_outline_path": str(writing_path.resolve()),
        "response_outline_path": str(response_path.resolve()),
        "task_information_path": str(task_path.resolve()),
        "draft_units_path": str(draft_path.resolve()),
        "confirmed_project_facts_path": None,
    }
    input_hashes = {
        key.replace("_path", "_sha256"): sha256_file(path)
        for key, path in input_paths.items()
        if path is not None
    }
    input_hashes["confirmed_project_facts_sha256"] = None
    run_path = fixture["fulltext_path"].parent / "technical_fulltext.run.json"
    run = _load(run_path)
    run["input_paths"] = input_paths
    run["input_hashes"] = input_hashes
    write_json(run_path, run)


def _seed_parent_review_issues(fixture: dict[str, Any]) -> None:
    paragraphs = [
        "Parent review duplicate group one records the complete execution boundary, "
        "responsible role, verification evidence, closure condition, and final acceptance "
        "result so the deterministic audit has one stable cross-chapter issue.",
        "Parent review duplicate group two records implementation, independent checking, "
        "issue registration, corrective action, result confirmation, and the evidence needed "
        "before work may advance to the following delivery stage.",
        "Parent review duplicate group three records document ownership, archive rules, "
        "approval signatures, unresolved-item closure, delivery completeness, and the final "
        "quality gate applied before the historical version is accepted.",
    ]
    fulltext = _load(fixture["fulltext_path"])
    duplicated = "\n\n".join(paragraphs)
    fulltext["chapters"][0]["sections"][0]["content"] = duplicated
    fulltext["chapters"][1]["sections"][0]["content"] = duplicated
    write_json(fixture["fulltext_path"], fulltext)
    commit_path = fixture["fulltext_path"].parent / "technical_fulltext.commit.json"
    commit = _load(commit_path)
    commit["technical_fulltext_json_sha256"] = sha256_file(fixture["fulltext_path"])
    write_json(commit_path, commit)


def _patch(review_path: Path, **sections: list[dict[str, Any]]) -> dict[str, Any]:
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


def _apply(review_path: Path, patch: dict[str, Any], name: str) -> dict[str, Any]:
    patch_path = review_path.parent / name
    write_json(patch_path, patch)
    return TechnicalFulltextHumanReviewService().apply(
        human_review_json_path=review_path,
        patch_json_path=patch_path,
    )


def _snapshot(root: Path) -> dict[str, tuple[bytes, str]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), sha256_file(path))
        for path in root.rglob("*")
        if path.is_file()
    }


def _materialize_t34_revision(
    fixture: dict[str, Any], tmp_path: Path
) -> dict[str, Any]:
    _replace_handoff_candidate(fixture["handoff_path"], _candidate())
    stage9_source, _ = _stage9_source(tmp_path, fixture["case"]["outline_path"])
    source_fulltext = _load(fixture["fulltext_path"])
    replacement = _replacement_draft(source_fulltext)
    retained_duplicate = source_fulltext["chapters"][1]["sections"][0][
        "content"
    ].split("\n\n", 1)[0]
    replacement["sections"][0]["content"] += f"\n\n{retained_duplicate}"
    return execute_targeted_section_revision(
        source_fulltext_path=fixture["fulltext_path"],
        stage12_handoff_path=fixture["handoff_path"],
        candidate_source_type=CANDIDATE_SOURCE_TYPE,
        candidate_source_id=CANDIDATE_SOURCE_ID,
        stage9_source=stage9_source,
        draft_units_path=None,
        output_dir=fixture["case"]["output_dir"],
        output_key="revision_t35",
        llm=FinalModel(replacement),
        metadata_store=EmptyMetadataStore(),
    )


def test_revision_runs_existing_stage11_and_starts_isolated_stage12_round(
    tmp_path: Path,
) -> None:
    fixture = _committed_source(tmp_path)
    _seed_parent_review_issues(fixture)
    _install_stage11_inputs(fixture, tmp_path)

    parent_round = review_stage10_revision(
        fulltext_json_path=fixture["fulltext_path"],
        semantic_llm_client=FakeSemanticLLM(),
    )
    parent_package_path = Path(
        parent_round["review_package"]["outputs"][
            "technical_fulltext_review_package_json"
        ]
    )
    parent_review_path = Path(
        parent_round["human_review"]["outputs"][
            "technical_fulltext_human_review_json"
        ]
    )
    parent_review = _load(parent_review_path)
    assert len(parent_review["issue_decisions"]) >= 3
    first, second, third = parent_review["issue_decisions"][:3]
    parent_patch = _patch(
        parent_review_path,
        issue_decisions=[
            {
                "source_type": first["source_type"],
                "source_id": first["source_id"],
                "decision": "accepted",
            },
            {
                "source_type": second["source_type"],
                "source_id": second["source_id"],
                "decision": "rejected",
                "rejection_reason": "旧正文问题不适用。",
            },
            {
                "source_type": third["source_type"],
                "source_id": third["source_id"],
                "decision": "modified",
                "revision_instruction": "旧 revision instruction",
                "optional_comment": "旧 optional comment",
            },
        ],
        risk_acceptances=[
            {
                "source_refs": [first["source_id"]],
                "accepted": True,
                "reason": "旧正文风险已由用户接受。",
                "scope": "parent revision only",
            }
        ],
    )
    _apply(parent_review_path, parent_patch, "parent.patch.json")
    parent_decisions = {
        item["source_id"]: item for item in _load(parent_review_path)["issue_decisions"]
    }
    assert parent_decisions[first["source_id"]]["decision"] == "accepted"
    assert parent_decisions[second["source_id"]]["decision"] == "rejected"
    assert parent_decisions[third["source_id"]]["decision"] == "modified"
    assert parent_decisions[third["source_id"]]["revision_instruction"]
    assert parent_decisions[third["source_id"]]["optional_comment"]
    parent_finalization_path = write_json(
        parent_review_path.parent / "technical_fulltext.review_finalization.json",
        {"schema_version": "parent-finalization-test", "status": "completed"},
    )

    t34 = _materialize_t34_revision(fixture, tmp_path)
    revision_fulltext_path = Path(
        t34["materialization"]["outputs"]["technical_fulltext_json"]
    )
    original_run_path = write_json(
        tmp_path / "bid_runs" / "completed.json",
        {
            "run_id": "parent-completed-run",
            "pipeline_status": "HUMAN_REVIEW_READY",
            "final_status": "HUMAN_REVIEW_READY",
        },
    )
    parent_root = fixture["fulltext_path"].parent
    parent_before = _snapshot(parent_root)
    pipeline_before = original_run_path.read_bytes()

    revision_round = review_stage10_revision(
        fulltext_json_path=revision_fulltext_path,
        semantic_llm_client=FakeSemanticLLM(),
    )

    assert revision_round["status"] == "human_review_pending"
    assert revision_round["hygiene"]["status"] == "completed"
    assert revision_round["deterministic_audit"]["status"] == "completed"
    assert revision_round["semantic_audit"]["status"] == "completed"
    assert revision_round["scoring_assessment"]["status"] == "completed"
    assert revision_round["audit_finalization"]["status"] == "completed"
    assert revision_round["review_package"]["status"] == "assembled"
    assert revision_round["human_review"]["status"] == "initialized"

    audit_path = Path(
        revision_round["audit_finalization"]["outputs"][
            "technical_fulltext_audit_json"
        ]
    )
    scoring_path = Path(
        revision_round["scoring_assessment"]["outputs"][
            "technical_fulltext_scoring_assessment_json"
        ]
    )
    package_path = Path(
        revision_round["review_package"]["outputs"][
            "technical_fulltext_review_package_json"
        ]
    )
    review_path = Path(
        revision_round["human_review"]["outputs"][
            "technical_fulltext_human_review_json"
        ]
    )
    audit = _load(audit_path)
    scoring = _load(scoring_path)
    package = _load(package_path)
    review = _load(review_path)
    revision_fulltext = _load(revision_fulltext_path)
    audited_content_sha = stable_json_sha256(
        revision_round["hygiene"]["cleaned_artifact"]["fulltext"]
    )

    assert audit["output_key"] == revision_fulltext["output_key"] == "revision_t35"
    assert scoring["output_key"] == package["artifact_identity"]["output_key"]
    assert review["artifact_identity"]["output_key"] == "revision_t35"
    assert audit["source_lineage"]["committed_fulltext"]["file_sha256"] == sha256_file(
        revision_fulltext_path
    )
    assert audit["source_lineage"]["audited_fulltext"][
        "content_sha256"
    ] == audited_content_sha
    assert scoring["audited_fulltext"]["content_sha256"] == audited_content_sha
    reviewed_content_sha = package["reviewed_content_sha256"]
    assert reviewed_content_sha == audited_content_sha
    assert package["source_lineage"]["final_stage11_audit"][
        "file_sha256"
    ] == sha256_file(audit_path)
    assert package["source_lineage"]["stage11_scoring_assessment"][
        "file_sha256"
    ] == sha256_file(scoring_path)
    assert package["source_lineage"]["audited_response_content"][
        "content_sha256"
    ] == audited_content_sha
    assert review["audited_content_sha256"] == reviewed_content_sha
    assert package["artifact_identity"]["review_package_id"] != _load(
        parent_package_path
    )["artifact_identity"]["review_package_id"]

    assert all(item["decision"] == "pending" for item in review["issue_decisions"])
    assert all(item["revision_instruction"] is None for item in review["issue_decisions"])
    assert all(item["optional_comment"] is None for item in review["issue_decisions"])
    assert review["risk_acceptances"] == []

    assert review["fact_confirmations"]
    new_fact = review["fact_confirmations"][0]
    assert new_fact["confirmation_status"] == "pending"
    command_patch = _patch(
        review_path,
        fact_confirmations=[
            {
                "source_type": new_fact["source_type"],
                "source_id": new_fact["source_id"],
                "confirmation_status": "confirmed",
                "confirmed_value": "Revision B confirmed value",
                "source_description": "Confirmed by the revision reviewer.",
                "confirmation_basis": "explicitly_confirmed",
            }
        ],
    )
    _apply(review_path, command_patch, "revision.patch.json")
    assert _load(review_path)["fact_confirmations"][0][
        "confirmation_status"
    ] == "confirmed"
    with pytest.raises(
        TechnicalFulltextHumanReviewError,
        match="changed since the patch was prepared",
    ):
        _apply(review_path, command_patch, "stale.patch.json")

    assert _snapshot(parent_root) == parent_before
    assert original_run_path.read_bytes() == pipeline_before
    assert parent_finalization_path.read_bytes() == parent_before[
        str(parent_finalization_path.relative_to(parent_root))
    ][0]
    assert not (review_path.parent / "technical_fulltext.review_finalization.json").exists()
    assert not (review_path.parent / "technical_fulltext.stage13_handoff.json").exists()

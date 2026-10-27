from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_human_review_finalization import (
    TechnicalFulltextHumanReviewFinalizer,
)
from app.technical_fulltext_revision_review import review_stage10_revision
from app.web.api import create_app
from app.web.project_lifecycle_service import ProjectLifecycleConflictError
from test_technical_fulltext_revision_review import (
    _apply,
    _install_stage11_inputs,
    _patch,
    _seed_parent_review_issues,
    _snapshot,
)
from test_technical_fulltext_semantic_audit import FakeSemanticLLM
from test_technical_fulltext_release import _complete_review
from test_technical_fulltext_targeted_revision import (
    _committed_source,
    _replacement_draft,
    _stage9_source,
)
from test_technical_writing_agent_revision import EmptyMetadataStore, FinalModel
from test_web_current_artifact_set import _register_project


class RecordingLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def launch(self, *, project_id: str, operation_id: str) -> None:
        self.calls.append((project_id, operation_id))


class RevisionInputs:
    def __init__(self, source: Any) -> None:
        self.source = source

    def revision_inputs(self, project_id: str):
        return self.source, None


class ExplodingModel:
    def __init__(self) -> None:
        self.calls = 0

    def run_with_tools(self, **kwargs: Any):
        self.calls += 1
        raise RuntimeError("focused provider failure")


class StalePublishingLifecycle:
    def publish_current_artifact_set(self, *args: Any, **kwargs: Any) -> None:
        raise ProjectLifecycleConflictError("current artifact set changed")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare_ready_project(
    tmp_path: Path,
    project_id: str,
    *,
    target_decision: str = "modified",
) -> dict[str, Any]:
    fixture = _committed_source(tmp_path)
    _seed_parent_review_issues(fixture)
    _install_stage11_inputs(fixture, tmp_path)
    parent_round = review_stage10_revision(
        fulltext_json_path=fixture["fulltext_path"],
        semantic_llm_client=FakeSemanticLLM(),
    )
    package_path = Path(
        parent_round["review_package"]["outputs"][
            "technical_fulltext_review_package_json"
        ]
    )
    review_path = Path(
        parent_round["human_review"]["outputs"][
            "technical_fulltext_human_review_json"
        ]
    )
    review = _load(review_path)
    target = review["issue_decisions"][0]
    issue_decisions = []
    for item in review["issue_decisions"]:
        if item["source_id"] == target["source_id"]:
            update = {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "decision": target_decision,
            }
            if target_decision == "modified":
                update.update(
                    {
                        "revision_instruction": "保留完整小节并补充执行、复核和关闭判据。",
                        "use_machine_location": False,
                        "location_override": {"section_id": "WC-010-01"},
                        "location_override_reason": "用户明确要求修改准备工作小节。",
                    }
                )
            else:
                update.update(
                    {
                        "use_machine_location": False,
                        "location_override": {"section_id": "WC-010-01"},
                        "location_override_reason": "聚焦验证机器建议的既有修改链路。",
                    }
                )
            issue_decisions.append(update)
        else:
            issue_decisions.append(
                {
                    "source_type": item["source_type"],
                    "source_id": item["source_id"],
                    "decision": "accepted",
                }
            )
    fact_confirmations = [
        {
            "source_type": item["source_type"],
            "source_id": item["source_id"],
            "confirmation_status": "confirmed",
            "confirmed_value": "经本轮人工审核确认",
            "source_description": "T38 saved Stage12 decision",
            "confirmation_basis": "human_provided",
        }
        for item in review["fact_confirmations"]
    ]
    _apply(
        review_path,
        _patch(
            review_path,
            issue_decisions=issue_decisions,
            fact_confirmations=fact_confirmations,
        ),
        "t38-ready.patch.json",
    )
    finalized = TechnicalFulltextHumanReviewFinalizer().finalize(
        review_package_json_path=package_path,
        human_review_json_path=review_path,
        output_dir=review_path.parent,
    )
    assert finalized["stage13_ready"] is True
    handoff_path = Path(
        finalized["outputs"]["technical_fulltext_stage13_handoff_json"]
    )
    handoff = _load(handoff_path)
    candidate = next(
        item
        for item in handoff["revision_candidates"]
        if item["source_type"] == target["source_type"]
        and item["source_id"] == target["source_id"]
    )
    assert candidate["human_decision"] == target_decision
    if target_decision == "modified":
        assert candidate["target_locations"] == [{"section_id": "WC-010-01"}]
    else:
        assert candidate["machine_recommended_action"]["text"]
        assert candidate["effective_revision_instruction"]

    outputs_root = Path(fixture["case"]["output_dir"])
    parent_root = Path(fixture["fulltext_path"]).parent.parent
    manifest_path, pipeline_state_path = _register_project(
        outputs_root, project_id, parent_root
    )
    stage9_source, _ = _stage9_source(tmp_path, fixture["case"]["outline_path"])
    app = create_app(outputs_root)
    launcher = RecordingLauncher()
    operation_service = app.state.revision_operation_service
    operation_service.launcher = launcher
    operation_service.writing_plan = RevisionInputs(stage9_source)
    operation_service.semantic_llm_client = FakeSemanticLLM()
    operation_service.metadata_store = EmptyMetadataStore()
    return {
        "app": app,
        "client": TestClient(app),
        "fixture": fixture,
        "outputs_root": outputs_root,
        "parent_root": parent_root,
        "manifest_path": manifest_path,
        "pipeline_state_path": pipeline_state_path,
        "review_path": review_path,
        "handoff_path": handoff_path,
        "candidate": candidate,
        "launcher": launcher,
        "operation_service": operation_service,
    }


def _review_token(client: TestClient, project_id: str) -> str:
    response = client.get(f"/api/projects/{project_id}/review")
    assert response.status_code == 200, response.text
    return response.headers["etag"]


def _start(prepared: dict[str, Any], project_id: str, etag: str):
    candidate = prepared["candidate"]
    return prepared["client"].post(
        f"/api/projects/{project_id}/review/issues/{candidate['source_id']}/ai-modify",
        headers={"If-Match": etag},
        json={"source_type": candidate["source_type"]},
    )


def test_start_is_etag_candidate_and_active_operation_safe(tmp_path: Path) -> None:
    project_id = "prj-20260914T010000Z-00000038"
    prepared = _prepare_ready_project(tmp_path, project_id)
    token = _review_token(prepared["client"], project_id)

    stale = _start(prepared, project_id, '"' + "0" * 64 + '"')
    assert stale.status_code == 409
    ready_handoff = prepared["handoff_path"].read_bytes()
    blocked_handoff = _load(prepared["handoff_path"])
    blocked_handoff["artifact_identity"]["status"] = "blocked"
    prepared["handoff_path"].write_text(
        json.dumps(blocked_handoff, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    not_ready = _start(prepared, project_id, token)
    assert not_ready.status_code == 409
    prepared["handoff_path"].write_bytes(ready_handoff)
    missing = prepared["client"].post(
        f"/api/projects/{project_id}/review/issues/not-a-candidate/ai-modify",
        headers={"If-Match": token},
        json={"source_type": "FAI"},
    )
    assert missing.status_code == 409
    duplicated = _load(prepared["handoff_path"])
    duplicated["revision_candidates"].append(duplicated["revision_candidates"][0])
    prepared["handoff_path"].write_text(
        json.dumps(duplicated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ambiguous = _start(prepared, project_id, token)
    assert ambiguous.status_code == 409
    prepared["handoff_path"].write_bytes(ready_handoff)
    assert prepared["launcher"].calls == []

    started = _start(prepared, project_id, token)
    assert started.status_code == 202, started.text
    operation = started.json()
    assert operation["status"] == "queued"
    assert operation["phase"] == "rewriting"
    assert prepared["launcher"].calls == [(project_id, operation["operation_id"])]
    state_path = (
        prepared["outputs_root"]
        / "web_projects"
        / project_id
        / "revision_operation.json"
    )
    assert state_path.is_file()
    persisted = _load(state_path)
    assert persisted["operation_id"] == operation["operation_id"]
    assert persisted["output_key"].startswith("stage13-")
    polled = prepared["client"].get(
        f"/api/projects/{project_id}/review/ai-modify"
    )
    assert polled.status_code == 200
    assert polled.json() == operation

    duplicate = _start(prepared, project_id, token)
    assert duplicate.status_code == 409
    assert len(prepared["launcher"].calls) == 1


def test_worker_rechecks_human_review_before_calling_t34(tmp_path: Path) -> None:
    project_id = "prj-20260914T020000Z-00000038"
    prepared = _prepare_ready_project(tmp_path, project_id)
    started = _start(prepared, project_id, _review_token(prepared["client"], project_id))
    assert started.status_code == 202
    operation = started.json()
    model = ExplodingModel()
    prepared["operation_service"].revision_llm = model

    review = _load(prepared["review_path"])
    review["updated_at"] = "2026-09-14T02:00:01+00:00"
    prepared["review_path"].write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = prepared["operation_service"].execute(
        project_id, operation["operation_id"]
    )

    assert result.status == "failed"
    assert result.error_code == "stale_revision"
    assert model.calls == 0
    assert "current_artifact_set" not in _load(prepared["manifest_path"])


def test_web_operation_runs_t34_t35_t37_and_switches_current_reads(
    tmp_path: Path,
) -> None:
    project_id = "prj-20260914T030000Z-00000038"
    prepared = _prepare_ready_project(tmp_path, project_id)
    client = prepared["client"]
    parent_before = _snapshot(prepared["parent_root"])
    manifest_before = _load(prepared["manifest_path"])
    pipeline_before = prepared["pipeline_state_path"].read_bytes()
    source = _load(prepared["fixture"]["fulltext_path"])
    model = FinalModel(_replacement_draft(source))
    prepared["operation_service"].revision_llm = model

    before_fulltext = client.get(f"/api/projects/{project_id}/fulltext")
    assert before_fulltext.status_code == 200
    started = _start(prepared, project_id, _review_token(client, project_id))
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    completed = prepared["operation_service"].execute(project_id, operation_id)

    assert completed.status == "completed", completed.error_message
    assert model.messages
    prompt = json.dumps(model.messages, ensure_ascii=False)
    current_body = source["chapters"][0]["sections"][0]["content"]
    assert current_body.split("\n\n", 1)[0] in prompt
    assert prepared["candidate"]["effective_revision_instruction"] in prompt

    manifest_after = _load(prepared["manifest_path"])
    assert manifest_after["current_run_id"] == manifest_before["current_run_id"]
    assert manifest_after["current_artifact_set"]["output_key"].startswith("stage13-")
    assert prepared["pipeline_state_path"].read_bytes() == pipeline_before
    after_fulltext = client.get(f"/api/projects/{project_id}/fulltext")
    assert after_fulltext.status_code == 200, after_fulltext.text
    assert after_fulltext.json()["document_id"] != before_fulltext.json()["document_id"]
    assert after_fulltext.json()["status"]["stage13_ready"] is False
    after_review = client.get(f"/api/projects/{project_id}/review")
    assert after_review.status_code == 200, after_review.text
    assert after_review.headers["etag"]
    revision_items = [
        item for group in after_review.json()["groups"] for item in group["items"]
    ]
    assert revision_items
    assert all(
        item["current_decision"] == "pending"
        for item in revision_items
    )
    revision_root = (
        Path(manifest_after["current_artifact_set"]["fulltext_output_dir"])
        / manifest_after["current_artifact_set"]["output_key"]
    )
    assert (revision_root / "fulltext" / "audit" / "technical_fulltext.audit.json").is_file()
    assert (revision_root / "fulltext" / "review" / "technical_fulltext.review_package.json").is_file()
    assert (revision_root / "fulltext" / "review" / "technical_fulltext.human_review.json").is_file()
    assert not (revision_root / "fulltext" / "review" / "technical_fulltext.review_finalization.json").exists()
    fact_item = after_review.json()["fact_confirmations"][0]
    revision_decision = client.patch(
        f"/api/projects/{project_id}/review/facts/{fact_item['trace_id']}",
        headers={"If-Match": after_review.headers["etag"]},
        json={
            "confirmation_status": "confirmed",
            "confirmed_value": "Revision B current-round confirmation",
            "source_description": "T38 existing command integration",
            "confirmation_basis": "explicitly_confirmed",
        },
    )
    assert revision_decision.status_code == 200, revision_decision.text
    assert revision_decision.headers["etag"] != after_review.headers["etag"]
    assert _snapshot(prepared["parent_root"]) == parent_before


def test_accepted_machine_recommendation_runs_existing_revision_and_rereview_flow(
    tmp_path: Path,
) -> None:
    project_id = "prj-20260914T035000Z-00000038"
    prepared = _prepare_ready_project(
        tmp_path, project_id, target_decision="accepted"
    )
    client = prepared["client"]
    source = _load(prepared["fixture"]["fulltext_path"])
    model = FinalModel(_replacement_draft(source))
    prepared["operation_service"].revision_llm = model

    before_release = client.get(f"/api/projects/{project_id}/release")
    assert before_release.status_code == 200
    assert before_release.json()["releaseable"] is False
    assert "revision_required" in before_release.json()["blocking_reasons"]

    started = _start(prepared, project_id, _review_token(client, project_id))
    assert started.status_code == 202, started.text
    completed = prepared["operation_service"].execute(
        project_id, started.json()["operation_id"]
    )
    assert completed.status == "completed", completed.error_message
    assert model.messages
    assert prepared["candidate"]["effective_revision_instruction"] in json.dumps(
        model.messages, ensure_ascii=False
    )

    manifest = _load(prepared["manifest_path"])
    revision_root = (
        Path(manifest["current_artifact_set"]["fulltext_output_dir"])
        / manifest["current_artifact_set"]["output_key"]
    )
    pending_review = client.get(f"/api/projects/{project_id}/review")
    assert pending_review.status_code == 200
    assert all(
        item["current_decision"] == "pending"
        for group in pending_review.json()["groups"]
        for item in group["items"]
    )
    assert client.get(f"/api/projects/{project_id}/release").json()["releaseable"] is False

    _complete_review(revision_root)
    after_rereview = client.get(f"/api/projects/{project_id}/release")
    assert after_rereview.status_code == 200
    assert after_rereview.json()["releaseable"] is True


def test_accepted_candidate_without_machine_instruction_is_rejected_before_launch(
    tmp_path: Path,
) -> None:
    project_id = "prj-20260914T036000Z-00000038"
    prepared = _prepare_ready_project(
        tmp_path, project_id, target_decision="accepted"
    )
    handoff = _load(prepared["handoff_path"])
    candidate = next(
        item
        for item in handoff["revision_candidates"]
        if item["source_type"] == prepared["candidate"]["source_type"]
        and item["source_id"] == prepared["candidate"]["source_id"]
    )
    candidate["machine_recommended_action"]["text"] = ""
    prepared["handoff_path"].write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    response = _start(
        prepared, project_id, _review_token(prepared["client"], project_id)
    )
    assert response.status_code == 409
    assert prepared["launcher"].calls == []


def test_worker_failure_and_publication_conflict_never_publish(tmp_path: Path) -> None:
    failure_project = "prj-20260914T040000Z-00000038"
    failure = _prepare_ready_project(tmp_path / "failure", failure_project)
    failure["operation_service"].revision_llm = ExplodingModel()
    started = _start(
        failure, failure_project, _review_token(failure["client"], failure_project)
    )
    result = failure["operation_service"].execute(
        failure_project, started.json()["operation_id"]
    )
    assert result.status == "failed"
    assert result.error_code == "revision_failed"
    assert "current_artifact_set" not in _load(failure["manifest_path"])

    stale_project = "prj-20260914T050000Z-00000038"
    stale = _prepare_ready_project(tmp_path / "stale", stale_project)
    source = _load(stale["fixture"]["fulltext_path"])
    stale["operation_service"].revision_llm = FinalModel(_replacement_draft(source))
    stale["operation_service"].lifecycle = StalePublishingLifecycle()
    started = _start(stale, stale_project, _review_token(stale["client"], stale_project))
    result = stale["operation_service"].execute(
        stale_project, started.json()["operation_id"]
    )
    assert result.status == "failed"
    assert result.error_code == "stale_revision"
    assert "current_artifact_set" not in _load(stale["manifest_path"])

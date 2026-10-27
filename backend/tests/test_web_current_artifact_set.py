from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_human_review_finalization import (
    TechnicalFulltextHumanReviewFinalizer,
)
from app.technical_fulltext_revision_materializer import (
    materialize_single_section_revision,
)
from app.technical_fulltext_revision_review import review_stage10_revision
from app.web.api import create_app
from app.web.project_lifecycle_service import (
    ProjectLifecycleConflictError,
    ProjectLifecycleService,
)
from app.web.project_resolver import (
    ProjectArtifactReferenceError,
    ReadOnlyProjectResolver,
)
from app.web.query_service import ProjectReadService
from test_technical_fulltext_revision_materializer import write_json
from test_technical_fulltext_revision_review import (
    _install_stage11_inputs,
    _materialize_t34_revision,
    _seed_parent_review_issues,
    _snapshot,
)
from test_technical_fulltext_semantic_audit import FakeSemanticLLM
from test_technical_fulltext_targeted_revision import _committed_source


PROJECT_A = "prj-20260914T000000Z-0000000a"
PROJECT_STALE = "prj-20260914T000000Z-0000000b"
PROJECT_INVALID = "prj-20260914T000000Z-0000000c"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _register_project(outputs_root: Path, project_id: str, artifact_root: Path) -> tuple[Path, Path]:
    run_id = f"bid-20260914T000000Z-{project_id[-8:]}"
    state_path = write_json(
        outputs_root / "bid_runs" / run_id / "pipeline_state.json",
        {
            "run_id": run_id,
            "status": "HUMAN_REVIEW_READY",
            "routing": {
                "fulltext_output_dir": str(artifact_root.parent.resolve()),
                "fulltext_output_key": artifact_root.name,
            },
        },
    )
    manifest_path = write_json(
        outputs_root / "web_projects" / project_id / "project.json",
        {
            "schema_version": "web_project_manifest_v1",
            "project_id": project_id,
            "project_name": project_id,
            "source_files": [],
            "pipeline_runs": [{"run_id": run_id}],
            "current_run_id": run_id,
            "created_at": "2026-09-14T00:00:00+00:00",
            "updated_at": "2026-09-14T00:00:00+00:00",
        },
    )
    return manifest_path, state_path


@pytest.fixture(scope="module")
def artifact_sets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp_path = tmp_path_factory.mktemp("t37")
    fixture = _committed_source(tmp_path)
    _seed_parent_review_issues(fixture)
    _install_stage11_inputs(fixture, tmp_path)

    parent_round = review_stage10_revision(
        fulltext_json_path=fixture["fulltext_path"],
        semantic_llm_client=FakeSemanticLLM(),
    )
    parent_review_path = Path(
        parent_round["human_review"]["outputs"]["technical_fulltext_human_review_json"]
    )
    TechnicalFulltextHumanReviewFinalizer().finalize(
        review_package_json_path=Path(
            parent_round["review_package"]["outputs"][
                "technical_fulltext_review_package_json"
            ]
        ),
        human_review_json_path=parent_review_path,
        output_dir=parent_review_path.parent,
    )

    t34 = _materialize_t34_revision(fixture, tmp_path)
    revision_b_fulltext = Path(
        t34["materialization"]["outputs"]["technical_fulltext_json"]
    )
    review_stage10_revision(
        fulltext_json_path=revision_b_fulltext,
        semantic_llm_client=FakeSemanticLLM(),
    )

    source = _load(fixture["fulltext_path"])
    replacement_c = deepcopy(source["chapters"][0]["sections"][0])
    replacement_c["content"] = (
        "Revision C is a separately audited sibling prepared from Revision A. "
        "It exists only to prove stale publication cannot overwrite a newer current set."
    )
    revision_c = materialize_single_section_revision(
        source_fulltext_path=fixture["fulltext_path"],
        target_section_id=replacement_c["section_id"],
        replacement_section=replacement_c,
        output_dir=fixture["case"]["output_dir"],
        output_key="revision_t37_c",
        stage12_handoff_path=fixture["handoff_path"],
    )
    revision_c_fulltext = Path(revision_c["outputs"]["technical_fulltext_json"])
    review_stage10_revision(
        fulltext_json_path=revision_c_fulltext,
        semantic_llm_client=FakeSemanticLLM(),
    )

    return {
        "outputs_root": Path(fixture["case"]["output_dir"]),
        "parent_root": Path(fixture["fulltext_path"]).parent.parent,
        "parent_fulltext": Path(fixture["fulltext_path"]),
        "revision_b_root": revision_b_fulltext.parent.parent,
        "revision_c_root": revision_c_fulltext.parent.parent,
    }


def test_publish_switches_fulltext_and_pending_review_without_mutating_parent(
    artifact_sets: dict[str, Any],
) -> None:
    outputs_root = artifact_sets["outputs_root"]
    parent_root = artifact_sets["parent_root"]
    revision_b_root = artifact_sets["revision_b_root"]
    manifest_path, state_path = _register_project(outputs_root, PROJECT_A, parent_root)
    manifest_before = _load(manifest_path)
    state_before = state_path.read_bytes()
    parent_before = _snapshot(parent_root)

    client = TestClient(create_app(outputs_root))
    before = client.get(f"/api/projects/{PROJECT_A}/fulltext")
    assert before.status_code == 200, before.text
    assert before.json()["document_id"] == "DOC-STRICT"
    parent_section_text = "\n\n".join(
        paragraph["text"]
        for chapter in before.json()["chapters"]
        for section in chapter["sections"]
        if section["section_id"] == "WC-010-01"
        for paragraph in section["paragraphs"]
    )

    ProjectLifecycleService(outputs_root).publish_current_artifact_set(
        PROJECT_A,
        fulltext_output_dir=revision_b_root.parent,
        output_key=revision_b_root.name,
    )

    manifest_after = _load(manifest_path)
    assert manifest_after["current_run_id"] == manifest_before["current_run_id"]
    assert manifest_after["current_artifact_set"] == {
        "fulltext_output_dir": str(revision_b_root.parent.resolve()),
        "output_key": revision_b_root.name,
    }
    assert state_path.read_bytes() == state_before

    after = client.get(f"/api/projects/{PROJECT_A}/fulltext")
    assert after.status_code == 200, after.text
    assert after.json()["document_id"] == "technical-fulltext:revision_t35"
    assert after.json()["version"]["audited_content_sha256"] != before.json()["version"][
        "audited_content_sha256"
    ]
    revision_section_text = "\n\n".join(
        paragraph["text"]
        for chapter in after.json()["chapters"]
        for section in chapter["sections"]
        if section["section_id"] == "WC-010-01"
        for paragraph in section["paragraphs"]
    )
    canonical_revision = _load(
        revision_b_root / "fulltext" / "hygiene" / "technical_fulltext.cleaned.json"
    )
    expected_replacement = canonical_revision["fulltext"]["chapters"][0]["sections"][0][
        "content"
    ]
    assert revision_section_text != parent_section_text
    assert revision_section_text == expected_replacement
    assert after.json()["status"]["stage13_ready"] is False
    assert not any(
        item["revision_candidate"]
        for item in after.json()["trace_index"]["reviews"]
    )

    revision_review_dir = revision_b_root / "fulltext" / "review"
    revision_finalization = revision_review_dir / "technical_fulltext.review_finalization.json"
    assert not revision_finalization.exists()
    review = client.get(f"/api/projects/{PROJECT_A}/review")
    assert review.status_code == 200, review.text
    overview = ProjectReadService(ReadOnlyProjectResolver(outputs_root)).overview(PROJECT_A)
    assert overview.project.current_revision.version_id == "technical-fulltext:revision_t35"
    token_before = review.headers["etag"]
    fact = review.json()["fact_confirmations"][0]
    mutation = client.patch(
        f"/api/projects/{PROJECT_A}/review/facts/{fact['trace_id']}",
        headers={"If-Match": token_before},
        json={
            "confirmation_status": "confirmed",
            "confirmed_value": "Revision B current-round confirmation",
            "source_description": "T37 focused Web command",
            "confirmation_basis": "explicitly_confirmed",
        },
    )
    assert mutation.status_code == 200, mutation.text
    assert mutation.headers["etag"] != token_before
    assert revision_finalization.is_file()
    assert _load(revision_review_dir / "technical_fulltext.human_review.json")[
        "artifact_identity"
    ]["output_key"] == "revision_t35"

    assert _snapshot(parent_root) == parent_before
    assert state_path.read_bytes() == state_before


def test_invalid_explicit_pointer_fails_instead_of_using_legacy_run(
    artifact_sets: dict[str, Any],
) -> None:
    outputs_root = artifact_sets["outputs_root"]
    manifest_path, _ = _register_project(
        outputs_root, PROJECT_INVALID, artifact_sets["parent_root"]
    )
    manifest = _load(manifest_path)
    manifest["current_artifact_set"] = {
        "fulltext_output_dir": str(outputs_root.resolve()),
        "output_key": "missing-revision",
    }
    write_json(manifest_path, manifest)

    with pytest.raises(ProjectArtifactReferenceError, match="unavailable"):
        ReadOnlyProjectResolver(outputs_root).resolve(PROJECT_INVALID)


def test_stale_revision_cannot_overwrite_newer_current_artifact_set(
    artifact_sets: dict[str, Any],
) -> None:
    outputs_root = artifact_sets["outputs_root"]
    manifest_path, _ = _register_project(
        outputs_root, PROJECT_STALE, artifact_sets["parent_root"]
    )
    lifecycle = ProjectLifecycleService(outputs_root)
    lifecycle.publish_current_artifact_set(
        PROJECT_STALE,
        fulltext_output_dir=artifact_sets["revision_c_root"].parent,
        output_key=artifact_sets["revision_c_root"].name,
    )
    current_c = manifest_path.read_bytes()

    with pytest.raises(ProjectLifecycleConflictError, match="changed"):
        lifecycle.publish_current_artifact_set(
            PROJECT_STALE,
            fulltext_output_dir=artifact_sets["revision_b_root"].parent,
            output_key=artifact_sets["revision_b_root"].name,
        )

    assert manifest_path.read_bytes() == current_c
    assert _load(manifest_path)["current_artifact_set"]["output_key"] == "revision_t37_c"

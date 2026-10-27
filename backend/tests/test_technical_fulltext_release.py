from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_human_review import (
    PATCH_SCHEMA_VERSION,
    TechnicalFulltextHumanReviewService,
)
from app.technical_fulltext_human_review_finalization import (
    TechnicalFulltextHumanReviewFinalizer,
)
from app.technical_fulltext_release import (
    TechnicalFulltextReleaseStale,
    evaluate_release_gate,
)
from app.web.api import create_app
from app.web.analysis_query_service import project_required_submission_items
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.release_service import TechnicalFulltextReleaseService


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REAL_OUTPUTS = BACKEND_ROOT / "outputs"
REAL_PROJECT_ID = "prj-20260910T052035Z-a48291a1"
REAL_CURRENT_ROOT = (
    REAL_OUTPUTS
    / "bid_pipeline_outputs"
    / "bid-20260910T052035Z-342c5914"
    / "stage13-20260917T084618Z-04f758d5"
)
REAL_PARENT_ROOT = REAL_CURRENT_ROOT.parent / "fulltext-bid-20260910T052035Z-342c5914"
PROJECT_ID = "prj-t43-finalized"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _complete_review(root: Path) -> None:
    review = root / "fulltext" / "review"
    for name in (
        "technical_fulltext.human_review.json",
        "technical_fulltext.human_review.md",
        "technical_fulltext.review_finalization.json",
        "technical_fulltext.review_finalization.md",
        "technical_fulltext.stage13_handoff.json",
    ):
        (review / name).unlink(missing_ok=True)
    package = review / "technical_fulltext.review_package.json"
    initialized = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=package, output_dir=review
    )
    human_path = Path(initialized["outputs"]["technical_fulltext_human_review_json"])
    human = _load(human_path)
    patch = {
        "schema_version": PATCH_SCHEMA_VERSION,
        "human_review_id": human["artifact_identity"]["human_review_id"],
        "base_human_review_sha256": sha256_file(human_path),
        "review_package_id": human["review_package_ref"]["review_package_id"],
        "review_package_sha256": human["review_package_ref"]["review_package_sha256"],
        "audited_content_sha256": human["audited_content_sha256"],
        "issue_decisions": [
            {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "decision": "rejected",
                "rejection_reason": "isolated release fixture: no document change required",
            }
            for item in human["issue_decisions"]
        ],
        "fact_confirmations": [
            {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "confirmation_status": "rejected",
                "rejection_reason": "isolated release fixture: unavailable fact",
            }
            for item in human["fact_confirmations"]
        ],
        "material_verifications": [
            {
                "source_type": item["source_type"],
                "material_or_evidence_ref": item["material_or_evidence_ref"],
                "verification_status": "not_provided",
                "verification_note": "isolated release fixture: not provided",
            }
            for item in human["material_verifications"]
        ],
        "risk_acceptances": [],
    }
    patch_path = review / "release-fixture.patch.json"
    _write(patch_path, patch)
    TechnicalFulltextHumanReviewService().apply(
        human_review_json_path=human_path, patch_json_path=patch_path
    )
    TechnicalFulltextHumanReviewFinalizer().finalize(
        review_package_json_path=package,
        human_review_json_path=human_path,
        output_dir=review,
    )


def _register(outputs: Path, root: Path, project_id: str = PROJECT_ID) -> Path:
    manifest = outputs / "web_projects" / project_id / "project.json"
    _write(
        manifest,
        {
            "schema_version": "web_project_manifest_v1",
            "project_id": project_id,
            "project_name": "智慧医院信息化项目",
            "source_files": [],
            "pipeline_runs": [{"run_id": "bid-t43-original"}],
            "current_run_id": "bid-t43-original",
            "current_artifact_set": {
                "fulltext_output_dir": str(root.parent.resolve()),
                "output_key": root.name,
            },
            "created_at": "2026-09-22T00:00:00Z",
            "updated_at": "2026-09-22T00:00:00Z",
        },
    )
    return manifest


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    outputs = tmp_path_factory.mktemp("t43-release")
    root = outputs / "artifact-sets" / REAL_CURRENT_ROOT.name
    shutil.copytree(REAL_CURRENT_ROOT, root)
    _complete_review(root)
    manifest = _register(outputs, root)
    client = TestClient(create_app(outputs))
    ready = client.get(f"/api/projects/{PROJECT_ID}/release")
    assert ready.status_code == 200, ready.text
    assert ready.json()["releaseable"] is True
    response = client.post(f"/api/projects/{PROJECT_ID}/release")
    assert response.status_code == 200, response.text
    return {
        "outputs": outputs,
        "root": root,
        "manifest": manifest,
        "client": client,
        "release": response.json(),
    }


def test_current_pending_review_is_blocked_without_artifacts() -> None:
    service = TechnicalFulltextReleaseService(ReadOnlyProjectResolver(REAL_OUTPUTS))
    status = service.status(REAL_PROJECT_ID)
    assert status.releaseable is False
    assert status.blocking_reasons == [
        "review_not_finalized",
        "pending_issue_decisions",
        "pending_fact_confirmations",
        "pending_material_verifications",
    ]
    assert not (REAL_CURRENT_ROOT / "fulltext" / "release").exists()


def test_existing_parent_revision_required_is_blocked() -> None:
    review = REAL_PARENT_ROOT / "fulltext" / "review"
    result = evaluate_release_gate(
        cleaned_fulltext_path=REAL_PARENT_ROOT / "fulltext" / "hygiene" / "technical_fulltext.cleaned.json",
        review_package_path=review / "technical_fulltext.review_package.json",
        human_review_path=review / "technical_fulltext.human_review.json",
        finalization_path=review / "technical_fulltext.review_finalization.json",
    )
    assert result["releaseable"] is False
    assert "revision_required" in result["blocking_reasons"]


def test_finalized_fixture_generates_valid_docx_pdf_and_manifest(published: dict[str, Any]) -> None:
    release = published["release"]
    assert release["status"] == "published"
    assert release["docx"]["size_bytes"] > 0
    assert release["pdf"]["size_bytes"] > 0
    assert release["docx"]["filename"].endswith("_技术标正文.docx")
    assert release["pdf"]["filename"].endswith("_技术标正文.pdf")
    manifest = _load(
        published["root"] / "fulltext" / "release" / "technical_fulltext.release.json"
    )
    assert manifest["docx"]["validation"] == {
        "chapter_count": 9,
        "section_count": 74,
        "table_count": 7,
        "placeholder_count": 29,
        "toc_entry_count": 83,
        "page_field_present": True,
        "size_bytes": release["docx"]["size_bytes"],
    }
    assert manifest["pdf"]["validation"]["page_count"] > 0
    assert manifest["pdf"]["validation"]["outline_entry_count"] == 83
    assert manifest["pdf"]["validation"]["footer_page_number_pages"] == manifest["pdf"]["validation"]["page_count"]
    for kind in ("markdown", "docx", "pdf"):
        path = published["root"] / manifest[kind]["path"]
        assert sha256_file(path) == manifest[kind]["sha256"]


def test_web_downloads_have_content_type_filename_and_valid_content(published: dict[str, Any]) -> None:
    client = published["client"]
    docx = client.get(f"/api/projects/{PROJECT_ID}/release/docx")
    pdf = client.get(f"/api/projects/{PROJECT_ID}/release/pdf")
    assert docx.status_code == 200
    assert docx.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "filename*=utf-8''" in docx.headers["content-disposition"].lower()
    assert docx.content[:2] == b"PK"
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert "filename*=utf-8''" in pdf.headers["content-disposition"].lower()
    assert pdf.content.startswith(b"%PDF")


def test_required_submission_projection_is_empty_without_fill_form_actions() -> None:
    matrix = {
        "requirements": [
            {
                "requirement_id": "req-technical",
                "applicable_scope": {"applicable": True},
            }
        ]
    }
    assert project_required_submission_items(matrix, {"action_groups": []}) == []


def test_required_submission_projection_uses_natural_titles_and_external_status() -> None:
    matrix = {
        "requirements": [
            {"requirement_id": "req-deviation", "title": "技术偏差要求", "applicable_scope": {"applicable": True}},
            {"requirement_id": "req-resume", "title": "项目团队情况", "applicable_scope": {"applicable": True}},
            {"requirement_id": "req-upload", "title": "专项投标文件要求", "applicable_scope": {"applicable": True}},
            {"requirement_id": "req-not-applicable", "title": "其他标包人员表", "applicable_scope": {"applicable": False}},
        ]
    }
    actionable = {
        "action_groups": [
            {
                "action_group_id": "action-deviation",
                "primary_action_kind": "fill_form",
                "member_requirement_ids": ["req-deviation"],
                "form_or_table_refs": [{"table_id": "technical_deviation_table", "title": "技术偏差表"}],
            },
            {
                "action_group_id": "action-resume",
                "primary_action_kind": "fill_form",
                "member_requirement_ids": ["req-resume"],
                "form_or_table_refs": [{"table_id": "personnel_resume_table", "title": "主要人员简历表"}],
            },
            {
                "action_group_id": "action-upload",
                "primary_action_kind": "fill_form",
                "member_requirement_ids": ["req-upload"],
                "form_or_table_refs": [{"table_id": "special_tender_document", "title": "专项投标文件"}],
            },
            {
                "action_group_id": "action-ignored",
                "primary_action_kind": "fill_form",
                "member_requirement_ids": ["req-not-applicable"],
                "form_or_table_refs": [{"table_id": "personnel_summary_table", "title": "其他标包人员汇总表"}],
            },
        ]
    }

    items = project_required_submission_items(matrix, actionable)
    assert [item.display_title for item in items] == ["技术偏差表", "主要人员简历表", "专项投标文件"]
    assert items[0].description == "正式技术偏差表尚未自动生成，请在提交前完成。"
    assert items[2].submission_kind == "external_upload"
    assert "投标平台另行处理" in items[2].description
    visible_projection = " ".join(f"{item.display_title} {item.description}" for item in items)
    assert "technical_deviation_table" not in visible_projection
    assert "personnel_resume_table" not in visible_projection
    assert "special_tender_document" not in visible_projection


def test_real_sample_projects_required_technical_forms_and_special_upload() -> None:
    requirements_dir = REAL_OUTPUTS / "0425139003001-6582" / "requirements"
    items = project_required_submission_items(
        _load(requirements_dir / "tender_requirement_matrix.json"),
        _load(requirements_dir / "actionable_requirement_index.json"),
    )
    titles = {item.display_title for item in items}
    assert {
        "技术偏差表",
        "近年完成的类似项目情况表",
        "拟委任的主要人员汇总表",
        "主要人员简历表",
        "专项投标文件",
    }.issubset(titles)
    special = next(item for item in items if item.display_title == "专项投标文件")
    assert special.submission_kind == "external_upload"


def test_submission_checklist_does_not_change_published_release_gate(published: dict[str, Any]) -> None:
    checklist = project_required_submission_items(
        {"requirements": [{"requirement_id": "req-1", "title": "技术偏差表", "applicable_scope": {"applicable": True}}]},
        {"action_groups": [{"action_group_id": "action-1", "primary_action_kind": "fill_form", "member_requirement_ids": ["req-1"], "form_or_table_refs": [{"table_id": "technical_deviation_table", "title": "技术偏差表"}]}]},
    )
    service = TechnicalFulltextReleaseService(
        ReadOnlyProjectResolver(published["outputs"]),
        lambda _: checklist,
    )
    status = service.status(PROJECT_ID)
    assert status.status == "published"
    assert status.releaseable is True
    assert status.docx is not None and status.pdf is not None
    assert [item.display_title for item in status.required_submission_items] == ["技术偏差表"]


def test_stale_current_is_not_published(monkeypatch: pytest.MonkeyPatch, published: dict[str, Any]) -> None:
    outputs = published["outputs"]
    root_a = published["root"]
    root_b = outputs / "artifact-sets" / "stage13-new-current"
    shutil.copytree(root_a, root_b)
    shutil.rmtree(root_b / "fulltext" / "release")
    manifest_path = published["manifest"]

    def stale_generation(**kwargs: Any) -> dict[str, Any]:
        manifest = _load(manifest_path)
        manifest["current_artifact_set"] = {
            "fulltext_output_dir": str(root_b.parent.resolve()),
            "output_key": root_b.name,
        }
        _write(manifest_path, manifest)
        kwargs["stale_check"]()
        raise AssertionError("stale_check must fail")

    monkeypatch.setattr("app.web.release_service.generate_release_artifacts", stale_generation)
    with pytest.raises(TechnicalFulltextReleaseStale):
        TechnicalFulltextReleaseService(ReadOnlyProjectResolver(outputs)).publish(PROJECT_ID)
    current = _load(manifest_path)["current_artifact_set"]
    assert current["output_key"] == root_b.name
    status = TechnicalFulltextReleaseService(ReadOnlyProjectResolver(outputs)).status(PROJECT_ID)
    assert status.status == "ready"
    assert status.docx is None and status.pdf is None

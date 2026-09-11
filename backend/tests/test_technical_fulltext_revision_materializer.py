from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.technical_fulltext_auditor import TechnicalFulltextAuditor
from app.technical_fulltext_composer import inspect_technical_fulltext, sha256_file
from app.technical_fulltext_revision_materializer import (
    TechnicalFulltextRevisionMaterializationError,
    materialize_single_section_revision,
)
from test_technical_fulltext_composer import build_case, compose


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def committed_source(tmp_path: Path) -> dict[str, Any]:
    case = build_case(tmp_path, output_key="revision_source")
    composed = compose(case)
    planning = {
        "response_outline": {"schema_version": "technical_response_outline_v1", "chapters": []},
        "task_information": {"schema_version": "task_information_requirements_v1", "items": []},
        "draft_units": {
            "schema_version": "draft_units_v1",
            "content_location_plan": {},
            "draft_units": [],
        },
    }
    planning_paths = {
        name: write_json(tmp_path / "planning" / f"{name}.json", payload)
        for name, payload in planning.items()
    }
    source_fulltext_path = Path(composed["outputs"]["technical_fulltext_json"])
    source_run_path = source_fulltext_path.parent / "technical_fulltext.run.json"
    input_paths = {
        "writing_outline_path": str(case["outline_path"].resolve()),
        "response_outline_path": str(planning_paths["response_outline"].resolve()),
        "task_information_path": str(planning_paths["task_information"].resolve()),
        "draft_units_path": str(planning_paths["draft_units"].resolve()),
        "confirmed_project_facts_path": None,
    }
    input_hashes = {
        key.replace("_path", "_sha256"): sha256_file(path)
        for key, path in input_paths.items()
        if path is not None
    }
    input_hashes["confirmed_project_facts_sha256"] = None
    write_json(
        source_run_path,
        {
            "schema_version": "technical_fulltext_run_v1",
            "status": "completed",
            "production_status": "complete",
            "composition_status": "complete",
            "output_key": "revision_source",
            "input_paths": input_paths,
            "input_hashes": input_hashes,
            "chapter_count_expected": 2,
            "chapter_count_completed": 2,
            "chapter_count_submission_ready": 2,
            "chapter_count_blocked": 0,
            "chapter_count_failed": 0,
            "chapters": [],
            "outputs": composed["outputs"],
        },
    )
    handoff_path = write_json(
        source_fulltext_path.parent / "review" / "technical_fulltext.stage13_handoff.json",
        {
            "schema_version": "technical_fulltext_stage13_handoff_v1",
            "artifact_identity": {
                "handoff_id": "S13H-0123456789ABCDEF",
                "document_id": "DOC-STRICT",
                "output_key": "revision_source",
                "status": "ready",
            },
            "source_lineage": {},
            "finalization_ref": {"finalization_id": "THF-1", "path": "finalization.json"},
            "revision_candidates": [{}],
            "approved_facts": [],
            "approved_material_refs": [],
            "prohibited_inferences": [],
            "accepted_risk_items": [],
            "summary": {
                "revision_candidate_count": 1,
                "release_blocking_candidate_count": 0,
                "accepted_risk_count": 0,
            },
            "pre_stage13_dependencies": [],
            "generated_at": "2026-09-11T00:00:00+00:00",
        },
    )
    return {
        "case": case,
        "composed": composed,
        "fulltext_path": source_fulltext_path,
        "run_path": source_run_path,
        "handoff_path": handoff_path,
    }


def replacement_for(source: dict[str, Any]) -> dict[str, Any]:
    replacement = deepcopy(source["chapters"][0]["sections"][0])
    replacement["content"] = "T33 replacement section content"
    return replacement


def test_single_section_materialization_preserves_source_and_other_content(
    tmp_path: Path,
) -> None:
    fixture = committed_source(tmp_path)
    source = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))
    source_files = tuple(fixture["fulltext_path"].parent.iterdir())
    before = {path: (path.read_bytes(), sha256_file(path)) for path in source_files if path.is_file()}

    result = materialize_single_section_revision(
        source_fulltext_path=fixture["fulltext_path"],
        target_section_id="WC-010-01",
        replacement_section=replacement_for(source),
        output_dir=fixture["case"]["output_dir"],
        output_key="revision_child",
        stage12_handoff_path=fixture["handoff_path"],
    )

    revised = result["fulltext"]
    assert revised["output_key"] == "revision_child"
    assert revised["document_id"] == "technical-fulltext:revision_child"
    assert revised["chapter_order"] == source["chapter_order"]
    assert all(chapter["source"]["output_key"] == "revision_child" for chapter in revised["chapters"])
    assert all("candidate_json_path" not in chapter["source"] for chapter in revised["chapters"])
    assert revised["chapters"][0]["sections"][0] == replacement_for(source)
    assert revised["chapters"][0]["sections"][1:] == source["chapters"][0]["sections"][1:]
    assert revised["chapters"][1]["sections"] == source["chapters"][1]["sections"]
    assert [item["chapter_id"] for item in revised["chapters"]] == [
        item["chapter_id"] for item in source["chapters"]
    ]
    assert {
        key: value
        for key, value in revised["chapters"][1].items()
        if key != "source"
    } == {
        key: value
        for key, value in source["chapters"][1].items()
        if key != "source"
    }
    assert {
        path: (path.read_bytes(), sha256_file(path))
        for path in source_files
        if path.is_file()
    } == before
    assert inspect_technical_fulltext(result["outputs"]["technical_fulltext_json"])[
        "output_key"
    ] == "revision_child"
    for artifact_key in (
        "chapter_production_index",
        "technical_fulltext_build_manifest",
        "technical_fulltext_run",
        "technical_fulltext_commit",
    ):
        artifact = json.loads(Path(result["outputs"][artifact_key]).read_text(encoding="utf-8"))
        assert artifact["output_key"] == "revision_child"


def test_materialization_records_parent_and_handoff_provenance(tmp_path: Path) -> None:
    fixture = committed_source(tmp_path)
    source = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))

    result = materialize_single_section_revision(
        source_fulltext_path=fixture["fulltext_path"],
        target_section_id="WC-010-01",
        replacement_section=replacement_for(source),
        output_dir=fixture["case"]["output_dir"],
        output_key="revision_provenance",
        stage12_handoff_path=fixture["handoff_path"],
    )

    commit = json.loads(
        Path(result["outputs"]["technical_fulltext_commit"]).read_text(encoding="utf-8")
    )
    provenance = commit["revision_provenance"]
    assert provenance["parent_stage10"] == {
        "document_id": "DOC-STRICT",
        "output_key": "revision_source",
        "fulltext_path": str(fixture["fulltext_path"].resolve()),
        "fulltext_sha256": sha256_file(fixture["fulltext_path"]),
        "commit_path": str((fixture["fulltext_path"].parent / "technical_fulltext.commit.json").resolve()),
        "commit_sha256": sha256_file(fixture["fulltext_path"].parent / "technical_fulltext.commit.json"),
        "run_path": str(fixture["run_path"].resolve()),
        "run_sha256": sha256_file(fixture["run_path"]),
    }
    assert provenance["stage12_handoff"] == {
        "handoff_id": "S13H-0123456789ABCDEF",
        "path": str(fixture["handoff_path"].resolve()),
        "sha256": sha256_file(fixture["handoff_path"]),
    }
    assert provenance["target_section_id"] == "WC-010-01"


@pytest.mark.parametrize("target_section_id", ["WC-404-01", ""])
def test_invalid_target_fails_without_creating_revision(
    tmp_path: Path, target_section_id: str
) -> None:
    fixture = committed_source(tmp_path)
    source = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))
    replacement = replacement_for(source)
    replacement["section_id"] = target_section_id

    with pytest.raises(
        TechnicalFulltextRevisionMaterializationError,
        match="must match exactly one source section",
    ):
        materialize_single_section_revision(
            source_fulltext_path=fixture["fulltext_path"],
            target_section_id=target_section_id,
            replacement_section=replacement,
            output_dir=fixture["case"]["output_dir"],
            output_key="invalid_target",
            stage12_handoff_path=fixture["handoff_path"],
        )
    assert not (fixture["case"]["output_dir"] / "invalid_target").exists()


def test_replacement_identity_mismatch_fails_fast(tmp_path: Path) -> None:
    fixture = committed_source(tmp_path)
    source = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))
    replacement = replacement_for(source)
    replacement["section_id"] = "WC-002-01"

    with pytest.raises(
        TechnicalFulltextRevisionMaterializationError,
        match="replacement section_id does not match",
    ):
        materialize_single_section_revision(
            source_fulltext_path=fixture["fulltext_path"],
            target_section_id="WC-010-01",
            replacement_section=replacement,
            output_dir=fixture["case"]["output_dir"],
            output_key="identity_mismatch",
            stage12_handoff_path=fixture["handoff_path"],
        )


def test_duplicate_target_fails_without_creating_revision(tmp_path: Path) -> None:
    fixture = committed_source(tmp_path)
    source = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))
    duplicate = deepcopy(source["chapters"][0]["sections"][0])
    source["chapters"][1]["sections"].append(duplicate)
    write_json(fixture["fulltext_path"], source)
    commit_path = fixture["fulltext_path"].parent / "technical_fulltext.commit.json"
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    commit["technical_fulltext_json_sha256"] = sha256_file(fixture["fulltext_path"])
    write_json(commit_path, commit)

    with pytest.raises(
        TechnicalFulltextRevisionMaterializationError,
        match="must match exactly one source section; found 2",
    ):
        materialize_single_section_revision(
            source_fulltext_path=fixture["fulltext_path"],
            target_section_id="WC-010-01",
            replacement_section=replacement_for(source),
            output_dir=fixture["case"]["output_dir"],
            output_key="duplicate_target",
            stage12_handoff_path=fixture["handoff_path"],
        )
    assert not (fixture["case"]["output_dir"] / "duplicate_target").exists()


def test_existing_stage11_accepts_materialized_revision(tmp_path: Path) -> None:
    fixture = committed_source(tmp_path)
    source = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))
    result = materialize_single_section_revision(
        source_fulltext_path=fixture["fulltext_path"],
        target_section_id="WC-010-01",
        replacement_section=replacement_for(source),
        output_dir=fixture["case"]["output_dir"],
        output_key="revision_stage11",
        stage12_handoff_path=fixture["handoff_path"],
    )

    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=result["outputs"]["technical_fulltext_json"]
    )["audit"]

    assert audit["status"] == "completed"
    assert audit["output_key"] == "revision_stage11"
    assert audit["source_run"]["sha256"] == result["outputs"][
        "technical_fulltext_run_sha256"
    ]

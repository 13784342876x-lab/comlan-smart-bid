from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import cli as cli_module
import app.bid_pipeline_runner as bid_pipeline_module
from app.bid_pipeline_runner import (
    ANALYSIS_STOP_STAGE,
    DEFAULT_COMPANY_OUTLINE,
    FINAL_STATUS,
    STAGE_DEFINITIONS,
    WRITING_PLAN_STOP_STAGE,
    BidPipelineRunner,
    StageResult,
)


def write_json(path: Path, payload: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload or {"ok": True}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inputs(tmp_path: Path) -> dict[str, Path]:
    source_dir = tmp_path / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    tender = source_dir / "tender.txt"
    score = source_dir / "score.txt"
    tender.write_text("tender source", encoding="utf-8")
    score.write_text("score source", encoding="utf-8")
    return {"source_dir": source_dir, "tender": tender, "score": score}


def artifact_result(tmp_path: Path, stage_id: str, names: list[str] | None = None) -> StageResult:
    paths = {}
    for name in names or [f"{stage_id}_artifact"]:
        paths[name] = str(write_json(tmp_path / "service_outputs" / stage_id / f"{name}.json"))
    return StageResult(artifacts=paths, outputs={"stage": stage_id})


def successful_overrides(tmp_path: Path, calls: dict[str, int] | None = None) -> dict[str, Any]:
    required_names = {
        "ingest_sources": ["ingested_tender_file", "ingested_score_file"],
        "build_project_response_context": ["technical_response_context"],
        "build_technical_response_outline": ["technical_response_outline_json"],
        "build_technical_writing_outline": ["technical_writing_outline_json"],
        "build_writing_tasks": [
            "technical_writing_outline_json",
            "task_information_requirements_json",
            "draft_units_json",
        ],
        "generate_fulltext": ["technical_fulltext_json", "fulltext_run", "chapter_production_index"],
        "hygiene_fulltext": ["technical_fulltext_cleaned_json"],
        "audit_fulltext": ["technical_fulltext_audit_json"],
        "semantic_audit": ["technical_fulltext_audit_json"],
        "scoring_assessment": ["technical_fulltext_audit_json", "technical_fulltext_scoring_assessment_json"],
        "finalize_audit": ["technical_fulltext_audit_json"],
        "assemble_stage12a": ["technical_fulltext_review_package_json"],
    }
    overrides: dict[str, Any] = {}
    for stage_id, _ in STAGE_DEFINITIONS:
        if stage_id == "validate_inputs":
            continue

        def handler(state: dict[str, Any], current: str = stage_id) -> StageResult:
            if calls is not None:
                calls[current] = calls.get(current, 0) + 1
            result = artifact_result(tmp_path, current, required_names.get(current))
            if current == "assemble_stage12a":
                result.outputs = {"status": "assembled", "review_package_id": "TRP-TEST"}
            return result

        overrides[stage_id] = handler
    return overrides


def source_stage_test_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[BidPipelineRunner, dict[str, Any]]:
    metadata: dict[str, Any] = {"files": []}

    class FakeMetadataStore:
        def __init__(self, path: Path | None = None) -> None:
            self.path = path

        def load(self) -> dict[str, Any]:
            return metadata

    class FakeKnowledgeBaseService:
        def __init__(self, metadata: Any = None) -> None:
            self.metadata = metadata

        def ingest_file(self, path: Path, category: str, tags: list[str]) -> dict[str, Any]:
            file_id = f"file_test_{len(metadata['files']) + 1:03d}"
            record = {
                "id": file_id,
                "original_name": path.name,
                "category": category,
                "tags": list(tags),
                "storage_key": str(path.resolve()),
                "parsed_text": "",
                "tables": [],
            }
            metadata["files"].append(record)
            if self.metadata is not None and self.metadata.path is not None:
                write_json(self.metadata.path, metadata)
            return record

        def parse_file(self, file_id: str) -> dict[str, Any]:
            record = next(item for item in metadata["files"] if item["id"] == file_id)
            record["parsed_text"] = Path(record["storage_key"]).read_text(encoding="utf-8")
            record["parse_ok"] = True
            if self.metadata is not None and self.metadata.path is not None:
                write_json(self.metadata.path, metadata)
            return {"ok": True, "record": record, "warnings": [], "message": ""}

        def classify_file(self, file_id: str) -> dict[str, Any]:
            record = next(item for item in metadata["files"] if item["id"] == file_id)
            classification = {
                "source_file_id": file_id,
                "category": record.get("category", "待分类"),
                "document_type": "测试分类",
                "material_group": "tender_source",
                "source_type": "tender_source",
                "material_types": ["tender_document"],
                "tags": ["tender_document"],
                "confidence": 0.5,
                "is_bundle": False,
            }
            record["classification"] = classification
            if self.metadata is not None and self.metadata.path is not None:
                write_json(self.metadata.path, metadata)
            return classification

        def index_file(self, file_id: str) -> dict[str, Any]:
            raise AssertionError("run-bid must not index source files into Chroma")

    monkeypatch.setattr(bid_pipeline_module, "MetadataStore", FakeMetadataStore)
    monkeypatch.setattr(bid_pipeline_module, "KnowledgeBaseService", FakeKnowledgeBaseService)
    monkeypatch.setattr(bid_pipeline_module, "resolve_storage_key", lambda key: Path(key))
    overrides = successful_overrides(tmp_path)
    overrides.pop("ingest_sources")
    overrides.pop("parse_sources")
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=overrides,
    )
    return runner, metadata


def run_successful_pipeline(tmp_path: Path, *, calls: dict[str, int] | None = None) -> tuple[BidPipelineRunner, dict[str, Any]]:
    inputs = source_inputs(tmp_path)
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=successful_overrides(tmp_path, calls),
    )
    state = runner.run(
        source_dir=inputs["source_dir"],
        lot_name="LOT-001",
        run_id="bid-test-complete",
    )
    return runner, state


def test_complete_pipeline_reaches_human_review_ready(tmp_path: Path) -> None:
    runner, state = run_successful_pipeline(tmp_path)

    assert state["pipeline_status"] == FINAL_STATUS
    assert state["final_status"] == FINAL_STATUS
    assert all(stage["status"] == "completed" for stage in state["stages"])
    assert all(stage["inputs"] for stage in state["stages"])
    assert "SUBMISSION_READY" not in json.dumps(state)
    assert state["run_id"] != state["routing"]["fulltext_output_key"]
    assert Path(state["routing"]["pipeline_state_path"]).is_file()
    assert runner.status(state["run_id"])["pipeline_status"] == FINAL_STATUS


def test_single_tender_source_uses_same_score_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "single-project"
    source_dir.mkdir()
    (source_dir / "招标文件.txt").write_text(
        "投标人须知前附表\n响应文件组成\n资格条件\n否决\n评标办法\n评分表\n评分标准",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(
        source_dir=source_dir,
        lot_name="LOT-SINGLE",
        run_id="bid-single-source",
    )

    manifest = state["source_manifest"]
    assert state["pipeline_status"] == FINAL_STATUS
    assert manifest["tender_file_id"] == manifest["score_file_id"]
    assert manifest["selection_status"] == "selected"
    assert manifest["files"][0]["parsed_status"] == "success"
    assert manifest["files"][0]["selected_as"] == ["tender", "score"]
    assert "selected_as_score_anchor" in manifest["files"][0]["selection_reason"]
    assert state["inputs"]["lot_resolution_status"] == "explicit"


def test_single_project_without_lot_is_not_applicable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "no-lot-project"
    source_dir.mkdir()
    (source_dir / "RFP_Project.pdf.txt").write_text(
        "Request for Proposal\nResponse Instructions\nSupplier Qualification\n"
        "Evaluation / Selection Process\nResponse Specifications",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(source_dir=source_dir, run_id="bid-no-lot-project")

    assert state["pipeline_status"] == FINAL_STATUS
    assert state["inputs"]["lot_name"] is None
    assert state["inputs"]["lot_resolution_status"] == "not_applicable"
    assert state["inputs"]["lot_candidates"] == []
    assert state["routing"]["planning_output_key"] == state["run_id"]


def test_single_file_without_lot_and_embedded_scoring_uses_same_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "no-lot-embedded-score"
    source_dir.mkdir()
    (source_dir / "RFP_Project.txt").write_text(
        "Request for Proposal\nResponse Instructions\nSupplier Qualification\n"
        "Evaluation criteria and scoring criteria are included in this document.\n"
        "Response Specifications",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(source_dir=source_dir, run_id="bid-no-lot-embedded-score")
    manifest = state["source_manifest"]

    assert state["inputs"]["lot_name"] is None
    assert state["inputs"]["lot_resolution_status"] == "not_applicable"
    assert manifest["tender_file_id"] == manifest["score_file_id"]


def test_independent_score_source_without_lot_is_scoped_without_fake_lot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "no-lot-independent-score"
    source_dir.mkdir()
    (source_dir / "RFP_Project.txt").write_text(
        "Request for Proposal\nResponse Instructions\nSupplier Qualification\nResponse Specifications",
        encoding="utf-8",
    )
    (source_dir / "评分表.txt").write_text(
        "评分表\n商务评分标准\n技术评分标准\n分值权重构成",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(source_dir=source_dir, run_id="bid-no-lot-independent-score")
    manifest = state["source_manifest"]
    entries = {item["original_name"]: item for item in manifest["files"]}

    assert state["inputs"]["lot_name"] is None
    assert state["inputs"]["lot_resolution_status"] == "not_applicable"
    assert manifest["score_file_id"] == entries["评分表.txt"]["file_id"]


def test_unique_lot_is_resolved_from_scoped_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "unique-lot"
    source_dir.mkdir()
    (source_dir / "招标文件.txt").write_text(
        "投标人须知前附表\n标段名称：软件许可服务\n响应文件组成\n资格条件\n否决\n签字盖章\n服务期限\n评分标准",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(source_dir=source_dir, run_id="bid-unique-lot")

    assert state["inputs"]["lot_name"] == "软件许可服务"
    assert state["inputs"]["lot_resolution_status"] == "resolved_unique"
    assert len(state["inputs"]["lot_candidates"]) == 1


def test_multiple_lots_stop_at_gate_and_persist_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "multiple-lots"
    source_dir.mkdir()
    (source_dir / "招标文件.txt").write_text(
        "投标人须知前附表\n标段名称：软件许可服务\n标段名称：技术支持服务\n响应文件组成\n资格条件\n否决\n签字盖章\n服务期限",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(source_dir=source_dir, run_id="bid-multiple-lots")

    assert state["pipeline_status"] == "FAILED"
    assert state["failure"]["stage_id"] == "parse_sources"
    assert state["inputs"]["lot_name"] is None
    assert state["inputs"]["lot_resolution_status"] == "ambiguous"
    assert {item["value"] for item in state["inputs"]["lot_candidates"]} == {
        "软件许可服务",
        "技术支持服务",
    }
    assert "pass --lot-name" in state["failure"]["message"]


def test_explicit_lot_name_overrides_multiple_document_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "explicit-lot"
    source_dir.mkdir()
    (source_dir / "招标文件.txt").write_text(
        "投标人须知前附表\n标段名称：软件许可服务\n标段名称：技术支持服务\n响应文件组成\n资格条件\n否决\n签字盖章\n服务期限",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(
        source_dir=source_dir,
        lot_name="技术支持服务",
        run_id="bid-explicit-lot",
    )

    assert state["pipeline_status"] == FINAL_STATUS
    assert state["inputs"]["lot_name"] == "技术支持服务"
    assert state["inputs"]["lot_resolution_status"] == "explicit"


def test_multi_source_directory_builds_role_aware_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "multi-project"
    source_dir.mkdir()
    sources = {
        "招标文件.txt": "投标人须知前附表\n响应文件组成\n资格条件\n否决\n签字盖章\n服务期限",
        "技术规范书.txt": "技术规范书\n技术要求\n服务内容",
        "评分表.txt": "评标办法\n评分表\n技术评分标准",
        "答疑文件.txt": "答疑文件\n对技术要求的补充说明",
    }
    for name, text in sources.items():
        (source_dir / name).write_text(text, encoding="utf-8")
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(
        source_dir=source_dir,
        lot_name="LOT-MULTI",
        run_id="bid-multi-source",
    )

    entries = {item["original_name"]: item for item in state["source_manifest"]["files"]}
    assert set(entries) == set(sources)
    assert entries["招标文件.txt"]["detected_role"] == "full_tender_document"
    assert entries["技术规范书.txt"]["detected_role"] == "technical_spec"
    assert entries["评分表.txt"]["detected_role"] == "score_table"
    assert entries["答疑文件.txt"]["detected_role"] == "tender_attachment"
    assert entries["招标文件.txt"]["selected_as"] == ["tender"]
    assert entries["评分表.txt"]["selected_as"] == ["score"]
    assert all(item["parsed_status"] == "success" for item in entries.values())


def test_ambiguous_tender_anchors_are_persisted_for_manual_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "ambiguous-project"
    source_dir.mkdir()
    common = (
        "Request for Proposal\nResponse Instructions\nSupplier Qualification\n"
        "Evaluation / Selection Process\nResponse Specifications\n"
    )
    (source_dir / "RFP_Project_A.txt").write_text(common + ("ALPHA " * 2200), encoding="utf-8")
    (source_dir / "RFP_Project_B.txt").write_text(common + ("BETA " * 2400), encoding="utf-8")
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(
        source_dir=source_dir,
        lot_name="LOT-AMBIGUOUS",
        run_id="bid-ambiguous-source",
    )

    manifest = state["source_manifest"]
    assert state["pipeline_status"] == "FAILED"
    assert manifest["selection_status"] == "ambiguous"
    assert manifest["tender_file_id"] is None
    assert "manual selection required" in manifest["selection_failure"]
    assert len(manifest["anchor_candidates"]["tender"]) == 2
    assert all(
        {"file_id", "original_name", "role", "score", "reasons"} <= set(item)
        for item in manifest["anchor_candidates"]["tender"]
    )


def test_source_manifests_do_not_cross_project_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_dir = tmp_path / "project-a"
    second_dir = tmp_path / "project-b"
    first_dir.mkdir()
    second_dir.mkdir()
    content = "投标人须知前附表\n响应文件组成\n资格条件\n否决\n评标办法\n评分表"
    (first_dir / "A招标文件.txt").write_text(content, encoding="utf-8")
    (second_dir / "B招标文件.txt").write_text(content, encoding="utf-8")
    runner, metadata = source_stage_test_runner(tmp_path, monkeypatch)

    first = runner.run(source_dir=first_dir, lot_name="LOT-A", run_id="bid-project-a")
    second = runner.run(source_dir=second_dir, lot_name="LOT-B", run_id="bid-project-b")

    first_ids = {item["file_id"] for item in first["source_manifest"]["files"]}
    second_ids = {item["file_id"] for item in second["source_manifest"]["files"]}
    assert first_ids.isdisjoint(second_ids)
    assert {item["id"] for item in runner._source_metadata(first)["files"]} == first_ids
    assert {item["id"] for item in runner._source_metadata(second)["files"]} == second_ids
    assert first["routing"]["source_metadata_path"] != second["routing"]["source_metadata_path"]
    assert len(metadata["files"]) == 2


def test_company_outline_is_loaded_from_fixed_project_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "company-default"
    source_dir.mkdir()
    (source_dir / "招标文件.txt").write_text(
        "投标人须知前附表\n响应文件组成\n资格条件\n否决\n评标办法\n评分表",
        encoding="utf-8",
    )
    runner, _ = source_stage_test_runner(tmp_path, monkeypatch)

    state = runner.run(
        source_dir=source_dir,
        lot_name="LOT-COMPANY",
        run_id="bid-company-default",
    )

    validate = next(item for item in state["stages"] if item["stage_id"] == "validate_inputs")
    assert Path(state["inputs"]["company_outline"]) == DEFAULT_COMPANY_OUTLINE
    assert Path(validate["artifacts"]["company_outline"]) == DEFAULT_COMPANY_OUTLINE
    assert validate["details"]["company_outline_validation"]["valid"] is True


def test_resume_continues_from_failed_stage(tmp_path: Path) -> None:
    inputs = source_inputs(tmp_path)
    calls: dict[str, int] = {}
    overrides = successful_overrides(tmp_path, calls)
    normal = overrides["build_requirement_analysis"]
    requirement_attempts = 0

    def fail_once(state: dict[str, Any]) -> StageResult:
        nonlocal requirement_attempts
        requirement_attempts += 1
        if requirement_attempts == 1:
            raise RuntimeError("fixture requirement failure")
        return normal(state)

    overrides["build_requirement_analysis"] = fail_once
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=overrides,
    )
    first = runner.run(
        source_dir=inputs["source_dir"],
        lot_name="LOT-001",
        run_id="bid-test-resume",
    )

    assert first["pipeline_status"] == "FAILED"
    assert first["failure"]["stage_id"] == "build_requirement_analysis"
    resumed = runner.resume(first["run_id"])

    assert resumed["pipeline_status"] == FINAL_STATUS
    assert calls["ingest_sources"] == 1
    assert requirement_attempts == 2
    assert calls["build_requirement_analysis"] == 1


def test_completed_stages_are_skipped_without_reexecution(tmp_path: Path) -> None:
    calls: dict[str, int] = {}
    runner, state = run_successful_pipeline(tmp_path, calls=calls)
    attempts_before = {item["stage_id"]: item["attempts"] for item in state["stages"]}

    resumed = runner.resume(state["run_id"])

    assert calls and all(count == 1 for count in calls.values())
    assert {item["stage_id"]: item["attempts"] for item in resumed["stages"]} == attempts_before
    assert all(item["skip_count"] == 1 for item in resumed["stages"])


@pytest.mark.parametrize("stop_stage", [ANALYSIS_STOP_STAGE, WRITING_PLAN_STOP_STAGE])
def test_continue_full_pipeline_reuses_paused_run_and_completes_remaining_stages(
    tmp_path: Path, stop_stage: str
) -> None:
    calls: dict[str, int] = {}
    inputs = source_inputs(tmp_path)
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=successful_overrides(tmp_path, calls),
    )
    paused = runner.run(
        source_dir=inputs["source_dir"],
        lot_name="LOT-CONTINUE",
        run_id=f"bid-continue-{stop_stage}",
        stop_after_stage=stop_stage,
    )

    completed = runner.continue_full_pipeline(paused["run_id"])

    assert completed["run_id"] == paused["run_id"]
    assert completed["inputs"]["stop_after_stage"] is None
    assert completed["pipeline_status"] == FINAL_STATUS
    assert all(item["status"] == "completed" for item in completed["stages"])
    assert all(count == 1 for count in calls.values())


def test_continue_full_pipeline_resumes_failed_stage_on_same_run(tmp_path: Path) -> None:
    inputs = source_inputs(tmp_path)
    overrides = successful_overrides(tmp_path)
    original = overrides["generate_fulltext"]
    attempts = 0

    def fail_once(state: dict[str, Any]) -> StageResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("full generation fixture failure")
        return original(state)

    overrides["generate_fulltext"] = fail_once
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs", stream_model=False, stage_overrides=overrides
    )
    paused = runner.run(
        source_dir=inputs["source_dir"],
        lot_name="LOT-RESUME-FULL",
        run_id="bid-continue-failed-full",
        stop_after_stage=WRITING_PLAN_STOP_STAGE,
    )
    failed = runner.continue_full_pipeline(paused["run_id"])
    resumed = runner.continue_full_pipeline(paused["run_id"])

    assert failed["pipeline_status"] == "FAILED"
    assert failed["failure"]["stage_id"] == "generate_fulltext"
    assert resumed["pipeline_status"] == FINAL_STATUS
    assert resumed["run_id"] == paused["run_id"]
    assert attempts == 2


def test_missing_source_dir_is_saved_as_pipeline_failure(tmp_path: Path) -> None:
    missing = tmp_path / "missing-sources"
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=successful_overrides(tmp_path),
    )

    state = runner.run(
        source_dir=missing,
        lot_name="LOT-001",
        run_id="bid-test-missing",
    )

    assert state["pipeline_status"] == "FAILED"
    assert state["failure"]["stage_id"] == "validate_inputs"
    assert state["failure"]["error_type"] == "FileNotFoundError"
    persisted = runner.load_state(state["run_id"])
    assert persisted["failure"] == state["failure"]


def test_stage_failure_reason_and_attempt_are_persisted(tmp_path: Path) -> None:
    inputs = source_inputs(tmp_path)
    overrides = successful_overrides(tmp_path)

    def explode(state: dict[str, Any]) -> StageResult:
        raise ValueError("score analysis exploded")

    overrides["build_scoring_analysis"] = explode
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=overrides,
    )
    state = runner.run(
        source_dir=inputs["source_dir"],
        lot_name="LOT-001",
        run_id="bid-test-stage-failure",
    )

    failed = next(item for item in state["stages"] if item["stage_id"] == "build_scoring_analysis")
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1
    assert failed["error"]["error_type"] == "ValueError"
    assert "score analysis exploded" in failed["error"]["message"]


@pytest.mark.parametrize(
    ("raised", "error_type"),
    [
        (KeyboardInterrupt(), "KeyboardInterrupt"),
        (SystemExit(2), "SystemExit"),
    ],
)
def test_base_exception_stage_failure_is_persisted_then_reraised(
    tmp_path: Path,
    raised: BaseException,
    error_type: str,
) -> None:
    inputs = source_inputs(tmp_path)

    def abort_stage(state: dict[str, Any]) -> StageResult:
        raise raised

    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides={"validate_inputs": abort_stage},
    )

    with pytest.raises(type(raised)) as exc_info:
        runner.run(
            source_dir=inputs["source_dir"],
            lot_name="LOT-001",
            run_id=f"bid-test-{error_type.lower()}",
        )

    if isinstance(raised, SystemExit):
        assert exc_info.value.code == 2
    persisted = runner.load_state(f"bid-test-{error_type.lower()}")
    failed = next(item for item in persisted["stages"] if item["stage_id"] == "validate_inputs")
    assert persisted["pipeline_status"] == "FAILED"
    assert persisted["failure"]["stage_id"] == "validate_inputs"
    assert failed["status"] == "failed"
    assert failed["error"]["error_type"] == error_type
    assert failed["error"]["failed_at"]
    assert persisted["updated_at"] >= failed["error"]["failed_at"]


def test_source_manifest_duplicate_file_id_merges_package_lineage() -> None:
    direct = {
        "file_id": "file-shared",
        "original_name": "tender.docx",
        "path": "D:/sources/tender.docx",
        "stored_path": "D:/storage/tender.docx",
        "selection_reason": "included_by_source_dir_boundary",
        "selected_as": ["tender"],
    }
    package = {
        "file_id": "file-shared",
        "original_name": "tender.docx",
        "path": "D:/run/expanded/archive/tender.docx",
        "stored_path": "D:/storage/tender.docx",
        "source_package_id": "pkg-001",
        "parent_archive_id": "archive-001",
        "archive_internal_path": "nested/tender.docx",
        "archive_depth": 2,
        "source_sha256": "abc123",
        "file_type": "docx",
        "logical_source_id": "source-entry-001",
        "extraction_status": "ingested_parsed",
        "selected_as": ["score"],
    }

    manifest_files = BidPipelineRunner._deduplicate_source_manifest_entries([direct, package])

    assert len(manifest_files) == 1
    merged = manifest_files[0]
    assert merged["file_id"] == "file-shared"
    assert merged["path"] == "D:/sources/tender.docx"
    assert merged["source_package_id"] == "pkg-001"
    assert merged["parent_archive_id"] == "archive-001"
    assert merged["archive_internal_path"] == "nested/tender.docx"
    assert merged["selected_as"] == ["tender", "score"]


class PartialFulltextService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.run_calls = 0
        self.retry_calls: list[str] = []
        self.recovery_tender_metadata: dict[str, Any] | None = None
        self.success_candidate = self.root / "drafts" / "CH-OK.candidate.json"
        self.failed_candidate = self.root / "drafts" / "CH-FAIL.candidate.json"
        self.index = self.root / "fulltext" / "chapter_production_index.json"
        self.run_path = self.root / "fulltext" / "technical_fulltext.run.json"
        self.events = self.root / "fulltext" / "technical_fulltext.events.jsonl"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.run_calls += 1
        write_json(self.success_candidate, {"chapter_id": "CH-OK", "content": "preserve me"})
        self.failed_candidate.parent.mkdir(parents=True, exist_ok=True)
        self.events.parent.mkdir(parents=True, exist_ok=True)
        self.events.write_text('{"event":"failed"}\n', encoding="utf-8")
        index = {
            "schema_version": "technical_fulltext_chapter_production_index_v1",
            "production_status": "incomplete",
            "chapters": [
                {
                    "chapter_id": "CH-OK",
                    "status": "completed",
                    "candidate_json_sha256": sha256(self.success_candidate),
                    "run_json_sha256": "run-ok",
                },
                {"chapter_id": "CH-FAIL", "status": "failed"},
            ],
        }
        write_json(self.index, index)
        write_json(self.run_path, {"status": "failed"})
        return {
            "status": "failed",
            "production_status": "incomplete",
            "composition_status": "failed",
            "chapter_count_expected": 2,
            "chapter_count_completed": 1,
            "chapter_count_failed": 1,
            "chapters": index["chapters"],
            "failures": [{"chapter_id": "CH-FAIL"}],
            "manifest_path": None,
            "run_path": str(self.run_path),
            "outputs": {
                "chapter_production_index": str(self.index),
                "technical_fulltext_json": None,
                "technical_fulltext_preview": None,
                "technical_fulltext_commit": None,
                "fulltext_events": str(self.events),
            },
        }

    def inspect_existing_run(self, *, output_key: str) -> dict[str, Any]:
        if not self.index.is_file() or not self.run_path.is_file():
            return {"exists": False, "output_key": output_key}
        return {
            "exists": True,
            "output_key": output_key,
            "index": json.loads(self.index.read_text(encoding="utf-8")),
            "run": json.loads(self.run_path.read_text(encoding="utf-8")),
        }

    def recover_existing_run(
        self,
        *,
        output_key: str,
        stream_model: bool,
        tender_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.recovery_tender_metadata = tender_metadata
        index = self.inspect_existing_run(output_key=output_key)["index"]
        failed = [item["chapter_id"] for item in index["chapters"] if item["status"] == "failed"]
        assert failed == ["CH-FAIL"]
        result = self.retry_failed_chapter(
            output_key=output_key,
            chapter_id=failed[0],
            stream_model=stream_model,
            tender_metadata=tender_metadata,
        )
        result["recovery_mode"] = "retry_failed_chapter"
        return result

    def retry_failed_chapter(
        self,
        *,
        output_key: str,
        chapter_id: str,
        stream_model: bool,
        tender_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.retry_calls.append(chapter_id)
        before = sha256(self.success_candidate)
        write_json(self.failed_candidate, {"chapter_id": chapter_id, "content": "recovered"})
        fulltext = write_json(self.root / "fulltext" / "technical_fulltext.json", {"complete": True})
        preview = self.root / "fulltext" / "technical_fulltext.preview.md"
        preview.write_text("complete", encoding="utf-8")
        commit = write_json(self.root / "fulltext" / "technical_fulltext.commit.json", {"ok": True})
        manifest = write_json(self.root / "fulltext" / "technical_fulltext_build_manifest.json", {"ok": True})
        index = {
            "schema_version": "technical_fulltext_chapter_production_index_v1",
            "production_status": "complete",
            "chapters": [
                {
                    "chapter_id": "CH-OK",
                    "status": "completed",
                    "candidate_json_sha256": before,
                    "run_json_sha256": "run-ok",
                },
                {
                    "chapter_id": "CH-FAIL",
                    "status": "completed",
                    "candidate_json_sha256": sha256(self.failed_candidate),
                    "run_json_sha256": "run-recovered",
                },
            ],
        }
        write_json(self.index, index)
        write_json(self.run_path, {"status": "completed"})
        assert sha256(self.success_candidate) == before
        return {
            "status": "completed",
            "production_status": "complete",
            "composition_status": "complete",
            "chapter_count_expected": 2,
            "chapter_count_completed": 2,
            "chapter_count_failed": 0,
            "chapters": index["chapters"],
            "failures": [],
            "manifest_path": str(manifest),
            "run_path": str(self.run_path),
            "outputs": {
                "chapter_production_index": str(self.index),
                "technical_fulltext_json": str(fulltext),
                "technical_fulltext_preview": str(preview),
                "technical_fulltext_commit": str(commit),
                "fulltext_events": str(self.events),
            },
        }


class PartialFulltextFactory:
    def __init__(self, service: PartialFulltextService) -> None:
        self.service = service

    def __call__(self, **kwargs: Any) -> PartialFulltextService:
        return self.service


def test_fulltext_resume_retries_only_failed_chapter_and_recomposes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = source_inputs(tmp_path)
    overrides = successful_overrides(tmp_path)
    overrides.pop("generate_fulltext")
    service = PartialFulltextService(tmp_path / "formal_fulltext")
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs",
        stream_model=False,
        stage_overrides=overrides,
        fulltext_runner_factory=PartialFulltextFactory(service),
    )
    tender_metadata = {
        "fields": {
            "project_name": {"value": "Recovered project", "source_file_id": "tender-1"},
            "tender_no": {"value": "PROC-001", "source_file_id": "tender-1"},
        }
    }
    monkeypatch.setattr(
        runner,
        "_load_project",
        lambda state: {"tender_project_metadata": tender_metadata},
    )
    first = runner.run(
        source_dir=inputs["source_dir"],
        lot_name="LOT-001",
        run_id="bid-test-fulltext-recovery",
    )
    preserved_before = sha256(service.success_candidate)

    assert first["pipeline_status"] == "FAILED"
    assert first["failure"]["stage_id"] == "generate_fulltext"
    # Mirror a pre-recovery state written by an older runner: the durable
    # Fulltext production index exists, but the failed stage never recorded it.
    failed_stage = next(item for item in first["stages"] if item["stage_id"] == "generate_fulltext")
    failed_stage["artifacts"] = {}
    runner._save_state(first)
    resumed = runner.resume(first["run_id"])

    assert resumed["pipeline_status"] == FINAL_STATUS
    assert service.run_calls == 1
    assert service.retry_calls == ["CH-FAIL"]
    assert service.recovery_tender_metadata == tender_metadata
    assert sha256(service.success_candidate) == preserved_before
    fulltext_stage = next(item for item in resumed["stages"] if item["stage_id"] == "generate_fulltext")
    recovery = fulltext_stage["details"]["chapter_recovery"]
    assert recovery["mode"] == "retry_failed_chapter"
    assert recovery["failed_chapters_before"] == ["CH-FAIL"]
    assert [item["chapter_id"] for item in recovery["reused_completed_chapters"]] == ["CH-OK"]
    assert recovery["recomposed"] is True
    assert recovery["failed_chapters_after"] == []


def test_resume_rejects_running_pipeline_without_executing_stage(tmp_path: Path) -> None:
    inputs = source_inputs(tmp_path)
    runner = BidPipelineRunner(
        output_dir=tmp_path / "outputs", stream_model=False, stage_overrides=successful_overrides(tmp_path)
    )
    state = runner.run(source_dir=inputs["source_dir"], lot_name="LOT-001", run_id="bid-running")
    state["pipeline_status"] = "RUNNING"
    state["current_stage"] = "generate_fulltext"
    runner._save_state(state)
    with pytest.raises(bid_pipeline_module.BidPipelineStateError, match="already_running"):
        runner.resume("bid-running")


def test_four_bid_cli_commands_are_registered_and_callable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = source_inputs(tmp_path)
    calls: list[str] = []

    class DummyPipeline:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, **kwargs: Any) -> dict[str, Any]:
            calls.append("run")
            return {
                "run_id": "bid-cli",
                "pipeline_status": FINAL_STATUS,
                "final_status": FINAL_STATUS,
                "current_stage": None,
                "failure": None,
                "routing": {"pipeline_state_path": "state.json"},
            }

        def resume(self, run_id: str) -> dict[str, Any]:
            calls.append("resume")
            return {"run_id": run_id, "pipeline_status": FINAL_STATUS}

        def status(self, run_id: str) -> dict[str, Any]:
            calls.append("status")
            return {"run_id": run_id, "pipeline_status": FINAL_STATUS}

        def inspect(self, run_id: str) -> dict[str, Any]:
            calls.append("inspect")
            return {"state": {"run_id": run_id}, "artifact_checks": []}

    monkeypatch.setattr(cli_module, "BidPipelineRunner", DummyPipeline)
    cli = CliRunner()
    results = [
        cli.invoke(cli_module.cli, [
            "run-bid",
            "--source-dir", str(inputs["source_dir"]),
            "--lot-name", "LOT-001",
            "--no-stream-model",
        ]),
        cli.invoke(cli_module.cli, ["bid-status", "--run-id", "bid-cli"]),
        cli.invoke(cli_module.cli, ["resume-bid", "--run-id", "bid-cli", "--no-stream-model"]),
        cli.invoke(cli_module.cli, ["inspect-bid", "--run-id", "bid-cli"]),
    ]

    assert all(result.exit_code == 0 for result in results), [result.output for result in results]
    assert calls == ["run", "status", "resume", "status", "inspect"]

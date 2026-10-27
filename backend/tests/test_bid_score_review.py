from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.kb_service as kb_service_module
from app.bid_score_review import (
    BidScoreReviewError,
    BidScoreReviewService,
    COMPONENT_WEIGHTS_CONFIRMATION,
    DECISION_INPUT_SCHEMA_VERSION,
    TECHNICAL_TEMPLATE_SELECTION,
)
from app.bid_pipeline_runner import BidPipelineRunner, StageResult
from app.technical_fulltext_composer import stable_json_sha256


RUN_ID = "bid-score-review-test"
SCORE_FILE_ID = "file-score"


def machine_score_parse() -> dict:
    technical_candidates = []
    for table_index, heading in ((2, "服务类技术评分"), (3, "科技技术服务技术评分")):
        technical_candidates.append(
            {
                "candidate_id": f"technical_table_{table_index}",
                "heading": heading,
                "table_index": table_index,
                "headers": ["评审要素", "评审内容"],
                "selection_signals": ["section_heading:technical_scoring"],
                "confidence": 0.9,
                "selection_score": 12,
                "score_items": [
                    {
                        "id": f"technical_{table_index}",
                        "category": "technical",
                        "item_name": heading,
                        "score": 20,
                        "score_rule": "技术评分规则",
                        "generation_instruction": "生成技术响应。",
                        "is_disqualifying": False,
                    }
                ],
                "selected": False,
            }
        )
    return {
        "schema_version": "score_table_parse_v2",
        "status": "needs_review",
        "parsed": True,
        "score_file_id": SCORE_FILE_ID,
        "review_gate": "technical_template_unresolved",
        "score_rules_extracted": True,
        "technical_template_resolution_status": "unresolved",
        "technical_template_unresolved": True,
        "component_weights_unresolved": True,
        "unsupported_score_format": False,
        "weights": {},
        "technical_score_table_candidates": technical_candidates,
        "business_score_table": {"table_index": 5, "heading": "商务评分标准", "score_items": []},
        "business_score_items": [
            {
                "id": "business_1",
                "category": "business",
                "item_name": "诚信评价",
                "score": 3,
                "score_rule": "商务评分规则",
                "generation_instruction": "生成商务响应。",
                "is_disqualifying": False,
            }
        ],
        "technical_score_items": [],
        "price_scoring_method": {"table_index": 6, "method_name": "区间平均价浮动法"},
        "price_score_rule": {
            "id": "price_scoring_method",
            "category": "price",
            "item_name": "价格评分",
            "score": 0,
            "score_range": {"min": 0, "max": 0},
            "score_rule": "价格部分得分=公式",
            "generation_instruction": "生成报价核对。",
            "is_disqualifying": False,
        },
        "score_items": [],
        "score_scale": {"weights": {}},
        "generation_tasks": [],
        "disqualification_rules": [],
    }


def write_run(tmp_path: Path, *, score_parse: dict | None = None) -> tuple[Path, dict]:
    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "bid_runs" / RUN_ID
    run_dir.mkdir(parents=True)
    metadata_path = run_dir / "source_metadata.json"
    score = deepcopy(score_parse or machine_score_parse())
    metadata = {
        "files": [
            {"id": "file-tender", "parsed_text": "采购项目", "tables": []},
            {"id": SCORE_FILE_ID, "parsed_text": "评分文件", "tables": [], "score_table_parse": score},
        ]
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    state = {
        "schema_version": "bid_pipeline_state_v1",
        "run_id": RUN_ID,
        "source_manifest": {"tender_file_id": "file-tender", "score_file_id": SCORE_FILE_ID, "files": []},
        "routing": {"source_metadata_path": str(metadata_path.resolve())},
    }
    (run_dir / "pipeline_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_dir, score


def decision_base(service: BidScoreReviewService, output_dir: Path) -> dict:
    inspected = service.inspect(RUN_ID)
    return {
        "run_id": RUN_ID,
        "score_file_id": SCORE_FILE_ID,
        "base_score_parse_sha256": inspected["base_score_parse_sha256"],
        "reviewer": "reviewer@example.com",
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "reason": "核对采购文件评分规则后确认。",
    }


def envelope(*decisions: dict) -> dict:
    return {"schema_version": DECISION_INPUT_SCHEMA_VERSION, "run_id": RUN_ID, "decisions": list(decisions)}


def technical_decision(service: BidScoreReviewService, output_dir: Path, *, candidate_id: str = "technical_table_2", supersedes: str | None = None) -> dict:
    payload = {
        **decision_base(service, output_dir),
        "decision_id": f"decision-tech-{candidate_id}-{supersedes or 'first'}",
        "decision_type": TECHNICAL_TEMPLATE_SELECTION,
        "selected_technical_candidate_id": candidate_id,
        "candidate_refs": [{"candidate_id": candidate_id, "table_index": int(candidate_id.rsplit("_", 1)[1])}],
        "evidence": [{"source_file_id": SCORE_FILE_ID, "source_location": f"table:{candidate_id.rsplit('_', 1)[1]}"}],
        "supersedes": supersedes,
    }
    return payload


def weights_decision(service: BidScoreReviewService, output_dir: Path, *, business: float = 20, technical: float = 50, price: float = 30, supersedes: str | None = None) -> dict:
    return {
        **decision_base(service, output_dir),
        "decision_id": f"decision-weights-{business}-{technical}-{price}-{supersedes or 'first'}",
        "decision_type": COMPONENT_WEIGHTS_CONFIRMATION,
        "business_weight": business,
        "technical_weight": technical,
        "price_weight": price,
        "candidate_refs": [],
        "evidence": [{"source_file_id": SCORE_FILE_ID, "source_location": "采购文件评分权重人工确认"}],
        "supersedes": supersedes,
    }


def test_legal_technical_selection_creates_overlay_and_preserves_machine_parse(tmp_path: Path) -> None:
    output_dir, original = write_run(tmp_path)
    service = BidScoreReviewService(output_dir=output_dir)
    original_sha = stable_json_sha256(original)

    result = service.apply(RUN_ID, envelope(technical_decision(service, output_dir)))
    effective = service.effective_score_parse(RUN_ID)
    persisted = json.loads((output_dir / "bid_runs" / RUN_ID / "source_metadata.json").read_text(encoding="utf-8"))
    machine = next(item for item in persisted["files"] if item["id"] == SCORE_FILE_ID)["score_table_parse"]

    assert result["applied_decision_ids"]
    assert stable_json_sha256(machine) == original_sha
    assert effective["effective_score_parse"]["technical_template_unresolved"] is False
    assert effective["effective_score_parse"]["selected_technical_candidate_id"] == "technical_table_2"
    assert effective["effective_score_parse"]["technical_score_items"]
    assert service.inspect(RUN_ID)["current_score_review_gate"] == "component_weights_unresolved"


def test_single_decision_payload_is_accepted(tmp_path: Path) -> None:
    output_dir, _ = write_run(tmp_path)
    service = BidScoreReviewService(output_dir=output_dir)
    decision = technical_decision(service, output_dir)
    decision["schema_version"] = DECISION_INPUT_SCHEMA_VERSION

    result = service.apply(RUN_ID, decision)

    assert result["applied_decision_ids"]


def test_invalid_candidate_and_invalid_weights_are_rejected(tmp_path: Path) -> None:
    output_dir, _ = write_run(tmp_path)
    service = BidScoreReviewService(output_dir=output_dir)

    with pytest.raises(BidScoreReviewError, match="not a current technical candidate"):
        service.apply(RUN_ID, envelope(technical_decision(service, output_dir, candidate_id="technical_table_99")))
    with pytest.raises(BidScoreReviewError, match="must equal 100"):
        service.apply(RUN_ID, envelope(weights_decision(service, output_dir, business=20, technical=20, price=20)))


def test_component_weight_confirmation_ignores_price_parameters_and_resolves_gate(tmp_path: Path) -> None:
    output_dir, _ = write_run(tmp_path)
    service = BidScoreReviewService(output_dir=output_dir)
    tech = technical_decision(service, output_dir)
    weights = weights_decision(service, output_dir)
    weights["weights"] = {"W1": -15}

    with pytest.raises(BidScoreReviewError, match="W1/W2/C"):
        service.apply(RUN_ID, envelope(weights))

    service.apply(RUN_ID, envelope(tech, weights_decision(service, output_dir)))
    effective = service.effective_score_parse(RUN_ID)["effective_score_parse"]

    assert effective["weights"] == {"business": 20, "technical": 50, "price": 30}
    assert effective["component_weights_unresolved"] is False
    assert effective["review_gate"] is None
    assert effective["status"] == "success"
    assert effective["price_score_rule"]["score"] == 30


def test_stale_and_dry_run_do_not_write_decisions(tmp_path: Path) -> None:
    output_dir, _ = write_run(tmp_path)
    service = BidScoreReviewService(output_dir=output_dir)
    decision = technical_decision(service, output_dir)
    preview = service.apply(RUN_ID, envelope(decision), dry_run=True)
    store_path = output_dir / "bid_runs" / RUN_ID / "score_review" / "score_review_decisions.json"

    assert preview["dry_run"] is True
    assert not store_path.exists()

    metadata_path = output_dir / "bid_runs" / RUN_ID / "source_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    score = next(item for item in metadata["files"] if item["id"] == SCORE_FILE_ID)["score_table_parse"]
    score["message"] = "machine parse changed"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(BidScoreReviewError, match="stale decision"):
        service.apply(RUN_ID, envelope(decision))


def test_supersede_keeps_history_append_only(tmp_path: Path) -> None:
    output_dir, _ = write_run(tmp_path)
    service = BidScoreReviewService(output_dir=output_dir)
    first = technical_decision(service, output_dir, candidate_id="technical_table_2")
    service.apply(RUN_ID, envelope(first))
    second = technical_decision(
        service,
        output_dir,
        candidate_id="technical_table_3",
        supersedes=first["decision_id"],
    )
    service.apply(RUN_ID, envelope(second))
    inspected = service.inspect(RUN_ID)
    store = json.loads(Path(inspected["decision_artifact_path"]).read_text(encoding="utf-8"))

    assert len(store["decisions"]) == 2
    assert store["superseded_decision_ids"] == [first["decision_id"]]
    assert inspected["human_decisions"][0]["decision_id"] == second["decision_id"]


def test_resume_retries_build_stage_without_rerunning_ingest_or_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir, score = write_run(tmp_path)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "tender.txt").write_text("采购项目", encoding="utf-8")

    runner = BidPipelineRunner(output_dir=output_dir, stream_model=False)
    state_path = output_dir / "bid_runs" / RUN_ID / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    completed_stage = lambda stage_id: {
        **runner._new_stage(stage_id, stage_id),
        "status": "completed",
    }
    failed_stage = lambda stage_id: {
        **runner._new_stage(stage_id, stage_id),
        "status": "failed",
    }
    parse_stage = completed_stage("parse_sources")
    parse_stage["outputs"] = {"tender_file_id": "file-tender", "score_file_id": SCORE_FILE_ID}
    state.update(
        {
            "pipeline_status": "FAILED",
            "final_status": None,
            "current_stage": "build_tender_project",
            "inputs": {"source_dir": str(source_dir), "lot_name": None},
            "routing": {
                **state["routing"],
                "pipeline_state_path": str(state_path.resolve()),
                "stage_output_root": str((output_dir / "bid_pipeline_outputs" / RUN_ID).resolve()),
            },
            "source_manifest": {
                "tender_file_id": "file-tender",
                "score_file_id": SCORE_FILE_ID,
                "files": [],
            },
            "stages": [
                completed_stage("validate_inputs"),
                completed_stage("ingest_sources"),
                parse_stage,
                failed_stage("build_tender_project"),
            ],
        }
    )
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(
        kb_service_module.KnowledgeBaseService,
        "parse_tender",
        lambda self, _: {"status": "success", "project_summary": "", "sections": [], "disqualification_rules": [], "generation_tasks": []},
    )
    monkeypatch.setattr(kb_service_module, "save_tender_project", lambda _: None)

    service = BidScoreReviewService(output_dir=output_dir)
    service.apply(RUN_ID, envelope(technical_decision(service, output_dir), weights_decision(service, output_dir)))
    resumed = runner.resume(RUN_ID)
    stages = {item["stage_id"]: item for item in resumed["stages"]}

    assert stages["ingest_sources"]["skip_count"] == 1
    assert stages["parse_sources"]["skip_count"] == 1
    assert stages["build_tender_project"]["status"] == "completed"
    assert resumed["score_review"]["application_status"] == "resolved"
    assert stable_json_sha256(score) == stable_json_sha256(machine_score_parse())

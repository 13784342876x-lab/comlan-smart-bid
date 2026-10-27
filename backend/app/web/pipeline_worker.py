from __future__ import annotations

import argparse
from pathlib import Path

from app.bid_pipeline_runner import ANALYSIS_STOP_STAGE, FINAL_STATUS, BidPipelineRunner
from app.technical_fulltext_human_review import TechnicalFulltextHumanReviewService
from app.technical_fulltext_human_review_finalization import TechnicalFulltextHumanReviewFinalizer
from app.web.analysis_query_service import TenderAnalysisReadService
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.project_lifecycle_service import ProjectLifecycleService
from app.web.query_service import ProjectReadService
from app.web.writing_plan_query_service import WritingPlanReadService
from app.web.writing_preparation_query_service import WritingPreparationReadService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a persisted bid analysis job through the Python service layer.")
    parser.add_argument("--outputs-root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("start", "resume", "writing-plan", "full-bid"), required=True)
    args = parser.parse_args()

    outputs_root = Path(args.outputs_root).expanduser().resolve()
    lifecycle = ProjectLifecycleService(outputs_root)
    source_dir, lot_name = lifecycle.worker_inputs(args.project_id, args.run_id)
    resolver = ReadOnlyProjectResolver(outputs_root)
    project_read = ProjectReadService(resolver)
    analysis = TenderAnalysisReadService(resolver, lifecycle, project_read)
    preparation = WritingPreparationReadService(resolver, lifecycle, analysis)
    writing_plan = WritingPlanReadService(resolver, lifecycle, analysis, preparation)

    def stage9_inputs(_: dict) -> dict:
        snapshot = preparation.fact_service.snapshot(args.project_id)
        return {
            "confirmed_project_facts_path": (
                snapshot.state_path if snapshot.state_path.is_file() else None
            ),
            "writing_constraint_context": writing_plan.stage9_input_context(args.project_id),
        }

    runner = BidPipelineRunner(output_dir=outputs_root, stage9_input_loader=stage9_inputs)
    if args.mode == "writing-plan":
        runner.continue_to_writing_plan(args.run_id)
    elif args.mode == "full-bid":
        try:
            state = runner.continue_full_pipeline(args.run_id)
            if state.get("pipeline_status") == FINAL_STATUS:
                _prepare_human_review(state)
                lifecycle.record_full_bid_worker_outcome(args.project_id, args.run_id)
            else:
                failure = state.get("failure") or {}
                lifecycle.record_full_bid_worker_outcome(
                    args.project_id,
                    args.run_id,
                    error=str(failure.get("message") or "pipeline did not complete"),
                )
        except Exception as exc:
            lifecycle.record_full_bid_worker_outcome(
                args.project_id, args.run_id, error=str(exc)
            )
            raise
    elif args.mode == "resume":
        try:
            state = runner.resume(args.run_id)
            if state.get("pipeline_status") == FINAL_STATUS:
                _prepare_human_review(state)
        except Exception as exc:
            lifecycle.record_full_bid_worker_outcome(
                args.project_id, args.run_id, error=str(exc)
            )
            raise
        else:
            lifecycle.record_resume_worker_outcome(args.project_id, args.run_id)
    else:
        runner.run(
            source_dir=source_dir,
            lot_name=lot_name,
            run_id=args.run_id,
            stop_after_stage=ANALYSIS_STOP_STAGE,
        )


def _prepare_human_review(state: dict) -> None:
    package_path = next(
        item
        for item in state.get("stages") or []
        if item.get("stage_id") == "assemble_stage12a"
    )["artifacts"]["technical_fulltext_review_package_json"]
    initialized = TechnicalFulltextHumanReviewService().initialize(
        review_package_json_path=package_path,
        output_dir=Path(package_path).parent,
    )
    TechnicalFulltextHumanReviewFinalizer().finalize(
        review_package_json_path=package_path,
        human_review_json_path=initialized["outputs"]["technical_fulltext_human_review_json"],
        output_dir=Path(package_path).parent,
    )


if __name__ == "__main__":
    main()

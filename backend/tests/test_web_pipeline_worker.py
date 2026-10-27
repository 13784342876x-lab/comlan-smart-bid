from __future__ import annotations

import sys
from pathlib import Path

from app.web import pipeline_worker


def test_worker_bootstrap_supplies_project_read_service(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class Lifecycle:
        def __init__(self, outputs_root: Path) -> None:
            captured["outputs_root"] = outputs_root

        def worker_inputs(self, project_id: str, run_id: str):
            return tmp_path, "acceptance-lot"

    class Resolver:
        def __init__(self, outputs_root: Path) -> None:
            self.outputs_root = outputs_root

    class ProjectRead:
        def __init__(self, resolver: Resolver) -> None:
            captured["project_read"] = self

    class Analysis:
        def __init__(self, resolver: Resolver, lifecycle: Lifecycle, project_read: ProjectRead) -> None:
            captured["analysis_project_read"] = project_read

    class Preparation:
        def __init__(self, resolver: Resolver, lifecycle: Lifecycle, analysis: Analysis) -> None:
            pass

    class WritingPlan:
        def __init__(self, resolver: Resolver, lifecycle: Lifecycle, analysis: Analysis, preparation: Preparation) -> None:
            pass

    class Runner:
        def __init__(self, **kwargs) -> None:
            pass

        def run(self, **kwargs) -> None:
            captured["run"] = kwargs

    monkeypatch.setattr(pipeline_worker, "ProjectLifecycleService", Lifecycle)
    monkeypatch.setattr(pipeline_worker, "ReadOnlyProjectResolver", Resolver)
    monkeypatch.setattr(pipeline_worker, "ProjectReadService", ProjectRead)
    monkeypatch.setattr(pipeline_worker, "TenderAnalysisReadService", Analysis)
    monkeypatch.setattr(pipeline_worker, "WritingPreparationReadService", Preparation)
    monkeypatch.setattr(pipeline_worker, "WritingPlanReadService", WritingPlan)
    monkeypatch.setattr(pipeline_worker, "BidPipelineRunner", Runner)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline-worker",
            "--outputs-root",
            str(tmp_path),
            "--project-id",
            "prj-acceptance",
            "--run-id",
            "bid-acceptance",
            "--mode",
            "start",
        ],
    )

    pipeline_worker.main()

    assert captured["analysis_project_read"] is captured["project_read"]
    assert captured["run"] == {
        "source_dir": tmp_path,
        "lot_name": "acceptance-lot",
        "run_id": "bid-acceptance",
        "stop_after_stage": pipeline_worker.ANALYSIS_STOP_STAGE,
    }

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import app.technical_fulltext_runner as runner_module
import cli as cli_module
from app.technical_fulltext_composer import (
    TechnicalFulltextComposer,
    TechnicalFulltextCompositionError,
    sha256_file,
)
from app.technical_fulltext_runner import TechnicalFulltextRunner, TechnicalFulltextRunnerError


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def make_inputs(tmp_path: Path, chapters: list[tuple[str, str, list[tuple[str, str]]]]) -> dict[str, Path]:
    standard = {
        "schema_version": "technical_writing_outline_v0",
        "chapters": [
            {
                "chapter_id": chapter_id,
                "title": title,
                "children": [
                    {"chapter_id": section_id, "title": section_title, "children": []}
                    for section_id, section_title in children
                ],
            }
            for chapter_id, title, children in chapters
        ],
    }
    return {
        "response": write_json(tmp_path / "response.json", {"schema_version": "response_v1"}),
        "writing": write_json(tmp_path / "technical_writing_outline.json", standard),
        "task": write_json(tmp_path / "task.json", {"schema_version": "task_v1"}),
        "draft": write_json(tmp_path / "draft_units.json", {"draft_units": []}),
        "facts": write_json(tmp_path / "facts.json", {"confirmed_facts": []}),
    }


def candidate_for(chapter: dict[str, Any]) -> dict:
    children = list(chapter.get("children") or [])
    if children:
        section_values = [
            (item["chapter_id"], item["title"], f"{chapter['chapter_id']}::{item['chapter_id']} 正文")
            for item in children
        ]
    else:
        section_values = [(f"{chapter['chapter_id']}-LOCAL", "完整章节正文", f"{chapter['chapter_id']} 无 children 正文")]
    return {
        "chapter_title": chapter["title"],
        "sections": [
            {
                "section_id": section_id,
                "heading": heading,
                "block_type": "technical_solution",
                "content": content,
                "covered_score_item_ids": [f"SCORE-{chapter['chapter_id']}"],
                "covered_requirement_ids": [f"TR-{chapter['chapter_id']}"],
                "placeholder_ids": [],
            }
            for section_id, heading, content in section_values
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [f"SCORE-{chapter['chapter_id']}"],
        "requirements_covered": [f"TR-{chapter['chapter_id']}"],
        "warnings": [],
    }


class FakeAgentFactory:
    def __init__(
        self,
        writing_outline_path: Path,
        *,
        blocked: set[str] | None = None,
        failures: set[str] | None = None,
        event_modes: dict[str, str] | None = None,
    ) -> None:
        self.outline = json.loads(writing_outline_path.read_text(encoding="utf-8"))
        self.blocked = set(blocked or set())
        self.failures = set(failures or set())
        self.event_modes = dict(event_modes or {})
        self.build_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []
        self.active = 0
        self.max_active = 0

    def __call__(self, agent_input: Any, **kwargs: Any) -> Any:
        self.build_calls.append({"input": agent_input, "kwargs": kwargs})
        owner = self

        class FakeAgent:
            def run(self, *, event_handler: Any, event_log_path: Path, stream_model: bool) -> dict:
                chapter_id = agent_input.target_chapter_id
                owner.active += 1
                owner.max_active = max(owner.max_active, owner.active)
                try:
                    owner.run_calls.append({
                        "chapter_id": chapter_id,
                        "event_log_path": Path(event_log_path),
                        "stream_model": stream_model,
                    })
                    event_log_path.parent.mkdir(parents=True, exist_ok=True)
                    event_log_path.write_text('{"event_type":"fake_chapter_done"}\n', encoding="utf-8")
                    if owner.event_modes.get(chapter_id) == "empty":
                        event_log_path.write_text("", encoding="utf-8")
                    elif owner.event_modes.get(chapter_id) == "invalid":
                        event_log_path.write_text("not-json\n", encoding="utf-8")
                    if chapter_id in owner.failures:
                        raise RuntimeError(f"fixture failure for {chapter_id}")
                    chapter = next(item for item in owner.outline["chapters"] if item["chapter_id"] == chapter_id)
                    payload = candidate_for(chapter)
                    drafts = kwargs["output_dir"] / agent_input.output_key / "drafts"
                    candidate_path = write_json(drafts / f"{chapter_id}.candidate.json", payload)
                    candidate_markdown = drafts / f"{chapter_id}.candidate.md"
                    candidate_markdown.write_text(f"# {chapter['title']}\n\nCandidate body\n", encoding="utf-8")
                    # A formal file is deliberately present; Runner/Composer must still use Candidate JSON.
                    (drafts / f"{chapter_id}.md").write_text("FORMAL-SENTINEL\n", encoding="utf-8")
                    is_blocked = chapter_id in owner.blocked
                    run = {
                        "schema_version": "technical_writing_agent_run_v1",
                        "status": "completed",
                        "chapter_status": "auto_repair_exhausted" if is_blocked else "completed",
                        "validation_status": "blocked" if is_blocked else "passed",
                        "issue_counts": {"fatal": 0, "blocking": 3 if is_blocked else 0, "warning": 1 if is_blocked else 0},
                        "target": {"chapter_id": chapter_id, "section_id": None},
                        "candidate_generated": True,
                        "candidate_saved": True,
                        "candidate_available": True,
                        "candidate_reviewable": True,
                        "submission_ready": not is_blocked,
                        "selected_candidate": "fresh" if is_blocked else "initial",
                        "candidate_selection_reason": "fake-monotonic-selection",
                        "revision_round": 0,
                        "candidate_artifacts": {
                            "json_sha256": sha256_file(candidate_path),
                            "markdown_sha256": sha256_file(candidate_markdown),
                        },
                        "run_summary": {"output_key": agent_input.output_key},
                    }
                    run_path = write_json(drafts / f"{chapter_id}.run.json", run)
                    run["outputs"] = {
                        "candidate_json": str(candidate_path),
                        "candidate_markdown": str(candidate_markdown),
                        "formal_markdown": str(drafts / f"{chapter_id}.md"),
                        "run_json": str(run_path),
                        "events_jsonl": str(event_log_path),
                    }
                    return run
                finally:
                    owner.active -= 1

        return FakeAgent()


def run_fixture(
    tmp_path: Path,
    *,
    blocked: set[str] | None = None,
    failures: set[str] | None = None,
    event_modes: dict[str, str] | None = None,
    composer_factory: Any = None,
    output_key: str = "runner_case",
) -> tuple[dict, FakeAgentFactory, dict[str, Path]]:
    inputs = make_inputs(tmp_path, [
        ("WC-010", "第十章", [("WC-010-01", "十章一节")]),
        ("WC-002", "第二章", [("WC-002-01", "二章一节")]),
        ("WC-006", "无子节章", []),
    ])
    factory = FakeAgentFactory(
        inputs["writing"], blocked=blocked, failures=failures, event_modes=event_modes
    )
    service_kwargs: dict[str, Any] = {
        "output_dir": tmp_path / "outputs",
        "agent_factory": factory,
    }
    if composer_factory is not None:
        service_kwargs["composer_factory"] = composer_factory
    service = TechnicalFulltextRunner(**service_kwargs)
    result = service.run(
        response_outline_path=inputs["response"],
        writing_outline_path=inputs["writing"],
        task_information_path=inputs["task"],
        draft_units_path=inputs["draft"],
        confirmed_project_facts_path=inputs["facts"],
        output_key=output_key,
        web_search_policy="disabled",
        stream_model=False,
    )
    return result, factory, inputs


def recovery_inputs(tmp_path: Path) -> dict[str, Path]:
    chapters = [
        (f"WC-{number:03d}", f"第{number}章", [(f"WC-{number:03d}-01", f"第{number}章第一节")])
        for number in range(1, 9)
    ]
    return make_inputs(tmp_path, chapters)


def create_incomplete_recovery_run(
    tmp_path: Path, *, blocked: set[str] | None = None
) -> tuple[dict[str, Any], FakeAgentFactory, dict[str, Path]]:
    inputs = recovery_inputs(tmp_path)
    factory = FakeAgentFactory(inputs["writing"], blocked=blocked, failures={"WC-006"})
    result = TechnicalFulltextRunner(
        output_dir=tmp_path / "outputs", agent_factory=factory
    ).run(
        response_outline_path=inputs["response"],
        writing_outline_path=inputs["writing"],
        task_information_path=inputs["task"],
        draft_units_path=inputs["draft"],
        confirmed_project_facts_path=inputs["facts"],
        output_key="recovery_case",
        web_search_policy="disabled",
        stream_model=False,
    )
    return result, factory, inputs


def test_runner_builds_complete_stable_plan_and_invokes_each_chapter_sequentially(tmp_path: Path) -> None:
    result, factory, _ = run_fixture(tmp_path)

    expected = ["WC-010", "WC-002", "WC-006"]
    assert result["status"] == "completed"
    assert result["production_status"] == "complete"
    assert result["composition_status"] == "complete"
    assert [call["input"].target_chapter_id for call in factory.build_calls] == expected
    assert [call["chapter_id"] for call in factory.run_calls] == expected
    assert factory.max_active == 1
    for build in factory.build_calls:
        agent_input = build["input"]
        assert agent_input.target_section_id is None
        assert agent_input.output_key == "runner_case"
        assert agent_input.web_search_policy == "disabled"
        assert build["kwargs"]["disable_web"] is True
    assert len({str(call["event_log_path"]) for call in factory.run_calls}) == 3
    assert all(call["event_log_path"].name == f"{call['chapter_id']}.events.jsonl" for call in factory.run_calls)

    index_path = Path(result["outputs"]["chapter_production_index"])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["schema_version"] == "technical_fulltext_chapter_production_index_v1"
    assert [item["chapter_id"] for item in index["chapters"]] == expected
    assert [item["chapter_order"] for item in index["chapters"]] == [1, 2, 3]
    assert all(item["status"] == "completed" for item in index["chapters"])
    assert index["production_status"] == "complete"
    assert index["chapter_count_completed"] == 3
    assert index["chapter_count_submission_ready"] == 3
    assert index["chapter_count_blocked"] == 0
    assert index["chapter_count_failed"] == 0
    assert all(item["event_count"] == 1 for item in index["chapters"])
    assert all(item["events_jsonl_sha256"] == sha256_file(item["events_jsonl_path"]) for item in index["chapters"])
    assert index["chapters"][2]["candidate_reviewable"] is True
    assert all(item["response_outline_path"].endswith("response.json") for item in index["chapters"])
    assert all(item["writing_outline_path"].endswith("technical_writing_outline.json") for item in index["chapters"])
    assert all(item["task_information_path"].endswith("task.json") for item in index["chapters"])
    assert all(item["output_key"] == "runner_case" for item in index["chapters"])
    assert all(item["web_search_policy"] == "disabled" for item in index["chapters"])
    assert Path(result["manifest_path"]).is_file()
    assert Path(result["outputs"]["technical_fulltext_commit"]).is_file()
    fulltext = json.loads(Path(result["outputs"]["technical_fulltext_json"]).read_text(encoding="utf-8"))
    assert fulltext["chapter_order"] == expected
    assert "FORMAL-SENTINEL" not in json.dumps(fulltext, ensure_ascii=False)
    first_candidate_path = tmp_path / "outputs" / "runner_case" / "drafts" / "WC-010.candidate.json"
    assert json.loads(first_candidate_path.read_text(encoding="utf-8")) == candidate_for(
        json.loads((tmp_path / "technical_writing_outline.json").read_text(encoding="utf-8"))["chapters"][0]
    )


def test_blocked_reviewable_chapter_is_completed_and_does_not_stop_later_chapters(tmp_path: Path) -> None:
    result, factory, _ = run_fixture(tmp_path, blocked={"WC-002"})

    assert [call["chapter_id"] for call in factory.run_calls] == ["WC-010", "WC-002", "WC-006"]
    assert result["status"] == "completed"
    assert result["production_status"] == "complete_with_blockers"
    assert result["composition_status"] == "completed_with_blockers"
    assert result["chapter_count_completed"] == 3
    assert result["chapter_count_submission_ready"] == 2
    assert result["chapter_count_blocked"] == 1
    assert result["chapter_count_failed"] == 0
    record = next(item for item in result["chapters"] if item["chapter_id"] == "WC-002")
    assert record["status"] == "completed"
    assert record["submission_ready"] is False
    assert Path(result["manifest_path"]).is_file()
    assert Path(result["outputs"]["technical_fulltext_json"]).is_file()
    assert Path(result["outputs"]["technical_fulltext_preview"]).is_file()
    assert Path(result["outputs"]["technical_fulltext_commit"]).is_file()
    fulltext = json.loads(Path(result["outputs"]["technical_fulltext_json"]).read_text(encoding="utf-8"))
    assert fulltext["composition_status"] == "completed_with_blockers"
    assert fulltext["preview_only"] is True
    assert fulltext["release_ready"] is False
    blocked = next(item for item in fulltext["chapters"] if item["chapter_id"] == "WC-002")
    assert blocked["source"]["submission_ready"] is False
    assert blocked["source"]["issue_counts"]["blocking"] == 3
    assert blocked["sections"][0]["content"]


def test_retry_failed_chapter_reuses_seven_chapters_and_rebuilds_fulltext(tmp_path: Path) -> None:
    initial, _, inputs = create_incomplete_recovery_run(tmp_path)
    assert initial["production_status"] == "incomplete"
    old_index_path = Path(initial["outputs"]["chapter_production_index"])
    old_index_sha = sha256_file(old_index_path)
    old_index = json.loads(old_index_path.read_text(encoding="utf-8"))
    reusable = [item for item in old_index["chapters"] if item["chapter_id"] != "WC-006"]
    preserved_hashes = {
        item["chapter_id"]: {
            field: sha256_file(item[path_field])
            for field, path_field in (
                ("candidate", "candidate_json_path"),
                ("candidate_markdown", "candidate_markdown_path"),
                ("run", "run_json_path"),
                ("events", "events_jsonl_path"),
            )
        }
        for item in reusable
    }
    retry_factory = FakeAgentFactory(inputs["writing"])
    composer_calls: list[dict[str, Any]] = []

    class RecordingComposer:
        def __init__(self, **kwargs: Any) -> None:
            self.delegate = TechnicalFulltextComposer(**kwargs)

        def compose(self, **kwargs: Any) -> dict[str, Any]:
            composer_calls.append(kwargs)
            return self.delegate.compose(**kwargs)

    result = TechnicalFulltextRunner(
        output_dir=tmp_path / "outputs",
        agent_factory=retry_factory,
        composer_factory=RecordingComposer,
    ).retry_failed_chapter(
        output_key="recovery_case", chapter_id="WC-006", stream_model=False
    )

    assert [call["input"].target_chapter_id for call in retry_factory.build_calls] == ["WC-006"]
    assert retry_factory.build_calls[0]["input"].target_section_id is None
    assert retry_factory.build_calls[0]["input"].output_key == "recovery_case"
    assert [call["chapter_id"] for call in retry_factory.run_calls] == ["WC-006"]
    assert len(composer_calls) == 1
    assert result["status"] == "completed"
    assert result["production_status"] == "complete"
    assert result["composition_status"] == "complete"
    rebuilt_index = json.loads(old_index_path.read_text(encoding="utf-8"))
    assert rebuilt_index["recovery"]["retry_chapter_id"] == "WC-006"
    assert rebuilt_index["recovery"]["revalidated_chapter_count"] == 8
    assert rebuilt_index["chapter_count_completed"] == 8
    assert rebuilt_index["chapter_count_failed"] == 0
    assert all(item["status"] == "completed" for item in rebuilt_index["chapters"])
    assert all(item["candidate_json_sha256"] for item in rebuilt_index["chapters"])
    assert all(item["candidate_markdown_sha256"] for item in rebuilt_index["chapters"])
    assert all(item["run_json_sha256"] for item in rebuilt_index["chapters"])
    assert all(item["events_jsonl_sha256"] and item["event_count"] > 0 for item in rebuilt_index["chapters"])
    rebuilt_index_sha = sha256_file(old_index_path)
    assert rebuilt_index_sha != old_index_sha
    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["generated_from"]["chapter_production_index_sha256"] == rebuilt_index_sha
    assert len(manifest["chapters"]) == 8
    assert Path(result["outputs"]["technical_fulltext_json"]).is_file()
    assert Path(result["outputs"]["technical_fulltext_preview"]).is_file()
    assert Path(result["outputs"]["technical_fulltext_commit"]).is_file()
    for record in reusable:
        chapter_hashes = preserved_hashes[record["chapter_id"]]
        assert sha256_file(record["candidate_json_path"]) == chapter_hashes["candidate"]
        assert sha256_file(record["candidate_markdown_path"]) == chapter_hashes["candidate_markdown"]
        assert sha256_file(record["run_json_path"]) == chapter_hashes["run"]
        assert sha256_file(record["events_jsonl_path"]) == chapter_hashes["events"]


def test_retry_failed_chapter_allows_reused_content_blockers(tmp_path: Path) -> None:
    _, _, inputs = create_incomplete_recovery_run(tmp_path, blocked={"WC-003"})
    retry_factory = FakeAgentFactory(inputs["writing"])

    result = TechnicalFulltextRunner(
        output_dir=tmp_path / "outputs", agent_factory=retry_factory
    ).retry_failed_chapter(
        output_key="recovery_case", chapter_id="WC-006", stream_model=False
    )

    assert result["production_status"] == "complete_with_blockers"
    assert result["composition_status"] == "completed_with_blockers"
    assert result["chapter_count_completed"] == 8
    assert result["chapter_count_blocked"] == 1
    assert Path(result["outputs"]["technical_fulltext_commit"]).is_file()


def test_retry_rejects_tampered_reused_chapter_before_agent_call(tmp_path: Path) -> None:
    initial, _, inputs = create_incomplete_recovery_run(tmp_path)
    old_index = json.loads(
        Path(initial["outputs"]["chapter_production_index"]).read_text(encoding="utf-8")
    )
    reused = next(item for item in old_index["chapters"] if item["chapter_id"] == "WC-002")
    candidate_path = Path(reused["candidate_json_path"])
    candidate_path.write_text(candidate_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    retry_factory = FakeAgentFactory(inputs["writing"])

    with pytest.raises(TechnicalFulltextRunnerError, match="artifact changed"):
        TechnicalFulltextRunner(
            output_dir=tmp_path / "outputs", agent_factory=retry_factory
        ).retry_failed_chapter(
            output_key="recovery_case", chapter_id="WC-006", stream_model=False
        )

    assert retry_factory.build_calls == []


def test_retry_rejects_changed_original_input_before_agent_call(tmp_path: Path) -> None:
    _, _, inputs = create_incomplete_recovery_run(tmp_path)
    write_json(inputs["task"], {"schema_version": "task_v1", "changed": True})
    retry_factory = FakeAgentFactory(inputs["writing"])

    with pytest.raises(TechnicalFulltextRunnerError, match="planning input changed"):
        TechnicalFulltextRunner(
            output_dir=tmp_path / "outputs", agent_factory=retry_factory
        ).retry_failed_chapter(
            output_key="recovery_case", chapter_id="WC-006", stream_model=False
        )

    assert retry_factory.build_calls == []


def test_retry_rejects_chapter_that_was_already_completed(tmp_path: Path) -> None:
    _, _, inputs = create_incomplete_recovery_run(tmp_path)
    retry_factory = FakeAgentFactory(inputs["writing"])

    with pytest.raises(TechnicalFulltextRunnerError, match="only allowed for a failed chapter"):
        TechnicalFulltextRunner(
            output_dir=tmp_path / "outputs", agent_factory=retry_factory
        ).retry_failed_chapter(
            output_key="recovery_case", chapter_id="WC-005", stream_model=False
        )

    assert retry_factory.build_calls == []


def test_retry_failure_keeps_production_incomplete_and_does_not_compose(tmp_path: Path) -> None:
    initial, _, inputs = create_incomplete_recovery_run(tmp_path)
    old_index = json.loads(
        Path(initial["outputs"]["chapter_production_index"]).read_text(encoding="utf-8")
    )
    preserved = {
        item["chapter_id"]: item["candidate_json_sha256"]
        for item in old_index["chapters"]
        if item["status"] == "completed"
    }
    retry_factory = FakeAgentFactory(inputs["writing"], failures={"WC-006"})

    result = TechnicalFulltextRunner(
        output_dir=tmp_path / "outputs", agent_factory=retry_factory
    ).retry_failed_chapter(
        output_key="recovery_case", chapter_id="WC-006", stream_model=False
    )

    assert [call["chapter_id"] for call in retry_factory.run_calls] == ["WC-006"]
    assert result["status"] == "failed"
    assert result["production_status"] == "incomplete"
    assert result["composition_status"] == "failed"
    assert result["manifest_path"] is None
    assert result["outputs"]["technical_fulltext_json"] is None
    assert result["outputs"]["technical_fulltext_preview"] is None
    assert result["outputs"]["technical_fulltext_commit"] is None
    rebuilt = json.loads(
        Path(result["outputs"]["chapter_production_index"]).read_text(encoding="utf-8")
    )
    failed = next(item for item in rebuilt["chapters"] if item["chapter_id"] == "WC-006")
    assert failed["status"] == "failed"
    for chapter, digest in preserved.items():
        record = next(item for item in rebuilt["chapters"] if item["chapter_id"] == chapter)
        assert record["candidate_json_sha256"] == digest


def test_runtime_failure_continues_later_chapters_and_skips_composer(tmp_path: Path) -> None:
    result, factory, _ = run_fixture(tmp_path, failures={"WC-002"})

    assert [call["chapter_id"] for call in factory.run_calls] == ["WC-010", "WC-002", "WC-006"]
    assert result["status"] == "failed"
    assert result["production_status"] == "incomplete"
    assert result["composition_status"] == "failed"
    assert result["chapter_count_failed"] == 1
    assert result["manifest_path"] is None
    assert result["outputs"]["technical_fulltext_json"] is None
    assert result["outputs"]["technical_fulltext_preview"] is None
    failed = next(item for item in result["chapters"] if item["chapter_id"] == "WC-002")
    assert failed["status"] == "failed"
    assert failed["error"]["error_type"] == "RuntimeError"
    events = [json.loads(line) for line in Path(result["outputs"]["fulltext_events"]).read_text(encoding="utf-8").splitlines()]
    event_types = [item["event_type"] for item in events]
    assert "chapter_run_failed" in event_types
    assert "composition_started" not in event_types
    assert event_types[-1] == "fulltext_run_failed"


@pytest.mark.parametrize("mode", ["empty", "invalid"])
def test_invalid_chapter_events_fail_artifact_acceptance_but_later_chapters_continue(
    tmp_path: Path, mode: str
) -> None:
    result, factory, _ = run_fixture(tmp_path, event_modes={"WC-002": mode})

    assert [call["chapter_id"] for call in factory.run_calls] == ["WC-010", "WC-002", "WC-006"]
    assert result["production_status"] == "incomplete"
    assert result["composition_status"] == "failed"
    failed = next(item for item in result["chapters"] if item["chapter_id"] == "WC-002")
    assert failed["status"] == "failed"
    expected = "events_file_empty" if mode == "empty" else "invalid_events_jsonl"
    assert expected in failed["error"]["message"]
    assert result["manifest_path"] is None


def test_composer_blocking_issues_are_reported_separately_from_warnings(tmp_path: Path) -> None:
    blocking_issue = {
        "issue_id": "FCI-FIXTURE",
        "severity": "blocking",
        "category": "input_protocol",
        "code": "production_index_hash_mismatch",
        "message": "fixture mismatch",
        "release_blocking": True,
    }

    class RejectingComposer:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def compose(self, **kwargs: Any) -> dict[str, Any]:
            raise TechnicalFulltextCompositionError("fixture rejection", issues=[blocking_issue])

    result, _, _ = run_fixture(tmp_path, composer_factory=RejectingComposer)

    assert result["status"] == "failed"
    assert result["production_status"] == "complete"
    assert result["composition_status"] == "failed"
    assert result["composition_issues"] == [blocking_issue]
    assert result["composition_issue_counts"] == {"fatal": 0, "blocking": 1, "warning": 0}
    assert blocking_issue not in result["warnings"]
    assert result["outputs"]["technical_fulltext_json"] is None


def test_production_index_is_atomically_parseable_after_running_and_each_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots: list[list[str]] = []
    original = runner_module.atomic_write_json

    def recording_writer(path: Path, value: dict, *, required_fields: tuple[str, ...]) -> str:
        digest = original(path, value, required_fields=required_fields)
        if Path(path).name == "chapter_production_index.json":
            reparsed = json.loads(Path(path).read_text(encoding="utf-8"))
            snapshots.append([item["status"] for item in reparsed["chapters"]])
        return digest

    monkeypatch.setattr(runner_module, "atomic_write_json", recording_writer)

    run_fixture(tmp_path)

    assert ["planned", "planned", "planned"] in snapshots
    assert ["running", "planned", "planned"] in snapshots
    assert ["completed", "planned", "planned"] in snapshots
    assert ["completed", "running", "planned"] in snapshots
    assert snapshots[-1] == ["completed", "completed", "completed"]


def test_same_outline_produces_same_plan_order_with_new_output_keys(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path, [
        ("Z-2", "先写", [("Z-2-1", "小节")]),
        ("A-1", "后写", []),
    ])
    first_factory = FakeAgentFactory(inputs["writing"])
    second_factory = FakeAgentFactory(inputs["writing"])
    common = dict(
        response_outline_path=inputs["response"],
        writing_outline_path=inputs["writing"],
        task_information_path=inputs["task"],
        web_search_policy="disabled",
        stream_model=False,
    )
    first = TechnicalFulltextRunner(output_dir=tmp_path / "outputs", agent_factory=first_factory).run(
        **common, output_key="stable_1"
    )
    second = TechnicalFulltextRunner(output_dir=tmp_path / "outputs", agent_factory=second_factory).run(
        **common, output_key="stable_2"
    )

    assert [item["chapter_id"] for item in first["chapters"]] == ["Z-2", "A-1"]
    assert [item["chapter_id"] for item in second["chapters"]] == ["Z-2", "A-1"]
    assert [item["order"] for item in first["chapters"]] == [1, 2]


def test_existing_or_unsafe_output_key_fails_before_agent_construction(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path, [("WC-001", "章节", [])])
    factory = FakeAgentFactory(inputs["writing"])
    existing = tmp_path / "outputs" / "existing"
    existing.mkdir(parents=True)
    service = TechnicalFulltextRunner(output_dir=tmp_path / "outputs", agent_factory=factory)
    arguments = dict(
        response_outline_path=inputs["response"],
        writing_outline_path=inputs["writing"],
        task_information_path=inputs["task"],
        stream_model=False,
    )

    with pytest.raises(FileExistsError, match="resume is not supported"):
        service.run(**arguments, output_key="existing")
    with pytest.raises(ValueError, match="output_key"):
        service.run(**arguments, output_key="../unsafe")
    assert factory.build_calls == []


@pytest.mark.parametrize(
    "standard",
    [
        {"schema_version": "v", "chapters": []},
        {"schema_version": "v", "chapters": [{"chapter_id": "A", "title": ""}]},
        {"schema_version": "v", "chapters": [{"chapter_id": "A", "title": "甲"}, {"chapter_id": "A", "title": "乙"}]},
    ],
)
def test_invalid_standard_outline_fails_before_creating_output(tmp_path: Path, standard: dict) -> None:
    inputs = make_inputs(tmp_path, [("placeholder", "placeholder", [])])
    write_json(inputs["writing"], standard)
    factory = FakeAgentFactory(inputs["writing"])

    with pytest.raises(ValueError):
        TechnicalFulltextRunner(output_dir=tmp_path / "outputs", agent_factory=factory).run(
            response_outline_path=inputs["response"],
            writing_outline_path=inputs["writing"],
            task_information_path=inputs["task"],
            output_key="invalid_outline",
            stream_model=False,
        )

    assert not (tmp_path / "outputs" / "invalid_outline").exists()
    assert factory.build_calls == []


def test_run_fulltext_cli_passes_parameters_and_defaults_to_disabled_web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class DummyRunner:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def run(self, **kwargs: Any) -> dict:
            captured["run"] = kwargs
            return {
                "status": "completed",
                "production_status": "complete",
                "composition_status": "complete",
                "chapter_count_expected": 1,
                "chapter_count_completed": 1,
                "chapter_count_submission_ready": 1,
                "chapter_count_blocked": 0,
                "chapter_count_failed": 0,
                "outputs": {},
                "run_path": "run.json",
            }

    monkeypatch.setattr(cli_module, "TechnicalFulltextRunner", DummyRunner)
    result = CliRunner().invoke(cli_module.cli, [
        "run-technical-fulltext",
        "--response-outline", str(tmp_path / "response.json"),
        "--writing-outline", str(tmp_path / "writing.json"),
        "--task-information", str(tmp_path / "task.json"),
        "--draft-units", str(tmp_path / "draft.json"),
        "--confirmed-project-facts", str(tmp_path / "facts.json"),
        "--output-key", "cli_case",
        "--output-dir", str(tmp_path / "out"),
        "--no-stream-model",
    ])

    assert result.exit_code == 0, result.output
    assert captured["run"]["web_search_policy"] == "disabled"
    assert captured["run"]["stream_model"] is False
    assert captured["run"]["output_key"] == "cli_case"
    assert captured["run"]["draft_units_path"] == tmp_path / "draft.json"
    assert "model_profile" not in captured["run"]


def test_run_fulltext_cli_exits_nonzero_when_composition_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    composition_status = "failed"
    class DummyRunner:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "status": "failed",
                "production_status": "incomplete",
                "composition_status": composition_status,
                "chapter_count_expected": 2,
                "chapter_count_completed": 2,
                "chapter_count_submission_ready": 1,
                "chapter_count_blocked": 1,
                "chapter_count_failed": 0,
                "outputs": {},
                "run_path": "run.json",
            }

    monkeypatch.setattr(cli_module, "TechnicalFulltextRunner", DummyRunner)
    result = CliRunner().invoke(cli_module.cli, [
        "run-technical-fulltext",
        "--response-outline", str(tmp_path / "response.json"),
        "--writing-outline", str(tmp_path / "writing.json"),
        "--task-information", str(tmp_path / "task.json"),
        "--output-key", "cli_blocked",
        "--no-stream-model",
    ])

    assert result.exit_code == 1


def test_run_fulltext_cli_accepts_completed_with_blockers_and_prints_preview_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DummyRunner:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "status": "completed",
                "production_status": "complete_with_blockers",
                "composition_status": "completed_with_blockers",
                "chapter_count_expected": 2,
                "chapter_count_completed": 2,
                "chapter_count_submission_ready": 1,
                "chapter_count_blocked": 1,
                "chapter_count_failed": 0,
                "outputs": {},
                "run_path": "run.json",
            }

    monkeypatch.setattr(cli_module, "TechnicalFulltextRunner", DummyRunner)
    result = CliRunner().invoke(cli_module.cli, [
        "run-technical-fulltext",
        "--response-outline", str(tmp_path / "response.json"),
        "--writing-outline", str(tmp_path / "writing.json"),
        "--task-information", str(tmp_path / "task.json"),
        "--output-key", "cli_blocked_preview",
        "--no-stream-model",
    ])

    assert result.exit_code == 0, result.output
    assert "全文组合已完成，但仍存在未解除的章节 blocker。" in result.output
    assert "本次产物仅用于全文审计和人工审核，不得正式提交。" in result.output


def test_retry_fulltext_cli_calls_only_explicit_retry_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Any]] = []

    class DummyRunner:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(("init", kwargs))

        def retry_failed_chapter(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("retry", kwargs))
            return {
                "status": "completed",
                "production_status": "complete_with_blockers",
                "composition_status": "completed_with_blockers",
                "retry_chapter_id": "WC-006",
                "chapter_count_expected": 8,
                "chapter_count_completed": 8,
                "chapter_count_submission_ready": 3,
                "chapter_count_blocked": 5,
                "chapter_count_failed": 0,
                "outputs": {},
                "run_path": "run.json",
            }

    monkeypatch.setattr(cli_module, "TechnicalFulltextRunner", DummyRunner)
    result = CliRunner().invoke(cli_module.cli, [
        "retry-technical-fulltext-chapter",
        "--output-key", "hospital_hci_001_fulltext_real_20260806_02",
        "--chapter-id", "WC-006",
        "--output-dir", str(tmp_path / "outputs"),
        "--no-stream-model",
    ])

    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["init", "retry"]
    assert calls[1][1] == {
        "output_key": "hospital_hci_001_fulltext_real_20260806_02",
        "chapter_id": "WC-006",
        "stream_model": False,
    }


def test_compose_cli_uses_only_composer_and_inspect_cli_uses_only_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Any]] = []

    class DummyComposer:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(("composer_init", kwargs))

        def compose(self, **kwargs: Any) -> dict:
            calls.append(("compose", kwargs))
            return {
                "status": "completed",
                "composition_status": "complete",
                "composition_issue_counts": {},
                "outputs": {},
            }

    def fake_inspect(path: Path) -> dict:
        calls.append(("inspect", path))
        return {
            "document_id": "DOC",
            "output_key": "cli_case",
            "standard_outline_sha256": "abc",
            "chapter_count": 0,
            "chapter_order": [],
            "chapters": [],
            "composition_issues": [],
            "coverage_counts": {"requirements": 0, "score_items": 0, "placeholders": 0},
            "preview_only": True,
            "release_ready": False,
        }

    monkeypatch.setattr(cli_module, "TechnicalFulltextComposer", DummyComposer)
    monkeypatch.setattr(cli_module, "inspect_technical_fulltext_service", fake_inspect)
    compose_result = CliRunner().invoke(cli_module.cli, [
        "compose-technical-fulltext",
        "--manifest", str(tmp_path / "manifest.json"),
        "--output-key", "cli_case",
        "--output-dir", str(tmp_path / "outputs"),
    ])
    inspect_result = CliRunner().invoke(cli_module.cli, [
        "inspect-technical-fulltext",
        "--fulltext-json", str(tmp_path / "fulltext.json"),
    ])

    assert compose_result.exit_code == 0, compose_result.output
    assert inspect_result.exit_code == 0, inspect_result.output
    assert [name for name, _ in calls] == ["composer_init", "compose", "inspect"]


def test_fulltext_cli_commands_expose_no_model_profile_or_publication_options() -> None:
    cli_runner = CliRunner()
    for command in (
        "run-technical-fulltext",
        "retry-technical-fulltext-chapter",
        "compose-technical-fulltext",
        "inspect-technical-fulltext",
    ):
        result = cli_runner.invoke(cli_module.cli, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--model-profile" not in result.output
        assert "docx" not in result.output.lower()
        assert "pdf" not in result.output.lower()
        assert "formal-markdown" not in result.output.lower()


def test_runner_source_has_no_llm_client_or_formal_markdown_reader() -> None:
    source = Path(runner_module.__file__).read_text(encoding="utf-8")
    assert "LLMClient" not in source
    assert "read_text(encoding=\"utf-8\")" not in "\n".join(
        line for line in source.splitlines() if "formal_markdown_path" in line
    )

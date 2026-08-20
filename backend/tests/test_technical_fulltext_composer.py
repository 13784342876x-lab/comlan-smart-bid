from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import app.technical_fulltext_composer as composer_module
from app.technical_fulltext_composer import (
    COMMIT_SCHEMA_VERSION,
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PREVIEW_NOTICE,
    TechnicalFulltextComposer,
    TechnicalFulltextCompositionError,
    inspect_technical_fulltext,
    sha256_file,
)


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def node(section_id: str, title: str, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"chapter_id": section_id, "title": title, "children": list(children or [])}


def candidate(title: str, sections: list[tuple[str, str, str]], **extra: Any) -> dict[str, Any]:
    requirements = list(extra.get("requirements") or [])
    scores = list(extra.get("scores") or [])
    placeholders = list(extra.get("placeholders") or [])
    return {
        "chapter_title": title,
        "sections": [
            {
                "section_id": section_id,
                "heading": heading,
                "block_type": "technical_solution",
                "content": content,
                "covered_score_item_ids": scores,
                "covered_requirement_ids": requirements,
                "placeholder_ids": placeholders,
            }
            for section_id, heading, content in sections
        ],
        "claims": [{
            "text": "可追溯陈述",
            "claim_type": "tender_fact",
            "source_ids": list(extra.get("sources") or []),
            "evidence_ids": list(extra.get("evidence") or []),
            "human_review_required": False,
        }],
        "placeholders_used": placeholders,
        "score_items_covered": scores,
        "requirements_covered": requirements,
        "warnings": [],
    }


def default_outline() -> dict[str, Any]:
    return {
        "schema_version": "technical_writing_outline_v0",
        "chapters": [
            node("WC-010", "第十章", [node("WC-010-01", "十章一节")]),
            node("WC-002", "第二章", [node("WC-002-01", "二章一节")]),
        ],
    }


def default_candidates() -> dict[str, dict[str, Any]]:
    return {
        "WC-010": candidate(
            "第十章", [("WC-010-01", "十章一节", "第十章正文。")],
            requirements=["TR-1", "TR-2"], scores=["S-1"], placeholders=["PH-1"],
            sources=["SRC-1"], evidence=["EV-1"],
        ),
        "WC-002": candidate(
            "第二章", [("WC-002-01", "二章一节", "第二章正文。")],
            requirements=["TR-2", "TR-3"], scores=["S-1", "S-2"], placeholders=["PH-2"],
            sources=["SRC-2"], evidence=["EV-2"],
        ),
    }


def build_case(
    tmp_path: Path,
    *,
    outline: dict[str, Any] | None = None,
    candidates: dict[str, dict[str, Any]] | None = None,
    output_key: str = "strict_case",
) -> dict[str, Any]:
    output_dir = tmp_path / "outputs"
    standard = outline or default_outline()
    payloads = candidates or default_candidates()
    outline_path = write_json(tmp_path / "technical_writing_outline.json", standard)
    drafts = output_dir / output_key / "drafts"
    entries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for order, chapter in enumerate(standard["chapters"], start=1):
        chapter_id = chapter["chapter_id"]
        candidate_path = write_json(drafts / f"{chapter_id}.candidate.json", payloads[chapter_id])
        candidate_markdown = drafts / f"{chapter_id}.candidate.md"
        candidate_markdown.write_text("candidate artifact\n", encoding="utf-8")
        (drafts / f"{chapter_id}.md").write_text("FORMAL-MARKDOWN-SENTINEL\n", encoding="utf-8")
        events_path = drafts / f"{chapter_id}.events.jsonl"
        events_path.write_text('{"event_type":"chapter_completed"}\n', encoding="utf-8")
        candidate_sha = sha256_file(candidate_path)
        run = {
            "schema_version": "technical_writing_agent_run_v1",
            "status": "completed",
            "chapter_status": "completed",
            "validation_status": "passed",
            "issue_counts": {"fatal": 0, "blocking": 0, "warning": 0},
            "target": {"chapter_id": chapter_id, "section_id": None},
            "candidate_generated": True,
            "candidate_saved": True,
            "candidate_available": True,
            "candidate_reviewable": True,
            "submission_ready": True,
            "selected_candidate": "initial",
            "candidate_selection_reason": "fixture-selection",
            "revision_round": 0,
            "candidate_artifacts": {"json_sha256": candidate_sha},
            "run_summary": {"output_key": output_key},
        }
        run_path = write_json(drafts / f"{chapter_id}.run.json", run)
        entry = {
            "chapter_id": chapter_id,
            "chapter_order": order,
            "candidate_json_path": str(candidate_path.resolve()),
            "candidate_json_sha256": candidate_sha,
            "run_json_path": str(run_path.resolve()),
            "run_json_sha256": sha256_file(run_path),
            "events_jsonl_path": str(events_path.resolve()),
            "events_jsonl_sha256": sha256_file(events_path),
        }
        entries.append(entry)
        records.append({
            **entry,
            "chapter_title": chapter["title"],
            "status": "completed",
            "candidate_generated": True,
            "candidate_saved": True,
            "candidate_reviewable": True,
            "submission_ready": True,
            "issue_counts": {"fatal": 0, "blocking": 0, "warning": 0},
            "event_count": 1,
        })
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "output_key": output_key,
        "chapter_count_expected": len(records),
        "chapter_count_completed": len(records),
        "chapter_count_submission_ready": len(records),
        "chapter_count_blocked": 0,
        "chapter_count_failed": 0,
        "chapters": records,
        "production_status": "complete",
    }
    fulltext_dir = output_dir / output_key / "fulltext"
    index_path = write_json(fulltext_dir / "chapter_production_index.json", index)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "document_id": "DOC-STRICT",
        "output_key": output_key,
        "standard_outline_path": str(outline_path.resolve()),
        "standard_outline_sha256": sha256_file(outline_path),
        "chapters": list(reversed(entries)),
        "generated_from": {
            "chapter_production_index_path": str(index_path.resolve()),
            "chapter_production_index_sha256": sha256_file(index_path),
        },
        "generated_at": "fixture",
    }
    manifest_path = write_json(fulltext_dir / "manifest.json", manifest)
    return {
        "output_dir": output_dir,
        "output_key": output_key,
        "outline_path": outline_path,
        "manifest_path": manifest_path,
        "index_path": index_path,
        "manifest": manifest,
        "index": index,
    }


def save_index(case: dict[str, Any]) -> None:
    write_json(case["index_path"], case["index"])
    case["manifest"]["generated_from"]["chapter_production_index_sha256"] = sha256_file(case["index_path"])
    write_json(case["manifest_path"], case["manifest"])


def find_entry(case: dict[str, Any], chapter_id: str) -> dict[str, Any]:
    return next(item for item in case["manifest"]["chapters"] if item["chapter_id"] == chapter_id)


def find_record(case: dict[str, Any], chapter_id: str) -> dict[str, Any]:
    return next(item for item in case["index"]["chapters"] if item["chapter_id"] == chapter_id)


def sync_run(case: dict[str, Any], chapter_id: str, run: dict[str, Any]) -> None:
    entry = find_entry(case, chapter_id)
    record = find_record(case, chapter_id)
    run_path = Path(entry["run_json_path"])
    write_json(run_path, run)
    run_sha = sha256_file(run_path)
    entry["run_json_sha256"] = run_sha
    record["run_json_sha256"] = run_sha
    save_index(case)


def mark_chapter_blocked(
    case: dict[str, Any], chapter_id: str, *, fatal: int = 0, blocking: int = 1,
    submission_ready: bool = False,
) -> None:
    entry = find_entry(case, chapter_id)
    record = find_record(case, chapter_id)
    run = json.loads(Path(entry["run_json_path"]).read_text(encoding="utf-8"))
    run["chapter_status"] = "auto_repair_exhausted"
    run["validation_status"] = "blocked"
    run["submission_ready"] = submission_ready
    run["issue_counts"] = {"fatal": fatal, "blocking": blocking, "warning": 1}
    record["submission_ready"] = submission_ready
    record["validation_status"] = "blocked"
    record["chapter_status"] = "auto_repair_exhausted"
    record["issue_counts"] = dict(run["issue_counts"])
    case["index"]["production_status"] = "complete_with_blockers"
    case["index"]["chapter_count_submission_ready"] -= 1
    case["index"]["chapter_count_blocked"] += 1
    sync_run(case, chapter_id, run)


def compose(case: dict[str, Any]) -> dict[str, Any]:
    return TechnicalFulltextComposer(output_dir=case["output_dir"]).compose(
        manifest_path=case["manifest_path"], output_key=case["output_key"]
    )


def issue_codes(exc: pytest.ExceptionInfo[TechnicalFulltextCompositionError]) -> set[str]:
    return {item["code"] for item in exc.value.issues}


def test_all_qualified_artifacts_compose_in_outline_order_and_commit_as_set(tmp_path: Path) -> None:
    case = build_case(tmp_path)

    result = compose(case)

    assert result["composition_status"] == "complete"
    assert result["fulltext"]["chapter_order"] == ["WC-010", "WC-002"]
    assert result["fulltext"]["production"] == {
        "chapter_production_index_path": str(case["index_path"].resolve()),
        "chapter_production_index_sha256": sha256_file(case["index_path"]),
        "production_status": "complete",
        "chapter_count_expected": 2,
        "chapter_count_completed": 2,
        "chapter_count_submission_ready": 2,
        "chapter_count_blocked": 0,
        "chapter_count_failed": 0,
    }
    assert result["fulltext"]["release_ready"] is False
    outputs = result["outputs"]
    for name in ("technical_fulltext_json", "technical_fulltext_preview", "technical_fulltext_commit"):
        assert Path(outputs[name]).is_file()
    commit = json.loads(Path(outputs["technical_fulltext_commit"]).read_text(encoding="utf-8"))
    assert commit["schema_version"] == COMMIT_SCHEMA_VERSION
    assert commit["status"] == "committed"
    assert commit["technical_fulltext_json_sha256"] == sha256_file(outputs["technical_fulltext_json"])
    assert commit["technical_fulltext_preview_sha256"] == sha256_file(outputs["technical_fulltext_preview"])
    preview = Path(outputs["technical_fulltext_preview"]).read_text(encoding="utf-8")
    assert PREVIEW_NOTICE in preview
    assert preview.index("# 第十章") < preview.index("# 第二章")
    assert "FORMAL-MARKDOWN-SENTINEL" not in preview


@pytest.mark.parametrize(("fatal", "blocking"), [(0, 0), (1, 0), (0, 2)])
def test_reviewable_content_blockers_still_produce_committed_fulltext_preview(
    tmp_path: Path, fatal: int, blocking: int
) -> None:
    case = build_case(tmp_path)
    mark_chapter_blocked(case, "WC-002", fatal=fatal, blocking=blocking)

    result = compose(case)

    assert result["composition_status"] == "completed_with_blockers"
    fulltext = result["fulltext"]
    assert fulltext["composition_status"] == "completed_with_blockers"
    assert fulltext["production"]["production_status"] == "complete_with_blockers"
    assert fulltext["production"]["chapter_count_submission_ready"] == 1
    assert fulltext["production"]["chapter_count_blocked"] == 1
    assert fulltext["preview_only"] is True
    assert fulltext["release_ready"] is False
    blocked_chapter = next(item for item in fulltext["chapters"] if item["chapter_id"] == "WC-002")
    assert blocked_chapter["sections"][0]["content"] == default_candidates()["WC-002"]["sections"][0]["content"]
    assert blocked_chapter["source"]["submission_ready"] is False
    assert blocked_chapter["source"]["validation_status"] == "blocked"
    preview = Path(result["outputs"]["technical_fulltext_preview"]).read_text(encoding="utf-8")
    assert "Submission Ready：否" in preview
    assert Path(result["outputs"]["technical_fulltext_commit"]).is_file()
    summary = inspect_technical_fulltext(result["outputs"]["technical_fulltext_json"])
    assert summary["composition_status"] == "completed_with_blockers"


def test_validation_blocked_alone_is_previewable_content_state(tmp_path: Path) -> None:
    case = build_case(tmp_path)
    mark_chapter_blocked(case, "WC-002", fatal=0, blocking=0, submission_ready=True)

    result = compose(case)

    assert result["composition_status"] == "completed_with_blockers"
    chapter = next(item for item in result["fulltext"]["chapters"] if item["chapter_id"] == "WC-002")
    assert chapter["source"]["submission_ready"] is True
    assert chapter["source"]["validation_status"] == "blocked"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("reference_missing", "production_index_reference_missing"),
        ("file_missing", "production_index_unavailable"),
        ("invalid_json", "production_index_unavailable"),
        ("hash", "production_index_hash_mismatch"),
        ("schema", "invalid_production_index_schema"),
        ("output_key", "production_output_key_mismatch"),
        ("count", "production_chapter_count_mismatch"),
        ("counters", "production_chapter_count_mismatch"),
        ("running", "production_not_complete"),
        ("inconsistent_blocked_status", "production_not_complete"),
        ("incomplete", "production_not_complete"),
    ],
)
def test_production_index_is_mandatory_and_strict(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    case = build_case(tmp_path)
    if mutation == "reference_missing":
        case["manifest"]["generated_from"].pop("chapter_production_index_sha256")
        write_json(case["manifest_path"], case["manifest"])
    elif mutation == "file_missing":
        case["index_path"].unlink()
    elif mutation == "invalid_json":
        case["index_path"].write_text("{broken", encoding="utf-8")
        case["manifest"]["generated_from"]["chapter_production_index_sha256"] = sha256_file(case["index_path"])
        write_json(case["manifest_path"], case["manifest"])
    elif mutation == "hash":
        case["index"]["updated_at"] = "changed"
        write_json(case["index_path"], case["index"])
    elif mutation == "schema":
        case["index"]["schema_version"] = "wrong"
        save_index(case)
    elif mutation == "output_key":
        case["index"]["output_key"] = "other"
        save_index(case)
    elif mutation == "count":
        case["index"]["chapter_count_expected"] = 99
        save_index(case)
    elif mutation == "counters":
        case["index"]["chapter_count_submission_ready"] = 1
        case["index"]["chapter_count_blocked"] = 1
        save_index(case)
    elif mutation == "inconsistent_blocked_status":
        case["index"]["production_status"] = "complete_with_blockers"
        save_index(case)
    else:
        case["index"]["production_status"] = mutation
        save_index(case)

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert expected_code in issue_codes(caught)
    assert not (case["index_path"].parent / "technical_fulltext.commit.json").exists()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "production_chapter_missing"),
        ("not_completed", "production_chapter_not_completed"),
        ("not_reviewable", "production_chapter_not_reviewable"),
        ("reference", "production_chapter_artifact_reference_missing"),
    ],
)
def test_each_production_chapter_must_be_composition_qualified(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    case = build_case(tmp_path)
    record = find_record(case, "WC-002")
    if mutation == "missing":
        case["index"]["chapters"].remove(record)
    elif mutation == "not_completed":
        record["status"] = "failed"
    elif mutation == "not_reviewable":
        record["candidate_reviewable"] = False
    else:
        record["events_jsonl_sha256"] = ""
    save_index(case)

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert expected_code in issue_codes(caught)


@pytest.mark.parametrize(
    ("field", "expected_code", "replacement"),
    [
        ("candidate_json_path", "production_manifest_candidate_path_mismatch", "other-candidate.json"),
        ("candidate_json_sha256", "production_manifest_candidate_hash_mismatch", "0" * 64),
        ("run_json_path", "production_manifest_run_path_mismatch", "other-run.json"),
        ("run_json_sha256", "production_manifest_run_hash_mismatch", "1" * 64),
        ("events_jsonl_path", "production_manifest_events_path_mismatch", "other.events.jsonl"),
        ("events_jsonl_sha256", "production_manifest_events_hash_mismatch", "2" * 64),
    ],
)
def test_production_index_and_manifest_are_cross_checked(
    tmp_path: Path, field: str, expected_code: str, replacement: str
) -> None:
    case = build_case(tmp_path)
    find_record(case, "WC-002")[field] = replacement
    save_index(case)

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert expected_code in issue_codes(caught)


def test_run_must_be_completed(tmp_path: Path) -> None:
    case = build_case(tmp_path)
    entry = find_entry(case, "WC-002")
    run = json.loads(Path(entry["run_json_path"]).read_text(encoding="utf-8"))
    run["status"] = "failed"
    sync_run(case, "WC-002", run)

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert "run_status_not_completed" in issue_codes(caught)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "events_file_missing"),
        ("empty", "events_file_empty"),
        ("invalid", "invalid_events_jsonl"),
        ("hash", "events_hash_mismatch"),
    ],
)
def test_events_are_required_valid_and_hash_matched(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    case = build_case(tmp_path)
    entry = find_entry(case, "WC-002")
    events_path = Path(entry["events_jsonl_path"])
    if mutation == "missing":
        events_path.unlink()
    elif mutation == "empty":
        events_path.write_text("", encoding="utf-8")
        new_sha = sha256_file(events_path)
        entry["events_jsonl_sha256"] = new_sha
        find_record(case, "WC-002")["events_jsonl_sha256"] = new_sha
        save_index(case)
    elif mutation == "invalid":
        events_path.write_text("not-json\n", encoding="utf-8")
        new_sha = sha256_file(events_path)
        entry["events_jsonl_sha256"] = new_sha
        find_record(case, "WC-002")["events_jsonl_sha256"] = new_sha
        save_index(case)
    else:
        events_path.write_text('{"changed":true}\n', encoding="utf-8")

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert expected_code in issue_codes(caught)


def recursive_case(tmp_path: Path) -> dict[str, Any]:
    outline = {
        "schema_version": "technical_writing_outline_v0",
        "chapters": [node("WC-001", "递归章", [
            node("S-1", "一级节", [
                node("S-1-1", "二级节", [node("S-1-1-1", "三级节")]),
                node("S-1-2", "二级节二"),
            ]),
            node("S-2", "一级节二"),
        ])],
    }
    payloads = {"WC-001": candidate("递归章", [
        ("S-1", "一级节", "正文1"),
        ("S-1-1", "二级节", "正文2"),
        ("S-1-1-1", "三级节", "正文3"),
        ("S-1-2", "二级节二", "正文4"),
        ("S-2", "一级节二", "正文5"),
    ])}
    return build_case(tmp_path, outline=outline, candidates=payloads, output_key="recursive_case")


def test_recursive_outline_preorder_and_markdown_depth_are_preserved(tmp_path: Path) -> None:
    case = recursive_case(tmp_path)

    result = compose(case)

    chapter = result["fulltext"]["chapters"][0]
    assert [item["section_id"] for item in chapter["section_outline"]] == [
        "S-1", "S-1-1", "S-1-1-1", "S-1-2", "S-2"
    ]
    assert [item["depth"] for item in chapter["section_outline"]] == [1, 2, 3, 2, 1]
    preview = Path(result["outputs"]["technical_fulltext_preview"]).read_text(encoding="utf-8")
    assert "## 一级节" in preview
    assert "### 二级节" in preview
    assert "#### 三级节" in preview


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "missing_section"),
        ("order", "section_order_mismatch"),
        ("title", "section_title_mismatch"),
        ("parent", "section_parent_mismatch"),
        ("depth", "section_depth_mismatch"),
    ],
)
def test_recursive_structure_problem_blocks_all_fulltext_outputs(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    case = recursive_case(tmp_path)
    entry = find_entry(case, "WC-001")
    candidate_path = Path(entry["candidate_json_path"])
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload["sections"].pop(2)
    elif mutation == "order":
        payload["sections"][1], payload["sections"][2] = payload["sections"][2], payload["sections"][1]
    elif mutation == "title":
        payload["sections"][2]["heading"] = "错误标题"
    elif mutation == "parent":
        payload["sections"][2]["parent_id"] = "S-2"
    else:
        payload["sections"][2]["depth"] = 2
    write_json(candidate_path, payload)
    candidate_sha = sha256_file(candidate_path)
    run_path = Path(entry["run_json_path"])
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["candidate_artifacts"]["json_sha256"] = candidate_sha
    write_json(run_path, run)
    run_sha = sha256_file(run_path)
    record = find_record(case, "WC-001")
    entry["candidate_json_sha256"] = record["candidate_json_sha256"] = candidate_sha
    entry["run_json_sha256"] = record["run_json_sha256"] = run_sha
    save_index(case)

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert expected_code in issue_codes(caught)
    fulltext_dir = case["index_path"].parent
    assert not (fulltext_dir / "technical_fulltext.json").exists()
    assert not (fulltext_dir / "technical_fulltext.preview.md").exists()
    assert not (fulltext_dir / "technical_fulltext.commit.json").exists()


def test_chapter_without_children_accepts_unique_candidate_sections(tmp_path: Path) -> None:
    outline = {"schema_version": "technical_writing_outline_v0", "chapters": [node("WC-006", "无子树章")]}
    payloads = {"WC-006": candidate("无子树章", [
        ("LOCAL-A", "自带一", "正文一"), ("LOCAL-B", "自带二", "正文二")
    ])}
    case = build_case(tmp_path, outline=outline, candidates=payloads, output_key="leaf_case")

    result = compose(case)

    assert result["composition_status"] == "complete"
    assert [item["depth"] for item in result["fulltext"]["chapters"][0]["section_outline"]] == [1, 1]


@pytest.mark.parametrize("failure", ["json", "markdown"])
def test_artifact_stage_failure_leaves_no_committed_or_half_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    case = build_case(tmp_path)
    if failure == "json":
        original = composer_module.atomic_write_json

        def fail_json(path: Path, value: dict[str, Any], *, required_fields: Any) -> str:
            if str(path).endswith(".pending") and "technical_fulltext.json" in Path(path).name:
                raise OSError("fixture JSON stage failure")
            return original(path, value, required_fields=required_fields)

        monkeypatch.setattr(composer_module, "atomic_write_json", fail_json)
    else:
        monkeypatch.setattr(
            composer_module,
            "atomic_write_markdown",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fixture Markdown stage failure")),
        )

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert "fulltext_artifact_commit_failed" in issue_codes(caught)
    fulltext_dir = case["index_path"].parent
    assert not (fulltext_dir / "technical_fulltext.json").exists()
    assert not (fulltext_dir / "technical_fulltext.preview.md").exists()
    assert not (fulltext_dir / "technical_fulltext.commit.json").exists()


@pytest.mark.parametrize("mutation", ["commit_missing", "json_hash", "markdown_hash"])
def test_inspector_requires_valid_commit_and_artifact_hashes(tmp_path: Path, mutation: str) -> None:
    case = build_case(tmp_path)
    result = compose(case)
    json_path = Path(result["outputs"]["technical_fulltext_json"])
    commit_path = Path(result["outputs"]["technical_fulltext_commit"])
    if mutation == "commit_missing":
        commit_path.unlink()
    elif mutation == "json_hash":
        json_path.write_text(json_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    else:
        preview_path = Path(result["outputs"]["technical_fulltext_preview"])
        preview_path.write_text(preview_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(TechnicalFulltextCompositionError):
        inspect_technical_fulltext(json_path)


def test_failed_recomposition_invalidates_prior_commit(tmp_path: Path) -> None:
    case = build_case(tmp_path)
    first = compose(case)
    json_path = Path(first["outputs"]["technical_fulltext_json"])
    commit_path = Path(first["outputs"]["technical_fulltext_commit"])
    assert commit_path.is_file()
    case["index"]["production_status"] = "incomplete"
    save_index(case)

    with pytest.raises(TechnicalFulltextCompositionError) as caught:
        compose(case)

    assert "production_not_complete" in issue_codes(caught)
    assert not commit_path.exists()
    with pytest.raises(TechnicalFulltextCompositionError):
        inspect_technical_fulltext(json_path)


def test_inspector_accepts_committed_set_and_returns_summary_only(tmp_path: Path) -> None:
    case = build_case(tmp_path)
    result = compose(case)

    summary = inspect_technical_fulltext(result["outputs"]["technical_fulltext_json"])

    assert summary["document_id"] == "DOC-STRICT"
    assert summary["chapter_order"] == ["WC-010", "WC-002"]
    assert summary["coverage_counts"] == {"requirements": 3, "score_items": 2, "placeholders": 2}
    assert summary["preview_only"] is True
    assert summary["release_ready"] is False
    assert "sections" not in summary["chapters"][0]


def test_repeated_composition_keeps_order_body_coverage_and_commit_references_stable(tmp_path: Path) -> None:
    case = recursive_case(tmp_path)
    first = compose(case)
    second = compose(case)

    def projection(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "order": result["fulltext"]["chapter_order"],
            "chapters": [
                (item["chapter_id"], item["sections"], item["section_outline"])
                for item in result["fulltext"]["chapters"]
            ],
            "coverage": result["fulltext"]["coverage_summary"],
            "issue_ids": [item["issue_id"] for item in result["fulltext"]["composition_issues"]],
        }

    assert projection(first) == projection(second)
    first_commit = json.loads(Path(first["outputs"]["technical_fulltext_commit"]).read_text(encoding="utf-8"))
    second_commit = json.loads(Path(second["outputs"]["technical_fulltext_commit"]).read_text(encoding="utf-8"))
    for key in (
        "manifest_path", "production_index_path", "technical_fulltext_json_path",
        "technical_fulltext_preview_path",
    ):
        assert first_commit[key] == second_commit[key]

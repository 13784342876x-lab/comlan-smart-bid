from __future__ import annotations

import hashlib
import json
import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


MANIFEST_SCHEMA_VERSION = "technical_fulltext_build_manifest_v2"
INDEX_SCHEMA_VERSION = "technical_fulltext_chapter_production_index_v1"
FULLTEXT_SCHEMA_VERSION = "technical_fulltext_v2"
COMMIT_SCHEMA_VERSION = "technical_fulltext_commit_v1"
RUN_SCHEMA_VERSION = "technical_writing_agent_run_v1"
PREVIEW_NOTICE = (
    "本文件为技术标全文组合预览。\n"
    "各章节由 TechnicalWritingAgent 分别生成并完成各自的章节审计和返修。\n"
    "本文件尚未经过全文机器审计和最终人工审核。\n"
    "本文件不得直接作为正式投标文件提交。"
)
SAFE_OUTPUT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
REQUIRED_CANDIDATE_FIELDS = (
    "chapter_title",
    "sections",
    "claims",
    "placeholders_used",
    "score_items_covered",
    "requirements_covered",
    "warnings",
)


class TechnicalFulltextCompositionError(RuntimeError):
    """Raised when composition qualification or artifact submission fails."""

    def __init__(self, message: str, *, issues: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.issues = list(issues or [])


@dataclass(frozen=True)
class OutlineSection:
    section_id: str
    title: str
    depth: int
    parent_id: str
    sibling_order: int
    document_order: int
    outline_path: tuple[str, ...]


@dataclass(frozen=True)
class ChapterArtifact:
    chapter_id: str
    order: int
    outline: dict[str, Any]
    outline_sections: tuple[OutlineSection, ...]
    candidate: dict[str, Any]
    run: dict[str, Any]
    candidate_path: Path
    run_path: Path
    events_path: Path
    candidate_sha256: str
    run_sha256: str
    events_sha256: str
    event_count: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json_object(path: str | Path, *, label: str) -> dict[str, Any]:
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON: {type(exc).__name__}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(
    path: str | Path,
    value: dict[str, Any],
    *,
    required_fields: Iterable[str],
) -> str:
    target = Path(path)
    _atomic_write_text(target, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    reparsed = load_json_object(target, label=target.name)
    missing = [field for field in required_fields if field not in reparsed]
    if missing:
        raise OSError(f"JSON artifact verification failed; missing fields: {missing}")
    if reparsed != value:
        raise OSError(f"JSON artifact changed during persistence: {target}")
    return sha256_file(target)


def atomic_write_markdown(path: str | Path, content: str, *, required_text: str) -> str:
    target = Path(path)
    _atomic_write_text(target, content)
    try:
        persisted = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OSError(f"Markdown artifact could not be reread: {target}") from exc
    if persisted != content or required_text not in persisted:
        raise OSError(f"Markdown artifact verification failed: {target}")
    return sha256_file(target)


def _resolve_reference(value: Any, source_path: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return Path("")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = source_path.parent / path
    return path.resolve()


def _outline_node_id(node: dict[str, Any]) -> str:
    return str(
        node.get("chapter_id") or node.get("section_id") or node.get("id") or ""
    ).strip()


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _issue(
    *,
    code: str,
    message: str,
    category: str,
    chapter_id: str | None = None,
    section_id: str | None = None,
    source_paths: Iterable[str | Path] = (),
    expected: Any = None,
    actual: Any = None,
    severity: str = "blocking",
    release_blocking: bool = True,
) -> dict[str, Any]:
    identity = {
        "category": category,
        "code": code,
        "chapter_id": chapter_id,
        "section_id": section_id,
        "expected": expected,
        "actual": actual,
    }
    return {
        "issue_id": "FCI-" + stable_json_sha256(identity)[:16].upper(),
        "severity": severity,
        "category": category,
        "code": code,
        "message": message,
        "chapter_id": chapter_id,
        "section_id": section_id,
        "source_paths": [str(item) for item in source_paths if str(item)],
        "expected": expected,
        "actual": actual,
        "release_blocking": release_blocking,
        "created_at": utc_now(),
    }


def issue_counts(issues: Iterable[dict[str, Any]]) -> dict[str, int]:
    result = {"fatal": 0, "blocking": 0, "warning": 0}
    for issue in issues:
        severity = str(issue.get("severity") or "blocking")
        if severity in result:
            result[severity] += 1
    return result


def build_standard_section_index(outline_chapter: dict[str, Any]) -> list[OutlineSection]:
    """Flatten all descendants in pre-order without inferring depth from IDs."""
    result: list[OutlineSection] = []

    def visit(nodes: Any, *, parent_id: str, depth: int, path: tuple[str, ...]) -> None:
        if not isinstance(nodes, list):
            return
        for sibling_order, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                continue
            section_id = _outline_node_id(node)
            title = str(node.get("title") or "").strip()
            section_path = (*path, section_id)
            result.append(OutlineSection(
                section_id=section_id,
                title=title,
                depth=depth,
                parent_id=parent_id,
                sibling_order=sibling_order,
                document_order=len(result) + 1,
                outline_path=section_path,
            ))
            visit(
                node.get("children") or [],
                parent_id=section_id,
                depth=depth + 1,
                path=section_path,
            )

    chapter_id = _outline_node_id(outline_chapter)
    visit(outline_chapter.get("children") or [], parent_id=chapter_id, depth=1, path=(chapter_id,))
    return result


def validate_chapter_events_jsonl(
    path: str | Path,
    *,
    chapter_id: str | None = None,
    expected_sha256: str | None = None,
) -> tuple[int, str | None, list[dict[str, Any]]]:
    target = Path(path).resolve()
    issues: list[dict[str, Any]] = []
    if not target.is_file():
        issues.append(_issue(
            code="events_file_missing",
            category="artifact",
            message=f"Events JSONL is missing for {chapter_id or 'chapter'}",
            chapter_id=chapter_id,
            source_paths=[target],
        ))
        return 0, None, issues
    if target.stat().st_size <= 0:
        issues.append(_issue(
            code="events_file_empty",
            category="artifact",
            message=f"Events JSONL is empty for {chapter_id or 'chapter'}",
            chapter_id=chapter_id,
            source_paths=[target],
        ))
        return 0, sha256_file(target), issues
    try:
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError) as exc:
        issues.append(_issue(
            code="invalid_events_jsonl",
            category="artifact",
            message=f"Events JSONL is unreadable: {type(exc).__name__}: {exc}",
            chapter_id=chapter_id,
            source_paths=[target],
        ))
        return 0, None, issues
    if not lines:
        issues.append(_issue(
            code="events_file_empty",
            category="artifact",
            message=f"Events JSONL has no non-empty event for {chapter_id or 'chapter'}",
            chapter_id=chapter_id,
            source_paths=[target],
        ))
        return 0, sha256_file(target), issues
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(_issue(
                code="invalid_events_jsonl",
                category="artifact",
                message=f"Events JSONL line {line_number} is invalid JSON: {exc.msg}",
                chapter_id=chapter_id,
                source_paths=[target],
                actual={"line_number": line_number},
            ))
            break
        if not isinstance(event, dict):
            issues.append(_issue(
                code="invalid_events_jsonl",
                category="artifact",
                message=f"Events JSONL line {line_number} is not a JSON object",
                chapter_id=chapter_id,
                source_paths=[target],
                actual={"line_number": line_number},
            ))
            break
    actual_sha = sha256_file(target)
    if expected_sha256 is not None and actual_sha != expected_sha256:
        issues.append(_issue(
            code="events_hash_mismatch",
            category="artifact",
            message=f"Events hash does not match for {chapter_id or 'chapter'}",
            chapter_id=chapter_id,
            source_paths=[target],
            expected=expected_sha256,
            actual=actual_sha,
        ))
    return len(lines), actual_sha, issues


def _candidate_schema() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[1]
        / "agents"
        / "technical_writer"
        / "chapter_draft_output_schema.json"
    )
    return load_json_object(path, label="chapter candidate schema")


def _candidate_for_schema_validation(candidate: dict[str, Any]) -> dict[str, Any]:
    """Allow optional structural hints without changing the existing Agent schema."""
    value = deepcopy(candidate)
    for section in value.get("sections") or []:
        if isinstance(section, dict):
            section.pop("parent_id", None)
            section.pop("depth", None)
    return value


def _run_candidate_hash(run: dict[str, Any]) -> str:
    artifacts = run.get("candidate_artifacts")
    return str((artifacts or {}).get("json_sha256") or "").strip() if isinstance(artifacts, dict) else ""


def validate_chapter_artifact_pair(
    *,
    chapter_id: str,
    outline_chapter: dict[str, Any],
    candidate_path: str | Path,
    run_path: str | Path,
    events_path: str | Path | None = None,
    expected_candidate_sha256: str | None = None,
    expected_run_sha256: str | None = None,
    expected_events_sha256: str | None = None,
    expected_output_key: str | None = None,
    validate_structure: bool = True,
    require_submission_ready: bool = False,
) -> tuple[ChapterArtifact | None, list[dict[str, Any]]]:
    """Validate one explicit Candidate/Run/Events set without reading Markdown."""
    candidate_file = Path(candidate_path).resolve()
    run_file = Path(run_path).resolve()
    events_file = Path(events_path).resolve() if events_path is not None else Path("")
    issues: list[dict[str, Any]] = []
    if not candidate_file.is_file():
        issues.append(_issue(
            code="candidate_file_missing", category="artifact",
            message=f"Candidate JSON is missing for {chapter_id}", chapter_id=chapter_id,
            source_paths=[candidate_file],
        ))
    if not run_file.is_file():
        issues.append(_issue(
            code="run_file_missing", category="artifact",
            message=f"Run JSON is missing for {chapter_id}", chapter_id=chapter_id,
            source_paths=[run_file],
        ))
    if issues:
        return None, issues
    try:
        candidate = load_json_object(candidate_file, label=f"Candidate {chapter_id}")
    except ValueError as exc:
        issues.append(_issue(
            code="invalid_candidate_schema", category="artifact", message=str(exc),
            chapter_id=chapter_id, source_paths=[candidate_file],
        ))
        return None, issues
    try:
        run = load_json_object(run_file, label=f"Run {chapter_id}")
    except ValueError as exc:
        issues.append(_issue(
            code="run_file_missing", category="artifact", message=str(exc),
            chapter_id=chapter_id, source_paths=[run_file],
        ))
        return None, issues

    candidate_sha = sha256_file(candidate_file)
    run_sha = sha256_file(run_file)
    if expected_candidate_sha256 is not None and candidate_sha != expected_candidate_sha256:
        issues.append(_issue(
            code="candidate_hash_mismatch", category="artifact",
            message=f"Candidate hash does not match for {chapter_id}", chapter_id=chapter_id,
            source_paths=[candidate_file], expected=expected_candidate_sha256, actual=candidate_sha,
        ))
    if expected_run_sha256 is not None and run_sha != expected_run_sha256:
        issues.append(_issue(
            code="run_hash_mismatch", category="artifact",
            message=f"Run hash does not match for {chapter_id}", chapter_id=chapter_id,
            source_paths=[run_file], expected=expected_run_sha256, actual=run_sha,
        ))
    if run.get("status") != "completed":
        issues.append(_issue(
            code="run_status_not_completed", category="artifact",
            message=f"Run status is not completed for {chapter_id}", chapter_id=chapter_id,
            source_paths=[run_file], expected="completed", actual=run.get("status"),
        ))
    run_output_key = (run.get("run_summary") or {}).get("output_key") or run.get("output_key")
    if expected_output_key is not None and str(run_output_key or "") != expected_output_key:
        issues.append(_issue(
            code="run_output_key_mismatch", category="artifact",
            message=f"Run output_key does not match production for {chapter_id}",
            chapter_id=chapter_id, source_paths=[run_file],
            expected=expected_output_key, actual=run_output_key,
        ))
    target = run.get("target") if isinstance(run.get("target"), dict) else {}
    if (
        run.get("schema_version") != RUN_SCHEMA_VERSION
        or str(target.get("chapter_id") or "") != chapter_id
        or target.get("section_id") not in (None, "")
    ):
        issues.append(_issue(
            code="run_target_mismatch", category="artifact",
            message=f"Run target does not identify complete chapter {chapter_id}",
            chapter_id=chapter_id, source_paths=[run_file],
            expected={"schema_version": RUN_SCHEMA_VERSION, "chapter_id": chapter_id, "section_id": None},
            actual={"schema_version": run.get("schema_version"), "target": target},
        ))
    flags = {
        name: bool(run.get(name))
        for name in ("candidate_generated", "candidate_saved", "candidate_available", "candidate_reviewable")
    }
    if not all(flags.values()):
        issues.append(_issue(
            code="candidate_not_reviewable", category="artifact",
            message=f"Run does not expose a reviewable Candidate for {chapter_id}",
            chapter_id=chapter_id, source_paths=[run_file, candidate_file],
            expected={name: True for name in flags}, actual=flags,
        ))
    counts = run.get("issue_counts") if isinstance(run.get("issue_counts"), dict) else {}
    fatal_count = int(counts.get("fatal") or 0)
    blocking_count = int(counts.get("blocking") or 0)
    if require_submission_ready and not bool(run.get("submission_ready")):
        issues.append(_issue(
            code="production_chapter_not_submission_ready", category="artifact",
            message=f"Chapter {chapter_id} is not submission ready", chapter_id=chapter_id,
            source_paths=[run_file], expected=True, actual=run.get("submission_ready"),
        ))
    if require_submission_ready and (fatal_count > 0 or blocking_count > 0):
        issues.append(_issue(
            code="production_chapter_has_blockers", category="artifact",
            message=f"Chapter {chapter_id} still has fatal or blocking issues", chapter_id=chapter_id,
            source_paths=[run_file], expected={"fatal": 0, "blocking": 0},
            actual={"fatal": fatal_count, "blocking": blocking_count},
        ))
    recorded_candidate_sha = _run_candidate_hash(run)
    if not recorded_candidate_sha or recorded_candidate_sha != candidate_sha:
        issues.append(_issue(
            code="candidate_hash_mismatch", category="artifact",
            message=f"Run Candidate hash does not match Candidate file for {chapter_id}",
            chapter_id=chapter_id, source_paths=[run_file, candidate_file],
            expected=recorded_candidate_sha or "<missing>", actual=candidate_sha,
        ))

    schema_candidate = _candidate_for_schema_validation(candidate)
    schema_errors = sorted(
        Draft202012Validator(_candidate_schema()).iter_errors(schema_candidate),
        key=lambda error: list(error.absolute_path),
    )
    missing_fields = [field for field in REQUIRED_CANDIDATE_FIELDS if field not in candidate]
    if schema_errors or missing_fields:
        details = [error.message for error in schema_errors[:5]]
        issues.append(_issue(
            code="invalid_candidate_schema", category="artifact",
            message=f"Candidate Schema is invalid for {chapter_id}: " + "; ".join(details or [f"missing {missing_fields}"]),
            chapter_id=chapter_id, source_paths=[candidate_file],
            expected="chapter_draft_output_schema.json", actual=details or missing_fields,
        ))

    event_count = 0
    events_sha: str | None = None
    if events_path is not None:
        event_count, events_sha, event_issues = validate_chapter_events_jsonl(
            events_file, chapter_id=chapter_id, expected_sha256=expected_events_sha256
        )
        issues.extend(event_issues)

    fatal_codes = {
        "candidate_file_missing", "run_file_missing", "candidate_hash_mismatch",
        "run_hash_mismatch", "run_status_not_completed", "run_output_key_mismatch", "run_target_mismatch",
        "candidate_not_reviewable", "production_chapter_not_submission_ready",
        "production_chapter_has_blockers", "invalid_candidate_schema",
        "events_file_missing", "events_file_empty", "invalid_events_jsonl", "events_hash_mismatch",
    }
    if any(item["code"] in fatal_codes for item in issues):
        return None, issues
    outline_sections = tuple(build_standard_section_index(outline_chapter))
    if validate_structure:
        issues.extend(_validate_candidate_structure(
            chapter_id, outline_chapter, outline_sections, candidate, candidate_file
        ))
    if any(item.get("release_blocking") for item in issues):
        return None, issues
    return ChapterArtifact(
        chapter_id=chapter_id,
        order=0,
        outline=outline_chapter,
        outline_sections=outline_sections,
        candidate=candidate,
        run=run,
        candidate_path=candidate_file,
        run_path=run_file,
        events_path=events_file,
        candidate_sha256=candidate_sha,
        run_sha256=run_sha,
        events_sha256=events_sha or "",
        event_count=event_count,
    ), issues


def _validate_candidate_structure(
    chapter_id: str,
    outline_chapter: dict[str, Any],
    outline_sections: tuple[OutlineSection, ...],
    candidate: dict[str, Any],
    candidate_path: Path,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected_title = str(outline_chapter.get("title") or "").strip()
    actual_title = str(candidate.get("chapter_title") or "").strip()
    if actual_title != expected_title:
        issues.append(_issue(
            code="chapter_title_mismatch", category="structure",
            message=f"Candidate chapter title does not match standard outline for {chapter_id}",
            chapter_id=chapter_id, source_paths=[candidate_path],
            expected=expected_title, actual=actual_title,
        ))
    sections = list(candidate.get("sections") or [])
    actual_ids = [str(item.get("section_id") or "").strip() for item in sections if isinstance(item, dict)]
    for section_id in _duplicates(actual_ids):
        issues.append(_issue(
            code="duplicate_section", category="structure",
            message=f"Candidate contains duplicate section ID {section_id or '<empty>'}",
            chapter_id=chapter_id, section_id=section_id or None, source_paths=[candidate_path],
        ))

    if not outline_sections:
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("section_id") or "").strip()
            heading = str(section.get("heading") or "").strip()
            if not section_id or not heading:
                issues.append(_issue(
                    code="unexpected_section", category="structure",
                    message="A chapter without standard children requires non-empty unique Candidate section IDs and titles",
                    chapter_id=chapter_id, section_id=section_id or None, source_paths=[candidate_path],
                ))
        return issues

    expected_ids = [item.section_id for item in outline_sections]
    expected_by_id = {item.section_id: item for item in outline_sections}
    for expected in outline_sections:
        if expected.section_id not in actual_ids:
            issues.append(_issue(
                code="missing_section", category="structure",
                message=f"Candidate is missing standard section {expected.section_id}",
                chapter_id=chapter_id, section_id=expected.section_id, source_paths=[candidate_path],
            ))
    for section_id in _unique_strings(actual_ids):
        if section_id not in expected_by_id:
            issues.append(_issue(
                code="unexpected_section", category="structure",
                message=f"Candidate contains unexpected section {section_id}",
                chapter_id=chapter_id, section_id=section_id, source_paths=[candidate_path],
            ))
    if actual_ids != expected_ids:
        issues.append(_issue(
            code="section_order_mismatch", category="structure",
            message=f"Candidate section order does not match recursive standard outline for {chapter_id}",
            chapter_id=chapter_id, source_paths=[candidate_path],
            expected=expected_ids, actual=actual_ids,
        ))
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "").strip()
        expected = expected_by_id.get(section_id)
        if expected is None:
            continue
        heading = str(section.get("heading") or "").strip()
        if heading != expected.title:
            issues.append(_issue(
                code="section_title_mismatch", category="structure",
                message=f"Candidate section title does not match standard outline for {section_id}",
                chapter_id=chapter_id, section_id=section_id, source_paths=[candidate_path],
                expected=expected.title, actual=heading,
            ))
        if "parent_id" in section and str(section.get("parent_id") or "") != expected.parent_id:
            issues.append(_issue(
                code="section_parent_mismatch", category="structure",
                message=f"Candidate parent_id does not match standard outline for {section_id}",
                chapter_id=chapter_id, section_id=section_id, source_paths=[candidate_path],
                expected=expected.parent_id, actual=section.get("parent_id"),
            ))
        if "depth" in section:
            try:
                actual_depth = int(section.get("depth"))
            except (TypeError, ValueError):
                actual_depth = -1
            if actual_depth != expected.depth:
                issues.append(_issue(
                    code="section_depth_mismatch", category="structure",
                    message=f"Candidate depth does not match standard outline for {section_id}",
                    chapter_id=chapter_id, section_id=section_id, source_paths=[candidate_path],
                    expected=expected.depth, actual=section.get("depth"),
                ))
    return issues


class TechnicalFulltextComposer:
    """Strictly qualify and deterministically compose explicit chapter artifacts."""

    def __init__(self, *, output_dir: str | Path = "outputs") -> None:
        self.output_dir = Path(output_dir)

    def compose(self, *, manifest_path: str | Path, output_key: str) -> dict[str, Any]:
        output_key = str(output_key or "").strip()
        if not SAFE_OUTPUT_KEY.fullmatch(output_key) or ".." in output_key:
            raise TechnicalFulltextCompositionError("unsafe output_key")
        # A commit is the sole validity marker for a composed set.  Invalidate
        # any earlier marker before this attempt so a failed re-composition can
        # never leave stale artifacts inspectable as the current result.
        commit_path = (
            self.output_dir / output_key / "fulltext" / "technical_fulltext.commit.json"
        ).resolve()
        try:
            commit_path.unlink(missing_ok=True)
        except OSError as exc:
            issue = _issue(
                code="fulltext_commit_invalidation_failed",
                category="artifact",
                message=f"Cannot invalidate the prior fulltext commit: {type(exc).__name__}: {exc}",
                source_paths=[commit_path],
            )
            raise TechnicalFulltextCompositionError(
                "technical fulltext commit invalidation failed", issues=[issue]
            ) from exc
        manifest_file = Path(manifest_path).expanduser().resolve()
        try:
            manifest = load_json_object(manifest_file, label="fulltext build manifest")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextCompositionError(str(exc)) from exc

        issues: list[dict[str, Any]] = []
        self._validate_manifest_header(manifest, manifest_file, output_key, issues)
        outline_path, outline, outline_sha, canonical_chapters = self._load_outline(
            manifest, manifest_file, issues
        )
        manifest_entries = self._validate_manifest_chapters(
            manifest, manifest_file, canonical_chapters, issues
        )
        if issues:
            self._raise_failed(issues)

        production_path, production_sha, production = self._load_and_validate_production_index(
            manifest=manifest,
            manifest_path=manifest_file,
            output_key=output_key,
            canonical_chapters=canonical_chapters,
            manifest_entries=manifest_entries,
        )

        artifacts: list[ChapterArtifact] = []
        for order, outline_chapter in enumerate(canonical_chapters, start=1):
            chapter_id = _outline_node_id(outline_chapter)
            entry = manifest_entries[chapter_id]
            artifact, chapter_issues = validate_chapter_artifact_pair(
                chapter_id=chapter_id,
                outline_chapter=outline_chapter,
                candidate_path=_resolve_reference(entry.get("candidate_json_path"), manifest_file),
                run_path=_resolve_reference(entry.get("run_json_path"), manifest_file),
                events_path=_resolve_reference(entry.get("events_jsonl_path"), manifest_file),
                expected_candidate_sha256=str(entry.get("candidate_json_sha256") or ""),
                expected_run_sha256=str(entry.get("run_json_sha256") or ""),
                expected_events_sha256=str(entry.get("events_jsonl_sha256") or ""),
                expected_output_key=output_key,
                validate_structure=True,
                # Content/release blockers belong to the chapter Agent and later
                # review stages.  Composition only requires a trustworthy,
                # reviewable artifact set.
                require_submission_ready=False,
            )
            issues.extend(chapter_issues)
            if artifact is not None:
                artifacts.append(ChapterArtifact(**{**artifact.__dict__, "order": order}))
        if issues or len(artifacts) != len(canonical_chapters):
            self._raise_failed(issues)

        has_content_blockers = production.get("production_status") == "complete_with_blockers" or any(
            not bool(item.run.get("submission_ready"))
            or int((item.run.get("issue_counts") or {}).get("fatal") or 0) > 0
            or int((item.run.get("issue_counts") or {}).get("blocking") or 0) > 0
            or str(item.run.get("validation_status") or "").lower() == "blocked"
            for item in artifacts
        )
        composition_status = "completed_with_blockers" if has_content_blockers else "complete"
        fulltext = self._build_fulltext(
            manifest=manifest,
            manifest_path=manifest_file,
            outline=outline,
            outline_path=outline_path,
            outline_sha=outline_sha,
            production=production,
            production_path=production_path,
            production_sha=production_sha,
            artifacts=artifacts,
            output_key=output_key,
            composition_status=composition_status,
        )
        markdown = self._render_preview(fulltext)
        outputs = self._commit_artifact_set(
            fulltext=fulltext,
            markdown=markdown,
            manifest=manifest,
            manifest_path=manifest_file,
            production_path=production_path,
            production_sha=production_sha,
            output_key=output_key,
        )
        return {
            "status": "completed",
            "composition_status": composition_status,
            "fulltext": fulltext,
            "composition_issues": [],
            "composition_issue_counts": issue_counts([]),
            "outputs": outputs,
        }

    @staticmethod
    def _raise_failed(issues: list[dict[str, Any]]) -> None:
        codes = ", ".join(_unique_strings(item.get("code") for item in issues))
        raise TechnicalFulltextCompositionError(
            f"technical fulltext composition failed: {codes or 'unknown_error'}",
            issues=issues,
        )

    @staticmethod
    def _validate_manifest_header(
        manifest: dict[str, Any], manifest_path: Path, output_key: str, issues: list[dict[str, Any]]
    ) -> None:
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            issues.append(_issue(
                code="invalid_manifest_schema", category="input_protocol",
                message="Manifest schema_version is invalid", source_paths=[manifest_path],
                expected=MANIFEST_SCHEMA_VERSION, actual=manifest.get("schema_version"),
            ))
        if not str(manifest.get("document_id") or "").strip():
            issues.append(_issue(
                code="invalid_manifest_schema", category="input_protocol",
                message="Manifest document_id must be non-empty", source_paths=[manifest_path],
            ))
        if str(manifest.get("output_key") or "") != output_key:
            issues.append(_issue(
                code="invalid_manifest_schema", category="input_protocol",
                message="Manifest output_key does not match requested output key",
                source_paths=[manifest_path], expected=output_key, actual=manifest.get("output_key"),
            ))

    def _load_outline(
        self, manifest: dict[str, Any], manifest_path: Path, issues: list[dict[str, Any]]
    ) -> tuple[Path, dict[str, Any], str, list[dict[str, Any]]]:
        outline_path = _resolve_reference(manifest.get("standard_outline_path"), manifest_path)
        if not outline_path.is_file():
            issues.append(_issue(
                code="standard_outline_missing", category="input_protocol",
                message="Standard writing outline is missing", source_paths=[outline_path],
            ))
            self._raise_failed(issues)
        try:
            outline = load_json_object(outline_path, label="standard writing outline")
        except ValueError as exc:
            issues.append(_issue(
                code="invalid_standard_outline", category="input_protocol", message=str(exc),
                source_paths=[outline_path],
            ))
            self._raise_failed(issues)
        outline_sha = sha256_file(outline_path)
        if outline_sha != str(manifest.get("standard_outline_sha256") or ""):
            issues.append(_issue(
                code="standard_outline_hash_mismatch", category="input_protocol",
                message="Standard outline hash does not match Manifest",
                source_paths=[outline_path, manifest_path],
                expected=manifest.get("standard_outline_sha256"), actual=outline_sha,
            ))
        chapters = outline.get("chapters")
        if not isinstance(chapters, list) or not chapters or any(not isinstance(item, dict) for item in chapters):
            issues.append(_issue(
                code="invalid_standard_outline", category="input_protocol",
                message="Standard outline chapters must be a non-empty object list",
                source_paths=[outline_path],
            ))
            self._raise_failed(issues)
        chapter_ids = [_outline_node_id(item) for item in chapters]
        if any(not value for value in chapter_ids) or _duplicates(chapter_ids):
            issues.append(_issue(
                code="invalid_standard_outline", category="input_protocol",
                message="Standard chapter IDs must be non-empty and unique",
                source_paths=[outline_path], actual=chapter_ids,
            ))
        for chapter in chapters:
            if not str(chapter.get("title") or "").strip():
                issues.append(_issue(
                    code="invalid_standard_outline", category="input_protocol",
                    message="Standard chapter titles must be non-empty", source_paths=[outline_path],
                    chapter_id=_outline_node_id(chapter),
                ))
            flattened = build_standard_section_index(chapter)
            section_ids = [item.section_id for item in flattened]
            malformed_tree = False

            def check_children(nodes: Any) -> None:
                nonlocal malformed_tree
                if not isinstance(nodes, list):
                    malformed_tree = True
                    return
                for child in nodes:
                    if not isinstance(child, dict):
                        malformed_tree = True
                        continue
                    check_children(child.get("children", []))

            check_children(chapter.get("children", []))
            if any(not item.section_id or not item.title for item in flattened) or _duplicates(section_ids):
                issues.append(_issue(
                    code="invalid_standard_outline", category="input_protocol",
                    message="Recursive standard section IDs and titles must be non-empty and IDs unique",
                    source_paths=[outline_path], chapter_id=_outline_node_id(chapter), actual=section_ids,
                ))
            if malformed_tree:
                issues.append(_issue(
                    code="invalid_standard_outline", category="input_protocol",
                    message="Recursive standard outline children must be lists of objects",
                    source_paths=[outline_path], chapter_id=_outline_node_id(chapter),
                ))
        return outline_path, outline, outline_sha, chapters

    @staticmethod
    def _validate_manifest_chapters(
        manifest: dict[str, Any],
        manifest_path: Path,
        canonical_chapters: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        entries = manifest.get("chapters")
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            issues.append(_issue(
                code="invalid_manifest_schema", category="input_protocol",
                message="Manifest chapters must be an object list", source_paths=[manifest_path],
            ))
            return {}
        expected_ids = [_outline_node_id(item) for item in canonical_chapters]
        actual_ids = [str(item.get("chapter_id") or "").strip() for item in entries]
        for chapter_id in expected_ids:
            if chapter_id not in actual_ids:
                issues.append(_issue(
                    code="missing_chapter", category="manifest",
                    message=f"Manifest is missing chapter {chapter_id}", chapter_id=chapter_id,
                    source_paths=[manifest_path],
                ))
        for chapter_id in _duplicates(actual_ids):
            issues.append(_issue(
                code="duplicate_chapter", category="manifest",
                message=f"Manifest contains duplicate chapter {chapter_id}", chapter_id=chapter_id or None,
                source_paths=[manifest_path],
            ))
        for chapter_id in _unique_strings(actual_ids):
            if chapter_id not in expected_ids:
                issues.append(_issue(
                    code="unknown_chapter", category="manifest",
                    message=f"Manifest contains unknown chapter {chapter_id}", chapter_id=chapter_id,
                    source_paths=[manifest_path],
                ))
        return {str(item.get("chapter_id") or "").strip(): item for item in entries}

    def _load_and_validate_production_index(
        self,
        *,
        manifest: dict[str, Any],
        manifest_path: Path,
        output_key: str,
        canonical_chapters: list[dict[str, Any]],
        manifest_entries: dict[str, dict[str, Any]],
    ) -> tuple[Path, str, dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        generated_from = manifest.get("generated_from")
        if not isinstance(generated_from, dict) or not generated_from.get("chapter_production_index_path") or not generated_from.get("chapter_production_index_sha256"):
            issues.append(_issue(
                code="production_index_reference_missing", category="input_protocol",
                message="Manifest must reference the chapter production index path and SHA-256",
                source_paths=[manifest_path],
            ))
            self._raise_failed(issues)
        production_path = _resolve_reference(
            generated_from.get("chapter_production_index_path"), manifest_path
        )
        try:
            production = load_json_object(production_path, label="chapter production index")
        except (FileNotFoundError, ValueError) as exc:
            issues.append(_issue(
                code="production_index_unavailable", category="input_protocol",
                message=str(exc), source_paths=[production_path],
            ))
            self._raise_failed(issues)
        production_sha = sha256_file(production_path)
        recorded_sha = str(generated_from.get("chapter_production_index_sha256") or "")
        if production_sha != recorded_sha:
            issues.append(_issue(
                code="production_index_hash_mismatch", category="input_protocol",
                message="Production index hash does not match Manifest",
                source_paths=[production_path, manifest_path], expected=recorded_sha, actual=production_sha,
            ))
        if production.get("schema_version") != INDEX_SCHEMA_VERSION:
            issues.append(_issue(
                code="invalid_production_index_schema", category="input_protocol",
                message="Production index schema_version is invalid", source_paths=[production_path],
                expected=INDEX_SCHEMA_VERSION, actual=production.get("schema_version"),
            ))
        if str(production.get("output_key") or "") != output_key:
            issues.append(_issue(
                code="production_output_key_mismatch", category="input_protocol",
                message="Production index output_key does not match",
                source_paths=[production_path], expected=output_key, actual=production.get("output_key"),
            ))
        expected_count = len(canonical_chapters)
        try:
            actual_count = int(production.get("chapter_count_expected"))
        except (TypeError, ValueError):
            actual_count = -1
        if actual_count != expected_count:
            issues.append(_issue(
                code="production_chapter_count_mismatch", category="input_protocol",
                message="Production index expected chapter count does not match standard outline",
                source_paths=[production_path], expected=expected_count, actual=production.get("chapter_count_expected"),
            ))
        actual_counts: dict[str, int] = {}
        for field in (
            "chapter_count_completed",
            "chapter_count_submission_ready",
            "chapter_count_blocked",
            "chapter_count_failed",
        ):
            try:
                actual_counts[field] = int(production.get(field))
            except (TypeError, ValueError):
                actual_counts[field] = -1
        counts_are_consistent = (
            actual_counts["chapter_count_completed"] == expected_count
            and actual_counts["chapter_count_failed"] == 0
            and actual_counts["chapter_count_submission_ready"] >= 0
            and actual_counts["chapter_count_blocked"] >= 0
            and actual_counts["chapter_count_submission_ready"]
            + actual_counts["chapter_count_blocked"]
            == actual_counts["chapter_count_completed"]
        )
        if not counts_are_consistent:
            issues.append(_issue(
                code="production_chapter_count_mismatch", category="input_protocol",
                message="Production index chapter counters are inconsistent with a finished production",
                source_paths=[production_path],
                expected={
                    "chapter_count_completed": expected_count,
                    "chapter_count_failed": 0,
                    "chapter_count_submission_ready_plus_blocked": expected_count,
                },
                actual=actual_counts,
            ))
        standard_outline = production.get("standard_outline")
        if isinstance(standard_outline, dict):
            recorded_outline_sha = str(standard_outline.get("sha256") or "")
            manifest_outline_sha = str(manifest.get("standard_outline_sha256") or "")
            if recorded_outline_sha and recorded_outline_sha != manifest_outline_sha:
                issues.append(_issue(
                    code="invalid_production_index_schema", category="input_protocol",
                    message="Production index standard outline hash does not match Manifest",
                    source_paths=[production_path, manifest_path],
                    expected=manifest_outline_sha, actual=recorded_outline_sha,
                ))
        if production.get("production_status") not in {"complete", "complete_with_blockers"}:
            issues.append(_issue(
                code="production_not_complete", category="input_protocol",
                message="Production index is incomplete and cannot be composed",
                source_paths=[production_path],
                expected=["complete", "complete_with_blockers"],
                actual=production.get("production_status"),
            ))

        production_chapters = production.get("chapters")
        if not isinstance(production_chapters, list) or any(not isinstance(item, dict) for item in production_chapters):
            issues.append(_issue(
                code="invalid_production_index_schema", category="input_protocol",
                message="Production index chapters must be an object list", source_paths=[production_path],
            ))
            self._raise_failed(issues)
        production_ids = [str(item.get("chapter_id") or "").strip() for item in production_chapters]
        production_by_id = {str(item.get("chapter_id") or "").strip(): item for item in production_chapters}
        expected_ids = [_outline_node_id(item) for item in canonical_chapters]
        if _duplicates(production_ids) or any(value not in expected_ids for value in production_ids):
            issues.append(_issue(
                code="invalid_production_index_schema", category="input_protocol",
                message="Production index contains duplicate or unknown chapter IDs",
                source_paths=[production_path], actual=production_ids,
            ))
        outline_by_id = {_outline_node_id(item): item for item in canonical_chapters}
        for expected_order, chapter_id in enumerate(expected_ids, start=1):
            record = production_by_id.get(chapter_id)
            if record is None:
                issues.append(_issue(
                    code="production_chapter_missing", category="input_protocol",
                    message=f"Production index is missing chapter {chapter_id}", chapter_id=chapter_id,
                    source_paths=[production_path],
                ))
                continue
            self._validate_production_chapter_record(
                chapter_id=chapter_id,
                expected_title=str(outline_by_id[chapter_id].get("title") or ""),
                expected_order=expected_order,
                record=record,
                manifest_entry=manifest_entries[chapter_id],
                production_path=production_path,
                manifest_path=manifest_path,
                issues=issues,
            )
        completed_records = [
            item for item in production_chapters if item.get("status") == "completed"
        ]
        failed_records = [
            item for item in production_chapters if item.get("status") == "failed"
        ]

        def record_is_submission_ready(item: dict[str, Any]) -> bool:
            counts = item.get("issue_counts") if isinstance(item.get("issue_counts"), dict) else {}
            try:
                fatal = int(counts.get("fatal") or 0)
                blocking = int(counts.get("blocking") or 0)
            except (TypeError, ValueError):
                return False
            return (
                bool(item.get("submission_ready"))
                and fatal == 0
                and blocking == 0
                and str(item.get("validation_status") or "").lower() != "blocked"
            )

        ready_records = [item for item in completed_records if record_is_submission_ready(item)]
        derived_counts = {
            "chapter_count_completed": len(completed_records),
            "chapter_count_submission_ready": len(ready_records),
            "chapter_count_blocked": len(completed_records) - len(ready_records),
            "chapter_count_failed": len(failed_records),
        }
        if actual_counts != derived_counts:
            issues.append(_issue(
                code="production_chapter_count_mismatch", category="input_protocol",
                message="Production index counters do not match its chapter records",
                source_paths=[production_path], expected=derived_counts, actual=actual_counts,
            ))
        derived_status = (
            "complete"
            if len(completed_records) == expected_count and not failed_records and not derived_counts["chapter_count_blocked"]
            else "complete_with_blockers"
            if len(completed_records) == expected_count and not failed_records
            else "incomplete"
        )
        if production.get("production_status") != derived_status:
            issues.append(_issue(
                code="production_not_complete", category="input_protocol",
                message="Production status does not match its chapter records",
                source_paths=[production_path], expected=derived_status,
                actual=production.get("production_status"),
            ))
        if issues:
            self._raise_failed(issues)
        return production_path, production_sha, production

    @staticmethod
    def _validate_production_chapter_record(
        *,
        chapter_id: str,
        expected_title: str,
        expected_order: int,
        record: dict[str, Any],
        manifest_entry: dict[str, Any],
        production_path: Path,
        manifest_path: Path,
        issues: list[dict[str, Any]],
    ) -> None:
        try:
            actual_order = int(record.get("chapter_order") or 0)
        except (TypeError, ValueError):
            actual_order = 0
        if (
            str(record.get("chapter_title") or "") != expected_title
            or actual_order != expected_order
        ):
            issues.append(_issue(
                code="invalid_production_index_schema", category="input_protocol",
                message=f"Production chapter title or order does not match standard outline for {chapter_id}",
                chapter_id=chapter_id, source_paths=[production_path],
                expected={"chapter_title": expected_title, "chapter_order": expected_order},
                actual={"chapter_title": record.get("chapter_title"), "chapter_order": record.get("chapter_order")},
            ))
        if record.get("status") != "completed":
            issues.append(_issue(
                code="production_chapter_not_completed", category="input_protocol",
                message=f"Production chapter {chapter_id} is not completed", chapter_id=chapter_id,
                source_paths=[production_path], expected="completed", actual=record.get("status"),
            ))
        if not all(bool(record.get(name)) for name in ("candidate_generated", "candidate_saved", "candidate_reviewable")):
            issues.append(_issue(
                code="production_chapter_not_reviewable", category="input_protocol",
                message=f"Production chapter {chapter_id} has no reviewable Candidate", chapter_id=chapter_id,
                source_paths=[production_path],
            ))
        # submission_ready, validation_status and chapter issue severities are
        # informational at preview-composition time.  They remain in metadata,
        # but do not redefine an Agent-completed, reviewable chapter as failed.
        required_references = (
            "candidate_json_path", "candidate_json_sha256", "run_json_path", "run_json_sha256",
            "events_jsonl_path", "events_jsonl_sha256",
        )
        if any(not str(record.get(name) or "").strip() for name in required_references):
            issues.append(_issue(
                code="production_chapter_artifact_reference_missing", category="input_protocol",
                message=f"Production chapter {chapter_id} has incomplete artifact references",
                chapter_id=chapter_id, source_paths=[production_path],
            ))
            return
        comparisons = (
            ("candidate_json_path", "production_manifest_candidate_path_mismatch", True),
            ("candidate_json_sha256", "production_manifest_candidate_hash_mismatch", False),
            ("run_json_path", "production_manifest_run_path_mismatch", True),
            ("run_json_sha256", "production_manifest_run_hash_mismatch", False),
            ("events_jsonl_path", "production_manifest_events_path_mismatch", True),
            ("events_jsonl_sha256", "production_manifest_events_hash_mismatch", False),
        )
        for field, code, is_path in comparisons:
            production_value: Any = record.get(field)
            manifest_value: Any = manifest_entry.get(field)
            if is_path:
                production_value = _resolve_reference(production_value, production_path)
                manifest_value = _resolve_reference(manifest_value, manifest_path)
            else:
                production_value = str(production_value or "")
                manifest_value = str(manifest_value or "")
            if production_value != manifest_value:
                issues.append(_issue(
                    code=code, category="input_protocol",
                    message=f"Production index and Manifest disagree on {field} for {chapter_id}",
                    chapter_id=chapter_id, source_paths=[production_path, manifest_path],
                    expected=str(production_value), actual=str(manifest_value),
                ))

    @staticmethod
    def _build_fulltext(
        *,
        manifest: dict[str, Any],
        manifest_path: Path,
        outline: dict[str, Any],
        outline_path: Path,
        outline_sha: str,
        production: dict[str, Any],
        production_path: Path,
        production_sha: str,
        artifacts: list[ChapterArtifact],
        output_key: str,
        composition_status: str,
    ) -> dict[str, Any]:
        expected = len(artifacts)
        return {
            "schema_version": FULLTEXT_SCHEMA_VERSION,
            "document_id": str(manifest.get("document_id")),
            "output_key": output_key,
            "generated_at": utc_now(),
            "standard_outline": {
                "original_path": str(manifest.get("standard_outline_path") or ""),
                "resolved_path": str(outline_path),
                "schema_version": outline.get("schema_version"),
                "sha256": outline_sha,
            },
            "production": {
                "chapter_production_index_path": str(production_path),
                "chapter_production_index_sha256": production_sha,
                "production_status": production.get("production_status"),
                "chapter_count_expected": int(production.get("chapter_count_expected") or expected),
                "chapter_count_completed": int(production.get("chapter_count_completed") or 0),
                "chapter_count_submission_ready": int(production.get("chapter_count_submission_ready") or 0),
                "chapter_count_blocked": int(production.get("chapter_count_blocked") or 0),
                "chapter_count_failed": int(production.get("chapter_count_failed") or 0),
            },
            "composition_status": composition_status,
            "preview_only": True,
            "release_ready": False,
            "chapter_order": [item.chapter_id for item in artifacts],
            "chapters": [TechnicalFulltextComposer._chapter_payload(item) for item in artifacts],
            "coverage_summary": TechnicalFulltextComposer._coverage_summary(artifacts),
            "composition_issues": [],
            "source_manifest": {
                "path": str(manifest_path),
                "sha256": sha256_file(manifest_path),
            },
        }

    @staticmethod
    def _chapter_payload(artifact: ChapterArtifact) -> dict[str, Any]:
        candidate = artifact.candidate
        run = artifact.run
        if artifact.outline_sections:
            section_outline = [
                {
                    "section_id": item.section_id,
                    "title": item.title,
                    "depth": item.depth,
                    "parent_id": item.parent_id,
                    "sibling_order": item.sibling_order,
                    "document_order": item.document_order,
                    "outline_path": list(item.outline_path),
                }
                for item in artifact.outline_sections
            ]
        else:
            section_outline = [
                {
                    "section_id": str(section.get("section_id") or ""),
                    "title": str(section.get("heading") or ""),
                    "depth": 1,
                    "parent_id": artifact.chapter_id,
                    "sibling_order": order,
                    "document_order": order,
                    "outline_path": [artifact.chapter_id, str(section.get("section_id") or "")],
                }
                for order, section in enumerate(candidate.get("sections") or [], start=1)
                if isinstance(section, dict)
            ]
        return {
            "chapter_id": artifact.chapter_id,
            "chapter_title": candidate.get("chapter_title"),
            "order": artifact.order,
            "sections": deepcopy(candidate.get("sections") or []),
            "section_outline": section_outline,
            "claims": deepcopy(candidate.get("claims") or []),
            "placeholders_used": deepcopy(candidate.get("placeholders_used") or []),
            "score_items_covered": deepcopy(candidate.get("score_items_covered") or []),
            "requirements_covered": deepcopy(candidate.get("requirements_covered") or []),
            "warnings": deepcopy(candidate.get("warnings") or []),
            **(
                {"generated_supporting_tables": deepcopy(candidate.get("generated_supporting_tables") or [])}
                if "generated_supporting_tables" in candidate else {}
            ),
            "source": {
                "candidate_json_path": str(artifact.candidate_path),
                "candidate_json_sha256": artifact.candidate_sha256,
                "run_json_path": str(artifact.run_path),
                "run_json_sha256": artifact.run_sha256,
                "events_jsonl_path": str(artifact.events_path),
                "events_jsonl_sha256": artifact.events_sha256,
                "event_count": artifact.event_count,
                "output_key": run.get("run_summary", {}).get("output_key") or run.get("output_key"),
                "revision_round": run.get("revision_round"),
                "chapter_status": run.get("chapter_status"),
                "validation_status": run.get("validation_status"),
                "issue_counts": deepcopy(run.get("issue_counts") or {}),
                "candidate_generated": bool(run.get("candidate_generated")),
                "candidate_reviewable": bool(run.get("candidate_reviewable")),
                "submission_ready": bool(run.get("submission_ready")),
                "selected_candidate": run.get("selected_candidate"),
                "candidate_selection_reason": run.get("candidate_selection_reason"),
            },
        }

    @staticmethod
    def _coverage_summary(artifacts: list[ChapterArtifact]) -> dict[str, Any]:
        requirements: list[Any] = []
        score_items: list[Any] = []
        placeholders: list[Any] = []
        source_ids: list[Any] = []
        evidence_ids: list[Any] = []
        for artifact in artifacts:
            candidate = artifact.candidate
            requirements.extend(candidate.get("requirements_covered") or [])
            score_items.extend(candidate.get("score_items_covered") or [])
            placeholders.extend(candidate.get("placeholders_used") or [])
            for claim in candidate.get("claims") or []:
                if isinstance(claim, dict):
                    source_ids.extend(claim.get("source_ids") or [])
                    evidence_ids.extend(claim.get("evidence_ids") or [])
        unique_requirements = _unique_strings(requirements)
        unique_scores = _unique_strings(score_items)
        unique_placeholders = _unique_strings(placeholders)
        unique_sources = _unique_strings(source_ids)
        unique_evidence = _unique_strings(evidence_ids)
        return {
            "unique_requirement_ids": unique_requirements,
            "unique_requirement_count": len(unique_requirements),
            "unique_score_item_ids": unique_scores,
            "unique_score_item_count": len(unique_scores),
            "unique_placeholder_ids": unique_placeholders,
            "unique_placeholder_count": len(unique_placeholders),
            "unique_source_ids": unique_sources,
            "unique_source_count": len(unique_sources),
            "unique_evidence_ids": unique_evidence,
            "unique_evidence_count": len(unique_evidence),
        }

    @staticmethod
    def _render_preview(fulltext: dict[str, Any]) -> str:
        lines = [PREVIEW_NOTICE, "", f"组合状态：{fulltext.get('composition_status')}", ""]
        for chapter in fulltext.get("chapters") or []:
            source = chapter.get("source") or {}
            counts = source.get("issue_counts") or {}
            lines.extend([
                f"# {chapter.get('chapter_title')}", "",
                f"> 章节状态：{source.get('chapter_status') or 'unknown'}",
                f"> 校验状态：{source.get('validation_status') or 'unknown'}",
                f"> 问题数量：blocking={int(counts.get('blocking') or 0)}，warning={int(counts.get('warning') or 0)}",
                f"> 候选版本：{source.get('selected_candidate') or 'unknown'}",
                f"> Submission Ready：{'是' if source.get('submission_ready') else '否'}", "",
            ])
            depth_by_id = {
                str(item.get("section_id") or ""): int(item.get("depth") or 1)
                for item in chapter.get("section_outline") or []
                if isinstance(item, dict)
            }
            for section in chapter.get("sections") or []:
                section_id = str(section.get("section_id") or "")
                depth = max(1, depth_by_id.get(section_id, 1))
                lines.extend([
                    f"{'#' * (depth + 1)} {section.get('heading')}", "",
                    str(section.get("content") or ""), "",
                ])
        return "\n".join(lines).rstrip() + "\n"

    def _commit_artifact_set(
        self,
        *,
        fulltext: dict[str, Any],
        markdown: str,
        manifest: dict[str, Any],
        manifest_path: Path,
        production_path: Path,
        production_sha: str,
        output_key: str,
    ) -> dict[str, Any]:
        fulltext_dir = (self.output_dir / output_key / "fulltext").resolve()
        json_path = fulltext_dir / "technical_fulltext.json"
        preview_path = fulltext_dir / "technical_fulltext.preview.md"
        commit_path = fulltext_dir / "technical_fulltext.commit.json"
        token = f"{os.getpid()}.{stable_json_sha256(fulltext)[:12]}"
        json_temp = fulltext_dir / f".technical_fulltext.json.{token}.pending"
        preview_temp = fulltext_dir / f".technical_fulltext.preview.md.{token}.pending"
        try:
            commit_path.parent.mkdir(parents=True, exist_ok=True)
            commit_path.unlink(missing_ok=True)
            json_sha = atomic_write_json(
                json_temp, fulltext,
                required_fields=("schema_version", "document_id", "chapters", "composition_status", "release_ready"),
            )
            preview_sha = atomic_write_markdown(
                preview_temp, markdown, required_text=PREVIEW_NOTICE
            )
            os.replace(json_temp, json_path)
            os.replace(preview_temp, preview_path)
            persisted_json = load_json_object(json_path, label="technical fulltext")
            persisted_preview = preview_path.read_text(encoding="utf-8")
            if persisted_json != fulltext or persisted_preview != markdown:
                raise OSError("committed fulltext artifacts changed during final replacement")
            if sha256_file(json_path) != json_sha or sha256_file(preview_path) != preview_sha:
                raise OSError("committed fulltext artifact hash changed after final replacement")
            commit = {
                "schema_version": COMMIT_SCHEMA_VERSION,
                "status": "committed",
                "output_key": output_key,
                "document_id": str(manifest.get("document_id") or ""),
                "manifest_path": str(manifest_path),
                "manifest_sha256": sha256_file(manifest_path),
                "production_index_path": str(production_path),
                "production_index_sha256": production_sha,
                "technical_fulltext_json_path": str(json_path),
                "technical_fulltext_json_sha256": json_sha,
                "technical_fulltext_preview_path": str(preview_path),
                "technical_fulltext_preview_sha256": preview_sha,
                "committed_at": utc_now(),
            }
            commit_sha = atomic_write_json(
                commit_path, commit,
                required_fields=(
                    "schema_version", "status", "output_key", "document_id",
                    "technical_fulltext_json_path", "technical_fulltext_json_sha256",
                    "technical_fulltext_preview_path", "technical_fulltext_preview_sha256",
                ),
            )
            return {
                "technical_fulltext_json": str(json_path),
                "technical_fulltext_json_sha256": json_sha,
                "technical_fulltext_preview": str(preview_path),
                "technical_fulltext_preview_sha256": preview_sha,
                "technical_fulltext_commit": str(commit_path),
                "technical_fulltext_commit_sha256": commit_sha,
            }
        except Exception as exc:
            commit_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            preview_path.unlink(missing_ok=True)
            artifact_issue = _issue(
                code="fulltext_artifact_commit_failed", category="artifact",
                message=f"Fulltext artifact set commit failed: {type(exc).__name__}: {str(exc)[:300]}",
                source_paths=[json_path, preview_path, commit_path],
            )
            raise TechnicalFulltextCompositionError(
                "technical fulltext artifact set commit failed", issues=[artifact_issue]
            ) from exc
        finally:
            json_temp.unlink(missing_ok=True)
            preview_temp.unlink(missing_ok=True)


def inspect_technical_fulltext(path: str | Path) -> dict[str, Any]:
    requested_json = Path(path).expanduser().resolve()
    commit_path = requested_json.parent / "technical_fulltext.commit.json"
    try:
        commit = load_json_object(commit_path, label="technical fulltext commit")
    except (FileNotFoundError, ValueError) as exc:
        raise TechnicalFulltextCompositionError(f"committed fulltext is unavailable: {exc}") from exc
    if commit.get("schema_version") != COMMIT_SCHEMA_VERSION or commit.get("status") != "committed":
        raise TechnicalFulltextCompositionError("invalid technical fulltext commit schema or status")
    json_path = _resolve_reference(commit.get("technical_fulltext_json_path"), commit_path)
    preview_path = _resolve_reference(commit.get("technical_fulltext_preview_path"), commit_path)
    manifest_path = _resolve_reference(commit.get("manifest_path"), commit_path)
    production_path = _resolve_reference(commit.get("production_index_path"), commit_path)
    if json_path != requested_json:
        raise TechnicalFulltextCompositionError("commit does not reference the requested fulltext JSON")
    references = (
        (json_path, "technical_fulltext_json_sha256"),
        (preview_path, "technical_fulltext_preview_sha256"),
        (manifest_path, "manifest_sha256"),
        (production_path, "production_index_sha256"),
    )
    for target, hash_field in references:
        if not target.is_file():
            raise TechnicalFulltextCompositionError(f"committed artifact is missing: {target}")
        actual = sha256_file(target)
        if actual != str(commit.get(hash_field) or ""):
            raise TechnicalFulltextCompositionError(
                f"committed artifact hash mismatch: {hash_field}"
            )
    payload = load_json_object(json_path, label="technical fulltext")
    manifest_payload = load_json_object(manifest_path, label="fulltext build manifest")
    production_payload = load_json_object(production_path, label="chapter production index")
    if (
        str(payload.get("output_key") or "") != str(commit.get("output_key") or "")
        or str(payload.get("document_id") or "") != str(commit.get("document_id") or "")
    ):
        raise TechnicalFulltextCompositionError("commit identity does not match fulltext JSON")
    if (
        str(manifest_payload.get("output_key") or "") != str(commit.get("output_key") or "")
        or str(manifest_payload.get("document_id") or "") != str(commit.get("document_id") or "")
        or str(production_payload.get("output_key") or "") != str(commit.get("output_key") or "")
    ):
        raise TechnicalFulltextCompositionError("commit identity does not match Manifest or production index")
    if (
        payload.get("schema_version") != FULLTEXT_SCHEMA_VERSION
        or payload.get("composition_status") not in {"complete", "completed_with_blockers"}
        or payload.get("preview_only") is not True
        or payload.get("release_ready") is not False
    ):
        raise TechnicalFulltextCompositionError("committed technical fulltext status is invalid")
    chapters = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
    coverage = payload.get("coverage_summary") if isinstance(payload.get("coverage_summary"), dict) else {}
    return {
        "document_id": payload.get("document_id"),
        "output_key": payload.get("output_key"),
        "standard_outline_sha256": (payload.get("standard_outline") or {}).get("sha256"),
        "chapter_count": len(chapters),
        "chapter_order": list(payload.get("chapter_order") or []),
        "composition_status": payload.get("composition_status"),
        "chapters": [
            {
                "chapter_id": item.get("chapter_id"),
                "selected_candidate": (item.get("source") or {}).get("selected_candidate"),
                "chapter_status": (item.get("source") or {}).get("chapter_status"),
                "validation_status": (item.get("source") or {}).get("validation_status"),
                "issue_counts": (item.get("source") or {}).get("issue_counts") or {},
                "submission_ready": bool((item.get("source") or {}).get("submission_ready")),
            }
            for item in chapters if isinstance(item, dict)
        ],
        "composition_issues": list(payload.get("composition_issues") or []),
        "coverage_counts": {
            "requirements": int(coverage.get("unique_requirement_count") or 0),
            "score_items": int(coverage.get("unique_score_item_count") or 0),
            "placeholders": int(coverage.get("unique_placeholder_count") or 0),
        },
        "preview_only": bool(payload.get("preview_only")),
        "release_ready": bool(payload.get("release_ready")),
    }

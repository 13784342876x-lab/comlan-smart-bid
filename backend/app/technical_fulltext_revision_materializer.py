from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.technical_fulltext_composer import (
    COMMIT_SCHEMA_VERSION,
    FULLTEXT_SCHEMA_VERSION,
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PREVIEW_NOTICE,
    SAFE_OUTPUT_KEY,
    TechnicalFulltextComposer,
    atomic_write_json,
    atomic_write_markdown,
    inspect_technical_fulltext,
    load_json_object,
    sha256_file,
    utc_now,
)
from app.technical_fulltext_runner import FULLTEXT_RUN_SCHEMA_VERSION


class TechnicalFulltextRevisionMaterializationError(RuntimeError):
    pass


def materialize_single_section_revision(
    *,
    source_fulltext_path: str | Path,
    target_section_id: str,
    replacement_section: dict[str, Any],
    output_dir: str | Path,
    output_key: str,
    stage12_handoff_path: str | Path,
) -> dict[str, Any]:
    source_path = Path(source_fulltext_path).expanduser().resolve()
    source_identity = inspect_technical_fulltext(source_path)
    source_fulltext = load_json_object(source_path, label="source technical fulltext")
    source_commit_path = source_path.parent / "technical_fulltext.commit.json"
    source_run_path = source_path.parent / "technical_fulltext.run.json"
    source_run = load_json_object(source_run_path, label="source technical fulltext run")
    if source_run.get("schema_version") != FULLTEXT_RUN_SCHEMA_VERSION:
        raise TechnicalFulltextRevisionMaterializationError(
            "source technical fulltext run schema is unsupported"
        )
    if source_run.get("output_key") != source_identity["output_key"]:
        raise TechnicalFulltextRevisionMaterializationError(
            "source technical fulltext run output_key mismatch"
        )

    new_output_key = str(output_key or "").strip()
    if not SAFE_OUTPUT_KEY.fullmatch(new_output_key) or ".." in new_output_key:
        raise TechnicalFulltextRevisionMaterializationError("unsafe output_key")
    if new_output_key == source_identity["output_key"]:
        raise TechnicalFulltextRevisionMaterializationError(
            "revision output_key must differ from source output_key"
        )
    output_root = Path(output_dir).expanduser().resolve()
    revision_root = output_root / new_output_key
    if revision_root.exists():
        raise TechnicalFulltextRevisionMaterializationError(
            f"revision output already exists: {revision_root}"
        )

    handoff_path = Path(stage12_handoff_path).expanduser().resolve()
    handoff = load_json_object(handoff_path, label="Stage12 handoff")
    handoff_identity = handoff.get("artifact_identity")
    if (
        handoff.get("schema_version") != "technical_fulltext_stage13_handoff_v1"
        or not isinstance(handoff_identity, dict)
        or handoff_identity.get("status") != "ready"
        or not str(handoff_identity.get("handoff_id") or "").strip()
    ):
        raise TechnicalFulltextRevisionMaterializationError(
            "Stage12 handoff is not a ready Stage13 handoff"
        )
    if handoff_identity.get("output_key") != source_identity["output_key"]:
        raise TechnicalFulltextRevisionMaterializationError(
            "Stage12 handoff output_key does not match source fulltext"
        )
    if handoff_identity.get("document_id") != source_identity["document_id"]:
        raise TechnicalFulltextRevisionMaterializationError(
            "Stage12 handoff document_id does not match source fulltext"
        )

    target_id = str(target_section_id or "").strip()
    if replacement_section.get("section_id") != target_id:
        raise TechnicalFulltextRevisionMaterializationError(
            "replacement section_id does not match target_section_id"
        )
    matches: list[tuple[int, int]] = []
    for chapter_index, chapter in enumerate(source_fulltext["chapters"]):
        for section_index, section in enumerate(chapter["sections"]):
            if section.get("section_id") == target_id:
                matches.append((chapter_index, section_index))
    if len(matches) != 1:
        raise TechnicalFulltextRevisionMaterializationError(
            f"target_section_id must match exactly one source section; found {len(matches)}"
        )

    source_fulltext_sha = sha256_file(source_path)
    provenance = {
        "parent_stage10": {
            "document_id": source_identity["document_id"],
            "output_key": source_identity["output_key"],
            "fulltext_path": str(source_path),
            "fulltext_sha256": source_fulltext_sha,
            "commit_path": str(source_commit_path.resolve()),
            "commit_sha256": sha256_file(source_commit_path),
            "run_path": str(source_run_path.resolve()),
            "run_sha256": sha256_file(source_run_path),
        },
        "stage12_handoff": {
            "handoff_id": handoff_identity["handoff_id"],
            "path": str(handoff_path),
            "sha256": sha256_file(handoff_path),
        },
        "target_section_id": target_id,
    }

    fulltext_dir = revision_root / "fulltext"
    index_path = fulltext_dir / "chapter_production_index.json"
    manifest_path = fulltext_dir / "technical_fulltext_build_manifest.json"
    fulltext_path = fulltext_dir / "technical_fulltext.json"
    preview_path = fulltext_dir / "technical_fulltext.preview.md"
    run_path = fulltext_dir / "technical_fulltext.run.json"
    commit_path = fulltext_dir / "technical_fulltext.commit.json"
    document_id = f"technical-fulltext:{new_output_key}"
    generated_at = utc_now()

    chapters = deepcopy(source_fulltext["chapters"])
    target_chapter_index, target_section_index = matches[0]
    chapters[target_chapter_index]["sections"][target_section_index] = deepcopy(
        replacement_section
    )
    for chapter_index, chapter in enumerate(chapters):
        parent_chapter_source = chapter.get("source")
        if isinstance(parent_chapter_source, dict):
            chapter["source"] = {
                "output_key": new_output_key,
                "chapter_status": parent_chapter_source.get("chapter_status"),
                "validation_status": parent_chapter_source.get("validation_status"),
                "issue_counts": deepcopy(parent_chapter_source.get("issue_counts") or {}),
                "submission_ready": bool(parent_chapter_source.get("submission_ready")),
                "selected_candidate": "materialized_revision",
                "candidate_selection_reason": "single_section_revision_materialization",
                "materialized_from": {
                    "output_key": source_identity["output_key"],
                    "fulltext_sha256": source_fulltext_sha,
                    "mode": (
                        "section_replacement"
                        if chapter_index == target_chapter_index
                        else "canonical_content_reuse"
                    ),
                },
            }

    production = source_fulltext["production"]
    chapter_records = [
        {
            "chapter_id": chapter["chapter_id"],
            "chapter_order": index + 1,
            "status": "completed",
            "materialization": (
                "section_replacement"
                if index == target_chapter_index
                else "canonical_content_reuse"
            ),
        }
        for index, chapter in enumerate(chapters)
    ]
    production_index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "output_key": new_output_key,
        "chapter_count_expected": production["chapter_count_expected"],
        "chapter_count_completed": production["chapter_count_completed"],
        "chapter_count_submission_ready": production["chapter_count_submission_ready"],
        "chapter_count_blocked": production["chapter_count_blocked"],
        "chapter_count_failed": production["chapter_count_failed"],
        "chapters": chapter_records,
        "production_status": production["production_status"],
    }
    production_sha = atomic_write_json(
        index_path,
        production_index,
        required_fields=("schema_version", "output_key", "chapters", "production_status"),
    )

    standard_outline = source_fulltext["standard_outline"]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "document_id": document_id,
        "output_key": new_output_key,
        "standard_outline_path": standard_outline.get("resolved_path")
        or standard_outline.get("original_path"),
        "standard_outline_sha256": standard_outline["sha256"],
        "chapters": deepcopy(chapter_records),
        "generated_from": {
            "chapter_production_index_path": str(index_path.resolve()),
            "chapter_production_index_sha256": production_sha,
        },
        "generated_at": generated_at,
    }
    manifest_sha = atomic_write_json(
        manifest_path,
        manifest,
        required_fields=("schema_version", "document_id", "output_key", "chapters"),
    )

    revised_fulltext = deepcopy(source_fulltext)
    revised_fulltext.update(
        {
            "schema_version": FULLTEXT_SCHEMA_VERSION,
            "document_id": document_id,
            "output_key": new_output_key,
            "generated_at": generated_at,
            "chapters": chapters,
            "production": {
                **deepcopy(production),
                "chapter_production_index_path": str(index_path.resolve()),
                "chapter_production_index_sha256": production_sha,
            },
            "source_manifest": {
                "path": str(manifest_path.resolve()),
                "sha256": manifest_sha,
            },
        }
    )
    fulltext_sha = atomic_write_json(
        fulltext_path,
        revised_fulltext,
        required_fields=(
            "schema_version",
            "document_id",
            "output_key",
            "chapters",
            "composition_status",
            "release_ready",
        ),
    )
    preview_sha = atomic_write_markdown(
        preview_path,
        TechnicalFulltextComposer._render_preview(revised_fulltext),
        required_text=PREVIEW_NOTICE,
    )

    run_report = {
        "schema_version": FULLTEXT_RUN_SCHEMA_VERSION,
        "status": "completed",
        "production_status": production["production_status"],
        "composition_status": revised_fulltext["composition_status"],
        "output_key": new_output_key,
        "started_at": generated_at,
        "completed_at": generated_at,
        "duration_ms": 0,
        "input_paths": deepcopy(source_run["input_paths"]),
        "input_hashes": deepcopy(source_run["input_hashes"]),
        "chapter_count_expected": production["chapter_count_expected"],
        "chapter_count_completed": production["chapter_count_completed"],
        "chapter_count_submission_ready": production["chapter_count_submission_ready"],
        "chapter_count_blocked": production["chapter_count_blocked"],
        "chapter_count_failed": production["chapter_count_failed"],
        "chapters": deepcopy(chapter_records),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "outputs": {
            "chapter_production_index": str(index_path.resolve()),
            "technical_fulltext_json": str(fulltext_path.resolve()),
            "technical_fulltext_preview": str(preview_path.resolve()),
            "technical_fulltext_commit": str(commit_path.resolve()),
        },
    }
    run_sha = atomic_write_json(
        run_path,
        run_report,
        required_fields=(
            "schema_version",
            "status",
            "production_status",
            "composition_status",
            "output_key",
            "input_paths",
            "input_hashes",
        ),
    )

    commit = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "output_key": new_output_key,
        "document_id": document_id,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "production_index_path": str(index_path.resolve()),
        "production_index_sha256": production_sha,
        "technical_fulltext_json_path": str(fulltext_path.resolve()),
        "technical_fulltext_json_sha256": fulltext_sha,
        "technical_fulltext_preview_path": str(preview_path.resolve()),
        "technical_fulltext_preview_sha256": preview_sha,
        "technical_fulltext_run_path": str(run_path.resolve()),
        "technical_fulltext_run_sha256": run_sha,
        "revision_provenance": deepcopy(provenance),
        "committed_at": utc_now(),
    }
    commit_sha = atomic_write_json(
        commit_path,
        commit,
        required_fields=(
            "schema_version",
            "status",
            "output_key",
            "document_id",
            "technical_fulltext_json_path",
            "technical_fulltext_json_sha256",
            "technical_fulltext_preview_path",
            "technical_fulltext_preview_sha256",
            "revision_provenance",
        ),
    )
    inspection = inspect_technical_fulltext(fulltext_path)
    return {
        "status": "completed",
        "output_key": new_output_key,
        "document_id": document_id,
        "target_section_id": target_id,
        "fulltext": revised_fulltext,
        "inspection": inspection,
        "revision_provenance": provenance,
        "outputs": {
            "chapter_production_index": str(index_path.resolve()),
            "technical_fulltext_build_manifest": str(manifest_path.resolve()),
            "technical_fulltext_json": str(fulltext_path.resolve()),
            "technical_fulltext_json_sha256": fulltext_sha,
            "technical_fulltext_preview": str(preview_path.resolve()),
            "technical_fulltext_preview_sha256": preview_sha,
            "technical_fulltext_run": str(run_path.resolve()),
            "technical_fulltext_run_sha256": run_sha,
            "technical_fulltext_commit": str(commit_path.resolve()),
            "technical_fulltext_commit_sha256": commit_sha,
        },
    }

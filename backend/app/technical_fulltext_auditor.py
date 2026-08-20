from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from app.technical_fulltext_composer import (
    COMMIT_SCHEMA_VERSION,
    FULLTEXT_SCHEMA_VERSION,
    atomic_write_json,
    atomic_write_markdown,
    load_json_object,
    sha256_file,
    stable_json_sha256,
    utc_now,
    inspect_technical_fulltext,
)
from app.technical_fulltext_deterministic_audit import run_deterministic_audit


AUDIT_SCHEMA_VERSION = "technical_fulltext_audit_v1"
FULLTEXT_RUN_SCHEMA_VERSION = "technical_fulltext_run_v1"
AUDIT_MARKDOWN_NOTICE = "Stage ⑪-B 确定性全文审计已完成。"
PLACEHOLDER_PATTERN = re.compile(
    r"\{\{\s*(PH-[A-Za-z0-9][A-Za-z0-9._:-]*)\s*\}\}"
)


class TechnicalFulltextAuditError(RuntimeError):
    """Raised when the committed audit input snapshot cannot be trusted or loaded."""


@dataclass(frozen=True)
class NormalizedAuditDocument:
    document: dict[str, Any]
    chapters: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    paragraphs: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    placeholders: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    score_items: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": deepcopy(self.document),
            "chapters": deepcopy(self.chapters),
            "sections": deepcopy(self.sections),
            "paragraphs": deepcopy(self.paragraphs),
            "claims": deepcopy(self.claims),
            "placeholders": deepcopy(self.placeholders),
            "requirements": deepcopy(self.requirements),
            "score_items": deepcopy(self.score_items),
        }


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalize_text(value: str) -> str:
    normalized_newlines = value.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized_newlines.strip())


def _paragraphs(value: Any) -> list[str]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"\n[ \t]*\n+", text) if item.strip()]


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _outputs_root(fulltext_path: Path) -> Path | None:
    for parent in fulltext_path.parents:
        if parent.name.lower() == "outputs":
            return parent.resolve()
    return None


def _resolve_reference(
    value: Any,
    *,
    source_path: Path,
    outputs_root: Path | None,
) -> tuple[Path, str]:
    recorded = str(value or "").strip()
    if not recorded:
        return Path(""), recorded
    raw = Path(recorded).expanduser()
    candidate = raw if raw.is_absolute() else source_path.parent / raw
    if candidate.is_file():
        return candidate.resolve(), recorded

    # Stage ⑩ artifacts may be moved as one output tree.  Resolve a stale absolute
    # workspace prefix through the stable `outputs/<key>/...` suffix.
    if outputs_root is not None:
        parts = list(raw.parts)
        output_positions = [
            index for index, part in enumerate(parts) if str(part).lower() == "outputs"
        ]
        if output_positions:
            relocated = outputs_root.joinpath(*parts[output_positions[-1] + 1 :])
            if relocated.is_file():
                return relocated.resolve(), recorded
    return candidate.resolve(), recorded


def _append_occurrence(
    index: dict[str, dict[str, Any]],
    item_id: str,
    location: dict[str, Any],
) -> None:
    entry = index.setdefault(item_id, {"id": item_id, "locations": []})
    entry["locations"].append(location)


def _finalize_occurrences(
    index: dict[str, dict[str, Any]], *, id_field: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in index.values():
        locations = item["locations"]
        result.append({
            id_field: item["id"],
            "locations": locations,
            "chapter_ids": _unique_strings(location.get("chapter_id") for location in locations),
            "section_ids": _unique_strings(location.get("section_id") for location in locations),
            "occurrence_count": len(locations),
        })
    return result


def normalize_audit_document(fulltext: dict[str, Any]) -> NormalizedAuditDocument:
    """Build deterministic Stage ⑪ indexes without judging document content."""
    chapter_order = [str(value or "").strip() for value in fulltext.get("chapter_order") or []]
    chapters = fulltext.get("chapters")
    if not isinstance(chapters, list):
        raise TechnicalFulltextAuditError("technical fulltext chapters must be an array")

    chapter_index: list[dict[str, Any]] = []
    section_index: list[dict[str, Any]] = []
    paragraph_index: list[dict[str, Any]] = []
    claim_index: list[dict[str, Any]] = []
    placeholder_occurrences: dict[str, dict[str, Any]] = {}
    requirement_occurrences: dict[str, dict[str, Any]] = {}
    score_occurrences: dict[str, dict[str, Any]] = {}
    seen_chapters: set[str] = set()
    seen_sections: set[str] = set()
    section_document_order = 0
    paragraph_document_order = 0
    claim_document_order = 0

    for chapter_position, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            raise TechnicalFulltextAuditError(
                f"technical fulltext chapter at position {chapter_position} must be an object"
            )
        chapter_id = str(chapter.get("chapter_id") or "").strip()
        if not chapter_id or chapter_id in seen_chapters:
            raise TechnicalFulltextAuditError(
                f"technical fulltext chapter identity is missing or duplicated at position {chapter_position}"
            )
        seen_chapters.add(chapter_id)
        source = chapter.get("source") if isinstance(chapter.get("source"), dict) else {}
        chapter_index.append({
            "chapter_id": chapter_id,
            "chapter_title": str(chapter.get("chapter_title") or ""),
            "chapter_order": chapter_position,
            "chapter_status": source.get("chapter_status"),
            "validation_status": source.get("validation_status"),
            "submission_ready": bool(source.get("submission_ready")),
        })

        chapter_placeholder_ids = _unique_strings(chapter.get("placeholders_used") or [])
        chapter_requirement_ids = _unique_strings(chapter.get("requirements_covered") or [])
        chapter_score_ids = _unique_strings(chapter.get("score_items_covered") or [])
        chapter_sections = chapter.get("sections")
        if not isinstance(chapter_sections, list):
            raise TechnicalFulltextAuditError(f"chapter {chapter_id} sections must be an array")

        chapter_placeholder_locations: set[str] = set()
        chapter_requirement_locations: set[str] = set()
        chapter_score_locations: set[str] = set()
        for section_position, section in enumerate(chapter_sections, start=1):
            if not isinstance(section, dict):
                raise TechnicalFulltextAuditError(
                    f"section {section_position} in chapter {chapter_id} must be an object"
                )
            section_id = str(section.get("section_id") or "").strip()
            if not section_id or section_id in seen_sections:
                raise TechnicalFulltextAuditError(
                    f"section identity is missing or duplicated in chapter {chapter_id}"
                )
            seen_sections.add(section_id)
            section_document_order += 1
            content = str(section.get("content") or "")
            declared_placeholders = _unique_strings(section.get("placeholder_ids") or [])
            requirement_ids = _unique_strings(section.get("covered_requirement_ids") or [])
            score_ids = _unique_strings(section.get("covered_score_item_ids") or [])
            section_index.append({
                "chapter_id": chapter_id,
                "section_id": section_id,
                "heading": str(section.get("heading") or ""),
                "block_type": str(section.get("block_type") or ""),
                "document_order": section_document_order,
                "content": content,
                "covered_requirement_ids": requirement_ids,
                "covered_score_item_ids": score_ids,
                "placeholder_ids": declared_placeholders,
            })

            placeholders_found_in_content: set[str] = set()
            for paragraph_position, text in enumerate(_paragraphs(content), start=1):
                paragraph_document_order += 1
                normalized_text = _normalize_text(text)
                paragraph_id = "PAR-" + stable_json_sha256({
                    "chapter_id": chapter_id,
                    "section_id": section_id,
                    "paragraph_index": paragraph_position,
                })[:16].upper()
                record = {
                    "paragraph_id": paragraph_id,
                    "chapter_id": chapter_id,
                    "section_id": section_id,
                    "paragraph_index": paragraph_position,
                    "document_order": paragraph_document_order,
                    "text": text,
                    "normalized_text": normalized_text,
                    "text_sha256": _text_sha256(normalized_text),
                }
                paragraph_index.append(record)

                per_id_count: dict[str, int] = {}
                for match in PLACEHOLDER_PATTERN.finditer(text):
                    placeholder_id = match.group(1)
                    placeholders_found_in_content.add(placeholder_id)
                    chapter_placeholder_locations.add(placeholder_id)
                    per_id_count[placeholder_id] = per_id_count.get(placeholder_id, 0) + 1
                    source_types = ["content"]
                    if placeholder_id in declared_placeholders:
                        source_types.append("section_metadata")
                    if placeholder_id in chapter_placeholder_ids:
                        source_types.append("chapter_metadata")
                    _append_occurrence(placeholder_occurrences, placeholder_id, {
                        "chapter_id": chapter_id,
                        "section_id": section_id,
                        "paragraph_id": paragraph_id,
                        "occurrence_in_paragraph": per_id_count[placeholder_id],
                        "source_types": source_types,
                    })

            for placeholder_id in declared_placeholders:
                if placeholder_id not in placeholders_found_in_content:
                    chapter_placeholder_locations.add(placeholder_id)
                    _append_occurrence(placeholder_occurrences, placeholder_id, {
                        "chapter_id": chapter_id,
                        "section_id": section_id,
                        "paragraph_id": None,
                        "occurrence_in_paragraph": None,
                        "source_types": ["section_metadata"],
                    })

            base_location = {
                "chapter_id": chapter_id,
                "section_id": section_id,
                "paragraph_id": None,
                "source_types": ["section_metadata"],
            }
            for requirement_id in requirement_ids:
                chapter_requirement_locations.add(requirement_id)
                _append_occurrence(
                    requirement_occurrences, requirement_id, deepcopy(base_location)
                )
            for score_id in score_ids:
                chapter_score_locations.add(score_id)
                _append_occurrence(score_occurrences, score_id, deepcopy(base_location))

        for placeholder_id in chapter_placeholder_ids:
            if placeholder_id not in chapter_placeholder_locations:
                _append_occurrence(placeholder_occurrences, placeholder_id, {
                    "chapter_id": chapter_id,
                    "section_id": None,
                    "paragraph_id": None,
                    "occurrence_in_paragraph": None,
                    "source_types": ["chapter_metadata"],
                })
        for requirement_id in chapter_requirement_ids:
            if requirement_id not in chapter_requirement_locations:
                _append_occurrence(requirement_occurrences, requirement_id, {
                    "chapter_id": chapter_id,
                    "section_id": None,
                    "paragraph_id": None,
                    "source_types": ["chapter_metadata"],
                })
        for score_id in chapter_score_ids:
            if score_id not in chapter_score_locations:
                _append_occurrence(score_occurrences, score_id, {
                    "chapter_id": chapter_id,
                    "section_id": None,
                    "paragraph_id": None,
                    "source_types": ["chapter_metadata"],
                })

        claims = chapter.get("claims") or []
        if not isinstance(claims, list):
            raise TechnicalFulltextAuditError(f"chapter {chapter_id} claims must be an array")
        for claim_position, claim in enumerate(claims, start=1):
            if not isinstance(claim, dict):
                raise TechnicalFulltextAuditError(
                    f"claim {claim_position} in chapter {chapter_id} must be an object"
                )
            claim_document_order += 1
            claim_index.append({
                "claim_id": "CLM-" + stable_json_sha256({
                    "chapter_id": chapter_id,
                    "claim_index": claim_position,
                })[:16].upper(),
                "chapter_id": chapter_id,
                "claim_index": claim_position,
                "document_order": claim_document_order,
                "text": str(claim.get("text") or ""),
                "claim_type": str(claim.get("claim_type") or ""),
                "source_ids": _unique_strings(claim.get("source_ids") or []),
                "evidence_ids": _unique_strings(claim.get("evidence_ids") or []),
                "human_review_required": bool(claim.get("human_review_required")),
            })

    if chapter_order != [item["chapter_id"] for item in chapter_index]:
        raise TechnicalFulltextAuditError(
            "technical fulltext chapter_order does not match chapters"
        )

    return NormalizedAuditDocument(
        document={
            "document_id": str(fulltext.get("document_id") or ""),
            "output_key": str(fulltext.get("output_key") or ""),
            "schema_version": str(fulltext.get("schema_version") or ""),
            "chapter_order": chapter_order,
        },
        chapters=chapter_index,
        sections=section_index,
        paragraphs=paragraph_index,
        claims=claim_index,
        placeholders=_finalize_occurrences(
            placeholder_occurrences, id_field="placeholder_id"
        ),
        requirements=_finalize_occurrences(
            requirement_occurrences, id_field="requirement_id"
        ),
        score_items=_finalize_occurrences(score_occurrences, id_field="score_item_id"),
    )


class TechnicalFulltextAuditor:
    """Load and normalize one committed Stage ⑩ fulltext without content auditing."""

    def __init__(self, *, schema_path: str | Path | None = None):
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path is not None
            else Path(__file__).resolve().parent.parent
            / "schemas"
            / "technical_fulltext_audit_v1.json"
        )

    def audit(self, *, fulltext_json_path: str | Path) -> dict[str, Any]:
        requested_path = Path(fulltext_json_path).expanduser().resolve()
        source = self._load_audit_source(requested_path)
        fulltext = source["fulltext"]
        commit = source["commit"]
        commit_path = source["commit_path"]
        audited_file_sha = source["audited_file_sha256"]
        outputs_root = _outputs_root(source["committed_path"])
        run_path = source["committed_path"].parent / "technical_fulltext.run.json"
        try:
            run = load_json_object(run_path, label="technical fulltext run")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextAuditError(
                f"technical fulltext run is unavailable: {exc}"
            ) from exc
        if run.get("schema_version") != FULLTEXT_RUN_SCHEMA_VERSION:
            raise TechnicalFulltextAuditError("technical fulltext run schema is unsupported")
        if str(run.get("output_key") or "") != str(fulltext.get("output_key") or ""):
            raise TechnicalFulltextAuditError("technical fulltext run output_key mismatch")

        input_context, context_documents = self._load_input_context(
            fulltext=fulltext,
            run=run,
            run_path=run_path,
            fulltext_path=source["committed_path"],
            outputs_root=outputs_root,
        )
        normalized = normalize_audit_document(fulltext)
        normalized_payload = normalized.to_dict()
        draft_units_context = input_context.get("draft_units")
        draft_units_trusted = bool(
            isinstance(draft_units_context, dict)
            and draft_units_context.get("sha256_matches_run") is True
        )
        deterministic = run_deterministic_audit(
            normalized_payload,
            draft_units=context_documents.get("draft_units"),
            draft_units_trusted=draft_units_trusted,
        )
        issue_counts = {"fatal": 0, "blocking": 0, "warning": 0, "info": 0}
        for issue in deterministic.issues:
            severity = str(issue.get("severity") or "")
            if severity in issue_counts:
                issue_counts[severity] += 1
        metrics = self._metrics(normalized)
        audit_dir = source["committed_path"].parent / "audit"
        audit_json_path = audit_dir / "technical_fulltext.audit.json"
        preserved_observation_cache: list[dict[str, Any]] = []
        if audit_json_path.is_file():
            try:
                previous = load_json_object(audit_json_path, label="previous technical fulltext audit")
                if previous.get("source_lineage") == source["source_lineage"]:
                    preserved_observation_cache = deepcopy(
                        previous.get("semantic_observation_cache") or []
                    )
            except (FileNotFoundError, ValueError, OSError):
                preserved_observation_cache = []
        audit_payload = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "document_id": fulltext["document_id"],
            "output_key": fulltext["output_key"],
            "source_fulltext": {
                "path": str(requested_path),
                "sha256": audited_file_sha,
                "schema_version": source["audited_schema_version"],
            },
            "source_lineage": source["source_lineage"],
            "source_commit": {
                "path": str(commit_path),
                "sha256": sha256_file(commit_path),
                "schema_version": commit.get("schema_version"),
            },
            "source_run": {
                "path": str(run_path.resolve()),
                "sha256": sha256_file(run_path),
                "schema_version": run.get("schema_version"),
            },
            "input_context": input_context,
            "status": "completed",
            "audit_result": "partial",
            "audit_passes": {
                "deterministic": {"status": "completed"},
                "semantic": {"status": "not_run"},
            },
            "release_ready": False,
            "upstream_status": self._upstream_status(fulltext, run),
            "deterministic_checks": deterministic.deterministic_checks,
            "issue_counts": issue_counts,
            "issues": deterministic.issues,
            "confirmation_register": deterministic.confirmation_register,
            "semantic_candidates": deterministic.semantic_candidates,
            "coverage_analysis": deterministic.coverage_analysis,
            "metrics": metrics,
            "normalized_document": normalized_payload,
            "generated_at": utc_now(),
        }
        if preserved_observation_cache:
            audit_payload["semantic_observation_cache"] = preserved_observation_cache
        self._validate_audit_payload(audit_payload)

        # Detect a concurrent edit before emitting a successful Stage ⑪ artifact.
        if sha256_file(requested_path) != audited_file_sha:
            raise TechnicalFulltextAuditError(
                "technical fulltext changed while the audit input was being loaded"
            )

        audit_markdown_path = audit_dir / "technical_fulltext.audit.md"
        markdown = self._render_markdown(audit_payload)
        try:
            audit_json_sha = atomic_write_json(
                audit_json_path,
                audit_payload,
                required_fields=(
                    "schema_version",
                    "document_id",
                    "output_key",
                    "status",
                    "audit_result",
                    "release_ready",
                    "issues",
                    "normalized_document",
                ),
            )
            audit_markdown_sha = atomic_write_markdown(
                audit_markdown_path,
                markdown,
                required_text=AUDIT_MARKDOWN_NOTICE,
            )
        except (OSError, ValueError) as exc:
            raise TechnicalFulltextAuditError(
                f"audit artifact persistence failed: {type(exc).__name__}: {exc}"
            ) from exc
        return {
            "status": "completed",
            "audit_result": "partial",
            "release_ready": False,
            "issue_counts": issue_counts,
            "metrics": metrics,
            "audit": audit_payload,
            "normalized_document": normalized_payload,
            "outputs": {
                "technical_fulltext_audit_json": str(audit_json_path.resolve()),
                "technical_fulltext_audit_json_sha256": audit_json_sha,
                "technical_fulltext_audit_markdown": str(audit_markdown_path.resolve()),
                "technical_fulltext_audit_markdown_sha256": audit_markdown_sha,
            },
        }

    @staticmethod
    def _load_audit_source(requested_path: Path) -> dict[str, Any]:
        """Resolve either a committed Stage 10 source or a Stage 11-E cleaned wrapper."""
        try:
            requested_sha = sha256_file(requested_path)
            payload = load_json_object(requested_path, label="technical fulltext audit source")
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise TechnicalFulltextAuditError(
                f"audit source is unavailable: {type(exc).__name__}: {exc}"
            ) from exc

        if payload.get("schema_version") != "technical_fulltext_cleaned_v1":
            fulltext, commit, commit_path, committed_sha = (
                TechnicalFulltextAuditor._load_committed_snapshot(requested_path)
            )
            return {
                "fulltext": fulltext,
                "commit": commit,
                "commit_path": commit_path,
                "committed_path": requested_path,
                "audited_file_sha256": committed_sha,
                "audited_schema_version": fulltext["schema_version"],
                "source_lineage": {
                    "committed_fulltext": {
                        "path": str(requested_path),
                        "document_id": fulltext["document_id"],
                        "file_sha256": committed_sha,
                        "content_sha256": stable_json_sha256(fulltext),
                    },
                    "hygiene": None,
                    "audited_fulltext": {
                        "path": str(requested_path),
                        "document_id": fulltext["document_id"],
                        "file_sha256": committed_sha,
                        "content_sha256": stable_json_sha256(fulltext),
                        "schema_version": fulltext["schema_version"],
                    },
                },
            }

        required = (
            "cleaned_document_id", "source_document_id", "output_key",
            "source_fulltext_sha256", "cleaned_content_sha256", "fulltext",
        )
        missing = [name for name in required if name not in payload]
        if missing or not isinstance(payload.get("fulltext"), dict):
            raise TechnicalFulltextAuditError(
                f"cleaned fulltext wrapper is invalid; missing fields: {missing}"
            )
        fulltext = payload["fulltext"]
        cleaned_content_sha = stable_json_sha256(fulltext)
        if cleaned_content_sha != payload.get("cleaned_content_sha256"):
            raise TechnicalFulltextAuditError(
                "cleaned fulltext content SHA256 does not match its wrapper"
            )
        committed_path = requested_path.parent.parent / "technical_fulltext.json"
        fulltext_original, commit, commit_path, committed_sha = (
            TechnicalFulltextAuditor._load_committed_snapshot(committed_path)
        )
        try:
            inspect_technical_fulltext(committed_path)
        except Exception as exc:
            raise TechnicalFulltextAuditError(
                f"Stage 10 committed source integrity validation failed: {exc}"
            ) from exc
        if (
            payload.get("source_fulltext_sha256") != committed_sha
            or payload.get("source_document_id") != fulltext_original.get("document_id")
            or payload.get("output_key") != fulltext_original.get("output_key")
            or fulltext.get("document_id") != fulltext_original.get("document_id")
            or fulltext.get("output_key") != fulltext_original.get("output_key")
        ):
            raise TechnicalFulltextAuditError(
                "cleaned fulltext identity does not match its committed source"
            )
        hygiene_path = requested_path.with_name("technical_fulltext.hygiene.json")
        try:
            hygiene = load_json_object(hygiene_path, label="technical fulltext hygiene")
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise TechnicalFulltextAuditError(
                f"hygiene lineage artifact is unavailable: {exc}"
            ) from exc
        if (
            hygiene.get("schema_version") != "technical_fulltext_hygiene_v1"
            or hygiene.get("status") != "completed"
            or hygiene.get("hygiene_status") not in {"completed", "completed_with_unresolved"}
            or hygiene.get("human_review_hygiene_ready") is not True
            or (hygiene.get("source_fulltext") or {}).get("sha256") != committed_sha
            or (hygiene.get("cleaned_fulltext") or {}).get("cleaned_document_id")
            != payload.get("cleaned_document_id")
            or (hygiene.get("cleaned_fulltext") or {}).get("cleaned_content_sha256")
            != cleaned_content_sha
        ):
            raise TechnicalFulltextAuditError(
                "hygiene lineage does not authorize this cleaned audit source"
            )
        return {
            "fulltext": fulltext,
            "commit": commit,
            "commit_path": commit_path,
            "committed_path": committed_path,
            "audited_file_sha256": requested_sha,
            "audited_schema_version": payload["schema_version"],
            "source_lineage": {
                "committed_fulltext": {
                    "path": str(committed_path.resolve()),
                    "document_id": fulltext_original["document_id"],
                    "file_sha256": committed_sha,
                    "content_sha256": stable_json_sha256(fulltext_original),
                },
                "hygiene": {
                    "path": str(hygiene_path.resolve()),
                    "file_sha256": sha256_file(hygiene_path),
                    "schema_version": hygiene["schema_version"],
                    "hygiene_id": hygiene["hygiene_id"],
                    "cleaned_document_id": payload["cleaned_document_id"],
                    "cleaned_content_sha256": cleaned_content_sha,
                },
                "audited_fulltext": {
                    "path": str(requested_path),
                    "document_id": payload["cleaned_document_id"],
                    "file_sha256": requested_sha,
                    "content_sha256": cleaned_content_sha,
                    "schema_version": payload["schema_version"],
                },
            },
        }

    @staticmethod
    def revalidate_audit_source(payload: dict[str, Any]) -> dict[str, Any]:
        source = payload.get("source_fulltext") or {}
        path = Path(str(source.get("path") or "")).expanduser().resolve()
        resolved = TechnicalFulltextAuditor._load_audit_source(path)
        if resolved["audited_file_sha256"] != source.get("sha256"):
            raise TechnicalFulltextAuditError("audit working source SHA256 mismatch")
        if resolved["source_lineage"] != payload.get("source_lineage"):
            raise TechnicalFulltextAuditError("audit source lineage mismatch")
        if sha256_file(resolved["commit_path"]) != (
            payload.get("source_commit") or {}
        ).get("sha256"):
            raise TechnicalFulltextAuditError("audit source commit SHA256 mismatch")
        if (
            resolved["fulltext"].get("document_id") != payload.get("document_id")
            or resolved["fulltext"].get("output_key") != payload.get("output_key")
        ):
            raise TechnicalFulltextAuditError("audit/fulltext identity mismatch")
        return resolved

    @staticmethod
    def _load_committed_snapshot(
        requested_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
        commit_path = requested_path.parent / "technical_fulltext.commit.json"
        try:
            fulltext_sha_before = sha256_file(requested_path)
            fulltext = load_json_object(requested_path, label="technical fulltext")
            fulltext_sha = sha256_file(requested_path)
            commit = load_json_object(commit_path, label="technical fulltext commit")
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise TechnicalFulltextAuditError(
                f"committed fulltext snapshot is unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        if fulltext_sha_before != fulltext_sha:
            raise TechnicalFulltextAuditError(
                "technical fulltext changed while the committed snapshot was being read"
            )
        required = ("schema_version", "document_id", "output_key", "chapters", "chapter_order")
        missing = [field for field in required if field not in fulltext]
        if missing:
            raise TechnicalFulltextAuditError(
                f"technical fulltext is missing required fields: {missing}"
            )
        if fulltext.get("schema_version") != FULLTEXT_SCHEMA_VERSION:
            raise TechnicalFulltextAuditError("technical fulltext schema is unsupported")
        if not str(fulltext.get("document_id") or "").strip() or not str(
            fulltext.get("output_key") or ""
        ).strip():
            raise TechnicalFulltextAuditError("technical fulltext identity is invalid")
        if not isinstance(fulltext.get("chapters"), list) or not isinstance(
            fulltext.get("chapter_order"), list
        ):
            raise TechnicalFulltextAuditError(
                "technical fulltext chapters and chapter_order must be arrays"
            )
        if commit.get("schema_version") != COMMIT_SCHEMA_VERSION or commit.get("status") != "committed":
            raise TechnicalFulltextAuditError("technical fulltext commit is invalid")
        if str(commit.get("output_key") or "") != str(fulltext.get("output_key") or ""):
            raise TechnicalFulltextAuditError("commit output_key does not match fulltext")
        if str(commit.get("document_id") or "") != str(fulltext.get("document_id") or ""):
            raise TechnicalFulltextAuditError("commit document_id does not match fulltext")

        reference = str(commit.get("technical_fulltext_json_path") or "").strip()
        reference_name = Path(reference).name if reference else ""
        if requested_path.parent != commit_path.parent or reference_name != requested_path.name:
            raise TechnicalFulltextAuditError(
                "commit does not reasonably reference the requested fulltext JSON"
            )
        referenced_path = Path(reference).expanduser()
        if referenced_path.is_absolute() and referenced_path.is_file():
            if referenced_path.resolve() != requested_path:
                raise TechnicalFulltextAuditError(
                    "commit references a different existing fulltext JSON"
                )
        expected_sha = str(commit.get("technical_fulltext_json_sha256") or "").strip()
        if not expected_sha or expected_sha != fulltext_sha:
            raise TechnicalFulltextAuditError(
                "commit technical_fulltext_json_sha256 does not match the current fulltext"
            )
        return fulltext, commit, commit_path.resolve(), fulltext_sha

    def _load_input_context(
        self,
        *,
        fulltext: dict[str, Any],
        run: dict[str, Any],
        run_path: Path,
        fulltext_path: Path,
        outputs_root: Path | None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any] | None]]:
        paths = run.get("input_paths")
        hashes = run.get("input_hashes")
        if not isinstance(paths, dict) or not isinstance(hashes, dict):
            raise TechnicalFulltextAuditError(
                "technical fulltext run input_paths/input_hashes are invalid"
            )
        specs = (
            ("writing_outline", "writing_outline_path", "writing_outline_sha256", True),
            ("response_outline", "response_outline_path", "response_outline_sha256", True),
            ("task_information", "task_information_path", "task_information_sha256", True),
            ("draft_units", "draft_units_path", "draft_units_sha256", False),
            (
                "confirmed_project_facts",
                "confirmed_project_facts_path",
                "confirmed_project_facts_sha256",
                False,
            ),
        )
        context: dict[str, Any] = {}
        documents: dict[str, dict[str, Any] | None] = {}
        for name, path_field, hash_field, required in specs:
            recorded_path = paths.get(path_field)
            if name == "writing_outline" and not str(recorded_path or "").strip():
                outline = fulltext.get("standard_outline")
                if isinstance(outline, dict):
                    recorded_path = outline.get("resolved_path") or outline.get("original_path")
            context[name], documents[name] = self._load_context_file(
                name=name,
                recorded_path=recorded_path,
                recorded_sha256=hashes.get(hash_field),
                required=required,
                source_path=run_path,
                outputs_root=outputs_root,
            )

        production = fulltext.get("production")
        if not isinstance(production, dict):
            raise TechnicalFulltextAuditError("technical fulltext production context is invalid")
        context["production_index"], documents["production_index"] = self._load_context_file(
            name="production_index",
            recorded_path=production.get("chapter_production_index_path"),
            recorded_sha256=production.get("chapter_production_index_sha256"),
            required=True,
            source_path=fulltext_path,
            outputs_root=outputs_root,
        )
        return context, documents

    @staticmethod
    def _load_context_file(
        *,
        name: str,
        recorded_path: Any,
        recorded_sha256: Any,
        required: bool,
        source_path: Path,
        outputs_root: Path | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not str(recorded_path or "").strip():
            if required:
                raise TechnicalFulltextAuditError(f"required planning input path is missing: {name}")
            return None, None
        target, recorded = _resolve_reference(
            recorded_path, source_path=source_path, outputs_root=outputs_root
        )
        try:
            payload = load_json_object(target, label=name)
            actual_sha = sha256_file(target)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise TechnicalFulltextAuditError(
                f"planning input {name} is unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        schema_version = str(payload.get("schema_version") or "").strip()
        if not schema_version:
            raise TechnicalFulltextAuditError(
                f"planning input {name} has no schema_version"
            )
        recorded_hash = str(recorded_sha256 or "").strip() or None
        metadata = {
            "path": str(target),
            "recorded_path": recorded,
            "sha256": actual_sha,
            "recorded_sha256": recorded_hash,
            "sha256_matches_run": recorded_hash == actual_sha if recorded_hash else None,
            "schema_version": schema_version,
        }
        return metadata, payload

    @staticmethod
    def _upstream_status(
        fulltext: dict[str, Any], run: dict[str, Any]
    ) -> dict[str, Any]:
        production = fulltext.get("production") if isinstance(fulltext.get("production"), dict) else {}
        return {
            "status": run.get("status"),
            "production_status": production.get("production_status") or run.get("production_status"),
            "composition_status": fulltext.get("composition_status") or run.get("composition_status"),
            "preview_only": bool(fulltext.get("preview_only")),
            "release_ready": bool(fulltext.get("release_ready")),
            "chapter_count_expected": int(production.get("chapter_count_expected") or run.get("chapter_count_expected") or 0),
            "chapter_count_completed": int(production.get("chapter_count_completed") or run.get("chapter_count_completed") or 0),
            "chapter_count_submission_ready": int(production.get("chapter_count_submission_ready") or run.get("chapter_count_submission_ready") or 0),
            "chapter_count_blocked": int(production.get("chapter_count_blocked") or run.get("chapter_count_blocked") or 0),
            "chapter_count_failed": int(production.get("chapter_count_failed") or run.get("chapter_count_failed") or 0),
        }

    @staticmethod
    def _metrics(normalized: NormalizedAuditDocument) -> dict[str, int]:
        return {
            "chapter_count": len(normalized.chapters),
            "section_count": len(normalized.sections),
            "paragraph_count": len(normalized.paragraphs),
            "claim_count": len(normalized.claims),
            "unique_requirement_count": len(normalized.requirements),
            "requirement_occurrence_count": sum(item["occurrence_count"] for item in normalized.requirements),
            "unique_score_item_count": len(normalized.score_items),
            "score_item_occurrence_count": sum(item["occurrence_count"] for item in normalized.score_items),
            "unique_placeholder_count": len(normalized.placeholders),
            "placeholder_occurrence_count": sum(item["occurrence_count"] for item in normalized.placeholders),
        }

    def _validate_audit_payload(self, payload: dict[str, Any]) -> None:
        try:
            schema = load_json_object(self.schema_path, label="technical fulltext audit schema")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextAuditError(f"audit schema is unavailable: {exc}") from exc
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors[:5]
            )
            raise TechnicalFulltextAuditError(f"audit artifact schema validation failed: {detail}")

    @staticmethod
    def _render_markdown(payload: dict[str, Any]) -> str:
        metrics = payload["metrics"]
        upstream = payload["upstream_status"]
        issues = payload["issues"]
        confirmations = payload["confirmation_register"]
        candidates = payload["semantic_candidates"]
        candidate_counts = {name: len(values) for name, values in candidates.items()}
        semantic = payload.get("semantic_audit")
        semantic_completed = isinstance(semantic, dict) and semantic.get("status") == "completed"
        semantic_status = "completed" if semantic_completed else "not_run"
        semantic_notice = (
            "Semantic Audit 已完成结构化裁决；findings 尚未在 Stage ⑪-D 提升为正式 FAI。"
            if semantic_completed
            else "Semantic Audit 尚未执行；以下语义候选尚未判定为问题。"
        )
        lines = [
            "# 全文机器审计报告",
            "",
            f"> {AUDIT_MARKDOWN_NOTICE}",
            f"> {semantic_notice}",
            "",
            "## 审计范围",
            "",
            "- Deterministic Audit：completed",
            f"- Semantic Audit：{semantic_status}",
            "- audit_result：partial",
            "- release_ready：false",
            "",
            "## 输入快照与 Stage ⑩ 上游状态",
            "",
            f"- Document ID：{payload['document_id']}",
            f"- Output Key：{payload['output_key']}",
            f"- Fulltext SHA256：{payload['source_fulltext']['sha256']}",
            f"- Committed source SHA256：{payload['source_lineage']['committed_fulltext']['file_sha256']}",
            f"- Audited content SHA256：{payload['source_lineage']['audited_fulltext']['content_sha256']}",
            f"- production_status：{upstream.get('production_status')}",
            f"- composition_status：{upstream.get('composition_status')}",
            f"- chapter_count_blocked：{upstream.get('chapter_count_blocked')}（仅为上游状态）",
            "",
            "## 确定性问题",
            "",
            f"正式 FAI 数量：{len(issues)}",
            "",
        ]
        if issues:
            for issue in issues:
                lines.append(
                    f"- {issue['issue_id']} [{issue['severity']}] {issue['summary']}"
                    f"（locations={len(issue['locations'])}）"
                )
        else:
            lines.append("- 0（仅表示本轮确定性规则未生成正式 FAI）")
        lines.extend([
            "",
            "## 待确认事实治理",
            "",
            f"- FCA 数量：{len(confirmations)}",
            f"- Placeholder occurrence 数：{metrics['placeholder_occurrence_count']}",
            "- 所有 FCA 状态均为 pending；本阶段未确认或填充任何事实。",
            "",
            "## 语义审计候选",
            "",
            "以下仅为 Stage ⑪-C 输入，不是 issue、blocker 或发布结论：",
            "",
            f"- Near duplicate：{candidate_counts['duplicate_pairs']}",
            f"- Placeholder alias：{candidate_counts['confirmation_aliases']}",
            f"- Requirement location mismatch：{candidate_counts['requirement_location_mismatches']}",
            f"- Score distribution：{candidate_counts['score_distribution']}",
            f"- Numeric facts：{candidate_counts['numeric_facts']}",
            f"- Terminology：{candidate_counts['terminology']}",
            "",
        ])
        if semantic_completed:
            verdicts = semantic.get("verdict_counts") or {}
            lines.extend([
                "## Semantic Findings（尚未提升为正式 FAI）",
                "",
                f"- Batch 数：{semantic.get('batch_count', 0)}",
                f"- Audit unit 数：{semantic.get('audit_unit_count', 0)}",
                f"- Finding 数：{semantic.get('finding_count', 0)}",
                f"- issue verdict：{verdicts.get('issue', 0)}",
                f"- acceptable verdict：{verdicts.get('acceptable', 0)}",
                f"- uncertain verdict：{verdicts.get('uncertain', 0)}",
                "- 模型 finding 不是正式 FAI；issues 与 issue_counts 未被本阶段改写。",
                "",
            ])
        lines.extend([
            "## Requirement 分布",
            "",
            f"- Requirement unique 数：{metrics['unique_requirement_count']}",
            f"- 位置异常候选：{candidate_counts['requirement_location_mismatches']}",
            "",
            "## Score Item 分布",
            "",
            f"- Score item unique 数：{metrics['unique_score_item_count']}",
            f"- 跨多章节分布候选：{candidate_counts['score_distribution']}",
            "",
            "## Normalized Document 指标",
            "",
            f"- 章节数：{metrics['chapter_count']}",
            f"- Section 数：{metrics['section_count']}",
            f"- Paragraph 数：{metrics['paragraph_count']}",
            f"- Claim 数：{metrics['claim_count']}",
            "",
            "本报告尚未执行 Stage ⑪-D 的验证、合并、FAI 提升与最终状态归一化，不表示全文无问题，且不构成发布依据。",
            "",
        ])
        return "\n".join(lines)


def inspect_technical_fulltext_audit(path: str | Path) -> dict[str, Any]:
    """Read and summarize a persisted Stage ⑪ audit artifact without side effects."""
    target = Path(path).expanduser().resolve()
    try:
        payload = load_json_object(target, label="technical fulltext audit")
    except (FileNotFoundError, ValueError) as exc:
        raise TechnicalFulltextAuditError(f"audit artifact is unavailable: {exc}") from exc
    TechnicalFulltextAuditor()._validate_audit_payload(payload)
    return {
        "schema_version": payload.get("schema_version"),
        "document_id": payload.get("document_id"),
        "output_key": payload.get("output_key"),
        "status": payload.get("status"),
        "audit_result": payload.get("audit_result"),
        "audit_passes": deepcopy(payload.get("audit_passes") or {}),
        "release_ready": payload.get("release_ready"),
        "issue_counts": deepcopy(payload.get("issue_counts") or {}),
        "confirmation_count": len(payload.get("confirmation_register") or []),
        "semantic_candidate_counts": {
            name: len(values) if isinstance(values, list) else 0
            for name, values in (payload.get("semantic_candidates") or {}).items()
        },
        "semantic_audit": deepcopy(payload.get("semantic_audit")),
        "semantic_finding_count": len(payload.get("semantic_findings") or []),
        "finalization": deepcopy(payload.get("finalization")),
        "semantic_finding_disposition_count": len(
            payload.get("semantic_finding_dispositions") or []
        ),
        "confirmation_relationship_count": len(
            payload.get("confirmation_relationships") or []
        ),
        "metrics": deepcopy(payload.get("metrics") or {}),
        "source_fulltext": deepcopy(payload.get("source_fulltext") or {}),
        "source_lineage": deepcopy(payload.get("source_lineage") or {}),
    }

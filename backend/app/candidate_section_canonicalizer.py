"""Deterministic, provenance-bound Candidate section identity canonicalization.

Candidate JSON is an immutable production artifact.  This module may construct a
derived in-memory view for validation/composition, but never persists it over the
source Candidate.  It deliberately has no fuzzy matching or title aliases.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable


CANONICALIZATION_VERSION = "candidate_section_canonicalization_v1"


class CandidateCanonicalizationError(ValueError):
    """A Candidate cannot be safely given a canonical section identity."""


@dataclass(frozen=True)
class CanonicalCandidateView:
    candidate: dict[str, Any]
    lineage: dict[str, Any]


def _stable_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sections(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    values = candidate.get("sections") if isinstance(candidate, dict) else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise CandidateCanonicalizationError("candidate sections must be an object list")
    return values


def _expected_sections(values: Iterable[dict[str, Any]] | None) -> list[dict[str, str]]:
    expected: list[dict[str, str]] = []
    for item in values or []:
        section_id = str((item or {}).get("section_id") or "").strip()
        title = str((item or {}).get("section_title") or (item or {}).get("title") or "").strip()
        if not section_id or not title:
            raise CandidateCanonicalizationError("expected outline section identity is invalid")
        expected.append({"section_id": section_id, "section_title": title})
    if len({item["section_id"] for item in expected}) != len(expected):
        raise CandidateCanonicalizationError("expected outline section IDs are not unique")
    if len({item["section_title"] for item in expected}) != len(expected):
        raise CandidateCanonicalizationError("expected outline section titles are ambiguous")
    return expected


def _lineage(*, source_candidate_sha256: str | None, mode: str, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_candidate_sha256": str(source_candidate_sha256 or "") or None,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "canonicalization_mode": mode,
        "canonicalized_section_ids": [
            str(item.get("section_id") or "") for item in _sections(candidate)
        ],
        "canonical_view_sha256": _stable_json_sha256(candidate),
    }


def _all_section_id_state(sections: list[dict[str, Any]]) -> str:
    present = [bool(str(item.get("section_id") or "").strip()) for item in sections]
    if all(present):
        return "all_explicit"
    if not any(present):
        return "all_missing"
    return "mixed"


def _with_ids(candidate: dict[str, Any], section_ids: list[str]) -> dict[str, Any]:
    view = deepcopy(candidate)
    sections = _sections(view)
    if len(sections) != len(section_ids):
        raise CandidateCanonicalizationError("candidate section count changed during canonicalization")
    for section, section_id in zip(sections, section_ids):
        section["section_id"] = section_id
    return view


def _exact_redundant_chapter_wrapper(
    *,
    candidate: dict[str, Any],
    chapter_id: str,
    expected: list[dict[str, str]],
    expected_chapter_title: str | None,
) -> dict[str, Any] | None:
    """Return a derived view without one exact chapter-title wrapper section.

    This is deliberately narrow: there must be exactly one leading
    ``<chapter>-00`` section whose heading exactly equals the canonical chapter
    title, and every remaining section ID/title must already equal the outline
    in canonical order.  The source Candidate is never mutated.
    """
    chapter_title = str(expected_chapter_title or "").strip()
    if not expected or not chapter_title:
        return None
    sections = _sections(candidate)
    if len(sections) != len(expected) + 1:
        return None
    wrapper = sections[0]
    if (
        str(wrapper.get("section_id") or "").strip() != f"{chapter_id}-00"
        or str(wrapper.get("heading") or "").strip() != chapter_title
    ):
        return None
    remaining = sections[1:]
    expected_ids = [item["section_id"] for item in expected]
    expected_titles = [item["section_title"] for item in expected]
    if [str(item.get("section_id") or "").strip() for item in remaining] != expected_ids:
        return None
    if [str(item.get("heading") or "").strip() for item in remaining] != expected_titles:
        return None
    view = deepcopy(candidate)
    view["sections"] = deepcopy(remaining)
    return view


def canonicalize_agent_candidate(
    *,
    candidate: dict[str, Any],
    chapter_id: str,
    expected_sections: Iterable[dict[str, Any]] | None,
    section_identity_map: Iterable[dict[str, Any]] | None,
    section_identity_issues: Iterable[dict[str, Any]] | None,
) -> CanonicalCandidateView:
    """Make a future Agent candidate persist the identities it already resolved."""
    sections = _sections(candidate)
    expected = _expected_sections(expected_sections)
    state = _all_section_id_state(sections)
    if expected:
        if list(section_identity_issues or []):
            raise CandidateCanonicalizationError("section identity resolution contains issues")
        identity_map = list(section_identity_map or [])
        if len(sections) != len(expected) or len(identity_map) != len(expected):
            raise CandidateCanonicalizationError("section identity map does not completely cover the outline")
        expected_ids = [item["section_id"] for item in expected]
        expected_titles = [item["section_title"] for item in expected]
        mapped_ids = [str(item.get("section_id") or "").strip() for item in identity_map]
        mapped_titles = [str(item.get("section_title") or "").strip() for item in identity_map]
        actual_titles = [str(item.get("heading") or "").strip() for item in sections]
        if mapped_ids != expected_ids or mapped_titles != expected_titles or actual_titles != expected_titles:
            raise CandidateCanonicalizationError("section identity map is not an exact outline-backed mapping")
        if state == "mixed":
            raise CandidateCanonicalizationError("candidate has mixed explicit and missing section IDs")
        if state == "all_explicit":
            actual_ids = [str(item.get("section_id") or "").strip() for item in sections]
            if actual_ids != expected_ids:
                raise CandidateCanonicalizationError("explicit section IDs do not match the resolved outline identity")
            view = deepcopy(candidate)
            mode = "explicit_id"
        else:
            view = _with_ids(candidate, expected_ids)
            mode = "validated_agent_identity_map"
        return CanonicalCandidateView(view, _lineage(source_candidate_sha256=None, mode=mode, candidate=view))

    if state == "mixed":
        raise CandidateCanonicalizationError("leaf candidate has mixed explicit and missing section IDs")
    if state == "all_explicit":
        view = deepcopy(candidate)
        return CanonicalCandidateView(view, _lineage(source_candidate_sha256=None, mode="explicit_id", candidate=view))
    local_ids = [f"{chapter_id}-LOCAL-{index:02d}" for index in range(1, len(sections) + 1)]
    view = _with_ids(candidate, local_ids)
    return CanonicalCandidateView(
        view,
        _lineage(source_candidate_sha256=None, mode="deterministic_leaf_local_id", candidate=view),
    )


def canonicalize_trusted_legacy_candidate(
    *,
    candidate: dict[str, Any],
    run: dict[str, Any],
    chapter_id: str,
    output_key: str,
    source_candidate_sha256: str,
    expected_candidate_sha256: str | None,
    expected_outline_sha256: str | None,
    actual_outline_sha256: str | None,
    expected_sections: Iterable[dict[str, Any]] | None,
    expected_chapter_title: str | None = None,
) -> CanonicalCandidateView:
    """Create a strict in-memory view for a pre-canonicalization production Candidate."""
    sections = _sections(candidate)
    expected = _expected_sections(expected_sections)
    state = _all_section_id_state(sections)
    wrapper_view = _exact_redundant_chapter_wrapper(
        candidate=candidate,
        chapter_id=chapter_id,
        expected=expected,
        expected_chapter_title=expected_chapter_title,
    )
    if wrapper_view is not None:
        expected_hash = str(expected_candidate_sha256 or "").strip()
        source_hash = str(source_candidate_sha256 or "").strip()
        run_hash = str(((run.get("candidate_artifacts") or {}).get("json_sha256") or "")).strip()
        run_output_key = str((run.get("run_summary") or {}).get("output_key") or run.get("output_key") or "").strip()
        target = run.get("target") if isinstance(run.get("target"), dict) else {}
        if not expected_hash or source_hash != expected_hash or run_hash != source_hash:
            raise CandidateCanonicalizationError("chapter-wrapper candidate hash provenance is not exact")
        if (
            run_output_key != str(output_key or "")
            or str(target.get("chapter_id") or "") != chapter_id
            or target.get("section_id") not in (None, "")
        ):
            raise CandidateCanonicalizationError("chapter-wrapper candidate run target/output_key provenance is invalid")
        if not expected_outline_sha256 or expected_outline_sha256 != actual_outline_sha256:
            raise CandidateCanonicalizationError("chapter-wrapper candidate writing outline hash provenance is invalid")

        issues = list(run.get("section_identity_issues") or [])
        identity_map = list(run.get("section_identity_map") or [])
        expected_ids = [item["section_id"] for item in expected]
        expected_titles = [item["section_title"] for item in expected]
        mapped_ids = [str(item.get("section_id") or "").strip() for item in identity_map]
        mapped_titles = [str(item.get("section_title") or "").strip() for item in identity_map]
        allowed_methods = {"explicit_id", "unique_title", "title_and_order"}
        methods = [str(item.get("resolution_method") or "").strip() for item in identity_map]
        if (
            mapped_ids != expected_ids
            or mapped_titles != expected_titles
            or any(method not in allowed_methods for method in methods)
        ):
            raise CandidateCanonicalizationError("chapter-wrapper candidate identity map is not exact")
        if not (
            len(issues) == 1
            and str(issues[0].get("code") or "") == "section_identity_resolution_failed"
            and int(issues[0].get("section_index", -1)) == 0
            and str(issues[0].get("section_title") or "").strip()
            == str(expected_chapter_title or "").strip()
        ):
            raise CandidateCanonicalizationError("chapter-wrapper candidate has unexpected section identity issues")
        return CanonicalCandidateView(
            wrapper_view,
            _lineage(
                source_candidate_sha256=source_hash,
                mode="exact_redundant_chapter_wrapper_removed",
                candidate=wrapper_view,
            ),
        )
    if state == "all_explicit":
        view = deepcopy(candidate)
        return CanonicalCandidateView(view, _lineage(source_candidate_sha256=source_candidate_sha256, mode="explicit_id", candidate=view))
    if state == "mixed":
        raise CandidateCanonicalizationError("legacy candidate has mixed explicit and missing section IDs")

    expected_hash = str(expected_candidate_sha256 or "").strip()
    source_hash = str(source_candidate_sha256 or "").strip()
    run_hash = str(((run.get("candidate_artifacts") or {}).get("json_sha256") or "")).strip()
    run_output_key = str((run.get("run_summary") or {}).get("output_key") or run.get("output_key") or "").strip()
    target = run.get("target") if isinstance(run.get("target"), dict) else {}
    if not expected_hash or source_hash != expected_hash or run_hash != source_hash:
        raise CandidateCanonicalizationError("legacy candidate hash provenance is not exact")
    if run_output_key != str(output_key or "") or str(target.get("chapter_id") or "") != chapter_id or target.get("section_id") not in (None, ""):
        raise CandidateCanonicalizationError("legacy candidate run target/output_key provenance is invalid")
    if not expected_outline_sha256 or expected_outline_sha256 != actual_outline_sha256:
        raise CandidateCanonicalizationError("legacy candidate writing outline hash provenance is invalid")

    if not expected:
        local_ids = [f"{chapter_id}-LOCAL-{index:02d}" for index in range(1, len(sections) + 1)]
        view = _with_ids(candidate, local_ids)
        return CanonicalCandidateView(view, _lineage(source_candidate_sha256=source_hash, mode="deterministic_leaf_local_id", candidate=view))

    issues = list(run.get("section_identity_issues") or [])
    identity_map = list(run.get("section_identity_map") or [])
    expected_ids = [item["section_id"] for item in expected]
    expected_titles = [item["section_title"] for item in expected]
    actual_titles = [str(item.get("heading") or "").strip() for item in sections]
    mapped_ids = [str(item.get("section_id") or "").strip() for item in identity_map]
    mapped_titles = [str(item.get("section_title") or "").strip() for item in identity_map]
    allowed_methods = {"explicit_id", "unique_title", "title_and_order"}
    methods = [str(item.get("resolution_method") or "").strip() for item in identity_map]
    if (
        issues
        or len(sections) != len(expected)
        or len(identity_map) != len(expected)
        or actual_titles != expected_titles
        or mapped_ids != expected_ids
        or mapped_titles != expected_titles
        or any(method not in allowed_methods for method in methods)
    ):
        raise CandidateCanonicalizationError("legacy section identity map is not an exact complete outline-backed mapping")
    view = _with_ids(candidate, expected_ids)
    return CanonicalCandidateView(
        view,
        _lineage(source_candidate_sha256=source_hash, mode="validated_run_identity_map", candidate=view),
    )

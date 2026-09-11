"""Strict canonicalization for one exact redundant legacy chapter wrapper."""

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
        title = str(
            (item or {}).get("section_title") or (item or {}).get("title") or ""
        ).strip()
        if not section_id or not title:
            raise CandidateCanonicalizationError(
                "expected outline section identity is invalid"
            )
        expected.append({"section_id": section_id, "section_title": title})
    if len({item["section_id"] for item in expected}) != len(expected):
        raise CandidateCanonicalizationError(
            "expected outline section IDs are not unique"
        )
    if len({item["section_title"] for item in expected}) != len(expected):
        raise CandidateCanonicalizationError(
            "expected outline section titles are ambiguous"
        )
    return expected


def _lineage(
    *, source_candidate_sha256: str | None, mode: str, candidate: dict[str, Any]
) -> dict[str, Any]:
    return {
        "source_candidate_sha256": str(source_candidate_sha256 or "") or None,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "canonicalization_mode": mode,
        "canonicalized_section_ids": [
            str(item.get("section_id") or "") for item in _sections(candidate)
        ],
        "canonical_view_sha256": _stable_json_sha256(candidate),
    }


def _exact_redundant_chapter_wrapper(
    *,
    candidate: dict[str, Any],
    chapter_id: str,
    expected: list[dict[str, str]],
    expected_chapter_title: str | None,
) -> dict[str, Any] | None:
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
    if [str(item.get("section_id") or "").strip() for item in remaining] != [
        item["section_id"] for item in expected
    ]:
        return None
    if [str(item.get("heading") or "").strip() for item in remaining] != [
        item["section_title"] for item in expected
    ]:
        return None
    view = deepcopy(candidate)
    view["sections"] = deepcopy(remaining)
    return view


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
    sections = _sections(candidate)
    expected = _expected_sections(expected_sections)
    wrapper_view = _exact_redundant_chapter_wrapper(
        candidate=candidate,
        chapter_id=chapter_id,
        expected=expected,
        expected_chapter_title=expected_chapter_title,
    )
    if wrapper_view is not None:
        expected_hash = str(expected_candidate_sha256 or "").strip()
        source_hash = str(source_candidate_sha256 or "").strip()
        run_hash = str(
            ((run.get("candidate_artifacts") or {}).get("json_sha256") or "")
        ).strip()
        run_output_key = str(
            (run.get("run_summary") or {}).get("output_key")
            or run.get("output_key")
            or ""
        ).strip()
        target = run.get("target") if isinstance(run.get("target"), dict) else {}
        if not expected_hash or source_hash != expected_hash or run_hash != source_hash:
            raise CandidateCanonicalizationError(
                "chapter-wrapper candidate hash provenance is not exact"
            )
        if (
            run_output_key != str(output_key or "")
            or str(target.get("chapter_id") or "") != chapter_id
            or target.get("section_id") not in (None, "")
        ):
            raise CandidateCanonicalizationError(
                "chapter-wrapper candidate run target/output_key provenance is invalid"
            )
        if not expected_outline_sha256 or expected_outline_sha256 != actual_outline_sha256:
            raise CandidateCanonicalizationError(
                "chapter-wrapper candidate writing outline hash provenance is invalid"
            )

        identity_map = list(run.get("section_identity_map") or [])
        if [str(item.get("section_id") or "").strip() for item in identity_map] != [
            item["section_id"] for item in expected
        ] or [
            str(item.get("section_title") or "").strip() for item in identity_map
        ] != [item["section_title"] for item in expected]:
            raise CandidateCanonicalizationError(
                "chapter-wrapper candidate identity map is not exact"
            )
        allowed_methods = {"explicit_id", "unique_title", "title_and_order"}
        if any(
            str(item.get("resolution_method") or "").strip() not in allowed_methods
            for item in identity_map
        ):
            raise CandidateCanonicalizationError(
                "chapter-wrapper candidate identity map is not exact"
            )
        issues = list(run.get("section_identity_issues") or [])
        if not (
            len(issues) == 1
            and str(issues[0].get("code") or "")
            == "section_identity_resolution_failed"
            and int(issues[0].get("section_index", -1)) == 0
            and str(issues[0].get("section_title") or "").strip()
            == str(expected_chapter_title or "").strip()
        ):
            raise CandidateCanonicalizationError(
                "chapter-wrapper candidate has unexpected section identity issues"
            )
        return CanonicalCandidateView(
            wrapper_view,
            _lineage(
                source_candidate_sha256=source_hash,
                mode="exact_redundant_chapter_wrapper_removed",
                candidate=wrapper_view,
            ),
        )

    if all(str(item.get("section_id") or "").strip() for item in sections):
        view = deepcopy(candidate)
        return CanonicalCandidateView(
            view,
            _lineage(
                source_candidate_sha256=source_candidate_sha256,
                mode="explicit_id",
                candidate=view,
            ),
        )
    raise CandidateCanonicalizationError(
        "legacy candidate does not have explicit canonical section IDs"
    )

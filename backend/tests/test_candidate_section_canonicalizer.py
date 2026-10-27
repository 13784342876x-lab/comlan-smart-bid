from __future__ import annotations

from copy import deepcopy

import pytest

from app.candidate_section_canonicalizer import (
    CandidateCanonicalizationError,
    CANONICALIZATION_VERSION,
    canonicalize_agent_candidate,
    canonicalize_trusted_legacy_candidate,
)
from app.technical_fulltext_composer import _validate_candidate_structure, build_standard_section_index
from app.technical_writing_agent import normalize_chapter_draft_output


def _outline() -> dict:
    return {
        "chapter_id": "WC-001",
        "title": "项目方案",
        "children": [
            {"chapter_id": "WC-001-01", "title": "项目背景"},
            {"chapter_id": "WC-001-02", "title": "建设目标"},
        ],
    }


def _candidate(*, ids: list[str | None] | None = None, leaf: bool = False) -> dict:
    headings = ["绿色准入", "绿色采购"] if leaf else ["项目背景", "建设目标"]
    return {
        "chapter_title": "绿色供应链" if leaf else "项目方案",
        "sections": [
            {"heading": heading, "content": "正文", **({"section_id": section_id} if section_id else {})}
            for heading, section_id in zip(headings, ids or [None] * len(headings))
        ],
        "claims": [], "placeholders_used": [], "score_items_covered": [],
        "requirements_covered": [], "warnings": [],
    }


def _identity_map() -> list[dict]:
    return [
        {"section_id": "WC-001-01", "section_title": "项目背景", "resolution_method": "unique_title"},
        {"section_id": "WC-001-02", "section_title": "建设目标", "resolution_method": "title_and_order"},
    ]


def _run() -> dict:
    return {
        "status": "completed", "output_key": "fulltext-run", "target": {"chapter_id": "WC-001", "section_id": None},
        "candidate_artifacts": {"json_sha256": "candidate-sha"},
        "section_identity_map": _identity_map(), "section_identity_issues": [],
    }


def _legacy(*, candidate: dict | None = None, run: dict | None = None, expected_outline_sha: str = "outline-sha"):
    return canonicalize_trusted_legacy_candidate(
        candidate=candidate or _candidate(), run=run or _run(), chapter_id="WC-001", output_key="fulltext-run",
        source_candidate_sha256="candidate-sha", expected_candidate_sha256="candidate-sha",
        expected_outline_sha256=expected_outline_sha, actual_outline_sha256="outline-sha",
        expected_sections=[{"section_id": "WC-001-01", "section_title": "项目背景"}, {"section_id": "WC-001-02", "section_title": "建设目标"}],
    )


def test_future_normalization_preserves_ids_without_supporting_tables():
    source = _candidate(ids=["WC-001-01", "WC-001-02"])
    normalized = normalize_chapter_draft_output(source)
    assert [item["section_id"] for item in normalized["sections"]] == ["WC-001-01", "WC-001-02"]


def test_future_agent_canonicalization_keeps_identity_with_or_without_tables():
    for candidate in (_candidate(), {**_candidate(), "generated_supporting_tables": []}):
        view = canonicalize_agent_candidate(
            candidate=candidate, chapter_id="WC-001",
            expected_sections=[{"section_id": "WC-001-01", "section_title": "项目背景"}, {"section_id": "WC-001-02", "section_title": "建设目标"}],
            section_identity_map=_identity_map(), section_identity_issues=[],
        )
        assert [item["section_id"] for item in view.candidate["sections"]] == ["WC-001-01", "WC-001-02"]


def test_future_agent_does_not_fabricate_unresolved_outline_identity():
    with pytest.raises(CandidateCanonicalizationError, match="issues"):
        canonicalize_agent_candidate(
            candidate=_candidate(), chapter_id="WC-001", expected_sections=[{"section_id": "WC-001-01", "section_title": "项目背景"}, {"section_id": "WC-001-02", "section_title": "建设目标"}],
            section_identity_map=[], section_identity_issues=[{"code": "ambiguous"}],
        )


def test_legacy_outline_backed_view_passes_unchanged_strict_validator():
    source = _candidate()
    source_before = deepcopy(source)
    view = _legacy(candidate=source)
    assert source == source_before
    assert view.lineage["canonicalization_version"] == CANONICALIZATION_VERSION
    assert view.lineage["canonicalization_mode"] == "validated_run_identity_map"
    assert view.lineage["source_candidate_sha256"] == "candidate-sha"
    assert view.lineage["canonical_view_sha256"]
    assert not _validate_candidate_structure("WC-001", _outline(), tuple(build_standard_section_index(_outline())), view.candidate, __file__)


def test_exact_redundant_chapter_wrapper_is_removed_only_from_derived_view():
    source = _candidate(ids=["WC-001-01", "WC-001-02"])
    source["sections"].insert(
        0,
        {
            "section_id": "WC-001-00",
            "heading": "项目方案",
            "content": "重复章引言",
        },
    )
    source_before = deepcopy(source)
    run = _run()
    run["section_identity_issues"] = [
        {
            "code": "section_identity_resolution_failed",
            "section_index": 0,
            "section_title": "项目方案",
        }
    ]

    view = canonicalize_trusted_legacy_candidate(
        candidate=source,
        run=run,
        chapter_id="WC-001",
        output_key="fulltext-run",
        source_candidate_sha256="candidate-sha",
        expected_candidate_sha256="candidate-sha",
        expected_outline_sha256="outline-sha",
        actual_outline_sha256="outline-sha",
        expected_sections=[
            {"section_id": "WC-001-01", "section_title": "项目背景"},
            {"section_id": "WC-001-02", "section_title": "建设目标"},
        ],
        expected_chapter_title="项目方案",
    )

    assert source == source_before
    assert view.lineage["canonicalization_mode"] == (
        "exact_redundant_chapter_wrapper_removed"
    )
    assert [item["section_id"] for item in view.candidate["sections"]] == [
        "WC-001-01",
        "WC-001-02",
    ]


@pytest.mark.parametrize("mutate", ["title", "order", "hash", "outline", "ambiguous", "incomplete"])
def test_legacy_canonicalization_rejects_untrusted_or_nonexact_mapping(mutate: str):
    candidate, run = _candidate(), _run()
    kwargs = {}
    if mutate == "title":
        candidate["sections"][0]["heading"] = "不同标题"
    elif mutate == "order":
        candidate["sections"].reverse()
    elif mutate == "hash":
        run["candidate_artifacts"]["json_sha256"] = "other"
    elif mutate == "outline":
        kwargs["expected_outline_sha"] = "stale-outline"
    elif mutate == "ambiguous":
        run["section_identity_map"][1]["section_id"] = "WC-001-01"
    elif mutate == "incomplete":
        run["section_identity_map"].pop()
    with pytest.raises(CandidateCanonicalizationError):
        _legacy(candidate=candidate, run=run, **kwargs)


def test_leaf_legacy_canonicalization_is_stable_and_source_remains_immutable():
    source = _candidate(leaf=True)
    run = _run()
    run["target"] = {"chapter_id": "WC-006", "section_id": None}
    view_one = canonicalize_trusted_legacy_candidate(
        candidate=source, run=run, chapter_id="WC-006", output_key="fulltext-run",
        source_candidate_sha256="candidate-sha", expected_candidate_sha256="candidate-sha",
        expected_outline_sha256="outline-sha", actual_outline_sha256="outline-sha", expected_sections=[],
    )
    view_two = canonicalize_trusted_legacy_candidate(
        candidate=source, run=run, chapter_id="WC-006", output_key="fulltext-run",
        source_candidate_sha256="candidate-sha", expected_candidate_sha256="candidate-sha",
        expected_outline_sha256="outline-sha", actual_outline_sha256="outline-sha", expected_sections=[],
    )
    assert [item.get("section_id") for item in source["sections"]] == [None, None]
    assert [item["section_id"] for item in view_one.candidate["sections"]] == ["WC-006-LOCAL-01", "WC-006-LOCAL-02"]
    assert view_one.candidate == view_two.candidate
    assert view_one.lineage["canonical_view_sha256"] == view_two.lineage["canonical_view_sha256"]

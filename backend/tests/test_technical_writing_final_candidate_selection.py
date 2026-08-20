from __future__ import annotations

from copy import deepcopy

from app.technical_writing_agent import (
    _candidate_selection_audit,
    _fresh_candidate_is_monotonic_improvement,
)


SECTION_ID = "CH-SELECT-01"


def _snapshot(
    *,
    raw: int,
    grouped: int,
    prose: int,
    floor_met: bool = True,
    quality_counts: dict[str, int] | None = None,
    categories: list[tuple[str, str]] | None = None,
    placeholders: list[str] | None = None,
) -> dict:
    quality_counts = dict(quality_counts or {})
    return {
        "schema_valid": True,
        "fatal_issue_count": 0,
        "raw_error_count": raw,
        "grouped_error_count": grouped,
        "blocking_error_categories": [list(item) for item in (categories or [])],
        "placeholder_ids": list(placeholders or []),
        "content_placeholder_ids": list(placeholders or []),
        "coverage": {
            "headings_match": True,
            "missing_response_point_count": 0,
            "missing_score_item_count": 0,
            "covered_response_point_count": 3,
            "covered_score_item_count": 2,
        },
        "quality_comparison": {
            "prose_character_count": prose,
            "prose_ratio": 0.75,
            "quality_issue_codes": sorted(quality_counts),
            "quality_issue_code_counts": quality_counts,
        },
        "section_quality": {
            SECTION_ID: {
                "section_id": SECTION_ID,
                "section_title": "实施方案",
                "prose_character_count": prose,
                "prose_paragraph_count": 4,
                "quality_floor_met": floor_met,
                "quality_floor": {
                    "minimum_paragraph_count": 2,
                    "minimum_prose_characters": 300,
                },
            }
        },
    }


def test_final_selector_accepts_smaller_fresh_when_raw_and_grouped_errors_drop():
    initial = _snapshot(raw=34, grouped=17, prose=2_000)
    fresh = _snapshot(raw=29, grouped=12, prose=1_850)

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    ) == (True, "fresh_is_strict_monotonic_improvement")


def test_final_selector_records_warning_for_fifteen_to_thirty_percent_reduction():
    initial = _snapshot(raw=20, grouped=10, prose=2_000)
    fresh = _snapshot(raw=15, grouped=8, prose=1_600)

    accepted, reason = _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    )
    audit = _candidate_selection_audit(initial=initial, fresh=fresh)

    assert accepted is True
    assert reason == "fresh_is_strict_monotonic_improvement"
    assert audit["sections_with_length_warning"] == [SECTION_ID]
    assert audit["sections_crossing_quality_floor"] == []


def test_final_selector_does_not_reject_absolute_change_below_300_characters():
    initial = _snapshot(raw=12, grouped=8, prose=500)
    fresh = _snapshot(raw=10, grouped=7, prose=250)

    accepted, _ = _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    )
    audit = _candidate_selection_audit(initial=initial, fresh=fresh)

    assert accepted is True
    assert audit["sections_within_tolerance"] == [SECTION_ID]


def test_existing_initial_quality_issue_does_not_reject_unchanged_fresh_issue():
    quality = {"insufficient_prose": 1}
    initial = _snapshot(raw=10, grouped=7, prose=500, quality_counts=quality)
    fresh = _snapshot(raw=8, grouped=6, prose=450, quality_counts=quality)

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    ) == (True, "fresh_is_strict_monotonic_improvement")


def test_final_selector_rejects_new_insufficient_prose():
    initial = _snapshot(raw=10, grouped=7, prose=800)
    fresh = _snapshot(
        raw=8,
        grouped=6,
        prose=700,
        quality_counts={"insufficient_prose": 1},
    )

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    ) == (False, "fresh_new_quality_blocking_issue")


def test_final_selector_rejects_new_structured_content_dominance():
    initial = _snapshot(raw=10, grouped=7, prose=800)
    fresh = _snapshot(
        raw=8,
        grouped=6,
        prose=700,
        quality_counts={"structured_content_exceeds_prose": 1},
    )

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    ) == (False, "fresh_new_quality_blocking_issue")


def test_final_selector_rejects_section_crossing_absolute_quality_floor():
    initial = _snapshot(raw=10, grouped=7, prose=700, floor_met=True)
    fresh = _snapshot(raw=8, grouped=6, prose=250, floor_met=False)

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    ) == (False, "fresh_quality_floor_crossed")


def test_final_selector_still_rejects_coverage_regression_and_placeholder_loss():
    initial = _snapshot(
        raw=10, grouped=7, prose=700, placeholders=["PH-PERSON"]
    )
    coverage_fresh = _snapshot(
        raw=8, grouped=6, prose=650, placeholders=["PH-PERSON"]
    )
    coverage_fresh["coverage"]["covered_score_item_count"] = 1
    placeholder_fresh = _snapshot(raw=8, grouped=6, prose=650)

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=coverage_fresh
    ) == (False, "fresh_coverage_regressed")
    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=placeholder_fresh
    ) == (False, "fresh_placeholder_lost")


def test_final_selector_keeps_fact_category_monotonic_rule():
    initial = _snapshot(
        raw=10,
        grouped=7,
        prose=700,
        categories=[("numeric_commitment", "schedule_duration")],
    )
    fresh = deepcopy(initial)
    fresh["raw_error_count"] = 8
    fresh["grouped_error_count"] = 6
    fresh["blocking_error_categories"].append(
        ["unconfirmed_product_capability", "product_capability"]
    )

    assert _fresh_candidate_is_monotonic_improvement(
        initial=initial, fresh=fresh
    ) == (False, "fresh_added_new_blocking_categories")

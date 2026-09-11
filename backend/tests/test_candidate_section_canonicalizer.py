from copy import deepcopy

from app.candidate_section_canonicalizer import (
    canonicalize_trusted_legacy_candidate,
)


def test_exact_redundant_chapter_wrapper_is_removed_only_from_derived_view():
    source = {
        "chapter_title": "项目方案",
        "sections": [
            {
                "section_id": "WC-001-00",
                "heading": "项目方案",
                "content": "重复章引言",
            },
            {
                "section_id": "WC-001-01",
                "heading": "项目背景",
                "content": "正文",
            },
            {
                "section_id": "WC-001-02",
                "heading": "建设目标",
                "content": "正文",
            },
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }
    source_before = deepcopy(source)
    run = {
        "status": "completed",
        "output_key": "fulltext-run",
        "target": {"chapter_id": "WC-001", "section_id": None},
        "candidate_artifacts": {"json_sha256": "candidate-sha"},
        "section_identity_map": [
            {
                "section_id": "WC-001-01",
                "section_title": "项目背景",
                "resolution_method": "unique_title",
            },
            {
                "section_id": "WC-001-02",
                "section_title": "建设目标",
                "resolution_method": "title_and_order",
            },
        ],
        "section_identity_issues": [
            {
                "code": "section_identity_resolution_failed",
                "section_index": 0,
                "section_title": "项目方案",
            }
        ],
    }

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

from __future__ import annotations

from copy import deepcopy

from app.technical_writing_agent import (
    _build_fresh_repair_task_card,
    _candidate_sha256,
    _fresh_sentence_locations,
    _local_guarded_delete_candidate,
    _locate_fresh_patch_span_by_coordinates,
    _normalize_candidate_format_before_validation,
    _apply_validated_fresh_patches,
)


SECTION_ID = "CH-013-01"
TITLE = "实施安排"
BAD_SENTENCE = "我方将额外提交未经要求的进度报告。"


def _draft(content: str, *, placeholder_ids: list[str] | None = None) -> dict:
    return {
        "chapter_title": "测试章节",
        "sections": [
            {
                "section_id": SECTION_ID,
                "heading": TITLE,
                "block_type": "implementation_plan",
                "content": content,
                "covered_requirement_ids": [],
                "covered_score_item_ids": [],
                "placeholder_ids": list(placeholder_ids or []),
            }
        ],
        "claims": [],
        "placeholders_used": list(placeholder_ids or []),
        "requirements_covered": [],
        "score_items_covered": [],
        "warnings": [],
    }


def _permissions(placeholders: list[str] | None = None) -> dict:
    return {
        "allowed_confirmed_facts": [],
        "allowed_tender_facts": [],
        "effective_tender_facts": [],
        "allowed_placeholders": [
            {"placeholder_id": value} for value in placeholders or []
        ],
        "forbid_unlisted_numeric_commitments": True,
        "forbid_unlisted_personnel_qualifications": True,
        "forbid_unlisted_product_capabilities": True,
    }


def _card(content: str, *, sentence: str = BAD_SENTENCE) -> dict:
    result = _build_fresh_repair_task_card(
        {
            "section_id": SECTION_ID,
            "section_title": TITLE,
            "issue_code": "unconfirmed_bidder_commitment",
            "offending_sentence": sentence,
            "offending_span": "额外提交未经要求的进度报告",
        },
        section_content=content,
        section_permissions=_permissions(),
        section=_draft(content)["sections"][0],
    )
    assert result is not None
    return result


def _snapshot(draft: dict, *, blocked: bool) -> dict:
    errors = (
        [f"fact_boundary:{TITLE}:unconfirmed_bidder_commitment:{BAD_SENTENCE}"]
        if blocked
        else []
    )
    return {
        "errors": errors,
        "raw_error_count": len(errors),
        "grouped_error_count": len(errors),
        "blocking_error_categories": (
            [["unconfirmed_bidder_commitment", "bidder_commitment"]]
            if blocked
            else []
        ),
        "error_details": (
            [
                {
                    "section_id": SECTION_ID,
                    "section_title": TITLE,
                    "issue_code": "unconfirmed_bidder_commitment",
                    "error_code": "unconfirmed_bidder_commitment",
                    "offending_sentence": BAD_SENTENCE,
                    "offending_span": "额外提交未经要求的进度报告",
                }
            ]
            if blocked
            else []
        ),
        "schema_valid": True,
        "coverage": {
            "missing_response_point_count": 0,
            "missing_score_item_count": 0,
            "covered_response_point_count": 0,
            "covered_score_item_count": 0,
        },
        "quality_comparison": {
            "insufficient_section_count": 0,
            "list_dominant_section_count": 0,
            "inline_step_chain_count": 0,
            "markdown_table_error_count": 0,
        },
    }


def test_same_section_duplicate_span_uses_exact_sentence_coordinates():
    content = "合同签订后30日内完成到货。测试工作不承诺30日内完成。"
    locations = _fresh_sentence_locations(content)
    second = locations[1]
    sentence = second["sentence"]
    span_start = sentence.index("30日内")
    target = {
        "sentence_index": 1,
        "sentence_sha256": second["sentence_sha256"],
        "span_start": span_start,
        "span_end": span_start + len("30日内"),
        "span_text": "30日内",
    }

    anchor, error = _locate_fresh_patch_span_by_coordinates(content, target)

    assert error is None
    assert anchor is not None
    assert content[anchor[0] : anchor[1]] == "30日内"
    assert anchor[0] > content.index("30日内")


def test_sentence_hash_change_rejects_exact_span_locator():
    content = "合同签订后30日内完成到货。"
    location = _fresh_sentence_locations(content)[0]
    target = {
        "sentence_index": 0,
        "sentence_sha256": location["sentence_sha256"],
        "span_start": 5,
        "span_end": 9,
        "span_text": "30日内",
    }

    _, error = _locate_fresh_patch_span_by_coordinates(
        "合同签订后 30日内完成到货。", target
    )

    assert error == "fresh_patch_target_locator_mismatch"


def test_issue_not_improved_reports_post_patch_findings():
    content = (
        "项目实施过程按照确认后的计划开展跟踪和偏差分析。"
        + BAD_SENTENCE
        + "发现问题后进行记录和内部复核，保持过程可追溯。"
    )
    initial = _draft(content)
    card = _card(content)
    context = {
        "repair_task_cards": [card],
        "expected_sections": [
            {"section_id": SECTION_ID, "section_title": TITLE, "expected_order": 0}
        ],
        "preserved_placeholder_ids": [],
    }
    payload = {
        "base_candidate_hash": _candidate_sha256(initial),
        "repair_results": [
            {
                "repair_target_id": card["repair_target_id"],
                "section_id": SECTION_ID,
                "candidates": [
                    {
                        "candidate_id": "C1",
                        "candidate_role": "primary",
                        "operation_type": "replace_sentence",
                        "replacement_text": "项目实施期间开展内部进度跟踪。",
                    }
                ],
            }
        ],
    }

    def validator(value: dict) -> dict:
        # Deliberately retain the same finding to exercise rejection diagnostics.
        return _snapshot(value, blocked=True)

    _, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=payload,
        repair_context=context,
        fact_ledger=[
            {
                "section_id": SECTION_ID,
                "section_title": TITLE,
                "generation_permissions": _permissions(),
            }
        ],
        initial_validation=_snapshot(initial, blocked=True),
        validate_candidate=validator,
    )
    attempt = report["repair_target_groups"][0]["candidate_attempts"][0]

    assert "fresh_patch_issue_not_improved" in attempt["reason_codes"]
    assert attempt["before_matching_issue_count"] == 1
    assert attempt["after_matching_issue_count"] == 1
    assert attempt["post_patch_findings"][0]["issue_code"] == (
        "unconfirmed_bidder_commitment"
    )


def test_pure_unsupported_sentence_can_get_local_guarded_delete_candidate():
    card = _card(BAD_SENTENCE)

    candidate = _local_guarded_delete_candidate(card, model_candidates=[])

    assert candidate == {
        "candidate_id": "LOCAL-DELETE",
        "candidate_role": "delete",
        "candidate_source": "local_guarded_delete",
        "operation_type": "delete_sentence",
        "replacement_text": "",
    }


def test_local_delete_is_not_generated_for_preserve_coverage_or_placeholder_risk():
    base = _card(BAD_SENTENCE)
    preserve = deepcopy(base)
    preserve["explicit_must_preserve"] = ["必须保留"]
    coverage = deepcopy(base)
    coverage["target_carries_coverage"] = True
    placeholder = deepcopy(base)
    placeholder["target_contains_registered_placeholder"] = True

    assert _local_guarded_delete_candidate(preserve, model_candidates=[]) is None
    assert _local_guarded_delete_candidate(coverage, model_candidates=[]) is None
    assert _local_guarded_delete_candidate(placeholder, model_candidates=[]) is None


def test_local_guarded_delete_uses_normal_candidate_validation_before_apply():
    content = (
        "项目实施过程首先核对输入条件和工作边界，形成清晰的执行依据。\n\n"
        + BAD_SENTENCE
        + "\n\n发现偏差后开展内部分析并记录处理过程，保持过程可追溯。"
    )
    initial = _draft(content)
    card = _card(content)
    context = {
        "repair_task_cards": [card],
        "expected_sections": [
            {"section_id": SECTION_ID, "section_title": TITLE, "expected_order": 0}
        ],
        "preserved_placeholder_ids": [],
    }
    payload = {
        "base_candidate_hash": _candidate_sha256(initial),
        "repair_results": [
            {
                "repair_target_id": card["repair_target_id"],
                "section_id": SECTION_ID,
                "candidates": [],
            }
        ],
    }

    def validator(value: dict) -> dict:
        blocked = BAD_SENTENCE in value["sections"][0]["content"]
        return _snapshot(value, blocked=blocked)

    merged, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=payload,
        repair_context=context,
        fact_ledger=[
            {
                "section_id": SECTION_ID,
                "section_title": TITLE,
                "generation_permissions": _permissions(),
            }
        ],
        initial_validation=_snapshot(initial, blocked=True),
        validate_candidate=validator,
    )

    assert BAD_SENTENCE not in merged["sections"][0]["content"]
    assert report["local_guarded_delete_candidate_count"] == 1
    assert report["applied_operations"][0]["candidate_source"] == (
        "local_guarded_delete"
    )
    assert report["local_guarded_delete_applied_count"] == 1


def test_canonical_title_is_normalized_only_for_unique_section_id():
    value = _draft("正文内容。")
    value["sections"][0]["heading"] = "错误标题"
    target = {
        "chapter_id": "CH-013",
        "title": "测试章节",
        "children": [{"chapter_id": SECTION_ID, "title": TITLE}],
    }

    normalized, report = _normalize_candidate_format_before_validation(
        value, target=target, tender_tables=[]
    )

    assert normalized["sections"][0]["heading"] == TITLE
    assert report["canonical_title_normalization_count"] == 1


def test_ambiguous_section_identity_does_not_normalize_title():
    value = _draft("正文内容。")
    value["sections"][0]["heading"] = "错误标题"
    value["sections"].append(deepcopy(value["sections"][0]))
    target = {
        "chapter_id": "CH-013",
        "children": [{"chapter_id": SECTION_ID, "title": TITLE}],
    }

    normalized, report = _normalize_candidate_format_before_validation(
        value, target=target, tender_tables=[]
    )

    assert all(item["heading"] == "错误标题" for item in normalized["sections"])
    assert report["canonical_title_normalization_count"] == 0


def test_generated_plain_table_receives_neutral_intro():
    value = _draft("| 项目 | 说明 |\n| --- | --- |\n| A | B |")
    value["generated_supporting_tables"] = [
        {
            "section_id": SECTION_ID,
            "title": "关键事项表",
            "purpose": "辅助说明",
            "why_table_is_better_than_prose": "字段对应",
        }
    ]

    normalized, report = _normalize_candidate_format_before_validation(
        value,
        target={"children": [{"chapter_id": SECTION_ID, "title": TITLE}]},
        tender_tables=[],
    )

    assert normalized["sections"][0]["content"].startswith(
        "为便于说明本节相关内容，现将关键事项整理如下表。"
    )
    assert report["table_intro_normalization_count"] == 1


def test_tender_original_table_does_not_receive_generated_intro():
    value = _draft("| 项目 | 说明 |\n| --- | --- |\n| A | B |")
    value["generated_supporting_tables"] = [
        {
            "section_id": SECTION_ID,
            "title": "关键事项表",
            "purpose": "辅助说明",
            "why_table_is_better_than_prose": "字段对应",
        }
    ]
    normalized, report = _normalize_candidate_format_before_validation(
        value,
        target={"children": [{"chapter_id": SECTION_ID, "title": TITLE}]},
        tender_tables=[
            {
                "headers": ["项目", "说明"],
                "layout_complexity": "complex",
                "table_type": "required_response_table",
            }
        ],
    )

    assert "为便于说明" not in normalized["sections"][0]["content"]
    assert report["table_intro_normalization_count"] == 0

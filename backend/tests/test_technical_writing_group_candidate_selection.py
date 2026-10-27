from __future__ import annotations

from app.technical_writing_agent import (
    _apply_validated_fresh_patches,
    _build_fresh_repair_context,
)
from tests import test_technical_writing_eleventh_run_repairs as prior
from tests import test_technical_writing_structured_patch as base


def _multi_validator(value: dict) -> dict:
    result = base.snapshot(value)
    text = value["sections"][0]["content"]
    if base.BAD_A in text:
        result["raw_error_count"] = 2
        result["blocking_error_categories"] = [
            ("schedule_frequency", "schedule_frequency"),
            ("unconfirmed_bidder_commitment", "unconfirmed_bidder_commitment"),
        ]
    elif "阶段偏差分析" in text:
        result["raw_error_count"] = 1
        result["blocking_error_categories"] = [
            ("unconfirmed_bidder_commitment", "unconfirmed_bidder_commitment")
        ]
    else:
        result["raw_error_count"] = 0
        result["blocking_error_categories"] = []
    return result


def _multi_candidates(context: dict) -> list[dict]:
    return [
        base.candidate(
            context,
            "项目实施过程按照确认后的计划开展进度跟踪和阶段偏差分析。",
            candidate_id="C1",
        ),
        base.candidate(
            context,
            "项目进度按照合同约定及确认后的实施计划进行控制。",
            candidate_id="C2",
        ),
    ]


def test_multi_issue_group_evaluates_all_candidates():
    context = prior._same_sentence_context()
    _, report = prior._apply_group(
        context, _multi_candidates(context), validator=_multi_validator
    )
    attempts = report["repair_target_groups"][0]["candidate_attempts"]
    assert len(attempts) == 2
    assert all(
        item["status"] != "not_evaluated_after_prior_candidate_applied"
        for item in attempts
    )


def test_multi_issue_group_selects_c2_when_c1_partial_and_c2_complete():
    context = prior._same_sentence_context()
    merged, report = prior._apply_group(
        context, _multi_candidates(context), validator=_multi_validator
    )
    group = report["repair_target_groups"][0]
    assert group["selected_candidate_id"] == "C2"
    assert group["selection_reason"] == "max_member_issue_resolution"
    assert group["status"] == "resolved"
    assert "合同约定" in merged["sections"][0]["content"]
    attempts = {item["candidate_id"]: item for item in group["candidate_attempts"]}
    assert attempts["C1"]["resolved_member_issue_count"] == 1
    assert attempts["C1"]["remaining_member_issue_count"] == 1
    assert attempts["C1"]["selection_rank"] == 2
    assert attempts["C2"]["resolved_member_issue_count"] == 2
    assert attempts["C2"]["remaining_member_issue_count"] == 0
    assert attempts["C2"]["selection_rank"] == 1


def test_partial_resolution_marks_member_issues():
    context = prior._same_sentence_context()
    _, report = prior._apply_group(
        context, _multi_candidates(context)[:1], validator=_multi_validator
    )
    group = report["repair_target_groups"][0]
    assert group["status"] == "partially_resolved"
    assert group["selection_reason"] == "max_member_issue_resolution"
    assert {item["status"] for item in report["issue_results"]} == {
        "resolved_by_shared_operation",
        "remaining_after_shared_operation",
    }


def test_remaining_member_issue_reaches_merged_full_audit():
    context = prior._same_sentence_context()
    merged, report = prior._apply_group(
        context, _multi_candidates(context)[:1], validator=_multi_validator
    )
    full_validation = _multi_validator(merged)
    assert report["unresolved_repair_task_count"] == 1
    assert len(report["unresolved_issue_fingerprints"]) == 1
    assert full_validation["raw_error_count"] == 1
    assert full_validation["blocking_error_categories"] == [
        ("unconfirmed_bidder_commitment", "unconfirmed_bidder_commitment")
    ]


def test_single_issue_group_stops_after_first_qualified_candidate():
    context = base.repair_context()
    calls: list[str] = []

    def validator(value: dict) -> dict:
        calls.append(value["sections"][0]["content"])
        return base.snapshot(value)

    _, report = prior._apply_group(
        context,
        [
            base.candidate(context, base.SAFE_A, candidate_id="C1"),
            base.candidate(
                context,
                "项目进度按照合同约定及确认后的实施计划进行控制。",
                candidate_id="C2",
            ),
        ],
        validator=validator,
    )
    attempts = report["issue_results"][0]["candidate_attempts"]
    assert attempts[0]["status"] == "applied"
    assert attempts[1]["status"] == "not_evaluated_after_prior_candidate_applied"
    assert len(calls) == 2  # initial snapshot plus only C1 candidate validation


def test_section_reduction_within_fifteen_percent_is_allowed():
    context = prior._quality_context(trailing=prior.LONG_PARAGRAPH)
    replacement = (
        "项目实施期间按照确认后的计划开展进度跟踪，对阶段工作、偏差情况和处理安排"
        "进行持续分析。"
    )
    _, report = prior._apply_group(
        context, [base.candidate(context, replacement)]
    )
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert report["applied_operation_count"] == 1
    assert -0.15 <= attempt["quality_audit"]["change_ratio"] <= 0.20
    assert attempt["quality_audit"]["length_warning_code"] is None


def test_section_reduction_fifteen_to_thirty_percent_is_warning_only():
    context = prior._quality_context(trailing=prior.LONG_PARAGRAPH)
    replacement = "项目实施过程按照确认后的计划开展进度跟踪和阶段偏差分析。"
    _, report = prior._apply_group(
        context, [base.candidate(context, replacement)]
    )
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert report["applied_operation_count"] == 1
    assert -0.30 <= attempt["quality_audit"]["change_ratio"] < -0.15
    assert attempt["quality_audit"]["length_warning_code"] == (
        "fresh_patch_prose_reduction_review"
    )
    assert "fresh_patch_prose_reduction_review" in attempt["warning_codes"]


def test_absolute_change_under_three_hundred_is_not_rejected_for_length():
    context = prior._quality_context(trailing=prior.LONG_PARAGRAPH)
    replacement = "项目实施过程按照确认后的计划开展进度跟踪和阶段偏差分析。"
    _, report = prior._apply_group(
        context, [base.candidate(context, replacement)]
    )
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert abs(attempt["quality_audit"]["absolute_change"]) < 300
    assert "fresh_patch_prose_density_regressed" not in attempt["reason_codes"]
    assert attempt["status"] == "applied"


def test_new_insufficient_prose_still_rejected():
    prior.test_new_insufficient_prose_is_rejected()


def test_coverage_regression_still_rejected():
    prior.test_coverage_regression_is_still_rejected()


def test_placeholder_loss_still_rejected_and_reported():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    sentence = f"每周由{{{{{placeholder_id}}}}}向采购人提交进度报告。"
    initial = base.draft(sentence)
    initial["sections"][0]["placeholder_ids"] = [placeholder_id]
    initial["placeholders_used"] = [placeholder_id]
    local_ledger = base.ledger()
    local_ledger[0]["generation_permissions"]["allowed_placeholders"] = [
        {
            "placeholder_id": placeholder_id,
            "placeholder_token": f"{{{{{placeholder_id}}}}}",
        }
    ]
    message = f"fact_boundary:实施计划:schedule_frequency:{sentence}"
    context = _build_fresh_repair_context(
        validation={
            "errors": [message],
            "issues": [{
                "severity": "blocking", "category": "fact_boundary",
                "code": "schedule_frequency", "section_id": base.SECTION_A,
                "section_title": "实施计划", "message": message,
            }],
        },
        draft=initial,
        fact_ledger=local_ledger,
        pending_confirmations=[],
        target=base.target(),
    )
    patch = {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": [{
            "repair_target_id": context["repair_task_cards"][0]["repair_target_id"],
            "section_id": base.SECTION_A,
            "candidates": [base.candidate(context, base.SAFE_A)],
        }],
    }
    _, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=patch,
        repair_context=context,
        fact_ledger=local_ledger,
        initial_validation=base.snapshot(initial),
        validate_candidate=base.snapshot,
    )
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert "fresh_patch_registered_placeholder_removed" in attempt["reason_codes"]
    assert attempt["quality_audit"]["placeholder_lost"] is True


def test_registered_but_initially_missing_placeholder_does_not_block_other_repair():
    initial = base.draft(base.BAD_A)
    local_ledger = base.ledger()
    local_ledger[0]["generation_permissions"]["allowed_placeholders"] = [
        {
            "placeholder_id": "PH-FIELD-PENDING",
            "placeholder_token": "{{PH-FIELD-PENDING}}",
        }
    ]
    context = _build_fresh_repair_context(
        validation=base.validation_with_duplicate_issue(),
        draft=initial,
        fact_ledger=local_ledger,
        pending_confirmations=[],
        target=base.target(),
    )
    patch = {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": [
            {
                "repair_target_id": context["repair_task_cards"][0][
                    "repair_target_id"
                ],
                "section_id": base.SECTION_A,
                "candidates": [base.candidate(context, base.SAFE_A)],
            }
        ],
    }

    _, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=patch,
        repair_context=context,
        fact_ledger=local_ledger,
        initial_validation=base.snapshot(initial),
        validate_candidate=base.snapshot,
    )

    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert "fresh_patch_registered_placeholder_removed" not in attempt["reason_codes"]
    assert report["applied_operation_count"] == 1

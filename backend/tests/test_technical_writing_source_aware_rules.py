from __future__ import annotations

from app.technical_writing_quality import (
    _audit_tender_obligation_spans,
    calculate_confirmed_work_years,
    classify_deviation_status,
    count_confirmed_similar_projects,
    inspect_deviation_issues,
    inspect_unconfirmed_commitments,
    tender_response_policy,
)


def prose() -> str:
    return (
        "本节说明实施目标、输入条件、执行方法和责任边界，并通过过程记录保持可追溯。"
        "项目组按照确认后的方案开展检查、复核和问题闭环，形成可核验的交付成果。"
    )


def draft(statement: str = "") -> dict:
    content = prose() + ("\n\n" + statement if statement else "")
    return {
        "chapter_title": "技术方案",
        "sections": [
            {
                "heading": "项目负责人及主要人员",
                "block_type": "organization_plan",
                "content": content,
                "covered_score_item_ids": [],
                "covered_requirement_ids": [],
                "placeholder_ids": [],
            }
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }


def tender_claim(text: str, source_id: str = "TR-1") -> dict:
    return {
        "text": text,
        "claim_type": "tender_fact",
        "source_ids": [source_id],
        "evidence_ids": [],
        "human_review_required": False,
    }


def deviation_claim(
    *,
    text: str,
    baseline: str,
    offered: str,
    direction: str,
    status: str | None = None,
    response_intent: str | None = None,
) -> dict:
    result = {
        "text": text,
        "claim_type": "commitment_statement",
        "source_ids": ["TR-BASELINE"],
        "evidence_ids": [],
        "human_review_required": status
        != "positive_deviation_confirmed",
        "tender_baseline": baseline,
        "offered_value": offered,
        "comparison_direction": direction,
    }
    if status:
        result["deviation_status"] = status
    if response_intent:
        result["response_intent"] = response_intent
    return result


def test_confirmed_personnel_work_years_are_allowed():
    value = draft("拟派项目经理具有5年项目管理经验。")
    confirmed = {
        "project_manager": {
            "management_years": 5,
            "source_id": "CONF-PERSON-1",
        }
    }
    assert inspect_unconfirmed_commitments(
        value, confirmed_facts=confirmed
    ) == []


def test_confirmed_personnel_certificate_is_allowed():
    sentence = "张三持有PMP证书，证书有效期至2028年。"
    value = draft(sentence)
    value["claims"] = [
        {
            "text": sentence,
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["CONF-CERT-1"],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        confirmed_evidence_ids={"CONF-CERT-1"},
    ) == []


def test_confirmed_similar_project_count_is_allowed():
    value = draft("拟派项目经理参与过3个经确认的同类项目。")
    confirmed = {
        "project_manager": {
            "similar_project_count": 3,
            "source_id": "CONF-RESUME-1",
        }
    }
    assert inspect_unconfirmed_commitments(
        value, confirmed_facts=confirmed
    ) == []


def test_candidate_personnel_fact_uses_placeholder():
    value = draft(
        "拟派项目经理具有{{PH-PM-MANAGEMENT-YEARS}}年项目管理经验，"
        "参与过{{PH-PM-SIMILAR-PROJECT-COUNT}}个经确认的同类项目，"
        "相关证书信息见{{PH-PM-CERTIFICATE-REFERENCE}}。"
    )
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_unselected_resume_does_not_become_confirmed_fact():
    sentence = "张三持有PMP证书并具有5年项目管理经验。"
    value = draft(sentence)
    value["claims"] = [
        {
            "text": sentence,
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["KB-CANDIDATE-RESUME"],
            "human_review_required": True,
        }
    ]
    errors = inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        candidate_evidence_ids={"KB-CANDIDATE-RESUME"},
    )
    assert any("candidate_promoted_to_fact" in item for item in errors)


def test_personnel_years_can_be_deterministically_calculated_from_confirmed_dates():
    result = calculate_confirmed_work_years(
        start_date="2018-03-15",
        end_date="2024-03-14",
        calculation_date="2024-03-14",
        source_id="CONF-RESUME-DATES",
    )
    assert result["work_years"] == 5
    assert result["calculation_basis"] == "completed_calendar_years"
    assert result["start_date"] == "2018-03-15"
    assert result["end_date"] == "2024-03-14"
    assert result["calculation_date"] == "2024-03-14"
    assert result["source_id"] == "CONF-RESUME-DATES"


def test_similar_project_count_only_counts_confirmed_matching_projects():
    result = count_confirmed_similar_projects(
        [
            {
                "project_name": "已确认虚拟化项目",
                "project_content": "虚拟化平台建设",
                "status": "confirmed",
                "is_similar_project": True,
                "source_id": "CONF-P-1",
            },
            {
                "project_name": "普通信息化项目",
                "project_content": "办公系统",
                "status": "confirmed",
                "is_similar_project": False,
                "source_id": "CONF-P-2",
            },
            {
                "project_name": "候选超融合项目",
                "project_content": "超融合建设",
                "status": "candidate",
                "is_similar_project": True,
                "source_id": "KB-P-3",
            },
        ]
    )
    assert result["similar_project_count"] == 1
    assert result["project_names"] == ["已确认虚拟化项目"]
    assert result["source_ids"] == ["CONF-P-1"]


def _assert_unrequired_numeric_is_blocked(statement: str) -> None:
    errors = inspect_unconfirmed_commitments(
        draft(statement),
        confirmed_facts={},
    )
    assert any("numeric_commitment" in item for item in errors)


def test_unrequired_rectification_deadline_is_not_generated():
    _assert_unrequired_numeric_is_blocked(
        "重大偏差在三个工作日内形成整改方案。"
    )


def test_unrequired_notice_period_is_not_generated():
    _assert_unrequired_numeric_is_blocked("项目组提前十个工作日发出通知。")


def test_unrequired_trial_run_duration_is_not_generated():
    _assert_unrequired_numeric_is_blocked("系统连续试运行七天后进入验收。")


def test_unrequired_response_hours_are_not_generated():
    _assert_unrequired_numeric_is_blocked("一般问题在4小时内解决。")


def test_generic_cross_review_does_not_assume_staff_count():
    generic = draft("关键配置完成后由项目组进行交叉复核。")
    assert inspect_unconfirmed_commitments(generic, confirmed_facts={}) == []
    inferred = draft("关键配置由另一名工程师进行复核。")
    assert any(
        "unconfirmed_staffing_assignment" in item
        for item in inspect_unconfirmed_commitments(
            inferred, confirmed_facts={}
        )
    )


def test_tender_delivery_period_defaults_to_no_deviation():
    policy = tender_response_policy(
        "货到时间为自合同签订之日起30日内。",
        source_id="TR-DELIVERY",
    )
    assert policy["response_intent"] == "no_deviation"
    assert policy["deviation_status"] == "no_deviation"
    assert policy["evidence_status"] == "confirmed"


def test_tender_installation_period_defaults_to_no_deviation():
    assert tender_response_policy(
        "安装应在货到之日起10日内完成。",
        source_id="TR-INSTALL",
    )["default_no_deviation"] is True


def test_tender_warranty_period_defaults_to_no_deviation():
    assert tender_response_policy(
        "免费保修期不少于5年。",
        source_id="TR-WARRANTY",
    )["deviation_status"] == "no_deviation"


def test_tender_service_level_defaults_to_no_deviation():
    assert tender_response_policy(
        "投标人应提供7×24小时技术响应，24小时内到达现场。",
        source_id="TR-SERVICE",
    )["default_no_deviation"] is True


def test_exact_tender_value_does_not_require_placeholder():
    policy = tender_response_policy(
        "交货期为合同签订后30日内。",
        source_id="TR-30D",
    )
    assert policy["response_intent"] == "no_deviation"
    assert policy["evidence_status"] == "confirmed"


def test_exact_tender_value_is_not_numeric_commitment_error():
    sentence = (
        "我方交货期为合同签订之日起30日内，完全响应招标要求，无偏离。"
    )
    value = draft(sentence)
    value["claims"] = [
        tender_claim("招标要求交货期为合同签订之日起30日内。", "TR-30D")
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_tender_baseline_narration_is_not_a_numeric_bidder_commitment():
    sentence = "招标文件要求合同签订后1个月内完成，本文仅复述该采购基准。"
    value = draft(sentence)
    value["claims"] = [
        tender_claim("招标文件要求合同签订后1个月内完成。", "TR-DELIVERY")
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_neutral_installation_testing_acceptance_methodology_is_not_bidder_commitment():
    for sentence in (
        "实施流程包括授权核验、部署、测试和验收。",
        "项目实施过程可形成部署记录和测试记录。",
        "具体安排按照合同约定及采购人确认计划执行。",
    ):
        assert _audit_tender_obligation_spans(
            sentence,
            claims=[],
            permissions={"effective_tender_facts": []},
        )["blocked_spans"] == []


def test_explicit_bidder_commitment_remains_a_boundary_error():
    errors = inspect_unconfirmed_commitments(
        draft("我方保证3日内完成安装。"),
        confirmed_facts={},
    )
    assert any("numeric_commitment" in item or "unconfirmed_bidder_commitment" in item for item in errors)


def test_exact_tender_delivery_value_with_bidder_response_wording_is_allowed():
    value = draft(
        "根据招标要求，货到时间为自签订合同之日起30日内，"
        "我方在该期限内完成交货。"
    )
    value["claims"] = [
        tender_claim("货到时间为自签订合同之日起30日内。", "TR-DELIVERY")
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_exact_tender_installation_value_with_bidder_response_wording_is_allowed():
    value = draft("我方在合同约定的10日完成时限内开展安装工作。")
    value["claims"] = [
        tender_claim("安装完成时限为货到之日起10日内。", "TR-INSTALL")
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_delivery_and_installation_values_can_appear_in_same_sentence():
    value = draft(
        "招标要求货到时间为自签订合同之日起30日内，"
        "安装完成时限为货到之日起10日内。"
    )
    value["claims"] = [
        tender_claim("货到时间为自签订合同之日起30日内。", "TR-DELIVERY"),
        tender_claim("安装完成时限为货到之日起10日内。", "TR-INSTALL"),
    ]
    assert inspect_unconfirmed_commitments(value, confirmed_facts={}) == []


def test_tender_numeric_value_cannot_be_reused_for_different_semantics():
    value = draft("重大偏差在24小时内提交整改报告。")
    value["claims"] = [
        tender_claim("招标要求维修工程师24小时内到达现场。", "TR-ONSITE")
    ]
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any("numeric_commitment" in item for item in errors)


def test_better_or_worse_value_still_requires_deviation_handling():
    for delivery_days in (25, 35):
        value = draft(f"我方货到时间为自签订合同之日起{delivery_days}日内。")
        value["claims"] = [
            tender_claim("货到时间为自签订合同之日起30日内。", "TR-DELIVERY")
        ]
        errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
        assert any("numeric_commitment" in item for item in errors)


def test_better_upper_limit_value_requires_positive_deviation_confirmation():
    assert classify_deviation_status(
        tender_baseline="30日内",
        offered_value="25日内",
        comparison_direction="upper_limit",
    ) == "positive_deviation_pending_confirmation"


def test_better_lower_limit_value_requires_positive_deviation_confirmation():
    assert classify_deviation_status(
        tender_baseline="至少5年",
        offered_value="6年",
        comparison_direction="lower_limit",
    ) == "positive_deviation_pending_confirmation"


def test_confirmed_positive_deviation_can_be_written():
    assert classify_deviation_status(
        tender_baseline="30日内",
        offered_value="25日内",
        comparison_direction="upper_limit",
        positive_confirmed=True,
    ) == "positive_deviation_confirmed"
    sentence = "经确认，我方将在25日内完成交货。"
    value = draft(sentence)
    value["claims"] = [
        deviation_claim(
            text=sentence,
            baseline="30日内",
            offered="25日内",
            direction="upper_limit",
            status="positive_deviation_confirmed",
        )
    ]
    assert inspect_deviation_issues(value) == []
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
    ) == []


def test_negative_delivery_deviation_is_blocking():
    value = draft()
    value["claims"] = [
        deviation_claim(
            text="确认交货期为35日。",
            baseline="30日内",
            offered="35日",
            direction="upper_limit",
            status="negative_deviation",
        )
    ]
    assert any(
        "negative_deviation" in item
        for item in inspect_deviation_issues(value)
    )


def test_negative_warranty_deviation_is_blocking():
    value = draft()
    value["claims"] = [
        deviation_claim(
            text="确认保修期为3年。",
            baseline="至少5年",
            offered="3年",
            direction="lower_limit",
            status="negative_deviation",
        )
    ]
    assert any(
        "negative_deviation" in item
        for item in inspect_deviation_issues(value)
    )


def test_negative_deviation_cannot_be_marked_no_deviation():
    value = draft()
    value["claims"] = [
        deviation_claim(
            text="交货期35日，无偏离。",
            baseline="30日内",
            offered="35日",
            direction="upper_limit",
            status="no_deviation",
            response_intent="no_deviation",
        )
    ]
    assert any(
        "negative_deviation_marked_no_deviation" in item
        for item in inspect_deviation_issues(value)
    )


def test_fixed_requirement_difference_requires_review():
    value = draft()
    value["claims"] = [
        deviation_claim(
            text="交货地点拟变更。",
            baseline="采购人指定地点",
            offered="供应商仓库",
            direction="fixed",
        )
    ]
    assert any(
        "fixed_requirement_difference_requires_review" in item
        for item in inspect_deviation_issues(value)
    )


def test_default_no_deviation_does_not_confirm_product_capability():
    policy = tender_response_policy(
        "所投产品应支持在线扩展。",
        source_id="TR-CAP",
    )
    assert policy["response_intent"] == "no_deviation"
    assert policy["evidence_status"] == "pending"
    assert policy["deviation_status"] == "evidence_pending"


def test_no_deviation_intent_is_separate_from_evidence_status():
    policy = tender_response_policy(
        "所投产品应提供多副本能力。",
        source_id="TR-MULTI",
    )
    assert policy["response_intent"] == "no_deviation"
    assert policy["evidence_status"] != "confirmed"


def test_missing_product_evidence_remains_review_issue():
    value = draft("本系统支持在线扩展。")
    assert any(
        "unconfirmed_product_capability" in item
        for item in inspect_unconfirmed_commitments(
            value, confirmed_facts={}
        )
    )


def test_confirmed_product_evidence_allows_definite_capability():
    sentence = "本系统支持在线扩展。"
    value = draft(sentence)
    value["claims"] = [
        {
            "text": sentence,
            "claim_type": "confirmed_enterprise_fact",
            "source_ids": [],
            "evidence_ids": ["CONF-PRODUCT-CAP"],
            "human_review_required": False,
        }
    ]
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        confirmed_evidence_ids={"CONF-PRODUCT-CAP"},
    ) == []

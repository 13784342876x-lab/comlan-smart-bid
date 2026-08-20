from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.chapter_draft_schema import load_chapter_draft_schema
from app.llm_client import ToolModelResult, empty_usage
from app.technical_placeholders import placeholder_id_for_field
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    _build_fresh_repair_context,
    _build_section_fact_ledger,
    _fresh_quality_error_details,
    _fresh_quality_regeneration_instruction,
    build_technical_writing_agent,
    classify_chapter_audit,
    validate_technical_chapter,
)
from app.technical_writing_quality import (
    audit_generation_permissions,
    inspect_unconfirmed_commitments,
)


SECTION_ID = "CH-FRESH-01"
SECTION_TITLE = "项目设计实施周期"


def target() -> dict:
    requirements = {
        "response_point_ids": ["TR-006", "TR-007"],
        "score_item_refs": ["SCORE-001"],
        "manual_project_facts": {"need": "none", "fact_types": []},
        "web": {"need": "none"},
    }
    return {
        "chapter_id": "CH-FRESH",
        "title": "Fresh返修测试",
        "writing_requirements": requirements,
        "children": [
            {
                "chapter_id": SECTION_ID,
                "title": SECTION_TITLE,
                "writing_requirements": requirements,
                "children": [],
            }
        ],
    }


def prose(extra: str = "") -> str:
    return (
        "本节以形成可执行的实施安排为目标，在招标要求、责任接口和输入资料完成核对后，"
        "项目组组织方案复核、现场实施和过程记录，使每项工作能够追踪到输入、动作与成果。"
        "\n\n实施过程中如发现条件变化或检查结果不满足要求，项目组及时登记问题、分析影响并"
        "组织处置，待复核结果满足完成判据后继续推进，最终形成完整记录和问题闭环资料。"
        + (f"\n\n{extra}" if extra else "")
    )


def chapter(
    extra: str = "",
    *,
    placeholder_ids: list[str] | None = None,
    claims: list[dict] | None = None,
    metadata: bool = False,
) -> dict:
    placeholder_ids = placeholder_ids or []
    value = {
        "chapter_title": "Fresh返修测试",
        "sections": [
            {
                "section_id": SECTION_ID,
                "heading": SECTION_TITLE,
                "block_type": "implementation_plan",
                "content": prose(extra),
                "covered_score_item_ids": ["SCORE-001"],
                "covered_requirement_ids": ["TR-006", "TR-007"],
                "placeholder_ids": placeholder_ids,
            }
        ],
        "claims": claims or [],
        "placeholders_used": placeholder_ids,
        "score_items_covered": ["SCORE-001"],
        "requirements_covered": ["TR-006", "TR-007"],
        "warnings": [],
    }
    if metadata:
        value["generated_supporting_tables"] = [
            {
                "section_id": SECTION_ID,
                "title": "实施里程碑节点表",
                "purpose": "核对实施节点",
                "why_table_is_better_than_prose": "节点与成果需要横向对应",
            }
        ]
    return value


def pending(
    placeholder_id: str,
    field: str,
    *,
    affected: str = SECTION_ID,
) -> dict:
    return {
        "field": field,
        "placeholder_id": placeholder_id,
        "placeholder_token": f"{{{{{placeholder_id}}}}}",
        "affected_sections": [affected],
        "blocking": True,
        "reason": f"{field}尚未确认",
    }


def fact_ledger(
    *,
    pending_items: list[dict] | None = None,
    confirmed_records: dict | None = None,
) -> list[dict]:
    records = (
        {
        "project_manager": {
            "status": "confirmed",
            "value": {"management_years": 5},
            "source_id": "CONF-PERSON-1",
        },
        "product_capability": {
            "status": "confirmed",
            "value": "本系统支持已确认的审计日志能力",
            "source_id": "CONF-CAP-1",
        },
        }
        if confirmed_records is None
        else confirmed_records
    )
    return _build_section_fact_ledger(
        original_context={
            "tender_requirements": {
                "requirements": [
                    {
                        "point_id": "TR-006",
                        "original_text": "货到时间为自签订合同之日起30日内。",
                    },
                    {
                        "point_id": "TR-007",
                        "original_text": "安装完成时间为货到之日起10日内。",
                    },
                ]
            },
            "confirmed_project_facts": {
                "facts": {
                    key: value["value"] for key, value in records.items()
                },
                "fact_records": records,
            },
            "draft_plan": {"draft_units": []},
            "task_information": {"tasks": []},
            "tender_tables": [],
        },
        target=target(),
        pending_confirmations=pending_items or [],
        local_evidence_refs=[],
    )


def issue(code: str, *, category: str = "fact_boundary", message: str = "x") -> dict:
    return {
        "severity": "warning" if code == "placeholder_pending_completion" else "blocking",
        "category": category,
        "code": code,
        "section_id": SECTION_ID,
        "section_title": SECTION_TITLE,
        "message": message,
    }


def disposition(code: str, *, category: str = "fact_boundary") -> str:
    audit = classify_chapter_audit({"issues": [issue(code, category=category)]})
    values = [
        item
        for item in [
            *audit["auto_repairable"],
            *audit["review_only"],
        ]
        if item["code"] == code
    ]
    assert values
    return values[0]["repair_disposition"]


def validation_with(
    errors: list[str],
    issues: list[dict],
) -> dict:
    return {
        "errors": errors,
        "warnings": [],
        "issues": issues,
        "issue_counts": {
            "fatal": 0,
            "blocking": sum(item["severity"] == "blocking" for item in issues),
            "warning": sum(item["severity"] == "warning" for item in issues),
        },
    }


def numeric_repair_context() -> dict:
    sentence = (
        "货到时间为自签订合同之日起30日内，安装完成时间为货到之日起10日内；"
        "规划设计压缩在前5日内完成。"
    )
    error = f"fact_boundary:{SECTION_TITLE}:numeric_commitment:{sentence}"
    return _build_fresh_repair_context(
        validation=validation_with(
            [error],
            [issue("numeric_commitment", message=error)],
        ),
        draft=chapter(sentence),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )


def test_numeric_commitment_is_auto_repairable():
    assert disposition("numeric_commitment") == "auto_repairable"


def test_unconfirmed_personnel_qualification_is_auto_repairable():
    assert disposition("unconfirmed_personnel_qualification") == "auto_repairable"


def test_unconfirmed_product_capability_is_auto_repairable():
    assert disposition("unconfirmed_product_capability") == "auto_repairable"


def test_candidate_promoted_to_fact_is_auto_repairable():
    assert disposition("candidate_promoted_to_fact") == "auto_repairable"


def test_unverified_standard_reference_is_auto_repairable():
    assert disposition("unverified_standard_reference") == "auto_repairable"


def test_placeholder_pending_completion_is_review_only():
    assert disposition("placeholder_pending_completion", category="placeholder") == "review_only"


def test_evidence_pending_is_review_only():
    assert disposition("evidence_pending") == "review_only"


def test_positive_deviation_pending_is_review_only():
    assert disposition("positive_deviation_pending_confirmation") == "review_only"


def test_missing_product_evidence_is_review_only():
    assert disposition("required_proof_missing", category="source_verification") == "review_only"


def test_existing_unfilled_placeholder_is_review_only():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    audit = classify_chapter_audit(
        {"issues": [issue("placeholder_pending_completion", category="placeholder")]},
        pending_confirmations=[pending(placeholder_id, "项目经理")],
    )
    assert all(
        item["repair_disposition"] == "review_only"
        for item in audit["review_only"]
    )


def test_required_registered_placeholder_missing_is_auto_repairable():
    assert disposition(
        "required_registered_placeholder_missing",
        category="placeholder",
    ) == "auto_repairable"


def _validate_default_placeholder(placeholder_id: str, field: str) -> dict:
    pending_item = pending(placeholder_id, field)
    return validate_technical_chapter(
        chapter(),
        schema=load_chapter_draft_schema(),
        target=target(),
        confirmed_facts={},
        pending_confirmations=[pending_item],
        tender_tables=[],
        generation_permissions=fact_ledger(pending_items=[pending_item]),
    )


def test_default_no_deviation_delivery_does_not_require_delivery_placeholder():
    validation = _validate_default_placeholder(
        "PH-FIELD-DELIVERY-COMMITMENT",
        "最终交货承诺",
    )
    assert "PH-FIELD-DELIVERY-COMMITMENT" in validation["ignored_pending_confirmation_ids"]
    assert not any(
        item["code"] == "required_registered_placeholder_missing"
        for item in validation["issues"]
    )


def test_default_no_deviation_installation_does_not_require_installation_placeholder():
    validation = _validate_default_placeholder(
        "PH-FIELD-INSTALLATION-PERIOD",
        "最终安装周期",
    )
    assert "PH-FIELD-INSTALLATION-PERIOD" in validation["ignored_pending_confirmation_ids"]
    assert not any(
        item["code"] == "required_registered_placeholder_missing"
        for item in validation["issues"]
    )


def test_fresh_repair_item_contains_exact_offending_sentence():
    item = numeric_repair_context()["auto_repair_items"][0]
    assert item["offending_sentence"] == (
        "货到时间为自签订合同之日起30日内，安装完成时间为货到之日起10日内；"
        "规划设计压缩在前5日内完成。"
    )


def test_fresh_repair_item_contains_exact_offending_span():
    assert numeric_repair_context()["auto_repair_items"][0]["offending_span"] == "前5日内"


def test_section_wide_numeric_keywords_are_not_used_as_repair_targets():
    item = numeric_repair_context()["auto_repair_items"][0]
    assert "30日" not in item["keywords"]
    assert "10日" not in item["keywords"]
    assert "5日" in item["keywords"] or "前5日内" in item["offending_span"]


def test_unconfirmed_five_day_value_does_not_include_tender_thirty_day_value():
    item = numeric_repair_context()["auto_repair_items"][0]
    assert item["offending_span"] == "前5日内"
    assert "30日" not in item["must_not_assert"]
    assert any("30日" in fact["text"] for fact in item["must_preserve_facts"])


def test_repair_item_contains_required_action():
    assert "删除未经确认" in numeric_repair_context()["auto_repair_items"][0]["required_action"]


def preserve_context() -> dict:
    person_id = "PH-FIELD-PROJECT-MANAGER"
    team_id = "PH-FIELD-PROJECT-TEAM-MEMBERS"
    validation = validation_with(
        [f"fact_boundary:{SECTION_TITLE}:numeric_commitment:前5日内完成"],
        [
            issue(
                "numeric_commitment",
                message=f"fact_boundary:{SECTION_TITLE}:numeric_commitment:前5日内完成",
            )
        ],
    )
    pending_items = [pending(person_id, "项目经理"), pending(team_id, "团队成员")]
    return _build_fresh_repair_context(
        validation=validation,
        draft=chapter("前5日内完成。", metadata=True),
        fact_ledger=fact_ledger(pending_items=pending_items),
        pending_confirmations=pending_items,
        target=target(),
    )


def test_fresh_preserves_tender_delivery_period():
    assert any("30日" in item["text"] for item in preserve_context()["must_preserve"]["confirmed_tender_facts"])


def test_fresh_preserves_tender_installation_period():
    assert any("10日" in item["text"] for item in preserve_context()["must_preserve"]["confirmed_tender_facts"])


def test_fresh_preserves_registered_person_placeholder():
    assert "PH-FIELD-PROJECT-MANAGER" in preserve_context()["preserved_placeholder_ids"]


def test_fresh_preserves_registered_team_placeholder():
    assert "PH-FIELD-PROJECT-TEAM-MEMBERS" in preserve_context()["preserved_placeholder_ids"]


def test_fresh_preserves_confirmed_personnel_fact():
    assert preserve_context()["must_preserve"]["confirmed_personnel_facts"]


def test_fresh_preserves_requirement_and_score_coverage():
    protected = preserve_context()["must_preserve"]
    assert protected["required_requirement_ids"] == ["TR-006", "TR-007"]
    assert protected["required_score_item_ids"] == ["SCORE-001"]
    assert protected["required_section_ids"] == [SECTION_ID]


def test_fresh_preserves_registered_table_title():
    assert preserve_context()["must_preserve"]["registered_table_titles"] == ["实施里程碑节点表"]


def review_context() -> dict:
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    placeholder_issue = issue(
        "placeholder_pending_completion",
        category="placeholder",
        message="registered placeholders remain pending completion",
    )
    evidence_issue = issue(
        "evidence_pending",
        message=f"fact_boundary:{SECTION_TITLE}:evidence_pending:产品证明材料缺失",
    )
    return _build_fresh_repair_context(
        validation=validation_with([], [placeholder_issue, evidence_issue]),
        draft=chapter(
            f"项目经理为{{{{{placeholder_id}}}}}。",
            placeholder_ids=[placeholder_id],
        ),
        fact_ledger=fact_ledger(
            pending_items=[pending(placeholder_id, "项目经理")]
        ),
        pending_confirmations=[pending(placeholder_id, "项目经理")],
        target=target(),
        local_evidence_refs=[
            {
                "candidate_id": "KB-CERT-1",
                "evidence_type": "person_certificate",
                "selection_status": "candidate",
                "excerpt": "候选PMP证书编号和有效期不得进入Fresh",
            }
        ],
    )


def test_review_only_issue_is_not_sent_as_repair_target():
    context = review_context()
    assert context["auto_repair_items"] == []
    assert {item["issue_code"] for item in context["review_only_guards"]} >= {
        "placeholder_pending_completion",
        "evidence_pending",
    }


def test_review_only_placeholder_is_sent_as_preservation_guard():
    context = review_context()
    assert "PH-FIELD-PROJECT-MANAGER" in context["preserved_placeholder_ids"]
    assert any(
        item.get("placeholder_id") == "PH-FIELD-PROJECT-MANAGER"
        for item in context["review_only_guards"]
    )


def test_missing_person_name_does_not_trigger_fact_invention():
    prompt = _fresh_quality_regeneration_instruction(
        schema=load_chapter_draft_schema(),
        repair_context=review_context(),
    )
    assert "不得自行补充答案" in prompt
    assert "保留占位符" in prompt


def test_candidate_certificate_confirmation_is_not_requested_from_fresh():
    context = review_context()
    serialized = json.dumps(context, ensure_ascii=False)
    assert "候选PMP证书编号和有效期不得进入Fresh" not in serialized
    assert "knowledge_candidate_selection_required" in context["review_only_issue_codes"]


def _context_for_error(code: str, sentence: str) -> dict:
    error = f"fact_boundary:{SECTION_TITLE}:{code}:{sentence}"
    return _build_fresh_repair_context(
        validation=validation_with([error], [issue(code, message=error)]),
        draft=chapter(sentence),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )


def test_product_capability_repair_uses_verification_wording():
    item = _context_for_error(
        "unconfirmed_product_capability",
        "确保单台交换机故障不影响集群正常运行。",
    )["auto_repair_items"][0]
    assert item["repair_type"] == "convert_capability_to_requirement_or_verification"
    assert "验证是否满足" in item["required_action"]


def test_product_capability_repair_does_not_create_new_capability():
    item = _context_for_error(
        "unconfirmed_product_capability",
        "本系统确保故障自动切换且业务不中断。",
    )["auto_repair_items"][0]
    assert "不得替换成另一种未确认能力" in item["required_action"]
    assert "自动故障切换" in item["must_not_assert"]


def test_personnel_qualification_repair_deletes_source_free_role_narrative():
    item = _context_for_error(
        "unconfirmed_personnel_qualification",
        "项目经理应具备三年以上实施管理经验并负责项目统筹。",
    )["auto_repair_items"][0]
    assert "删除无来源的资历" in item["required_action"]
    assert "保留中性项目角色动作" in item["required_action"]
    assert (
        item["allowed_replacement_type"]
        == "preserve_existing_generic_role_duty_only"
    )


def test_personnel_qualification_repair_removes_unconfirmed_years():
    item = _context_for_error(
        "unconfirmed_personnel_qualification",
        "项目经理应具备三年以上实施管理经验。",
    )["auto_repair_items"][0]
    assert item["repair_type"] == "remove_unconfirmed_personnel_qualification"
    assert "三年以上" in item["offending_span"]


def test_personnel_repair_does_not_replace_one_experience_claim_with_another():
    item = _context_for_error(
        "unconfirmed_personnel_qualification",
        "主要现场实施人员具备丰富医疗信息化集成经验。",
    )["auto_repair_items"][0]
    assert item["must_remove_qualification_assertion"] is True
    assert item["allowed_replacement_type"] == "delete_unsupported_sentence"
    assert "具有数据中心部署经验" in item["must_not_assert"]


def test_fresh_removes_unsupported_personnel_sentence_instead_of_replacing_it():
    item = _context_for_error(
        "unconfirmed_personnel_qualification",
        "实施工程师具有数据中心部署经验。",
    )["auto_repair_items"][0]
    assert item["allowed_generic_role_descriptions"] == []
    assert item["allowed_replacement_type"] == "delete_unsupported_sentence"


def test_personnel_repair_preserves_registered_placeholders():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    error = (
        f"fact_boundary:{SECTION_TITLE}:unconfirmed_personnel_qualification:"
        "项目经理应具备三年以上实施经验。"
    )
    context = _build_fresh_repair_context(
        validation=validation_with(
            [error],
            [issue("unconfirmed_personnel_qualification", message=error)],
        ),
        draft=chapter(
            f"项目经理为{{{{{placeholder_id}}}}}。",
            placeholder_ids=[placeholder_id],
        ),
        fact_ledger=fact_ledger(
            pending_items=[pending(placeholder_id, "项目经理")]
        ),
        pending_confirmations=[pending(placeholder_id, "项目经理")],
        target=target(),
    )
    assert placeholder_id in context["auto_repair_items"][0][
        "must_preserve_placeholders"
    ]


def test_personnel_repair_forbids_skill_and_certification_synonyms():
    item = _context_for_error(
        "unconfirmed_personnel_qualification",
        "技术负责人精通虚拟化技术并持有相关认证。",
    )["auto_repair_items"][0]
    assert {"熟悉超融合系统", "精通虚拟化技术", "持有相关认证"} <= set(
        item["must_not_assert"]
    )


def test_unconfirmed_generic_experience_is_not_candidate_promoted_without_candidate_evidence():
    errors = inspect_unconfirmed_commitments(
        chapter("主要现场实施人员具有数据中心部署经验。"),
        confirmed_facts={},
    )
    assert any("unconfirmed_personnel_qualification" in item for item in errors)
    assert not any("candidate_promoted_to_fact" in item for item in errors)


def test_candidate_personnel_fact_with_candidate_evidence_uses_candidate_promoted_code():
    sentence = "拟派张三担任项目经理并具有数据中心部署经验。"
    errors = inspect_unconfirmed_commitments(
        chapter(
            sentence,
            claims=[{
                "text": sentence,
                "claim_type": "confirmed_enterprise_fact",
                "source_ids": [],
                "evidence_ids": ["KB-PERSON-1"],
                "human_review_required": True,
            }],
        ),
        confirmed_facts={},
        candidate_evidence_ids={"KB-PERSON-1"},
    )
    assert any("candidate_promoted_to_fact" in item for item in errors)
    assert not any("unconfirmed_personnel_qualification" in item for item in errors)


def test_same_personnel_span_is_not_double_counted():
    sentence = "拟派张三担任项目经理并具有数据中心部署经验。"
    errors = inspect_unconfirmed_commitments(
        chapter(
            sentence,
            claims=[{
                "text": sentence,
                "claim_type": "confirmed_enterprise_fact",
                "source_ids": [],
                "evidence_ids": ["KB-PERSON-1"],
                "human_review_required": True,
            }],
        ),
        confirmed_facts={},
        candidate_evidence_ids={"KB-PERSON-1"},
    )
    personnel_errors = [
        item
        for item in errors
        if "candidate_promoted_to_fact" in item
        or "unconfirmed_personnel_qualification" in item
    ]
    assert len(personnel_errors) == 1


def test_fresh_repair_keeps_risk_section_complete_prose():
    context = _context_for_error(
        "numeric_commitment",
        "风险事件在2小时内完成处置。",
    )
    requirements = context["must_preserve"]["writing_quality_requirements"]
    assert requirements["complete_prose_required"] is True
    assert requirements["risk_section_required_dimensions"] == [
        "风险识别",
        "风险影响",
        "预防措施",
        "应急措施",
        "升级处理",
    ]


def test_fresh_repair_cannot_replace_risk_prose_with_table():
    context = _context_for_error(
        "numeric_commitment",
        "风险事件在2小时内完成处置。",
    )
    assert context["must_preserve"]["writing_quality_requirements"][
        "table_cannot_replace_prose"
    ] is True


def test_fresh_repair_must_pass_existing_prose_ratio_rules():
    prompt = _fresh_quality_regeneration_instruction(
        schema=load_chapter_draft_schema(),
        repair_context=_context_for_error(
            "numeric_commitment",
            "风险事件在2小时内完成处置。",
        ),
    )
    assert "prose ratio" in prompt
    assert "表格不得替代完整正文" in prompt


def test_fact_repair_does_not_remove_unrelated_section_structure():
    context = _context_for_error(
        "numeric_commitment",
        "技术负责人在2小时内给出处理意见。",
    )
    assert context["must_preserve"]["required_section_ids"] == [SECTION_ID]
    assert context["must_preserve"]["writing_quality_requirements"][
        "fact_repair_must_not_remove_unrelated_section_structure"
    ] is True


class EmptyStore:
    def load(self):
        return {"files": []}

    def get_file(self, file_id):
        raise KeyError(file_id)


class NoWeb:
    name = "fake"

    def search(self, query, options):
        raise AssertionError("web must not be called")


class TwoCandidateModel:
    def __init__(self, initial: dict, fresh: dict | None = None, *, fail_fresh=False):
        self.initial = initial
        self.fresh = fresh
        self.fail_fresh = fail_fresh
        self.calls = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        if len(self.calls) == 2 and self.fail_fresh:
            raise RuntimeError("fake fresh transport failure")
        value = self.initial if len(self.calls) == 1 else self.fresh
        return ToolModelResult(
            "final",
            [],
            json.dumps(value, ensure_ascii=False),
            {"provider": "fake", "model": "fake", "usage": empty_usage()},
        )


class PlaceholderRepairModel:
    def __init__(self):
        self.calls = []
        self.placeholder_id = placeholder_id_for_field("项目经理")

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        metadata = {"provider": "fake", "model": "fake", "usage": empty_usage()}
        if len(self.calls) == 1:
            return ToolModelResult(
                "tool_calls",
                [{
                    "tool_call_id": "pending-1",
                    "name": "record_pending_confirmation",
                    "arguments": {
                        "items": [{
                            "field": "项目经理",
                            "question": "请确认项目经理。",
                            "reason": "人员尚未选定。",
                            "candidates": [],
                            "affected_sections": [SECTION_ID],
                            "blocking": True,
                        }]
                    },
                }],
                None,
                metadata,
            )
        if len(self.calls) == 2:
            return ToolModelResult("final", [], json.dumps(chapter(), ensure_ascii=False), metadata)
        value = chapter(
            f"本项目拟派项目经理为{{{{{self.placeholder_id}}}}}。",
            placeholder_ids=[self.placeholder_id],
        )
        return ToolModelResult("final", [], json.dumps(value, ensure_ascii=False), metadata)


def write_inputs(root: Path) -> dict[str, Path]:
    response = {
        "chapters": [{
            "chapter_id": "SRC-FRESH",
            "response_points": [
                {
                    "point_id": "TR-006",
                    "original_text": "货到时间为自签订合同之日起30日内。",
                    "mandatory_marker": "required",
                    "content_type": "narrative",
                    "source_refs": [{"file": "fake.pdf", "page": 1}],
                },
                {
                    "point_id": "TR-007",
                    "original_text": "安装完成时间为货到之日起10日内。",
                    "mandatory_marker": "required",
                    "content_type": "narrative",
                    "source_refs": [{"file": "fake.pdf", "page": 2}],
                },
            ],
        }]
    }
    writing = {"chapters": [target()], "score_items": [{"score_item_id": "SCORE-001", "response_point_id": "TR-006"}]}
    tasks = {"tasks": []}
    paths = {}
    for name, value in (("response", response), ("writing", writing), ("tasks", tasks)):
        path = root / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    return paths


def run_agent(tmp_path: Path, model) -> dict:
    paths = write_inputs(tmp_path)
    agent = build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=paths["response"],
            writing_outline_path=paths["writing"],
            task_information_path=paths["tasks"],
            output_key="fresh-repair",
            target_chapter_id="CH-FRESH",
            web_search_policy="disabled",
        ),
        llm=model,
        output_dir=tmp_path,
        disable_web=True,
        metadata_store=EmptyStore(),
        web_provider=NoWeb(),
    )
    return agent.run()


def product_errors(count: int) -> dict:
    statements = [
        "本系统支持在线扩展。",
        "本系统采用多副本机制。",
    ][:count]
    return chapter("\n\n".join(statements))


def test_fresh_reinserts_missing_registered_placeholder_without_value(tmp_path):
    model = PlaceholderRepairModel()
    result = run_agent(tmp_path, model)
    assert result["fresh_quality_regeneration"]["accepted"] is False
    assert result["candidate_selection_reason"] == "fresh_candidate_not_generated"
    content = result["chapter_draft"]["sections"][0]["content"]
    assert f"{{{{{model.placeholder_id}}}}}" not in content
    assert "张三" not in content


def rejected_fresh_result(tmp_path: Path) -> dict:
    model = TwoCandidateModel(
        product_errors(2),
        chapter("我公司已通过ISO 9001质量管理体系认证。"),
    )
    return run_agent(tmp_path, model)


def test_fresh_generated_and_validated_can_be_true_when_not_accepted(tmp_path):
    status = rejected_fresh_result(tmp_path)["fresh_quality_regeneration"]
    assert status["generated"] is False
    assert status["validated"] is False
    assert status["succeeded"] is False
    assert status["accepted"] is False


def test_fresh_rejected_reason_is_recorded(tmp_path):
    status = rejected_fresh_result(tmp_path)["fresh_quality_regeneration"]
    assert status["rejected_reason"] == "fresh_patch_protocol_invalid"


def test_model_call_failure_is_distinct_from_candidate_rejection(tmp_path):
    model = TwoCandidateModel(product_errors(1), fail_fresh=True)
    status = run_agent(tmp_path, model)["fresh_quality_regeneration"]
    assert status["attempted"] is True
    assert status["model_call_failed"] is True
    assert status["generated"] is False
    assert status["accepted"] is False
    assert status["rejected_reason"] is None


def test_validation_issue_persists_repair_disposition(tmp_path):
    result = run_agent(
        tmp_path,
        TwoCandidateModel(product_errors(1), product_errors(1)),
    )
    issues = result["validation"]["issues"]
    assert issues
    assert all(
        item.get("repair_disposition") in {"auto_repairable", "review_only"}
        for item in issues
    )


def test_review_only_issue_remains_blocking_when_required():
    audit = classify_chapter_audit(
        {"issues": [issue("evidence_pending")]}
    )
    finding = next(
        item for item in audit["review_only"] if item["code"] == "evidence_pending"
    )
    assert finding["repair_disposition"] == "review_only"
    assert finding["severity"] == "blocking"


def test_fresh_issue_classification_counts_are_mutually_exclusive():
    auto = issue("numeric_commitment")
    review = issue("evidence_pending")
    context = _build_fresh_repair_context(
        validation=validation_with(
            [auto["message"]],
            [auto, review],
        ),
        draft=chapter("应在2小时内完成处理。"),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )
    summary = context["repair_input_summary"]
    assert (
        summary["auto_repairable_unique_issue_count"]
        + summary["review_only_unique_issue_count"]
        == summary["unique_issue_count"]
    )
    assert summary["total_issue_count"] == summary["unique_issue_count"]


def test_review_guards_are_counted_separately():
    context = review_context()
    summary = context["repair_input_summary"]
    assert summary["review_guard_count"] == len(context["review_only_guards"])
    assert summary["review_guard_count"] >= summary[
        "review_only_unique_issue_count"
    ]


def test_duplicate_personnel_issue_does_not_inflate_counts():
    duplicate = issue(
        "unconfirmed_personnel_qualification",
        message=(
            f"fact_boundary:{SECTION_TITLE}:unconfirmed_personnel_qualification:"
            "主要现场实施人员具有数据中心部署经验。"
        ),
    )
    audit = classify_chapter_audit({"issues": [duplicate, dict(duplicate)]})
    assert audit["unique_issue_count"] == 1
    assert audit["auto_repairable_unique_issue_count"] == 1


def test_fresh_cannot_add_new_product_capability_while_repairing():
    prompt = _fresh_quality_regeneration_instruction(
        schema=load_chapter_draft_schema(),
        repair_context=_context_for_error(
            "numeric_commitment", "问题在2小时内处理。"
        ),
    )
    assert "不得补充事实、人员内容、产品方案、资源计划" in prompt
    assert "MLAG" in prompt and "链路冗余" in prompt


def test_fresh_cannot_add_staffing_assignment_while_repairing():
    prompt = _fresh_quality_regeneration_instruction(
        schema={}, repair_context=_context_for_error("numeric_commitment", "2小时内通报。")
    )
    assert "双人复核" in prompt and "质量保障工程师复核" in prompt


def test_fresh_cannot_add_resource_commitment_while_repairing():
    prompt = _fresh_quality_regeneration_instruction(
        schema={}, repair_context=_context_for_error("numeric_commitment", "2小时内通报。")
    )
    assert "备份人员" in prompt and "公司后备资源" in prompt
    assert "人员替换机制" in prompt


def test_fresh_deletes_claim_when_no_source_safe_replacement_exists():
    item = _context_for_error(
        "personnel_narrative_without_source",
        "技术负责人负责部署指导并具有协调能力。",
    )["auto_repair_items"][0]
    assert (
        item["allowed_replacement_type"]
        == "preserve_existing_generic_role_duty_only"
    )
    assert "删除无来源的资历" in item["required_action"]


def test_fresh_role_is_residual_repair_not_content_enrichment():
    prompt = _fresh_quality_regeneration_instruction(
        schema={}, repair_context=_context_for_error("numeric_commitment", "2小时内通报。")
    )
    assert "残余" in prompt
    assert "不得补充事实" in prompt
    assert "无法安全改写时" in prompt


def test_model_created_placeholder_is_auto_repairable():
    assert disposition("unregistered_placeholder", category="placeholder") == "auto_repairable"


def test_unregistered_trial_run_placeholder_is_removed():
    token = "{{PH-FIELD-TRIAL-RUN-DURATION}}"
    item = _context_for_error("unregistered_placeholder", token)["auto_repair_items"][0]
    assert item["repair_type"] == "delete_unregistered_placeholder"
    assert item["unregistered_placeholder_token"] == token
    assert token in item["must_not_assert"]


def test_fresh_does_not_replace_unregistered_placeholder_with_invented_value():
    item = _context_for_error(
        "unregistered_placeholder", "{{PH-FIELD-TRIAL-RUN-DURATION}}"
    )["auto_repair_items"][0]
    assert "不得填入推测值" in item["required_action"]
    assert "创建替代占位符" in item["required_action"]


def test_registered_unfilled_placeholder_remains_review_only():
    assert disposition(
        "placeholder_pending_completion", category="placeholder"
    ) == "review_only"


def _chapter_with_intro_table() -> dict:
    value = chapter()
    value["sections"][0]["content"] = (
        prose()
        + "\n\n下表列示经确认后需要维护的实施事项。"
        + "\n\n表1 实施事项表"
        + "\n\n| 事项 | 处理边界 |\n| --- | --- |\n| 输入核对 | 按确认资料执行 |"
        + "\n\n表中事项完成后继续形成记录并闭环问题。"
    )
    return value


def test_fresh_preserves_existing_table_intro():
    error = f"fact_boundary:{SECTION_TITLE}:numeric_commitment:问题在2小时内处理。"
    context = _build_fresh_repair_context(
        validation=validation_with([error], [issue("numeric_commitment", message=error)]),
        draft=_chapter_with_intro_table(),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )
    introductions = context["must_preserve"]["registered_table_introductions"]
    assert introductions and "下表列示" in introductions[0]["text"]


def test_fresh_cannot_create_table_without_intro():
    prompt = _fresh_quality_regeneration_instruction(
        schema={}, repair_context=_context_for_error("numeric_commitment", "2小时内通报。")
    )
    assert "不得重新设计表格" in prompt
    requirements = _context_for_error(
        "numeric_commitment", "2小时内通报。"
    )["must_preserve"]["writing_quality_requirements"]
    assert requirements["ordinary_table_requires_matching_intro"] is True


def test_table_without_intro_error_contains_actual_table_context():
    value = chapter()
    value["sections"][0]["content"] = (
        prose()
        + "\n\n| 事项 | 责任边界 |\n| --- | --- |\n| 输入核对 | 按确认资料执行 |"
        + "\n\n表中事项完成后继续形成记录。"
    )
    details = _fresh_quality_error_details(
        [f"section_quality:{SECTION_TITLE}:table_without_intro:1"],
        draft=value,
    )
    assert "Markdown表格上下文" in details[0]["offending_sentence"]
    assert "事项" in details[0]["offending_sentence"]


def test_table_number_only_is_not_used_as_offending_sentence():
    value = _chapter_with_intro_table()
    details = _fresh_quality_error_details(
        [f"section_quality:{SECTION_TITLE}:table_without_intro:1"],
        draft=value,
    )
    assert details[0]["offending_sentence"] not in {"1", "表1"}


def test_fresh_section_without_product_permission_cannot_add_product_capability():
    no_product_ledger = fact_ledger(confirmed_records={})
    error = f"fact_boundary:{SECTION_TITLE}:numeric_commitment:问题在2小时内处理。"
    context = _build_fresh_repair_context(
        validation=validation_with([error], [issue("numeric_commitment", message=error)]),
        draft=chapter("问题在2小时内处理。"),
        fact_ledger=no_product_ledger,
        pending_confirmations=[],
        target=target(),
    )
    guard = context["must_preserve"]["section_generation_guards"][SECTION_ID]
    assert guard["allowed_product_capabilities"] == []
    assert guard["product_capability_generation_allowed"] is False
    assert context["auto_repair_items"][0][
        "product_capability_generation_allowed"
    ] is False


def test_fresh_product_permission_is_derived_from_current_confirmed_facts():
    context = _context_for_error("numeric_commitment", "问题在2小时内处理。")
    guard = context["must_preserve"]["section_generation_guards"][SECTION_ID]
    assert guard["product_capability_generation_allowed"] is True
    assert guard["allowed_product_capabilities"][0]["source_ids"] == [
        "CONF-CAP-1"
    ]


def test_fresh_receives_section_specific_quality_floor():
    context = _context_for_error("numeric_commitment", "问题在2小时内处理。")
    floor = context["must_preserve"]["section_quality_floors"][SECTION_ID]
    assert floor["minimum_paragraph_count"] == 2
    assert floor["minimum_prose_characters"] == 150
    assert floor["required_dimensions"]


def test_fresh_fact_removal_does_not_drop_section_below_quality_floor():
    context = _context_for_error("numeric_commitment", "问题在2小时内处理。")
    item = context["auto_repair_items"][0]
    assert item["section_quality_floor"] == context["must_preserve"][
        "section_quality_floors"
    ][SECTION_ID]
    assert context["must_preserve"]["writing_quality_requirements"][
        "fact_repair_must_not_remove_unrelated_section_structure"
    ] is True


def test_fresh_cannot_restore_prose_with_unconfirmed_product_claim():
    error = f"fact_boundary:{SECTION_TITLE}:numeric_commitment:问题在2小时内处理。"
    context = _build_fresh_repair_context(
        validation=validation_with(
            [error],
            [issue("numeric_commitment", message=error)],
        ),
        draft=chapter("问题在2小时内处理。"),
        fact_ledger=fact_ledger(confirmed_records={}),
        pending_confirmations=[],
        target=target(),
    )
    item = context["auto_repair_items"][0]
    assert item["product_capability_generation_allowed"] is False
    assert item["allowed_product_capabilities"] == []


def test_registered_personnel_placeholders_enter_fresh_preserve_list():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    pending_item = pending(placeholder_id, "项目经理")
    error = f"fact_boundary:{SECTION_TITLE}:numeric_commitment:问题在2小时内处理。"
    context = _build_fresh_repair_context(
        validation=validation_with([error], [issue("numeric_commitment", message=error)]),
        draft=chapter(
            f"项目经理为{{{{{placeholder_id}}}}}，问题在2小时内处理。",
            placeholder_ids=[placeholder_id],
        ),
        fact_ledger=fact_ledger(pending_items=[pending_item]),
        pending_confirmations=[pending_item],
        target=target(),
    )
    assert context["preserved_placeholder_ids"] == [placeholder_id]


def test_no_registered_placeholder_keeps_preserve_list_empty():
    context = _context_for_error("numeric_commitment", "问题在2小时内处理。")
    assert context["preserved_placeholder_ids"] == []


def _required_fact_validation() -> dict:
    message = (
        "required project facts are unconfirmed but no pending confirmation "
        "was recorded"
    )
    return {
        "errors": [message],
        "issues": [
            {
                "severity": "blocking",
                "category": "placeholder",
                "code": "required_project_fact_pending_confirmation_missing",
                "section_id": None,
                "section_title": None,
                "message": message,
                "repair_disposition": "review_only",
            }
        ],
    }


def test_required_project_fact_pending_is_review_guard_only():
    context = _build_fresh_repair_context(
        validation=_required_fact_validation(),
        draft=chapter(),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )
    guard = next(
        item
        for item in context["review_only_guards"]
        if item["issue_code"]
        == "required_project_fact_pending_confirmation_missing"
    )
    assert guard["repair_type"] == "preserve_for_human_confirmation"
    assert "不得由Fresh补充" in guard["required_action"]


def test_required_project_fact_pending_is_not_sent_as_rewrite_target():
    context = _build_fresh_repair_context(
        validation=_required_fact_validation(),
        draft=chapter(),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )
    assert not any(
        item["issue_code"]
        == "required_project_fact_pending_confirmation_missing"
        for item in context["auto_repair_items"]
    )


def test_review_only_required_fact_remains_after_fresh():
    context = _build_fresh_repair_context(
        validation=_required_fact_validation(),
        draft=chapter(),
        fact_ledger=fact_ledger(),
        pending_confirmations=[],
        target=target(),
    )
    assert (
        "required_project_fact_pending_confirmation_missing"
        in context["review_only_issue_codes"]
    )

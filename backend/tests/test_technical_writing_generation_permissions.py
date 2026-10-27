from __future__ import annotations

import json
import inspect
from copy import deepcopy
from pathlib import Path

from app.chapter_draft_schema import load_chapter_draft_schema
from app.llm_client import ToolModelResult, empty_usage
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    _apply_personnel_content_gate,
    _build_section_fact_ledger,
    _section_fact_ledger_message,
    _fresh_quality_error_details,
    build_technical_writing_agent,
    validate_technical_chapter,
)
from app.technical_writing_quality import (
    audit_generation_permissions,
    inspect_deviation_issues,
    inspect_unconfirmed_commitments,
    review_chapter_quality,
)


SECTION_ID = "CH-PERM-01"
SECTION_TITLE = "实施与人员安排"


def prose(extra: str = "") -> str:
    return (
        "本节以形成可执行、可检查的实施安排为目标，在招标要求、责任接口和输入资料"
        "已经核对的前提下，由项目组组织方案复核、现场实施和过程记录，确保每项工作"
        "能够追踪到输入、执行动作与交付成果。"
        "\n\n实施过程中如发现输入缺失、接口异常或检查结果不满足要求，项目组将及时登记"
        "问题、分析影响并组织处置，待复核结论满足完成判据后继续推进，最终形成完整的"
        "实施记录和问题闭环资料。"
        + (f"\n\n{extra}" if extra else "")
    )


def target() -> dict:
    requirement = {
        "response_point_ids": ["TR-006", "TR-CAP"],
        "score_item_refs": [],
        "manual_project_facts": {"need": "none", "fact_types": []},
        "web": {"need": "none"},
    }
    return {
        "chapter_id": "CH-PERM",
        "title": "许可测试章节",
        "writing_requirements": requirement,
        "children": [
            {
                "chapter_id": SECTION_ID,
                "title": SECTION_TITLE,
                "writing_requirements": requirement,
                "children": [],
            }
        ],
    }


def original_context(
    *,
    confirmed_records: dict | None = None,
) -> dict:
    records = confirmed_records or {}
    return {
        "tender_requirements": {
            "requirements": [
                {
                    "point_id": "TR-006",
                    "original_text": "货到时间为自合同签订之日起30日内。",
                },
                {
                    "point_id": "TR-CAP",
                    "original_text": "所投产品应支持在线扩展和数据保护。",
                },
            ]
        },
        "confirmed_project_facts": {
            "facts": {
                key: item.get("value")
                for key, item in records.items()
            },
            "fact_records": records,
        },
        "draft_plan": {"draft_units": []},
        "task_information": {"tasks": []},
        "tender_tables": [],
    }


def pending(
    placeholder_id: str = "PH-FIELD-PROJECT-MANAGER",
    field: str = "项目经理",
) -> list[dict]:
    return [
        {
            "field": field,
            "placeholder_id": placeholder_id,
            "placeholder_token": f"{{{{{placeholder_id}}}}}",
            "affected_sections": [SECTION_ID],
            "blocking": True,
        }
    ]


def ledger(
    *,
    confirmed_records: dict | None = None,
    pending_items: list[dict] | None = None,
    local_refs: list[dict] | None = None,
) -> list[dict]:
    return _build_section_fact_ledger(
        original_context=original_context(
            confirmed_records=confirmed_records
        ),
        target=target(),
        pending_confirmations=pending_items or [],
        local_evidence_refs=local_refs or [],
    )


def draft(
    statement: str = "",
    *,
    claims: list[dict] | None = None,
    placeholder_ids: list[str] | None = None,
) -> dict:
    placeholder_ids = placeholder_ids or []
    return {
        "chapter_title": "许可测试章节",
        "sections": [
            {
                "section_id": SECTION_ID,
                "heading": SECTION_TITLE,
                "block_type": "implementation_plan",
                "content": prose(statement),
                "covered_score_item_ids": [],
                "covered_requirement_ids": ["TR-006", "TR-CAP"],
                "placeholder_ids": placeholder_ids,
            }
        ],
        "claims": claims or [],
        "placeholders_used": placeholder_ids,
        "score_items_covered": [],
        "requirements_covered": ["TR-006", "TR-CAP"],
        "warnings": [],
    }


def tender_claim(text: str, source_id: str) -> dict:
    return {
        "text": text,
        "claim_type": "tender_fact",
        "source_ids": [source_id],
        "evidence_ids": [],
        "human_review_required": False,
    }


def confirmed_claim(text: str, evidence_id: str) -> dict:
    return {
        "text": text,
        "claim_type": "confirmed_enterprise_fact",
        "source_ids": [],
        "evidence_ids": [evidence_id],
        "human_review_required": False,
    }


def audit(value: dict, fact_ledger: list[dict] | None = None) -> dict:
    return audit_generation_permissions(
        value,
        fact_ledger=fact_ledger or ledger(),
    )


def test_generation_context_contains_closed_fact_permissions():
    permissions = ledger(pending_items=pending())[0][
        "generation_permissions"
    ]
    assert {
        "allowed_confirmed_facts",
        "allowed_tender_facts",
        "allowed_confirmed_numeric_facts",
        "allowed_tender_numeric_facts",
        "allowed_confirmed_personnel_facts",
        "allowed_confirmed_product_capabilities",
        "allowed_placeholders",
        "allowed_generic_role_descriptions",
        "forbidden_unresolved_categories",
        "forbid_unlisted_numeric_commitments",
        "forbid_unlisted_personnel_qualifications",
        "forbid_unlisted_product_capabilities",
    } <= set(permissions)
    assert permissions["forbid_unlisted_numeric_commitments"] is True
    assert permissions["forbid_unlisted_personnel_qualifications"] is True
    assert permissions["forbid_unlisted_product_capabilities"] is True
    table_contract = permissions["generated_supporting_table_contract"]
    assert "metadata.title" in table_contract["exact_title"]
    assert table_contract["intro_requirement"]
    assert table_contract["followup_requirement"]
    assert table_contract["column_contract"]


def test_unlisted_commitment_number_is_forbidden():
    result = audit(draft("重大事项在24小时内完成通报。"))
    assert result["unauthorized_numeric_claims"]
    assert result["passed"] is False


def test_allowed_tender_number_is_available_to_model():
    fact_ledger = ledger()
    allowed = fact_ledger[0]["generation_permissions"][
        "allowed_tender_numeric_facts"
    ]
    assert any(
        item["value"] == 30
        and item["unit"] == "日"
        and item["source_id"] == "TR-006"
        for item in allowed
    )
    sentence = "我方货到时间为自合同签订之日起30日内，无偏离。"
    result = audit(
        draft(
            sentence,
            claims=[
                tender_claim(
                    "货到时间为自合同签订之日起30日内。",
                    "TR-006",
                )
            ],
        ),
        fact_ledger,
    )
    assert result["unauthorized_numeric_claims"] == []


def test_ordinary_sequence_number_is_not_commitment():
    result = audit(
        draft("步骤1核对输入，步骤2组织实施，步骤3复核结果。")
    )
    assert result["unauthorized_numeric_claims"] == []


def test_confirmed_personnel_years_are_in_allowed_permissions():
    records = {
        "project_manager": {
            "status": "confirmed",
            "value": {"management_years": 5},
            "source_id": "CONF-PERSON-1",
        }
    }
    fact_ledger = ledger(confirmed_records=records)
    allowed = fact_ledger[0]["generation_permissions"][
        "allowed_confirmed_personnel_facts"
    ]
    assert allowed and allowed[0]["source_ids"] == ["CONF-PERSON-1"]
    assert audit(
        draft("拟派项目经理具有5年项目管理经验。"),
        fact_ledger,
    )["unauthorized_personnel_claims"] == []


def test_unconfirmed_role_year_threshold_is_forbidden():
    result = audit(draft("项目经理应具有不少于五年项目管理经验。"))
    assert any(
        item["code"] == "unconfirmed_personnel_qualification"
        for item in result["unauthorized_personnel_claims"]
    )


def test_unconfirmed_similar_project_count_is_forbidden():
    result = audit(draft("项目经理应至少参与三个同类项目。"))
    assert result["unauthorized_personnel_claims"]


def test_generic_role_description_is_allowed_when_personnel_source_is_missing():
    value = draft(
        "项目经理负责项目统筹、沟通和进度管理；技术负责人负责技术方案审核和技术问题处理。"
    )
    result = audit(value)
    assert result["unauthorized_personnel_claims"] == []
    assert not any(
        "candidate_promoted_to_fact" in item
        for item in inspect_unconfirmed_commitments(
            value,
            confirmed_facts={},
        )
    )


def test_candidate_personnel_placeholder_is_not_promoted_fact():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    value = draft(
        f"本项目拟派项目经理为{{{{{placeholder_id}}}}}。",
        placeholder_ids=[placeholder_id],
    )
    assert not any(
        "candidate_promoted_to_fact" in item
        for item in inspect_unconfirmed_commitments(
            value,
            confirmed_facts={},
        )
    )
    assert audit(
        value,
        ledger(pending_items=pending()),
    )["candidate_facts_promoted"] == []


def test_unconfirmed_backup_staffing_is_forbidden():
    result = audit(draft("每个岗位设置候补人员，并由另一名工程师复核。"))
    assert result["unauthorized_personnel_claims"]


def test_confirmed_product_capability_is_allowed():
    sentence = "本系统支持在线扩展。"
    records = {
        "online_expansion": {
            "status": "confirmed",
            "value": sentence,
            "source_id": "CONF-CAP-1",
        }
    }
    fact_ledger = ledger(confirmed_records=records)
    result = audit(
        draft(sentence, claims=[confirmed_claim(sentence, "CONF-CAP-1")]),
        fact_ledger,
    )
    assert result["unauthorized_product_capabilities"] == []


def test_tender_capability_is_requirement_only():
    fact_ledger = ledger()
    permissions = fact_ledger[0]["generation_permissions"]
    assert permissions[
        "tender_capabilities_for_requirement_description_only"
    ]
    statement = "招标文件要求所投产品支持在线扩展，本节仅描述采购要求。"
    result = audit(
        draft(statement, claims=[tender_claim(statement, "TR-CAP")]),
        fact_ledger,
    )
    assert result["unauthorized_product_capabilities"] == []


def test_test_verification_target_is_allowed():
    statement = "部署完成后通过测试用例验证在线扩展是否满足技术应答要求。"
    result = audit(draft(statement))
    assert result["unauthorized_product_capabilities"] == []


def _assert_capability_forbidden(statement: str) -> None:
    assert audit(draft(statement))["unauthorized_product_capabilities"]


def test_unconfirmed_multi_copy_is_forbidden():
    _assert_capability_forbidden("本系统采用多副本机制保护数据。")


def test_unconfirmed_erasure_coding_is_forbidden():
    _assert_capability_forbidden("本系统支持纠删码并提供数据保护。")


def test_unconfirmed_online_expansion_is_forbidden():
    _assert_capability_forbidden("本系统支持节点在线扩展。")


def test_unconfirmed_failover_result_is_forbidden():
    _assert_capability_forbidden("本系统确保故障时自动切换且业务不中断。")


def test_candidate_iso_certificate_cannot_generate_confirmed_statement():
    sentence = "我公司已通过ISO 9001质量管理体系认证。"
    fact_ledger = ledger(
        local_refs=[
            {
                "candidate_id": "KB-ISO-1",
                "entity_type": "enterprise_material",
                "selection_status": "candidate",
                "excerpt": sentence,
            }
        ]
    )
    result = audit(
        draft(sentence, claims=[confirmed_claim(sentence, "KB-ISO-1")]),
        fact_ledger,
    )
    assert result["candidate_facts_promoted"]


def test_confirmed_selected_iso_certificate_can_generate_confirmed_statement():
    sentence = "我公司已通过ISO 9001质量管理体系认证。"
    fact_ledger = ledger(
        local_refs=[
            {
                "candidate_id": "CONF-ISO-1",
                "entity_type": "enterprise_material",
                "selection_status": "confirmed",
                "excerpt": sentence,
            }
        ]
    )
    result = audit(
        draft(sentence, claims=[confirmed_claim(sentence, "CONF-ISO-1")]),
        fact_ledger,
    )
    assert result["candidate_facts_promoted"] == []


def test_candidate_evidence_stays_in_pending_confirmation():
    fact_ledger = ledger(
        pending_items=pending("PH-ISO-CERTIFICATE", "ISO认证材料"),
        local_refs=[
            {
                "candidate_id": "KB-ISO-1",
                "entity_type": "enterprise_material",
                "selection_status": "candidate",
                "excerpt": "ISO 9001证书候选材料",
            }
        ],
    )
    entry = fact_ledger[0]
    assert "KB-ISO-1" in entry["candidate_evidence_ids"]
    assert not any(
        "KB-ISO-1" in item.get("source_ids", [])
        for item in entry["generation_permissions"]["allowed_confirmed_facts"]
    )
    assert any(
        item["placeholder_id"] == "PH-ISO-CERTIFICATE"
        for item in entry["generation_permissions"]["allowed_placeholders"]
    )


def _placeholder_validation(
    placeholder_id: str,
    sentence: str,
) -> dict:
    pending_items = pending(placeholder_id)
    value = draft(sentence, placeholder_ids=[placeholder_id])
    return validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target=target(),
        confirmed_facts={},
        pending_confirmations=pending_items,
        tender_tables=[],
        generation_permissions=ledger(
            pending_items=pending_items
        ),
    )


def test_registered_person_placeholder_is_warning_only():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    validation = _placeholder_validation(
        placeholder_id,
        f"本项目拟派项目经理为{{{{{placeholder_id}}}}}。",
    )
    placeholder_issues = [
        item
        for item in validation["issues"]
        if item["category"] == "placeholder"
    ]
    assert [(item["severity"], item["code"]) for item in placeholder_issues] == [
        ("warning", "placeholder_pending_completion")
    ]


def test_registered_team_placeholder_is_warning_only():
    placeholder_id = "PH-FIELD-PROJECT-TEAM"
    validation = _placeholder_validation(
        placeholder_id,
        f"项目团队成员包括{{{{{placeholder_id}}}}}。",
    )
    assert not any(
        item["severity"] == "blocking"
        for item in validation["issues"]
        if item["category"] in {"placeholder", "fact_boundary"}
    )


def test_placeholder_issue_is_not_triplicated():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    validation = _placeholder_validation(
        placeholder_id,
        f"本项目拟派项目经理为{{{{{placeholder_id}}}}}。",
    )
    relevant = [
        item
        for item in validation["issues"]
        if item["code"]
        in {
            "candidate_promoted_to_fact",
            "evidence_pending",
            "placeholder_pending_completion",
        }
    ]
    assert len(relevant) == 1
    assert relevant[0]["code"] == "placeholder_pending_completion"


def test_generic_project_manager_duty_is_not_candidate_fact():
    value = draft("项目经理负责项目统筹、沟通和进度管理。")
    assert not any(
        "candidate_promoted_to_fact" in item
        for item in inspect_unconfirmed_commitments(
            value,
            confirmed_facts={},
        )
    )


def test_unconfirmed_full_time_commitment_has_specific_code():
    value = draft("项目经理自合同生效之日起全程负责本项目。")
    assert any(
        "unconfirmed_resource_commitment" in item
        for item in inspect_unconfirmed_commitments(
            value,
            confirmed_facts={},
        )
    )


def test_table_intro_phrase_is_detected_across_blank_line():
    content = (
        prose()
        + "\n\n实施过程中的关键里程碑节点如下表所示。"
        + "\n\n表1 实施里程碑节点表"
        + "\n\n| 节点 | 工作 |\n| --- | --- |\n| 启动 | 核对输入 |"
        + "\n\n表中事项完成后继续执行复核和问题闭环。"
    )
    value = draft()
    value["sections"][0]["content"] = content
    value["generated_supporting_tables"] = [
        {
            "section_id": SECTION_ID,
            "title": "实施里程碑节点表",
            "purpose": "核对里程碑节点",
            "why_table_is_better_than_prose": "节点与工作需要横向对应",
        }
    ]
    review = review_chapter_quality(value, tender_tables=[])
    assert not any(
        "table_without_intro" in item
        for item in review["quality_errors"]
    )


def test_table_intro_with_xiashu_table_number_is_detected():
    value = draft()
    value["sections"][0]["content"] = (
        prose()
        + "\n\n下述表1展示了项目核心角色与职责的对应关系，作为人员就位前的职能框架。"
        + "\n\n| 角色 | 职责 |\n| --- | --- |\n| 项目经理 | 统筹沟通 |"
        + "\n\n表中职责将在人员确认后与项目任务继续校验。"
    )
    review = review_chapter_quality(value, tender_tables=[])
    assert not any("table_without_intro" in item for item in review["quality_errors"])


def test_table_intro_with_table_number_across_blank_line_is_detected():
    value = draft()
    value["sections"][0]["content"] = (
        prose()
        + "\n\n具体内容见表一。"
        + "\n\n表一 项目核心角色职责表"
        + "\n\n| 角色 | 职责 |\n| --- | --- |\n| 项目经理 | 统筹沟通 |"
        + "\n\n表中职责将在人员确认后与项目任务继续校验。"
    )
    review = review_chapter_quality(value, tender_tables=[])
    assert not any("table_without_intro" in item for item in review["quality_errors"])


def test_table_number_is_not_reported_as_offending_sentence():
    value = draft()
    details = _fresh_quality_error_details(
        [f"section_quality:{SECTION_TITLE}:table_without_intro:1"],
        draft=value,
    )
    assert details[0]["offending_sentence"] != "1"
    assert details[0]["offending_span"] != "1"


def test_generated_table_requires_exact_explicit_title():
    value = draft()
    value["sections"][0]["content"] = (
        prose()
        + "\n\n关键里程碑节点如下表所示。"
        + "\n\n| 节点 | 工作 |\n| --- | --- |\n| 启动 | 核对输入 |"
        + "\n\n表中事项完成后继续执行复核和问题闭环。"
    )
    value["generated_supporting_tables"] = [
        {
            "section_id": SECTION_ID,
            "title": "实施里程碑节点表",
            "purpose": "核对里程碑节点",
            "why_table_is_better_than_prose": "节点与工作需要横向对应",
        }
    ]
    review = review_chapter_quality(value, tender_tables=[])
    assert (
        "generated_supporting_table:"
        f"{SECTION_TITLE}:title_not_matched"
    ) in review["quality_errors"]


def test_candidate_iso_confirmed_wording_uses_candidate_promoted_code():
    sentence = "我公司已通过ISO 9001质量管理体系认证。"
    fact_ledger = ledger(
        local_refs=[
            {
                "candidate_id": "KB-ISO-1",
                "entity_type": "enterprise_material",
                "selection_status": "candidate",
                "excerpt": sentence,
            }
        ]
    )
    validation = validate_technical_chapter(
        draft(
            sentence,
            claims=[
                {
                    **confirmed_claim(sentence, "KB-ISO-1"),
                    "evidence_status": "pending",
                    "deviation_status": "evidence_pending",
                }
            ],
        ),
        schema=load_chapter_draft_schema(),
        target=target(),
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        candidate_evidence_ids={"KB-ISO-1"},
        generation_permissions=fact_ledger,
    )
    codes = [item["code"] for item in validation["issues"]]
    assert "candidate_promoted_to_fact" in codes
    assert "evidence_pending" not in codes


def test_generation_permission_audit_reports_unlisted_number():
    result = audit(draft("偏差应在4小时内完成评估。"))
    assert result["passed"] is False
    assert result["unauthorized_numeric_claims"][0]["numeric_text"] == "4小时内"


def test_generation_permission_audit_reports_unconfirmed_capability():
    result = audit(draft("本系统采用纠删码并支持在线扩展。"))
    assert result["unauthorized_product_capabilities"]


class EmptyMetadataStore:
    def load(self):
        return {"files": []}

    def get_file(self, file_id):
        raise KeyError(file_id)


class NoWeb:
    name = "fake"

    def search(self, query, options):
        raise AssertionError("web must not be called")


class RepeatingFakeClient:
    def __init__(self, value: dict):
        self.value = value
        self.calls: list[dict] = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": json.loads(
                    json.dumps(messages, ensure_ascii=False)
                ),
                "tools": list(tools),
            }
        )
        return ToolModelResult(
            "final",
            [],
            json.dumps(self.value, ensure_ascii=False),
            {
                "provider": "fake",
                "model": "fake",
                "api_mode": "fake",
                "usage": empty_usage(),
            },
        )


def write_inputs(root: Path) -> dict[str, Path]:
    response = {
        "chapters": [
            {
                "chapter_id": "SRC-PERM",
                "response_points": [
                    {
                        "point_id": "TR-006",
                        "original_text": "货到时间为自合同签订之日起30日内。",
                        "mandatory_marker": "required",
                        "content_type": "narrative",
                        "source_refs": [{"file": "fake.pdf", "page": 1}],
                    },
                    {
                        "point_id": "TR-CAP",
                        "original_text": "所投产品应支持在线扩展和数据保护。",
                        "mandatory_marker": "required",
                        "content_type": "narrative",
                        "source_refs": [{"file": "fake.pdf", "page": 2}],
                    },
                    {
                        "point_id": "TR-PERSON",
                        "original_text": "投标文件应填写项目负责人信息。",
                        "mandatory_marker": "required",
                        "content_type": "narrative",
                        "source_refs": [{"file": "fake.pdf", "page": 3}],
                    },
                ],
            }
        ]
    }
    writing_target = target()
    writing_target["writing_requirements"]["response_point_ids"].append(
        "TR-PERSON"
    )
    writing_target["children"][0]["writing_requirements"][
        "response_point_ids"
    ].append("TR-PERSON")
    writing = {"chapters": [writing_target], "score_items": []}
    tasks = {"tasks": []}
    paths: dict[str, Path] = {}
    for name, value in (
        ("response", response),
        ("writing", writing),
        ("tasks", tasks),
    ):
        path = root / f"{name}.json"
        path.write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )
        paths[name] = path
    return paths


def run_blocked_candidate(tmp_path: Path) -> tuple[dict, RepeatingFakeClient]:
    paths = write_inputs(tmp_path)
    value = draft("重大事项在24小时内完成通报。")
    model = RepeatingFakeClient(value)
    agent = build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=paths["response"],
            writing_outline_path=paths["writing"],
            task_information_path=paths["tasks"],
            output_key="permission-audit",
            target_chapter_id="CH-PERM",
            web_search_policy="disabled",
        ),
        llm=model,
        output_dir=tmp_path,
        disable_web=True,
        max_agent_steps=2,
        metadata_store=EmptyMetadataStore(),
        web_provider=NoWeb(),
    )
    return agent.run(), model


def _ledger_payload(call: dict) -> list[dict]:
    content = next(
        str(item["content"])
        for item in call["messages"]
        if item["role"] == "developer"
        and "[Section-specific Fact Ledger]" in str(item["content"])
    )
    payload = json.loads(content.split("\n", 1)[1])
    return payload["section_fact_ledger"]


def _compiled_permission_payload(call: dict) -> dict:
    content = next(
        str(item["content"])
        for item in call["messages"]
        if item["role"] == "user"
    )
    return json.loads(content)["compiled_effective_writing_context"]


def test_initial_and_fresh_use_same_generation_permissions(tmp_path):
    result, model = run_blocked_candidate(tmp_path)
    assert result["fresh_quality_regeneration"]["attempted"] is True
    initial = _ledger_payload(model.calls[0])
    fresh = _compiled_permission_payload(model.calls[-1])
    initial_by_id = {
        item["section_id"]: item["generation_permissions"] for item in initial
    }
    for section_id, section_context in fresh["section_contexts"].items():
        assert section_context["effective_writing_permissions"] == initial_by_id[section_id]


def test_generation_permission_audit_does_not_block_candidate_save(tmp_path):
    result, _ = run_blocked_candidate(tmp_path)
    assert result["status"] == "completed"
    assert result["candidate_saved"] is True
    assert result["candidate_available"] is True
    assert result["candidate_reviewable"] is True
    assert result["submission_ready"] is False
    assert Path(result["candidate_artifacts"]["json_path"]).is_file()
    assert Path(result["candidate_artifacts"]["markdown_path"]).is_file()


def test_generation_permission_audit_is_in_run_report(tmp_path):
    result, _ = run_blocked_candidate(tmp_path)
    report = result["generation_permission_audit"]
    assert report["passed"] is False
    assert report["unauthorized_numeric_claims"]
    run_report = json.loads(
        Path(result["outputs"]["run_json"]).read_text(encoding="utf-8")
    )
    assert run_report["generation_permission_audit"] == report


def _dynamic_chapter_ledger() -> list[dict]:
    writing = {
        "response_point_ids": [],
        "score_item_refs": [],
        "manual_project_facts": {"need": "none", "fact_types": []},
        "web": {"need": "none"},
    }
    value = {
        "chapter_id": "CH-DYNAMIC",
        "title": "动态许可章节",
        "writing_requirements": writing,
        "children": [
            {
                "chapter_id": "CH-DYNAMIC-01",
                "title": "实施总体规划",
                "writing_requirements": {**writing, "response_point_ids": ["REQ-A"]},
                "children": [],
            },
            {
                "chapter_id": "CH-DYNAMIC-02",
                "title": "安装部署",
                "writing_requirements": {**writing, "response_point_ids": ["REQ-OTHER"]},
                "children": [],
            },
        ],
    }
    context = {
        "tender_requirements": {
            "requirements": [
                {
                    "point_id": "REQ-A",
                    "original_text": "货到时间为自合同签订之日起42日内。",
                },
                {
                    "point_id": "REQ-B",
                    "original_text": "安装完成时间为货到之日起13日内。",
                },
                {
                    "point_id": "REQ-DOC",
                    "original_text": "投标人应提供完整的使用手册和维修手册。",
                },
                {
                    "point_id": "REQ-LOC",
                    "original_text": "全部货物交付至采购人指定地点，并提供制造商完整随机资料、使用手册和维修手册。",
                },
            ]
        },
        "confirmed_project_facts": {"facts": {}, "fact_records": {}},
        "draft_plan": {"draft_units": []},
        "task_information": {"tasks": []},
        "tender_tables": [],
    }
    return _build_section_fact_ledger(
        original_context=context,
        target=value,
        pending_confirmations=[],
        local_evidence_refs=[],
    )


def _dynamic_draft(statement: str, *, section_index: int = 1, claims=None) -> dict:
    section_id = f"CH-DYNAMIC-0{section_index + 1}"
    heading = ("实施总体规划", "安装部署")[section_index]
    return {
        "chapter_title": "动态许可章节",
        "sections": [
            {
                "section_id": section_id,
                "heading": heading,
                "block_type": "implementation_plan",
                "content": prose(statement),
                "covered_score_item_ids": [],
                "covered_requirement_ids": [],
                "placeholder_ids": [],
            }
        ],
        "claims": claims or [],
        "placeholders_used": [],
        "score_items_covered": [],
        "requirements_covered": [],
        "warnings": [],
    }


def test_chapter_level_tender_numeric_permission_is_not_section_bound():
    value = _dynamic_draft(
        "安装工作按照招标规定的货到后13日时限组织实施。",
        claims=[tender_claim("安装完成时间为货到之日起13日内。", "REQ-B")],
    )
    result = audit_generation_permissions(value, fact_ledger=_dynamic_chapter_ledger())
    assert result["unauthorized_numeric_claims"] == []


def test_tender_numeric_permission_is_built_from_current_project_facts():
    items = _dynamic_chapter_ledger()[0]["chapter_allowed_tender_numeric_facts"]
    assert {(item["value"], item["unit"], item["source_id"]) for item in items} >= {
        (42, "日", "REQ-A"),
        (13, "日", "REQ-B"),
    }


def test_production_rule_does_not_hardcode_numeric_value_or_source_id():
    source = inspect.getsource(_build_section_fact_ledger)
    assert "TR-006" not in source and "TR-007" not in source
    assert "30日" not in source and "10日" not in source


def test_same_tender_fact_can_be_used_in_related_chapter_sections():
    claim = tender_claim("货到时间为自合同签订之日起42日内。", "REQ-A")
    for section_index in (0, 1):
        value = _dynamic_draft(
            "根据招标要求，货到时间为自合同签订之日起42日内。",
            section_index=section_index,
            claims=[claim],
        )
        assert audit_generation_permissions(
            value, fact_ledger=_dynamic_chapter_ledger()
        )["unauthorized_numeric_claims"] == []


def test_same_numeric_value_with_different_business_semantics_is_blocked():
    value = _dynamic_draft(
        "整改报告在42日内提交。",
        claims=[tender_claim("货到时间为自合同签订之日起42日内。", "REQ-A")],
    )
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"]


def test_valid_tender_value_and_extra_improvement_commitment_are_evaluated_separately():
    statement = "我方按照招标要求在合同签订后42日内完成交货，并尽力提前完成。"
    value = _dynamic_draft(
        statement,
        claims=[tender_claim("货到时间为自合同签订之日起42日内。", "REQ-A")],
    )
    errors = inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        generation_permissions=_dynamic_chapter_ledger(),
    )
    assert not any(":numeric_commitment:" in item for item in errors)
    assert any(":unconfirmed_bidder_commitment:" in item for item in errors)


def test_chapter_level_tender_obligation_is_not_section_bound():
    statement = "根据招标要求，我方提供完整的使用手册和维修手册。"
    value = _dynamic_draft(
        statement,
        claims=[tender_claim("投标人应提供完整的使用手册和维修手册。", "REQ-DOC")],
    )
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        generation_permissions=_dynamic_chapter_ledger(),
    ) == []


def test_tender_required_documents_are_not_unconfirmed_commitment():
    test_chapter_level_tender_obligation_is_not_section_bound()


def test_tender_required_obligation_with_bidder_response_wording_is_allowed():
    statement = "我方响应招标要求并提供制造商完整的随机资料，包括使用手册和维修手册。"
    value = _dynamic_draft(
        statement,
        claims=[tender_claim("投标人应提供完整的使用手册和维修手册。", "REQ-DOC")],
    )
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        generation_permissions=_dynamic_chapter_ledger(),
    ) == []


def test_extra_commitment_appended_to_tender_obligation_is_still_blocked():
    statement = "我方提供完整的使用手册和维修手册，并额外免费提供设备。"
    value = _dynamic_draft(
        statement,
        claims=[tender_claim("投标人应提供完整的使用手册和维修手册。", "REQ-DOC")],
    )
    assert any(
        ":unconfirmed_bidder_commitment:" in item
        for item in inspect_unconfirmed_commitments(
            value,
            confirmed_facts={},
            generation_permissions=_dynamic_chapter_ledger(),
        )
    )


def _ledger_message_payload(call: dict) -> dict:
    content = next(
        str(item["content"])
        for item in call["messages"]
        if item["role"] == "developer"
        and "[Section-specific Fact Ledger]" in str(item["content"])
    )
    return json.loads(content.split("\n", 1)[1])


def test_initial_generation_receives_chapter_fact_permissions(tmp_path):
    _, model = run_blocked_candidate(tmp_path)
    payload = _ledger_message_payload(model.calls[0])
    assert payload["chapter_fact_permissions"]["chapter_allowed_tender_facts"]
    assert payload["chapter_fact_permissions"]["chapter_allowed_tender_numeric_facts"]


def test_initial_generation_receives_same_fact_boundaries_as_fresh(tmp_path):
    _, model = run_blocked_candidate(tmp_path)
    initial = _ledger_message_payload(model.calls[0])
    fresh = _compiled_permission_payload(model.calls[-1])
    initial_by_id = {
        item["section_id"]: item["generation_permissions"]
        for item in initial["section_fact_ledger"]
    }
    assert all(
        section["effective_writing_permissions"] == initial_by_id[section_id]
        for section_id, section in fresh["section_contexts"].items()
    )


def test_initial_prompt_explicitly_forbids_industry_assumption_filling(tmp_path):
    _, model = run_blocked_candidate(tmp_path)
    prompt = "\n".join(str(item.get("content") or "") for item in model.calls[0]["messages"])
    assert "不得为了正文完整、专业性或行业惯例补充未确认事实" in prompt


def test_initial_generation_cannot_add_unlisted_numeric_commitment():
    assert audit(draft("收到问题后2小时内提交报告。"))["unauthorized_numeric_claims"]


def test_initial_generation_cannot_add_unlisted_product_or_resource_claim():
    result = audit(draft("本系统支持MLAG，项目组设置备份人员并由另一名工程师复核。"))
    assert result["unauthorized_product_capabilities"]
    assert result["unauthorized_personnel_claims"]


def test_no_personnel_narrative_without_tender_or_confirmed_source():
    result = audit(draft("项目经理负责统筹，实施工程师负责现场配置。"))
    assert result["unauthorized_personnel_claims"] == []


def test_personnel_section_can_only_reuse_tender_table_and_registered_placeholders():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    fact_ledger = ledger(pending_items=pending(placeholder_id))
    permissions = fact_ledger[0]["generation_permissions"]
    assert permissions["allowed_generic_role_descriptions"] == []
    value = draft(
        f"拟派项目经理为{{{{{placeholder_id}}}}}。",
        placeholder_ids=[placeholder_id],
    )
    assert audit(value, fact_ledger)["unauthorized_personnel_claims"] == []


def test_candidate_personnel_material_is_not_paraphrased_before_confirmation():
    fact_ledger = ledger(
        local_refs=[
            {
                "candidate_id": "CAND-PERSON-1",
                "entity_type": "person",
                "selection_status": "candidate",
                "text": "候选简历记载某人员具有数据中心经验",
            }
        ]
    )
    result = audit_generation_permissions(
        draft("拟派人员具有数据中心部署经验。"),
        fact_ledger=fact_ledger,
    )
    assert result["unauthorized_personnel_claims"]


def test_double_check_staffing_is_blocked_without_source():
    result = audit(draft("关键配置由另一名工程师进行复核。"))
    assert any(
        item["code"] == "unconfirmed_resource_commitment"
        for item in result["unauthorized_personnel_claims"]
    )


def test_backup_staffing_and_replacement_commitments_are_blocked_without_source():
    result = audit(draft("项目设置备份人员，并建立人员替换机制。"))
    assert result["unauthorized_personnel_claims"]


def test_score_coverage_statement_is_not_written_as_business_claim():
    message = _section_fact_ledger_message(fact_ledger=ledger())
    assert "评分覆盖只写入" in message
    assert "不得写成商务事实正文" in message


def test_score_coverage_metadata_is_excluded_from_fact_boundary_audit():
    text = "本实施部署方案完整覆盖项目建设技术方案并满足实施部署评价要求。"
    value = draft(
        "",
        claims=[
            {
                "text": text,
                "claim_type": "score_coverage_statement",
                "source_ids": [],
                "evidence_ids": [],
                "human_review_required": False,
            }
        ],
    )
    assert inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        generation_permissions=ledger(),
    ) == []
    assert audit(value)["passed"] is True


def test_real_company_product_or_personnel_claim_still_requires_evidence():
    value = draft("本系统支持在线扩展，拟派人员具有五年实施经验。")
    result = audit(value)
    assert result["unauthorized_product_capabilities"]
    assert result["unauthorized_personnel_claims"]


def test_chapter_permissions_are_used_by_initial_and_fresh_audits(tmp_path):
    result, model = run_blocked_candidate(tmp_path)
    initial = result["initial_candidate_validation"]
    assert initial["chapter_tender_numeric_permission_count"] > 0
    assert initial["effective_tender_numeric_fact_ids_by_section"]
    compiled = _compiled_permission_payload(model.calls[-1])
    assert compiled["chapter_effective_tender_numeric_facts"]


def test_run_report_contains_effective_chapter_permission_ids(tmp_path):
    result, _ = run_blocked_candidate(tmp_path)
    assert result["chapter_tender_fact_permission_count"] > 0
    assert result["chapter_tender_numeric_permission_count"] > 0
    ids = result["effective_tender_numeric_fact_ids_by_section"]
    assert any("TR-006" in values for values in ids.values())


def test_tender_numeric_match_ignores_spacing_between_number_and_unit():
    value = _dynamic_draft(
        "根据招标要求，货到时间为自合同签订之日起42 日内。",
        section_index=0,
        claims=[tender_claim("货到时间为自合同签订之日起42日内。", "REQ-A")],
    )
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"] == []


def test_tender_calendar_days_and_working_days_are_not_equivalent():
    value = _dynamic_draft(
        "安装工作在货到后13个工作日内完成。",
        claims=[tender_claim("安装完成时间为货到之日起13日内。", "REQ-B")],
    )
    findings = audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"]
    assert findings and findings[0]["offending_span"] == "13个工作日内"


def test_tender_numeric_match_requires_same_start_event():
    value = _dynamic_draft(
        "验收后13日内完成安装。",
        claims=[tender_claim("安装完成时间为货到之日起13日内。", "REQ-B")],
    )
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"]


def test_tender_numeric_match_requires_same_completion_object():
    value = _dynamic_draft(
        "货到后13日内完成安装调试并具备试运行条件。",
        claims=[tender_claim("安装完成时间为货到之日起13日内。", "REQ-B")],
    )
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"]


def _two_tender_numeric_claims() -> list[dict]:
    return [
        tender_claim("货到时间为自合同签订之日起42日内。", "REQ-A"),
        tender_claim("安装完成时间为货到之日起13日内。", "REQ-B"),
    ]


def test_allowed_tender_numeric_span_is_not_blocked_by_resource_claim_in_same_sentence():
    value = _dynamic_draft(
        "启动预案并调配后备资源，守牢合同签订后42日到货和货到后13日安装完成的刚性边界。",
        claims=_two_tender_numeric_claims(),
    )
    result = audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )
    assert result["unauthorized_numeric_claims"] == []
    assert any(
        item["code"] == "unconfirmed_resource_commitment"
        for item in result["unauthorized_personnel_claims"]
    )


def test_tender_obligation_and_extra_obligation_are_audited_separately():
    statement = (
        "按照招标要求提供使用手册和维修手册，同时提供安装指南和合格证。"
    )
    value = _dynamic_draft(
        statement,
        claims=[tender_claim("投标人应提供完整的使用手册和维修手册。", "REQ-DOC")],
    )
    findings = audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_bidder_commitments"]
    assert {item["offending_span"] for item in findings} == {"安装指南", "合格证"}
    assert all(item["allowed_spans"] == ["使用手册", "维修手册"] for item in findings)


def test_including_but_not_limited_to_does_not_expand_tender_permission():
    value = _dynamic_draft(
        "按照招标要求提供资料，包括但不限于使用手册和维修手册。",
        claims=[tender_claim("投标人应提供完整的使用手册和维修手册。", "REQ-DOC")],
    )
    findings = audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_bidder_commitments"]
    assert any(item["offending_span"] == "包括但不限于" for item in findings)


def test_unsupported_result_guarantee_is_blocked_separately():
    value = _dynamic_draft(
        "按照招标要求提供使用手册和维修手册，确保采购人能够独立完成日常维护。",
        claims=[tender_claim("投标人应提供完整的使用手册和维修手册。", "REQ-DOC")],
    )
    findings = audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_bidder_commitments"]
    assert any("确保采购人" in item["offending_span"] for item in findings)


def test_issue_reports_exact_unsupported_span():
    value = _dynamic_draft(
        "启动预案并调配后备资源，守牢合同签订后42日到货的刚性边界。",
        claims=[tender_claim("货到时间为自合同签订之日起42日内。", "REQ-A")],
    )
    validation = validate_technical_chapter(
        value,
        schema=load_chapter_draft_schema(),
        target={"title": "动态许可章节", "children": []},
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        generation_permissions=_dynamic_chapter_ledger(),
    )
    issue_value = next(
        item
        for item in validation["issues"]
        if item["code"] == "unconfirmed_resource_commitment"
    )
    assert issue_value["offending_span"] == "调配后备资源"
    assert "42日" in issue_value["allowed_spans"]
    assert issue_value["matched_tender_fact_ids"] == ["REQ-A"]


def test_personnel_content_mode_is_source_only_without_tender_or_confirmed_source():
    assert ledger()[0]["personnel_content_mode"] == "source_only"


def test_source_only_personnel_finalization_removes_model_narrative():
    value, report = _apply_personnel_content_gate(
        draft("项目经理具有丰富经验，必要时设置备份人员。"),
        fact_ledger=ledger(),
    )
    assert value["sections"][0]["content"] == ""
    assert report["applied"] is True


def test_source_only_personnel_finalization_preserves_tender_table():
    value = draft()
    value["sections"][0]["content"] = (
        "具体人员填报结构见下表。\n\n"
        "| 姓名 | 岗位 |\n| --- | --- |\n| 待填 | 待填 |"
    )
    fact_ledger = ledger()
    fact_ledger[0]["generation_permissions"][
        "required_response_table_contracts"
    ] = [
        {
            "table_id": "T-PERSON",
            "column_contract": {"headers": ["姓名", "岗位"]},
        }
    ]
    gated, _ = _apply_personnel_content_gate(value, fact_ledger=fact_ledger)
    assert "| 姓名 | 岗位 |" in gated["sections"][0]["content"]
    assert "见下表" in gated["sections"][0]["content"]


def test_source_only_personnel_finalization_preserves_registered_placeholders():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    fact_ledger = ledger(pending_items=pending(placeholder_id))
    value = draft(
        f"拟派项目经理为{{{{{placeholder_id}}}}}。项目经理具有丰富经验。",
        placeholder_ids=[placeholder_id],
    )
    gated, _ = _apply_personnel_content_gate(value, fact_ledger=fact_ledger)
    assert f"{{{{{placeholder_id}}}}}" in gated["sections"][0]["content"]
    assert "丰富经验" not in gated["sections"][0]["content"]


def test_source_only_personnel_section_does_not_inject_generic_role_duties():
    gated, _ = _apply_personnel_content_gate(
        draft("项目经理负责统筹，实施工程师负责设备配置。"),
        fact_ledger=ledger(),
    )
    assert "项目经理负责统筹" in gated["sections"][0]["content"]
    assert "实施工程师负责设备配置" in gated["sections"][0]["content"]


def test_personnel_heading_does_not_count_as_personnel_source():
    entry = ledger()[0]
    assert entry["section_title"] == SECTION_TITLE
    assert entry["personnel_content_mode"] == "source_only"


def test_personnel_experience_without_candidate_binding_is_not_candidate_promoted():
    errors = inspect_unconfirmed_commitments(
        draft("项目经理具有丰富IT系统集成经验。"),
        confirmed_facts={},
        candidate_evidence_ids={"CAND-PERSON-1"},
    )
    assert any("unconfirmed_personnel_qualification" in item for item in errors)
    assert not any("candidate_promoted_to_fact" in item for item in errors)


def test_candidate_promoted_requires_candidate_evidence_id():
    sentence = "张三持有PMP证书并具有项目管理经验。"
    value = draft(
        sentence,
        claims=[
            {
                "text": sentence,
                "claim_type": "confirmed_enterprise_fact",
                "source_ids": [],
                "evidence_ids": ["CAND-PERSON-1"],
                "human_review_required": True,
            }
        ],
    )
    errors = inspect_unconfirmed_commitments(
        value,
        confirmed_facts={},
        candidate_evidence_ids={"CAND-PERSON-1"},
    )
    assert any("candidate_promoted_to_fact" in item for item in errors)


def test_required_project_fact_pending_issue_uses_stable_error_code():
    required_target = deepcopy(target())
    required_target["writing_requirements"]["manual_project_facts"] = {
        "need": "required",
        "fact_types": ["project_fact"],
    }
    result = validate_technical_chapter(
        draft(),
        schema=load_chapter_draft_schema(),
        target=required_target,
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        generation_permissions=ledger(),
    )
    issue_value = next(
        item
        for item in result["issues"]
        if item["code"]
        == "required_project_fact_pending_confirmation_missing"
    )
    assert issue_value["message"].startswith("required project facts are unconfirmed")


def test_tender_baseline_delivery_does_not_require_pending_but_installation_does():
    delivery_target = deepcopy(target())
    delivery_target["writing_requirements"]["manual_project_facts"] = {
        "need": "required", "fact_types": ["delivery_time"]
    }
    delivery = validate_technical_chapter(
        draft(),
        schema=load_chapter_draft_schema(),
        target=delivery_target,
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        generation_permissions=ledger(),
    )
    assert not any(
        issue["code"] == "required_project_fact_pending_confirmation_missing"
        for issue in delivery["issues"]
    )
    installation_target = deepcopy(target())
    installation_target["writing_requirements"]["manual_project_facts"] = {
        "need": "required", "fact_types": ["installation_time"]
    }
    installation = validate_technical_chapter(
        draft(),
        schema=load_chapter_draft_schema(),
        target=installation_target,
        confirmed_facts={},
        pending_confirmations=[],
        tender_tables=[],
        generation_permissions=ledger(),
    )
    assert any(
        issue["code"] == "required_project_fact_pending_confirmation_missing"
        for issue in installation["issues"]
    )


def test_issue_message_is_not_used_as_error_code():
    message = "required project facts are unconfirmed but no pending confirmation was recorded"
    details = _fresh_quality_error_details([message], draft=draft())
    assert details[0]["error_code"] == (
        "required_project_fact_pending_confirmation_missing"
    )
    assert details[0]["error_code"] != message


def test_numeric_matcher_receives_full_sentence_context():
    sentence = "守牢合同签订后42日到货和货到后13日安装完成的招标期限。"
    result = audit_generation_permissions(
        _dynamic_draft(sentence, claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    traces = [
        item
        for item in result["matched_tender_fact_spans"]
        if item.get("numeric_span")
    ]
    assert traces
    assert all(item["full_sentence"] == sentence for item in traces)
    assert all(item["normalized_sentence"] == sentence for item in traces)


def test_exact_tender_numeric_span_populates_matched_source_ids():
    result = audit_generation_permissions(
        _dynamic_draft("合同签订后42日内完成到货。", claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    trace = next(
        item
        for item in result["matched_tender_fact_spans"]
        if "42日" in item.get("numeric_span", "")
    )
    assert trace["matched_tender_fact_ids"] == ["REQ-A"]
    assert trace["blocked_spans"] == []


def test_exact_tender_numeric_in_summary_section_is_allowed():
    value = _dynamic_draft(
        "守牢合同签订后42日到货和货到后13日安装完成的招标期限。",
        section_index=0,
    )
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"] == []


def test_exact_tender_numeric_in_schedule_section_is_allowed():
    value = _dynamic_draft("我方响应合同签订后42日内完成到货。", claims=[])
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"] == []


def test_exact_tender_numeric_in_installation_section_is_allowed():
    value = _dynamic_draft("货到后13日内完成安装。", claims=[])
    assert audit_generation_permissions(
        value, fact_ledger=_dynamic_chapter_ledger()
    )["unauthorized_numeric_claims"] == []


def test_numeric_span_without_matching_start_event_is_blocked():
    result = audit_generation_permissions(
        _dynamic_draft("验收后13日内完成安装。", claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_numeric_claims"]


def test_numeric_span_without_matching_completion_object_is_blocked():
    result = audit_generation_permissions(
        _dynamic_draft("合同签订后42日内完成测试。", claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_numeric_claims"]


def test_calendar_day_and_working_day_remain_distinct():
    result = audit_generation_permissions(
        _dynamic_draft("合同签订后42个工作日内完成到货。", claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_numeric_claims"]


def test_mixed_sentence_allows_tender_numeric_spans():
    sentence = "启动风险预案并调配后备资源，守牢合同签订后42日到货和货到后13日安装完成的刚性边界。"
    result = audit_generation_permissions(
        _dynamic_draft(sentence, claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_numeric_claims"] == []


def test_mixed_sentence_blocks_only_resource_commitment_span():
    sentence = "启动风险预案并调配后备资源，守牢合同签订后42日到货和货到后13日安装完成的刚性边界。"
    result = audit_generation_permissions(
        _dynamic_draft(sentence, claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    finding = next(
        item
        for item in result["unauthorized_personnel_claims"]
        if item["code"] == "unconfirmed_resource_commitment"
    )
    assert finding["offending_span"] == "调配后备资源"
    assert finding["matched_tender_fact_ids"] == ["REQ-A", "REQ-B"]
    assert finding["blocked_spans"] == ["调配后备资源"]


def test_allowed_numeric_span_is_not_repeated_as_numeric_issue():
    result = audit_generation_permissions(
        _dynamic_draft(
            "调配后备资源，守牢合同签订后42日到货的招标期限。",
            claims=[],
        ),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_numeric_claims"] == []


def test_tender_delivery_location_obligation_matches_source():
    sentence = "我方承诺将全部货物交付至采购人指定地点。"
    result = audit_generation_permissions(
        _dynamic_draft(sentence, claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_bidder_commitments"] == []
    assert any(
        "REQ-LOC" in item["matched_tender_fact_ids"]
        for item in result["matched_tender_fact_spans"]
    )


def test_tender_random_document_obligation_matches_source():
    sentence = "我方提供制造商完整的随机资料。"
    result = audit_generation_permissions(
        _dynamic_draft(sentence, claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_bidder_commitments"] == []


def test_tender_manual_obligations_are_not_unconfirmed_commitments():
    errors = inspect_unconfirmed_commitments(
        _dynamic_draft("我方承诺提供完整的使用手册和维修手册。", claims=[]),
        confirmed_facts={},
        generation_permissions=_dynamic_chapter_ledger(),
    )
    assert not any(":unconfirmed_bidder_commitment:" in item for item in errors)


def test_bidder_response_wording_does_not_override_source_match():
    test_tender_manual_obligations_are_not_unconfirmed_commitments()


def test_extra_document_item_is_blocked_separately():
    result = audit_generation_permissions(
        _dynamic_draft(
            "我方提供使用手册、维修手册和额外安装指南。", claims=[]
        ),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert {item["offending_span"] for item in result["unauthorized_bidder_commitments"]} == {"安装指南"}


def test_result_guarantee_is_blocked_separately():
    result = audit_generation_permissions(
        _dynamic_draft(
            "我方提供使用手册和维修手册，确保采购人能够独立完成维护。",
            claims=[],
        ),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert any(
        "确保采购人" in item["offending_span"]
        for item in result["unauthorized_bidder_commitments"]
    )


def test_plain_deng_does_not_expand_obligation_without_extra_item():
    result = audit_generation_permissions(
        _dynamic_draft("我方提供完整的使用手册和维修手册等。", claims=[]),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert result["unauthorized_bidder_commitments"] == []


def test_including_but_not_limited_to_remains_expansion_signal():
    result = audit_generation_permissions(
        _dynamic_draft(
            "我方提供包括但不限于使用手册和维修手册。", claims=[]
        ),
        fact_ledger=_dynamic_chapter_ledger(),
    )
    assert any(
        item["offending_span"] == "包括但不限于"
        for item in result["unauthorized_bidder_commitments"]
    )


def _pending_process_claim(text: str) -> dict:
    return {
        "text": text,
        "claim_type": "proposal_statement",
        "source_ids": [],
        "evidence_ids": [],
        "deviation_status": "evidence_pending",
    }


def test_project_stage_plan_is_not_objective_evidence_pending():
    text = "项目实施将划分为需求确认、设备交付、安装部署、系统联调、试运行和验收交付等阶段。"
    assert inspect_deviation_issues(
        {"claims": [_pending_process_claim(text)]}
    ) == []


def test_implementation_process_statement_does_not_require_company_evidence():
    text = "项目实施按照需求确认、设备交付、安装部署、联调测试、试运行和验收交付等阶段组织推进。"
    assert inspect_deviation_issues(
        {"claims": [_pending_process_claim(text)]}
    ) == []


def test_company_maturity_claim_still_requires_evidence():
    text = "我公司拥有成熟的需求确认、安装部署和验收交付实施体系。"
    assert inspect_deviation_issues(
        {"claims": [_pending_process_claim(text)]}
    )


def test_past_project_process_claim_still_requires_evidence():
    text = "我公司已在多个医院项目中采用需求确认、安装部署和验收交付流程。"
    assert inspect_deviation_issues(
        {"claims": [_pending_process_claim(text)]}
    )


def test_process_statement_with_unconfirmed_resource_claim_is_partially_blocked():
    text = "项目按照需求确认、安装部署和验收交付阶段推进，并调配后备资源。"
    value = _dynamic_draft(text, claims=[_pending_process_claim(text)])
    errors = inspect_unconfirmed_commitments(value, confirmed_facts={})
    assert any(":unconfirmed_resource_commitment:" in item for item in errors)
    assert inspect_deviation_issues(value)


def _all_section_source_only_ledger(*, pending_items=None) -> list[dict]:
    writing = {
        "response_point_ids": ["REQ-A"],
        "score_item_refs": [],
        "manual_project_facts": {"need": "none", "fact_types": []},
        "web": {"need": "none"},
    }
    chapter_target = {
        "chapter_id": "CH-ALL-GATE",
        "title": "全章人员门控",
        "writing_requirements": writing,
        "children": [
            {
                "chapter_id": "CH-ALL-GATE-01",
                "title": "实施总体规划",
                "writing_requirements": writing,
                "children": [],
            },
            {
                "chapter_id": "CH-ALL-GATE-02",
                "title": "风险控制",
                "writing_requirements": writing,
                "children": [],
            },
            {
                "chapter_id": "CH-ALL-GATE-03",
                "title": "项目负责人及主要人员",
                "writing_requirements": writing,
                "children": [],
            },
        ],
    }
    context = {
        "tender_requirements": {
            "requirements": [
                {
                    "point_id": "REQ-A",
                    "original_text": "货到时间以合同约定为准。",
                }
            ]
        },
        "confirmed_project_facts": {"facts": {}, "fact_records": {}},
        "draft_plan": {"draft_units": []},
        "task_information": {"tasks": []},
        "tender_tables": [],
    }
    return _build_section_fact_ledger(
        original_context=context,
        target=chapter_target,
        pending_confirmations=pending_items or [],
        local_evidence_refs=[],
    )


def _all_section_gate_draft() -> dict:
    return {
        "chapter_title": "全章人员门控",
        "sections": [
            {
                "section_id": "CH-ALL-GATE-01",
                "heading": "实施总体规划",
                "content": "我公司将组建具备医院信息化经验的专项团队，设置项目经理及质量保障组。\n\n实施工作按照确认、交付和记录流程推进。",
                "placeholder_ids": [],
            },
            {
                "section_id": "CH-ALL-GATE-02",
                "heading": "风险控制",
                "content": "必要时设置备份人员并调配后备资源。\n\n风险发现后登记影响并执行确认后的处置流程。",
                "placeholder_ids": [],
            },
            {
                "section_id": "CH-ALL-GATE-03",
                "heading": "项目负责人及主要人员",
                "content": "项目经理具有丰富经验并负责统筹协调。",
                "placeholder_ids": [],
            },
        ],
        "claims": [],
        "placeholders_used": [],
    }


def test_source_only_personnel_gate_applies_to_all_chapter_sections():
    fact_ledger = _all_section_source_only_ledger()
    assert {item["personnel_content_mode"] for item in fact_ledger} == {
        "source_only"
    }
    gated, report = _apply_personnel_content_gate(
        _all_section_gate_draft(), fact_ledger=fact_ledger
    )
    assert set(report["source_only_section_ids"]) == {
        "CH-ALL-GATE-01",
        "CH-ALL-GATE-02",
        "CH-ALL-GATE-03",
    }
    assert all("项目经理" not in item["content"] for item in gated["sections"])


def test_source_only_removes_team_claim_from_overview_section():
    gated, _ = _apply_personnel_content_gate(
        _all_section_gate_draft(), fact_ledger=_all_section_source_only_ledger()
    )
    overview = gated["sections"][0]["content"]
    assert "专项团队" not in overview and "质量保障组" not in overview
    assert "实施工作按照" in overview


def test_source_only_removes_staffing_claim_from_risk_section():
    gated, _ = _apply_personnel_content_gate(
        _all_section_gate_draft(), fact_ledger=_all_section_source_only_ledger()
    )
    risk = gated["sections"][1]["content"]
    assert "备份人员" not in risk and "后备资源" not in risk
    assert "风险发现后" in risk


def test_source_only_preserves_tender_personnel_table_in_any_section():
    value = _all_section_gate_draft()
    value["sections"][0]["content"] = (
        "人员填报字段见下表。\n\n| 姓名 | 岗位 |\n| --- | --- |\n| 待填 | 待填 |"
    )
    gated, _ = _apply_personnel_content_gate(
        value, fact_ledger=_all_section_source_only_ledger()
    )
    assert "| 姓名 | 岗位 |" in gated["sections"][0]["content"]


def test_source_only_preserves_registered_personnel_placeholder_in_any_section():
    placeholder_id = "PH-FIELD-PROJECT-MANAGER"
    pending_items = [
        {
            "field": "项目经理",
            "placeholder_id": placeholder_id,
            "placeholder_token": f"{{{{{placeholder_id}}}}}",
            "affected_sections": ["CH-ALL-GATE-01"],
            "blocking": True,
        }
    ]
    value = _all_section_gate_draft()
    value["sections"][0]["content"] = f"拟派项目经理为{{{{{placeholder_id}}}}}。"
    value["sections"][0]["placeholder_ids"] = [placeholder_id]
    value["placeholders_used"] = [placeholder_id]
    gated, _ = _apply_personnel_content_gate(
        value,
        fact_ledger=_all_section_source_only_ledger(
            pending_items=pending_items
        ),
    )
    assert f"{{{{{placeholder_id}}}}}" in gated["sections"][0]["content"]


def test_source_only_does_not_treat_heading_or_table_field_as_narrative_claim():
    value = _all_section_gate_draft()
    value["sections"][0]["content"] = (
        "## 项目经理填报表\n\n| 项目经理 | 证书字段 |\n| --- | --- |\n| 待填 | 待填 |"
    )
    gated, _ = _apply_personnel_content_gate(
        value, fact_ledger=_all_section_source_only_ledger()
    )
    content = gated["sections"][0]["content"]
    assert "## 项目经理填报表" in content
    assert "| 项目经理 | 证书字段 |" in content

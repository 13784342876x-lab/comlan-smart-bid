import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.technical_writing_agent as technical_writing_agent_module
from app.llm_errors import ModelJsonParseError
from app.llm_client import ToolModelResult, empty_usage
from app.technical_agent_console import format_agent_event
from app.technical_placeholders import (
    attach_placeholder_identity,
    placeholder_id_for_field,
    placeholder_token,
)
from app.technical_user_instruction import (
    classify_revision_scope,
    collect_user_instructions,
)
from app.technical_writing_quality import (
    inspect_markdown_tables,
    normalize_tender_table,
    review_chapter_quality,
)
from app.technical_writing_rules import (
    load_writing_rules,
    normalize_persistent_constraint,
    suggest_instruction_scope,
)
from app.technical_writing_agent import (
    SYSTEM_PROMPT,
    TechnicalWritingAgentInput,
    _build_section_fact_ledger,
    build_markdown_change_summary,
    build_technical_writing_agent,
    validate_technical_chapter,
)
from app.technical_writing_tools import TechnicalWritingToolContext, build_technical_tool_registry


class EmptyMetadataStore:
    def load(self):
        return {"files": []}

    def get_file(self, file_id):
        raise KeyError(file_id)


class FakeWebProvider:
    name = "fake_web"

    def search(self, query, options):
        suffix = "two" if "第二" in query else "one"
        return {
            "status": "success",
            "provider": self.name,
            "results": [
                {
                    "title": f"官方标准-{suffix}",
                    "url": f"https://www.samr.gov.cn/standard/{suffix}",
                    "snippet": "fake",
                    "publisher": "标准主管部门",
                    "published_at": None,
                    "source_type": "standards_authority_official",
                    "retrieved_at": "2026-07-24T00:00:00+08:00",
                }
            ],
        }


def write_generic_inputs(root: Path, *, with_table=False):
    response = {
        "chapters": [
            {
                "chapter_id": "SRC-001",
                "response_points": [
                    {
                        "point_id": "RP-001",
                        "original_text": "应说明项目准备和执行安排。",
                        "mandatory_marker": "required",
                        "content_type": "narrative",
                        "source_refs": [{"file": "requirements.pdf", "page": 1}],
                        **(
                            {
                                "table": {
                                    "title": "阶段工作安排表",
                                    "headers": ["阶段", "工作内容", "交付物"],
                                    "rows": [],
                                    "filling_requirements": ["按实施顺序填写"],
                                    "requires_bidder_fill": True,
                                }
                            }
                            if with_table
                            else {}
                        ),
                    }
                ],
            }
        ]
    }
    web = {
        "need": "optional",
        "allowed_source_types": ["standards_authority_official"],
        "purposes": ["国家或行业标准核验"],
    }
    writing = {
        "score_items": [{"score_item_id": "SCORE-001", "response_point_id": "RP-001"}],
        "chapters": [
            {
                "chapter_id": "CH-001",
                "title": "项目交付计划",
                "writing_requirements": {
                    "response_point_ids": ["RP-001"],
                    "score_item_refs": ["SCORE-001"],
                    "manual_project_facts": {"need": "none", "fact_types": []},
                    "web": web,
                },
                "children": [
                    {
                        "chapter_id": "CH-001-01",
                        "title": "准备阶段",
                        "writing_requirements": {
                            "response_point_ids": ["RP-001"],
                            "score_item_refs": ["SCORE-001"],
                            "manual_project_facts": {"need": "none", "fact_types": []},
                            "web": web,
                        },
                        "children": [],
                    },
                    {
                        "chapter_id": "CH-001-02",
                        "title": "执行阶段",
                        "writing_requirements": {
                            "response_point_ids": ["RP-001"],
                            "score_item_refs": ["SCORE-001"],
                            "manual_project_facts": {"need": "none", "fact_types": []},
                            "web": web,
                        },
                        "children": [],
                    },
                ],
            }
        ],
    }
    tasks = {"tasks": []}
    paths = {}
    for name, value in (("response", response), ("writing", writing), ("tasks", tasks)):
        path = root / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    return paths


def generic_draft(*, revision=False, table=False):
    first = (
        "准备阶段以建立可执行的工作基线为目标，在招标文件、现场条件和责任接口已经核对的"
        "前提下，由项目团队确认输入资料、工作边界及协同关系，并形成可追踪的启动记录。"
        "\n\n实施前还需复核资料完整性和接口条件；发现缺项时登记问题、明确责任和关闭判据，"
        "只有必要输入通过检查后才进入下一阶段，从而保证后续工作具有一致的执行依据。"
    )
    second = (
        "执行阶段按照已确认的工作基线组织实施，各责任岗位依次完成操作、复核和过程记录，"
        "并通过阶段检查点确认方法、接口与成果状态，避免只描述原则而缺少实际执行闭环。"
        "\n\n遇到条件变化或检查不通过时，项目团队先隔离影响、分析原因并更新处置记录，"
        "待整改结果满足完成判据后再继续推进，最终汇总过程资料并形成可核验的交付成果。"
    )
    if revision:
        first = first.replace("建立可执行的工作基线", "统一并精简可执行的工作基线")
        second = second.replace("按照已确认的工作基线", "重新按照已确认的工作基线")
    if table:
        second += (
            "\n\n为便于横向核对执行动作与成果，以下阶段工作安排表按既定表头和字段顺序填写，"
            "用于集中列示阶段映射关系，填写边界不得超出招标要求。"
            "\n\n表1 阶段工作安排表"
            "\n\n| 阶段 | 工作内容 | 交付物 |\n| --- | --- | --- |\n| 执行 | 过程控制 | 过程记录 |"
            "\n\n该表仅用于辅助核对，实际执行仍以前述流程、检查点和问题关闭判据为准。"
        )
    result = {
        "chapter_title": "项目交付计划",
        "sections": [
            {
                "heading": "准备阶段",
                "block_type": "implementation_plan",
                "content": first,
                "covered_score_item_ids": ["SCORE-001"],
                "covered_requirement_ids": ["RP-001"],
                "placeholder_ids": [],
            },
            {
                "heading": "执行阶段",
                "block_type": "implementation_plan",
                "content": second,
                "covered_score_item_ids": ["SCORE-001"],
                "covered_requirement_ids": ["RP-001"],
                "placeholder_ids": [],
            },
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": ["SCORE-001"],
        "requirements_covered": ["RP-001"],
        "warnings": [],
    }
    if table:
        result["generated_supporting_tables"] = [
            {
                "section_id": "执行阶段",
                "title": "阶段工作安排表",
                "purpose": "横向映射执行动作与交付成果",
                "why_table_is_better_than_prose": "三类字段需要逐行对应，表格便于核对且不替代正文",
            }
        ]
    return result


class FinalModel:
    def __init__(self, draft):
        self.draft = draft
        self.messages = []
        self.tools = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        self.tools.append([item["function"]["name"] for item in tools])
        return ToolModelResult(
            "final",
            [],
            json.dumps(self.draft, ensure_ascii=False),
            {"provider": "fake", "model": "fake", "api_mode": "fake", "usage": empty_usage()},
        )


def make_agent(
    root,
    paths,
    model,
    *,
    instruction="",
    policy="auto",
    web_provider=None,
    max_web=3,
    instruction_scope="once",
    writing_rules_path=None,
    draft_units_path=None,
):
    return build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=paths["response"],
            writing_outline_path=paths["writing"],
            task_information_path=paths["tasks"],
            target_chapter_id="CH-001",
            output_key="generic_case",
            user_instruction=instruction,
            user_instruction_sources=({"type": "test"},) if instruction else (),
            user_instruction_summary=instruction[:100],
            web_search_policy=policy,
            instruction_scope=instruction_scope,
            writing_rules_path=writing_rules_path,
            draft_units_path=draft_units_path,
        ),
        llm=model,
        output_dir=root / "outputs",
        metadata_store=EmptyMetadataStore(),
        web_provider=web_provider or FakeWebProvider(),
        max_web_search_calls=max_web,
    )


class CountingWebProvider(FakeWebProvider):
    def __init__(self):
        self.calls = 0

    def search(self, query, options):
        self.calls += 1
        return super().search(query, options)


class JsonRepairModel:
    def __init__(self, initial_output, repair_output, *, first_web_tool=False):
        self.initial_output = initial_output
        self.repair_output = repair_output
        self.first_web_tool = first_web_tool
        self.turn = 0
        self.calls = []
        self.profile = SimpleNamespace(
            name="generic_profile",
            provider="openai_compatible",
            api_style="chat_completions",
            api_key="fake-api-key-never-report",
            max_output_tokens=1000,
            run_report_summary=lambda: {
                "profile_name": "generic_profile",
                "provider": "openai_compatible",
                "api_style": "chat_completions",
                "api_key_env": "FAKE_API_KEY",
            },
        )

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.turn += 1
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
                "kwargs": kwargs,
            }
        )
        metadata = {
            "provider": "fake",
            "profile_name": "generic_profile",
            "model": "fake",
            "api_mode": "chat_completions",
            "finish_status": "stop",
            "usage": empty_usage(),
        }
        if self.first_web_tool and self.turn == 1:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": "web-once",
                        "name": "web_search",
                        "arguments": {
                            "query": "国家标准",
                            "purpose": "国家或行业标准核验",
                            "allowed_source_types": ["standards_authority_official"],
                        },
                    }
                ],
                None,
                {**metadata, "finish_status": "tool_calls"},
            )
        if self.turn == (2 if self.first_web_tool else 1):
            return ToolModelResult("final", [], self.initial_output, metadata)
        return ToolModelResult("final", [], self.repair_output, metadata)


class ToolArgumentFailureThenRepairModel(JsonRepairModel):
    def run_with_tools(self, *, messages, tools, **kwargs):
        self.turn += 1
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
                "kwargs": kwargs,
            }
        )
        if self.turn == 1:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": "scope-once",
                        "name": "get_writing_scope",
                        "arguments": {"chapter_id": "CH-001", "section_id": None},
                    }
                ],
                None,
                {
                    "provider": "fake",
                    "profile_name": "generic_profile",
                    "api_mode": "chat_completions",
                    "finish_status": "tool_calls",
                    "usage": empty_usage(),
                },
            )
        if self.turn == 2:
            raise ModelJsonParseError(
                "tool arguments are not valid JSON: Expecting ',' delimiter",
                diagnostics={
                    "json_context": "tool_arguments",
                    "finish_reason": "tool_calls",
                    "content_character_count": 31,
                    "code_block_detected": False,
                    "suspected_truncation": True,
                    "json_error_summary": "Expecting ',' delimiter",
                },
                raw_content='{"query":"missing delimiter" "top_k":3}',
            )
        return ToolModelResult(
            "final",
            [],
            self.repair_output,
            {
                "provider": "fake",
                "profile_name": "generic_profile",
                "api_mode": "chat_completions",
                "finish_status": "stop",
                "usage": empty_usage(),
            },
        )


def test_fenced_final_json_uses_generic_parser_without_repair(tmp_path):
    paths = write_generic_inputs(tmp_path)
    fenced = "```json\n" + json.dumps(generic_draft(), ensure_ascii=False) + "\n```"
    model = JsonRepairModel(fenced, "unused")
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    assert result["model_json_repair"]["attempted"] is False
    assert model.turn == 1


def test_agent_local_json_syntax_repair_preserves_complete_section_text(tmp_path):
    paths = write_generic_inputs(tmp_path)
    expected = generic_draft()
    raw = json.dumps(expected, ensure_ascii=False)
    raw = raw.replace(r"\n", "\n", 1)
    raw = raw[:-1] + ",}"
    model = JsonRepairModel(raw, "unused")
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    assert result["model_json_repair"]["attempted"] is False
    assert result["fresh_final_generation"]["attempted"] is False
    assert model.turn == 1
    for expected_section, actual_section in zip(
        expected["sections"],
        result["model_final_output"]["sections"],
    ):
        assert hashlib.sha256(
            expected_section["content"].encode("utf-8")
        ).hexdigest() == hashlib.sha256(
            actual_section["content"].encode("utf-8")
        ).hexdigest()


@pytest.mark.parametrize(
    "invalid_output,expected_truncation",
    [
        (
            '{"chapter_title":"项目交付计划","sections":[{"content":"第一行\n第二行"}]}',
            False,
        ),
        (
            '{"chapter_title":"项目交付计划","sections":[',
            True,
        ),
    ],
)
def test_unrepairable_final_json_gets_one_clean_tool_free_fresh_final(
    tmp_path,
    invalid_output,
    expected_truncation,
):
    paths = write_generic_inputs(tmp_path)
    web = CountingWebProvider()
    model = JsonRepairModel(
        invalid_output,
        json.dumps(generic_draft(), ensure_ascii=False),
        first_web_tool=True,
    )
    events = []
    result = make_agent(
        tmp_path,
        paths,
        model,
        web_provider=web,
    ).run(event_handler=events.append)
    assert result["status"] == "completed"
    assert result["model_json_repair"]["attempted"] is False
    assert result["model_json_repair"]["disabled"] is True
    assert result["model_json_repair"]["suspected_truncation"] is expected_truncation
    assert result["fresh_final_generation"]["attempted"] is True
    assert result["fresh_final_generation"]["succeeded"] is True
    fresh_call = model.calls[-1]
    assert fresh_call["tools"] == []
    assert fresh_call["kwargs"]["tool_choice"] == "none"
    assert fresh_call["kwargs"]["options"]["reasoning_effort"] == "low"
    fresh_messages = json.dumps(fresh_call["messages"], ensure_ascii=False)
    assert invalid_output not in fresh_messages
    assert "应说明项目准备和执行安排" in fresh_messages
    assert not any(
        call["kwargs"].get("request_metadata", {}).get("purpose")
        == "model_json_repair"
        for call in model.calls
    )
    assert web.calls == 1
    assert [
        item["tool_name"] for item in result["tool_trace"] if item.get("tool_name")
    ] == ["web_search"]
    event_types = [item["event_type"] for item in events]
    assert event_types.count("fresh_final_generation_started") == 1
    assert event_types.count("fresh_final_generation_completed") == 1


def test_broken_body_is_never_included_in_fresh_final_request(tmp_path):
    paths = write_generic_inputs(tmp_path)
    sentinel = "BROKEN-BODY-SENTINEL-91AC"
    broken = (
        '{"chapter_title":"项目交付计划","sections":['
        '{"heading":"准备阶段","content":"'
        + sentinel
    )
    model = JsonRepairModel(
        broken,
        json.dumps(generic_draft(), ensure_ascii=False),
    )
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    assert result["fresh_final_generation"]["succeeded"] is True
    fresh_messages = json.dumps(model.calls[-1]["messages"], ensure_ascii=False)
    assert sentinel not in fresh_messages
    assert "应说明项目准备和执行安排" in fresh_messages
    assert result["model_final_output"]["sections"] == generic_draft()["sections"]


def test_tool_argument_parse_failure_uses_clean_fresh_final_without_model_repair(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = ToolArgumentFailureThenRepairModel(
        "",
        json.dumps(generic_draft(), ensure_ascii=False),
    )
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    assert result["stop_reason"] == "fresh_final_after_json_failure_succeeded"
    assert result["model_json_repair"]["source_stage"] == "tool_arguments"
    assert result["model_json_repair"]["attempted"] is False
    assert result["fresh_final_generation"]["succeeded"] is True
    assert model.calls[-1]["tools"] == []
    assert "missing delimiter" not in json.dumps(
        model.calls[-1]["messages"],
        ensure_ascii=False,
    )
    assert [item["tool_name"] for item in result["tool_trace"]] == ["get_writing_scope"]


def test_failed_clean_fresh_final_saves_redacted_debug_and_skips_business_checks(tmp_path):
    paths = write_generic_inputs(tmp_path)
    raw = (
        '{"reasoning_content":"private chain",'
        '"phone":"13812345678",'
        '"id":"11010519491231002X",'
        '"email":"person@example.com",'
        '"token":"fake-api-key-never-report",'
    )
    model = JsonRepairModel(raw, "still not json")
    events = []
    result = make_agent(tmp_path, paths, model).run(event_handler=events.append)
    assert result["status"] == "failed"
    assert result["stop_reason"] == "model_json_parse_error"
    assert result["model_json_repair"]["attempted"] is False
    assert result["model_json_repair"]["succeeded"] is False
    assert result["fresh_final_generation"]["attempted"] is True
    assert result["fresh_final_generation"]["succeeded"] is False
    diagnostics = result["model_json_repair"]["diagnostics"]
    assert diagnostics["model_step"] == 1
    assert diagnostics["profile_name"] == "generic_profile"
    assert diagnostics["api_style"] == "chat_completions"
    assert diagnostics["finish_reason"] == "stop"
    assert diagnostics["content_character_count"] == len(raw)
    assert diagnostics["code_block_detected"] is False
    assert diagnostics["suspected_truncation"] is True
    assert diagnostics["json_error_summary"]
    assert diagnostics["model_repair_disabled"] is True
    assert result["validation"]["valid"] is False
    assert result["validation"]["skipped"] is True
    assert result["validation"]["skip_reason"] == "model_json_parse_error"
    assert result["validation"]["validation_status"] == "fatal"
    assert result["validation"]["candidate_available"] is False
    assert result["validation"]["candidate_reviewable"] is False
    assert result["validation"]["issue_counts"] == {
        "fatal": 1,
        "blocking": 0,
        "warning": 0,
    }
    assert result["validation"]["issues"][0]["category"] == "protocol"
    assert result["validation"]["issues"][0]["code"] == "model_json_parse_error"
    assert result["duplicate_review_performed"] is False
    event_types = [item["event_type"] for item in events]
    assert "validation_skipped" in event_types
    assert "validation_completed" not in event_types
    assert "duplicate_review_skipped" in event_types
    assert "duplicate_review_started" not in event_types
    debug_path = Path(result["model_json_repair"]["raw_output_path"])
    debug = debug_path.read_text(encoding="utf-8")
    assert result["model_json_repair"]["raw_output_saved"] is True
    assert len(result["model_json_repair"]["raw_output_sha256"]) == 64
    for secret in [
        "private chain",
        "13812345678",
        "11010519491231002X",
        "person@example.com",
        "fake-api-key-never-report",
    ]:
        assert secret not in debug
        assert secret not in json.dumps(result, ensure_ascii=False)
    validation_event = next(item for item in events if item["event_type"] == "validation_skipped")
    assert "未取得可校验章节对象" in format_agent_event(validation_event)
    assert "model_json_repair_started" not in event_types
    assert event_types.count("fresh_final_generation_started") == 1


def test_first_generation_without_existing_draft_or_instruction_is_unchanged(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = FinalModel(generic_draft())
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    assert result["revision_round"] == 0
    assert result["based_on_existing_draft"] is False
    assert result["user_instruction_present"] is False
    user_message = next(item for item in model.messages[0] if item["role"] == "user")
    assert "current_draft" not in user_message["content"]


def test_existing_draft_and_instruction_trigger_full_validated_revision_and_history(tmp_path):
    paths = write_generic_inputs(tmp_path)
    first = make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    previous = Path(first["outputs"]["markdown"]).read_text(encoding="utf-8")
    model = FinalModel(generic_draft(revision=True, table=True))
    second = make_agent(
        tmp_path,
        paths,
        model,
        instruction="压缩重复内容，增加一个阶段职责表格并重新组织两个小节。",
    ).run()
    assert second["status"] == "completed"
    assert second["revision_round"] == 1
    assert second["based_on_existing_draft"] is True
    assert second["revision_scope"] == "structural_revision"
    user_message = next(item for item in model.messages[0] if item["role"] == "user")
    assert "current_draft" in user_message["content"]
    assert previous in user_message["content"]
    assert second["validation"]["valid"] is True
    history = Path(second["previous_draft_path"])
    assert history.is_file()
    assert history.read_text(encoding="utf-8") == previous
    assert len(list(history.parent.glob("*.md"))) == 1
    assert second["change_summary"]["current_table_count"] == 1
    assert set(second["change_summary"]["sections_changed"]) >= {"准备阶段", "执行阶段"}


def test_existing_draft_is_runtime_context_and_load_tool_is_not_required(tmp_path):
    paths = write_generic_inputs(tmp_path)
    make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    model = FinalModel(generic_draft(revision=True))
    result = make_agent(tmp_path, paths, model, instruction="统一术语。").run()
    assert result["based_on_existing_draft"] is True
    assert all("load_existing_draft" not in names for names in model.tools)
    assert result["existing_draft_sha256"]


def test_no_instruction_does_not_automatically_enter_revision_semantics(tmp_path):
    paths = write_generic_inputs(tmp_path)
    make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    result = make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    assert result["based_on_existing_draft"] is False
    assert result["revision_scope"] == "initial_generation"


def test_instruction_sources_file_utf8_merge_and_interactive_end(tmp_path):
    instruction_file = tmp_path / "意见.txt"
    instruction_file.write_text("调整表格字段顺序。", encoding="utf-8")
    lines = iter(["统一术语。", "保留标题。", "END", "不会读取"])
    bundle = collect_user_instructions(
        instruction="压缩重复内容。",
        instruction_file=instruction_file,
        interactive=True,
        input_fn=lambda: next(lines),
    )
    assert bundle.text == "压缩重复内容。\n\n调整表格字段顺序。\n\n统一术语。\n保留标题。"
    assert [item["type"] for item in bundle.sources] == ["cli", "file", "interactive"]
    assert "不会读取" not in bundle.text


def test_instruction_is_sanitized_and_recorded_in_report(tmp_path):
    paths = write_generic_inputs(tmp_path)
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(generic_draft()),
        instruction="统一术语。 api_key=top-secret 13800138000",
    ).run()
    assert result["user_instruction_present"] is True
    assert "top-secret" not in result["user_instruction"]
    assert "13800138000" not in result["user_instruction"]
    assert result["user_instruction_sources"] == [{"type": "test"}]


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("修改姓名并统一术语。", "local_fact_update"),
        ("调整章节边界并压缩重复内容。", "structural_revision"),
        ("请提升表达质量。", "full_chapter_revision"),
    ],
)
def test_revision_scope_classification(instruction, expected):
    assert classify_revision_scope(instruction) == expected


def test_stable_placeholder_id_and_combination_rejected():
    first = attach_placeholder_identity(
        {"field": "项目负责人", "question": "确认", "reason": "缺失"}
    )
    second = attach_placeholder_identity(
        {"field": " 项目负责人 ", "question": "再次确认", "reason": "缺失"}
    )
    assert first["placeholder_id"] == second["placeholder_id"] == "PH-PROJECT-MANAGER"
    assert first["placeholder_token"] == "{{PH-PROJECT-MANAGER}}"
    unknown_a = placeholder_id_for_field("自定义验收联系人")
    unknown_b = placeholder_id_for_field("自定义验收联系人")
    assert unknown_a == unknown_b and unknown_a.startswith("PH-FIELD-")
    with pytest.raises(ValueError, match="exactly one field"):
        attach_placeholder_identity(
            {"field": "负责人及团队成员", "question": "确认", "reason": "缺失"}
        )


def test_runtime_pending_confirmation_and_placeholder_ids_are_bidirectional(tmp_path):
    paths = write_generic_inputs(tmp_path)
    context = TechnicalWritingToolContext(
        response_outline_path=paths["response"],
        writing_outline_path=paths["writing"],
        task_information_path=paths["tasks"],
        output_root=tmp_path / "outputs",
        output_key="generic_case",
        target_chapter_id="CH-001",
        target_section_id=None,
        metadata_store=EmptyMetadataStore(),
        web_disabled=True,
    )
    result = build_technical_tool_registry(context).execute(
        "record_pending_confirmation",
        {"field": "自定义验收联系人", "question": "请确认", "reason": "未确认"},
    )
    mapping = result["placeholders"][0]
    assert mapping["placeholder_token"] == placeholder_token(mapping["placeholder_id"])
    assert context.pending_confirmations[0]["placeholder_id"] == mapping["placeholder_id"]
    repeated = build_technical_tool_registry(context).execute(
        "record_pending_confirmation",
        {"field": "自定义验收联系人", "question": "再次确认", "reason": "仍未确认"},
    )
    assert repeated["placeholders"][0]["placeholder_id"] == mapping["placeholder_id"]
    assert len(context.pending_confirmations) == 1


def test_exact_tender_baseline_does_not_create_pending_confirmation(tmp_path):
    paths = write_generic_inputs(tmp_path)
    response = json.loads(paths["response"].read_text(encoding="utf-8"))
    response["chapters"][0]["response_points"][0]["original_text"] = (
        "货到时间为合同签订之日起30日内，安装在货到后10日内完成。"
    )
    paths["response"].write_text(
        json.dumps(response, ensure_ascii=False),
        encoding="utf-8",
    )
    context = TechnicalWritingToolContext(
        response_outline_path=paths["response"],
        writing_outline_path=paths["writing"],
        task_information_path=paths["tasks"],
        output_root=tmp_path / "outputs",
        output_key="generic_case",
        target_chapter_id="CH-001",
        target_section_id=None,
        metadata_store=EmptyMetadataStore(),
        web_disabled=True,
    )
    result = context.record_pending_confirmation(
        {
            "field": "交货期限",
            "question": "是否按30日响应",
            "reason": "招标已经给出基准",
        }
    )
    assert result["status"] == "not_required"
    assert context.pending_confirmations == []

    positive = context.record_pending_confirmation(
        {
            "field": "交货期限正偏离",
            "question": "是否确认25日交货",
            "reason": "拟优于招标30日基准，形成额外履约责任",
        }
    )
    assert positive["status"] == "recorded"
    assert len(context.pending_confirmations) == 1


def test_confirmed_fact_and_selected_knowledge_do_not_create_pending_confirmation(
    tmp_path,
):
    paths = write_generic_inputs(tmp_path)
    confirmed_path = tmp_path / "confirmed_facts.json"
    confirmed_path.write_text(
        json.dumps(
            {
                "facts": {
                    "project_manager_management_years": {
                        "status": "confirmed",
                        "value": 5,
                        "source_id": "CONF-PERSON-1",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    context = TechnicalWritingToolContext(
        response_outline_path=paths["response"],
        writing_outline_path=paths["writing"],
        task_information_path=paths["tasks"],
        confirmed_project_facts_path=confirmed_path,
        output_root=tmp_path / "outputs",
        output_key="generic_case",
        target_chapter_id="CH-001",
        target_section_id=None,
        metadata_store=EmptyMetadataStore(),
        web_disabled=True,
    )
    years = context.record_pending_confirmation(
        {
            "field": "项目经理工作年限",
            "question": "请确认工作年限",
            "reason": "正文需要填写",
        }
    )
    assert years["status"] == "not_required"

    context.local_evidence_refs.append(
        {
            "candidate_id": "CONF-CERT-1",
            "entity_type": "person",
            "evidence_type": "person_certificate",
            "entity_name": "张三",
            "selection_status": "confirmed",
        }
    )
    certificate = context.record_pending_confirmation(
        {
            "field": "项目经理证书",
            "question": "请确认是否使用PMP证书",
            "reason": "人员证书信息",
        }
    )
    assert certificate["status"] == "not_required"
    assert context.pending_confirmations == []


def test_section_fact_ledger_separates_response_intent_and_evidence():
    original_context = {
        "tender_requirements": {
            "requirements": [
                {
                    "point_id": "TR-DELIVERY",
                    "original_text": "货到时间为合同签订之日起30日内。",
                },
                {
                    "point_id": "TR-CAPABILITY",
                    "original_text": "所投产品应支持在线扩展。",
                },
            ]
        },
        "confirmed_project_facts": {
            "facts": {
                "project_manager_management_years": 5,
            },
            "fact_records": {
                "project_manager_management_years": {
                    "status": "confirmed",
                    "value": 5,
                    "source_id": "CONF-PERSON-1",
                }
            },
        },
        "draft_plan": {"draft_units": []},
        "task_information": {"tasks": []},
    }
    target = {
        "chapter_id": "CH-001",
        "title": "人员及实施",
        "children": [
            {
                "chapter_id": "CH-001-01",
                "title": "项目负责人及主要人员",
                "writing_requirements": {
                    "response_point_ids": [
                        "TR-DELIVERY",
                        "TR-CAPABILITY",
                    ]
                },
            }
        ],
    }
    ledger = _build_section_fact_ledger(
        original_context=original_context,
        target=target,
        pending_confirmations=[],
        local_evidence_refs=[
            {
                "candidate_id": "KB-RESUME-1",
                "entity_type": "person",
                "selection_status": "candidate",
            }
        ],
    )
    entry = ledger[0]
    assert entry["confirmed_personnel_evidence_ids"] == ["CONF-PERSON-1"]
    assert entry["candidate_personnel_evidence_ids"] == ["KB-RESUME-1"]
    assert entry["default_no_deviation_requirements"][0][
        "response_intent"
    ] == "no_deviation"
    assert entry["default_no_deviation_requirements"][0][
        "evidence_status"
    ] == "confirmed"
    capability = next(
        item
        for item in entry["evidence_pending_requirements"]
        if item["source_id"] == "TR-CAPABILITY"
    )
    assert capability["response_intent"] == "no_deviation"
    assert capability["evidence_status"] == "pending"


def test_validator_links_content_to_registered_placeholder_id_not_chinese_text(tmp_path):
    paths = write_generic_inputs(tmp_path)
    target = json.loads(paths["writing"].read_text(encoding="utf-8"))["chapters"][0]
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "agents/technical_writer/chapter_draft_output_schema.json")
        .read_text(encoding="utf-8")
    )
    pending = attach_placeholder_identity(
        {
            "field": "自定义验收联系人",
            "question": "请确认",
            "reason": "未确认",
            "blocking": True,
        }
    )
    placeholder_id = pending["placeholder_id"]
    value = generic_draft()
    value["sections"][0]["content"] += " 联系人为" + placeholder_token(placeholder_id)
    value["sections"][0]["placeholder_ids"] = [placeholder_id]
    value["placeholders_used"] = [placeholder_id]
    valid = validate_technical_chapter(
        value,
        schema=schema,
        target=target,
        confirmed_facts={},
        pending_confirmations=[pending],
    )
    assert valid["valid"] is True

    value["sections"][0]["content"] = value["sections"][0]["content"].replace(
        placeholder_id, "PH-UNREGISTERED-FIELD"
    )
    invalid = validate_technical_chapter(
        value,
        schema=schema,
        target=target,
        confirmed_facts={},
        pending_confirmations=[pending],
    )
    assert invalid["valid"] is False
    assert any("not listed" in error or "unregistered" in error for error in invalid["errors"])


def test_tender_table_specification_is_returned_for_reuse(tmp_path):
    paths = write_generic_inputs(tmp_path, with_table=True)
    context = TechnicalWritingToolContext(
        response_outline_path=paths["response"],
        writing_outline_path=paths["writing"],
        task_information_path=paths["tasks"],
        output_root=tmp_path / "outputs",
        output_key="generic_case",
        target_chapter_id="CH-001",
        target_section_id=None,
        metadata_store=EmptyMetadataStore(),
        web_disabled=True,
    )
    result = context.get_response_requirements({"chapter_id": "CH-001", "response_point_ids": []})
    spec = result["requirements"][0]["table_writing_specification"]
    assert spec["reuse_required"] is True
    assert spec["table_name"] == "阶段工作安排表"
    assert spec["field_order"] == ["阶段", "工作内容", "交付物"]
    assert "不存在对应表格时才可自行设计" in SYSTEM_PROMPT


def test_long_term_rules_are_loaded_for_first_generation_and_reported(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = FinalModel(generic_draft())
    result = make_agent(tmp_path, paths, model).run()
    rules = load_writing_rules()
    assert result["writing_rules"]["loaded"] is True
    assert result["writing_rules"]["version"] == "1.1.0"
    assert result["writing_rules"]["sha256"] == rules.sha256
    assert result["writing_rules"]["rule_ids"] == [f"TW-{index:03d}" for index in range(1, 13)]
    rules_message = next(
        item for item in model.messages[0]
        if item["role"] == "developer" and item["content"].startswith("[Technical Writing Rules]")
    )
    assert "TW-002 小节职责边界" in rules_message["content"]
    prompt_text = json.dumps(model.messages[0], ensure_ascii=False)
    assert "writing_mode" in prompt_text and "formal_technical_prose" in prompt_text
    assert "每个主要小节必须先形成充分、连续的正文论述" in prompt_text


def test_revision_loads_same_rule_version_and_builds_section_boundaries(tmp_path):
    paths = write_generic_inputs(tmp_path)
    first = make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    second_model = FinalModel(generic_draft(revision=True))
    second = make_agent(
        tmp_path,
        paths,
        second_model,
        instruction="压缩重复内容。",
    ).run()
    assert first["writing_rules"]["sha256"] == second["writing_rules"]["sha256"]
    plan = second["rule_application_plan"]
    assert [item["section_title"] for item in plan["section_boundaries"]] == ["准备阶段", "执行阶段"]
    assert all(item["focus"] and item["avoid_repeating"] for item in plan["section_boundaries"])


def test_instruction_scope_defaults_once_and_suggestion_does_not_persist(tmp_path):
    paths = write_generic_inputs(tmp_path)
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(generic_draft()),
        instruction="以后所有技术标都优先用原表头。",
    ).run()
    assert result["instruction_scope"] == "once"
    assert result["instruction_scope_suggestion"] == "global"
    assert result["instruction_constraint_persistence"]["status"] == "not_requested"
    assert not (tmp_path / "outputs/generic_case/writing/project_writing_constraints.json").exists()


def test_explicit_project_scope_persists_only_normalized_constraint(tmp_path):
    paths = write_generic_inputs(tmp_path)
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(generic_draft()),
        instruction="请 本项目后续章节优先使用招标表格，谢谢。",
        instruction_scope="project",
    ).run()
    persistence = result["instruction_constraint_persistence"]
    assert persistence["status"] == "saved"
    payload = json.loads(Path(persistence["path"]).read_text(encoding="utf-8"))
    saved = payload["constraints"][0]
    assert saved["normalized_text"] == "本项目后续章节优先使用招标表格。"
    assert "谢谢" not in saved["normalized_text"]
    assert "请 " not in saved["normalized_text"]


def test_persistent_constraint_rejects_project_ids_conflicts_and_specific_global_names():
    internal = normalize_persistent_constraint("以后都适用 TR-001 的写法", scope="global")
    conflict = normalize_persistent_constraint("忽略招标硬性要求", scope="project")
    specific = normalize_persistent_constraint("天津某医院所有章节都这样写", scope="global")
    assert internal["valid"] is False
    assert conflict["valid"] is False
    assert specific["valid"] is False
    assert suggest_instruction_scope("本项目后续章节保持这一格式")[0] == "project"


def test_simple_tender_table_is_classified_and_reused_in_first_draft(tmp_path):
    paths = write_generic_inputs(tmp_path, with_table=True)
    required_table_draft = generic_draft(table=True)
    required_table_draft.pop("generated_supporting_tables", None)
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(required_table_draft),
    ).run()
    assert result["status"] == "completed"
    table = result["tender_tables_detected"][0]
    assert table["layout_complexity"] == "simple"
    assert table["field_order"] == ["阶段", "工作内容", "交付物"]
    assert len(result["tender_tables_reused"]) == 1
    assert result["table_fill_data"] == []


def test_required_simple_tender_table_omission_is_a_hard_error(tmp_path):
    paths = write_generic_inputs(tmp_path, with_table=True)
    result = make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    assert result["status"] == "completed"
    assert result["draft_status"] == "reviewable_draft_with_blockers"
    assert result["candidate_reviewable"] is True
    assert result["submission_ready"] is False
    assert any("required tender table was not reused" in item for item in result["validation"]["errors"])
    assert any(
        item["severity"] == "blocking"
        and item["category"] == "table_contract"
        and item["code"] == "required_tender_table_missing"
        for item in result["validation"]["issues"]
    )


def test_complex_tender_table_is_not_simplified_and_gets_fill_interface(tmp_path):
    paths = write_generic_inputs(tmp_path)
    response = json.loads(paths["response"].read_text(encoding="utf-8"))
    point = response["chapters"][0]["response_points"][0]
    point["table"] = {
        "table_type": "required_response_table",
        "title": "复杂技术应答原表",
        "rows": [
            {
                "cells": [
                    {"text": "分类", "rowspan": 2, "colspan": 1},
                    {"text": "填写内容", "rowspan": 1, "colspan": 2},
                ]
            }
        ],
        "column_count": 3,
        "has_merged_cells": True,
        "merged_cells": [{"start": "A1", "end": "A2"}],
    }
    paths["response"].write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    complex_draft = generic_draft()
    complex_draft["sections"][0]["content"] += (
        "\n\n复杂技术应答原表用于逐项记录填写内容，回填时必须保留原表表名、表头、"
        "字段顺序和合并关系，不得改变填写边界；完成后与后续技术说明逐项复核并保持一致。"
    )
    result = make_agent(tmp_path, paths, FinalModel(complex_draft)).run()
    assert result["status"] == "completed"
    detected = result["tender_tables_detected"][0]
    assert detected["layout_complexity"] == "complex"
    assert detected["table_name"] == "复杂技术应答原表"
    assert detected["headers"] == ["分类", "填写内容"]
    assert detected["field_order"] == ["分类", "填写内容"]
    assert detected["merged_cells"] == [{"start": "A1", "end": "A2"}]
    assert detected["row_column_relationship"]["header_row_index"] == 0
    assert result["complex_tender_tables_preserved"][0]["preserved"] is True
    assert result["table_fill_data"][0]["format_preservation"] == "exact"
    assert result["tender_tables_created"] == []

    context = TechnicalWritingToolContext(
        response_outline_path=paths["response"],
        writing_outline_path=paths["writing"],
        task_information_path=paths["tasks"],
        output_root=tmp_path / "tool-output",
        output_key="generic_case",
        target_chapter_id="CH-001",
        target_section_id=None,
        metadata_store=EmptyMetadataStore(),
        web_disabled=True,
    )
    registry = build_technical_tool_registry(context)
    table_id = context.tender_tables[0]["table_id"]
    assert "record_table_fill_data" in registry.names()
    recorded = registry.execute(
        "record_table_fill_data",
        {
            "table_id": table_id,
            "cell_values": {"分类": "已知分类", "填写内容": None},
        },
    )
    assert recorded["status"] == "recorded"
    assert context.table_fill_data_records[0]["cell_values"]["分类"] == "已知分类"


def test_complex_table_markdown_simplification_is_rejected():
    point = {
        "point_id": "RP-X",
        "content_type": "table",
        "table_type": "required_response_table",
        "source_refs": [{"file_id": "F-1", "table_name": "复杂表", "row_or_cell": "table_2"}],
    }
    table = normalize_tender_table(
        {
            "headers": ["分类", "填写内容"],
            "rows": [],
            "has_merged_cells": True,
        },
        point=point,
    )
    draft = {
        "sections": [
            {
                "heading": "填写",
                "content": "| 分类 | 填写内容 |\n| --- | --- |\n| A | B |",
            }
        ]
    }
    review = review_chapter_quality(draft, tender_tables=[table])
    assert review["complex_tender_tables_preserved"][0]["preserved"] is False


def test_markdown_table_validator_checks_pandoc_compatible_shape():
    valid = inspect_markdown_tables(
        "| 阶段 | 成果 |\n| --- | --- |\n| 准备 | 计划 |"
    )
    invalid = inspect_markdown_tables(
        "| 阶段 | 成果 |\n| -- | --- |\n| 准备 |"
    )
    assert len(valid["tables"]) == 1
    assert valid["errors"] == []
    assert invalid["errors"]


class DuplicateThenRepairModel:
    def __init__(self):
        repeated = (
            "项目执行按照统一流程开展准备、实施、检查和交付，明确责任边界、输入资料、"
            "完成判据和过程记录，并对发现的问题实施闭环整改，确保阶段成果满足进入下一阶段的条件。"
        ) * 2
        self.first = generic_draft()
        self.first["sections"][0]["content"] = repeated
        self.first["sections"][1]["content"] = repeated
        self.repaired = generic_draft(revision=True)
        self.calls = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        value = self.first if len(self.calls) == 1 else self.repaired
        return ToolModelResult(
            "final",
            [],
            json.dumps(value, ensure_ascii=False),
            {"provider": "fake", "model": "fake", "usage": empty_usage()},
        )


class SavedDraftThenFreshQualityModel:
    sentinel = "FIRST-DRAFT-SENTINEL-7F2D"

    def __init__(self):
        self.calls = []
        self.poor = generic_draft()
        for section in self.poor["sections"]:
            section["content"] = (
                f"{self.sentinel}\n\n"
                "| 阶段 | 工作 |\n| --- | --- |\n| 准备 | 核对 |\n\n"
                "- 检查输入\n- 形成记录"
            )

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
                "kwargs": kwargs,
            }
        )
        metadata = {
            "provider": "fake",
            "model": "fake",
            "usage": empty_usage(),
        }
        if len(self.calls) == 1:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": "save-first-draft",
                        "name": "save_section_draft",
                        "arguments": {
                            "sections": [
                                {
                                    "section_id": "CH-001-01",
                                    "title": "准备阶段",
                                    "content": self.sentinel,
                                }
                            ]
                        },
                    }
                ],
                None,
                metadata,
            )
        value = self.poor if len(self.calls) == 2 else generic_draft()
        return ToolModelResult(
            "final",
            [],
            json.dumps(value, ensure_ascii=False),
            metadata,
        )


class FactBoundaryThenFreshModel:
    failed_full_paragraph = (
        "FAILED-FACT-PARAGRAPH-4D8E 本系统支持高可用、在线扩展、多副本、反亲和和自动重启，"
        "并确保扩展过程业务不中断；该段是首次失败稿的完整段落，不得进入重新生成请求。"
    )

    def __init__(self):
        self.calls = []
        self.first = generic_draft()
        self.first["sections"][0]["content"] += (
            "\n\n" + self.failed_full_paragraph
        )
        self.first["sections"][1]["content"] += (
            "\n\n拟派张三具备5年以上项目经验并持有高级资格证书，"
            "最终团队全部满足上述要求。"
        )

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
                "kwargs": kwargs,
            }
        )
        value = self.first if len(self.calls) == 1 else generic_draft()
        return ToolModelResult(
            "final",
            [],
            json.dumps(value, ensure_ascii=False),
            {
                "provider": "fake",
                "model": "fake",
                "usage": empty_usage(),
            },
        )


class CandidateSelectionModel:
    def __init__(self, initial, fresh):
        self.initial = initial
        self.fresh = fresh
        self.calls = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, ensure_ascii=False)),
                "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
                "kwargs": kwargs,
            }
        )
        value = self.initial if len(self.calls) == 1 else self.fresh
        return ToolModelResult(
            "final",
            [],
            json.dumps(value, ensure_ascii=False),
            {
                "provider": "fake",
                "model": "fake",
                "usage": empty_usage(),
            },
        )


def draft_with_product_errors(count):
    value = generic_draft()
    value["sections"][0]["content"] += "\n\n" + "\n\n".join(
        f"本系统支持在线扩展并具备多副本能力，能力断言序号{i}。"
        for i in range(1, count + 1)
    )
    return value


def draft_with_candidate_error():
    value = generic_draft()
    value["sections"][0]["content"] += "\n\n我公司已通过质量管理体系认证。"
    return value


def test_worse_fresh_candidate_is_rejected(tmp_path):
    paths = write_generic_inputs(tmp_path)
    initial = draft_with_product_errors(1)
    fresh = draft_with_product_errors(2)
    fresh["sections"][1]["content"] += "\n\n我公司已通过质量管理体系认证。"
    model = CandidateSelectionModel(initial, fresh)
    result = make_agent(tmp_path, paths, model).run()
    assert result["selected_candidate"] == "initial"
    assert result["fresh_candidate_accepted"] is False
    assert result["fresh_candidate_validation"] is None
    assert result["candidate_selection_reason"] == "fresh_candidate_not_generated"
    assert result["status"] == "completed"
    assert result["draft_status"] == "reviewable_draft_with_blockers"
    assert result["submission_ready"] is False
    assert result["candidate_generated"] is True
    assert result["candidate_saved"] is True
    assert result["candidate_available"] is True
    assert result["candidate_reviewable"] is True
    assert Path(result["outputs"]["candidate_markdown"]).is_file()
    assert Path(result["outputs"]["candidate_json"]).is_file()
    assert result["outputs"]["markdown"] == result["outputs"]["candidate_markdown"]
    assert result["outputs"]["formal_markdown"] is None


def test_blocking_candidate_is_atomically_saved_and_verified(tmp_path):
    paths = write_generic_inputs(tmp_path)
    events = []
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(draft_with_product_errors(1)),
    ).run(event_handler=events.append)

    assert result["status"] == "completed"
    assert result["stop_reason"] == "candidate_validation_blocked"
    assert result["candidate_generated"] is True
    assert result["candidate_saved"] is True
    assert result["candidate_available"] is True
    assert result["candidate_reviewable"] is True
    assert result["submission_ready"] is False
    assert result["validation_status"] == "blocked"
    assert result["issue_counts"] == result["validation"]["issue_counts"]

    json_path = Path(result["outputs"]["candidate_json"])
    markdown_path = Path(result["outputs"]["candidate_markdown"])
    assert json_path.name == "CH-001.candidate.json"
    assert markdown_path.name == "CH-001.candidate.md"
    assert json_path.stat().st_size > 0
    assert markdown_path.stat().st_size > 0
    assert json.loads(json_path.read_text(encoding="utf-8")) == result["chapter_draft"]
    assert result["candidate_artifacts"]["json_sha256"] == hashlib.sha256(
        json_path.read_bytes()
    ).hexdigest()
    assert result["candidate_artifacts"]["markdown_sha256"] == hashlib.sha256(
        markdown_path.read_bytes()
    ).hexdigest()
    candidate_markdown = markdown_path.read_text(encoding="utf-8")
    assert "<!-- technical-writing-issues" in candidate_markdown
    assert "unconfirmed_product_capability" in candidate_markdown
    assert not json_path.with_name(json_path.name + ".tmp").exists()
    assert not markdown_path.with_name(markdown_path.name + ".tmp").exists()
    assert result["outputs"]["formal_markdown"] is None
    assert not markdown_path.with_name("CH-001.md").exists()
    assert "candidate_artifact_saved" in [
        item["event_type"] for item in events
    ]


def test_submission_ready_chapter_also_saves_candidate_for_stitching(tmp_path):
    paths = write_generic_inputs(tmp_path)
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(generic_draft()),
    ).run()

    assert result["status"] == "completed"
    assert result["submission_ready"] is True
    assert result["candidate_generated"] is True
    assert result["candidate_saved"] is True
    assert result["candidate_available"] is True
    assert result["candidate_reviewable"] is True
    assert Path(result["outputs"]["formal_markdown"]).is_file()
    assert Path(result["outputs"]["candidate_markdown"]).is_file()
    assert Path(result["outputs"]["candidate_json"]).is_file()
    assert result["outputs"]["markdown"] == result["outputs"]["formal_markdown"]
    persisted = json.loads(
        Path(result["outputs"]["candidate_json"]).read_text(encoding="utf-8")
    )
    assert persisted == result["chapter_draft"]
    assert all(
        str(section.get("content") or "").strip()
        for section in persisted["sections"]
    )


def test_candidate_artifact_write_failure_is_fatal(tmp_path, monkeypatch):
    paths = write_generic_inputs(tmp_path)

    def fail_candidate_write(**kwargs):
        raise OSError("fake candidate disk failure")

    monkeypatch.setattr(
        technical_writing_agent_module,
        "_write_and_verify_candidate_artifacts",
        fail_candidate_write,
    )
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(draft_with_product_errors(1)),
    ).run()

    assert result["status"] == "failed"
    assert result["stop_reason"] == "candidate_artifact_write_failed"
    assert result["candidate_generated"] is True
    assert result["candidate_saved"] is False
    assert result["candidate_available"] is False
    assert result["candidate_reviewable"] is False
    assert result["submission_ready"] is False
    assert result["validation_status"] == "fatal"
    assert result["issue_counts"]["fatal"] == 1
    assert any(
        item["severity"] == "fatal"
        and item["category"] == "runtime"
        and item["code"] == "candidate_artifact_write_failed"
        for item in result["validation"]["issues"]
    )
    assert result["outputs"]["candidate_json"] is None
    assert result["outputs"]["candidate_markdown"] is None
    assert result["outputs"]["formal_markdown"] is None


def test_blocking_validation_console_uses_warning_marker_and_issue_counts():
    line = format_agent_event(
        {
            "event_type": "validation_completed",
            "timestamp": "2026-07-27T00:00:00+00:00",
            "data": {
                "valid": False,
                "validation_status": "blocked",
                "issue_counts": {
                    "fatal": 0,
                    "blocking": 3,
                    "warning": 2,
                },
                "actual_section_count": 11,
                "expected_section_count": 11,
                "missing_response_point_ids": [],
                "missing_score_item_ids": [],
            },
        }
    )
    assert "⚠ 校验完成" in line
    assert "blocking 3" in line
    assert "warning 2" in line
    assert "✗ 校验完成" not in line


def test_better_fresh_candidate_is_selected(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = CandidateSelectionModel(
        draft_with_product_errors(2),
        generic_draft(),
    )
    result = make_agent(tmp_path, paths, model).run()
    # Legacy complete-chapter Fresh responses are no longer accepted by the
    # structured patch protocol.
    assert result["selected_candidate"] == "initial"
    assert result["fresh_candidate_accepted"] is False
    assert result["candidate_selection_reason"] == "fresh_candidate_not_generated"
    assert result["status"] == "completed"
    assert result["initial_candidate_sha256"]
    assert result["fresh_candidate_sha256"] is None


def test_candidate_selection_preserves_coverage(tmp_path):
    paths = write_generic_inputs(tmp_path)
    fresh = generic_draft()
    fresh["requirements_covered"] = []
    fresh["sections"][0]["covered_requirement_ids"] = []
    fresh["sections"][1]["covered_requirement_ids"] = []
    model = CandidateSelectionModel(draft_with_product_errors(1), fresh)
    result = make_agent(tmp_path, paths, model).run()
    assert result["selected_candidate"] == "initial"
    assert result["fresh_candidate_accepted"] is False
    assert result["candidate_selection_reason"] == "fresh_candidate_not_generated"


def test_fresh_candidate_does_not_add_new_blocking_categories(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = CandidateSelectionModel(
        draft_with_product_errors(2),
        draft_with_candidate_error(),
    )
    result = make_agent(tmp_path, paths, model).run()
    assert result["fresh_candidate_validation"] is None
    assert result["fresh_candidate_accepted"] is False
    assert result["selected_candidate"] == "initial"
    assert (
        result["candidate_selection_reason"]
        == "fresh_candidate_not_generated"
    )


def test_raw_and_grouped_error_counts_are_consistent(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = CandidateSelectionModel(
        draft_with_product_errors(2),
        draft_with_product_errors(1),
    )
    result = make_agent(tmp_path, paths, model).run()
    regeneration = result["fresh_quality_regeneration"]
    assert regeneration["first_raw_error_count"] == 2
    assert regeneration["first_grouped_error_count"] == 1
    assert regeneration["first_error_count"] == len(
        regeneration["first_error_details"]
    )
    assert regeneration["first_error_count"] == regeneration[
        "first_grouped_error_count"
    ]
    assert regeneration["remaining_raw_error_count"] is None
    assert regeneration["remaining_grouped_error_count"] is None
    assert regeneration["remaining_error_count"] is None
    assert regeneration["remaining_error_details"] is None
    assert regeneration["remaining_validation_status"] == "not_run"


def test_remaining_details_only_describe_fresh_candidate(tmp_path):
    paths = write_generic_inputs(tmp_path)
    initial = generic_draft()
    initial["generated_supporting_tables"] = [
        {
            "section_id": "准备阶段",
            "title": "未创建辅助表",
            "purpose": "诊断初始错误",
            "why_table_is_better_than_prose": "字段横向对应",
        }
    ]
    model = CandidateSelectionModel(initial, draft_with_candidate_error())
    result = make_agent(tmp_path, paths, model).run()
    first_codes = {
        item["error_code"]
        for item in result["fresh_quality_regeneration"]["first_error_details"]
    }
    remaining_codes = set()
    assert "declared_but_missing" in first_codes
    assert remaining_codes == set()
    assert result["candidate_selection_reason"] == "fresh_candidate_not_generated"


def test_fresh_fact_regeneration_receives_section_specific_boundary_summary(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = FactBoundaryThenFreshModel()
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    regeneration = result["fresh_quality_regeneration"]
    assert regeneration["attempted"] is True
    assert regeneration["succeeded"] is False
    assert regeneration["first_error_count"] >= 2
    assert regeneration["remaining_error_count"] is None
    details = regeneration["first_error_details"]
    assert any(
        item["section_title"] == "准备阶段"
        and item["error_code"] == "unconfirmed_product_capability"
        and "多副本" in item["keywords"]
        and "技术应答表" in item["boundary_instruction"]
        for item in details
    )
    assert any(
            item["section_title"] == "执行阶段"
            and item["error_code"] in {
                "candidate_promoted_to_fact",
                "unconfirmed_personnel_qualification",
                "numeric_commitment",
            }
        and item["boundary_instruction"]
        for item in details
    )
    fresh_prompt = str(model.calls[-1]["messages"][-1]["content"])
    assert '"issue_code":"unconfirmed_product_capability"' in fresh_prompt
    assert '"section_id":"CH-001-01"' in fresh_prompt


def test_fresh_fact_summary_uses_initial_candidate_only_as_edit_baseline(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = FactBoundaryThenFreshModel()
    result = make_agent(tmp_path, paths, model).run()
    assert result["fresh_quality_regeneration"]["succeeded"] is False
    fresh_messages = json.dumps(model.calls[-1]["messages"], ensure_ascii=False)
    assert "FAILED-FACT-PARAGRAPH-4D8E" in fresh_messages
    assert "initial_candidate" in fresh_messages
    assert "multi_candidate_structured_patch" in fresh_messages


def test_cannot_finalize_section_receives_unresolved_fact_ledger(tmp_path):
    paths = write_generic_inputs(tmp_path)
    draft_units_path = tmp_path / "draft_units.json"
    draft_units_path.write_text(
        json.dumps(
            {
                "draft_units": [
                    {
                        "chapter_id": "CH-001",
                        "section_id": "CH-001-01",
                        "title": "准备阶段",
                        "draftability": {"status": "cannot_finalize"},
                        "parameter_consistency_confirmed": False,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    model = FinalModel(generic_draft())
    make_agent(
        tmp_path,
        paths,
        model,
        draft_units_path=draft_units_path,
    ).run()
    ledger = next(
        str(item["content"])
        for item in model.messages[0]
        if item["role"] == "developer"
        and "[Section-specific Fact Ledger]" in str(item["content"])
    )
    assert "[Section-specific Fact Ledger]" in ledger
    assert '"draftability":"cannot_finalize"' in ledger
    assert (
        "本节只能生成带占位符或条件边界的初稿，不得自行补齐数字、人员和产品能力。"
        in ledger
    )
    assert '"requirement_only":true' in ledger
    assert '"tender_requirements_only"' in ledger
    assert '"confirmed_evidence_ids"' in ledger
    assert '"candidate_evidence_ids"' in ledger
    assert '"registered_placeholders"' in ledger
    assert '"unresolved_staffing"' in ledger
    assert '"unresolved_numeric_commitments"' in ledger
    assert '"unresolved_product_capabilities"' in ledger
    assert '"forbidden_as_confirmed_fact"' in ledger


def test_fact_ledger_does_not_include_failed_draft_text(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = FactBoundaryThenFreshModel()
    result = make_agent(tmp_path, paths, model).run()
    assert result["fresh_quality_regeneration"]["attempted"] is True
    fresh_messages = model.calls[-1]["messages"]
    ledger_messages = [
        str(item["content"])
        for item in fresh_messages
        if item["role"] == "user"
    ]
    assert ledger_messages
    fresh_payload = json.loads(ledger_messages[0])
    serialized = json.dumps(
        fresh_payload["compiled_effective_writing_context"],
        ensure_ascii=False,
    )
    assert FactBoundaryThenFreshModel.failed_full_paragraph not in serialized
    assert "FAILED-FACT-PARAGRAPH-4D8E" not in serialized
    required_fields = {
        "section_id",
        "section_title",
        "confirmed_facts",
        "tender_requirements_only",
        "confirmed_evidence_ids",
        "candidate_evidence_ids",
        "registered_placeholders",
        "unresolved_staffing",
        "unresolved_numeric_commitments",
        "unresolved_product_capabilities",
        "forbidden_as_confirmed_fact",
        "confirmed_personnel_facts",
        "confirmed_personnel_evidence_ids",
        "candidate_personnel_evidence_ids",
        "confirmed_tender_commitments",
        "default_no_deviation_requirements",
        "positive_deviation_candidates",
        "negative_deviations",
        "evidence_pending_requirements",
    }
    initial_ledger = next(
        str(item["content"])
        for item in model.calls[0]["messages"]
        if item["role"] == "developer"
        and "[Section-specific Fact Ledger]" in str(item["content"])
    )
    initial_payload = json.loads(initial_ledger.split("\n", 1)[1])
    assert initial_payload["chapter_fact_permissions"]
    assert all(
        required_fields <= set(entry)
        for entry in initial_payload["section_fact_ledger"]
    )
    compiled = fresh_payload["compiled_effective_writing_context"]
    assert compiled["permission_source"] == "section_fact_ledger"
    assert compiled["section_contexts"]


def test_fresh_quality_regeneration_uses_clean_context_with_initial_edit_baseline(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = SavedDraftThenFreshQualityModel()
    result = make_agent(tmp_path, paths, model).run()
    assert result["status"] == "completed"
    assert result["fresh_quality_regeneration"]["attempted"] is True
    assert result["fresh_quality_regeneration"]["succeeded"] is False
    assert len(model.calls) == 3
    fresh_call = model.calls[-1]
    serialized = json.dumps(fresh_call["messages"], ensure_ascii=False)
    assert model.sentinel in serialized
    assert "initial_candidate" in serialized
    assert "multi_candidate_structured_patch" in serialized
    assert "working_draft" not in serialized
    assert "应说明项目准备和执行安排" in serialized
    assert "写作质量" in serialized
    assert fresh_call["tools"][0]["function"]["name"] == "submit_structured_patch"
    assert fresh_call["kwargs"]["tool_choice"]["function"]["name"] == "submit_structured_patch"


def test_single_fresh_repair_stops_after_one_monotonic_improvement(tmp_path):
    paths = write_generic_inputs(tmp_path)

    class ThreeRoundModel:
        def __init__(self):
            self.calls = []
            self.responses = [
                draft_with_product_errors(2),
                draft_with_product_errors(1),
                generic_draft(),
            ]

        def run_with_tools(self, *, messages, tools, **kwargs):
            self.calls.append(
                {
                    "messages": json.loads(
                        json.dumps(messages, ensure_ascii=False)
                    ),
                    "tools": json.loads(json.dumps(tools, ensure_ascii=False)),
                    "kwargs": kwargs,
                }
            )
            value = self.responses[min(len(self.calls) - 1, 2)]
            return ToolModelResult(
                "final",
                [],
                json.dumps(value, ensure_ascii=False),
                {
                    "provider": "fake",
                    "model": "fake",
                    "usage": empty_usage(),
                },
            )

    model = ThreeRoundModel()
    result = make_agent(tmp_path, paths, model).run()
    repair = result["automatic_repair"]
    assert repair["maximum_rounds"] == 1
    assert repair["rounds_attempted"] == 1
    assert [item["repair_round"] for item in repair["rounds"]] == [1]
    assert all(item["input_draft_hash"] for item in repair["rounds"])
    assert all(item["output_draft_hash"] for item in repair["rounds"])
    assert all("writing_rules_hash" in item for item in repair["rounds"])
    assert all("project_constraints_hash" in item for item in repair["rounds"])
    assert repair["rounds"][0]["accepted"] is False
    assert result["unresolved_auto_fixable_issues"]
    assert result["chapter_status"] == "auto_repair_exhausted"
    assert len(model.calls) == 2
    assert model.calls[1]["tools"][0]["function"]["name"] == "submit_structured_patch"
    targeted_messages = json.dumps(model.calls[1]["messages"], ensure_ascii=False)
    assert "current_chapter_draft" not in targeted_messages
    assert "compiled_effective_writing_context" in targeted_messages
    assert "[Technical Writing Rules]" not in targeted_messages


def test_single_fresh_repair_marks_quality_failure_when_exhausted(tmp_path):
    paths = write_generic_inputs(tmp_path)

    class ExhaustedModel:
        def __init__(self):
            self.calls = []
            self.responses = [
                draft_with_product_errors(3),
                draft_with_product_errors(2),
                draft_with_product_errors(1),
            ]

        def run_with_tools(self, *, messages, tools, **kwargs):
            self.calls.append({"messages": messages, "tools": tools, **kwargs})
            value = self.responses[min(len(self.calls) - 1, 2)]
            return ToolModelResult(
                "final",
                [],
                json.dumps(value, ensure_ascii=False),
                {
                    "provider": "fake",
                    "model": "fake",
                    "usage": empty_usage(),
                },
            )

    result = make_agent(tmp_path, paths, ExhaustedModel()).run()
    assert result["automatic_repair"]["rounds_attempted"] == 1
    assert result["chapter_status"] == "auto_repair_exhausted"
    assert result["unresolved_auto_fixable_issues"]
    assert result["submission_ready"] is False
    assert result["outputs"]["formal_markdown"] is None
    assert Path(result["outputs"]["candidate_markdown"]).is_file()


def test_fresh_repair_is_tool_free_and_does_not_start_second_round(tmp_path):
    paths = write_generic_inputs(tmp_path)

    class RepairToolModel:
        def __init__(self):
            self.calls = []

        def run_with_tools(self, *, messages, tools, **kwargs):
            self.calls.append(
                {"messages": messages, "tools": tools, "kwargs": kwargs}
            )
            metadata = {
                "provider": "fake",
                "model": "fake",
                "usage": empty_usage(),
            }
            if len(self.calls) == 1:
                value = draft_with_product_errors(2)
            elif len(self.calls) == 2:
                value = draft_with_product_errors(1)
            elif len(self.calls) == 3:
                return ToolModelResult(
                    "tool_calls",
                    [
                        {
                            "tool_call_id": "repair-task-info",
                            "name": "get_task_information",
                            "arguments": {},
                        }
                    ],
                    None,
                    metadata,
                )
            else:
                value = generic_draft()
            return ToolModelResult(
                "final",
                [],
                json.dumps(value, ensure_ascii=False),
                metadata,
            )

    model = RepairToolModel()
    result = make_agent(tmp_path, paths, model).run()
    assert result["automatic_repair"]["rounds_attempted"] == 1
    first = result["automatic_repair"]["rounds"][0]
    assert first["accepted"] is False
    assert first["tool_calls"] == []
    assert len(model.calls) == 2
    assert model.calls[1]["tools"][0]["function"]["name"] == "submit_structured_patch"


def test_severe_duplicate_triggers_at_most_one_controlled_repair_without_tools(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = DuplicateThenRepairModel()
    events = []
    result = make_agent(tmp_path, paths, model).run(event_handler=events.append)
    assert result["status"] == "completed"
    assert result["duplicate_review_performed"] is True
    assert result["duplicate_repair_performed"] is False
    assert result["fresh_quality_regeneration"]["attempted"] is True
    assert result["fresh_quality_regeneration"]["succeeded"] is False
    assert result["self_review"]["repair_count"] == 0
    assert len(model.calls) == 2
    assert model.calls[1]["tools"][0]["function"]["name"] == "submit_structured_patch"
    assert "query_local_knowledge" not in json.dumps(model.calls[1], ensure_ascii=False)
    fresh_prompt = str(model.calls[1]["messages"][-1]["content"])
    assert "multi_candidate_structured_patch" in fresh_prompt
    assert "initial_candidate" in fresh_prompt
    assert model.first["sections"][0]["content"] in json.dumps(
        model.calls[1]["messages"],
        ensure_ascii=False,
    )
    assert "web_search" not in json.dumps(model.calls[1], ensure_ascii=False)
    event_types = [item["event_type"] for item in events]
    assert event_types.count("fresh_quality_regeneration_started") == 1
    assert event_types.count("fresh_quality_regeneration_failed") == 1


class WebSequenceModel:
    def __init__(self, search_count):
        self.turn = 0
        self.search_count = search_count

    def run_with_tools(self, *, tools, **kwargs):
        self.turn += 1
        if self.turn <= self.search_count:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": f"web-{self.turn}",
                        "name": "web_search",
                        "arguments": {
                            "query": "第二轮国家标准核验" if self.turn == 2 else "第一轮国家标准核验",
                            "purpose": "国家或行业标准核验",
                            "allowed_source_types": ["standards_authority_official"],
                            "allowed_domains": [],
                            "top_k": 3,
                        },
                    }
                ],
                None,
                {"provider": "fake", "usage": empty_usage()},
            )
        return ToolModelResult(
            "final",
            [],
            json.dumps(generic_draft(), ensure_ascii=False),
            {"provider": "fake", "usage": empty_usage()},
        )


def test_web_auto_can_use_zero_or_multiple_searches_based_on_evidence_need(tmp_path):
    paths = write_generic_inputs(tmp_path)
    zero = make_agent(tmp_path, paths, FinalModel(generic_draft()), policy="auto").run()
    assert zero["run_summary"]["web_search_call_count"] == 0
    assert zero["web_verification"]["status"] == "not_requested"

    other_root = tmp_path / "multiple"
    other_root.mkdir()
    other_paths = write_generic_inputs(other_root)
    multiple = make_agent(
        other_root,
        other_paths,
        WebSequenceModel(2),
        policy="auto",
        web_provider=FakeWebProvider(),
    ).run()
    assert multiple["run_summary"]["web_search_call_count"] == 2
    assert multiple["web_verification"]["status"] == "sufficient"


def test_web_required_is_not_equivalent_to_one_automatically_verified_search(tmp_path):
    paths = write_generic_inputs(tmp_path)
    one = make_agent(
        tmp_path,
        paths,
        WebSequenceModel(1),
        policy="required",
        web_provider=FakeWebProvider(),
    ).run()
    assert one["status"] == "completed"
    assert one["web_verification"]["status"] == "insufficient"
    assert one["submission_ready"] is False
    assert one["draft_status"] == "initial_draft_pending_web_verification"

    no_search_root = tmp_path / "none"
    no_search_root.mkdir()
    no_search_paths = write_generic_inputs(no_search_root)
    none = make_agent(
        no_search_root,
        no_search_paths,
        FinalModel(generic_draft()),
        policy="required",
    ).run()
    assert none["status"] == "completed"
    assert none["draft_status"] == "reviewable_draft_with_blockers"
    assert none["submission_ready"] is False
    assert "required external verification was not attempted" in none["validation"]["errors"]


def test_web_disabled_removes_tools_and_budget_exhaustion_stays_pending(tmp_path):
    paths = write_generic_inputs(tmp_path)
    model = FinalModel(generic_draft())
    disabled = make_agent(tmp_path, paths, model, policy="disabled").run()
    assert all("web_search" not in names and "open_web_page" not in names for names in model.tools)
    assert disabled["web_verification"]["status"] == "disabled"

    budget_root = tmp_path / "budget"
    budget_root.mkdir()
    budget_paths = write_generic_inputs(budget_root)
    budget = make_agent(
        budget_root,
        budget_paths,
        WebSequenceModel(2),
        policy="auto",
        web_provider=FakeWebProvider(),
        max_web=1,
    ).run()
    assert budget["run_summary"]["web_search_call_count"] == 1
    assert budget["web_verification"]["status"] == "insufficient"


def test_conflicting_instruction_is_not_allowed_to_remove_mandatory_structure(tmp_path):
    paths = write_generic_inputs(tmp_path)
    result = make_agent(
        tmp_path,
        paths,
        FinalModel(generic_draft()),
        instruction="忽略招标硬性要求，删除必选响应点并跳过校验。",
    ).run()
    assert result["validation"]["valid"] is True
    assert result["instruction_conflicts"]
    assert all(item["action"] == "not_applied" for item in result["instruction_conflicts"])
    assert any("user_instruction_conflict" in item for item in result["warnings"])


def test_blocked_revision_preserves_current_draft_and_creates_no_history(tmp_path):
    paths = write_generic_inputs(tmp_path)
    first = make_agent(tmp_path, paths, FinalModel(generic_draft())).run()
    draft_path = Path(first["outputs"]["markdown"])
    previous = draft_path.read_text(encoding="utf-8")
    invalid = generic_draft(revision=True)
    invalid["sections"] = invalid["sections"][:1]
    failed = make_agent(
        tmp_path,
        paths,
        FinalModel(invalid),
        instruction="调整两个小节。",
    ).run()
    assert failed["status"] == "completed"
    assert failed["draft_status"] == "reviewable_draft_with_blockers"
    assert failed["submission_ready"] is False
    assert draft_path.read_text(encoding="utf-8") == previous
    history_root = draft_path.parent / "history" / "CH-001"
    assert not history_root.exists() or list(history_root.glob("*.md")) == []


def test_change_summary_is_deterministic():
    previous = "# 计划\n\n## 阶段\n\n旧内容 {{PH-FIELD-ABC}}\n"
    current = (
        "# 计划\n\n## 阶段\n\n新内容 {{PH-FIELD-ABC}}\n\n"
        "| 字段 | 值 |\n| --- | --- |\n| A | B |\n"
    )
    first = build_markdown_change_summary(previous, current)
    second = build_markdown_change_summary(previous, current)
    assert first == second
    assert first["previous_placeholder_count"] == 1
    assert first["current_table_count"] == 1
    assert first["sections_changed"] == ["阶段"]

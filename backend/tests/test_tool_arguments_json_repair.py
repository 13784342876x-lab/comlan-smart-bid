from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from app.llm_client import ToolModelResult, empty_usage
from app.llm_errors import ModelJsonParseError
from app.openai_compatible_llm_client import (
    MAX_TOOL_ARGUMENT_QUOTE_REPAIRS,
    extract_tool_calls,
    parse_model_tool_arguments,
)
from app.technical_writing_agent import (
    _apply_validated_fresh_patches,
    _fresh_patch_protocol_payload,
    _fresh_patch_response_schema,
    _record_tool_argument_json_repairs,
)
from tests.test_technical_writing_structured_patch import (
    BAD_A,
    SAFE_A,
    SECTION_A,
    apply,
    candidate,
    ledger,
    payload,
    repair_context,
    snapshot,
)


def test_tool_arguments_repairs_unescaped_inner_ascii_quotes():
    raw = '{"question":"是否按招标要求的"自签订合同之日起30日内"执行无偏离？"}'
    result = parse_model_tool_arguments(raw, tool_name="record_pending_confirmation")
    assert result.arguments["question"] == '是否按招标要求的"自签订合同之日起30日内"执行无偏离？'
    assert result.json_syntax_repair["repair_count"] == 2


def test_tool_arguments_repairs_multiple_inner_quote_pairs():
    raw = '{"text":"按照"先准备后实施"并遵循"先测试后切换"原则"}'
    result = parse_model_tool_arguments(raw, tool_name="submit_structured_patch")
    assert result.arguments["text"] == '按照"先准备后实施"并遵循"先测试后切换"原则'
    assert result.json_syntax_repair["repair_count"] == 4


def _chat_tool_response(name: str, arguments: str) -> SimpleNamespace:
    function = SimpleNamespace(name=name, arguments=arguments)
    call = SimpleNamespace(id="call-1", function=function)
    message = SimpleNamespace(tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_initial_pending_confirmation_uses_shared_quote_repair():
    raw = '{"items":[{"question":"是否按"30日内"执行？"}]}'
    calls = extract_tool_calls(
        _chat_tool_response("record_pending_confirmation", raw)
    )
    assert calls[0]["arguments"]["items"][0]["question"] == '是否按"30日内"执行？'
    assert calls[0]["argument_metadata"]["applied"] is True


def test_fresh_patch_uses_shared_quote_repair():
    raw = '{"context_before":"各阶段遵循"先准备后实施、先测试后切换"原则。"}'
    calls = extract_tool_calls(_chat_tool_response("submit_structured_patch", raw))
    assert calls[0]["arguments"]["context_before"] == '各阶段遵循"先准备后实施、先测试后切换"原则。'
    assert calls[0]["argument_metadata"]["repair_count"] == 2


def test_fresh_quote_repair_sets_success_report_state(tmp_path: Path):
    from tests.test_technical_writing_fresh_repair import product_errors, run_agent

    class QuotePatchModel:
        def __init__(self, initial: dict):
            self.initial = initial
            self.turn = 0

        def run_with_tools(self, *, messages, **kwargs):
            self.turn += 1
            metadata = {"provider": "fake", "model": "fake", "usage": empty_usage()}
            if self.turn == 1:
                return ToolModelResult(
                    "final", [], json.dumps(self.initial, ensure_ascii=False), metadata
                )
            request = json.loads(
                next(item["content"] for item in messages if item.get("role") == "user")
            )
            card = request["repair_task_cards"][0]
            response = {
                "base_candidate_hash": request["base_candidate_hash"],
                "repair_results": [
                    {
                        "issue_fingerprint": card["issue_fingerprint"],
                        "section_id": card["section_id"],
                        "candidates": [
                            {
                                "candidate_id": "C1",
                                "candidate_role": "primary",
                                "operation_type": "replace_sentence",
                                "replacement_text": (
                                    '部署过程依据招标要求和"确认后的技术资料"组织配置，'
                                    "完成后通过测试用例核验相关要求。"
                                ),
                            }
                        ],
                    }
                ],
            }
            raw = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            raw = raw.replace('\\"确认后的技术资料\\"', '"确认后的技术资料"')
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": "patch-quote",
                        "name": "submit_structured_patch",
                        "arguments": raw,
                    }
                ],
                None,
                metadata,
            )

    result = run_agent(tmp_path, QuotePatchModel(product_errors(1)))
    regeneration = result["fresh_quality_regeneration"]
    patch_report = regeneration["fresh_patch"]
    assert regeneration["model_response_parse_failed"] is False
    assert patch_report["protocol_validated"] is True
    assert patch_report["json_syntax_repair_applied"] is True
    assert patch_report["json_syntax_repair"]["repair_count"] == 2
    assert Path(
        patch_report["json_syntax_repair"]["repaired_response_path"]
    ).exists()


def test_quote_repair_preserves_string_semantics():
    raw = '{"text":"按照"术语A"执行"}'
    result = parse_model_tool_arguments(raw, tool_name="test")
    assert result.arguments == {"text": '按照"术语A"执行'}
    assert '\\"术语A\\"' in result.repaired_arguments


def test_valid_json_is_not_modified():
    raw = json.dumps({"text": '按照"术语A"执行'}, ensure_ascii=False)
    result = parse_model_tool_arguments(raw, tool_name="test")
    assert result.repaired_arguments == raw
    assert result.json_syntax_repair["attempted"] is False
    assert result.json_syntax_repair["applied"] is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":"x"\n"b":"y"}',
        '{"a":"未结束',
        '以下是结果：{"a":"b"}',
        '{"items":[{"a":"b"}',
        '{"a":"x","b":"y" "c":"z"}',
    ],
)
def test_quote_repair_rejects_unsupported_json_errors(raw: str):
    with pytest.raises(ModelJsonParseError):
        parse_model_tool_arguments(raw, tool_name="test")


def test_quote_repair_does_not_fix_missing_field_comma():
    with pytest.raises(ModelJsonParseError):
        parse_model_tool_arguments('{"a":"x"\n"b":"y"}', tool_name="test")


def test_quote_repair_does_not_fix_truncated_json():
    with pytest.raises(ModelJsonParseError):
        parse_model_tool_arguments('{"a":"未结束', tool_name="test")


def test_quote_repair_does_not_extract_markdown_json():
    with pytest.raises(ModelJsonParseError):
        parse_model_tool_arguments('```json\n{"a":"b"}\n```', tool_name="test")


def test_quote_repair_does_not_add_missing_brackets():
    with pytest.raises(ModelJsonParseError):
        parse_model_tool_arguments('{"items":[{"a":"b"}', tool_name="test")


def test_quote_repair_stops_at_max_repairs():
    inner = "并".join(
        f'"v{index}"' for index in range(MAX_TOOL_ARGUMENT_QUOTE_REPAIRS + 2)
    )
    raw = '{"text":"前' + inner + '后"}'
    with pytest.raises(ModelJsonParseError) as caught:
        parse_model_tool_arguments(raw, tool_name="test")
    repair = caught.value.safe_diagnostics()["json_syntax_repair"]
    assert repair["repair_count"] == MAX_TOOL_ARGUMENT_QUOTE_REPAIRS
    assert repair["applied"] is False


def test_schema_invalid_object_remains_rejected():
    context = repair_context()
    value = {
        "base_candidate_hash": context["initial_candidate_sha256"],
        "repair_results": "不是数组",
    }
    parsed = parse_model_tool_arguments(
        json.dumps(value, ensure_ascii=False),
        tool_name="submit_structured_patch",
    )
    with pytest.raises(ValidationError):
        _fresh_patch_protocol_payload(parsed.arguments)


def test_quote_repair_report_records_count_and_hashes():
    raw = '{"text":"按照"术语A"执行"}'
    report = parse_model_tool_arguments(raw, tool_name="test").json_syntax_repair
    assert report["attempted"] is True
    assert report["applied"] is True
    assert report["repair_count"] == 2
    assert len(report["original_sha256"]) == 64
    assert len(report["repaired_sha256"]) == 64
    assert report["repaired_character_count"] == report["original_character_count"] + 2


def test_quote_repair_events_do_not_include_raw_arguments(tmp_path: Path):
    raw = '{"text":"敏感正文中的"内部术语"不得进入事件"}'
    parsed = parse_model_tool_arguments(raw, tool_name="submit_structured_patch")
    calls = [
        {
            "name": "submit_structured_patch",
            "arguments": parsed.arguments,
            "argument_metadata": {
                **parsed.json_syntax_repair,
                "_original_arguments": parsed.raw_arguments,
                "_repaired_arguments": parsed.repaired_arguments,
            },
        }
    ]
    events: list[dict] = []

    def emit(event_type: str, *, step: int, data: dict):
        events.append({"event_type": event_type, "step": step, "data": data})

    reports = _record_tool_argument_json_repairs(
        tool_calls=calls,
        step=2,
        output_root=tmp_path,
        output_key="repair-debug",
        target_id="CH-001",
        secret="",
        emit=emit,
    )
    serialized = json.dumps(events, ensure_ascii=False)
    assert "敏感正文" not in serialized
    assert reports[0]["original_response_saved"] is True
    assert reports[0]["repaired_response_saved"] is True
    assert Path(reports[0]["repaired_response_path"]).exists()


def test_failed_quote_repair_preserves_invalid_json_diagnostics():
    raw = '{"a":"x"\n"b":"y"}'
    with pytest.raises(ModelJsonParseError) as caught:
        parse_model_tool_arguments(raw, tool_name="record_pending_confirmation")
    diagnostics = caught.value.safe_diagnostics()
    assert diagnostics["json_context"] == "tool_arguments"
    assert diagnostics["json_syntax_repair"]["attempted"] is True
    assert diagnostics["json_syntax_repair"]["applied"] is False
    assert caught.value.raw_content == raw


def test_patch_candidate_schema_does_not_require_original_text():
    required = _fresh_patch_response_schema()["properties"]["repair_results"]["items"]["properties"]["candidates"]["items"]["required"]
    assert "original_text" not in required
    assert "candidate_role" not in required


def test_patch_candidate_schema_does_not_require_context_fields():
    candidate_schema = _fresh_patch_response_schema()["properties"]["repair_results"]["items"]["properties"]["candidates"]["items"]
    assert "context_before" not in candidate_schema["properties"]
    assert "context_after" not in candidate_schema["properties"]


def test_patch_runtime_restores_sentence_anchor_from_task():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert attempt["original_text"] == BAD_A


def test_patch_runtime_restores_span_anchor_from_task():
    context = repair_context()
    patch = candidate(
        context,
        "按计划",
        candidate_id="C1",
        operation_type="replace_span",
    )
    initial = context["initial_candidate"]

    def validation(value: dict) -> dict:
        text = value["sections"][0]["content"]
        count = int("每周" in text)
        return {
            **snapshot(value),
            "raw_error_count": count,
            "blocking_error_categories": (
                [("schedule_frequency", "schedule_frequency")] if count else []
            ),
        }

    _, report = _apply_validated_fresh_patches(
        initial=initial,
        patch_payload=payload(context, [patch]),
        repair_context=context,
        fact_ledger=ledger(),
        initial_validation=validation(initial),
        validate_candidate=validation,
    )
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert attempt["original_text"] == context["repair_task_cards"][0]["target"]["offending_span"]


def test_patch_runtime_restores_context_from_task():
    context = repair_context()
    card = context["repair_task_cards"][0]
    card["target"]["context_before"] = "前置句。"
    card["target"]["context_after"] = "后续句。"
    _, report = apply(context, [candidate(context, SAFE_A)])
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert attempt["context_before"] == "前置句。"
    assert attempt["context_after"] == "后续句。"


def test_replace_span_requires_task_offending_span():
    context = repair_context()
    context["repair_task_cards"][0]["target"]["offending_span"] = ""
    patch = candidate(
        context,
        "按计划",
        candidate_id="C1",
        operation_type="replace_span",
    )
    _, report = apply(context, [patch])
    reasons = report["issue_results"][0]["candidate_attempts"][0]["reason_codes"]
    assert "fresh_patch_operation_target_invalid" in reasons


def test_applied_operation_report_contains_restored_original_text():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    assert report["applied_operations"][0]["original_text"] == BAD_A


def test_candidate_attempt_report_contains_restored_context():
    context = repair_context()
    _, report = apply(context, [candidate(context, SAFE_A)])
    attempt = report["issue_results"][0]["candidate_attempts"][0]
    assert "context_before" in attempt
    assert "context_after" in attempt

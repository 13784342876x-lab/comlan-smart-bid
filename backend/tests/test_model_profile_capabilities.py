from __future__ import annotations

import inspect
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.llm_errors import ModelConfigurationError, ModelJsonParseError
from app.llm_profiles import (
    LLMProfile,
    ModelCapabilities,
    RequestMapping,
    load_environment,
    resolve_profile,
)
from app.openai_compatible_llm_client import (
    OpenAICompatibleLLMClient,
    build_tool_request_kwargs,
    parse_json_object,
)
from app.web_search_provider import ArkBuiltinWebSearchProvider, get_web_search_provider


class Endpoint:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.actions.pop(0)


class FakeSDK:
    def __init__(self, actions):
        self.endpoint = Endpoint(actions)
        self.responses = self.endpoint
        self.chat = SimpleNamespace(completions=self.endpoint)


def profile(
    *,
    name="generic",
    api_mode="chat_completions",
    supports_reasoning=True,
    replay=True,
    supports_developer=False,
    supports_tool_choice=False,
    supports_tool_choice_none=False,
    finalization="omit_tools",
    token_parameter="max_tokens",
    extra_body=None,
):
    return LLMProfile(
        name=name,
        provider="openai_compatible",
        protocol="openai_compatible",
        base_url="https://provider.example/v1",
        api_key_env="TEST_API_KEY",
        model_env="",
        api_key="fake-key",
        model="fake-model",
        api_mode=api_mode,
        structured_output_mode="prompt_json",
        timeout_seconds=5,
        max_retries=0,
        max_output_tokens=321,
        temperature=None,
        enabled=True,
        use_instructions=False,
        send_temperature=False,
        send_max_output_tokens=True,
        send_thinking=bool(extra_body),
        send_metadata=False,
        extra_body=extra_body or {},
        extra_headers={},
        stream=False,
        capabilities=ModelCapabilities(
            supports_tools=True,
            supports_parallel_tool_calls=True,
            supports_streaming=True,
            supports_structured_output=True,
            supports_reasoning_content=supports_reasoning,
            requires_reasoning_content_replay=replay,
            supports_developer_role=supports_developer,
            supports_tool_choice=supports_tool_choice,
            supports_tool_choice_none=supports_tool_choice_none,
            supports_usage_in_stream=True,
        ),
        request_mapping=RequestMapping(
            system_role="system",
            token_limit_parameter=token_parameter,
            finalization_strategy=finalization,
            thinking_enabled=bool(extra_body),
            reasoning_effort="high" if extra_body else None,
        ),
    )


def client(actions, profile_value=None):
    sdk = FakeSDK(actions)
    value = profile_value or profile()
    return (
        OpenAICompatibleLLMClient(value, client_factory=lambda **_: sdk),
        sdk,
    )


def tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            },
        }
    ]


def chat_response(*, content=None, reasoning=None, calls=None, finish_reason=None):
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
            )
            for call_id, name, arguments in (calls or [])
        ],
    )
    return SimpleNamespace(
        id="chat-fake",
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason=finish_reason or ("tool_calls" if calls else "stop"),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3, total_tokens=7),
    )


def run_turn(model, messages, *, tool_choice="auto", stream=False, events=None):
    return model.run_with_tools(
        messages=messages,
        tools=tools(),
        tool_choice=tool_choice,
        options={
            "stream": stream,
            **({"stream_event_handler": events.append} if events is not None else {}),
        },
    )


def test_builtin_deepseek_profiles_and_ark_profile_are_selectable():
    draft = resolve_profile(
        profile_name="deepseek_draft",
        environ={"DEEPSEEK_API_KEY": "fake"},
        load_dotenv_file=False,
    )
    fast = resolve_profile(
        profile_name="deepseek_fast",
        environ={"DEEPSEEK_API_KEY": "fake"},
        load_dotenv_file=False,
    )
    ark = resolve_profile(
        profile_name="ark_draft",
        environ={"ARK_API_KEY": "fake", "COMLAN_ARK_DRAFT_MODEL": "endpoint"},
        load_dotenv_file=False,
    )
    assert (draft.model, fast.model) == ("deepseek-v4-pro", "deepseek-v4-flash")
    assert draft.capabilities.requires_reasoning_content_replay is True
    assert draft.request_mapping.finalization_strategy == "omit_tools"
    assert fast.request_mapping.thinking_enabled is False
    assert ark.api_style == "responses"


def test_missing_deepseek_key_is_clear_and_does_not_leak():
    with pytest.raises(ModelConfigurationError) as caught:
        resolve_profile(
            profile_name="deepseek_draft",
            environ={},
            load_dotenv_file=False,
        )
    assert "DEEPSEEK_API_KEY" in str(caught.value)
    assert "Authorization" not in str(caught.value)


def test_process_environment_overrides_env_local_and_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ORDER_VALUE=env\nONLY_ENV=yes\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("ORDER_VALUE=local\n", encoding="utf-8")
    monkeypatch.setenv("ORDER_VALUE", "process")
    loaded = load_environment(root=tmp_path)
    assert loaded["ORDER_VALUE"] == "process"
    assert loaded["ONLY_ENV"] == "yes"


def test_workspace_root_dotenv_local_overrides_legacy_backend_env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    backend = project / "backend"
    backend.mkdir(parents=True)
    (backend / ".env").write_text("ROOT_ORDER=backend-env\n", encoding="utf-8")
    (project / ".env").write_text("ROOT_ORDER=root-env\n", encoding="utf-8")
    (project / ".env.local").write_text("ROOT_ORDER=root-local\n", encoding="utf-8")
    monkeypatch.setattr("app.llm_profiles.BACKEND_ROOT", backend)
    monkeypatch.setattr("app.llm_profiles.PROJECT_ROOT", project)
    monkeypatch.delenv("ROOT_ORDER", raising=False)
    assert load_environment()["ROOT_ORDER"] == "root-local"


def test_profile_files_store_only_api_key_environment_name():
    from app.llm_profiles import DEFAULT_PROFILE_CONFIG

    text = DEFAULT_PROFILE_CONFIG.read_text(encoding="utf-8")
    assert '"api_key_env": "DEEPSEEK_API_KEY"' in text
    assert '"api_key":' not in text


@pytest.mark.parametrize(
    "content",
    [
        '{"value":"ok"}',
        '\ufeff  {"value":"ok"}  ',
        '```json\n{"value":"ok"}\n```',
        '简短说明：\n{"value":"ok"}\n以上。',
        '说明 {"value":"字符串内部允许 {大括号}"} 结束',
    ],
)
def test_deterministic_json_extraction_accepts_one_complete_object(content):
    assert parse_json_object(content)["value"]


def test_deterministic_json_extraction_rejects_conflicting_objects():
    with pytest.raises(ModelJsonParseError, match="multiple conflicting"):
        parse_json_object('first {"value":1} second {"value":2}')


def test_json_parse_error_marks_unclosed_output_as_suspected_truncation():
    with pytest.raises(ModelJsonParseError) as caught:
        parse_json_object('{"chapter_title":"x","sections":[')
    diagnostics = caught.value.safe_diagnostics()
    assert diagnostics["suspected_truncation"] is True
    assert diagnostics["content_character_count"] > 0


def test_chat_plain_text_and_non_stream_single_and_multiple_tools():
    model, _ = client(
        [
            chat_response(content="done"),
            chat_response(reasoning="private", calls=[("one", "lookup", {"q": "a"})]),
            chat_response(
                reasoning="private-two",
                calls=[
                    ("two", "lookup", {"q": "b"}),
                    ("three", "lookup", {"q": "c"}),
                ],
            ),
        ]
    )
    final = run_turn(model, [{"role": "user", "content": "work"}])
    one = run_turn(model, [{"role": "user", "content": "work"}])
    many = run_turn(model, [{"role": "user", "content": "work"}])
    assert final.final_content == "done"
    assert one.tool_calls == [{"tool_call_id": "one", "name": "lookup", "arguments": {"q": "a"}}]
    assert [item["tool_call_id"] for item in many.tool_calls] == ["two", "three"]
    assert one.assistant_turn.reasoning_content == "private"


def test_reasoning_replay_is_capability_driven_across_multiple_tool_rounds():
    first = chat_response(reasoning="reason-one", calls=[("one", "lookup", {"q": "a"})])
    second = chat_response(reasoning="reason-two", calls=[("two", "lookup", {"q": "b"})])
    final = chat_response(content="done")
    model, sdk = client([first, second, final])
    messages = [{"role": "user", "content": "work"}]
    turn_one = run_turn(model, messages)
    messages.extend(
        [
            turn_one.context_message(),
            {"role": "tool", "tool_call_id": "one", "content": '{"ok":1}'},
        ]
    )
    turn_two = run_turn(model, messages)
    messages.extend(
        [
            turn_two.context_message(),
            {"role": "tool", "tool_call_id": "two", "content": '{"ok":2}'},
        ]
    )
    turn_three = run_turn(model, messages)
    assert turn_three.final_content == "done"
    replayed = sdk.endpoint.calls[2]["messages"]
    assistant_messages = [item for item in replayed if item["role"] == "assistant"]
    assert [item["reasoning_content"] for item in assistant_messages] == [
        "reason-one",
        "reason-two",
    ]


def test_reasoning_is_saved_but_not_replayed_when_capability_is_false():
    no_replay = profile(replay=False)
    model, sdk = client(
        [
            chat_response(reasoning="private", calls=[("one", "lookup", {"q": "a"})]),
            chat_response(content="done"),
        ],
        no_replay,
    )
    first = run_turn(model, [{"role": "user", "content": "work"}])
    assert first.assistant_turn.reasoning_content == "private"
    messages = [
        {"role": "user", "content": "work"},
        first.context_message(),
        {"role": "tool", "tool_call_id": "one", "content": "{}"},
    ]
    run_turn(model, messages)
    assistant = next(item for item in sdk.endpoint.calls[1]["messages"] if item["role"] == "assistant")
    assert "reasoning_content" not in assistant


def test_streaming_reasoning_text_multiple_tool_calls_usage_and_events():
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="hidden-",
                        tool_calls=[],
                    ),
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="reason",
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-a",
                                function=SimpleNamespace(name="lookup", arguments='{"q":"a"}'),
                            ),
                            SimpleNamespace(
                                index=1,
                                id="call-b",
                                function=SimpleNamespace(name="lookup", arguments='{"q":"b"}'),
                            ),
                        ],
                    ),
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9),
        ),
    ]
    model, _ = client([iter(chunks)])
    events = []
    result = run_turn(
        model,
        [{"role": "user", "content": "work"}],
        stream=True,
        events=events,
    )
    assert result.assistant_turn.reasoning_content == "hidden-reason"
    assert [item["tool_call_id"] for item in result.tool_calls] == ["call-a", "call-b"]
    kinds = [item["type"] for item in events]
    assert kinds.count("reasoning_delta") == 2
    assert kinds.count("tool_call_completed") == 2
    assert "usage_update" in kinds
    assert kinds[-1] == "response_completed"
    assert "hidden-reason" not in json.dumps(events, ensure_ascii=False)


def test_streaming_incomplete_tool_arguments_are_explicit_error():
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="bad",
                                function=SimpleNamespace(name="lookup", arguments='{"q":'),
                            )
                        ],
                    ),
                )
            ],
            usage=None,
        )
    ]
    model, _ = client([iter(chunks)])
    with pytest.raises(ModelJsonParseError, match="tool arguments"):
        run_turn(model, [{"role": "user", "content": "work"}], stream=True)


def test_developer_messages_are_merged_or_preserved_by_capability():
    messages = [
        {"role": "system", "content": "[Agent System Rules]\nA"},
        {"role": "developer", "content": "[Technical Writing Rules]\nB"},
        {"role": "developer", "content": "[Project Constraints]\nC"},
        {"role": "developer", "content": "[Output Schema]\nD"},
        {"role": "user", "content": "work"},
    ]
    merged_profile = profile()
    _, merged = build_tool_request_kwargs(
        merged_profile,
        messages=messages,
        tools=tools(),
        tool_choice="auto",
        options={},
        request_metadata={},
    )
    assert [item["role"] for item in merged["messages"]] == ["system", "user"]
    assert all(
        section in merged["messages"][0]["content"]
        for section in [
            "[Agent System Rules]",
            "[Technical Writing Rules]",
            "[Project Constraints]",
            "[Output Schema]",
        ]
    )

    developer_profile = profile(
        supports_developer=True,
        supports_tool_choice=True,
        supports_tool_choice_none=True,
        finalization="tool_choice_none",
    )
    _, preserved = build_tool_request_kwargs(
        developer_profile,
        messages=messages,
        tools=tools(),
        tool_choice="auto",
        options={},
        request_metadata={},
    )
    assert [item["role"] for item in preserved["messages"]] == [
        "system",
        "developer",
        "developer",
        "developer",
        "user",
    ]


def test_tool_choice_and_finalization_are_profile_mapped():
    deep = profile()
    _, normal = build_tool_request_kwargs(
        deep,
        messages=[{"role": "user", "content": "work"}],
        tools=tools(),
        tool_choice="auto",
        options={},
        request_metadata={},
    )
    _, final = build_tool_request_kwargs(
        deep,
        messages=[{"role": "user", "content": "work"}],
        tools=tools(),
        tool_choice="none",
        options={},
        request_metadata={},
    )
    assert "tool_choice" not in normal
    assert "tools" in normal
    assert "tool_choice" not in final
    assert "tools" not in final

    generic = profile(
        supports_developer=True,
        supports_tool_choice=True,
        supports_tool_choice_none=True,
        finalization="tool_choice_none",
    )
    _, generic_final = build_tool_request_kwargs(
        generic,
        messages=[{"role": "user", "content": "work"}],
        tools=tools(),
        tool_choice="none",
        options={},
        request_metadata={},
    )
    assert generic_final["tools"] == tools()
    assert generic_final["tool_choice"] == "none"


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
def test_token_limit_parameter_is_exclusive(parameter):
    value = profile(token_parameter=parameter)
    _, kwargs = build_tool_request_kwargs(
        value,
        messages=[{"role": "user", "content": "work"}],
        tools=[],
        tool_choice="none",
        options={"max_output_tokens": 222},
        request_metadata={},
    )
    assert kwargs[parameter] == 222
    assert len({"max_tokens", "max_completion_tokens"} & set(kwargs)) == 1


def test_extra_body_and_reasoning_effort_only_follow_selected_profile():
    deep = profile(extra_body={"thinking": {"type": "enabled"}})
    generic = profile(extra_body={})
    _, deep_kwargs = build_tool_request_kwargs(
        deep,
        messages=[{"role": "user", "content": "work"}],
        tools=tools(),
        tool_choice="auto",
        options={},
        request_metadata={},
    )
    _, generic_kwargs = build_tool_request_kwargs(
        generic,
        messages=[{"role": "user", "content": "work"}],
        tools=tools(),
        tool_choice="auto",
        options={},
        request_metadata={},
    )
    assert deep_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert deep_kwargs["reasoning_effort"] == "high"
    assert "extra_body" not in generic_kwargs
    assert "reasoning_effort" not in generic_kwargs


def test_safe_run_report_profile_has_no_key_or_reasoning_body():
    value = profile(name="deepseek_draft", extra_body={"thinking": {"type": "enabled"}})
    summary = value.run_report_summary()
    rendered = json.dumps(summary, ensure_ascii=False)
    assert summary["base_url_host"] == "provider.example"
    assert summary["api_key_env"] == "TEST_API_KEY"
    assert "fake-key" not in rendered
    assert "private reasoning body" not in rendered
    assert "reasoning_content" not in summary


def test_technical_agent_has_no_provider_or_model_name_branches():
    import app.technical_writing_agent as module

    source = inspect.getsource(module)
    assert 'provider == "deepseek"' not in source
    assert 'provider == "ark"' not in source
    assert 'model.startswith("deepseek")' not in source


def test_web_search_can_use_independent_ark_client_from_deepseek_main():
    deep_client = SimpleNamespace(profile=profile(name="deepseek_draft"), is_mock=False)
    ark_profile = replace(
        profile(
            name="ark_web_search",
            api_mode="responses",
            supports_reasoning=False,
            replay=False,
            supports_developer=True,
            supports_tool_choice=True,
            supports_tool_choice_none=True,
            finalization="tool_choice_none",
            token_parameter="max_output_tokens",
        ),
        provider="volcengine_ark",
    )
    ark_client = SimpleNamespace(
        profile=ark_profile,
        run_builtin_web_search=lambda **_: {"status": "success"},
    )
    provider = get_web_search_provider(
        "ark_builtin",
        llm_client=deep_client,
        independent_llm_client=ark_client,
    )
    assert isinstance(provider, ArkBuiltinWebSearchProvider)
    assert provider.llm_client is ark_client

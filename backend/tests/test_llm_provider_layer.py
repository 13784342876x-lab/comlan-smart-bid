import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.chapter_draft_schema import load_chapter_draft_schema
from app.llm_client import MockLLMClient, get_llm_client
from app.llm_errors import (
    ModelAuthenticationError,
    ModelConfigurationError,
    ModelJsonParseError,
    ModelOutputIncompleteError,
    ModelPermissionError,
    ModelResponseError,
    ModelSchemaValidationError,
)
from app.llm_profiles import (
    LLMProfile,
    RequestMapping,
    list_profile_summaries,
    profile_from_mapping,
    resolve_profile,
    select_profile_name,
    load_profile_config,
)
from app.llm_smoke_test import build_smoke_profile, check_drafting_model, inspect_drafting_request
from app.openai_compatible_llm_client import OpenAICompatibleLLMClient, build_request_kwargs


@pytest.fixture(autouse=True)
def isolate_user_local_profile_config(monkeypatch, tmp_path):
    """Unit tests must not merge a developer's ignored local model settings."""
    monkeypatch.setattr(
        "app.llm_profiles.DEFAULT_LOCAL_PROFILE_CONFIG",
        tmp_path / "absent-llm-profiles.local.json",
    )


def valid_chapter_output():
    return {
        "chapter_title": "模型连通测试",
        "sections": [
            {
                "heading": "技术方案一",
                "block_type": "technical_solution",
                "content": "本项目拟建立可追溯的数据治理机制。",
                "covered_score_item_ids": ["smoke-score-001"],
                "covered_requirement_ids": ["smoke-requirement-001"],
                "placeholder_ids": ["smoke-placeholder-001"],
            },
            {
                "heading": "技术方案二",
                "block_type": "implementation_plan",
                "content": "项目实施过程中将进行阶段检查。",
                "covered_score_item_ids": ["smoke-score-001"],
                "covered_requirement_ids": ["smoke-requirement-001"],
                "placeholder_ids": [],
            },
        ],
        "claims": [
            {
                "text": "本项目拟建立可追溯的数据治理机制。",
                "claim_type": "proposal_statement",
                "source_ids": ["smoke-score-001", "smoke-requirement-001"],
                "evidence_ids": [],
                "human_review_required": False,
            }
        ],
        "placeholders_used": ["smoke-placeholder-001"],
        "score_items_covered": ["smoke-score-001"],
        "requirements_covered": ["smoke-requirement-001"],
        "warnings": [],
    }


def fake_response(value=None, *, mode="responses"):
    text = value if isinstance(value, str) else json.dumps(value or valid_chapter_output(), ensure_ascii=False)
    usage = SimpleNamespace(input_tokens=11, output_tokens=22, total_tokens=33)
    if mode == "responses":
        return SimpleNamespace(id="resp-001", output_text=text, status="completed", usage=usage)
    message = SimpleNamespace(content=text, refusal=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    chat_usage = SimpleNamespace(prompt_tokens=11, completion_tokens=22, total_tokens=33)
    return SimpleNamespace(id="chat-001", choices=[choice], usage=chat_usage)


def fake_minimal_response():
    return fake_response({"status": "ok"})


def incomplete_reasoning_response(*, reasoning_tokens=1180, output_tokens=1200):
    usage = SimpleNamespace(
        input_tokens=701,
        output_tokens=output_tokens,
        total_tokens=701 + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=23),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens, answer_tokens=0),
    )
    reasoning_item = SimpleNamespace(type="reasoning", content=[], summary="private reasoning must not persist")
    return SimpleNamespace(
        id="resp-incomplete-001",
        output_text="",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=usage,
        output=[reasoning_item],
    )


class Endpoint:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class FakeSDKClient:
    def __init__(self, actions):
        self.endpoint = Endpoint(actions)
        self.responses = self.endpoint
        self.chat = SimpleNamespace(completions=self.endpoint)


class StatusError(Exception):
    def __init__(self, status_code, *, body=None, request_id=None, message=None):
        super().__init__(message or "provider error with hidden request details")
        self.status_code = status_code
        self.body = body
        self.request_id = request_id


def make_profile(**overrides):
    values = {
        "name": "test_profile",
        "provider": "test_provider",
        "protocol": "openai_compatible",
        "base_url": "https://provider.example/v1",
        "api_key_env": "TEST_API_KEY",
        "model_env": "TEST_MODEL",
        "api_key": "test-secret-key",
        "model": "test-model",
        "api_mode": "responses",
        "structured_output_mode": "json_schema",
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "max_output_tokens": 1000,
        "temperature": 0.1,
        "enabled": True,
        "use_instructions": True,
        "send_temperature": True,
        "send_max_output_tokens": True,
        "send_thinking": True,
        "send_metadata": True,
        "extra_body": {},
        "extra_headers": {},
    }
    values.update(overrides)
    return LLMProfile(**values)


def make_client(actions, **profile_overrides):
    sdk = FakeSDKClient(actions)
    init_kwargs = {}

    def factory(**kwargs):
        init_kwargs.update(kwargs)
        return sdk

    client = OpenAICompatibleLLMClient(
        make_profile(**profile_overrides),
        client_factory=factory,
        sleep_fn=lambda _seconds: None,
        random_fn=lambda: 0.0,
    )
    return client, sdk, init_kwargs


def write_config(path: Path, *, api_mode="responses", output_mode="json_schema"):
    value = {
        "schema_version": "1.0",
        "default_profile": "draft",
        "role_bindings": {"drafting": "draft", "review": "review", "fast_task": "fast"},
        "profiles": {
            name: {
                "provider": "volcengine_ark",
                "protocol": "openai_compatible",
                "base_url": "https://ark.example/v3",
                "api_key_env": "ARK_API_KEY",
                "model_env": model_env,
                "api_mode": api_mode,
                "structured_output_mode": output_mode,
                "timeout_seconds": 10,
                "max_retries": 2,
                "max_output_tokens": 1000,
                "temperature": 0.1,
                "enabled": True,
                "extra_body": {},
                "extra_headers": {},
            }
            for name, model_env in {
                "draft": "DRAFT_MODEL",
                "review": "REVIEW_MODEL",
                "fast": "FAST_MODEL",
                "override": "OVERRIDE_MODEL",
            }.items()
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")


def generate(client):
    return client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        response_schema=load_chapter_draft_schema(),
        request_metadata={"purpose": "test"},
    )


def test_volcengine_ark_example_profile_loads():
    profile = resolve_profile(
        profile_name="ark_draft",
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "endpoint-id"},
        load_dotenv_file=False,
    )
    assert profile.provider == "volcengine_ark"
    assert profile.base_url == "https://ark.cn-beijing.volces.com/api/v3"


def test_openai_compatible_client_uses_profile_values_not_ark_constants():
    client, _, init_kwargs = make_client([fake_response()], base_url="https://other.example/v1", api_key="other-secret")
    generate(client)
    assert init_kwargs["base_url"] == "https://other.example/v1"
    assert init_kwargs["api_key"] == "other-secret"


def test_missing_api_key_is_explicit_configuration_error():
    with pytest.raises(ModelConfigurationError, match="ARK_API_KEY"):
        resolve_profile(
            profile_name="ark_draft",
            environ={"COMLAN_ARK_DRAFT_MODEL": "endpoint-id"},
            load_dotenv_file=False,
        )


def test_missing_model_environment_is_explicit_configuration_error():
    with pytest.raises(ModelConfigurationError, match="COMLAN_ARK_DRAFT_MODEL"):
        resolve_profile(profile_name="ark_draft", environ={"ARK_API_KEY": "secret"}, load_dotenv_file=False)


def test_unsupported_api_mode_is_rejected(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path, api_mode="unsupported")
    with pytest.raises(ModelConfigurationError, match="unsupported api_mode"):
        resolve_profile(
            config_path=path,
            profile_name="draft",
            environ={"ARK_API_KEY": "secret", "DRAFT_MODEL": "model"},
            load_dotenv_file=False,
        )


def test_unsupported_structured_output_mode_is_rejected(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path, output_mode="unsupported")
    with pytest.raises(ModelConfigurationError, match="structured_output_mode"):
        resolve_profile(
            config_path=path,
            profile_name="draft",
            environ={"ARK_API_KEY": "secret", "DRAFT_MODEL": "model"},
            load_dotenv_file=False,
        )


def test_mock_structured_result_is_identified_as_mock():
    result = MockLLMClient().generate_structured(
        system_prompt="x", user_prompt="y", response_schema=load_chapter_draft_schema()
    )
    assert result.provider == "mock" and result.finish_status == "mock"


def test_multiple_profiles_resolve_different_models(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path)
    env = {
        "ARK_API_KEY": "secret",
        "DRAFT_MODEL": "draft-model",
        "REVIEW_MODEL": "review-model",
        "FAST_MODEL": "fast-model",
        "OVERRIDE_MODEL": "override-model",
    }
    assert resolve_profile(config_path=path, profile_name="draft", environ=env, load_dotenv_file=False).model == "draft-model"
    assert resolve_profile(config_path=path, profile_name="review", environ=env, load_dotenv_file=False).model == "review-model"


def test_drafting_role_selects_draft_profile(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path)
    config = load_profile_config(path, environ={}, load_dotenv_file=False)
    assert select_profile_name(config, role="drafting", environ={}) == "draft"


def test_review_role_selects_review_profile(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path)
    config = load_profile_config(path, environ={}, load_dotenv_file=False)
    assert select_profile_name(config, role="review", environ={}) == "review"


def test_explicit_profile_overrides_role_and_environment(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path)
    config = load_profile_config(path, environ={}, load_dotenv_file=False)
    assert select_profile_name(
        config,
        role="drafting",
        profile_name="override",
        environ={"COMLAN_LLM_DRAFT_PROFILE": "fast"},
    ) == "override"


def test_responses_api_mode_calls_responses_endpoint():
    client, sdk, _ = make_client([fake_response()])
    result = generate(client)
    assert result.api_mode == "responses"
    assert "instructions" in sdk.endpoint.calls[0] and "text" in sdk.endpoint.calls[0]


def test_chat_completions_api_mode_calls_chat_endpoint():
    client, sdk, _ = make_client([fake_response(mode="chat")], api_mode="chat_completions")
    result = generate(client)
    assert result.api_mode == "chat_completions"
    assert "messages" in sdk.endpoint.calls[0] and "response_format" in sdk.endpoint.calls[0]


def test_json_schema_mode_still_validates_locally():
    invalid = valid_chapter_output()
    invalid.pop("claims")
    client, sdk, _ = make_client([fake_response(invalid), fake_response(invalid)])
    with pytest.raises(ModelSchemaValidationError):
        generate(client)
    assert len(sdk.endpoint.calls) == 2


def test_prompt_json_mode_uses_same_schema_and_prompt_instruction():
    client, sdk, _ = make_client([fake_response()], structured_output_mode="prompt_json")
    result = generate(client)
    assert result.parsed_output["chapter_title"] == "模型连通测试"
    assert "JSON Schema" in sdk.endpoint.calls[0]["input"]
    assert "text" not in sdk.endpoint.calls[0]


def test_prompt_json_responses_sends_no_native_schema_parameters():
    profile = make_profile(structured_output_mode="prompt_json", use_instructions=False)
    _, kwargs = build_request_kwargs(
        profile,
        system_prompt="system",
        user_prompt="user",
        response_schema=load_chapter_draft_schema(),
    )
    assert "text" not in kwargs
    assert "response_format" not in kwargs
    assert "json_schema" not in json.dumps(kwargs, ensure_ascii=False)


def test_responses_capabilities_omit_disabled_optional_parameters():
    profile = make_profile(
        structured_output_mode="prompt_json",
        use_instructions=False,
        send_temperature=False,
        send_max_output_tokens=False,
        send_thinking=False,
        send_metadata=False,
        extra_body={"thinking": {"type": "enabled"}},
    )
    _, kwargs = build_request_kwargs(
        profile,
        system_prompt="system constraint",
        user_prompt="user task",
        response_schema=load_chapter_draft_schema(),
        request_metadata={"purpose": "test"},
    )
    assert "instructions" not in kwargs
    assert "temperature" not in kwargs
    assert "max_output_tokens" not in kwargs
    assert "metadata" not in kwargs
    assert "extra_body" not in kwargs
    assert "thinking" not in kwargs
    assert "system constraint" in kwargs["input"]
    assert "user task" in kwargs["input"]


def test_formal_drafting_profile_can_keep_thinking_and_timeout_enabled():
    client, sdk, init_kwargs = make_client(
        [fake_response()],
        timeout_seconds=240.0,
        max_retries=2,
        max_output_tokens=8000,
        send_thinking=True,
        extra_body={"thinking": {"type": "enabled"}},
    )
    generate(client)
    assert init_kwargs["timeout"] == 240.0
    assert client.profile.max_retries == 2
    assert sdk.endpoint.calls[0]["max_output_tokens"] == 8000
    assert sdk.endpoint.calls[0]["extra_body"]["thinking"]["type"] == "enabled"


def test_minimal_responses_prompt_json_request_succeeds_with_fake_client():
    client, sdk, _ = make_client(
        [fake_response()],
        structured_output_mode="prompt_json",
        use_instructions=False,
        send_temperature=False,
        send_max_output_tokens=False,
        send_thinking=False,
        send_metadata=False,
    )
    result = generate(client)
    assert result.request_id == "resp-001"
    assert set(sdk.endpoint.calls[0]) == {"model", "input"}


def test_provider_400_error_exposes_safe_diagnostics_without_api_key():
    secret = "test-secret-key"
    error = StatusError(
        400,
        body={
            "error": {
                "code": "InvalidParameter",
                "type": "invalid_request_error",
                "message": f"unsupported parameter temperature; token={secret}",
            }
        },
        request_id="req-safe-400",
    )
    client, sdk, _ = make_client([error], api_key=secret)
    with pytest.raises(ModelResponseError) as caught:
        generate(client)
    diagnostics = caught.value.safe_diagnostics()
    rendered = json.dumps({"error": str(caught.value), **diagnostics}, ensure_ascii=False)
    assert diagnostics["status_code"] == 400
    assert diagnostics["provider_error_code"] == "InvalidParameter"
    assert diagnostics["provider_error_message"] == "unsupported parameter temperature; token=[REDACTED]"
    assert diagnostics["request_id"] == "req-safe-400"
    assert diagnostics["failed_operation"] == "client.responses.create"
    assert diagnostics["retryable"] is False
    assert secret not in rendered
    assert len(sdk.endpoint.calls) == 1


def test_request_inspection_is_safe_and_does_not_include_prompt_or_key(tmp_path):
    path = tmp_path / "profiles.json"
    write_config(path, output_mode="prompt_json")
    secret = "preview-secret-never-print"
    preview = inspect_drafting_request(
        profile_name="draft",
        config_path=path,
        environ={"ARK_API_KEY": secret, "DRAFT_MODEL": "model"},
    )
    rendered = json.dumps(preview, ensure_ascii=False)
    assert preview["sdk_method"] == "client.responses.create"
    assert preview["native_schema_sent"] is False
    assert preview["max_output_tokens_sent"] is True
    assert preview["timeout_seconds"] == 60.0
    assert preview["thinking_mode"] == "disabled"
    assert "input_character_count" in preview
    assert secret not in rendered
    assert "input" not in preview


def test_minimal_smoke_uses_independent_fixed_overrides_without_mutating_profile():
    formal = make_profile(
        timeout_seconds=240.0,
        max_retries=3,
        max_output_tokens=8000,
        temperature=0.2,
        send_thinking=True,
        extra_body={"thinking": {"type": "enabled"}, "formal_option": True},
    )
    smoke = build_smoke_profile(formal, smoke_mode="minimal")
    assert smoke is not formal
    assert smoke.timeout_seconds == 60.0
    assert smoke.max_retries == 0
    assert smoke.max_output_tokens == 100
    assert smoke.temperature == 0.0
    assert smoke.extra_body == {"thinking": {"type": "disabled"}}
    assert formal.timeout_seconds == 240.0
    assert formal.max_retries == 3
    assert formal.max_output_tokens == 8000
    assert formal.extra_body == {"thinking": {"type": "enabled"}, "formal_option": True}


def test_chapter_schema_smoke_defaults_to_120_seconds_and_can_override_once():
    formal = make_profile(timeout_seconds=240.0, send_thinking=True, extra_body={"thinking": {"type": "enabled"}})
    default_smoke = build_smoke_profile(formal, smoke_mode="chapter-schema")
    overridden = build_smoke_profile(
        formal,
        smoke_mode="chapter-schema",
        thinking="enabled",
        timeout_seconds=180,
        max_output_tokens=4000,
    )
    assert default_smoke.timeout_seconds == 120.0
    assert default_smoke.extra_body == {"thinking": {"type": "disabled"}}
    assert overridden.timeout_seconds == 180.0
    assert overridden.max_output_tokens == 4000
    assert overridden.extra_body == {"thinking": {"type": "enabled"}}
    assert formal.timeout_seconds == 240.0
    assert formal.max_output_tokens == 1000
    assert formal.extra_body == {"thinking": {"type": "enabled"}}


def test_minimal_smoke_does_not_attempt_schema_repair(tmp_path):
    sdk = FakeSDKClient([fake_response({"status": "wrong"}), fake_minimal_response()])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        write_result=False,
        smoke_mode="minimal",
    )
    assert result["status"] == "failed"
    assert result["failed_stage"] == "local_schema_validation"
    assert result["repair_attempted"] is False
    assert len(sdk.endpoint.calls) == 1


def test_empty_output_preserves_response_metadata_and_never_repairs():
    client, sdk, _ = make_client([incomplete_reasoning_response(), fake_response()])
    with pytest.raises(ModelOutputIncompleteError) as caught:
        generate(client)
    diagnostics = caught.value.safe_diagnostics()
    assert diagnostics["request_id"] == "resp-incomplete-001"
    assert diagnostics["response_received"] is True
    assert diagnostics["response_status"] == "incomplete"
    assert diagnostics["finish_status"] == "incomplete"
    assert diagnostics["incomplete_details"] == {"reason": "max_output_tokens"}
    assert diagnostics["usage"]["reasoning_tokens"] == 1180
    assert diagnostics["usage"]["output_tokens"] == 1200
    assert diagnostics["usage"]["cached_tokens"] == 23
    assert diagnostics["output_item_types"] == ["reasoning"]
    assert diagnostics["output_text_available"] is False
    assert diagnostics["output_text_character_count"] == 0
    assert diagnostics["failed_stage"] == "output_extraction"
    assert diagnostics["repair_attempted"] is False
    assert "repair failed" not in str(caught.value)
    assert "private reasoning must not persist" not in json.dumps(diagnostics, ensure_ascii=False)
    assert len(sdk.endpoint.calls) == 1


def test_tool_turn_can_lower_reasoning_effort_without_mutating_profile():
    mapping = RequestMapping(thinking_enabled=True, reasoning_effort="high")
    client, sdk, _ = make_client(
        [fake_response({"answer": "ok"}, mode="chat")],
        api_mode="chat_completions",
        structured_output_mode="prompt_json",
        request_mapping=mapping,
        extra_body={"thinking": {"type": "enabled"}},
    )

    result = client.run_with_tools(
        messages=[{"role": "user", "content": "finalize"}],
        tools=[],
        tool_choice="none",
        options={"stream": False, "reasoning_effort": "low"},
    )

    assert result.final_content == json.dumps({"answer": "ok"}, ensure_ascii=False)
    assert sdk.endpoint.calls[0]["reasoning_effort"] == "low"
    assert client.profile.request_mapping.reasoning_effort == "high"


def test_smoke_empty_output_reports_extraction_stage_and_reasoning_usage(tmp_path):
    sdk = FakeSDKClient([incomplete_reasoning_response()])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        write_result=False,
        smoke_mode="chapter-schema",
        thinking="enabled",
        timeout_seconds=300,
        max_output_tokens=1200,
    )
    assert result["status"] == "failed"
    assert result["failed_stage"] == "output_extraction"
    assert result["request_id"] == "resp-incomplete-001"
    assert result["response_received"] is True
    assert result["finish_status"] == "incomplete"
    assert result["incomplete_details"] == {"reason": "max_output_tokens"}
    assert result["usage"]["reasoning_tokens"] == 1180
    assert result["output_text_available"] is False
    assert result["length_truncated"] is True
    assert result["repair_attempted"] is False
    assert "repair failed" not in result["error"]
    assert "thinking enabled with a low output token budget may leave no final structured answer" in result["warnings"]
    assert "The output token budget appears to have been consumed before a final structured answer was produced." in result["warnings"]
    assert len(sdk.endpoint.calls) == 1


def test_json_parse_failure_and_schema_failure_have_distinct_stages():
    parse_client, _, _ = make_client([fake_response("not-json")])
    parse_client._schema_repair_enabled = False
    with pytest.raises(ModelJsonParseError) as parse_error:
        generate(parse_client)
    assert parse_error.value.safe_diagnostics()["failed_stage"] == "json_parse"

    invalid = valid_chapter_output()
    invalid["sections"] = []
    schema_client, sdk, _ = make_client([fake_response(invalid), fake_response()])
    result = generate(schema_client)
    assert result.repair_attempted is True
    assert len(sdk.endpoint.calls) == 2


def test_chapter_schema_smoke_uses_formal_schema_and_temporary_cli_overrides(tmp_path):
    init_kwargs = {}
    sdk = FakeSDKClient([fake_response()])

    def factory(**kwargs):
        init_kwargs.update(kwargs)
        return sdk

    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=factory,
        write_result=False,
        smoke_mode="chapter-schema",
        thinking="enabled",
        timeout_seconds=180,
        max_output_tokens=4000,
    )
    assert result["status"] == "success"
    assert result["smoke_mode"] == "chapter-schema"
    assert result["timeout_seconds"] == 180.0
    assert result["thinking_mode"] == "enabled"
    assert result["profile_timeout_seconds"] == 120.0
    assert result["smoke_timeout_overrode_profile"] is True
    assert init_kwargs["timeout"] == 180.0
    assert result["max_output_tokens"] == 4000
    assert sdk.endpoint.calls[0]["max_output_tokens"] == 4000
    assert sdk.endpoint.calls[0]["extra_body"]["thinking"]["type"] == "enabled"


def test_chapter_schema_smoke_thinking_disabled_success_path_is_preserved(tmp_path):
    sdk = FakeSDKClient([fake_response()])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        write_result=False,
        smoke_mode="chapter-schema",
        thinking="disabled",
        timeout_seconds=180,
    )
    assert result["status"] == "success"
    assert result["schema_valid"] is True
    assert result["response_received"] is True
    assert result["output_text_available"] is True
    assert result["repair_attempted"] is False
    assert sdk.endpoint.calls[0]["extra_body"]["thinking"]["type"] == "disabled"


def test_smoke_does_not_write_back_profile_config(tmp_path):
    config_path = tmp_path / "profiles.json"
    write_config(config_path, output_mode="prompt_json")
    before = config_path.read_bytes()
    sdk = FakeSDKClient([fake_minimal_response()])
    result = check_drafting_model(
        profile_name="draft",
        config_path=config_path,
        output_dir=tmp_path / "outputs",
        environ={"ARK_API_KEY": "secret", "DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        write_result=False,
    )
    assert result["status"] == "success"
    assert config_path.read_bytes() == before


def test_smoke_timeout_reports_smoke_and_profile_settings(tmp_path):
    sdk = FakeSDKClient([TimeoutError()])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        sleep_fn=lambda _seconds: None,
        write_result=False,
        smoke_mode="minimal",
    )
    assert result["status"] == "failed"
    assert result["smoke_mode"] == "minimal"
    assert result["timeout_seconds"] == 60.0
    assert result["thinking_mode"] == "disabled"
    assert result["failed_stage"] == "provider_request_timeout"
    assert result["provider_request_ms"] is not None
    assert result["profile_timeout_seconds"] == 120.0
    assert result["smoke_timeout_overrode_profile"] is True
    assert result["retry_count"] == 0
    assert len(sdk.endpoint.calls) == 1


def test_smoke_check_exposes_provider_400_diagnostics(tmp_path):
    error = StatusError(
        400,
        body={
            "error": {
                "code": "InvalidParameter",
                "type": "invalid_request_error",
                "message": "unsupported request field",
            }
        },
        request_id="req-smoke-400",
    )
    sdk = FakeSDKClient([error])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        write_result=False,
    )
    assert result["status"] == "failed"
    assert result["status_code"] == 400
    assert result["provider_error_code"] == "InvalidParameter"
    assert result["provider_error_message"] == "unsupported request field"
    assert result["request_id"] == "req-smoke-400"
    assert result["failed_operation"] == "client.responses.create"


@pytest.mark.parametrize("failure", [TimeoutError(), StatusError(429), StatusError(503)])
def test_temporary_errors_retry_with_finite_attempts(failure):
    client, sdk, _ = make_client([failure, fake_response()])
    result = generate(client)
    assert result.retry_count == 1
    assert len(sdk.endpoint.calls) == 2


@pytest.mark.parametrize(
    "status,error_type",
    [(401, ModelAuthenticationError), (403, ModelPermissionError)],
)
def test_authentication_and_permission_errors_do_not_retry(status, error_type):
    client, sdk, _ = make_client([StatusError(status)])
    with pytest.raises(error_type):
        generate(client)
    assert len(sdk.endpoint.calls) == 1


def test_schema_repair_occurs_at_most_once_and_can_succeed():
    invalid = valid_chapter_output()
    invalid["sections"] = []
    client, sdk, _ = make_client([fake_response(invalid), fake_response()])
    result = generate(client)
    assert result.repair_attempted is True
    assert len(sdk.endpoint.calls) == 2


def test_invalid_json_enters_one_controlled_repair_and_then_fails():
    client, sdk, _ = make_client([fake_response("not-json"), fake_response("still-not-json")])
    with pytest.raises(ModelJsonParseError) as caught:
        generate(client)
    assert caught.value.safe_diagnostics()["failed_stage"] == "repair_completed"
    assert caught.value.safe_diagnostics()["repair_attempted"] is True
    assert "repair failed" in str(caught.value)
    assert len(sdk.endpoint.calls) == 2


def test_invalid_json_can_succeed_after_one_controlled_repair():
    client, sdk, _ = make_client(
        [fake_response('{"chapter_title":"broken\nline"}'), fake_response()]
    )
    result = generate(client)
    assert result.repair_attempted is True
    assert result.parsed_output["chapter_title"] == "模型连通测试"
    assert len(sdk.endpoint.calls) == 2


def test_profile_list_never_contains_api_key_value():
    secret = "do-not-leak-this-key"
    summaries = list_profile_summaries(
        environ={"ARK_API_KEY": secret, "COMLAN_ARK_DRAFT_MODEL": "model"},
        load_dotenv_file=False,
    )
    text = json.dumps(summaries)
    assert secret not in text
    ark_draft = next(item for item in summaries if item["profile_name"] == "ark_draft")
    assert ark_draft["api_key_configured"] is True


def test_smoke_result_recursively_excludes_api_key(tmp_path):
    secret = "smoke-secret-never-persist"
    sdk = FakeSDKClient([fake_minimal_response()])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path / "outputs",
        environ={"ARK_API_KEY": secret, "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        sleep_fn=lambda _seconds: None,
        random_fn=lambda: 0.0,
    )
    saved = Path(result["output_path"]).read_text(encoding="utf-8")
    assert result["status"] == "success"
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert secret not in saved


def test_smoke_check_does_not_write_real_case_outputs(tmp_path):
    output_dir = tmp_path / "outputs"
    sdk = FakeSDKClient([fake_minimal_response()])
    check_drafting_model(
        profile_name="ark_draft",
        output_dir=output_dir,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
    )
    assert (output_dir / "model_checks" / "latest_drafting_model_check.json").exists()
    assert not list(output_dir.glob("*/drafts/chapter_drafts.json"))


def test_missing_configuration_smoke_status_is_not_fake_success(tmp_path):
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={},
        write_result=False,
    )
    assert result["status"] == "configuration_missing"
    assert result["schema_valid"] is False


def test_get_llm_client_legacy_mock_remains_compatible():
    assert isinstance(get_llm_client("mock"), MockLLMClient)


def test_smoke_output_has_no_bid_decision_language(tmp_path):
    sdk = FakeSDKClient([fake_minimal_response()])
    result = check_drafting_model(
        profile_name="ark_draft",
        output_dir=tmp_path,
        environ={"ARK_API_KEY": "secret", "COMLAN_ARK_DRAFT_MODEL": "model"},
        client_factory=lambda **_kwargs: sdk,
        write_result=False,
    )
    text = json.dumps(result, ensure_ascii=False)
    assert not any(term in text for term in ["能投", "不能投", "建议投标", "不建议投标"])

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


FORBIDDEN_DECISION_TERMS = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go", "bid / no-bid decision"]


class LLMClient(Protocol):
    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        request_metadata: dict[str, Any] | None = None,
        profile_name: str | None = None,
    ) -> "ModelCallResult":
        ...

    def run_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
        options: dict[str, Any] | None = None,
        request_metadata: dict[str, Any] | None = None,
        profile_name: str | None = None,
    ) -> "ToolModelResult":
        ...

    def run_builtin_web_search(
        self,
        *,
        query: str,
        options: dict[str, Any] | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ModelCallResult:
    provider: str
    protocol: str
    profile_name: str
    model: str
    api_mode: str
    request_id: str | None
    started_at: str
    completed_at: str
    duration_ms: int
    parsed_output: dict[str, Any]
    raw_output_available: bool
    finish_status: str
    retry_count: int
    repair_attempted: bool
    usage: dict[str, int | None]
    warnings: list[str]
    response_received: bool = False
    response_status: str | None = None
    incomplete_details: dict[str, Any] | None = None
    output_item_types: list[str] = field(default_factory=list)
    output_text_available: bool = False
    output_text_character_count: int = 0
    processing_stages: list[str] = field(default_factory=list)

    def as_dict(self, *, include_parsed_output: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_parsed_output:
            value.pop("parsed_output", None)
        return value


@dataclass(frozen=True)
class ToolModelResult:
    """Provider-neutral result for one turn of a model tool loop."""

    response_type: str
    tool_calls: list[dict[str, Any]]
    final_content: str | None
    request_metadata: dict[str, Any]
    assistant_turn: "AssistantTurn | None" = None

    def as_dict(self, *, include_reasoning_content: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not include_reasoning_content and isinstance(value.get("assistant_turn"), dict):
            value["assistant_turn"].pop("reasoning_content", None)
        return value

    def context_message(self) -> dict[str, Any]:
        """Return a provider-neutral assistant message for the next tool turn."""
        if self.assistant_turn is not None:
            return self.assistant_turn.as_context_message()
        calls = [
            ToolCall(
                id=str(call.get("tool_call_id") or ""),
                name=str(call.get("name") or ""),
                arguments=dict(call.get("arguments") or {}),
            )
            for call in self.tool_calls
        ]
        return AssistantTurn(content=self.final_content, tool_calls=calls).as_context_message()


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    argument_metadata: dict[str, Any] = field(default_factory=dict)

    def as_message_call(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    provider_details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssistantTurn:
    content: str | None = None
    reasoning_content: str | None = field(default=None, repr=False)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def as_context_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.reasoning_content:
            message["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            message["tool_calls"] = [call.as_message_call() for call in self.tool_calls]
        return message


def empty_usage() -> dict[str, int | None]:
    return {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "reasoning_tokens": None,
        "answer_tokens": None,
        "cached_tokens": None,
    }


class LegacyJSONClient(Protocol):
    def generate_json(
        self,
        agent_name: str,
        system_prompt: str,
        input_data: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class MockLLMClient:
    is_mock = True

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        request_metadata: dict[str, Any] | None = None,
        profile_name: str | None = None,
    ) -> ModelCallResult:
        now = datetime.now(timezone.utc).isoformat()
        metadata = request_metadata or {}
        chapter_title = str(metadata.get("chapter_title") or "Mock 结构化测试")
        parsed = {
            "chapter_title": chapter_title,
            "sections": [
                {
                    "heading": "Mock 输出",
                    "block_type": "other",
                    "content": "仅用于单元测试，不得标记为真实 AI 草稿。",
                    "covered_score_item_ids": [],
                    "covered_requirement_ids": [],
                    "placeholder_ids": [],
                }
            ],
            "claims": [],
            "placeholders_used": [],
            "score_items_covered": [],
            "requirements_covered": [],
            "warnings": ["mock_result"],
        }
        return ModelCallResult(
            provider="mock",
            protocol="mock",
            profile_name=profile_name or "mock",
            model="mock",
            api_mode="mock",
            request_id=None,
            started_at=now,
            completed_at=now,
            duration_ms=0,
            parsed_output=parsed,
            raw_output_available=False,
            finish_status="mock",
            retry_count=0,
            repair_attempted=False,
            usage=empty_usage(),
            warnings=["MockLLMClient is test-only and must not produce draft_mode=ai_generated."],
        )

    def generate_json(
        self,
        agent_name: str,
        system_prompt: str,
        input_data: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if agent_name == "business_writer":
            result = mock_writer_result(agent_name, input_data, "商务章节 Mock 草稿")
        elif agent_name == "technical_writer":
            result = mock_writer_result(agent_name, input_data, "技术章节 Mock 草稿")
        elif agent_name == "price_writer":
            result = {
                "section_name": str(input_data.get("task_name") or "报价类任务"),
                "content": "报价测算与价格策略不在 AgentRunner V0 mock 范围内；本结果仅保留后续价格文件核对入口，不进行报价测算。",
                "used_kb_files": [],
                "trace": {
                    "agent_name": agent_name,
                    "provider": "mock",
                    "status": "not_applicable",
                },
            }
        else:
            result = {
                "section_name": str(input_data.get("task_name") or agent_name),
                "content": "Mock provider 已读取输入，但该 agent 暂未配置专用生成模板。",
                "used_kb_files": evidence_file_ids(input_data),
                "trace": {
                    "agent_name": agent_name,
                    "provider": "mock",
                    "status": "generic_mock",
                },
            }
        ensure_no_decision_terms(result)
        return result

    def run_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
        options: dict[str, Any] | None = None,
        request_metadata: dict[str, Any] | None = None,
        profile_name: str | None = None,
    ) -> ToolModelResult:
        now = datetime.now(timezone.utc).isoformat()
        return ToolModelResult(
            response_type="final",
            tool_calls=[],
            final_content=json.dumps(
                {
                    "status": "mock_only",
                    "message": "MockLLMClient does not simulate autonomous production tool use.",
                },
                ensure_ascii=False,
            ),
            request_metadata={
                "provider": "mock",
                "profile_name": profile_name or "mock",
                "started_at": now,
                "completed_at": now,
                "usage": empty_usage(),
                **sanitize_simple_metadata(request_metadata or {}),
            },
        )

    def run_builtin_web_search(
        self,
        *,
        query: str,
        options: dict[str, Any] | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "not_configured",
            "provider": "mock",
            "reason": "mock_client_has_no_hosted_web_search",
            "results": [],
        }


def get_llm_client(
    provider: str | None = None,
    *,
    role: str = "drafting",
    profile_name: str | None = None,
    config_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
    client_factory: Any = None,
    sleep_fn: Any = None,
    random_fn: Any = None,
    runtime_profile_overrides: dict[str, Any] | None = None,
    schema_repair_enabled: bool = True,
) -> LLMClient:
    """Return a profile-backed client while preserving the legacy mock call."""
    if provider == "mock":
        return MockLLMClient()
    if provider:
        from app.llm_errors import ModelConfigurationError

        raise ModelConfigurationError(
            "direct provider selection is deprecated; configure an LLM profile and select it by role or profile_name"
        )
    from app.llm_profiles import resolve_profile
    from app.openai_compatible_llm_client import OpenAICompatibleLLMClient

    profile = resolve_profile(
        role=role,
        profile_name=profile_name,
        config_path=config_path,
        environ=environ,
    )
    if runtime_profile_overrides:
        allowed = {
            "max_output_tokens",
            "send_max_output_tokens",
            "send_thinking",
            "extra_body",
            "temperature",
            "send_temperature",
            "timeout_seconds",
            "max_retries",
            "structured_output_mode",
            "use_instructions",
            "send_metadata",
        }
        unknown = sorted(set(runtime_profile_overrides) - allowed)
        if unknown:
            from app.llm_errors import ModelConfigurationError

            raise ModelConfigurationError(f"unsupported runtime profile override fields: {unknown}")
        profile = replace(profile, **runtime_profile_overrides)
    return OpenAICompatibleLLMClient(
        profile,
        client_factory=client_factory,
        sleep_fn=sleep_fn,
        random_fn=random_fn,
        schema_repair_enabled=schema_repair_enabled,
    )


def mock_writer_result(agent_name: str, input_data: dict[str, Any], heading: str) -> dict[str, Any]:
    task_name = str(input_data.get("task_name") or "未命名章节")
    outline = [str(item) for item in list(input_data.get("outline") or []) if str(item).strip()]
    evidence_refs = [item for item in list(input_data.get("evidence_refs") or []) if isinstance(item, dict)]
    missing_materials = [item for item in list(input_data.get("missing_materials") or []) if isinstance(item, dict)]
    evidence_lines = [
        f"- {ref.get('title') or ref.get('file_id')}（{ref.get('material_type') or 'material'}）：{preview(ref.get('snippet'), 90)}"
        for ref in evidence_refs[:5]
    ]
    missing_lines = [
        f"- {item.get('material_type') or 'material'}：{item.get('reason') or item.get('suggested_action') or '需补充材料'}"
        for item in missing_materials[:5]
    ]
    content_parts = [
        f"【{task_name}】{heading}",
        "本段由 MockLLMClient 生成，用于验证 Agent 调用链路；后续接入真实模型前需人工复核。",
        "",
        "写作提纲：",
        *(f"- {item}" for item in (outline or ["任务要求理解", "材料引用", "响应骨架", "复核事项"])),
        "",
        "材料引用边界：",
        *(evidence_lines or ["- 当前输入未提供 evidence_refs。"]),
    ]
    if missing_lines:
        content_parts.extend(["", "材料缺口：", *missing_lines])
    content_parts.extend(
        [
            "",
            "表达约束：仅说明可依据上述材料展开，不扩大材料证明范围；个人证书不得写成企业资质，人员项目经历不得写成公司业绩合同。",
        ]
    )
    return {
        "section_name": task_name,
        "content": "\n".join(content_parts),
        "used_kb_files": evidence_file_ids(input_data),
        "trace": {
            "agent_name": agent_name,
            "provider": "mock",
            "section_type": input_data.get("section_type"),
            "evidence_count": len(evidence_refs),
            "missing_count": len(missing_materials),
        },
    }


def evidence_file_ids(input_data: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for ref in list(input_data.get("evidence_refs") or []):
        if not isinstance(ref, dict):
            continue
        file_id = str(ref.get("file_id") or "").strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        result.append(file_id)
    return result


def ensure_no_decision_terms(value: Any) -> None:
    text = str(value).lower()
    for term in FORBIDDEN_DECISION_TERMS:
        if term.lower() in text:
            raise ValueError("mock llm output contains forbidden bid decision language")


def preview(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def sanitize_simple_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (str, int, float, bool)) or item is None
    }

from __future__ import annotations

import json
import hashlib
import random
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.llm_client import AssistantTurn, ModelCallResult, ToolCall, ToolModelResult, Usage, empty_usage
from app.llm_errors import (
    ModelAuthenticationError,
    ModelConfigurationError,
    ModelConnectionError,
    ModelError,
    ModelNotFoundError,
    ModelJsonParseError,
    ModelOutputIncompleteError,
    ModelPermissionError,
    ModelRateLimitError,
    ModelResponseError,
    ModelSafetyRefusalError,
    ModelSchemaValidationError,
    ModelTimeoutError,
)
from app.llm_profiles import LLMProfile


ClientFactory = Callable[..., Any]
MAX_TOOL_ARGUMENT_QUOTE_REPAIRS = 32


@dataclass(frozen=True)
class ParsedToolArguments:
    arguments: dict[str, Any]
    raw_arguments: str
    repaired_arguments: str
    json_syntax_repair: dict[str, Any]


class OpenAICompatibleLLMClient:
    is_mock = False

    def __init__(
        self,
        profile: LLMProfile,
        *,
        client_factory: ClientFactory | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
        schema_repair_enabled: bool = True,
    ) -> None:
        self.profile = profile
        self._sleep = sleep_fn or time.sleep
        self._random = random_fn or random.random
        self._schema_repair_enabled = schema_repair_enabled
        factory = client_factory or load_openai_factory()
        try:
            self._client = factory(
                base_url=profile.base_url,
                api_key=profile.api_key,
                timeout=profile.timeout_seconds,
                max_retries=0,
                default_headers=profile.extra_headers or None,
            )
        except ModelError:
            raise
        except Exception as exc:
            raise ModelConfigurationError("failed to initialize OpenAI-compatible client") from exc

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        request_metadata: dict[str, Any] | None = None,
        profile_name: str | None = None,
    ) -> ModelCallResult:
        if not self.profile.capabilities.supports_structured_output:
            raise ModelConfigurationError(
                f"profile {self.profile.name} does not support structured output"
            )
        validate_schema(response_schema)
        metadata = sanitize_metadata(request_metadata or {})
        started = datetime.now(timezone.utc)
        warnings: list[str] = []
        first_response, retry_count = self._request_with_retry(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            request_metadata=metadata,
        )
        first_usage = extract_usage(first_response)
        repair_attempted = False
        try:
            parsed = parse_and_validate_response(first_response, response_schema)
            final_response = first_response
            usage = first_usage
        except ModelSafetyRefusalError as first_error:
            raise attach_response_diagnostics(
                first_error,
                first_response,
                failed_stage="provider_response",
                repair_attempted=False,
                max_output_tokens=self.profile.max_output_tokens,
                provider_request_ms=elapsed_ms(started),
            ) from None
        except ModelOutputIncompleteError as first_error:
            raise attach_response_diagnostics(
                first_error,
                first_response,
                failed_stage="output_extraction",
                repair_attempted=False,
                max_output_tokens=self.profile.max_output_tokens,
                provider_request_ms=elapsed_ms(started),
            ) from None
        except (ModelJsonParseError, ModelSchemaValidationError) as first_error:
            if not self._schema_repair_enabled:
                raise attach_response_diagnostics(
                    first_error,
                    first_response,
                    failed_stage=(
                        "json_parse"
                        if isinstance(first_error, ModelJsonParseError)
                        else "local_schema_validation"
                    ),
                    repair_attempted=False,
                    max_output_tokens=self.profile.max_output_tokens,
                    provider_request_ms=elapsed_ms(started),
                ) from None
            repair_attempted = True
            warnings.append(f"initial_structured_output_invalid: {safe_validation_message(first_error)}")
            repair_prompt = build_repair_prompt(first_response, first_error)
            try:
                repaired_response, repair_retries = self._request_with_retry(
                    system_prompt="修复给定 JSON，使其严格符合指定 Schema。只输出修复后的 JSON。",
                    user_prompt=repair_prompt,
                    response_schema=response_schema,
                    request_metadata={**metadata, "repair_attempt": 1},
                )
            except ModelError as repair_request_error:
                repair_request_error.diagnostics = {
                    **repair_request_error.safe_diagnostics(),
                    "repair_attempted": True,
                    "failed_stage": "repair_started",
                    "processing_stages": [
                        "request_started",
                        "response_received",
                        "output_items_inspected",
                        "output_extracted",
                        "json_parsed",
                        "repair_started",
                    ],
                }
                raise repair_request_error from None
            retry_count += repair_retries
            try:
                parsed = parse_and_validate_response(repaired_response, response_schema)
            except (ModelOutputIncompleteError, ModelJsonParseError, ModelSchemaValidationError) as repair_error:
                diagnostics = response_diagnostics(repaired_response)
                error_type = (
                    ModelJsonParseError
                    if isinstance(first_error, ModelJsonParseError)
                    or isinstance(repair_error, (ModelOutputIncompleteError, ModelJsonParseError))
                    else ModelSchemaValidationError
                )
                raise error_type(
                    f"structured output repair failed: {safe_validation_message(repair_error)}",
                    diagnostics={
                        **diagnostics,
                        **(
                            repair_error.safe_diagnostics()
                            if isinstance(repair_error, ModelJsonParseError)
                            else {}
                        ),
                        "initial_error": safe_validation_message(first_error),
                        "repair_error": safe_validation_message(repair_error),
                        "repair_attempted": True,
                        "failed_stage": "repair_completed",
                        "processing_stages": [
                            "request_started",
                            "response_received",
                            "output_items_inspected",
                            "output_extracted",
                            "json_parsed",
                            "repair_started",
                            "repair_completed",
                        ],
                    },
                    **(
                        {"raw_content": getattr(repair_error, "raw_content", None)}
                        if error_type is ModelJsonParseError
                        else {}
                    ),
                ) from None
            final_response = repaired_response
            usage = merge_usage(first_usage, extract_usage(repaired_response))

        completed = datetime.now(timezone.utc)
        final_diagnostics = response_diagnostics(final_response)
        warnings.extend(output_budget_warnings(final_diagnostics, self.profile.max_output_tokens))
        return ModelCallResult(
            provider=self.profile.provider,
            protocol=self.profile.protocol,
            profile_name=profile_name or self.profile.name,
            model=self.profile.model,
            api_mode=self.profile.api_mode,
            request_id=extract_request_id(final_response),
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            duration_ms=max(0, int((completed - started).total_seconds() * 1000)),
            parsed_output=parsed,
            raw_output_available=bool(extract_text(final_response)),
            finish_status=extract_finish_status(final_response),
            retry_count=retry_count,
            repair_attempted=repair_attempted,
            usage=usage,
            warnings=warnings,
            response_received=True,
            response_status=final_diagnostics.get("response_status"),
            incomplete_details=final_diagnostics.get("incomplete_details"),
            output_item_types=final_diagnostics.get("output_item_types") or [],
            output_text_available=bool(final_diagnostics.get("output_text_available")),
            output_text_character_count=int(final_diagnostics.get("output_text_character_count") or 0),
            processing_stages=[
                "request_started",
                "response_received",
                "output_items_inspected",
                "output_extracted",
                "json_parsed",
                "schema_validated",
                *(["repair_started", "repair_completed"] if repair_attempted else []),
            ],
        )

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
        """Run one provider turn and normalize Chat/Responses tool calls."""
        if not isinstance(messages, list) or not messages:
            raise ModelConfigurationError("messages must be a non-empty list")
        validate_tool_definitions(tools, allow_empty=True)
        run_options = dict(options or {})
        stream_handler = run_options.pop("stream_event_handler", None)
        stream_requested = bool(run_options.get("stream", self.profile.stream))
        if stream_requested and not self.profile.capabilities.supports_streaming:
            raise ModelConfigurationError(
                f"profile {self.profile.name} does not support streaming"
            )
        run_options["stream"] = stream_requested
        started = datetime.now(timezone.utc)
        retries = 0
        while True:
            try:
                method, kwargs = build_tool_request_kwargs(
                    self.profile,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    options=run_options,
                    request_metadata=request_metadata or {},
                )
                if method == "client.responses.create":
                    response = self._client.responses.create(**kwargs)
                elif method == "client.chat.completions.create":
                    response = self._client.chat.completions.create(**kwargs)
                else:
                    raise ModelConfigurationError("tool request builder returned an invalid SDK method")
                if stream_requested:
                    assistant_turn, response_for_metadata = consume_streaming_tool_response(
                        response,
                        api_mode=self.profile.api_mode,
                        supports_reasoning_content=self.profile.capabilities.supports_reasoning_content,
                        event_handler=stream_handler,
                    )
                else:
                    assistant_turn = extract_assistant_turn(
                        response,
                        supports_reasoning_content=self.profile.capabilities.supports_reasoning_content,
                    )
                    response_for_metadata = response
                calls = [
                    {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        **(
                            {"argument_metadata": dict(call.argument_metadata)}
                            if (call.argument_metadata or {}).get("applied")
                            else {}
                        ),
                    }
                    for call in assistant_turn.tool_calls
                ]
                final = None if calls else assistant_turn.content
                break
            except Exception as exc:
                mapped = map_provider_error(exc, profile=self.profile, failed_operation=method if "method" in locals() else None)
                if isinstance(mapped, ModelJsonParseError):
                    diagnostics = mapped.safe_diagnostics()
                    finish_reason = diagnostics.get("finish_reason")
                    if not finish_reason and "response" in locals() and not stream_requested:
                        finish_reason = extract_finish_status(response)
                    usage = diagnostics.get("usage")
                    if not usage and "response" in locals() and not stream_requested:
                        usage = extract_usage(response)
                    raw_content = mapped.raw_content or ""
                    diagnostics.update(
                        {
                            "model_step": (request_metadata or {}).get("agent_step"),
                            "profile_name": profile_name or self.profile.name,
                            "api_style": self.profile.api_style,
                            "finish_reason": finish_reason,
                            "usage": usage or empty_usage(),
                            "suspected_truncation": bool(
                                diagnostics.get("suspected_truncation")
                                or suspected_json_truncation(
                                    raw_content,
                                    finish_reason=finish_reason,
                                    usage=usage,
                                    max_output_tokens=self.profile.max_output_tokens,
                                    error_summary=str(diagnostics.get("json_error_summary") or ""),
                                )
                            ),
                            "repair_attempted": False,
                        }
                    )
                    mapped.diagnostics = diagnostics
                if not mapped.retryable or retries >= self.profile.max_retries:
                    raise mapped from None
                self._sleep((2**retries) + (self._random() * 0.25))
                retries += 1
        if not calls and not final:
            raise attach_response_diagnostics(
                ModelOutputIncompleteError("provider returned neither tool calls nor final content"),
                response_for_metadata,
                failed_stage="tool_response_extraction",
                repair_attempted=False,
                max_output_tokens=self.profile.max_output_tokens,
                provider_request_ms=elapsed_ms(started),
            )
        completed = datetime.now(timezone.utc)
        return ToolModelResult(
            response_type="tool_calls" if calls else "final",
            tool_calls=calls,
            final_content=final,
            request_metadata={
                "provider": self.profile.provider,
                "protocol": self.profile.protocol,
                "profile_name": profile_name or self.profile.name,
                "model": self.profile.model,
                "api_mode": self.profile.api_mode,
                "request_id": extract_request_id(response_for_metadata),
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
                "duration_ms": max(0, int((completed - started).total_seconds() * 1000)),
                "finish_status": extract_finish_status(response_for_metadata),
                "retry_count": retries,
                "usage": extract_usage(response_for_metadata),
                "streamed": stream_requested,
            },
            assistant_turn=assistant_turn,
        )

    def run_builtin_web_search(
        self,
        *,
        query: str,
        options: dict[str, Any] | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute Ark Responses hosted Web Search and normalize its citations."""
        if self.profile.provider != "volcengine_ark" or self.profile.api_mode != "responses":
            return {
                "status": "not_configured",
                "provider": "ark_builtin_web_search",
                "reason": "requires_volcengine_ark_responses_profile",
                "results": [],
            }
        query_text = str(query or "").strip()
        if not query_text:
            raise ModelConfigurationError("built-in web search query must not be empty")
        settings = options or {}
        metadata = sanitize_metadata(request_metadata or {})
        started = datetime.now(timezone.utc)
        retries = 0
        while True:
            try:
                kwargs: dict[str, Any] = {
                    "model": self.profile.model,
                    "input": query_text,
                    "tools": [{"type": "web_search"}],
                    "tool_choice": settings.get("tool_choice") or "required",
                }
                if self.profile.send_max_output_tokens:
                    kwargs["max_output_tokens"] = min(
                        int(settings.get("max_output_tokens") or 1200),
                        int(self.profile.max_output_tokens or 1200),
                    )
                if self.profile.send_metadata and metadata:
                    kwargs["metadata"] = metadata
                extra_body = filtered_extra_body(self.profile)
                if extra_body:
                    kwargs["extra_body"] = extra_body
                response = self._client.responses.create(**kwargs)
                break
            except Exception as exc:
                mapped = map_provider_error(
                    exc,
                    profile=self.profile,
                    failed_operation="client.responses.create:web_search",
                )
                if not mapped.retryable or retries >= self.profile.max_retries:
                    raise mapped from None
                self._sleep((2**retries) + (self._random() * 0.25))
                retries += 1
        completed = datetime.now(timezone.utc)
        results, citations, calls = extract_builtin_web_search_sources(response)
        answer = extract_text(response)
        return {
            "status": "success" if results or answer else "no_results",
            "provider": "ark_builtin_web_search",
            "query": query_text,
            "results": results,
            "citations": citations,
            "answer": answer,
            "web_search_calls": calls,
            "request_metadata": {
                "provider": self.profile.provider,
                "model": self.profile.model,
                "api_mode": self.profile.api_mode,
                "request_id": extract_request_id(response),
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
                "retrieved_at": completed.isoformat(),
                "duration_ms": max(0, int((completed - started).total_seconds() * 1000)),
                "finish_status": extract_finish_status(response),
                "retry_count": retries,
                "usage": extract_usage(response),
            },
        }

    def _request_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        request_metadata: dict[str, Any],
    ) -> tuple[Any, int]:
        retries = 0
        while True:
            try:
                return self._perform_request(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_schema=response_schema,
                    request_metadata=request_metadata,
                ), retries
            except Exception as exc:
                operation = (
                    "client.responses.create"
                    if self.profile.api_mode == "responses"
                    else "client.chat.completions.create"
                )
                mapped = map_provider_error(exc, profile=self.profile, failed_operation=operation)
                if not mapped.retryable or retries >= self.profile.max_retries:
                    raise mapped from None
                delay = (2**retries) + (self._random() * 0.25)
                self._sleep(delay)
                retries += 1

    def _perform_request(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        request_metadata: dict[str, Any],
    ) -> Any:
        method, common = build_request_kwargs(
            self.profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            request_metadata=request_metadata,
        )
        if self.profile.api_mode == "responses":
            if method != "client.responses.create":
                raise ModelConfigurationError("responses request builder returned an invalid SDK method")
            return self._client.responses.create(**common)
        if self.profile.api_mode == "chat_completions":
            if method != "client.chat.completions.create":
                raise ModelConfigurationError("chat request builder returned an invalid SDK method")
            return self._client.chat.completions.create(**common)
        raise ModelConfigurationError(f"unsupported api_mode: {self.profile.api_mode}")


def load_openai_factory() -> ClientFactory:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ModelConfigurationError("openai package is required for OpenAI-compatible providers") from exc
    return OpenAI


def validate_schema(schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        raise ModelSchemaValidationError("response_schema must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ModelSchemaValidationError(f"invalid local response schema: {exc.message}") from exc


def parse_and_validate_response(response: Any, schema: dict[str, Any]) -> dict[str, Any]:
    refusal = extract_refusal(response)
    if refusal:
        raise ModelSafetyRefusalError("provider refused the structured generation request")
    text = extract_text(response)
    if not text:
        raise ModelOutputIncompleteError("provider returned no final structured text output")
    parsed = parse_json_object(text)
    try:
        Draft202012Validator(schema).validate(parsed)
    except ValidationError as exc:
        path = ".".join(str(value) for value in exc.absolute_path) or "$"
        raise ModelSchemaValidationError(f"schema validation failed at {path}: {exc.message}") from exc
    return parsed


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse one JSON object, applying only field-preserving deterministic syntax fixes."""
    raw = str(text)
    normalized = raw.lstrip("\ufeff").strip()
    code_blocks = re.findall(
        r"```(?:json)?[ \t]*\r?\n?(.*?)```",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    failures: list[json.JSONDecodeError] = []

    def decode_object(candidate: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            failures.append(exc)
            return None
        if not isinstance(parsed, dict):
            raise ModelJsonParseError(
                "provider structured output is not a JSON object",
                diagnostics=json_parse_diagnostics(
                    raw,
                    error_summary="top-level JSON value is not an object",
                ),
                raw_content=raw,
            )
        return parsed

    # 1. Exact provider content.
    direct = decode_object(raw)
    if direct is not None:
        return direct

    # 2. UTF-8 BOM and surrounding whitespace.
    if normalized != raw:
        cleaned = decode_object(normalized)
        if cleaned is not None:
            return cleaned

    # 3. One fenced JSON block. Explanatory text around that one block is okay.
    if len(code_blocks) == 1:
        fenced = decode_object(code_blocks[0].strip())
        if fenced is not None:
            return fenced

    # 4. Unique balanced top-level object from explanatory prose.
    candidates, structure = scan_top_level_json_objects(normalized)
    valid: list[dict[str, Any]] = []
    for candidate in candidates:
        parsed = decode_object(candidate)
        if parsed is not None:
            valid.append(parsed)
    if valid:
        canonical = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in valid
        }
        if len(canonical) == 1:
            return valid[0]
        message = "provider output contains multiple conflicting top-level JSON objects"
        raise ModelJsonParseError(
            message,
            diagnostics=json_parse_diagnostics(
                raw,
                error_summary=message,
                structure=structure,
                top_level_object_count=len(valid),
            ),
            raw_content=raw,
        )

    # 5. Conservative local syntax repair: escape literal control characters
    # inside strings and remove only trailing commas outside strings. No field
    # or value is inferred, added, or discarded.
    repair_candidates: list[str] = []
    if len(code_blocks) == 1:
        repair_candidates.append(code_blocks[0].strip())
    elif len(candidates) == 1:
        repair_candidates.append(candidates[0])
    else:
        repair_candidates.append(normalized)
    for candidate in repair_candidates:
        repaired = repair_json_syntax_deterministically(candidate)
        if repaired == candidate:
            continue
        parsed = decode_object(repaired)
        if parsed is not None:
            return parsed

    last_error = failures[-1] if failures else None
    summary = (
        f"{last_error.msg} at character {last_error.pos}"
        if last_error is not None
        else "no complete top-level JSON object was found"
    )
    raise ModelJsonParseError(
        f"provider output is not valid JSON: {summary}",
        diagnostics=json_parse_diagnostics(
            raw,
            error_summary=summary,
            structure=structure,
            top_level_object_count=0,
        ),
        raw_content=raw,
    ) from None


def repair_json_syntax_deterministically(value: str) -> str:
    """Repair two unambiguous JSON syntax defects without changing field values."""
    escaped: list[str] = []
    in_string = False
    escape = False
    for char in str(value):
        if in_string:
            if escape:
                escaped.append(char)
                escape = False
                continue
            if char == "\\":
                escaped.append(char)
                escape = True
                continue
            if char == '"':
                escaped.append(char)
                in_string = False
                continue
            if char == "\n":
                escaped.append(r"\n")
                continue
            if char == "\r":
                escaped.append(r"\r")
                continue
            if char == "\t":
                escaped.append(r"\t")
                continue
            escaped.append(char)
            continue
        escaped.append(char)
        if char == '"':
            in_string = True

    source = "".join(escaped)
    result: list[str] = []
    in_string = False
    escape = False
    index = 0
    while index < len(source):
        char = source[index]
        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(source) and source[lookahead].isspace():
                lookahead += 1
            if lookahead < len(source) and source[lookahead] in "}]":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def scan_top_level_json_objects(text: str) -> tuple[list[str], dict[str, Any]]:
    """Return balanced object substrings while respecting strings and escapes."""
    candidates: list[str] = []
    start: int | None = None
    object_depth = 0
    array_depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character == "{":
            if object_depth == 0 and array_depth == 0:
                start = index
            object_depth += 1
        elif character == "}" and object_depth:
            object_depth -= 1
            if object_depth == 0 and array_depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
        elif character == "[" and (object_depth or array_depth):
            array_depth += 1
        elif character == "]" and array_depth:
            array_depth -= 1
    return candidates, {
        "unclosed_object_depth": object_depth,
        "unclosed_array_depth": array_depth,
        "unclosed_string": in_string,
    }


def json_parse_diagnostics(
    text: str,
    *,
    error_summary: str,
    structure: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    usage: dict[str, int | None] | None = None,
    max_output_tokens: int | None = None,
    top_level_object_count: int | None = None,
) -> dict[str, Any]:
    value = str(text)
    structure_value = structure or scan_top_level_json_objects(value)[1]
    diagnostics: dict[str, Any] = {
        "content_character_count": len(value),
        "code_block_detected": bool(re.search(r"```(?:json)?", value, flags=re.IGNORECASE)),
        "finish_reason": finish_reason,
        "json_error_summary": str(error_summary)[:500],
        "schema_validation_error_summary": None,
        "suspected_truncation": suspected_json_truncation(
            value,
            finish_reason=finish_reason,
            usage=usage,
            max_output_tokens=max_output_tokens,
            structure=structure_value,
            error_summary=error_summary,
        ),
    }
    if top_level_object_count is not None:
        diagnostics["top_level_object_count"] = top_level_object_count
    return diagnostics


def suspected_json_truncation(
    text: str,
    *,
    finish_reason: str | None = None,
    usage: dict[str, int | None] | None = None,
    max_output_tokens: int | None = None,
    structure: dict[str, Any] | None = None,
    error_summary: str = "",
) -> bool:
    reason = str(finish_reason or "").lower()
    if reason in {"length", "incomplete", "max_tokens", "max_output_tokens"}:
        return True
    state = structure or scan_top_level_json_objects(str(text))[1]
    if any(
        int(state.get(key) or 0) > 0
        for key in ("unclosed_object_depth", "unclosed_array_depth")
    ) or bool(state.get("unclosed_string")):
        return True
    lowered = str(error_summary).lower()
    if any(
        term in lowered
        for term in (
            "unterminated string",
            "expecting ',' delimiter",
            "expecting value",
            "expecting property name",
        )
    ) and not str(text).rstrip().endswith(("}", "]")):
        return True
    output_tokens = (usage or {}).get("output_tokens")
    return bool(
        max_output_tokens
        and output_tokens is not None
        and int(output_tokens) >= max(1, int(max_output_tokens * 0.9))
    )


def prepare_user_prompt(user_prompt: str, schema: dict[str, Any], mode: str) -> str:
    if mode != "prompt_json":
        return user_prompt
    compact_schema = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return f"{user_prompt}\n\n严格只输出一个符合以下 JSON Schema 的 JSON 对象：\n{compact_schema}"


def combine_system_user_prompt(system_prompt: str, user_prompt: str) -> str:
    return f"【系统约束】\n{system_prompt}\n\n【用户任务】\n{user_prompt}"


def build_request_kwargs(
    profile: LLMProfile,
    *,
    system_prompt: str,
    user_prompt: str,
    response_schema: dict[str, Any],
    request_metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    metadata = sanitize_metadata(request_metadata or {})
    if profile.api_mode == "responses":
        prompt = user_prompt if profile.use_instructions else combine_system_user_prompt(system_prompt, user_prompt)
        user_content = prepare_user_prompt(prompt, response_schema, profile.structured_output_mode)
        kwargs: dict[str, Any] = {"model": profile.model, "input": user_content}
        if profile.use_instructions:
            kwargs["instructions"] = system_prompt
        if profile.send_temperature and profile.temperature is not None:
            kwargs["temperature"] = profile.temperature
        apply_token_limit(profile, kwargs, profile.max_output_tokens)
        if profile.send_metadata and metadata:
            kwargs["metadata"] = metadata
        extra_body = filtered_extra_body(profile)
        if extra_body:
            kwargs["extra_body"] = extra_body
        apply_reasoning_effort(profile, kwargs)
        structured = responses_structured_parameter(response_schema, profile.structured_output_mode)
        if structured:
            kwargs["text"] = structured
        return "client.responses.create", kwargs

    if profile.api_mode == "chat_completions":
        user_content = prepare_user_prompt(user_prompt, response_schema, profile.structured_output_mode)
        kwargs = {
            "model": profile.model,
            "messages": prepare_messages_for_profile(
                profile,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            ),
        }
        if profile.send_temperature and profile.temperature is not None:
            kwargs["temperature"] = profile.temperature
        apply_token_limit(profile, kwargs, profile.max_output_tokens)
        if profile.send_metadata and metadata:
            kwargs["metadata"] = metadata
        extra_body = filtered_extra_body(profile)
        if extra_body:
            kwargs["extra_body"] = extra_body
        apply_reasoning_effort(profile, kwargs)
        response_format = chat_structured_parameter(response_schema, profile.structured_output_mode)
        if response_format:
            kwargs["response_format"] = response_format
        return "client.chat.completions.create", kwargs
    raise ModelConfigurationError(f"unsupported api_mode: {profile.api_mode}")


def validate_tool_definitions(
    tools: list[dict[str, Any]],
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(tools, list) or (not tools and not allow_empty):
        raise ModelConfigurationError("tools must be a list" if allow_empty else "tools must be a non-empty list")
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = str((function or {}).get("name") or "")
        parameters = (function or {}).get("parameters")
        if tool.get("type") != "function" or not name or not isinstance(parameters, dict):
            raise ModelConfigurationError("each tool must be an OpenAI function definition")
        if name in names:
            raise ModelConfigurationError(f"duplicate tool name: {name}")
        names.add(name)
        validate_schema(parameters)


def build_tool_request_kwargs(
    profile: LLMProfile,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str | dict[str, Any],
    options: dict[str, Any],
    request_metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    metadata = sanitize_metadata(request_metadata)
    effective_tools = tools
    if (
        tool_choice == "none"
        and profile.request_mapping.finalization_strategy == "tool_choice_none"
        and not effective_tools
    ):
        candidate_tools = options.get("finalization_tools") or []
        if isinstance(candidate_tools, list):
            validate_tool_definitions(candidate_tools, allow_empty=True)
            effective_tools = candidate_tools
    if effective_tools and not profile.capabilities.supports_tools:
        raise ModelConfigurationError(
            f"profile {profile.name} does not support tools required by this agent turn"
        )
    finalizing = tool_choice == "none"
    send_tools = bool(effective_tools)
    if finalizing and profile.request_mapping.finalization_strategy == "omit_tools":
        send_tools = False
    send_tool_choice = profile.capabilities.supports_tool_choice and send_tools
    if finalizing and send_tool_choice and not profile.capabilities.supports_tool_choice_none:
        raise ModelConfigurationError(
            f"profile {profile.name} does not support tool_choice=none"
        )
    if profile.api_mode == "chat_completions":
        kwargs: dict[str, Any] = {
            "model": profile.model,
            "messages": prepare_messages_for_profile(profile, messages),
        }
        if send_tools:
            kwargs["tools"] = effective_tools
        if send_tool_choice:
            kwargs["tool_choice"] = tool_choice
        if profile.send_temperature and profile.temperature is not None:
            kwargs["temperature"] = options.get("temperature", profile.temperature)
        apply_token_limit(
            profile,
            kwargs,
            options.get("max_output_tokens", profile.max_output_tokens),
        )
        if profile.send_metadata and metadata:
            kwargs["metadata"] = metadata
        extra_body = filtered_extra_body(profile)
        if extra_body:
            kwargs["extra_body"] = extra_body
        apply_reasoning_effort(
            profile,
            kwargs,
            override=options.get("reasoning_effort"),
        )
        if options.get("stream"):
            kwargs["stream"] = True
            if profile.capabilities.supports_usage_in_stream:
                kwargs["stream_options"] = {"include_usage": True}
        return "client.chat.completions.create", kwargs
    if profile.api_mode == "responses":
        kwargs = {
            "model": profile.model,
            "input": responses_input_from_messages(
                prepare_messages_for_profile(profile, messages)
            ),
        }
        if send_tools:
            kwargs["tools"] = [responses_tool_definition(item) for item in effective_tools]
        if send_tool_choice:
            kwargs["tool_choice"] = responses_tool_choice(tool_choice)
        if profile.send_temperature and profile.temperature is not None:
            kwargs["temperature"] = options.get("temperature", profile.temperature)
        apply_token_limit(
            profile,
            kwargs,
            options.get("max_output_tokens", profile.max_output_tokens),
        )
        if profile.send_metadata and metadata:
            kwargs["metadata"] = metadata
        extra_body = filtered_extra_body(profile)
        if extra_body:
            kwargs["extra_body"] = extra_body
        apply_reasoning_effort(
            profile,
            kwargs,
            override=options.get("reasoning_effort"),
        )
        if options.get("stream"):
            kwargs["stream"] = True
        return "client.responses.create", kwargs
    raise ModelConfigurationError(f"unsupported api_mode: {profile.api_mode}")


def apply_token_limit(
    profile: LLMProfile,
    kwargs: dict[str, Any],
    value: Any,
) -> None:
    if not profile.send_max_output_tokens or value is None:
        return
    parameter = profile.request_mapping.token_limit_parameter
    if profile.api_mode == "responses" and profile.request_mapping == type(profile.request_mapping)():
        parameter = "max_output_tokens"
    for candidate in TOKEN_LIMIT_KEYS:
        kwargs.pop(candidate, None)
    kwargs[parameter] = int(value)


TOKEN_LIMIT_KEYS = {"max_tokens", "max_completion_tokens", "max_output_tokens"}


def apply_reasoning_effort(
    profile: LLMProfile,
    kwargs: dict[str, Any],
    *,
    override: str | None = None,
) -> None:
    effort = str(override or profile.request_mapping.reasoning_effort or "").strip()
    if profile.request_mapping.thinking_enabled and effort:
        kwargs["reasoning_effort"] = effort


def prepare_messages_for_profile(
    profile: LLMProfile,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map roles and replay fields from neutral messages using profile capabilities."""
    copied: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for source in messages:
        message = dict(source)
        role = str(message.get("role") or "")
        if role == "assistant" and (
            not profile.capabilities.supports_reasoning_content
            or not profile.capabilities.requires_reasoning_content_replay
        ):
            message.pop("reasoning_content", None)
        if role in {"system", "developer"} and not profile.capabilities.supports_developer_role:
            content = str(message.get("content") or "").strip()
            if content:
                system_parts.append(content)
            continue
        if role == "system" and profile.request_mapping.system_role == "developer":
            message["role"] = "developer"
        copied.append(message)
    if system_parts:
        copied.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return copied


def responses_input_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant" and message.get("tool_calls"):
            content = message.get("content")
            if content:
                result.append({"role": "assistant", "content": str(content)})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                result.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id"),
                        "name": function.get("name"),
                        "arguments": arguments,
                    }
                )
        elif role == "tool":
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id"),
                    "output": str(message.get("content") or ""),
                }
            )
        else:
            result.append({"role": role, "content": str(message.get("content") or "")})
    return result


def responses_tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool["function"]
    result = {
        "type": "function",
        "name": function["name"],
        "description": function.get("description") or "",
        "parameters": function["parameters"],
    }
    if "strict" in function:
        result["strict"] = bool(function["strict"])
    return result


def responses_tool_choice(choice: str | dict[str, Any]) -> str | dict[str, Any]:
    if not isinstance(choice, dict):
        return choice
    function = choice.get("function") or {}
    return {"type": "function", "name": function.get("name")} if function.get("name") else choice


def extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def parsed_call(
        *, call_id: str, name: str, raw_arguments: Any
    ) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return {
                "tool_call_id": call_id,
                "name": name,
                "arguments": raw_arguments,
                "argument_metadata": {},
            }
        parsed = parse_model_tool_arguments(
            "{}" if raw_arguments in {None, ""} else str(raw_arguments),
            tool_name=name,
        )
        metadata = dict(parsed.json_syntax_repair)
        if metadata.get("applied"):
            metadata["_original_arguments"] = parsed.raw_arguments
            metadata["_repaired_arguments"] = parsed.repaired_arguments
        return {
            "tool_call_id": call_id,
            "name": name,
            "arguments": parsed.arguments,
            "argument_metadata": metadata,
        }

    choices = list(get_value(response, "choices") or [])
    if choices:
        message = get_value(choices[0], "message")
        for index, call in enumerate(list(get_value(message, "tool_calls") or []), start=1):
            function = get_value(call, "function")
            name = str(get_value(function, "name") or "")
            result.append(
                parsed_call(
                    call_id=str(get_value(call, "id") or f"call-{index}"),
                    name=name,
                    raw_arguments=get_value(function, "arguments"),
                )
            )
    for index, item in enumerate(list(get_value(response, "output") or []), start=1):
        if str(get_value(item, "type") or "") != "function_call":
            continue
        name = str(get_value(item, "name") or "")
        result.append(
            parsed_call(
                call_id=str(
                    get_value(item, "call_id")
                    or get_value(item, "id")
                    or f"call-{index}"
                ),
                name=name,
                raw_arguments=get_value(item, "arguments"),
            )
        )
    return result


def extract_assistant_turn(
    response: Any,
    *,
    supports_reasoning_content: bool,
) -> AssistantTurn:
    calls = [
        ToolCall(
            id=str(item.get("tool_call_id") or ""),
            name=str(item.get("name") or ""),
            arguments=dict(item.get("arguments") or {}),
            argument_metadata=dict(item.get("argument_metadata") or {}),
        )
        for item in extract_tool_calls(response)
    ]
    reasoning = extract_reasoning_content(response) if supports_reasoning_content else None
    usage = usage_object(extract_usage(response))
    return AssistantTurn(
        content=extract_text(response) or None,
        reasoning_content=reasoning,
        tool_calls=calls,
        finish_reason=extract_finish_status(response),
        usage=usage,
        provider_metadata={"request_id": extract_request_id(response)},
    )


def extract_reasoning_content(response: Any) -> str | None:
    direct = get_value(response, "reasoning_content")
    if direct:
        return str(direct)
    choices = list(get_value(response, "choices") or [])
    if choices:
        message = get_value(choices[0], "message")
        value = get_value(message, "reasoning_content")
        if value:
            return str(value)
    parts: list[str] = []
    for item in list(get_value(response, "output") or []):
        value = get_value(item, "reasoning_content")
        if value:
            parts.append(str(value))
    return "".join(parts) or None


def usage_object(value: dict[str, int | None]) -> Usage:
    known = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
    }
    return Usage(
        input_tokens=value.get("input_tokens"),
        output_tokens=value.get("output_tokens"),
        total_tokens=value.get("total_tokens"),
        cached_tokens=value.get("cached_tokens"),
        reasoning_tokens=value.get("reasoning_tokens"),
        provider_details={key: item for key, item in value.items() if key not in known and item is not None},
    )


def consume_streaming_tool_response(
    stream: Any,
    *,
    api_mode: str,
    supports_reasoning_content: bool = False,
    event_handler: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[AssistantTurn, Any]:
    """Assemble Chat/Responses deltas without inventing text or tool arguments."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    completed_response: Any = None
    last_event: Any = None
    latest_usage = empty_usage()
    finish_reason: str | None = None

    def notify(kind: str, **data: Any) -> None:
        if event_handler:
            event_handler({"type": kind, **data})

    for event in stream:
        last_event = event
        if api_mode == "chat_completions":
            choices = list(get_value(event, "choices") or [])
            if not choices:
                usage_value = extract_usage(event)
                if any(value is not None for value in usage_value.values()):
                    latest_usage = usage_value
                    notify("usage_update", usage=usage_value)
                continue
            choice = choices[0]
            delta = get_value(choice, "delta")
            if get_value(choice, "finish_reason"):
                finish_reason = str(get_value(choice, "finish_reason"))
            reasoning_delta = (
                get_value(delta, "reasoning_content")
                if supports_reasoning_content
                else None
            )
            if reasoning_delta:
                value = str(reasoning_delta)
                reasoning_parts.append(value)
                notify("reasoning_delta", character_count=len(value))
            content = get_value(delta, "content")
            if content:
                value = str(content)
                text_parts.append(value)
                notify("text_delta", delta=value)
            for index, call in enumerate(list(get_value(delta, "tool_calls") or [])):
                call_index = str(get_value(call, "index"))
                if call_index == "None":
                    call_index = str(index)
                entry = calls.setdefault(
                    call_index,
                    {"tool_call_id": "", "name": "", "argument_parts": [], "started": False},
                )
                if get_value(call, "id"):
                    entry["tool_call_id"] = str(get_value(call, "id"))
                if not entry["started"]:
                    notify("tool_call_started", tool_call_id=entry["tool_call_id"], index=int(call_index))
                    entry["started"] = True
                function = get_value(call, "function")
                if get_value(function, "name"):
                    name_delta = str(get_value(function, "name"))
                    entry["name"] += name_delta
                    notify(
                        "tool_call_name_delta",
                        tool_call_id=entry["tool_call_id"],
                        delta=name_delta,
                    )
                arguments_delta = get_value(function, "arguments")
                if arguments_delta:
                    value = str(arguments_delta)
                    entry["argument_parts"].append(value)
                    notify(
                        "tool_call_arguments_delta",
                        tool_call_id=entry["tool_call_id"],
                        argument_character_count=len(value),
                    )
            usage_value = extract_usage(event)
            if any(value is not None for value in usage_value.values()):
                latest_usage = usage_value
                notify("usage_update", usage=usage_value)
            continue

        event_type = str(get_value(event, "type") or "")
        if event_type == "response.output_text.delta":
            value = str(get_value(event, "delta") or "")
            if value:
                text_parts.append(value)
                notify("text_delta", delta=value)
        elif event_type in {
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
            "response.reasoning.delta",
        } and supports_reasoning_content:
            value = str(get_value(event, "delta") or "")
            if value:
                reasoning_parts.append(value)
                notify("reasoning_delta", character_count=len(value))
        elif event_type == "response.output_item.added":
            item = get_value(event, "item")
            if str(get_value(item, "type") or "") == "function_call":
                key = str(get_value(item, "id") or get_value(item, "call_id") or len(calls))
                calls[key] = {
                    "tool_call_id": str(get_value(item, "call_id") or get_value(item, "id") or key),
                    "name": str(get_value(item, "name") or ""),
                    "argument_parts": [],
                    "started": True,
                }
                notify("tool_call_started", tool_call_id=calls[key]["tool_call_id"])
                if calls[key]["name"]:
                    notify(
                        "tool_call_name_delta",
                        tool_call_id=calls[key]["tool_call_id"],
                        delta=calls[key]["name"],
                    )
        elif event_type == "response.function_call_arguments.delta":
            key = str(get_value(event, "item_id") or get_value(event, "call_id") or len(calls))
            entry = calls.setdefault(
                key,
                {
                    "tool_call_id": str(get_value(event, "call_id") or key),
                    "name": str(get_value(event, "name") or ""),
                    "argument_parts": [],
                    "started": True,
                },
            )
            if len(entry["argument_parts"]) == 0 and key not in calls:
                notify("tool_call_started", tool_call_id=entry["tool_call_id"])
            value = str(get_value(event, "delta") or "")
            entry["argument_parts"].append(value)
            notify(
                "tool_call_arguments_delta",
                tool_call_id=entry["tool_call_id"],
                argument_character_count=len(value),
            )
        elif event_type == "response.output_item.done":
            item = get_value(event, "item")
            if str(get_value(item, "type") or "") == "function_call":
                key = str(get_value(item, "id") or get_value(item, "call_id") or len(calls))
                calls[key] = {
                    "tool_call_id": str(get_value(item, "call_id") or get_value(item, "id") or key),
                    "name": str(get_value(item, "name") or ""),
                    "argument_parts": [str(get_value(item, "arguments") or "")],
                    "started": True,
                }
        elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
            completed_response = get_value(event, "response") or completed_response
            if completed_response is not None:
                latest_usage = extract_usage(completed_response)
                if any(value is not None for value in latest_usage.values()):
                    notify("usage_update", usage=latest_usage)
                finish_reason = extract_finish_status(completed_response)

    if completed_response is not None:
        complete_turn = extract_assistant_turn(
            completed_response,
            supports_reasoning_content=supports_reasoning_content,
        )
        if complete_turn.tool_calls or complete_turn.content:
            for call in complete_turn.tool_calls:
                notify("tool_call_completed", tool_call_id=call.id, name=call.name)
            notify("response_completed", finish_reason=complete_turn.finish_reason)
            return complete_turn, completed_response

    normalized_calls: list[ToolCall] = []
    for index, entry in enumerate(calls.values(), start=1):
        arguments_text = "".join(entry["argument_parts"])
        try:
            parsed_arguments = parse_model_tool_arguments(
                arguments_text,
                tool_name=str(entry["name"] or ""),
            )
            arguments = parsed_arguments.arguments
        except ModelJsonParseError as exc:
            diagnostics = exc.safe_diagnostics()
            diagnostics.update(
                {
                    "finish_reason": finish_reason,
                    "usage": latest_usage,
                    "tool_call_id": entry["tool_call_id"] or f"call-{index}",
                    "tool_name": entry["name"],
                    "suspected_truncation": bool(
                        diagnostics.get("suspected_truncation")
                        or suspected_json_truncation(
                            arguments_text,
                            finish_reason=finish_reason,
                            usage=latest_usage,
                            error_summary=str(diagnostics.get("json_error_summary") or ""),
                        )
                    ),
                }
            )
            raise ModelJsonParseError(
                str(exc),
                diagnostics=diagnostics,
                raw_content=arguments_text,
            ) from None
        call = ToolCall(
            id=entry["tool_call_id"] or f"call-{index}",
            name=entry["name"],
            arguments=arguments,
            argument_metadata={
                **dict(parsed_arguments.json_syntax_repair),
                **(
                    {
                        "_original_arguments": parsed_arguments.raw_arguments,
                        "_repaired_arguments": parsed_arguments.repaired_arguments,
                    }
                    if parsed_arguments.json_syntax_repair.get("applied")
                    else {}
                ),
            },
        )
        normalized_calls.append(call)
        notify("tool_call_completed", tool_call_id=call.id, name=call.name)
    turn = AssistantTurn(
        content=None if normalized_calls else "".join(text_parts) or None,
        reasoning_content="".join(reasoning_parts) or None,
        tool_calls=normalized_calls,
        finish_reason=finish_reason or "completed",
        usage=usage_object(latest_usage),
        provider_metadata={"request_id": extract_request_id(last_event)},
    )
    notify("response_completed", finish_reason=turn.finish_reason)
    return turn, completed_response or last_event


def _tool_argument_repair_metadata(
    *,
    raw: str,
    repaired: str,
    attempted: bool,
    applied: bool,
    positions: list[int],
) -> dict[str, Any]:
    return {
        "attempted": bool(attempted),
        "applied": bool(applied),
        "repair_type": (
            "escape_unescaped_inner_double_quote" if attempted else None
        ),
        "repair_count": len(positions),
        "original_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "repaired_sha256": hashlib.sha256(repaired.encode("utf-8")).hexdigest(),
        "original_character_count": len(raw),
        "repaired_character_count": len(repaired),
        # Positions are zero-based indexes in the progressively repaired text,
        # immediately before each backslash insertion.
        "repair_positions": list(positions),
        "repair_position_basis": "progressive_repaired_text_zero_based",
    }


def _supported_inner_quote_index(
    value: str,
    error: json.JSONDecodeError,
) -> int | None:
    if error.msg != "Expecting ',' delimiter":
        return None
    position = int(error.pos)
    if position < 0 or position >= len(value):
        return None
    current = value[position]
    if current.isspace() or current in '{}[],:"':
        return None
    quote_index = position - 1
    while quote_index >= 0 and value[quote_index].isspace():
        quote_index -= 1
    if quote_index < 0 or value[quote_index] != '"':
        return None
    slash_count = 0
    cursor = quote_index - 1
    while cursor >= 0 and value[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    if slash_count % 2:
        return None
    return quote_index


def parse_model_tool_arguments(
    raw_arguments: str,
    *,
    tool_name: str,
    allow_narrow_quote_repair: bool = True,
) -> ParsedToolArguments:
    raw = str(raw_arguments)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as initial_error:
        if not allow_narrow_quote_repair:
            metadata = _tool_argument_repair_metadata(
                raw=raw,
                repaired=raw,
                attempted=False,
                applied=False,
                positions=[],
            )
            raise ModelJsonParseError(
                "tool arguments are not valid JSON: " + str(initial_error)[:500],
                diagnostics={
                    "json_context": "tool_arguments",
                    "tool_name": tool_name,
                    "json_error_summary": str(initial_error)[:500],
                    "json_syntax_repair": metadata,
                },
                raw_content=raw,
            ) from None
        current = raw
        positions: list[int] = []
        last_error = initial_error
        for _ in range(MAX_TOOL_ARGUMENT_QUOTE_REPAIRS):
            quote_index = _supported_inner_quote_index(current, last_error)
            if quote_index is None:
                break
            current = current[:quote_index] + '\\"' + current[quote_index + 1 :]
            positions.append(quote_index)
            try:
                parsed = json.loads(current)
            except json.JSONDecodeError as next_error:
                last_error = next_error
                continue
            if not isinstance(parsed, dict):
                last_error = json.JSONDecodeError(
                    "tool arguments top-level value is not an object",
                    current,
                    0,
                )
                break
            metadata = _tool_argument_repair_metadata(
                raw=raw,
                repaired=current,
                attempted=True,
                applied=True,
                positions=positions,
            )
            return ParsedToolArguments(
                arguments=parsed,
                raw_arguments=raw,
                repaired_arguments=current,
                json_syntax_repair=metadata,
            )
        metadata = _tool_argument_repair_metadata(
            raw=raw,
            repaired=current,
            attempted=True,
            applied=False,
            positions=positions,
        )
        raise ModelJsonParseError(
            "tool arguments are not valid JSON after narrow quote repair: "
            + str(last_error)[:500],
            diagnostics={
                "json_context": "tool_arguments",
                "tool_name": tool_name,
                "json_error_summary": str(last_error)[:500],
                "json_syntax_repair": metadata,
            },
            raw_content=raw,
        ) from None
    if not isinstance(parsed, dict):
        raise ModelJsonParseError(
            "tool arguments top-level value is not an object",
            diagnostics={
                "json_context": "tool_arguments",
                "tool_name": tool_name,
                "json_syntax_repair": _tool_argument_repair_metadata(
                    raw=raw,
                    repaired=raw,
                    attempted=False,
                    applied=False,
                    positions=[],
                ),
            },
            raw_content=raw,
        )
    return ParsedToolArguments(
        arguments=parsed,
        raw_arguments=raw,
        repaired_arguments=raw,
        json_syntax_repair=_tool_argument_repair_metadata(
            raw=raw,
            repaired=raw,
            attempted=False,
            applied=False,
            positions=[],
        ),
    )


def parse_tool_arguments(value: Any, *, tool_name: str = "") -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    return parse_model_tool_arguments(
        str(value),
        tool_name=tool_name,
    ).arguments


def extract_builtin_web_search_sources(
    response: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize Ark/OpenAI-compatible web_search_call items and URL annotations."""
    retrieved_at = datetime.now(timezone.utc).isoformat()
    by_url: dict[str, dict[str, Any]] = {}
    citations: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for item in list(get_value(response, "output") or []):
        item_type = str(get_value(item, "type") or "")
        if item_type == "web_search_call":
            action = get_value(item, "action")
            call = {
                "call_id": get_value(item, "id"),
                "status": get_value(item, "status"),
                "action_type": get_value(action, "type"),
                "query": get_value(action, "query"),
            }
            calls.append({key: value for key, value in call.items() if value is not None})
            for source in list(get_value(action, "sources") or []):
                _merge_web_source(
                    by_url,
                    url=get_value(source, "url"),
                    title=get_value(source, "title"),
                    publisher=get_value(source, "publisher") or get_value(source, "site_name"),
                    published_at=get_value(source, "published_at") or get_value(source, "published_time"),
                    retrieved_at=retrieved_at,
                    citation={"web_search_call_id": get_value(item, "id")},
                )
        for content in list(get_value(item, "content") or []):
            for annotation in list(get_value(content, "annotations") or []):
                if str(get_value(annotation, "type") or "") not in {"url_citation", "citation"}:
                    continue
                url = get_value(annotation, "url")
                citation = {
                    "type": get_value(annotation, "type"),
                    "url": url,
                    "title": get_value(annotation, "title"),
                    "start_index": get_value(annotation, "start_index"),
                    "end_index": get_value(annotation, "end_index"),
                }
                citation = {key: value for key, value in citation.items() if value is not None}
                citations.append(citation)
                _merge_web_source(
                    by_url,
                    url=url,
                    title=get_value(annotation, "title"),
                    publisher=get_value(annotation, "publisher") or get_value(annotation, "site_name"),
                    published_at=get_value(annotation, "published_at"),
                    retrieved_at=retrieved_at,
                    citation=citation,
                )
    return list(by_url.values()), citations, calls


def _merge_web_source(
    by_url: dict[str, dict[str, Any]],
    *,
    url: Any,
    title: Any,
    publisher: Any,
    published_at: Any,
    retrieved_at: str,
    citation: dict[str, Any],
) -> None:
    url_text = str(url or "").strip()
    if not url_text:
        return
    host = (urllib.parse.urlparse(url_text).hostname or "").lower()
    item = by_url.setdefault(
        url_text,
        {
            "title": str(title or url_text),
            "url": url_text,
            "publisher": str(publisher or host),
            "published_at": published_at,
            "retrieved_at": retrieved_at,
            "source_type": "web",
            "citations": [],
        },
    )
    if title and item["title"] == url_text:
        item["title"] = str(title)
    if publisher and item["publisher"] == host:
        item["publisher"] = str(publisher)
    if published_at and not item.get("published_at"):
        item["published_at"] = published_at
    if citation and citation not in item["citations"]:
        item["citations"].append(citation)


def filtered_extra_body(profile: LLMProfile) -> dict[str, Any]:
    result = dict(profile.extra_body)
    if not profile.send_thinking:
        result.pop("thinking", None)
    return result


def inspect_request_parameters(
    profile: LLMProfile,
    *,
    system_prompt: str,
    user_prompt: str,
    response_schema: dict[str, Any],
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    method, kwargs = build_request_kwargs(
        profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=response_schema,
        request_metadata=request_metadata,
    )
    input_value = kwargs.get("input")
    if input_value is None:
        input_value = json.dumps(kwargs.get("messages") or [], ensure_ascii=False)
    extra_body = kwargs.get("extra_body") or {}
    return {
        "sdk_method": method,
        "parameter_names": sorted(kwargs.keys()),
        "api_mode": profile.api_mode,
        "structured_output_mode": profile.structured_output_mode,
        "instructions_sent": "instructions" in kwargs,
        "temperature_sent": "temperature" in kwargs,
        "max_output_tokens_sent": "max_output_tokens" in kwargs or "max_tokens" in kwargs,
        "thinking_sent": "thinking" in kwargs or "thinking" in extra_body,
        "metadata_sent": "metadata" in kwargs,
        "native_schema_sent": "text" in kwargs or "response_format" in kwargs,
        "input_character_count": len(str(input_value)),
        "schema_name": response_schema.get("title") or response_schema.get("$id") or "chapter_draft",
    }


def responses_structured_parameter(schema: dict[str, Any], mode: str) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {"format": {"type": "json_schema", "name": "chapter_draft", "schema": schema, "strict": True}}
    if mode == "json_object":
        return {"format": {"type": "json_object"}}
    return None


def chat_structured_parameter(schema: dict[str, Any], mode: str) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "chapter_draft", "schema": schema, "strict": True}}
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def extract_text(response: Any) -> str:
    output_text = get_value(response, "output_text")
    if output_text:
        return str(output_text)
    choices = get_value(response, "choices") or []
    if choices:
        message = get_value(choices[0], "message")
        content = get_value(message, "content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(get_value(item, "text") or "") for item in content)
    return ""


def extract_refusal(response: Any) -> str | None:
    choices = get_value(response, "choices") or []
    if choices:
        refusal = get_value(get_value(choices[0], "message"), "refusal")
        if refusal:
            return str(refusal)
    status = str(get_value(response, "status") or "").lower()
    return status if status in {"refused", "safety_refusal"} else None


def extract_request_id(response: Any) -> str | None:
    value = get_value(response, "id") or get_value(response, "request_id")
    return str(value) if value else None


def extract_finish_status(response: Any) -> str:
    status = get_value(response, "status")
    if status:
        return str(status)
    choices = get_value(response, "choices") or []
    if choices:
        reason = get_value(choices[0], "finish_reason")
        return str(reason or "completed")
    return "completed"


def extract_usage(response: Any) -> dict[str, int | None]:
    usage = get_value(response, "usage")
    if not usage:
        return empty_usage()
    input_tokens = get_value(usage, "input_tokens")
    if input_tokens is None:
        input_tokens = get_value(usage, "prompt_tokens")
    output_tokens = get_value(usage, "output_tokens")
    if output_tokens is None:
        output_tokens = get_value(usage, "completion_tokens")
    total_tokens = get_value(usage, "total_tokens")
    input_details = get_value(usage, "input_tokens_details") or get_value(usage, "prompt_tokens_details")
    output_details = get_value(usage, "output_tokens_details") or get_value(usage, "completion_tokens_details")
    reasoning_tokens = get_value(output_details, "reasoning_tokens")
    answer_tokens = get_value(output_details, "answer_tokens")
    cached_tokens = get_value(input_details, "cached_tokens")
    return {
        "input_tokens": int(input_tokens) if input_tokens is not None else None,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "total_tokens": int(total_tokens) if total_tokens is not None else None,
        "reasoning_tokens": int(reasoning_tokens) if reasoning_tokens is not None else None,
        "answer_tokens": int(answer_tokens) if answer_tokens is not None else None,
        "cached_tokens": int(cached_tokens) if cached_tokens is not None else None,
    }


def response_diagnostics(response: Any) -> dict[str, Any]:
    text = extract_text(response)
    incomplete = get_value(response, "incomplete_details")
    output_types: list[str] = []
    for item in list(get_value(response, "output") or []):
        item_type = get_value(item, "type")
        if item_type:
            output_types.append(str(item_type))
        for content in list(get_value(item, "content") or []):
            content_type = get_value(content, "type")
            if content_type:
                output_types.append(f"{item_type or 'output'}.{content_type}")
    return {
        "request_id": extract_request_id(response),
        "response_received": response is not None,
        "response_status": str(get_value(response, "status") or "") or None,
        "finish_status": extract_finish_status(response),
        "incomplete_details": safe_incomplete_details(incomplete),
        "usage": extract_usage(response),
        "output_item_types": list(dict.fromkeys(output_types)),
        "output_text_available": bool(text),
        "output_text_character_count": len(text),
    }


def safe_incomplete_details(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    result: dict[str, Any] = {}
    for key in ["reason", "type", "code", "message"]:
        item = get_value(value, key)
        if isinstance(item, (str, int, float, bool)) or item is None:
            if item is not None:
                result[key] = item
    return result or {"present": True}


def attach_response_diagnostics(
    error: ModelError,
    response: Any,
    *,
    failed_stage: str,
    repair_attempted: bool,
    max_output_tokens: int | None = None,
    provider_request_ms: int | None = None,
) -> ModelError:
    diagnostics = response_diagnostics(response)
    stages = ["request_started", "response_received", "output_items_inspected"]
    if diagnostics.get("output_text_available"):
        stages.append("output_extracted")
    if failed_stage == "local_schema_validation":
        stages.append("json_parsed")
    diagnostics.update(
        {
            "failed_stage": failed_stage,
            "repair_attempted": repair_attempted,
            "processing_stages": stages,
            "warnings": output_budget_warnings(diagnostics, max_output_tokens),
            "provider_request_ms": provider_request_ms,
        }
    )
    error.diagnostics = {**error.safe_diagnostics(), **diagnostics}
    return error


def elapsed_ms(started: datetime) -> int:
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))


def output_budget_warnings(diagnostics: dict[str, Any], max_output_tokens: int | None) -> list[str]:
    usage = diagnostics.get("usage") or {}
    incomplete = diagnostics.get("incomplete_details") or {}
    reason = str(incomplete.get("reason") or "").lower()
    finish = str(diagnostics.get("finish_status") or "").lower()
    no_text = not diagnostics.get("output_text_available")
    reasoning_tokens = usage.get("reasoning_tokens")
    near_budget = (
        max_output_tokens is not None
        and reasoning_tokens is not None
        and reasoning_tokens >= max(1, int(max_output_tokens * 0.9))
    )
    explicit_budget = reason in {"max_output_tokens", "length", "max_tokens"} or finish in {"length", "incomplete"}
    if no_text and (explicit_budget or near_budget):
        return ["The output token budget appears to have been consumed before a final structured answer was produced."]
    return []


def merge_usage(first: dict[str, int | None], second: dict[str, int | None]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for key in empty_usage():
        values = [value for value in [first.get(key), second.get(key)] if value is not None]
        result[key] = sum(values) if values else None
    return result


def map_provider_error(
    exc: Exception,
    *,
    profile: LLMProfile | None = None,
    failed_operation: str | None = None,
) -> ModelError:
    if isinstance(exc, ModelError):
        return exc
    status = getattr(exc, "status_code", None)
    name = exc.__class__.__name__.lower()
    details = provider_error_details(exc, profile=profile, failed_operation=failed_operation)
    if status == 401 or "authentication" in name:
        return ModelAuthenticationError("model provider authentication failed", diagnostics=details)
    if status == 403 or "permission" in name:
        return ModelPermissionError("model provider permission denied", diagnostics=details)
    if status == 404 or "notfound" in name:
        return ModelNotFoundError("configured model or endpoint was not found", diagnostics=details)
    if status == 429 or "ratelimit" in name:
        return ModelRateLimitError("model provider rate limit exceeded", diagnostics={**details, "retryable": True})
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return ModelTimeoutError("model provider request timed out", diagnostics={**details, "retryable": True})
    if status is not None and int(status) >= 500:
        return ModelConnectionError(
            "model provider service is temporarily unavailable",
            diagnostics={**details, "retryable": True},
        )
    if "connection" in name:
        return ModelConnectionError("model provider connection failed", diagnostics={**details, "retryable": True})
    provider_message = details.get("provider_error_message")
    message = "model provider request failed"
    if provider_message:
        message += f": {provider_message}"
    return ModelResponseError(message, diagnostics=details)


def provider_error_details(
    exc: Exception,
    *,
    profile: LLMProfile | None,
    failed_operation: str | None,
) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        body = {}
    error_body = body.get("error") if isinstance(body.get("error"), dict) else body
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    request_id = (
        getattr(exc, "request_id", None)
        or error_body.get("request_id")
        or safe_header(headers, "x-request-id")
        or safe_header(headers, "x-tt-logid")
    )
    message = error_body.get("message") or getattr(exc, "message", None) or str(exc)
    api_key = profile.api_key if profile is not None else ""
    details = {
        "status_code": getattr(exc, "status_code", None),
        "provider_error_code": error_body.get("code"),
        "provider_error_type": error_body.get("type") or exc.__class__.__name__,
        "provider_error_message": redact_sensitive_text(str(message), api_key),
        "request_id": str(request_id) if request_id else None,
        "failed_operation": failed_operation,
        "profile_name": profile.name if profile else None,
        "provider": profile.provider if profile else None,
        "model": profile.model if profile else None,
        "base_url_host": (
            (urllib.parse.urlparse(profile.base_url).hostname or "").lower()
            if profile
            else None
        ),
        "api_mode": profile.api_mode if profile else None,
        "structured_output_mode": profile.structured_output_mode if profile else None,
        "retryable": False,
    }
    return details


def safe_header(headers: Any, name: str) -> Any:
    if hasattr(headers, "get"):
        return headers.get(name) or headers.get(name.upper())
    return None


def redact_sensitive_text(text: str, api_key: str = "") -> str:
    value = text
    if api_key:
        value = value.replace(api_key, "[REDACTED]")
    value = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", value)
    return value[:2000]


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if any(term in lowered for term in ["key", "token", "authorization", "secret", "password"]):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
    return result


def build_repair_prompt(response: Any, error: Exception) -> str:
    text = extract_text(response)
    if len(text) > 20000:
        text = text[:20000]
    return (
        "校验错误："
        + safe_validation_message(error)
        + "\n待修复 JSON：\n"
        + text
        + "\n只修复结构和字段，不新增未经输入支持的事实。"
    )


def safe_validation_message(error: Exception) -> str:
    return str(error)[:1000]


def get_value(value: Any, field_name: str) -> Any:
    if isinstance(value, dict):
        return value.get(field_name)
    return getattr(value, field_name, None)

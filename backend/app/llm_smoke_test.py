from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.chapter_draft_schema import load_chapter_draft_schema
from app.llm_client import empty_usage
from app.llm_errors import (
    ModelConfigurationError,
    ModelError,
    ModelJsonParseError,
    ModelOutputIncompleteError,
    ModelSchemaValidationError,
    ModelTimeoutError,
)
from app.llm_profiles import resolve_profile
from app.openai_compatible_llm_client import OpenAICompatibleLLMClient, inspect_request_parameters


SMOKE_MODES = {"minimal", "chapter-schema"}
THINKING_MODES = {"enabled", "disabled"}
MINIMAL_SMOKE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Minimal Model Connectivity Check",
    "type": "object",
    "properties": {"status": {"const": "ok"}},
    "required": ["status"],
    "additionalProperties": False,
}
MINIMAL_SYSTEM_PROMPT = "只输出严格 JSON，不要输出解释或 Markdown。"
MINIMAL_USER_PROMPT = '只返回：{"status":"ok"}'
CHAPTER_SMOKE_SYSTEM_PROMPT = """你是模型连通测试助手。只输出符合给定 Schema 的 JSON。
不得编造人员、业绩、证书、专利或其他企业事实，不得输出投标决策性结论。"""

CHAPTER_SMOKE_USER_PROMPT = """生成“模型连通测试”章节：
1. 只写一个简短技术方案小节，使用方案性或将来时表达。
2. 覆盖 score_item_id=smoke-score-001。
3. 覆盖 requirement_id=smoke-requirement-001。
4. 保留 placeholder_id=smoke-placeholder-001。
5. 不得生成任何企业既有事实。"""


def check_drafting_model(
    *,
    profile_name: str | None = None,
    output_dir: str | Path = "outputs",
    write_result: bool = True,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: Any = None,
    sleep_fn: Any = None,
    random_fn: Any = None,
    smoke_mode: str = "minimal",
    thinking: str | None = None,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    validate_smoke_options(smoke_mode, thinking, timeout_seconds, max_output_tokens)
    try:
        profile = resolve_profile(
            role="drafting",
            profile_name=profile_name,
            config_path=config_path,
            environ=environ,
            require_credentials=False,
            load_dotenv_file=environ is None,
        )
    except ModelConfigurationError as exc:
        result = configuration_result(
            profile_name,
            checked_at,
            str(exc),
            smoke_mode=smoke_mode,
            max_output_tokens=max_output_tokens,
        )
        return maybe_write(result, output_dir, write_result)

    smoke_profile = build_smoke_profile(
        profile,
        smoke_mode=smoke_mode,
        thinking=thinking,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )
    system_prompt, user_prompt, response_schema = smoke_request_contract(smoke_mode)
    base_result = {
        "schema_version": "1.0",
        "checked_at": checked_at,
        "status": "configuration_missing",
        "provider": profile.provider,
        "protocol": profile.protocol,
        "profile_name": profile.name,
        "model": profile.model or None,
        "api_mode": profile.api_mode,
        "structured_output_mode": profile.structured_output_mode,
        "smoke_mode": smoke_mode,
        "timeout_seconds": smoke_profile.timeout_seconds,
        "thinking_mode": profile_thinking_mode(smoke_profile),
        "max_output_tokens": smoke_profile.max_output_tokens,
        "profile_timeout_seconds": profile.timeout_seconds,
        "smoke_timeout_overrode_profile": smoke_profile.timeout_seconds != profile.timeout_seconds,
        "failed_stage": None,
        "provider_request_ms": None,
        "key_configured": bool(profile.api_key),
        "model_configured": bool(profile.model),
        "schema_valid": False,
        "request_id": None,
        "duration_ms": None,
        "usage": empty_usage(),
        "retry_count": 0,
        "repair_attempted": False,
        "finish_status": None,
        "response_status": None,
        "incomplete_details": None,
        "response_received": False,
        "output_item_types": [],
        "output_text_available": False,
        "output_text_character_count": 0,
        "processing_stages": ["request_started"],
        "length_truncated": None,
        "smoke_output": None,
        "warnings": [],
        "error_type": None,
        "error": None,
        "status_code": None,
        "provider_error_code": None,
        "provider_error_type": None,
        "provider_error_message": None,
        "failed_operation": None,
        "retryable": False,
        "writes_real_case": False,
    }
    base_result["warnings"].extend(smoke_budget_warnings(smoke_profile))
    if not profile.api_key:
        base_result["error_type"] = "ModelConfigurationError"
        base_result["error"] = f"API key is not configured; set {profile.api_key_env}"
        base_result["failed_stage"] = "configuration"
        return maybe_write(base_result, output_dir, write_result)
    if not profile.model:
        base_result["error_type"] = "ModelConfigurationError"
        base_result["error"] = f"model is not configured; set {profile.model_env}"
        base_result["failed_stage"] = "configuration"
        return maybe_write(base_result, output_dir, write_result)

    try:
        client = OpenAICompatibleLLMClient(
            smoke_profile,
            client_factory=client_factory,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
            schema_repair_enabled=smoke_mode != "minimal",
        )
        request_started = time.monotonic()
        call = client.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            request_metadata={
                "purpose": "drafting_model_smoke_test",
                "chapter_title": "模型连通测试",
            },
            profile_name=profile.name,
        )
        base_result.update(
            {
                "status": "success",
                "schema_valid": True,
                "request_id": call.request_id,
                "duration_ms": call.duration_ms,
                "usage": call.usage,
                "retry_count": call.retry_count,
                "repair_attempted": call.repair_attempted,
                "finish_status": call.finish_status,
                "response_status": call.response_status,
                "incomplete_details": call.incomplete_details,
                "response_received": call.response_received,
                "output_item_types": call.output_item_types,
                "output_text_available": call.output_text_available,
                "output_text_character_count": call.output_text_character_count,
                "processing_stages": call.processing_stages,
                "length_truncated": is_length_truncated(call.finish_status, call.incomplete_details),
                "smoke_output": call.parsed_output,
                "warnings": [*base_result["warnings"], *call.warnings],
                "provider_request_ms": int((time.monotonic() - request_started) * 1000),
            }
        )
    except ModelError as exc:
        provider_request_ms = (
            int((time.monotonic() - request_started) * 1000) if "request_started" in locals() else None
        )
        diagnostics = exc.safe_diagnostics()
        base_result.update(
            {
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "status_code": diagnostics.get("status_code"),
                "provider_error_code": diagnostics.get("provider_error_code"),
                "provider_error_type": diagnostics.get("provider_error_type"),
                "provider_error_message": diagnostics.get("provider_error_message"),
                "request_id": diagnostics.get("request_id"),
                "failed_operation": diagnostics.get("failed_operation"),
                "api_mode": diagnostics.get("api_mode") or base_result.get("api_mode"),
                "structured_output_mode": diagnostics.get("structured_output_mode")
                or base_result.get("structured_output_mode"),
                "retryable": bool(diagnostics.get("retryable")),
                "provider_request_ms": provider_request_ms,
                "failed_stage": diagnostics.get("failed_stage") or smoke_failure_stage(exc),
                "repair_attempted": bool(diagnostics.get("repair_attempted")),
                "usage": diagnostics.get("usage") or empty_usage(),
                "finish_status": diagnostics.get("finish_status"),
                "response_status": diagnostics.get("response_status"),
                "incomplete_details": diagnostics.get("incomplete_details"),
                "response_received": bool(diagnostics.get("response_received")),
                "output_item_types": diagnostics.get("output_item_types") or [],
                "output_text_available": bool(diagnostics.get("output_text_available")),
                "output_text_character_count": int(diagnostics.get("output_text_character_count") or 0),
                "processing_stages": diagnostics.get("processing_stages") or ["request_started"],
                "length_truncated": is_length_truncated(
                    diagnostics.get("finish_status"), diagnostics.get("incomplete_details")
                ),
                "warnings": [
                    *base_result["warnings"],
                    *(diagnostics.get("warnings") or []),
                ],
            }
        )
    return maybe_write(base_result, output_dir, write_result)


def configuration_result(
    profile_name: str | None,
    checked_at: str,
    message: str,
    *,
    smoke_mode: str,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "checked_at": checked_at,
        "status": "configuration_missing",
        "provider": None,
        "protocol": None,
        "profile_name": profile_name,
        "model": None,
        "api_mode": None,
        "structured_output_mode": None,
        "smoke_mode": smoke_mode,
        "timeout_seconds": 60.0 if smoke_mode == "minimal" else 120.0,
        "thinking_mode": "disabled",
        "max_output_tokens": max_output_tokens or (100 if smoke_mode == "minimal" else 1200),
        "profile_timeout_seconds": None,
        "smoke_timeout_overrode_profile": None,
        "failed_stage": "configuration",
        "provider_request_ms": None,
        "key_configured": False,
        "model_configured": False,
        "schema_valid": False,
        "request_id": None,
        "duration_ms": None,
        "usage": empty_usage(),
        "retry_count": 0,
        "repair_attempted": False,
        "finish_status": None,
        "response_status": None,
        "incomplete_details": None,
        "response_received": False,
        "output_item_types": [],
        "output_text_available": False,
        "output_text_character_count": 0,
        "processing_stages": ["request_started"],
        "length_truncated": None,
        "smoke_output": None,
        "warnings": [],
        "error_type": "ModelConfigurationError",
        "error": message,
        "status_code": None,
        "provider_error_code": None,
        "provider_error_type": None,
        "provider_error_message": None,
        "failed_operation": None,
        "retryable": False,
        "writes_real_case": False,
    }


def inspect_drafting_request(
    *,
    profile_name: str | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    smoke_mode: str = "minimal",
    thinking: str | None = None,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    validate_smoke_options(smoke_mode, thinking, timeout_seconds, max_output_tokens)
    profile = resolve_profile(
        role="drafting",
        profile_name=profile_name,
        config_path=config_path,
        environ=environ,
        require_credentials=False,
        load_dotenv_file=environ is None,
    )
    smoke_profile = build_smoke_profile(
        profile,
        smoke_mode=smoke_mode,
        thinking=thinking,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )
    system_prompt, user_prompt, response_schema = smoke_request_contract(smoke_mode)
    preview = inspect_request_parameters(
        smoke_profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=response_schema,
        request_metadata={"purpose": "drafting_model_smoke_test", "chapter_title": "模型连通测试"},
    )
    return {
        "schema_version": "1.0",
        "profile_name": profile.name,
        "provider": profile.provider,
        "protocol": profile.protocol,
        "key_configured": bool(profile.api_key),
        "model_configured": bool(profile.model),
        "smoke_mode": smoke_mode,
        "timeout_seconds": smoke_profile.timeout_seconds,
        "thinking_mode": profile_thinking_mode(smoke_profile),
        "max_output_tokens": smoke_profile.max_output_tokens,
        "profile_timeout_seconds": profile.timeout_seconds,
        "smoke_timeout_overrode_profile": smoke_profile.timeout_seconds != profile.timeout_seconds,
        **preview,
    }


def validate_smoke_options(
    smoke_mode: str,
    thinking: str | None,
    timeout_seconds: float | None,
    max_output_tokens: int | None,
) -> None:
    if smoke_mode not in SMOKE_MODES:
        raise ModelConfigurationError(f"unsupported smoke_mode: {smoke_mode}")
    if thinking is not None and thinking not in THINKING_MODES:
        raise ModelConfigurationError(f"unsupported thinking mode: {thinking}")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ModelConfigurationError("timeout_seconds must be positive")
    if max_output_tokens is not None and max_output_tokens <= 0:
        raise ModelConfigurationError("max_output_tokens must be positive")
    if smoke_mode == "minimal" and thinking not in {None, "disabled"}:
        raise ModelConfigurationError("minimal smoke requires thinking=disabled")
    if smoke_mode == "minimal" and timeout_seconds is not None:
        raise ModelConfigurationError("minimal smoke timeout is fixed at 60 seconds")


def build_smoke_profile(
    profile: Any,
    *,
    smoke_mode: str,
    thinking: str | None = None,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    """Return an immutable in-memory smoke override; never mutate or persist the formal profile."""
    thinking_mode = thinking or "disabled"
    common = {
        "max_retries": 0,
        "send_thinking": True,
        "send_metadata": False,
        "extra_body": {"thinking": {"type": thinking_mode}},
    }
    if smoke_mode == "minimal":
        return replace(
            profile,
            **common,
            structured_output_mode="prompt_json",
            use_instructions=False,
            temperature=0.0,
            send_temperature=True,
            max_output_tokens=int(max_output_tokens or 100),
            send_max_output_tokens=True,
            timeout_seconds=60.0,
        )
    return replace(
        profile,
        **common,
        max_output_tokens=int(max_output_tokens or 1200),
        send_max_output_tokens=True,
        timeout_seconds=float(timeout_seconds or 120.0),
    )


def smoke_request_contract(smoke_mode: str) -> tuple[str, str, dict[str, Any]]:
    if smoke_mode == "minimal":
        return MINIMAL_SYSTEM_PROMPT, MINIMAL_USER_PROMPT, MINIMAL_SMOKE_SCHEMA
    return CHAPTER_SMOKE_SYSTEM_PROMPT, CHAPTER_SMOKE_USER_PROMPT, load_chapter_draft_schema()


def profile_thinking_mode(profile: Any) -> str:
    if not getattr(profile, "send_thinking", False):
        return "not_sent"
    thinking = dict(getattr(profile, "extra_body", {}) or {}).get("thinking")
    if isinstance(thinking, dict):
        return str(thinking.get("type") or "configured")
    return str(thinking or "configured")


def smoke_failure_stage(exc: ModelError) -> str:
    if isinstance(exc, ModelTimeoutError):
        return "provider_request_timeout"
    if isinstance(exc, ModelOutputIncompleteError):
        return "output_extraction"
    if isinstance(exc, ModelJsonParseError):
        return "json_parse"
    if isinstance(exc, ModelSchemaValidationError):
        return "local_schema_validation"
    return "provider_request"


def smoke_budget_warnings(profile: Any) -> list[str]:
    if profile_thinking_mode(profile) == "enabled" and int(profile.max_output_tokens or 0) <= 1200:
        return ["thinking enabled with a low output token budget may leave no final structured answer"]
    return []


def is_length_truncated(finish_status: Any, incomplete_details: Any) -> bool | None:
    finish = str(finish_status or "").lower()
    details = incomplete_details if isinstance(incomplete_details, dict) else {}
    reason = str(details.get("reason") or "").lower()
    if finish in {"length", "incomplete"} or reason in {"max_output_tokens", "length", "max_tokens"}:
        return True
    if finish_status is None and not details:
        return None
    return False


def maybe_write(result: dict[str, Any], output_dir: str | Path, write_result: bool) -> dict[str, Any]:
    path = Path(output_dir) / "model_checks" / "latest_drafting_model_check.json"
    if write_result:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {**result, "output_path": str(path)}
    else:
        result = {**result, "output_path": None}
    return result

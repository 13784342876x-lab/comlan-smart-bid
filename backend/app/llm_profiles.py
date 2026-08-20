from __future__ import annotations

import json
import os
import urllib.parse
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

from app.llm_errors import ModelConfigurationError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_PROFILE_CONFIG = BACKEND_ROOT / "config" / "llm_profiles.example.json"
DEFAULT_LOCAL_PROFILE_CONFIG = BACKEND_ROOT / "config" / "llm_profiles.local.json"
ROLE_ENV_VARS = {
    "drafting": "COMLAN_LLM_DRAFT_PROFILE",
    "review": "COMLAN_LLM_REVIEW_PROFILE",
    "fast_task": "COMLAN_LLM_FAST_PROFILE",
}
API_MODES = {"responses", "chat_completions"}
STRUCTURED_OUTPUT_MODES = {"json_schema", "json_object", "prompt_json"}
FINALIZATION_STRATEGIES = {"omit_tools", "tool_choice_none"}
TOKEN_LIMIT_PARAMETERS = {"max_tokens", "max_completion_tokens", "max_output_tokens"}
SYSTEM_ROLES = {"system", "developer"}
PROVIDER_DEFAULT_BASE_URLS = {
    "volcengine_ark": "https://ark.cn-beijing.volces.com/api/v3",
}
PROVIDER_CAPABILITY_DEFAULTS = {
    "volcengine_ark": {
        "use_instructions": False,
        "send_temperature": False,
        "send_max_output_tokens": True,
        "send_thinking": False,
        "send_metadata": False,
    }
}
SENSITIVE_HEADER_NAMES = {"authorization", "proxy-authorization", "x-api-key", "api-key"}


@dataclass(frozen=True)
class ModelCapabilities:
    supports_tools: bool = True
    supports_parallel_tool_calls: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = True
    supports_reasoning_content: bool = False
    requires_reasoning_content_replay: bool = False
    supports_developer_role: bool = False
    supports_tool_choice: bool = True
    supports_tool_choice_none: bool = True
    supports_usage_in_stream: bool = False


@dataclass(frozen=True)
class RequestMapping:
    system_role: str = "system"
    token_limit_parameter: str = "max_tokens"
    finalization_strategy: str = "tool_choice_none"
    thinking_enabled: bool = False
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class LLMProfile:
    name: str
    provider: str
    protocol: str
    base_url: str
    api_key_env: str
    model_env: str
    model: str
    api_mode: str
    structured_output_mode: str
    timeout_seconds: float
    max_retries: int
    max_output_tokens: int | None
    temperature: float | None
    enabled: bool
    use_instructions: bool
    send_temperature: bool
    send_max_output_tokens: bool
    send_thinking: bool
    send_metadata: bool
    api_key: str = field(repr=False)
    extra_body: dict[str, Any] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    stream: bool = False
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    request_mapping: RequestMapping = field(default_factory=RequestMapping)

    @property
    def api_style(self) -> str:
        return self.api_mode

    def safe_summary(self) -> dict[str, Any]:
        return {
            "profile_name": self.name,
            "provider": self.provider,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "model_configured": bool(self.model),
            "api_style": self.api_style,
            "api_mode": self.api_mode,
            "structured_output_mode": self.structured_output_mode,
            "stream": self.stream,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "use_instructions": self.use_instructions,
            "send_temperature": self.send_temperature,
            "send_max_output_tokens": self.send_max_output_tokens,
            "send_thinking": self.send_thinking,
            "send_metadata": self.send_metadata,
            "capabilities": asdict(self.capabilities),
            "request_mapping": asdict(self.request_mapping),
            "enabled": self.enabled,
        }

    def run_report_summary(self) -> dict[str, Any]:
        host = (urllib.parse.urlparse(self.base_url).hostname or "").lower()
        return {
            "profile_name": self.name,
            "provider": self.provider,
            "api_style": self.api_style,
            "base_url_host": host,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "stream": self.stream,
            "capabilities": asdict(self.capabilities),
            "thinking_enabled": self.request_mapping.thinking_enabled,
            "reasoning_effort": self.request_mapping.reasoning_effort,
        }


@dataclass(frozen=True)
class LLMProfileConfig:
    schema_version: str
    default_profile: str
    role_bindings: dict[str, str]
    profiles: dict[str, dict[str, Any]]
    sources: tuple[str, ...]


def load_environment(
    environ: Mapping[str, str] | None = None,
    *,
    load_dotenv_files: bool = True,
    root: Path | None = None,
) -> dict[str, str]:
    """Load .env files without mutating os.environ.

    Explicit mappings are isolated for tests. Runtime precedence is process,
    .env.local, then .env.
    """
    if environ is not None:
        return {str(key): str(value) for key, value in environ.items()}
    if not load_dotenv_files:
        return dict(os.environ)
    if root is not None:
        dotenv_paths = (root / ".env", root / ".env.local")
    else:
        # Keep the historical backend files working while making the workspace
        # root files authoritative within each .env/.env.local tier.
        dotenv_paths = (
            BACKEND_ROOT / ".env",
            PROJECT_ROOT / ".env",
            BACKEND_ROOT / ".env.local",
            PROJECT_ROOT / ".env.local",
        )
    merged: dict[str, str] = {}
    for path in dotenv_paths:
        if not path.exists():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                merged[str(key)] = str(value)
    merged.update({str(key): str(value) for key, value in os.environ.items()})
    return merged


def load_profile_config(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    load_dotenv_file: bool = True,
) -> LLMProfileConfig:
    env = load_environment(environ, load_dotenv_files=load_dotenv_file)
    example_path = Path(config_path) if config_path else DEFAULT_PROFILE_CONFIG
    if not example_path.exists():
        raise ModelConfigurationError(f"LLM profile config not found: {example_path}")
    merged = read_json_object(example_path)
    sources = [str(example_path)]

    override_value = str(env.get("COMLAN_LLM_PROFILE_CONFIG") or "").strip()
    local_path = Path(override_value) if override_value else DEFAULT_LOCAL_PROFILE_CONFIG
    if local_path.exists() and local_path.resolve() != example_path.resolve():
        merged = merge_profile_config(merged, read_json_object(local_path))
        sources.append(str(local_path))

    profiles = merged.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ModelConfigurationError("LLM profile config must define at least one profile")
    role_bindings = merged.get("role_bindings") or {}
    if not isinstance(role_bindings, dict):
        raise ModelConfigurationError("role_bindings must be an object")
    return LLMProfileConfig(
        schema_version=str(merged.get("schema_version") or ""),
        default_profile=str(merged.get("default_profile") or ""),
        role_bindings={str(key): str(value) for key, value in role_bindings.items()},
        profiles={str(key): deepcopy(value) for key, value in profiles.items() if isinstance(value, dict)},
        sources=tuple(sources),
    )


def select_profile_name(
    config: LLMProfileConfig,
    *,
    role: str = "drafting",
    profile_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    if profile_name:
        return profile_name
    env = os.environ if environ is None else environ
    role_env = ROLE_ENV_VARS.get(role)
    if role_env and str(env.get(role_env) or "").strip():
        return str(env[role_env]).strip()
    if config.role_bindings.get(role):
        return config.role_bindings[role]
    if config.default_profile:
        return config.default_profile
    raise ModelConfigurationError(f"no LLM profile configured for role: {role}")


def resolve_profile(
    *,
    role: str = "drafting",
    profile_name: str | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    require_credentials: bool = True,
    load_dotenv_file: bool = True,
) -> LLMProfile:
    env = load_environment(environ, load_dotenv_files=load_dotenv_file)
    config = load_profile_config(config_path, environ=env, load_dotenv_file=False)
    selected = select_profile_name(config, role=role, profile_name=profile_name, environ=env)
    raw = config.profiles.get(selected)
    if raw is None:
        raise ModelConfigurationError(f"LLM profile not found: {selected}")
    profile = profile_from_mapping(selected, raw, env)
    if not profile.enabled:
        raise ModelConfigurationError(f"LLM profile is disabled: {selected}")
    if require_credentials and not profile.api_key:
        raise ModelConfigurationError(
            f"API key is not configured for profile {selected}; set environment variable {profile.api_key_env}"
        )
    if require_credentials and not profile.model:
        suffix = f"; set environment variable {profile.model_env}" if profile.model_env else ""
        raise ModelConfigurationError(f"model is not configured for profile {selected}{suffix}")
    return profile


def profile_from_mapping(name: str, raw: Mapping[str, Any], env: Mapping[str, str]) -> LLMProfile:
    provider = required_text(raw, "provider", name)
    protocol = str(raw.get("protocol") or "openai_compatible").strip()
    if protocol != "openai_compatible":
        raise ModelConfigurationError(f"unsupported protocol for profile {name}: {protocol}")
    # Prefer the legacy alias when both exist so existing local overrides keep working.
    api_mode = str(raw.get("api_mode") or raw.get("api_style") or "").strip()
    if api_mode not in API_MODES:
        raise ModelConfigurationError(
            f"unsupported api_mode/api_style for profile {name}: {api_mode}"
        )
    output_mode = str(raw.get("structured_output_mode") or "prompt_json").strip()
    if output_mode not in STRUCTURED_OUTPUT_MODES:
        raise ModelConfigurationError(f"unsupported structured_output_mode for profile {name}: {output_mode}")
    api_key_env = required_text(raw, "api_key_env", name)
    model_env = str(raw.get("model_env") or "").strip()
    base_url_env = str(raw.get("base_url_env") or "").strip()
    base_url = str(env.get(base_url_env) or "").strip() if base_url_env else ""
    base_url = base_url or str(raw.get("base_url") or "").strip() or PROVIDER_DEFAULT_BASE_URLS.get(provider, "")
    if not base_url:
        raise ModelConfigurationError(f"base_url is not configured for profile {name}")
    model = str(env.get(model_env) or "").strip() if model_env else ""
    model = model or str(raw.get("model") or "").strip()

    extra_headers = string_mapping(raw.get("extra_headers") or {}, f"profile {name} extra_headers")
    if any(key.lower() in SENSITIVE_HEADER_NAMES for key in extra_headers):
        raise ModelConfigurationError(f"profile {name} extra_headers must not contain authentication headers")
    extra_body = raw.get("extra_body") or {}
    if not isinstance(extra_body, dict):
        raise ModelConfigurationError(f"profile {name} extra_body must be an object")

    capabilities_raw = raw.get("capabilities") or {}
    if not isinstance(capabilities_raw, dict):
        raise ModelConfigurationError(f"profile {name} capabilities must be an object")
    capabilities = ModelCapabilities(
        **{
            field_name: bool(capabilities_raw.get(field_name, default))
            for field_name, default in asdict(ModelCapabilities()).items()
        }
    )
    mapping_raw = raw.get("request_mapping") or {}
    if not isinstance(mapping_raw, dict):
        raise ModelConfigurationError(f"profile {name} request_mapping must be an object")
    thinking = mapping_raw.get("thinking_configuration") or {}
    if not isinstance(thinking, dict):
        raise ModelConfigurationError(f"profile {name} thinking_configuration must be an object")
    default_token_parameter = "max_output_tokens" if api_mode == "responses" else "max_tokens"
    request_mapping = RequestMapping(
        system_role=str(mapping_raw.get("system_role") or "system"),
        token_limit_parameter=str(mapping_raw.get("token_limit_parameter") or default_token_parameter),
        finalization_strategy=str(mapping_raw.get("finalization_strategy") or "tool_choice_none"),
        thinking_enabled=bool(thinking.get("enabled", raw.get("send_thinking", False))),
        reasoning_effort=(
            str(thinking.get("reasoning_effort") or mapping_raw.get("reasoning_effort") or "").strip() or None
        ),
    )
    validate_capability_mapping(name, capabilities, request_mapping)

    capability_defaults = PROVIDER_CAPABILITY_DEFAULTS.get(provider, {})
    stream = bool(raw.get("stream", False))
    if stream and not capabilities.supports_streaming:
        raise ModelConfigurationError(f"profile {name} enables stream but does not support streaming")
    return LLMProfile(
        name=name,
        provider=provider,
        protocol=protocol,
        base_url=base_url,
        api_key_env=api_key_env,
        model_env=model_env,
        api_key=str(env.get(api_key_env) or "").strip(),
        model=model,
        api_mode=api_mode,
        structured_output_mode=output_mode,
        timeout_seconds=positive_float(raw.get("timeout_seconds", 120), "timeout_seconds", name),
        max_retries=nonnegative_int(raw.get("max_retries", 2), "max_retries", name),
        max_output_tokens=optional_positive_int(raw.get("max_output_tokens"), "max_output_tokens", name),
        temperature=optional_float(raw.get("temperature"), "temperature", name),
        enabled=bool(raw.get("enabled", True)),
        use_instructions=bool(raw.get("use_instructions", capability_defaults.get("use_instructions", False))),
        send_temperature=bool(raw.get("send_temperature", capability_defaults.get("send_temperature", False))),
        send_max_output_tokens=bool(
            raw.get("send_max_output_tokens", capability_defaults.get("send_max_output_tokens", True))
        ),
        send_thinking=bool(raw.get("send_thinking", capability_defaults.get("send_thinking", False))),
        send_metadata=bool(raw.get("send_metadata", capability_defaults.get("send_metadata", False))),
        extra_body=deepcopy(extra_body),
        extra_headers=extra_headers,
        stream=stream,
        capabilities=capabilities,
        request_mapping=request_mapping,
    )


def validate_capability_mapping(
    name: str,
    capabilities: ModelCapabilities,
    request_mapping: RequestMapping,
) -> None:
    if request_mapping.system_role not in SYSTEM_ROLES:
        raise ModelConfigurationError(f"profile {name} unsupported system_role: {request_mapping.system_role}")
    if request_mapping.system_role == "developer" and not capabilities.supports_developer_role:
        raise ModelConfigurationError(
            f"profile {name} uses developer system_role but does not support developer messages"
        )
    if request_mapping.token_limit_parameter not in TOKEN_LIMIT_PARAMETERS:
        raise ModelConfigurationError(
            f"profile {name} unsupported token_limit_parameter: {request_mapping.token_limit_parameter}"
        )
    if request_mapping.finalization_strategy not in FINALIZATION_STRATEGIES:
        raise ModelConfigurationError(
            f"profile {name} unsupported finalization_strategy: {request_mapping.finalization_strategy}"
        )
    if capabilities.requires_reasoning_content_replay and not capabilities.supports_reasoning_content:
        raise ModelConfigurationError(
            f"profile {name} requires reasoning_content replay but does not support reasoning_content"
        )
    if request_mapping.finalization_strategy == "tool_choice_none" and (
        not capabilities.supports_tool_choice or not capabilities.supports_tool_choice_none
    ):
        raise ModelConfigurationError(
            f"profile {name} finalization_strategy=tool_choice_none requires tool_choice none support"
        )


def list_profile_summaries(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    load_dotenv_file: bool = True,
) -> list[dict[str, Any]]:
    env = load_environment(environ, load_dotenv_files=load_dotenv_file)
    config = load_profile_config(config_path, environ=env, load_dotenv_file=False)
    roles_by_profile: dict[str, list[str]] = {}
    for role in set(ROLE_ENV_VARS) | set(config.role_bindings):
        selected = select_profile_name(config, role=role, environ=env)
        roles_by_profile.setdefault(selected, []).append(role)
    result = []
    for name, raw in config.profiles.items():
        profile = profile_from_mapping(name, raw, env)
        summary = profile.safe_summary()
        summary["roles"] = roles_by_profile.get(name, [])
        result.append(summary)
    return result


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelConfigurationError(f"invalid LLM profile config: {path}") from exc
    if not isinstance(value, dict):
        raise ModelConfigurationError(f"LLM profile config must be a JSON object: {path}")
    return value


def merge_profile_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key in ["schema_version", "default_profile"]:
        if key in override:
            result[key] = deepcopy(override[key])
    result.setdefault("role_bindings", {}).update(deepcopy(override.get("role_bindings") or {}))
    result.setdefault("profiles", {})
    for name, values in (override.get("profiles") or {}).items():
        if isinstance(values, dict):
            current = result["profiles"].setdefault(name, {})
            for key, value in values.items():
                if key in {"capabilities", "request_mapping"} and isinstance(value, dict):
                    current.setdefault(key, {}).update(deepcopy(value))
                else:
                    current[key] = deepcopy(value)
    return result


def required_text(raw: Mapping[str, Any], field_name: str, profile_name: str) -> str:
    value = str(raw.get(field_name) or "").strip()
    if not value:
        raise ModelConfigurationError(f"profile {profile_name} missing required field: {field_name}")
    return value


def string_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ModelConfigurationError(f"{label} must be an object")
    return {str(key): str(item) for key, item in value.items()}


def positive_float(value: Any, field_name: str, profile_name: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ModelConfigurationError(f"profile {profile_name} {field_name} must be positive")
    return parsed


def positive_int(value: Any, field_name: str, profile_name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ModelConfigurationError(f"profile {profile_name} {field_name} must be positive")
    return parsed


def optional_positive_int(value: Any, field_name: str, profile_name: str) -> int | None:
    if value is None or value == "":
        return None
    return positive_int(value, field_name, profile_name)


def optional_float(value: Any, field_name: str, profile_name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ModelConfigurationError(f"profile {profile_name} {field_name} must be numeric") from exc


def nonnegative_int(value: Any, field_name: str, profile_name: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ModelConfigurationError(f"profile {profile_name} {field_name} must be nonnegative")
    return parsed

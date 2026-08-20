from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.llm_profiles import load_environment


class WebSearchProvider(Protocol):
    name: str

    def search(self, query: str, options: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class ArkBuiltinWebSearchProvider:
    """Adapter for the Web Search tool hosted by Ark Responses API."""

    llm_client: Any
    name: str = "ark_builtin_web_search"

    def search(self, query: str, options: dict[str, Any]) -> dict[str, Any]:
        method = getattr(self.llm_client, "run_builtin_web_search", None)
        if not callable(method):
            return {
                "status": "not_configured",
                "provider": self.name,
                "reason": "llm_client_does_not_support_builtin_web_search",
                "results": [],
            }
        try:
            return method(
                query=query,
                options=options,
                request_metadata={"purpose": str(options.get("purpose") or "")[:100]},
            )
        except Exception as exc:
            reason = classify_unavailable_reason(exc)
            return {
                "status": "not_configured" if reason != "request_failed" else "error",
                "provider": self.name,
                "reason": reason,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "results": [],
            }


class NotConfiguredSearchProvider:
    name = "not_configured"

    def __init__(self, reason: str = "ark_builtin_web_search_not_configured") -> None:
        self.reason = reason

    def search(self, query: str, options: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "not_configured",
            "provider": self.name,
            "reason": self.reason,
            "results": [],
        }


def get_web_search_provider(
    name: str | None = None,
    *,
    llm_client: Any = None,
    independent_llm_client: Any = None,
) -> WebSearchProvider:
    env = load_environment()
    selected = (name or env.get("COMLAN_WEB_SEARCH_PROFILE") or "ark_builtin").strip().lower()
    if selected in {"none", "disabled"}:
        return NotConfiguredSearchProvider("web_search_disabled")
    if selected not in {"ark", "ark_builtin", "volcengine_ark"}:
        return NotConfiguredSearchProvider(
            "third_party_search_providers_are_not_enabled; use ark_builtin"
        )
    selected_client = independent_llm_client or llm_client
    profile = getattr(selected_client, "profile", None)
    is_ark_responses = profile is not None and (
        getattr(profile, "provider", "") == "volcengine_ark"
        and getattr(profile, "api_mode", "") == "responses"
    )
    if not is_ark_responses and profile is not None and not getattr(selected_client, "is_mock", False):
        try:
            from app.llm_client import get_llm_client

            selected_client = independent_llm_client or get_llm_client(role="web_search")
            profile = getattr(selected_client, "profile", None)
            is_ark_responses = profile is not None and (
                getattr(profile, "provider", "") == "volcengine_ark"
                and getattr(profile, "api_mode", "") == "responses"
            )
        except Exception:
            return NotConfiguredSearchProvider("ark_builtin_web_search_profile_not_configured")
    if selected_client is None:
        return NotConfiguredSearchProvider("llm_client_not_available")
    if not is_ark_responses:
        return NotConfiguredSearchProvider("requires_volcengine_ark_responses_profile")
    if not callable(getattr(selected_client, "run_builtin_web_search", None)):
        return NotConfiguredSearchProvider("llm_client_does_not_support_builtin_web_search")
    return ArkBuiltinWebSearchProvider(selected_client)


def classify_unavailable_reason(exc: Exception) -> str:
    diagnostics = getattr(exc, "safe_diagnostics", lambda: {})()
    text = " ".join(
        [
            str(exc),
            str(diagnostics.get("provider_error_code") or ""),
            str(diagnostics.get("provider_error_message") or ""),
            str(diagnostics.get("status_code") or ""),
        ]
    ).lower()
    if "toolnotopen" in text or "has not activated web search" in text:
        return "account_web_search_not_activated"
    if any(term in text for term in ("not supported", "unsupported", "hosted tool")):
        return "model_does_not_support_builtin_web_search"
    if any(term in text for term in ("permission", "forbidden", "403", "quota", "not enabled")):
        return "account_permission_or_service_not_enabled"
    if any(term in text for term in ("authentication", "unauthorized", "401", "api key")):
        return "account_authentication_failed"
    if any(term in text for term in ("configuration", "not configured", "requires_volcengine")):
        return "provider_configuration_missing"
    return "request_failed"

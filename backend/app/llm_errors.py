from __future__ import annotations

from typing import Any


class ModelError(Exception):
    """Base class for model-layer failures with secret-safe messages."""

    retryable = False

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}

    def safe_diagnostics(self) -> dict[str, Any]:
        return dict(self.diagnostics)


class ModelConfigurationError(ModelError):
    pass


class ModelAuthenticationError(ModelError):
    pass


class ModelPermissionError(ModelError):
    pass


class ModelNotFoundError(ModelError):
    pass


class ModelRateLimitError(ModelError):
    retryable = True


class ModelTimeoutError(ModelError):
    retryable = True


class ModelConnectionError(ModelError):
    retryable = True


class ModelResponseError(ModelError):
    pass


class ModelOutputIncompleteError(ModelResponseError):
    # A provider response/stream that lacks a usable terminal output is a
    # transient execution failure, not a semantic chapter-review finding.
    retryable = True


class ModelJsonParseError(ModelResponseError):
    def __init__(
        self,
        message: str,
        *,
        diagnostics: dict[str, Any] | None = None,
        raw_content: str | None = None,
    ) -> None:
        super().__init__(message, diagnostics=diagnostics)
        # Raw model output is intentionally excluded from safe_diagnostics and
        # exception text. The Agent may redact and persist it in its own output.
        self.raw_content = raw_content


class ModelSchemaValidationError(ModelError):
    pass


class ModelSafetyRefusalError(ModelError):
    pass

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TextIO


EventHandler = Callable[[dict[str, Any]], None]
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "authorization_header",
    "headers",
    "messages",
    "prompt",
    "system_prompt",
}
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


class TechnicalAgentEventEmitter:
    """Publish a small, auditable event stream and optionally persist JSONL."""

    def __init__(
        self,
        *,
        max_steps: int,
        event_handler: EventHandler | None = None,
        event_log_path: str | Path | None = None,
        run_id: str | None = None,
    ) -> None:
        self.max_steps = int(max_steps)
        self.event_handler = event_handler
        self.run_id = run_id or f"TA-{uuid.uuid4().hex}"
        self.sequence = 0
        self.event_log_path = Path(event_log_path).expanduser().resolve() if event_log_path else None
        self._stream: TextIO | None = None
        if self.event_log_path:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self.event_log_path.open("w", encoding="utf-8", newline="\n")

    def emit(
        self,
        event_type: str,
        *,
        step: int = 0,
        phase: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "schema_version": "technical_agent_event_v1",
            "run_id": self.run_id,
            "event_id": f"EVT-{self.sequence:06d}",
            "sequence": self.sequence,
            "timestamp": datetime.now().astimezone().isoformat(),
            "event_type": str(event_type),
            "step": int(step),
            "max_steps": self.max_steps,
            "phase": str(phase),
            "data": sanitize_event_data(data or {}),
        }
        if self._stream:
            self._stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._stream.flush()
        if self.event_handler:
            self.event_handler(event)
        return event

    def close(self) -> None:
        if self._stream:
            self._stream.flush()
            self._stream.close()
            self._stream = None


def sanitize_event_data(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SENSITIVE_KEYS or normalized.endswith("_api_key"):
                continue
            result[str(key)] = sanitize_event_data(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [sanitize_event_data(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _BEARER_PATTERN.sub("Bearer [REDACTED]", value)[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


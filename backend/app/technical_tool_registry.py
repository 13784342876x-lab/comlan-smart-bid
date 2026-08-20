from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


class ToolExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def openai_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
                "strict": True,
            },
        }


class TechnicalToolRegistry:
    """Allow-list registry. It never exposes Python, shell or arbitrary file access."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        Draft202012Validator.check_schema(tool.parameters)
        self._tools[tool.name] = tool

    def definitions(self, names: set[str] | None = None) -> list[dict[str, Any]]:
        return [
            tool.openai_definition()
            for name, tool in self._tools.items()
            if names is None or name in names
        ]

    def names(self) -> list[str]:
        return list(self._tools)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolExecutionError(f"tool is not registered: {name}")
        if not isinstance(arguments, dict):
            raise ToolExecutionError("tool arguments must be an object")
        try:
            Draft202012Validator(tool.parameters).validate(arguments)
        except ValidationError as exc:
            path = ".".join(str(item) for item in exc.absolute_path) or "$"
            raise ToolExecutionError(f"invalid arguments at {path}: {exc.message}") from exc
        result = tool.handler(arguments)
        if not isinstance(result, dict):
            raise ToolExecutionError(f"tool {name} returned a non-object result")
        return result


def object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or [],
    }

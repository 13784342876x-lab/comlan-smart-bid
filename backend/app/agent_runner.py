from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.llm_client import FORBIDDEN_DECISION_TERMS, get_llm_client


def load_agent_config(agent_name: str, agents_dir: str = "agents") -> dict[str, Any]:
    agent_path = Path(agents_dir) / agent_name
    result: dict[str, Any] = {
        "agent_name": agent_name,
        "agent_path": str(agent_path),
        "exists": agent_path.exists() and agent_path.is_dir(),
        "system_prompt": "",
        "output_schema": None,
        "has_system_prompt": False,
        "has_output_schema": False,
        "errors": [],
        "notes": [],
    }
    if not result["exists"]:
        result["errors"].append(f"agent not found: {agent_name}")
        return result

    system_path = agent_path / "system.txt"
    if system_path.exists():
        result["system_prompt"] = system_path.read_text(encoding="utf-8").strip()
        result["has_system_prompt"] = bool(result["system_prompt"])
    else:
        result["errors"].append(f"system.txt missing for agent: {agent_name}")

    schema_path = agent_path / "output_schema.json"
    if schema_path.exists():
        try:
            result["output_schema"] = json.loads(schema_path.read_text(encoding="utf-8") or "{}")
            result["has_output_schema"] = True
        except json.JSONDecodeError as exc:
            result["errors"].append(f"output_schema.json invalid: {exc}")
    else:
        result["notes"].append("schema_missing")

    return result


def run_agent(
    agent_name: str,
    input_data: dict[str, Any],
    provider: str | None = None,
    agents_dir: str = "agents",
    model_profile: str | None = None,
) -> dict[str, Any]:
    config = load_agent_config(agent_name, agents_dir=agents_dir)
    input_summary = build_input_summary(input_data)
    if not config.get("exists") or not config.get("has_system_prompt"):
        return {
            "agent_name": agent_name,
            "provider": provider,
            "status": "failed",
            "input_summary": input_summary,
            "result": {},
            "errors": list(config.get("errors") or []),
            "notes": list(config.get("notes") or []),
        }

    result = {}
    raw_output = ""
    model_call = {}
    try:
        if provider == "mock":
            client = get_llm_client(provider)
            result = client.generate_json(
                agent_name=agent_name,
                system_prompt=str(config.get("system_prompt") or ""),
                input_data=input_data,
                output_schema=config.get("output_schema"),
            )
        else:
            client = get_llm_client(provider, role="drafting", profile_name=model_profile)
            call = client.generate_structured(
                system_prompt=str(config.get("system_prompt") or ""),
                user_prompt=json.dumps({"input": input_data, "output_schema": config.get("output_schema")}, ensure_ascii=False),
                response_schema={"type": "object"},
                request_metadata={"agent_name": agent_name},
            )
            result = call.parsed_output
            model_call = call.as_dict(include_parsed_output=False)
        errors = validate_result(result, config.get("output_schema"))
        status = "failed" if errors else "success"
    except Exception as exc:
        raw_output = getattr(exc, "raw_content", None) or ""
        errors = [str(exc)]
        status = "failed"

    output = {
        "agent_name": agent_name,
        "provider": model_call.get("provider") or provider or "configured_profile",
        "is_mock": provider == "mock",
        "model_call": model_call,
        "raw_output": raw_output,
        "status": status,
        "input_summary": input_summary,
        "result": result,
        "errors": errors,
        "notes": list(config.get("notes") or []),
    }
    if contains_forbidden_language(output):
        output["status"] = "failed"
        output.setdefault("errors", []).append("output contains forbidden bid decision language")
    return output


def validate_result(result: dict[str, Any], output_schema: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["agent result is not a JSON object"]
    if not isinstance(output_schema, dict):
        return errors
    required = output_schema.get("required")
    if isinstance(required, list):
        for field in required:
            if field not in result:
                errors.append(f"missing required output field: {field}")
    properties = output_schema.get("properties")
    if isinstance(properties, dict):
        for field, schema in properties.items():
            if field not in result or not isinstance(schema, dict):
                continue
            expected = schema.get("type")
            if expected and not value_matches_type(result[field], str(expected)):
                errors.append(f"output field type mismatch: {field}")
    return errors


def value_matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "number":
        return isinstance(value, (int, float))
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def build_input_summary(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": input_data.get("task_id"),
        "task_name": input_data.get("task_name"),
        "section_type": input_data.get("section_type"),
        "evidence_count": len(list(input_data.get("evidence_refs") or [])),
        "missing_count": len(list(input_data.get("missing_materials") or [])),
        "outline_count": len(list(input_data.get("outline") or [])),
    }


def contains_forbidden_language(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    return any(term.lower() in text for term in FORBIDDEN_DECISION_TERMS)

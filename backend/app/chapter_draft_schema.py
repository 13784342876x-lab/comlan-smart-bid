from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.llm_errors import ModelSchemaValidationError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CHAPTER_DRAFT_SCHEMA_PATH = BACKEND_ROOT / "agents" / "technical_writer" / "chapter_draft_output_schema.json"


def load_chapter_draft_schema(path: str | Path | None = None) -> dict[str, Any]:
    schema_path = Path(path) if path else CHAPTER_DRAFT_SCHEMA_PATH
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelSchemaValidationError(f"invalid chapter draft schema: {schema_path}") from exc
    if not isinstance(schema, dict):
        raise ModelSchemaValidationError("chapter draft schema must be a JSON object")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ModelSchemaValidationError(f"invalid chapter draft schema: {exc.message}") from exc
    return deepcopy(schema)

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MISMATCH_MESSAGE = "当前DOCX不是由当前Markdown生成"


def docx_source_record_path(output_docx_path: str | Path) -> Path:
    output = Path(output_docx_path).expanduser().resolve()
    return output.with_suffix(output.suffix + ".source.json")


def record_docx_source(
    source_markdown_path: str | Path,
    output_docx_path: str | Path,
    *,
    converter: str,
    reference_doc: str | Path | None = None,
    record_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    markdown = Path(source_markdown_path).expanduser().resolve()
    output = Path(output_docx_path).expanduser().resolve()
    if not markdown.is_file():
        raise FileNotFoundError(f"source Markdown not found: {markdown}")
    if not output.is_file():
        raise FileNotFoundError(f"output DOCX not found: {output}")
    record = {
        "source_markdown_path": str(markdown),
        "source_markdown_sha256": _sha256(markdown),
        "output_docx_path": str(output),
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "converter": str(converter),
        "reference_doc": (
            str(Path(reference_doc).expanduser().resolve()) if reference_doc else None
        ),
    }
    destination = (
        Path(record_path).expanduser().resolve()
        if record_path
        else docx_source_record_path(output)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**record, "record_path": str(destination)}


def verify_docx_source(
    source_markdown_path: str | Path,
    output_docx_path: str | Path,
    *,
    record_path: str | Path | None = None,
) -> dict[str, Any]:
    markdown = Path(source_markdown_path).expanduser().resolve()
    output = Path(output_docx_path).expanduser().resolve()
    destination = (
        Path(record_path).expanduser().resolve()
        if record_path
        else docx_source_record_path(output)
    )
    if not markdown.is_file():
        raise FileNotFoundError(f"source Markdown not found: {markdown}")
    if not destination.is_file():
        return {
            "matches": False,
            "message": MISMATCH_MESSAGE,
            "reason": "source_record_missing",
            "record_path": str(destination),
        }
    try:
        record = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "matches": False,
            "message": MISMATCH_MESSAGE,
            "reason": f"source_record_invalid:{type(exc).__name__}",
            "record_path": str(destination),
        }
    current_sha = _sha256(markdown)
    recorded_sha = str(record.get("source_markdown_sha256") or "")
    recorded_output = str(record.get("output_docx_path") or "")
    matches = (
        current_sha == recorded_sha
        and Path(recorded_output).expanduser().resolve() == output
        and output.is_file()
    )
    return {
        "matches": matches,
        "message": "当前DOCX与当前Markdown来源一致" if matches else MISMATCH_MESSAGE,
        "reason": "matched" if matches else "markdown_sha256_or_output_mismatch",
        "source_markdown_sha256": current_sha,
        "recorded_source_markdown_sha256": recorded_sha,
        "record_path": str(destination),
        "output_docx_path": str(output),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

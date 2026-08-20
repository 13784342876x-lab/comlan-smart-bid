from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RULE_ID_PATTERN = re.compile(r"^##\s+(TW-\d{3})\s+(.+?)\s*$", re.MULTILINE)
RULE_VERSION_PATTERN = re.compile(r"<!--\s*version:\s*([^\s>]+)\s*-->")
GLOBAL_CONSTRAINT_MARKER = "## 用户确认的全局补充约束"


@dataclass(frozen=True)
class WritingRules:
    path: Path
    version: str
    sha256: str
    content: str
    rules: tuple[dict[str, str], ...]

    def report(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.version,
            "sha256": self.sha256,
            "loaded": True,
            "rule_ids": [item["rule_id"] for item in self.rules],
        }


def default_writing_rules_path() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "writing" / "technical_writing_rules.md"


def load_writing_rules(path: str | Path | None = None) -> WritingRules:
    resolved = Path(path or default_writing_rules_path()).expanduser().resolve()
    content = resolved.read_text(encoding="utf-8-sig")
    matches = list(RULE_ID_PATTERN.finditer(content))
    if not matches:
        raise ValueError(f"writing rules contain no stable rule IDs: {resolved}")
    rules: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        rules.append(
            {
                "rule_id": match.group(1),
                "title": match.group(2).strip(),
                "content": content[match.start():end].strip(),
            }
        )
    version_match = RULE_VERSION_PATTERN.search(content)
    version = version_match.group(1) if version_match else "unversioned"
    return WritingRules(
        path=resolved,
        version=version,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content=content,
        rules=tuple(rules),
    )


def suggest_instruction_scope(instruction: str) -> tuple[str, str]:
    value = re.sub(r"\s+", " ", str(instruction or "")).strip()
    if not value:
        return "once", "本轮没有用户意见。"
    if any(term in value for term in ("所有项目", "以后每次", "长期规则", "全局规则", "所有技术标")):
        return "global", "意见表述为跨项目、长期适用的写作偏好，但仍需用户确认后才能持久化。"
    if any(term in value for term in ("本项目", "当前项目", "本次投标", "本标段", "后续章节")):
        return "project", "意见可能适用于当前项目后续章节，但仍需用户确认后才能持久化。"
    return "once", "意见缺少明确的长期适用范围，按最小影响原则仅用于本轮。"


def normalize_persistent_constraint(instruction: str, *, scope: str) -> dict[str, Any]:
    if scope not in {"project", "global"}:
        raise ValueError("persistent constraint scope must be project or global")
    raw = re.sub(r"\s+", " ", str(instruction or "")).strip()
    normalized = raw
    normalized = re.sub(r"^(请|麻烦|帮我|我希望|我要求|建议|以后|从今以后)[，,:：\s]*", "", normalized)
    normalized = re.sub(r"(谢谢|辛苦了)[。！!]*$", "", normalized).strip(" ，,。；;")
    if normalized and normalized[-1] not in "。！？":
        normalized += "。"
    blockers: list[str] = []
    if re.search(r"\b(?:TR|WC|SCORE)-[0-9A-Z-]+\b", normalized, flags=re.IGNORECASE):
        blockers.append("contains_internal_project_identifier")
    if re.search(r"(?:项目名称|招标文件名称|人员姓名)\s*[:：]", normalized):
        blockers.append("contains_explicit_project_or_person_fact")
    if scope == "global" and re.search(
        r"[\u4e00-\u9fffA-Za-z0-9]{3,}(?:医院|学校|公司|采购项目|建设项目)",
        normalized,
    ):
        blockers.append("contains_project_specific_name")
    if len(normalized) > 600:
        blockers.append("constraint_too_long")
    conflict = bool(
        re.search(r"(忽略|绕过|删除).{0,16}(招标|评分项|响应点|校验|占位符)", normalized)
        or re.search(r"(允许|直接).{0,12}(编造|猜测|候选材料作为事实)", normalized)
    )
    if conflict:
        blockers.append("conflicts_with_core_writing_rules")
    return {
        "scope": scope,
        "normalized_text": normalized[:600],
        "valid": bool(normalized) and not blockers,
        "blockers": blockers,
        "raw_text_persisted": False,
    }


def load_project_constraints(output_root: str | Path, output_key: str) -> dict[str, Any]:
    path = (
        Path(output_root).expanduser().resolve()
        / output_key
        / "writing"
        / "project_writing_constraints.json"
    )
    if not path.is_file():
        return {
            "path": str(path),
            "loaded": False,
            "constraints": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    constraints = [
        item
        for item in payload.get("constraints") or []
        if isinstance(item, dict) and str(item.get("normalized_text") or "").strip()
    ]
    return {
        "path": str(path),
        "loaded": True,
        "constraints": constraints,
    }


def persist_confirmed_constraint(
    *,
    instruction: str,
    scope: str,
    output_root: str | Path,
    output_key: str,
    rules_path: str | Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_persistent_constraint(instruction, scope=scope)
    if not normalized["valid"]:
        return {"status": "rejected", **normalized}
    text = normalized["normalized_text"]
    constraint_id = "UC-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12].upper()
    now = datetime.now(timezone.utc).astimezone().isoformat()
    if scope == "project":
        path = (
            Path(output_root).expanduser().resolve()
            / output_key
            / "writing"
            / "project_writing_constraints.json"
        )
        payload: dict[str, Any]
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            payload = {
                "schema_version": "project_writing_constraints_v1",
                "output_key": output_key,
                "constraints": [],
            }
        constraints = list(payload.get("constraints") or [])
        if any(str(item.get("normalized_text") or "") == text for item in constraints if isinstance(item, dict)):
            return {"status": "duplicate", "scope": scope, "path": str(path), "constraint_id": constraint_id}
        constraints.append(
            {
                "constraint_id": constraint_id,
                "normalized_text": text,
                "confirmed_by": "user_explicit_scope",
                "created_at": now,
            }
        )
        payload["constraints"] = constraints
        _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"status": "saved", "scope": scope, "path": str(path), "constraint_id": constraint_id}

    path = Path(rules_path or default_writing_rules_path()).expanduser().resolve()
    content = path.read_text(encoding="utf-8-sig")
    line = f"- [{constraint_id}] {text}"
    if line in content:
        return {"status": "duplicate", "scope": scope, "path": str(path), "constraint_id": constraint_id}
    if GLOBAL_CONSTRAINT_MARKER not in content:
        content = content.rstrip() + f"\n\n{GLOBAL_CONSTRAINT_MARKER}\n\n"
    content = content.rstrip() + "\n" + line + "\n"
    _atomic_write(path, content)
    return {"status": "saved", "scope": scope, "path": str(path), "constraint_id": constraint_id}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)

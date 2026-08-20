from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


NORMALIZED_SCHEMA_VERSION = "company_writing_outline_normalized_v0"


def load_company_outline(path: str | Path) -> dict[str, Any]:
    """Load a user-maintained company outline without modifying it."""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("company outline root must be a JSON object")
    return data


def stable_node_id(path: Iterable[str]) -> str:
    normalized = " / ".join(str(part).strip() for part in path)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()
    return f"CON-{digest}"


def normalize_company_outline(outline: dict[str, Any]) -> dict[str, Any]:
    """Normalize module, recursive-tree, parent_id, or level based outlines."""
    if not isinstance(outline, dict):
        raise ValueError("company outline must be an object")

    source = copy.deepcopy(outline)
    generated_ids: list[str] = []
    profiles = list(source.get("project_type_profiles") or [])
    applicable_by_module: dict[str, list[str]] = {}
    for profile in profiles:
        project_type = str(profile.get("project_type") or "").strip()
        for module_id in profile.get("default_module_ids") or []:
            applicable_by_module.setdefault(str(module_id), []).append(project_type)

    if isinstance(source.get("modules"), list):
        chapters = []
        for index, module in enumerate(source["modules"], start=1):
            if not isinstance(module, dict):
                module = {"default_title": str(module)}
            title = str(module.get("default_title") or module.get("title") or "").strip()
            path = [title or f"untitled-{index}"]
            supplied_id = module.get("node_id") or module.get("module_id")
            node_id = str(supplied_id or stable_node_id(path))
            if not supplied_id:
                generated_ids.append(node_id)
            raw_children = module.get("children")
            if not isinstance(raw_children, list):
                raw_children = module.get("default_sections") or []
            children = _normalize_recursive_nodes(raw_children, path, generated_ids)
            chapters.append(
                {
                    "node_id": node_id,
                    "module_id": str(module.get("module_id") or node_id),
                    "title": title,
                    "required_by_default": bool(module.get("required_by_default", False)),
                    "chapter_type": str(module.get("chapter_type") or "narrative"),
                    "placement_aliases": list(module.get("placement_aliases") or []),
                    "trigger_terms": list(module.get("trigger_terms") or []),
                    "evidence_types": list(module.get("evidence_types") or []),
                    "web_policy": str(module.get("web_policy") or "forbidden"),
                    "split_policy": copy.deepcopy(module.get("split_policy")),
                    "applicable_project_types": applicable_by_module.get(str(module.get("module_id") or ""), []),
                    "children": children,
                }
            )
        source_kind = "module_list"
    else:
        raw_nodes = source.get("chapters")
        if not isinstance(raw_nodes, list):
            raw_nodes = source.get("nodes") or []
        if _looks_flat(raw_nodes):
            chapters = _normalize_flat_nodes(raw_nodes, generated_ids)
            source_kind = "flat_node_list"
        else:
            chapters = _normalize_recursive_nodes(raw_nodes, [], generated_ids)
            source_kind = "recursive_tree"

    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "source_schema_version": str(source.get("schema_version") or ""),
        "outline_id": str(source.get("outline_id") or ""),
        "name": str(source.get("name") or ""),
        "scope": str(source.get("scope") or ""),
        "status": str(source.get("status") or ""),
        "adaptation_policy": copy.deepcopy(source.get("adaptation_policy") or {}),
        "project_type_profiles": profiles,
        "chapters": chapters,
        "normalization": {
            "source_kind": source_kind,
            "generated_node_ids": generated_ids,
            "source_was_modified": False,
        },
    }


def validate_company_outline(outline: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(outline, dict):
        return {
            "valid": False,
            "errors": ["company outline must be an object"],
            "warnings": [],
            "chapter_count": 0,
            "node_count": 0,
            "max_depth": 0,
        }

    raw_nodes = outline.get("nodes") if isinstance(outline.get("nodes"), list) else []
    supplied_ids = {
        str(node.get("node_id") or node.get("module_id"))
        for node in raw_nodes
        if isinstance(node, dict) and (node.get("node_id") or node.get("module_id"))
    }
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        parent_id = str(node.get("parent_id") or "").strip()
        if parent_id and parent_id not in supplied_ids:
            errors.append(f"orphan parent_id: {parent_id}")

    try:
        normalized = normalize_company_outline(outline)
    except (TypeError, ValueError) as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "chapter_count": 0,
            "node_count": 0,
            "max_depth": 0,
        }

    seen: set[str] = set()
    node_count = 0
    max_depth = 0
    for node, depth in iter_outline_nodes(normalized.get("chapters") or []):
        node_count += 1
        max_depth = max(max_depth, depth)
        node_id = str(node.get("node_id") or "").strip()
        title = str(node.get("title") or "").strip()
        if not node_id:
            errors.append(f"missing node_id at depth {depth}")
        elif node_id in seen:
            errors.append(f"duplicate node_id: {node_id}")
        seen.add(node_id)
        if not title:
            errors.append(f"empty title: {node_id or '<missing-id>'}")

    generated = normalized.get("normalization", {}).get("generated_node_ids") or []
    if generated:
        warnings.append(f"generated {len(generated)} stable node_id values from full paths")
    return {
        "valid": not errors,
        "errors": _unique(errors),
        "warnings": _unique(warnings),
        "chapter_count": len(normalized.get("chapters") or []),
        "node_count": node_count,
        "max_depth": max_depth,
    }


def iter_outline_nodes(nodes: list[dict[str, Any]], depth: int = 1):
    for node in nodes:
        yield node, depth
        yield from iter_outline_nodes(list(node.get("children") or []), depth + 1)


def _normalize_recursive_nodes(
    raw_nodes: list[Any],
    parent_path: list[str],
    generated_ids: list[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    title_occurrences: dict[str, int] = {}
    for index, raw in enumerate(raw_nodes, start=1):
        node = raw if isinstance(raw, dict) else {"title": str(raw)}
        title = str(node.get("title") or node.get("default_title") or node.get("name") or "").strip()
        title_occurrences[title] = title_occurrences.get(title, 0) + 1
        path_part = title or f"untitled-{index}"
        if title_occurrences[title] > 1:
            path_part = f"{path_part}#{title_occurrences[title]}"
        path = [*parent_path, path_part]
        supplied_id = node.get("node_id") or node.get("module_id") or node.get("id")
        node_id = str(supplied_id or stable_node_id(path))
        if not supplied_id:
            generated_ids.append(node_id)
        result.append(
            {
                "node_id": node_id,
                "module_id": str(node.get("module_id") or ""),
                "title": title,
                "required_by_default": bool(node.get("required_by_default", False)),
                "chapter_type": str(node.get("chapter_type") or "narrative"),
                "placement_aliases": list(node.get("placement_aliases") or []),
                "trigger_terms": list(node.get("trigger_terms") or []),
                "evidence_types": list(node.get("evidence_types") or []),
                "web_policy": str(node.get("web_policy") or "forbidden"),
                "split_policy": copy.deepcopy(node.get("split_policy")),
                "applicable_project_types": list(node.get("applicable_project_types") or []),
                "children": _normalize_recursive_nodes(list(node.get("children") or []), path, generated_ids),
            }
        )
    return result


def _normalize_flat_nodes(raw_nodes: list[Any], generated_ids: list[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    pending: list[tuple[dict[str, Any], str]] = []

    for index, raw in enumerate(raw_nodes, start=1):
        item = raw if isinstance(raw, dict) else {"title": str(raw)}
        title = str(item.get("title") or item.get("default_title") or item.get("name") or "").strip()
        parent_id = str(item.get("parent_id") or "").strip()
        level_value = item.get("level")
        try:
            level = max(1, int(level_value)) if level_value is not None else 1
        except (TypeError, ValueError):
            level = 1
        parent_path = [str(node.get("title") or "") for node in stack[: level - 1]]
        supplied_id = item.get("node_id") or item.get("module_id") or item.get("id")
        node_id = str(supplied_id or stable_node_id([*parent_path, title or f"untitled-{index}"]))
        if not supplied_id:
            generated_ids.append(node_id)
        node = {
            "node_id": node_id,
            "module_id": str(item.get("module_id") or ""),
            "title": title,
            "required_by_default": bool(item.get("required_by_default", False)),
            "chapter_type": str(item.get("chapter_type") or "narrative"),
            "placement_aliases": list(item.get("placement_aliases") or []),
            "trigger_terms": list(item.get("trigger_terms") or []),
            "evidence_types": list(item.get("evidence_types") or []),
            "web_policy": str(item.get("web_policy") or "forbidden"),
            "split_policy": copy.deepcopy(item.get("split_policy")),
            "applicable_project_types": list(item.get("applicable_project_types") or []),
            "children": [],
        }
        by_id[node_id] = node
        if parent_id:
            pending.append((node, parent_id))
        else:
            while len(stack) >= level:
                stack.pop()
            if level > 1 and stack:
                stack[-1]["children"].append(node)
            else:
                normalized.append(node)
            stack.append(node)

    for node, parent_id in pending:
        parent = by_id.get(parent_id)
        if parent is not None:
            parent["children"].append(node)
        else:
            normalized.append(node)
    return normalized


def _looks_flat(nodes: list[Any]) -> bool:
    return any(
        isinstance(node, dict) and ("parent_id" in node or "level" in node)
        for node in nodes
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))

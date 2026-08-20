from __future__ import annotations

import re
from typing import Any


CN_NUMBER = "一二三四五六七八九十百零〇"
SEMANTIC_HEADINGS = {
    "技术要求",
    "项目需求书",
    "评审因素及评标标准",
    "投标文件内容要求",
    "投标文件格式",
    "技术要求点对点应答表",
    "商务要求点对点应答表",
}


def normalize_document_structure(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover a conservative heading tree without changing source text."""
    candidates = [_heading_candidate(block) for block in blocks]
    numbered_sequences = _numbered_sequence_indexes(candidates)
    numbered_parent_headings = _numbered_parent_heading_indexes(candidates)
    stack: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []

    for index, (block, candidate) in enumerate(zip(blocks, candidates)):
        item = dict(block)
        is_heading, level, source, confidence = _resolve_heading(
            block,
            candidate,
            index,
            numbered_sequences,
            numbered_parent_headings,
            stack,
        )
        if is_heading:
            while stack and int(stack[-1]["heading_level"]) >= level:
                stack.pop()
            parent_path = [node["text"] for node in stack]
            node = {
                "text": str(block.get("text") or "").strip(),
                "heading_level": level,
                "heading_source": source,
                "heading_confidence": confidence,
                "original_style": str(block.get("original_style") or ""),
                "parent_path": parent_path,
            }
            stack.append(node)
            item.update(node)
            item["is_heading"] = True
            item["node_type"] = "heading"
            item["heading_path"] = parent_path + [node["text"]]
        else:
            family = candidate.get("family") or ""
            if block.get("block_type") == "table":
                node_type = "table"
            elif family in {"decimal", "arabic"}:
                node_type = "clause"
            elif family == "cn_paren":
                node_type = "list_item"
            else:
                node_type = "paragraph"
            item.update(
                {
                    "is_heading": False,
                    "node_type": node_type,
                    "heading_level": None,
                    "heading_source": "none",
                    "heading_confidence": confidence,
                    "parent_path": [node["text"] for node in stack],
                    "heading_path": [node["text"] for node in stack],
                }
            )
        normalized.append(item)
    return normalized


def _heading_candidate(block: dict[str, Any]) -> dict[str, Any]:
    if block.get("block_type") != "paragraph":
        return {"family": "", "number": None, "text": ""}
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    patterns = [
        ("part", rf"^第([{CN_NUMBER}\d]+)部分\s*"),
        ("chapter", rf"^第([{CN_NUMBER}\d]+)章\s*"),
        ("cn_top", rf"^([{CN_NUMBER}]+)、\s*"),
        ("cn_paren", rf"^[（(]([{CN_NUMBER}]+)[）)]\s*"),
        ("decimal", r"^(\d+(?:\.\d+)+)[.、]?\s*"),
        ("arabic", r"^(\d+)[.、]\s*"),
        ("attachment", r"^附件\s*(\d+(?:-\d+)*)\s*$"),
    ]
    for family, pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return {"family": family, "number": match.group(1), "text": text}
    return {"family": "", "number": None, "text": text}


def _numbered_sequence_indexes(candidates: list[dict[str, Any]]) -> set[int]:
    result: set[int] = set()
    for index, item in enumerate(candidates):
        family = item.get("family")
        if family not in {"cn_top", "cn_paren", "arabic"}:
            continue
        value = _number_value(item.get("number"))
        if value is None:
            continue
        for other_index in range(max(0, index - 12), min(len(candidates), index + 13)):
            if other_index == index or candidates[other_index].get("family") != family:
                continue
            other_value = _number_value(candidates[other_index].get("number"))
            if other_value is not None and abs(other_value - value) == 1:
                result.add(index)
                result.add(other_index)
    return result


def _numbered_parent_heading_indexes(candidates: list[dict[str, Any]]) -> set[int]:
    result: set[int] = set()
    for index, item in enumerate(candidates):
        if item.get("family") != "arabic":
            continue
        number = str(item.get("number") or "")
        for next_item in candidates[index + 1:index + 4]:
            if not next_item.get("text"):
                continue
            if next_item.get("family") == "decimal" and str(next_item.get("number") or "").startswith(f"{number}."):
                result.add(index)
            break
    return result


def _resolve_heading(
    block: dict[str, Any],
    candidate: dict[str, Any],
    index: int,
    numbered_sequences: set[int],
    numbered_parent_headings: set[int],
    stack: list[dict[str, Any]],
) -> tuple[bool, int, str, float]:
    if block.get("block_type") != "paragraph":
        return False, 0, "none", 0.0
    text = candidate["text"]
    if not text:
        return False, 0, "none", 0.0
    style = str(block.get("original_style") or "").lower()
    outline = block.get("outline_level")
    family = candidate.get("family") or ""
    short = len(text) <= 40
    sentence_like = len(text) > 48 or text.endswith(("。", "；"))
    heading_text = re.sub(rf"^(?:第[{CN_NUMBER}\d]+(?:部分|章)|[{CN_NUMBER}]+、|[（(][{CN_NUMBER}]+[）)])\s*", "", text).strip()
    semantic = heading_text in SEMANTIC_HEADINGS

    if text.rstrip("：:") == "注":
        return False, 0, "none", 0.99

    if text == "项目需求书":
        return True, 1, "rule_inferred", 0.99
    if text == "技术要求点对点应答表":
        level = min(6, (int(stack[-1]["heading_level"]) + 1) if stack else 1)
        return True, level, "rule_inferred", 0.98

    if ("heading" in style or "标题" in style or style == "subtitle") and len(text) <= 100:
        return True, _level_for(family, stack, outline), "native_style", 0.99
    if outline is not None and short and not sentence_like:
        return True, _level_for(family, stack, int(outline)), "outline_level", 0.98
    if family in {"part", "chapter"} and len(text) <= 80:
        return True, 1, "rule_inferred", 0.98
    if family == "cn_top" and short and not sentence_like:
        confidence = 0.97 if index in numbered_sequences else (0.92 if semantic else 0.88)
        return True, _level_for(family, stack, None), "rule_inferred", confidence
    if family == "cn_paren" and short and not sentence_like:
        return True, _level_for(family, stack, None), "rule_inferred", 0.9
    if family == "arabic" and index in numbered_parent_headings and short and not sentence_like:
        return True, 2 if any(node["heading_level"] == 1 for node in stack) else 1, "rule_inferred", 0.94
    if family == "decimal" and len(text) <= 20 and not sentence_like and not text.endswith(("：", ":")):
        return True, _level_for(family, stack, None), "rule_inferred", 0.86
    if family == "arabic" and short and not sentence_like:
        formatting = bool(block.get("is_bold")) or block.get("keep_with_next") is True
        if formatting:
            return True, _level_for(family, stack, None), "rule_inferred", 0.82
    if family == "attachment":
        return True, 2, "rule_inferred", 0.9
    if semantic and short:
        return True, 1 if text == "项目需求书" else _level_for("cn_top", stack, None), "rule_inferred", 0.92
    if short and len(text) > 2 and not text.endswith(("：", ":")) and block.get("is_bold") and block.get("alignment") == 1:
        return True, min(4, (int(stack[-1]["heading_level"]) + 1) if stack else 1), "rule_inferred", 0.78
    return False, 0, "none", 0.15


def _level_for(family: str, stack: list[dict[str, Any]], outline: int | None) -> int:
    if family in {"part", "chapter"}:
        return 1
    if family == "cn_top":
        return 2 if any(node["heading_level"] == 1 for node in stack) else 1
    if family == "cn_paren":
        return 3 if any(node["heading_level"] <= 2 for node in stack) else 2
    if family == "decimal":
        dots = str(family).count(".")
        return min(6, 3 + dots)
    if family == "arabic":
        return min(6, (int(stack[-1]["heading_level"]) + 1) if stack else 2)
    if outline is not None:
        native_level = int(outline) + 1
        if native_level == 1 and stack and stack[-1]["heading_level"] == 1:
            return 2
        return max(1, min(6, native_level))
    return min(6, (int(stack[-1]["heading_level"]) + 1) if stack else 1)


def _number_value(value: Any) -> int | None:
    text = str(value or "")
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if text in digits:
        return digits[text]
    if text.startswith("十") and len(text) == 2 and text[1] in digits:
        return 10 + digits[text[1]]
    if text.endswith("十") and len(text) == 2 and text[0] in digits:
        return digits[text[0]] * 10
    return None

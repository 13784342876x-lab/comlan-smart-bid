from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


FORM_NAMES = (
    "技术要求点对点应答表",
    "技术偏差表",
    "项目需求逐条应答表",
    "人员表",
    "业绩表",
    "证明材料索引表",
)
FULL_OUTLINE_PATTERNS = (
    r"严格(?:按照|遵循).{0,20}(?:目录|章节|顺序)",
    r"不得(?:调整|改变).{0,20}(?:目录|章节|顺序)",
    r"按下列(?:目录|章节).{0,10}(?:编制|排列)",
    r"(?:目录|章节)顺序如下",
)
TITLE_LOCK_PATTERNS = (r"章节名称不得修改", r"标题不得修改", r"须使用下列章节名称")


def load_response_outline(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("technical response outline root must be a JSON object")
    return data


def analyze_tender_writing_format(response_outline: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(response_outline, (str, Path)):
        response_outline = load_response_outline(response_outline)
    if not isinstance(response_outline, dict):
        raise ValueError("technical response outline must be an object")

    required_forms: list[dict[str, Any]] = []
    format_requirements: list[dict[str, Any]] = []
    required_sections: list[dict[str, Any]] = []
    source_refs: list[dict[str, Any]] = []
    all_format_text: list[str] = []

    for chapter in response_outline.get("chapters") or []:
        chapter_title = str(chapter.get("title") or "")
        is_format_chapter = (
            str(chapter.get("source_category") or "") == "technical_format"
            or "格式" in chapter_title
            or "应答要求" in chapter_title
        )
        if not is_format_chapter:
            continue
        for point in chapter.get("response_points") or []:
            point_id = str(point.get("point_id") or "")
            heading = str(point.get("original_heading") or "")
            text = str(point.get("original_text") or "")
            combined = f"{heading}\n{text}"
            all_format_text.append(combined)
            ref = {
                "response_point_id": point_id,
                "chapter_id": str(chapter.get("chapter_id") or ""),
                "heading": heading,
                "source_refs": list(point.get("source_refs") or []),
            }
            source_refs.append(ref)
            format_requirements.append(
                {
                    "response_point_id": point_id,
                    "requirement": _shorten(text, 260),
                    "heading": heading,
                }
            )
            for form_name in FORM_NAMES:
                if form_name in combined:
                    _merge_form(required_forms, form_name, point, chapter)

            if _is_explicit_section_requirement(point):
                required_sections.append(
                    {
                        "title": _directory_title(point),
                        "response_point_ids": [point_id],
                        "source_refs": list(point.get("source_refs") or []),
                    }
                )

    joined = "\n".join(all_format_text)
    order_locked = any(re.search(pattern, joined) for pattern in FULL_OUTLINE_PATTERNS)
    title_locked = any(re.search(pattern, joined) for pattern in TITLE_LOCK_PATTERNS)
    has_complete_directory = order_locked and len(required_sections) >= 2

    reasoning_codes: list[str] = []
    if has_complete_directory:
        format_mode = "full_outline"
        confidence = 0.96
        reasoning_codes.extend(["EXPLICIT_COMPLETE_DIRECTORY", "EXPLICIT_ORDER_LOCK"])
    elif required_sections:
        format_mode = "partial_outline"
        confidence = 0.84
        reasoning_codes.append("EXPLICIT_PARTIAL_SECTION_REQUIREMENTS")
    elif required_forms:
        format_mode = "forms_only"
        confidence = 0.96
        reasoning_codes.extend(["MANDATORY_FORMS_FOUND", "NO_COMPLETE_DIRECTORY_FOUND"])
    else:
        format_mode = "none"
        confidence = 0.78
        reasoning_codes.append("NO_WRITING_STRUCTURE_CONSTRAINT_FOUND")

    if "投标文件格式" in joined and not has_complete_directory:
        reasoning_codes.append("GENERIC_FORMAT_REFERENCE_NOT_TREATED_AS_FULL_OUTLINE")
    if required_forms and format_mode == "forms_only":
        reasoning_codes.append("COMPANY_OUTLINE_REMAINS_BODY_SKELETON")
    return {
        "format_mode": format_mode,
        "confidence": confidence,
        "order_locked": order_locked,
        "title_locked": title_locked,
        "required_sections": _dedupe_by(required_sections, "title"),
        "required_forms": required_forms,
        "format_requirements": format_requirements,
        "source_refs": source_refs,
        "reasoning_codes": list(dict.fromkeys(reasoning_codes)),
    }


def _merge_form(
    forms: list[dict[str, Any]],
    title: str,
    point: dict[str, Any],
    chapter: dict[str, Any],
) -> None:
    existing = next((item for item in forms if item["title"] == title), None)
    if existing is None:
        existing = {
            "form_id": f"FORM-{len(forms) + 1:03d}",
            "title": title,
            "response_point_ids": [],
            "display_block_ids": [],
            "source_refs": [],
        }
        forms.append(existing)
    point_id = str(point.get("point_id") or "")
    if point_id and point_id not in existing["response_point_ids"]:
        existing["response_point_ids"].append(point_id)
    for block_id in chapter.get("display_block_ids") or []:
        if block_id not in existing["display_block_ids"]:
            existing["display_block_ids"].append(block_id)
    existing["source_refs"].extend(point.get("source_refs") or [])


def _is_explicit_section_requirement(point: dict[str, Any]) -> bool:
    text = str(point.get("original_text") or "")
    if point.get("content_type") in {"form", "table"}:
        return False
    has_section_language = bool(re.search(r"(?:章节|目录).{0,16}(?:名称|顺序|编制|包括)", text))
    has_named_solution = "方案" in text and bool(re.search(r"(?:应包括|须包括|按以下)", text))
    return has_section_language or has_named_solution


def _directory_title(point: dict[str, Any]) -> str:
    heading = str(point.get("original_heading") or "").strip()
    return heading or _shorten(str(point.get("original_text") or ""), 60)


def _shorten(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _dedupe_by(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        value = str(item.get(key) or "")
        if value not in seen:
            result.append(item)
            seen.add(value)
    return result

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from app.technical_placeholders import extract_placeholder_references


TABLE_TYPES = {
    "required_response_table",
    "scoring_reference_table",
    "informational_source_table",
    "generated_supporting_table",
}
NARRATIVE_BLOCK_TYPES = {
    "introduction",
    "technical_solution",
    "installation_plan",
    "implementation_plan",
    "organization_plan",
    "quality_assurance",
    "risk_control",
}
TABLE_ORIENTED_BLOCK_TYPES = {
    "evidence_table",
    "required_response_table",
    "parameter_response",
    "personnel_detail",
    "placeholder_section",
}

DEVIATION_STATUSES = {
    "no_deviation",
    "positive_deviation_pending_confirmation",
    "positive_deviation_confirmed",
    "negative_deviation",
    "evidence_pending",
}
QUALITY_POLICIES: dict[str, dict[str, float | int]] = {
    "introduction": {
        "min_prose_paragraphs": 2, "min_prose_characters": 140,
        "prose_ratio_warning": 0.75, "prose_ratio_error": 0.55,
        "structured_error_multiple": 1.5, "short_bullet_min_characters": 24,
    },
    "technical_solution": {
        "min_prose_paragraphs": 2, "min_prose_characters": 160,
        "prose_ratio_warning": 0.70, "prose_ratio_error": 0.50,
        "structured_error_multiple": 1.5, "short_bullet_min_characters": 24,
    },
    "implementation_plan": {
        "min_prose_paragraphs": 2, "min_prose_characters": 150,
        "prose_ratio_warning": 0.60, "prose_ratio_error": 0.42,
        "structured_error_multiple": 1.6, "short_bullet_min_characters": 24,
    },
    "organization_plan": {
        "min_prose_paragraphs": 2, "min_prose_characters": 140,
        "prose_ratio_warning": 0.60, "prose_ratio_error": 0.40,
        "structured_error_multiple": 1.6, "short_bullet_min_characters": 22,
    },
    "quality_assurance": {
        "min_prose_paragraphs": 2, "min_prose_characters": 140,
        "prose_ratio_warning": 0.58, "prose_ratio_error": 0.40,
        "structured_error_multiple": 1.7, "short_bullet_min_characters": 22,
    },
    "risk_control": {
        "min_prose_paragraphs": 2, "min_prose_characters": 130,
        "prose_ratio_warning": 0.45, "prose_ratio_error": 0.30,
        "structured_error_multiple": 1.8, "short_bullet_min_characters": 22,
    },
    "list_or_table": {
        "min_prose_paragraphs": 1, "min_prose_characters": 45,
        "prose_ratio_warning": 0.15, "prose_ratio_error": 0.05,
        "structured_error_multiple": 4.0, "short_bullet_min_characters": 12,
    },
}


def normalize_tender_table(
    table: dict[str, Any],
    *,
    point: dict[str, Any],
    table_index: int = 0,
) -> dict[str, Any]:
    source_refs = list(point.get("source_refs") or [])
    source = source_refs[0] if source_refs and isinstance(source_refs[0], dict) else {}
    rows = list(table.get("rows") or [])
    headers = _table_headers(table, rows)
    has_merges = bool(table.get("has_merged_cells")) or any(
        int(cell.get("rowspan") or 1) > 1 or int(cell.get("colspan") or 1) > 1
        for row in rows
        for cell in (row.get("cells") or [] if isinstance(row, dict) else [])
        if isinstance(cell, dict)
    )
    multi_level_header = _has_multi_level_header(rows)
    fixed_layout = bool(
        source.get("table_range")
        or table.get("column_widths")
        or table.get("row_heights")
        or table.get("signature_area")
    ) and has_merges
    complexity = "complex" if has_merges or multi_level_header or fixed_layout else "simple"
    title = str(
        table.get("title")
        or table.get("table_name")
        or table.get("name")
        or table.get("caption")
        or source.get("table_name")
        or point.get("original_heading")
        or ""
    )
    point_id = str(point.get("point_id") or "")
    identity = "|".join(
        [
            point_id,
            str(source.get("file_id") or source.get("file") or ""),
            str(source.get("row_or_cell") or table_index),
        ]
    )
    table_type = classify_tender_table(table, point=point, title=title, headers=headers)
    return {
        "table_id": str(table.get("table_id") or f"TBL-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12].upper()}"),
        "title": title,
        "table_name": title,
        "table_type": table_type,
        "reuse_required": table_type == "required_response_table",
        "response_point_id": point_id,
        "source_file_id": source.get("file_id") or source.get("file"),
        "source_file_name": source.get("file_name") or source.get("source_file_name"),
        "source_location": {
            "clause_path": source.get("clause_path") or point.get("section_path") or [],
            "page_start": source.get("page_start"),
            "page_end": source.get("page_end"),
            "table_range": source.get("table_range"),
            "row_or_cell": source.get("row_or_cell"),
        },
        "headers": headers,
        "field_order": headers,
        "row_count": len(rows),
        "column_count": int(table.get("column_count") or len(headers) or 0),
        "row_column_relationship": _row_column_relationship(rows, headers),
        "filling_requirements": table.get("filling_requirements")
        or table.get("instructions")
        or table.get("notes")
        or [],
        "required": table_type == "required_response_table",
        "source_table_index": _source_table_index(source, table_index),
        "has_merged_cells": has_merges,
        "merged_cells": list(table.get("merged_cells") or []),
        "layout_complexity": complexity,
        "format_preservation": "exact" if complexity == "complex" else "semantic",
        "markdown_allowed": complexity == "simple",
        "policy": _table_policy(table_type),
    }


def classify_tender_table(
    table: dict[str, Any],
    *,
    point: dict[str, Any],
    title: str = "",
    headers: list[str] | None = None,
) -> str:
    """Classify a source table by purpose, never merely by ``content_type=table``."""
    explicit = str(table.get("table_type") or point.get("table_type") or "").strip()
    if explicit == "scoring_reference_table":
        return explicit
    header_text = " ".join(str(item) for item in (headers or []))
    filling_text = json_safe_text(
        table.get("filling_requirements")
        or table.get("instructions")
        or table.get("notes")
        or ""
    )
    context = " ".join(
        str(value or "")
        for value in (
            title,
            point.get("original_heading"),
            point.get("original_text"),
            point.get("section_title"),
            " ".join(str(item) for item in point.get("section_path") or []),
            header_text,
            filling_text,
        )
    )
    scoring_context_terms = (
        "评审因素",
        "评标标准",
        "评分标准",
        "评分办法",
        "评审办法",
    )
    non_header_context = " ".join(
        str(value or "")
        for value in (
            title,
            point.get("original_heading"),
            point.get("original_text"),
            point.get("section_title"),
            " ".join(str(item) for item in point.get("section_path") or []),
        )
    )
    scoring_header_markers = sum(
        term in header_text
        for term in ("评分项", "评审因素", "分值", "评分标准", "得分")
    )
    if (
        any(term in non_header_context for term in scoring_context_terms)
        or scoring_header_markers >= 2
    ):
        return "scoring_reference_table"
    if explicit in {"required_response_table", "informational_source_table"}:
        return explicit
    response_terms = (
        "投标人填写",
        "投标人响应",
        "投标人应答",
        "由投标人填写",
        "响应表",
        "应答表",
        "偏离表",
        "偏差表",
        "报价表",
        "填写要求",
        "声明函",
        "承诺函",
    )
    if (
        any(term in context for term in response_terms)
        or _structured_true(table.get("requires_bidder_fill"))
        or _structured_true(table.get("response_table"))
        or _structured_true(point.get("requires_bidder_fill"))
        or _structured_true(point.get("response_table"))
    ):
        return "required_response_table"
    return "informational_source_table"


def _structured_true(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return str(value or "").strip().lower() in {
        "true",
        "yes",
        "required",
        "bidder_fill",
        "response_table",
    }


def _table_policy(table_type: str) -> str:
    if table_type == "required_response_table":
        return "招标文件明确要求投标人填写或响应；原表名、表头、字段顺序、合并关系和填写要求必须保留。"
    if table_type == "scoring_reference_table":
        return "评分与评审表只用于写作和覆盖判断；禁止插入响应正文，禁止进入table_fill_data。"
    return "来源表只用于提取事实或要求，默认不插入响应正文。"


def collect_applicable_tender_tables(
    response_outline: dict[str, Any],
    response_point_ids: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for chapter in response_outline.get("chapters") or []:
        for point in chapter.get("response_points") or []:
            if str(point.get("point_id") or "") not in response_point_ids:
                continue
            table = point.get("table")
            if isinstance(table, dict):
                result.append(normalize_tender_table(table, point=point, table_index=len(result)))
    return result


def build_rule_application_plan(
    *,
    target: dict[str, Any],
    rule_ids: list[str],
    tender_tables: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = list(target.get("children") or [])
    boundaries: list[dict[str, Any]] = []
    for node in nodes:
        title = str(node.get("title") or "")
        focus, avoid = _section_boundary(title)
        boundaries.append(
            {
                "section_title": title,
                "focus": focus,
                "avoid_repeating": avoid,
            }
        )
    manual_facts: list[str] = []
    web_needs: list[str] = []
    for node in [target, *nodes]:
        requirements = node.get("writing_requirements") or {}
        manual_facts.extend(
            str(item)
            for item in ((requirements.get("manual_project_facts") or {}).get("fact_types") or [])
        )
        web_needs.extend(
            str(item)
            for item in ((requirements.get("web") or {}).get("purposes") or [])
        )
    reuse = [
        {
            "table_id": item["table_id"],
            "title": item["title"],
            "layout_complexity": item["layout_complexity"],
            "format_preservation": item["format_preservation"],
        }
        for item in tender_tables
        if item.get("table_type") == "required_response_table"
    ]
    create_candidates = _created_table_candidates(nodes, tender_tables)
    return {
        "applicable_rule_ids": list(rule_ids),
        "section_boundaries": boundaries,
        "tender_tables_to_reuse": reuse,
        "tables_to_create": create_candidates,
        "facts_requiring_placeholders": list(dict.fromkeys(manual_facts)),
        "external_evidence_needs": list(dict.fromkeys(web_needs)),
    }


def review_chapter_quality(
    draft: dict[str, Any] | None,
    *,
    tender_tables: list[dict[str, Any]],
    source_only_personnel_section_ids: set[str] | None = None,
) -> dict[str, Any]:
    sections = list((draft or {}).get("sections") or [])
    duplicates = detect_duplicate_sections(sections)
    markdown_tables: list[dict[str, Any]] = []
    table_errors: list[str] = []
    section_metrics: list[dict[str, Any]] = []
    quality_errors: list[str] = []
    quality_warnings: list[str] = []
    for section in sections:
        content = str(section.get("content") or "")
        parsed = inspect_markdown_tables(content)
        for item in parsed["tables"]:
            item["section_title"] = str(section.get("heading") or "")
        markdown_tables.extend(parsed["tables"])
        table_errors.extend(
            f"{section.get('heading')}: {error}" for error in parsed["errors"]
        )
        metrics = inspect_section_composition(section)
        section_references = {
            str(section.get("section_id") or ""),
            str(section.get("heading") or ""),
        }
        if section_references & set(source_only_personnel_section_ids or set()):
            metrics["source_only_personnel_exempt"] = True
            metrics["insufficient_prose"] = False
            metrics["errors"] = [
                error
                for error in metrics.get("errors") or []
                if any(
                    code in str(error)
                    for code in (
                        "table_without_intro",
                        "table_without_followup",
                        "dangling_table_reference",
                    )
                )
            ]
        section_metrics.append(metrics)
        quality_errors.extend(metrics["errors"])
        quality_warnings.extend(metrics["warnings"])
    reused: list[dict[str, Any]] = []
    complex_preserved: list[dict[str, Any]] = []
    fill_data: list[dict[str, Any]] = []
    required_tables = [
        table
        for table in tender_tables
        if table.get("table_type") == "required_response_table"
    ]
    for table in required_tables:
        if table["layout_complexity"] == "complex":
            simplified = _complex_table_was_simplified(table, markdown_tables)
            complex_preserved.append(
                {
                    "table_id": table["table_id"],
                    "title": table["title"],
                    "preserved": not simplified,
                    "source_file_id": table.get("source_file_id"),
                    "source_table_index": table.get("source_table_index"),
                }
            )
            fill_data.append(
                {
                    "table_id": table["table_id"],
                    "source_file_id": table.get("source_file_id"),
                    "source_table_index": table.get("source_table_index"),
                    "format_preservation": "exact",
                    "cell_values": {header: None for header in table.get("field_order") or []},
                }
            )
        elif _simple_table_was_reused(table, markdown_tables):
            reused.append(
                {
                    "table_id": table["table_id"],
                    "title": table["title"],
                    "format_preservation": "semantic",
                }
            )
    reused_signatures = {
        tuple(item.get("headers") or [])
        for table in required_tables
        if table["layout_complexity"] == "simple"
        for item in markdown_tables
        if _headers_match(table.get("headers") or [], item.get("headers") or [])
    }
    scoring_signatures = {
        tuple(table.get("headers") or [])
        for table in tender_tables
        if table.get("table_type") == "scoring_reference_table"
    }
    scoring_tables_inserted = [
        item
        for item in markdown_tables
        if any(
            _headers_match(list(signature), item.get("headers") or [])
            for signature in scoring_signatures
        )
    ]
    for item in scoring_tables_inserted:
        quality_errors.append(
            "scoring_reference_table_inserted_in_response:"
            + str(item.get("section_title") or "")
        )
    all_source_signatures = {
        tuple(table.get("headers") or [])
        for table in tender_tables
        if table.get("headers")
    }
    created = [
        {
            "section_title": item["section_title"],
            "headers": item["headers"],
            "row_count": item["row_count"],
        }
        for item in markdown_tables
        if tuple(item.get("headers") or []) not in all_source_signatures
    ]
    required_table_characters_by_section: dict[str, int] = {}
    for table in required_tables:
        for item in markdown_tables:
            if not _headers_match(
                table.get("headers") or [],
                item.get("headers") or [],
            ):
                continue
            section_title = str(item.get("section_title") or "")
            required_table_characters_by_section[section_title] = (
                required_table_characters_by_section.get(section_title, 0)
                + int(item.get("character_count") or 0)
            )
    for metrics in section_metrics:
        required_characters = required_table_characters_by_section.get(
            metrics["section_title"],
            0,
        )
        if required_characters:
            metrics["contains_required_response_table"] = True
            _apply_required_table_ratio_exemption(
                metrics,
                required_table_characters=required_characters,
            )
    sections_by_title = {
        str(section.get("heading") or ""): section for section in sections
    }
    for table in required_tables:
        matched_titles = [
            str(item.get("section_title") or "")
            for item in markdown_tables
            if _headers_match(
                table.get("headers") or [],
                item.get("headers") or [],
            )
        ]
        if not matched_titles:
            response_point_id = str(table.get("response_point_id") or "")
            matched_titles = [
                str(section.get("heading") or "")
                for section in sections
                if response_point_id
                and response_point_id
                in {
                    str(value)
                    for value in section.get("covered_requirement_ids") or []
                }
            ][:1]
        for section_title in dict.fromkeys(matched_titles):
            section = sections_by_title.get(section_title)
            if not section:
                continue
            explanation_errors = _required_table_explanation_errors(
                str(section.get("content") or "")
            )
            metrics = next(
                item
                for item in section_metrics
                if item["section_title"] == section_title
            )
            metrics["errors"].extend(
                f"required_response_table:{section_title}:{error}"
                for error in explanation_errors
            )
    quality_errors = [
        item
        for metrics in section_metrics
        for item in metrics["errors"]
    ]
    quality_warnings = [
        item
        for metrics in section_metrics
        for item in metrics["warnings"]
    ]
    for item in scoring_tables_inserted:
        quality_errors.append(
            "scoring_reference_table_inserted_in_response:"
            + str(item.get("section_title") or "")
        )
    _validate_generated_table_metadata(
        draft or {},
        created=created,
        section_metrics=section_metrics,
        errors=quality_errors,
    )
    soft_warnings: list[str] = []
    for section in sections:
        heading = str(section.get("heading") or "")
        content = str(section.get("content") or "")
        metrics = next(item for item in section_metrics if item["section_title"] == heading)
        if (
            metrics["prose_character_count"] < 30
            and not metrics.get("source_only_personnel_exempt")
        ):
            soft_warnings.append(f"section content may be too generic or short: {heading}")
        if re.search(r"\b(?:GB/?T|GB|ISO|IEC)\s*[0-9]", content) and not re.search(
            r"[\u4e00-\u9fff]{2,}[（(](?:GB/?T|GB|ISO|IEC)",
            content,
        ):
            soft_warnings.append(f"standard reference may lack a natural standard name: {heading}")
    totals = _aggregate_composition_metrics(section_metrics)
    total_structured = (
        totals["bullet_character_count"]
        + totals["markdown_table_character_count"]
        - sum(required_table_characters_by_section.values())
    )
    total_structured = max(total_structured, 0)
    total_prose = totals["prose_character_count"]
    totals["required_table_exempt_character_count"] = sum(
        required_table_characters_by_section.values()
    )
    totals["non_exempt_structured_character_count"] = max(total_structured, 0)
    if total_prose:
        if total_structured > total_prose * 1.75:
            quality_errors.append(
                "chapter_quality:structured_content_exceeds_prose:error"
            )
        elif total_structured > total_prose:
            quality_warnings.append(
                "chapter_quality:structured_content_exceeds_prose:warning"
            )
    return {
        "duplicate_section_pairs": duplicates,
        "markdown_tables": markdown_tables,
        "markdown_table_errors": table_errors,
        "tender_tables_reused": reused,
        "tender_tables_created": created,
        "scoring_reference_tables_inserted": scoring_tables_inserted,
        "complex_tender_tables_preserved": complex_preserved,
        "table_fill_data": fill_data,
        "section_metrics": section_metrics,
        "chapter_metrics": totals,
        "quality_errors": list(dict.fromkeys(quality_errors)),
        "quality_warnings": list(dict.fromkeys(quality_warnings)),
        "sections_with_insufficient_prose": [
            item["section_title"]
            for item in section_metrics
            if item["insufficient_prose"]
        ],
        "soft_warnings": soft_warnings,
    }


def inspect_section_composition(section: dict[str, Any]) -> dict[str, Any]:
    heading = str(section.get("heading") or "")
    block_type = str(section.get("block_type") or "other")
    content = str(section.get("content") or "")
    blocks = _markdown_blocks(content)
    policy = _quality_policy(block_type)
    for item in blocks:
        item["effective_prose"] = (
            item["kind"] == "prose"
            and _is_effective_prose_paragraph(item["text"])
        )
    prose_blocks_all = [item for item in blocks if item.get("effective_prose")]
    bullet_blocks = [item for item in blocks if item["kind"] == "bullet"]
    table_blocks = [item for item in blocks if item["kind"] == "table"]
    numbered_blocks = [item for item in blocks if item["kind"] == "numbered"]
    repeated_prose_blocks = [
        item
        for item in prose_blocks_all
        if _prose_repeats_table_content(item["text"], table_blocks)
    ]
    prose_blocks = [
        item for item in prose_blocks_all if item not in repeated_prose_blocks
    ]
    numbered_prose_items = [
        text
        for block in numbered_blocks
        for text in block.get("item_texts") or []
        if _is_effective_prose_paragraph(text, numbered_step=True)
    ]
    prose_lengths = [
        item["characters"] for item in prose_blocks
    ] + [len(re.sub(r"\s+", "", value)) for value in numbered_prose_items]
    prose_chars = sum(prose_lengths)
    bullet_chars = sum(
        len(re.sub(r"\s+", "", value))
        for block in bullet_blocks
        for value in block.get("item_texts") or []
    )
    table_chars = sum(item["characters"] for item in table_blocks)
    total = prose_chars + bullet_chars + table_chars
    prose_ratio = prose_chars / total if total else 0.0
    bullet_ratio = bullet_chars / total if total else 0.0
    table_ratio = table_chars / total if total else 0.0
    consecutive_groups = 0
    bullet_streak = 0
    for item in blocks:
        if item["kind"] == "bullet":
            if bullet_streak:
                consecutive_groups += 1
            bullet_streak += 1
        elif item.get("effective_prose"):
            bullet_streak = 0
    table_without_intro = 0
    table_without_followup = 0
    for index, block in enumerate(blocks):
        if block["kind"] != "table":
            continue
        if not (
            _has_effective_neighbor(blocks, index, before=True)
            or _has_table_intro_neighbor(blocks, index)
        ):
            table_without_intro += 1
        if not _has_effective_neighbor(blocks, index, before=False):
            table_without_followup += 1

    bullet_without_intro = 0
    bullet_without_followup = 0
    for index, block in enumerate(blocks):
        if block["kind"] != "bullet":
            continue
        if not _has_effective_neighbor(blocks, index, before=True):
            bullet_without_intro += 1
        if not _has_effective_neighbor(blocks, index, before=False):
            bullet_without_followup += 1

    narrative = block_type in NARRATIVE_BLOCK_TYPES
    table_oriented = block_type in TABLE_ORIENTED_BLOCK_TYPES
    inline_step_chain_count = sum(
        len(
            re.findall(
                r"步骤(?:[一二三四五六七八九十]+|\d+)",
                item["text"],
            )
        )
        >= 4
        for item in blocks
        if item["kind"] == "prose"
    )
    dangling_table_reference = _has_dangling_table_reference(content)
    effective_paragraph_count = len(prose_blocks) + len(numbered_prose_items)
    insufficient = narrative and (
        effective_paragraph_count < int(policy["min_prose_paragraphs"])
        or prose_chars < int(policy["min_prose_characters"])
    )
    short_bullets = sum(
        len(re.sub(r"\s+", "", value))
        < int(policy["short_bullet_min_characters"])
        for block in bullet_blocks
        for value in block.get("item_texts") or []
    )
    bullet_items = sum(item["item_count"] for item in bullet_blocks)
    list_dominant = bool(
        bullet_blocks
        and (
            not prose_chars
            or bullet_chars > prose_chars
            or (bullet_ratio >= 0.50 and bullet_items >= 4)
        )
    )
    errors: list[str] = []
    warnings: list[str] = []
    if narrative:
        if not effective_paragraph_count:
            errors.append(f"section_quality:{heading}:no_complete_prose")
        elif insufficient:
            errors.append(
                f"section_quality:{heading}:insufficient_prose:"
                f"paragraphs={effective_paragraph_count},characters={prose_chars}"
            )
    if repeated_prose_blocks:
        warnings.append(
            f"section_quality:{heading}:prose_repeats_table_content:"
            f"{len(repeated_prose_blocks)}"
        )
    if table_blocks and table_without_intro:
        errors.append(f"section_quality:{heading}:table_without_intro:{table_without_intro}")
    if table_blocks and table_without_followup:
        errors.append(f"section_quality:{heading}:table_without_followup:{table_without_followup}")
    if bullet_blocks and bullet_without_intro:
        errors.append(f"section_quality:{heading}:bullet_groups_without_intro:{bullet_without_intro}")
    if bullet_blocks and bullet_without_followup:
        errors.append(f"section_quality:{heading}:bullet_groups_without_followup:{bullet_without_followup}")
    structured_chars = bullet_chars + table_chars
    if not table_oriented and prose_chars:
        if structured_chars > prose_chars * float(policy["structured_error_multiple"]):
            errors.append(
                f"section_quality:{heading}:structured_content_exceeds_prose:error"
            )
        elif structured_chars > prose_chars:
            warnings.append(
                f"section_quality:{heading}:structured_content_exceeds_prose:warning"
            )
        if prose_ratio < float(policy["prose_ratio_error"]):
            errors.append(f"section_quality:{heading}:prose_ratio_too_low:error")
        elif prose_ratio < float(policy["prose_ratio_warning"]):
            warnings.append(f"section_quality:{heading}:prose_ratio_too_low:warning")
        if bullet_chars > prose_chars * float(policy["structured_error_multiple"]):
            errors.append(f"section_quality:{heading}:bullet_dominant:error")
        elif bullet_chars > prose_chars:
            warnings.append(f"section_quality:{heading}:bullet_dominant:warning")
    if not table_oriented and not prose_chars and structured_chars:
        errors.append(f"section_quality:{heading}:only_tables_or_lists")
    if consecutive_groups >= 2:
        errors.append(f"section_quality:{heading}:consecutive_list_groups:{consecutive_groups}")
    elif consecutive_groups:
        warnings.append(f"section_quality:{heading}:consecutive_list_groups:1")
    ppt_short_bullets = bullet_items >= 5 and (
        short_bullets >= 5 or short_bullets / max(bullet_items, 1) >= 0.70
    )
    if ppt_short_bullets:
        target = errors if narrative else warnings
        target.append(
            f"section_quality:{heading}:ppt_style_short_bullets:"
            f"{short_bullets}/{bullet_items}"
        )
    if list_dominant and narrative:
        warnings.append(f"section_quality:{heading}:list_dominant_section")
    if inline_step_chain_count and narrative:
        errors.append(f"section_quality:{heading}:inline_step_chain")
    if dangling_table_reference:
        errors.append(f"section_quality:{heading}:dangling_table_reference")
    return {
        "section_title": heading,
        "block_type": block_type,
        "prose_paragraph_count": effective_paragraph_count,
        "prose_character_count": prose_chars,
        "average_prose_paragraph_length": (
            round(prose_chars / effective_paragraph_count, 2)
            if effective_paragraph_count
            else 0.0
        ),
        "longest_prose_paragraph_length": max(prose_lengths, default=0),
        "section_total_character_count": len(re.sub(r"\s+", "", content)),
        "prose_paragraphs_repeating_tables": len(repeated_prose_blocks),
        "bullet_group_count": len(bullet_blocks),
        "bullet_item_count": bullet_items,
        "bullet_character_count": bullet_chars,
        "short_bullet_item_count": short_bullets,
        "bullet_groups_without_intro": bullet_without_intro,
        "bullet_groups_without_followup": bullet_without_followup,
        "list_dominant_sections": [heading] if list_dominant else [],
        "numbered_step_count": sum(item["item_count"] for item in numbered_blocks),
        "inline_step_chain_count": inline_step_chain_count,
        "markdown_table_count": len(table_blocks),
        "markdown_table_character_count": table_chars,
        "prose_ratio": round(prose_ratio, 4),
        "bullet_ratio": round(bullet_ratio, 4),
        "table_ratio": round(table_ratio, 4),
        "consecutive_list_group_count": consecutive_groups,
        "table_without_intro_count": table_without_intro,
        "table_without_followup_count": table_without_followup,
        "insufficient_prose": insufficient,
        "contains_required_response_table": False,
        "errors": errors,
        "warnings": warnings,
    }


def _has_dangling_table_reference(content: str) -> bool:
    for match in re.finditer(
        r"(?:如下表|详见下表|下表(?:所示|列出|汇总|说明)|将在下表中列出)",
        str(content or ""),
    ):
        if not inspect_markdown_tables(str(content or "")[match.end() :])["tables"]:
            return True
    return False


def _apply_required_table_ratio_exemption(
    metrics: dict[str, Any],
    *,
    required_table_characters: int,
) -> None:
    """Exclude only the matched mandatory source table from ratio calculations."""
    exempt = min(
        max(int(required_table_characters), 0),
        int(metrics.get("markdown_table_character_count") or 0),
    )
    metrics["required_table_exempt_character_count"] = exempt
    non_exempt_table = max(
        int(metrics.get("markdown_table_character_count") or 0) - exempt,
        0,
    )
    prose = int(metrics.get("prose_character_count") or 0)
    bullets = int(metrics.get("bullet_character_count") or 0)
    denominator = prose + bullets + non_exempt_table
    metrics["non_exempt_table_character_count"] = non_exempt_table
    metrics["effective_prose_ratio"] = (
        round(prose / denominator, 4) if denominator else 0.0
    )
    metrics["effective_table_ratio"] = (
        round(non_exempt_table / denominator, 4) if denominator else 0.0
    )
    metrics["errors"] = [
        item
        for item in metrics.get("errors") or []
        if "structured_content_exceeds_prose" not in item
        and "prose_ratio_too_low" not in item
    ]
    metrics["warnings"] = [
        item
        for item in metrics.get("warnings") or []
        if "structured_content_exceeds_prose" not in item
        and "prose_ratio_too_low" not in item
    ]
    block_type = str(metrics.get("block_type") or "other")
    if block_type in TABLE_ORIENTED_BLOCK_TYPES or not prose:
        return
    policy = _quality_policy(block_type)
    structured = bullets + non_exempt_table
    if structured > prose * float(policy["structured_error_multiple"]):
        metrics["errors"].append(
            f"section_quality:{metrics['section_title']}:"
            "structured_content_exceeds_prose:error"
        )
    elif structured > prose:
        metrics["warnings"].append(
            f"section_quality:{metrics['section_title']}:"
            "structured_content_exceeds_prose:warning"
        )
    effective_ratio = float(metrics["effective_prose_ratio"])
    if effective_ratio < float(policy["prose_ratio_error"]):
        metrics["errors"].append(
            f"section_quality:{metrics['section_title']}:prose_ratio_too_low:error"
        )
    elif effective_ratio < float(policy["prose_ratio_warning"]):
        metrics["warnings"].append(
            f"section_quality:{metrics['section_title']}:prose_ratio_too_low:warning"
        )


def _required_table_explanation_errors(content: str) -> list[str]:
    prose = "\n".join(
        item["text"]
        for item in _markdown_blocks(content)
        if item["kind"] in {"prose", "numbered"}
    )
    compact = re.sub(r"\s+", "", prose)
    errors: list[str] = []
    if not any(term in compact for term in ("用于", "目的", "逐项响应", "填写")):
        errors.append("missing_purpose_explanation")
    if not any(
        term in compact
        for term in ("原表", "表头", "字段", "顺序", "合并关系", "填写边界")
    ):
        errors.append("missing_filling_boundary")
    if not any(
        term in compact
        for term in ("后续", "核对", "复核", "一致", "回填", "闭环", "为准")
    ):
        errors.append("missing_followup_explanation")
    return errors


def calculate_confirmed_work_years(
    *,
    start_date: str,
    end_date: str | None = None,
    calculation_date: str | None = None,
    source_id: str,
) -> dict[str, Any]:
    """Deterministically calculate completed work years from confirmed dates."""
    start = date.fromisoformat(start_date)
    effective_end = date.fromisoformat(end_date or calculation_date or date.today().isoformat())
    if effective_end < start:
        raise ValueError("confirmed work end date precedes start date")
    years = effective_end.year - start.year - (
        (effective_end.month, effective_end.day) < (start.month, start.day)
    )
    return {
        "work_years": years,
        "calculation_basis": "completed_calendar_years",
        "start_date": start.isoformat(),
        "end_date": effective_end.isoformat(),
        "calculation_date": date.fromisoformat(
            calculation_date or effective_end.isoformat()
        ).isoformat(),
        "source_id": source_id,
    }


def count_confirmed_similar_projects(
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    counted = [
        item
        for item in projects
        if isinstance(item, dict)
        and str(item.get("confirmation_status") or item.get("status") or "")
        == "confirmed"
        and item.get("is_similar_project") is True
        and str(item.get("project_name") or "").strip()
        and str(item.get("project_content") or "").strip()
        and (
            item.get("source_id")
            or item.get("evidence_id")
            or item.get("evidence_ids")
        )
    ]
    return {
        "similar_project_count": len(counted),
        "project_names": [str(item.get("project_name")) for item in counted],
        "source_ids": list(
            dict.fromkeys(
                str(source_id)
                for item in counted
                for source_id in (
                    list(item.get("evidence_ids") or [])
                    or [
                        item.get("source_id")
                        or item.get("evidence_id")
                    ]
                )
                if source_id
            )
        ),
    }


def classify_deviation_status(
    *,
    tender_baseline: str,
    offered_value: str,
    comparison_direction: str,
    positive_confirmed: bool = False,
) -> str:
    baseline = _comparable_numeric_value(tender_baseline)
    offered = _comparable_numeric_value(offered_value)
    if comparison_direction == "fixed":
        return (
            "no_deviation"
            if _normalize_fact_text(tender_baseline)
            == _normalize_fact_text(offered_value)
            else "evidence_pending"
        )
    if comparison_direction == "interval":
        interval = _numeric_interval(tender_baseline)
        if interval is None or offered is None:
            return "evidence_pending"
        low, high, dimension = interval
        if offered[1] != dimension:
            return "evidence_pending"
        return "no_deviation" if low <= offered[0] <= high else "negative_deviation"
    if baseline is None or offered is None or baseline[1] != offered[1]:
        return "evidence_pending"
    baseline_value, _ = baseline
    offered_number, _ = offered
    if offered_number == baseline_value:
        return "no_deviation"
    if comparison_direction == "upper_limit":
        better = offered_number < baseline_value
    elif comparison_direction == "lower_limit":
        better = offered_number > baseline_value
    else:
        return "evidence_pending"
    if better:
        return (
            "positive_deviation_confirmed"
            if positive_confirmed
            else "positive_deviation_pending_confirmation"
        )
    return "negative_deviation"


def tender_response_policy(
    requirement_text: str,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    text = str(requirement_text or "")
    objective_evidence = bool(
        re.search(
            r"(?:所投产品|产品功能|产品性能|技术参数|制造商资质|投标人资质|"
            r"人员姓名|人员证书|工作年限|同类项目|企业业绩|检测报告|认证证书|"
            r"现场环境)",
            text,
        )
    )
    directly_acceptable = bool(
        re.search(
            r"(?:交货|货到|安装|工期|保修|服务|响应|到场|培训|交货地点|"
            r"到达现场|维修|随机资料|验收|履约|交付|合同义务)",
            text,
        )
    )
    requires_bidder_value = bool(
        re.search(r"(?:由投标人|投标人自行|投标人填写|投标人承诺).{0,20}(?:数值|期限|周期)", text)
    )
    has_baseline = bool(_commitment_tokens(text))
    if objective_evidence:
        return {
            "source_id": source_id,
            "requirement_text": text,
            "response_intent": "no_deviation",
            "evidence_status": "pending",
            "deviation_status": "evidence_pending",
            "default_no_deviation": False,
        }
    if directly_acceptable and not (requires_bidder_value and not has_baseline):
        return {
            "source_id": source_id,
            "requirement_text": text,
            "response_intent": "no_deviation",
            "evidence_status": "confirmed",
            "deviation_status": "no_deviation",
            "default_no_deviation": True,
        }
    return {
        "source_id": source_id,
        "requirement_text": text,
        "response_intent": None,
        "evidence_status": "missing",
        "deviation_status": "evidence_pending",
        "default_no_deviation": False,
    }


def confirmation_requirement(
    item: dict[str, Any],
    *,
    tender_baselines: list[dict[str, Any]],
    confirmed_facts: dict[str, Any] | None = None,
) -> str:
    """Return required, satisfied, or not_required for one atomic project fact.

    A tender baseline suppresses a pending item only when the item explicitly
    identifies the same sourced fact and value.  Category similarity is never
    enough: an installation period cannot inherit a delivery-period baseline.
    """
    if _pending_item_is_confirmed(item, confirmed_facts or {}):
        return "satisfied"
    field = " ".join(
        str(item.get(key) or "")
        for key in ("fact_semantic_field", "field", "field_name", "question", "reason")
    )
    semantic = _pending_fact_semantic(field)
    combined = " ".join(
        str(item.get(key) or "")
        for key in ("field", "field_name", "question", "reason")
    )
    proposed_value = _normalize_pending_value(
        item.get("normalized_value") or item.get("proposed_value")
    )
    proposed_unit = _normalize_pending_value(item.get("unit"))
    if not proposed_value:
        inferred = re.search(
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>个?工作日|天|日|个月|月|年|小时|分钟)",
            combined,
        )
        if inferred:
            proposed_value = _normalize_pending_value(inferred.group("value"))
            proposed_unit = _normalize_pending_value(inferred.group("unit"))
    proposed_start = _normalize_pending_value(item.get("start_reference"))
    source_ids = _pending_source_ids(item)
    if not semantic or not proposed_value:
        return "required"
    eligible = [
        baseline
        for baseline in tender_baselines
        if isinstance(baseline, dict)
        and _pending_fact_semantic(
            str(baseline.get("semantic_field") or baseline.get("text") or "")
        ) == semantic
        and bool(baseline.get("default_no_deviation", True))
        and _normalize_pending_value(baseline.get("value")) == proposed_value
        and (
            not proposed_unit
            or not baseline.get("unit")
            or _normalize_pending_value(baseline.get("unit")) == proposed_unit
        )
    ]
    if not source_ids:
        # A unique exact baseline recovered from the runtime response context is
        # sufficient provenance; ambiguous category matches remain required.
        source_ids = {
            str(eligible[0].get("source_id") or "")
        } if len(eligible) == 1 else set()
    if not source_ids:
        return "required"
    for baseline in tender_baselines:
        if not isinstance(baseline, dict):
            continue
        if _pending_fact_semantic(str(baseline.get("semantic_field") or baseline.get("text") or "")) != semantic:
            continue
        if not bool(baseline.get("default_no_deviation", True)):
            continue
        if str(baseline.get("source_id") or "") not in source_ids:
            continue
        if _normalize_pending_value(baseline.get("value")) != proposed_value:
            continue
        baseline_unit = _normalize_pending_value(baseline.get("unit"))
        if proposed_unit and baseline_unit and proposed_unit != baseline_unit:
            continue
        baseline_start = _normalize_pending_value(baseline.get("start_reference"))
        if proposed_start and baseline_start and proposed_start != baseline_start:
            continue
        return "not_required"
    return "required"


def confirmation_baselines_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    baselines: list[dict[str, Any]] = []
    for chapter in response.get("chapters") or []:
        for point in chapter.get("response_points") or []:
            text = str(point.get("original_text") or "")
            source_id = str(point.get("point_id") or "")
            policy = tender_response_policy(text, source_id=source_id)
            if not policy.get("default_no_deviation"):
                continue
            baselines.extend(_confirmation_baselines_from_text(text, source_id=source_id))
    return _unique_confirmation_baselines(baselines)


def confirmation_baselines_from_generation_permissions(
    generation_permissions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baselines: list[dict[str, Any]] = []
    for entry in generation_permissions:
        if not isinstance(entry, dict):
            continue
        permissions = entry.get("generation_permissions") or {}
        values = [
            *(permissions.get("allowed_tender_numeric_facts") or []),
            *(permissions.get("chapter_allowed_tender_numeric_facts") or []),
            *(entry.get("chapter_allowed_tender_numeric_facts") or []),
        ]
        for value in values:
            if not isinstance(value, dict) or value.get("usage") != "no_deviation_response":
                continue
            source_id = str(value.get("source_id") or value.get("requirement_id") or "")
            text = str(value.get("text") or "")
            if not source_id or not text:
                continue
            baselines.append(
                {
                    "semantic_field": _pending_fact_semantic(text),
                    "source_id": source_id,
                    "value": value.get("value"),
                    "unit": value.get("unit"),
                    "start_reference": value.get("time_origin"),
                    "text": text,
                    "default_no_deviation": True,
                }
            )
    return _unique_confirmation_baselines(baselines)


def _confirmation_baselines_from_text(text: str, *, source_id: str) -> list[dict[str, Any]]:
    baselines: list[dict[str, Any]] = []
    for clause in re.split(r"(?<=[，；。;])", text):
        semantic = _pending_fact_semantic(clause)
        if not semantic:
            continue
        for value, unit in re.findall(
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>个?工作日|天|日|个月|月|年|小时|分钟)",
            clause,
        ):
            baselines.append(
                {
                    "semantic_field": semantic,
                    "source_id": source_id,
                    "value": value,
                    "unit": unit,
                    "start_reference": _pending_start_reference(clause),
                    "text": clause,
                    "default_no_deviation": True,
                }
            )
    return baselines


def _pending_fact_semantic(value: str) -> str | None:
    text = str(value or "")
    if re.search(r"(?:交货|货到|到货|交付|delivery|arrival)", text, re.I):
        return "delivery"
    if re.search(r"(?:安装|部署|installation)", text, re.I):
        return "installation"
    return None


def _pending_start_reference(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if re.search(r"(?:合同签订|签订合同|合同生效)", text):
        return "contract_signing"
    if re.search(r"(?:货到|到货)", text):
        return "arrival"
    return ""


def _pending_source_ids(item: dict[str, Any]) -> set[str]:
    values = [item.get("tender_source_id"), item.get("source_id")]
    for reference in item.get("source_references") or []:
        if isinstance(reference, dict):
            values.extend(reference.get(key) for key in ("source_id", "point_id", "requirement_id"))
        else:
            values.append(reference)
    return {str(value) for value in values if value}


def _pending_item_is_confirmed(item: dict[str, Any], confirmed_facts: dict[str, Any]) -> bool:
    field = _normalize_pending_value(item.get("field") or item.get("field_name"))
    if not field:
        return False
    return any(
        field in _normalize_pending_value(f"{key} {json.dumps(value, ensure_ascii=False)}")
        for key, value in confirmed_facts.items()
    )


def _normalize_pending_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _unique_confirmation_baselines(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for value in values:
        key = tuple(
            str(value.get(name) or "")
            for name in ("semantic_field", "source_id", "value", "unit")
        )
        result[key] = value
    return list(result.values())


def inspect_deviation_issues(
    draft: dict[str, Any],
    *,
    registered_placeholder_ids: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    for claim in draft.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if str(claim.get("claim_type") or "") == "score_coverage_statement":
            continue
        text = str(claim.get("text") or "")
        status = str(claim.get("deviation_status") or "")
        baseline = str(claim.get("tender_baseline") or "")
        offered = str(claim.get("offered_value") or "")
        direction = str(claim.get("comparison_direction") or "")
        calculated = (
            classify_deviation_status(
                tender_baseline=baseline,
                offered_value=offered,
                comparison_direction=direction,
                positive_confirmed=status == "positive_deviation_confirmed",
            )
            if baseline and offered and direction
            else status
        )
        if direction == "fixed" and baseline and offered and calculated != "no_deviation":
            errors.append(
                f"fact_boundary:相关小节:fixed_requirement_difference_requires_review:{text[:100]}"
            )
            continue
        if calculated == "positive_deviation_pending_confirmation":
            errors.append(
                f"fact_boundary:相关小节:positive_deviation_pending_confirmation:{text[:100]}"
            )
        elif calculated == "negative_deviation":
            code = (
                "negative_deviation_marked_no_deviation"
                if claim.get("response_intent") == "no_deviation"
                or status == "no_deviation"
                else "negative_deviation"
            )
            errors.append(f"fact_boundary:相关小节:{code}:{text[:100]}")
        elif (
            status == "evidence_pending"
            and str(claim.get("claim_type") or "") != "tender_fact"
        ):
            if _is_implementation_process_statement(text):
                continue
            claim_placeholders = {
                value.removeprefix("{{").removesuffix("}}")
                for value in extract_placeholder_references(text)
                if value.startswith("{{") and value.endswith("}}")
            }
            if (
                claim_placeholders
                and claim_placeholders <= (registered_placeholder_ids or set())
                and (
                    str(claim.get("claim_type") or "")
                    in {"placeholder", "cautious_reference", "proposal_statement"}
                    or _placeholder_covers_personnel_identity(text)
                    or _placeholder_covers_personnel_fact(text)
                )
            ):
                continue
            errors.append(
                f"fact_boundary:相关小节:evidence_pending:{text[:100]}"
            )
    return list(dict.fromkeys(errors))


def _is_implementation_process_statement(text: str) -> bool:
    """Identify a proposed implementation sequence, not an objective fact claim."""
    value = str(text or "").strip()
    if not value:
        return False
    if _commitment_numeric_matches(value):
        return False
    if re.search(
        r"(?:我公司|本公司|我方)[^，。；;\n]{0,30}"
        r"(?:拥有|具备|形成|建立|已在|曾在|成熟的|专业团队|现有团队|资源)",
        value,
    ):
        return False
    if re.search(
        r"(?:历史业绩|既有业绩|多个[^，。；;\n]{0,12}项目|已在[^，。；;\n]{0,20}项目中|"
        r"人员已经|团队已经|产品(?:支持|具备|实现)|系统(?:支持|具备|实现)|"
        r"保证|确保[^，。；;\n]{0,24}(?:无缝|完成|达成|不中断)|"
        r"全职投入|后备资源|资源池|备份人员|双人复核)",
        value,
    ):
        return False
    process_terms = (
        "需求确认",
        "设备交付",
        "安装部署",
        "系统联调",
        "联调测试",
        "试运行",
        "验收交付",
        "组织推进",
        "划分为",
        "分为",
        "实施阶段",
    )
    return sum(term in value for term in process_terms) >= 2 and bool(
        re.search(r"(?:项目实施|实施工作|本项目|按照|划分|阶段|组织推进)", value)
    )


def inspect_unconfirmed_commitments(
    draft: dict[str, Any],
    *,
    confirmed_facts: dict[str, Any],
    candidate_evidence_ids: set[str] | None = None,
    confirmed_evidence_ids: set[str] | None = None,
    generation_permissions: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return deterministic fact-boundary errors for unsupported promises/capabilities."""
    confirmed_text = json_safe_text(confirmed_facts)
    claims = [
        item
        for item in draft.get("claims") or []
        if isinstance(item, dict)
        and str(item.get("claim_type") or "") != "score_coverage_statement"
    ]
    patterns = [
        (
            "numeric_commitment",
            re.compile(
                r"(?:\d+\s*[×xX*]\s*\d+|\d+|[一二三四五六七八九十百]+)\s*"
                r"(?:个)?(?:工作日|小时|分钟|人|名|人天|年|个月|日|周|月|天)"
                r"(?:内|完成|响应|通知|到场|驻场|试运行|投入)?"
            ),
        ),
        (
            "service_level_commitment",
            re.compile(r"(?:一级|二级|三级|四级|P[0-4])(?:故障|事件|响应).{0,16}(?:分钟|小时|工作日)"),
        ),
        (
            "schedule_frequency",
            re.compile(
                r"(?:每日|每周|每月|每季度|每年|每个工作日|"
                r"每\s*(?:\d+|[一二三四五六七八九十]+)\s*(?:日|天|周|月|季度|年))"
            ),
        ),
        (
            "unconfirmed_product_capability",
            re.compile(
                r"(?:(?:本系统|本方案|所投产品|所投系统|本项目系统)"
                r"[^。！？；;\n]{0,18}(?:支持|提供|采用|实现|确保|具备|配置)"
                r"[^。！？；;\n]{0,80}|"
                r"(?:采用|实现|配置|支持)[^。！？；;\n]{0,24}"
                r"(?:堆叠|MLAG|VPC|分布式虚拟交换机|纠删码|反亲和|资源预留)|"
                r"(?:与|对接|集成)[^。！？；;\n]{0,28}"
                r"(?:现有系统|AD|备份系统|容灾系统|安全设备)|"
                r"(?:本系统|本方案|所投产品|所投系统)"
                r"[^。！？；;\n]{0,20}(?:性能|吞吐|时延|故障|切换|业务)"
                r"[^。！？；;\n]{0,28}(?:达到|提升|降低|不影响|无感知|"
                r"自动切换|自动恢复|保持连续)|"
                r"(?:测试|试运行|验收)(?:结果|证明|表明)"
                r"[^。！？；;\n]{0,24}(?:系统|产品)[^。！？；;\n]{0,24}"
                r"(?:实现|具备|支持|确保)[^。！？；;\n]{0,40}|"
                r"(?:保证|确保)(?:本)?系统[^。！？；;\n]{0,24}"
                r"(?:具备|实现|支持|提供)[^。！？；;\n]{0,40}|"
                r"确保[^。！？；;\n]{0,30}(?:节点|故障)[^。！？；;\n]{0,30}"
                r"(?:不影响|不中断|无感知|自动恢复))",
                re.I,
            ),
        ),
        (
            "candidate_promoted_to_fact",
            re.compile(
                r"(?:我公司已(?:通过|取得|获得|完成|承担)|"
                r"(?:公司|本公司)[^，。；;\n]{0,16}符合\s*ISO\s*9001"
                r"[^，。；;\n]{0,20}(?:体系|要求)|"
                r"我方项目经理为|本项目经理为|"
                r"项目经理由[^，。；;\n]{1,20}担任|"
                r"拟派[\u4e00-\u9fff·]{2,4}(?:担任|作为|负责)?|"
                r"[\u4e00-\u9fff·]{2,4}负责本项目|"
                r"[\u4e00-\u9fff·]{2,4}(?:持有|具有)[^，。；;\n]{0,20}"
                r"(?:岗位|资格|资质|证书|业绩|经验)|"
                r"(?:拟派人员|拟派__PLACEHOLDER__|__PLACEHOLDER__)"
                r"[^，。；;\n]{0,40}(?:具备|持有|满足|提供)"
                r"[^，。；;\n]{0,40}(?:资质|资格|证书|年限|经验|劳动合同|社保|投入)|"
                r"最终团队[^，。；;\n]{0,20}(?:全部|均)(?:满足|具备))"
            ),
        ),
        (
            "unconfirmed_personnel_qualification",
            re.compile(
                r"(?:项目经理|项目负责人|技术负责人|主要现场实施人员|"
                r"实施工程师|拟派人员|团队成员|人员)"
                r"[^，。；;\n]{0,48}(?:具备|具有|熟悉|精通|持有|取得|"
                r"已通过|能够独立完成)[^，。；;\n]{0,48}"
                r"(?:经验|年限|证书|认证|资质|资格|技术|系统|部署|项目)?"
            ),
        ),
        (
            "unconfirmed_bidder_commitment",
            re.compile(
                r"(?:(?:我方|本公司)(?:郑重)?(?:承诺|保证|提供)|"
                r"保证在[^，。；;\n]{0,24}(?:内|前)(?:完成|响应|到场|提供)|"
                r"(?:确保|保证)[^，。；;\n]{0,48}"
                r"(?:可重复|后续运维|参考依据|管理基础|工作基础)|"
                r"(?:尽力|争取)?提前完成|"
                r"无条件整改直至满意|"
                r"额外免费提供|"
                r"(?:对|向)采购人[^，。；;\n]{0,32}(?:操作转移|知识转移|培训))"
            ),
        ),
        (
            "unconfirmed_staffing_assignment",
            re.compile(
                r"(?:另一名(?:已确认)?工程师|两名?人员|两人复核|双人复核|"
                r"质量保障工程师[^，。；;\n]{0,16}复核|"
                r"由[^，。；;\n]{0,16}另行复核|"
                r"(?:项目组织)?采用[^，。；;\n]{0,24}矩阵式(?:组织)?结构|"
                r"(?:项目组织)?(?:将)?设立[^。；;\n]{0,64}(?:组|部门)|"
                r"质量保障组[^。；;\n]{0,32}独立于[^。；;\n]{0,24}(?:组|部门))"
            ),
        ),
        (
            "unconfirmed_resource_commitment",
            re.compile(
                r"(?:合同(?:签订|生效)之日起[^，。；;\n]{0,16}全程负责|"
                r"(?:人员|团队|核心成员)[^，。；;\n]{0,16}全职投入|"
                r"每个岗位[^，。；;\n]{0,12}(?:设置|配备)候补人员|"
                r"(?:备份|备选|候补)人员|"
                r"人员替换[^，。；;\n]{0,24}(?:机制|安排|人选|工作日)|"
                r"(?:调配)?后备资源|"
                r"公司(?:级)?(?:后备资源|资源池)[^，。；;\n]{0,24}(?:随时)?调配?[^，。；;\n]{0,16}(?:同等资质人员)?|"
                r"(?:获得|提供|确保|保障)[^，。；;\n]{0,20}"
                r"(?:技术与管理资源|技术资源|管理资源)[^，。；;\n]{0,12}(?:保障|支持)?)"
            ),
        ),
    ]
    errors: list[str] = []
    for section in draft.get("sections") or []:
        heading = str(section.get("heading") or "")
        content = str(section.get("content") or "")
        section_permissions = _permissions_for_section(
            generation_permissions or [],
            section_id=str(section.get("section_id") or ""),
            section_title=heading,
        )
        for sentence in _sentences(content):
            analyzable = sentence
            for placeholder in extract_placeholder_references(sentence):
                analyzable = analyzable.replace(placeholder, "__PLACEHOLDER__")
            for code, pattern in patterns:
                match = pattern.search(analyzable)
                if not match:
                    continue
                candidate_binding = _candidate_evidence_supports_sentence(
                    sentence,
                    claims,
                    candidate_evidence_ids or set(),
                )
                if code == "numeric_commitment":
                    numeric_matches = list(_commitment_numeric_matches(sentence)) or [match]
                    blocked_numeric = [
                        numeric
                        for numeric in numeric_matches
                        if not _numeric_claim_is_permitted(
                            sentence,
                            numeric.group(0),
                            claims=claims,
                            permissions=section_permissions,
                            numeric_start=numeric.start(),
                            candidate_context=_numeric_candidate_context(
                                sentence,
                                numeric.group(0),
                                numeric_start=numeric.start(),
                                section_id=str(section.get("section_id") or ""),
                                permissions=section_permissions,
                            ),
                        )
                    ]
                    if not blocked_numeric or _exact_tender_required_commitment(
                        sentence,
                        claims,
                    ):
                        continue
                    if (
                        _confirmed_personnel_fact_supports_sentence(
                            sentence,
                            confirmed_facts,
                        )
                        or _statement_is_substantiated(
                            sentence,
                            claims,
                            confirmed_text,
                            candidate_evidence_ids=candidate_evidence_ids or set(),
                            confirmed_evidence_ids=confirmed_evidence_ids or set(),
                        )
                    ):
                        continue
                    effective_code = (
                        "confirmed_personnel_fact_mismatch"
                        if _is_personnel_fact_sentence(sentence)
                        and _has_confirmed_personnel_context(confirmed_facts)
                        else "numeric_commitment"
                    )
                    errors.extend(
                        f"fact_boundary:{heading}:{effective_code}:"
                        f"{''.join(numeric.group(0).split())}"
                        for numeric in blocked_numeric
                    )
                    continue
                if code == "schedule_frequency":
                    frequency_span = match.group(0)
                    if _schedule_frequency_is_permitted(
                        sentence,
                        frequency_span,
                        permissions=section_permissions,
                    ):
                        continue
                    errors.append(
                        f"fact_boundary:{heading}:schedule_frequency:"
                        f"{frequency_span}"
                    )
                    continue
                if code in {
                    "service_level_commitment",
                    "unconfirmed_bidder_commitment",
                }:
                    numeric_matches = list(_commitment_numeric_matches(sentence))
                    permission_match = bool(numeric_matches) and all(
                        _numeric_claim_is_permitted(
                            sentence,
                            numeric.group(0),
                            claims=claims,
                            permissions=section_permissions,
                            numeric_start=numeric.start(),
                            candidate_context=_numeric_candidate_context(
                                sentence,
                                numeric.group(0),
                                numeric_start=numeric.start(),
                                section_id=str(section.get("section_id") or ""),
                                permissions=section_permissions,
                            ),
                        )
                        for numeric in numeric_matches
                    )
                    additional_commitment = _has_extra_bidder_commitment(
                        sentence
                    )
                    tender_numeric_match = (
                        permission_match
                        or _exact_tender_required_commitment(
                            sentence,
                            claims,
                        )
                    )
                    if code == "unconfirmed_bidder_commitment" and not numeric_matches:
                        obligation_debug = _audit_tender_obligation_spans(
                            sentence,
                            claims=claims,
                            permissions=section_permissions,
                        )
                        if obligation_debug["blocked_spans"]:
                            errors.extend(
                                f"fact_boundary:{heading}:"
                                f"unconfirmed_bidder_commitment:{blocked_span}"
                                for blocked_span in obligation_debug[
                                    "blocked_spans"
                                ]
                            )
                            continue
                        if obligation_debug["matched_tender_fact_ids"]:
                            continue
                    if tender_numeric_match and (
                        code != "unconfirmed_bidder_commitment"
                        or not additional_commitment
                    ):
                        continue
                    if (
                        code == "unconfirmed_bidder_commitment"
                        and not numeric_matches
                        and not additional_commitment
                        and _tender_obligation_permits_statement(
                            sentence,
                            claims=claims,
                            permissions=section_permissions,
                        )
                    ):
                        continue
                if (
                    code == "candidate_promoted_to_fact"
                    and (
                        _placeholder_covers_personnel_fact(sentence)
                        or _placeholder_covers_personnel_identity(sentence)
                    )
                ):
                    continue
                if code == "candidate_promoted_to_fact" and not candidate_binding:
                    if (
                        _confirmed_personnel_fact_supports_sentence(
                            sentence,
                            confirmed_facts,
                        )
                        or _statement_is_substantiated(
                            sentence,
                            claims,
                            confirmed_text,
                            candidate_evidence_ids=candidate_evidence_ids or set(),
                            confirmed_evidence_ids=confirmed_evidence_ids or set(),
                        )
                    ):
                        continue
                    fallback_code = (
                        "unconfirmed_personnel_qualification"
                        if _is_personnel_fact_sentence(sentence)
                        else "unconfirmed_enterprise_qualification"
                    )
                    excerpt = re.sub(r"\s+", " ", sentence).strip()[:100]
                    errors.append(
                        f"fact_boundary:{heading}:{fallback_code}:{excerpt}"
                    )
                    continue
                if code == "unconfirmed_personnel_qualification" and candidate_binding:
                    continue
                if (
                    code == "candidate_promoted_to_fact"
                    and _placeholder_covers_personnel_fact(sentence)
                ):
                    continue
                if (
                    code == "unconfirmed_personnel_qualification"
                    and _placeholder_covers_personnel_fact(sentence)
                ):
                    continue
                if (
                    code == "candidate_promoted_to_fact"
                    and _placeholder_covers_personnel_identity(sentence)
                ):
                    continue
                if (
                    code == "candidate_promoted_to_fact"
                    and _generic_candidate_binding(match.group(0))
                ):
                    continue
                if _is_cautious_or_placeholder(
                    analyzable,
                    match=match,
                    claim_code=code,
                ):
                    continue
                if (
                    code == "unconfirmed_product_capability"
                    and _is_design_principle_or_goal(analyzable)
                ):
                    continue
                if (
                    code == "unconfirmed_product_capability"
                    and _is_capability_test_or_source_boundary(analyzable)
                ):
                    continue
                if (
                    code == "unconfirmed_product_capability"
                    and _is_document_or_process_outcome(analyzable)
                ):
                    continue
                if (
                    code in {
                        "numeric_commitment",
                        "candidate_promoted_to_fact",
                        "unconfirmed_staffing_assignment",
                        "unconfirmed_personnel_qualification",
                    }
                    and _confirmed_personnel_fact_supports_sentence(
                        sentence,
                        confirmed_facts,
                    )
                ):
                    continue
                if _statement_is_substantiated(
                    sentence,
                    claims,
                    confirmed_text,
                    candidate_evidence_ids=candidate_evidence_ids or set(),
                    confirmed_evidence_ids=confirmed_evidence_ids or set(),
                ):
                    continue
                effective_code = code
                if (
                    code == "numeric_commitment"
                    and _is_personnel_fact_sentence(sentence)
                    and _has_confirmed_personnel_context(confirmed_facts)
                ):
                    effective_code = "confirmed_personnel_fact_mismatch"
                precise_excerpt = match.group(0)
                if code == "unconfirmed_bidder_commitment":
                    precise_excerpt = next(
                        iter(_extra_bidder_commitment_spans(sentence)),
                        precise_excerpt,
                    )
                excerpt = re.sub(
                    r"\s+",
                    " ",
                    precise_excerpt
                    if code
                    in {
                        "unconfirmed_product_capability",
                        "candidate_promoted_to_fact",
                        "unconfirmed_personnel_qualification",
                        "unconfirmed_bidder_commitment",
                        "unconfirmed_staffing_assignment",
                        "unconfirmed_resource_commitment",
                    }
                    else sentence,
                ).strip()[:100]
                errors.append(
                    f"fact_boundary:{heading}:{effective_code}:{excerpt}"
                )
    return list(dict.fromkeys(errors))


def audit_generation_permissions(
    draft: dict[str, Any],
    *,
    fact_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit a generated candidate against the closed per-section permission set.

    This audit is deterministic and diagnostic only.  It never mutates prose or
    decides whether a reviewable candidate artifact may be saved.
    """
    ledger_by_reference: dict[str, dict[str, Any]] = {}
    for entry in fact_ledger:
        if not isinstance(entry, dict):
            continue
        for reference in (
            entry.get("section_id"),
            entry.get("section_title"),
        ):
            if reference:
                ledger_by_reference[str(reference)] = entry
    claims = [
        item
        for item in draft.get("claims") or []
        if isinstance(item, dict)
        and str(item.get("claim_type") or "") != "score_coverage_statement"
    ]
    chapter_tender_facts = _unique_permission_items(
        [
            item
            for entry in fact_ledger
            if isinstance(entry, dict)
            for item in (
                list(entry.get("chapter_allowed_tender_facts") or [])
                or list(
                    (entry.get("generation_permissions") or {}).get(
                        "chapter_allowed_tender_facts"
                    )
                    or []
                )
            )
            if isinstance(item, dict)
        ]
    )
    chapter_tender_numeric_facts = _unique_permission_items(
        [
            item
            for entry in fact_ledger
            if isinstance(entry, dict)
            for item in (
                list(entry.get("chapter_allowed_tender_numeric_facts") or [])
                or list(
                    (entry.get("generation_permissions") or {}).get(
                        "chapter_allowed_tender_numeric_facts"
                    )
                    or []
                )
            )
            if isinstance(item, dict)
        ]
    )
    result: dict[str, Any] = {
        "passed": True,
        "unauthorized_numeric_claims": [],
        "unauthorized_personnel_claims": [],
        "unauthorized_product_capabilities": [],
        "candidate_facts_promoted": [],
        "unregistered_placeholders": [],
        "unauthorized_bidder_commitments": [],
        "matched_tender_fact_spans": [],
        "chapter_tender_fact_permission_count": len(chapter_tender_facts),
        "chapter_tender_numeric_permission_count": len(
            chapter_tender_numeric_facts
        ),
        "effective_tender_fact_ids_by_section": {},
        "effective_tender_numeric_fact_ids_by_section": {},
    }
    for section_index, section in enumerate(draft.get("sections") or []):
        if not isinstance(section, dict):
            continue
        explicit_section_id = str(section.get("section_id") or "")
        section_title = str(section.get("heading") or "")
        entry = _resolve_permission_entry_for_section(
            section,
            section_index=section_index,
            fact_ledger=fact_ledger,
            ledger_by_reference=ledger_by_reference,
        )
        section_id = str(entry.get("section_id") or explicit_section_id)
        permissions = _permissions_for_section(
            fact_ledger,
            section_id=section_id,
            section_title=section_title,
        )
        if not permissions:
            continue
        section_key = section_id or section_title
        result["effective_tender_fact_ids_by_section"][section_key] = sorted(
            _permission_source_ids(
                permissions.get("effective_tender_facts")
                or permissions.get("allowed_tender_facts")
                or []
            )
        )
        result["effective_tender_numeric_fact_ids_by_section"][
            section_key
        ] = sorted(
            _permission_source_ids(
                permissions.get("effective_tender_numeric_facts")
                or permissions.get("allowed_tender_numeric_facts")
                or []
            )
        )
        forbid_personnel_narrative = bool(
            permissions.get("forbid_unlisted_personnel_narrative")
        )
        allowed_placeholders = {
            str(
                item.get("placeholder_id")
                if isinstance(item, dict)
                else item
            )
            for item in permissions.get("allowed_placeholders") or []
            if (
                item.get("placeholder_id")
                if isinstance(item, dict)
                else item
            )
        }
        content = str(section.get("content") or "")
        used_placeholders = {
            value.removeprefix("{{").removesuffix("}}")
            for value in extract_placeholder_references(content)
            if value.startswith("{{") and value.endswith("}}")
        }
        for placeholder_id in sorted(used_placeholders - allowed_placeholders):
            result["unregistered_placeholders"].append(
                _permission_finding(
                    section_id,
                    section_title,
                    "unregistered_placeholder",
                    f"{{{{{placeholder_id}}}}}",
                    placeholder_id=placeholder_id,
                )
            )
        allowed_confirmed_facts = {
            str(item.get("fact_id") or ""): item
            for item in permissions.get("allowed_confirmed_facts") or []
            if isinstance(item, dict)
        }
        allowed_personnel = [
            item
            for item in permissions.get("allowed_confirmed_personnel_facts")
            or []
            if isinstance(item, dict)
        ]
        allowed_confirmed_evidence_ids = {
            str(source_id)
            for item in [
                *list(
                    permissions.get("allowed_confirmed_product_capabilities")
                    or []
                ),
                *allowed_personnel,
                *list(allowed_confirmed_facts.values()),
            ]
            if isinstance(item, dict)
            for source_id in (
                list(item.get("source_ids") or [])
                or [item.get("source_id") or item.get("evidence_id")]
            )
            if source_id
        }
        candidate_evidence_ids = {
            str(value)
            for value in entry.get("candidate_evidence_ids") or []
            if value
        }
        confirmed_for_section = {
            key: item.get("value")
            for key, item in allowed_confirmed_facts.items()
        }
        section_draft = {
            "sections": [section],
            "claims": claims,
        }
        boundary_errors = inspect_unconfirmed_commitments(
            section_draft,
            confirmed_facts=confirmed_for_section,
            candidate_evidence_ids=candidate_evidence_ids,
            confirmed_evidence_ids=allowed_confirmed_evidence_ids,
            generation_permissions=[entry] if entry else [],
        )
        sentences = _sentences(content)
        for sentence in sentences:
            placeholder_sentence = bool(extract_placeholder_references(sentence))
            personnel_sentence = _is_personnel_fact_sentence(sentence)
            personnel_narrative_sentence = _is_personnel_narrative_sentence(
                sentence
            )
            sentence_placeholder_ids = {
                value.removeprefix("{{").removesuffix("}}")
                for value in extract_placeholder_references(sentence)
                if value.startswith("{{") and value.endswith("}}")
            }
            candidate_personnel_binding = _candidate_evidence_supports_sentence(
                sentence,
                claims,
                candidate_evidence_ids,
            )
            resource_commitment = bool(
                re.search(
                    r"(?:全职投入|全程负责|候补人员|另一名工程师|两人复核|"
                    r"双人复核|(?:调配)?后备资源|资源池[^，。；;\n]{0,20}随时调配)",
                    sentence,
                )
            )
            qualification = bool(
                re.search(
                    r"(?:不少于|至少|具有|具备|持有|取得|均持有|必须持有)"
                    r"[^，。；;\n]{0,28}(?:年|个同类项目|证书|认证|资质|资格|经验)",
                    sentence,
                )
            )
            specific_organization_code = _specific_organization_boundary_code(
                sentence
            )
            if (
                personnel_narrative_sentence
                and forbid_personnel_narrative
                and not specific_organization_code
                and not _is_generic_project_role_duty(sentence)
                and not _is_neutral_team_configuration_statement(
                    sentence,
                    registered_placeholder_ids=allowed_placeholders,
                )
                and not candidate_personnel_binding
                and not _placeholder_covers_personnel_fact(sentence)
                and not _placeholder_covers_personnel_identity(sentence)
                and not (
                    sentence_placeholder_ids
                    and sentence_placeholder_ids <= allowed_placeholders
                    and not qualification
                    and not resource_commitment
                )
                and not _tender_personnel_statement_is_permitted(
                    sentence,
                    claims=claims,
                    permissions=permissions,
                )
                and not _confirmed_personnel_fact_supports_sentence(
                    sentence,
                    confirmed_for_section,
                )
            ):
                result["unauthorized_personnel_claims"].append(
                    _permission_finding(
                        section_id,
                        section_title,
                        (
                            "unconfirmed_resource_commitment"
                            if resource_commitment
                            else (
                                "unconfirmed_personnel_qualification"
                                if qualification
                                else "personnel_narrative_without_source"
                            )
                        ),
                        sentence,
                    )
                )
                continue
            if personnel_sentence and (qualification or resource_commitment):
                if qualification and candidate_personnel_binding:
                    continue
                if not (
                    _placeholder_covers_personnel_fact(sentence)
                    or _placeholder_covers_personnel_identity(sentence)
                    or _confirmed_personnel_fact_supports_sentence(
                        sentence,
                        confirmed_for_section,
                    )
                    or _sentence_has_allowed_confirmed_evidence(
                        sentence,
                        claims,
                        allowed_confirmed_evidence_ids,
                    )
                ):
                    result["unauthorized_personnel_claims"].append(
                        _permission_finding(
                            section_id,
                            section_title,
                            (
                                "unconfirmed_resource_commitment"
                                if resource_commitment
                                else "unconfirmed_personnel_qualification"
                            ),
                            sentence,
                        )
                    )
                    continue
            if placeholder_sentence and (
                _placeholder_covers_personnel_fact(sentence)
                or _placeholder_covers_personnel_identity(sentence)
            ):
                continue
            numeric_matches = list(re.finditer(
                r"(?:\d+\s*[×xX*]\s*\d+|\d+|[一二三四五六七八九十百]+)"
                r"\s*(?:个)?(?:工作日|小时|分钟|人|名|人天|年|个月|日|周|月|天)"
                r"(?:内|完成|响应|通知|到场|驻场|试运行|投入)?",
                sentence,
            ))
            allowed_numeric_spans: list[str] = []
            allowed_numeric_ids: set[str] = set()
            blocked_numeric_spans: list[str] = []
            for numeric_match in numeric_matches:
                if personnel_sentence and _confirmed_personnel_fact_supports_sentence(
                    sentence,
                    confirmed_for_section,
                ):
                    continue
                candidate_context = _numeric_candidate_context(
                    sentence,
                    numeric_match.group(0),
                    numeric_start=numeric_match.start(),
                    section_id=section_id,
                    permissions=permissions,
                )
                matched_items = _matching_numeric_permission_items(
                    sentence,
                    numeric_match.group(0),
                    claims=claims,
                    permissions=permissions,
                    numeric_start=numeric_match.start(),
                    candidate_context=candidate_context,
                )
                if matched_items:
                    allowed_numeric_spans.append(numeric_match.group(0))
                    matched_ids = _permission_source_ids(matched_items)
                    allowed_numeric_ids.update(matched_ids)
                    result["matched_tender_fact_spans"].append(
                        {
                            "numeric_span": candidate_context["numeric_span"],
                            "full_sentence": candidate_context["full_sentence"],
                            "normalized_sentence": candidate_context[
                                "normalized_sentence"
                            ],
                            "section_id": candidate_context["section_id"],
                            "matched_tender_fact_ids": sorted(matched_ids),
                            "allowed_spans": [numeric_match.group(0)],
                            "blocked_spans": [],
                        }
                    )
                    continue
                blocked_numeric_spans.append(numeric_match.group(0))
            for blocked_span in blocked_numeric_spans:
                result["unauthorized_numeric_claims"].append(
                    _permission_finding(
                        section_id,
                        section_title,
                        "numeric_commitment",
                        sentence,
                        numeric_text=re.sub(r"\s+", "", blocked_span),
                        offending_sentence=sentence,
                        offending_span=blocked_span,
                        full_sentence=sentence,
                        normalized_sentence=_normalize_numeric_sentence(sentence),
                        matched_tender_fact_ids=sorted(allowed_numeric_ids),
                        allowed_spans=allowed_numeric_spans,
                        blocked_spans=[blocked_span],
                    )
                )
            obligation_debug = _audit_tender_obligation_spans(
                sentence,
                claims=claims,
                permissions=permissions,
            )
            if obligation_debug["matched_tender_fact_ids"]:
                result["matched_tender_fact_spans"].append(
                    {
                        "section_id": section_id or None,
                        "full_sentence": sentence,
                        "normalized_sentence": _normalize_numeric_sentence(sentence),
                        "matched_tender_fact_ids": obligation_debug[
                            "matched_tender_fact_ids"
                        ],
                        "allowed_spans": obligation_debug["allowed_spans"],
                        "blocked_spans": obligation_debug["blocked_spans"],
                    }
                )
            for blocked_span in obligation_debug["blocked_spans"]:
                result["unauthorized_bidder_commitments"].append(
                    _permission_finding(
                        section_id,
                        section_title,
                        "unconfirmed_bidder_commitment",
                        sentence,
                        offending_sentence=sentence,
                        offending_span=blocked_span,
                        matched_tender_fact_ids=obligation_debug[
                            "matched_tender_fact_ids"
                        ],
                        allowed_spans=obligation_debug["allowed_spans"],
                        blocked_spans=[blocked_span],
                    )
                )
        for error in boundary_errors:
            parts = str(error).split(":", 3)
            if len(parts) < 4:
                continue
            code = parts[2]
            excerpt = parts[3]
            source_sentence = next(
                (
                    sentence
                    for sentence in sentences
                    if _normalize_fact_text(excerpt)
                    in _normalize_fact_text(sentence)
                ),
                excerpt,
            )
            span = _fact_boundary_offending_span(code, source_sentence) or excerpt
            numeric_debug = _allowed_numeric_debug_for_sentence(
                source_sentence,
                claims=claims,
                permissions=permissions,
            )
            finding = _permission_finding(
                section_id,
                section_title,
                code,
                source_sentence,
                offending_sentence=source_sentence,
                offending_span=span,
                matched_tender_fact_ids=numeric_debug[
                    "matched_tender_fact_ids"
                ],
                allowed_spans=numeric_debug["allowed_spans"],
                blocked_spans=[span],
            )
            if code == "unconfirmed_product_capability":
                result["unauthorized_product_capabilities"].append(finding)
            elif code == "candidate_promoted_to_fact":
                result["candidate_facts_promoted"].append(finding)
            elif code in {
                "unconfirmed_staffing_assignment",
                "unconfirmed_resource_commitment",
            }:
                result["unauthorized_personnel_claims"].append(finding)
            elif code == "schedule_frequency":
                result["unauthorized_numeric_claims"].append(finding)
    for key in (
        "unauthorized_numeric_claims",
        "unauthorized_personnel_claims",
        "unauthorized_product_capabilities",
        "candidate_facts_promoted",
        "unregistered_placeholders",
        "unauthorized_bidder_commitments",
    ):
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in result[key]:
            unique[
                (
                    str(item.get("section_id") or item.get("section_title") or ""),
                    str(item.get("code") or ""),
                    _normalize_fact_text(
                        str(item.get("offending_span") or item.get("text") or "")
                    ),
                )
            ] = item
        result[key] = list(unique.values())
    unique_matches: dict[str, dict[str, Any]] = {}
    for item in result["matched_tender_fact_spans"]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        unique_matches[key] = item
    result["matched_tender_fact_spans"] = list(unique_matches.values())
    result["passed"] = not any(
        result[key]
        for key in (
            "unauthorized_numeric_claims",
            "unauthorized_personnel_claims",
            "unauthorized_product_capabilities",
            "candidate_facts_promoted",
            "unregistered_placeholders",
            "unauthorized_bidder_commitments",
        )
    )
    return result


def _resolve_permission_entry_for_section(
    section: dict[str, Any],
    *,
    section_index: int,
    fact_ledger: list[dict[str, Any]],
    ledger_by_reference: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    explicit_id = str(section.get("section_id") or "")
    if explicit_id and explicit_id in ledger_by_reference:
        return ledger_by_reference[explicit_id]
    title = str(section.get("heading") or "")
    title_matches = [
        item
        for item in fact_ledger
        if isinstance(item, dict)
        and str(item.get("section_title") or "") == title
    ]
    if len(title_matches) == 1:
        return title_matches[0]
    if (
        0 <= section_index < len(fact_ledger)
        and isinstance(fact_ledger[section_index], dict)
        and str(fact_ledger[section_index].get("section_title") or "") == title
    ):
        return fact_ledger[section_index]
    return {}


def generation_permission_audit_errors(audit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "unauthorized_numeric_claims",
        "unauthorized_personnel_claims",
        "unauthorized_product_capabilities",
        "candidate_facts_promoted",
        "unregistered_placeholders",
        "unauthorized_bidder_commitments",
    ):
        for item in audit.get(key) or []:
            prefix = (
                "placeholder"
                if item.get("code") == "unregistered_placeholder"
                else "fact_boundary"
            )
            errors.append(
                f"{prefix}:{item.get('section_title') or '相关小节'}:"
                f"{item.get('code') or 'generation_permission_violation'}:"
                f"{str(item.get('offending_span') or item.get('text') or '')[:100]}"
            )
    return list(dict.fromkeys(errors))


def _permission_finding(
    section_id: str,
    section_title: str,
    code: str,
    text: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "section_id": section_id or None,
        "section_title": section_title or None,
        "code": code,
        "text": re.sub(r"\s+", " ", str(text or "")).strip()[:200],
        **details,
    }


def _sentence_has_allowed_confirmed_evidence(
    sentence: str,
    claims: list[dict[str, Any]],
    allowed_evidence_ids: set[str],
) -> bool:
    compact = _normalize_fact_text(sentence)
    for claim in claims:
        claim_text = _normalize_fact_text(str(claim.get("text") or ""))
        if not claim_text or not (claim_text in compact or compact in claim_text):
            continue
        evidence_ids = {
            str(value) for value in claim.get("evidence_ids") or []
        }
        if evidence_ids and evidence_ids <= allowed_evidence_ids:
            return True
    return False


def _numeric_claim_is_permitted(
    sentence: str,
    numeric_text: str,
    *,
    claims: list[dict[str, Any]],
    permissions: dict[str, Any],
    numeric_start: int | None = None,
    candidate_context: dict[str, Any] | None = None,
) -> bool:
    return bool(
        _matching_numeric_permission_items(
            sentence,
            numeric_text,
            claims=claims,
            permissions=permissions,
            numeric_start=numeric_start,
            candidate_context=candidate_context,
        )
    )


def _matching_numeric_permission_items(
    sentence: str,
    numeric_text: str,
    *,
    claims: list[dict[str, Any]],
    permissions: dict[str, Any],
    numeric_start: int | None = None,
    candidate_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if candidate_context:
        sentence = str(candidate_context.get("full_sentence") or sentence)
        numeric_text = str(candidate_context.get("numeric_span") or numeric_text)
        numeric_start = int(
            candidate_context.get("numeric_start")
            if candidate_context.get("numeric_start") is not None
            else numeric_start or 0
        )
    numeric_tokens = _commitment_tokens(numeric_text)
    if not numeric_tokens:
        return []
    if numeric_start is None:
        numeric_start = max(0, str(sentence).find(str(numeric_text)))
    sentence_kinds = _numeric_semantic_kinds(
        sentence,
        numeric_start,
        numeric_start + len(str(numeric_text)),
    )
    effective_tender_numeric = list(
        permissions.get("effective_tender_numeric_facts")
        or permissions.get("allowed_tender_numeric_facts")
        or []
    )
    allowed_items = [
        item
        for item in [
            *list(permissions.get("allowed_confirmed_numeric_facts") or []),
            *effective_tender_numeric,
        ]
        if isinstance(item, dict)
    ]
    matched_items: list[dict[str, Any]] = []
    for item in allowed_items:
        if not numeric_tokens <= _commitment_tokens(
            str(item.get("text") or item.get("value") or "")
            + str(item.get("unit") or "")
        ):
            continue
        allowed_source_ids = {
            str(value)
            for value in (
                list(item.get("source_ids") or [])
                or [item.get("source_id")]
            )
            if value
        }
        allowed_value = str(item.get("text") or "")
        allowed_numeric = next(
            (
                match
                for match in _commitment_numeric_matches(allowed_value)
                if numeric_tokens <= _commitment_tokens(match.group(0))
            ),
            None,
        )
        if allowed_numeric is None:
            continue
        allowed_kinds = _numeric_semantic_kinds(
            allowed_value,
            allowed_numeric.start(),
            allowed_numeric.end(),
        )
        if sentence_kinds and not (sentence_kinds & allowed_kinds):
            continue
        if not _tender_time_origin_is_compatible(sentence, item):
            continue
        if not _numeric_completion_is_compatible(
            sentence,
            numeric_start,
            numeric_start + len(str(numeric_text)),
            item,
        ):
            continue
        # The effective permission itself is the trusted source link.  Candidate
        # JSON is not required to duplicate it as a tender_fact claim after the
        # value, unit, origin and completion object have all matched.
        matched_items.append(item)
    return _unique_permission_items(matched_items)


def _normalize_numeric_sentence(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(
        r"(?<=[0-9])\s+(?=(?:个)?(?:工作日|小时|分钟|人天|个月|日|天|周|月|年))",
        "",
        normalized,
    )


def _numeric_candidate_context(
    sentence: str,
    numeric_span: str,
    *,
    numeric_start: int,
    section_id: str,
    permissions: dict[str, Any],
) -> dict[str, Any]:
    effective = list(
        permissions.get("effective_tender_numeric_facts")
        or permissions.get("allowed_tender_numeric_facts")
        or []
    )
    return {
        "numeric_span": str(numeric_span),
        "numeric_start": int(numeric_start),
        "full_sentence": str(sentence),
        "normalized_sentence": _normalize_numeric_sentence(sentence),
        "section_id": section_id or None,
        "effective_tender_numeric_facts": effective,
    }


def _numeric_completion_is_compatible(
    sentence: str,
    start: int,
    end: int,
    permission: dict[str, Any],
) -> bool:
    allowed_text = str(permission.get("text") or "")
    sentence_text = str(sentence or "")
    sentence_clause = _numeric_local_clause(sentence_text, start, end)
    allowed_match = next(iter(_commitment_numeric_matches(allowed_text)), None)
    allowed_clause = (
        _numeric_local_clause(
            allowed_text,
            allowed_match.start(),
            allowed_match.end(),
        )
        if allowed_match
        else allowed_text
    )
    sentence_objects = _completion_object_kinds(sentence_clause)
    allowed_objects = _completion_object_kinds(allowed_clause)
    if not sentence_objects or not allowed_objects:
        return True
    return sentence_objects <= allowed_objects


def _numeric_local_clause(value: str, start: int, end: int) -> str:
    left = max(
        [str(value).rfind(separator, 0, start) for separator in (",", "，", "；", ";", "。", "\n")]
    )
    rights = [
        position
        for separator in (",", "，", "；", ";", "。", "\n")
        for position in [str(value).find(separator, end)]
        if position >= 0
    ]
    right = min(rights) if rights else len(str(value))
    for other in _commitment_numeric_matches(value):
        if other.end() <= start:
            left = max(left, (other.end() + start) // 2)
        elif other.start() >= end:
            right = min(right, (end + other.start()) // 2)
            break
    return str(value)[left + 1 : right]


def _completion_object_kinds(value: str) -> set[str]:
    patterns = {
        "delivery": r"(?:交货|到货|货到|交付)",
        "installation": r"安装",
        "commissioning": r"调试|联调",
        "trial_run": r"试运行",
        "test": r"测试",
        "rectification": r"整改|纠正",
        "report": r"报告|通报|资料提交",
        "training": r"(?:培训|知识转移|操作转移)",
        "acceptance": r"验收",
        "service_response": r"(?:技术|故障|服务)响应|到场|维修",
        "warranty": r"保修|质保",
    }
    return {
        kind
        for kind, pattern in patterns.items()
        if re.search(pattern, str(value or ""), re.I)
    }


def _tender_time_origin_is_compatible(
    sentence: str,
    permission: dict[str, Any],
) -> bool:
    """Keep a permitted number attached to its tender time origin.

    A source-aware referential phrase (for example “按招标规定的期限”) may
    stand in for repeating the complete origin, but an unrelated event may not.
    """
    origin = str(permission.get("time_origin") or "")
    allowed_text = str(permission.get("text") or "")
    if not origin:
        return True
    sentence_text = unicodedata.normalize("NFKC", str(sentence or ""))
    origin_groups = {
        "contract_signed": r"(?:合同[^，。；;\n]{0,8}(?:签订|生效)|(?:签订|生效)[^，。；;\n]{0,8}合同)",
        "goods_arrived": r"(?:货到|到货)",
        "acceptance": r"验收",
        "notice": r"(?:通知|告知)",
    }
    allowed_origin_kinds = {
        key
        for key, pattern in origin_groups.items()
        if re.search(pattern, f"{origin} {allowed_text}")
    }
    if not allowed_origin_kinds:
        return _normalize_fact_text(origin) in _normalize_fact_text(
            sentence_text
        )
    sentence_origin_kinds = {
        key
        for key, pattern in origin_groups.items()
        if re.search(pattern, sentence_text)
    }
    if sentence_origin_kinds:
        return bool(allowed_origin_kinds & sentence_origin_kinds)
    return bool(
        re.search(
            r"(?:按|按照|依据|根据)(?:招标|采购|合同)[^，。；;\n]{0,18}"
            r"(?:规定|要求|约定|期限|时限)|(?:该|上述|前述)(?:期限|时限)",
            sentence_text,
        )
    )


def _permissions_for_section(
    fact_ledger: list[dict[str, Any]],
    *,
    section_id: str,
    section_title: str,
) -> dict[str, Any]:
    for entry in fact_ledger:
        if not isinstance(entry, dict):
            continue
        if (
            section_id and section_id == str(entry.get("section_id") or "")
        ) or (
            section_title
            and section_title == str(entry.get("section_title") or "")
        ):
            permissions = dict(entry.get("generation_permissions") or {})
            permissions["allowed_tender_facts"] = _unique_permission_items(
                [
                    *list(permissions.get("allowed_tender_facts") or []),
                    *list(
                        permissions.get("chapter_allowed_tender_facts")
                        or entry.get("chapter_allowed_tender_facts")
                        or []
                    ),
                ]
            )
            permissions["allowed_tender_numeric_facts"] = (
                _unique_permission_items(
                    [
                        *list(
                            permissions.get("allowed_tender_numeric_facts")
                            or []
                        ),
                        *list(
                            permissions.get(
                                "chapter_allowed_tender_numeric_facts"
                            )
                            or entry.get(
                                "chapter_allowed_tender_numeric_facts"
                            )
                            or []
                        ),
                    ]
                )
            )
            permissions["effective_tender_facts"] = list(
                permissions["allowed_tender_facts"]
            )
            permissions["effective_tender_numeric_facts"] = list(
                permissions["allowed_tender_numeric_facts"]
            )
            return permissions
    return {}


def _unique_permission_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        unique[key] = item
    return list(unique.values())


def _permission_source_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(value)
        for item in items
        if isinstance(item, dict)
        for value in (
            list(item.get("source_ids") or [])
            or [item.get("source_id") or item.get("requirement_id")]
        )
        if value
    }


def _allowed_numeric_debug_for_sentence(
    sentence: str,
    *,
    claims: list[dict[str, Any]],
    permissions: dict[str, Any],
) -> dict[str, list[str]]:
    ids: set[str] = set()
    spans: list[str] = []
    for numeric in _commitment_numeric_matches(sentence):
        matched = _matching_numeric_permission_items(
            sentence,
            numeric.group(0),
            claims=claims,
            permissions=permissions,
            numeric_start=numeric.start(),
        )
        if matched:
            spans.append(numeric.group(0))
            ids.update(_permission_source_ids(matched))
    return {
        "matched_tender_fact_ids": sorted(ids),
        "allowed_spans": list(dict.fromkeys(spans)),
    }


def _fact_boundary_offending_span(code: str, sentence: str) -> str:
    patterns = {
        "unconfirmed_resource_commitment": (
            r"(?:调配后备资源|后备资源|备份人员|备选人员|候补人员|"
            r"双人复核|两人复核|另一名工程师|人员替换[^，。；;\n]{0,20}|"
            r"资源池[^，。；;\n]{0,24}|"
            r"(?:获得|提供|确保|保障)[^，。；;\n]{0,20}"
            r"(?:技术与管理资源|技术资源|管理资源)[^，。；;\n]{0,12}(?:保障|支持)?)"
        ),
        "unconfirmed_staffing_assignment": (
            r"(?:双人复核|两人复核|另一名工程师|"
            r"质量保障工程师[^，。；;\n]{0,16}复核|"
            r"(?:项目组织)?采用[^，。；;\n]{0,24}矩阵式(?:组织)?结构|"
            r"(?:项目组织)?(?:将)?设立[^。；;\n]{0,64}(?:组|部门)|"
            r"质量保障组[^。；;\n]{0,32}独立于[^。；;\n]{0,24}(?:组|部门))"
        ),
        "unconfirmed_personnel_qualification": (
            r"(?:具有|具备|熟悉|精通|持有|取得)[^，。；;\n]{0,48}"
        ),
        "unconfirmed_product_capability": (
            r"(?:支持|提供|采用|实现|确保|具备|配置)[^，。；;\n]{0,70}"
        ),
        "candidate_promoted_to_fact": (
            r"(?:已通过|已取得|已获得|具备|持有)[^，。；;\n]{0,70}"
        ),
    }
    pattern = patterns.get(code)
    match = re.search(pattern, str(sentence or ""), re.I) if pattern else None
    return match.group(0).strip() if match else ""


def _audit_tender_obligation_spans(
    sentence: str,
    *,
    claims: list[dict[str, Any]],
    permissions: dict[str, Any],
) -> dict[str, list[str]]:
    if _is_neutral_methodology_statement(sentence):
        return {
            "matched_tender_fact_ids": [],
            "allowed_spans": [],
            "blocked_spans": [],
        }
    sentence_semantics = _obligation_semantics(sentence)
    matched_items: list[dict[str, Any]] = []
    for item in (
        permissions.get("effective_tender_facts")
        or permissions.get("allowed_tender_facts")
        or []
    ):
        if not isinstance(item, dict):
            continue
        if str(item.get("usage") or "") not in {
            "no_deviation_response",
            "tender_requirement",
            "",
        }:
            continue
        item_semantics = set(item.get("business_semantics") or []) | _obligation_semantics(
            str(item.get("text") or "")
        )
        specific_semantics = sentence_semantics & {
            "delivery_location",
            "random_documents",
            "training",
            "acceptance",
            "service_response",
            "warranty",
        }
        if specific_semantics:
            semantic_match = bool(specific_semantics & item_semantics)
        else:
            semantic_match = bool(sentence_semantics & item_semantics)
        if not semantic_match:
            continue
        source_ids = _permission_source_ids([item])
        if not source_ids:
            continue
        matched_items.append(item)
    allowed_text = " ".join(str(item.get("text") or "") for item in matched_items)
    allowed_document_items = [
        obligation
        for item in matched_items
        for obligation in (
            list(item.get("obligation_items") or [])
            or extract_tender_obligation_items(
                str(item.get("text") or ""),
                source_id=str(item.get("source_id") or "") or None,
                requirement_id=str(item.get("requirement_id") or "") or None,
                deviation_status=str(item.get("deviation_status") or "") or None,
            )
        )
        if isinstance(obligation, dict)
    ]
    candidate_document_items = (
        extract_tender_obligation_items(sentence)
        if _is_document_obligation_statement(sentence)
        else []
    )
    allowed_by_object: dict[str, list[dict[str, Any]]] = {}
    for item in allowed_document_items:
        allowed_by_object.setdefault(
            str(item.get("normalized_object") or ""), []
        ).append(item)
    allowed_spans: list[str] = []
    blocked_spans: list[str] = []
    concrete_candidate_objects = {
        str(item.get("normalized_object") or "")
        for item in candidate_document_items
        if str(item.get("normalized_object") or "") != "随机资料"
    }
    for candidate in candidate_document_items:
        normalized_object = str(candidate.get("normalized_object") or "")
        actual_span = str(candidate.get("text") or "")
        if normalized_object and normalized_object in actual_span:
            actual_span = normalized_object
        umbrella_is_only_a_label = (
            normalized_object == "随机资料"
            and bool(concrete_candidate_objects)
            and concrete_candidate_objects <= set(allowed_by_object)
        )
        if normalized_object in allowed_by_object or umbrella_is_only_a_label:
            allowed_spans.append(actual_span)
        else:
            blocked_spans.append(actual_span)
    location_matches = [
        match.group(0)
        for match in re.finditer(
            r"(?:交付|交货|送货|到货)[^，。；;\n]{0,24}"
            r"(?:采购人)?指定(?:地点|地址|场所)|"
            r"(?:交付|交货|送货|到货)[^，。；;\n]{0,16}(?:地点|地址|场所)",
            sentence,
        )
    ]
    location_items = [
        item
        for item in matched_items
        if "delivery_location"
        in (
            set(item.get("business_semantics") or [])
            | _obligation_semantics(str(item.get("text") or ""))
        )
    ]
    location_permitted = bool(location_items)
    if location_permitted:
        allowed_spans.extend(location_matches)
    else:
        blocked_spans.extend(location_matches)
    generic_terms = {
        "training": r"(?:培训|知识转移|操作转移)",
        "acceptance": r"验收",
        "service_response": r"(?:技术响应|故障响应|服务响应|到场|维修)",
        "warranty": r"(?:保修|质保|售后服务)",
    }
    allowed_semantics = _obligation_semantics(allowed_text)
    for semantic, pattern in generic_terms.items():
        if semantic not in sentence_semantics:
            continue
        actual_spans = [
            match.group(0) for match in re.finditer(pattern, sentence)
        ]
        if not actual_spans:
            continue
        if semantic in allowed_semantics:
            allowed_spans.extend(actual_spans)
        else:
            blocked_spans.extend(actual_spans)
    if "包括但不限于" in sentence:
        blocked_spans.append("包括但不限于")
    guarantee_patterns = (
        r"确保采购人[^，。；;\n]{0,40}(?:独立|能够)[^，。；;\n]{0,30}(?:维护|运维|操作)",
        r"保证采购人[^，。；;\n]{0,40}(?:独立|能够)[^，。；;\n]{0,30}(?:维护|运维|操作)",
    )
    for pattern in guarantee_patterns:
        blocked_spans.extend(
            match.group(0) for match in re.finditer(pattern, sentence)
        )
    blocked_spans.extend(_extra_bidder_commitment_spans(sentence))
    matched_document_sources = {
        str(source_id)
        for candidate in candidate_document_items
        for item in allowed_by_object.get(
            str(candidate.get("normalized_object") or ""), []
        )
        for source_id in (
            list(item.get("source_ids") or [])
            or [item.get("source_id") or item.get("requirement_id")]
        )
        if source_id
    }
    matched_sources = matched_document_sources | _permission_source_ids(
        location_items
    )
    if not candidate_document_items and not location_matches:
        matched_sources.update(_permission_source_ids(matched_items))
    return {
        "matched_tender_fact_ids": sorted(matched_sources),
        "allowed_spans": list(dict.fromkeys(allowed_spans)),
        "blocked_spans": list(dict.fromkeys(blocked_spans)),
    }


def _is_neutral_methodology_statement(sentence: str) -> bool:
    """Keep process descriptions out of bidder-fact commitment enforcement."""
    text = str(sentence or "")
    if not re.search(r"(?:流程|过程|记录|核验|核对|部署|测试|验收|计划|安排)", text):
        return False
    commitment_text = re.sub(
        r"(?:不|不得|不应|无权)[^，。；;\n]{0,16}(?:承诺|保证|确保)",
        "",
        text,
    )
    if re.search(r"(?:承诺|保证|确保)", commitment_text):
        return False
    if re.search(
        r"(?:我方|本公司|投标人).{0,24}(?:将提供|完成|解决|配置|投入|具备|拥有)",
        text,
    ):
        return False
    return True


def _commitment_numeric_matches(value: str) -> list[re.Match[str]]:
    return list(
        re.finditer(
            r"(?:7\s*[×xX*]\s*24\s*小时|"
            r"\d+(?:\.\d+)?\s*(?:个)?(?:分钟|小时|工作日|日|天|周|个月|月|年))",
            unicodedata.normalize("NFKC", str(value or "")),
            flags=re.I,
        )
    )


_NUMERIC_SEMANTIC_GROUPS: dict[str, tuple[str, ...]] = {
    "delivery": ("货到", "到货", "交货", "交付"),
    "installation": ("安装", "完成安装"),
    "on_site": ("到场", "抵达现场", "维修工程师"),
    "response": ("技术响应", "故障响应", "响应"),
    "warranty": ("保修", "质保", "保修期"),
    "rectification_or_report": ("整改", "整改报告", "提交报告", "通报", "报告"),
    "trial_run": ("试运行",),
    "training": ("培训",),
    "personnel": ("项目经理", "人员", "工程师", "团队"),
}


def _numeric_semantic_kinds(
    value: str,
    start: int,
    end: int,
) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    left = max(
        [text.rfind(separator, 0, start) for separator in (",", "，", "；", ";", "。", "\n")]
    )
    right_candidates = [
        position
        for separator in (",", "，", "；", ";", "。", "\n")
        for position in [text.find(separator, end)]
        if position >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    clause = text[left + 1 : right]
    local_start = start - left - 1
    center = local_start + max(1, end - start) / 2
    distances: dict[str, float] = {}
    for kind, terms in _NUMERIC_SEMANTIC_GROUPS.items():
        positions = [
            match.start()
            for term in terms
            for match in re.finditer(re.escape(term), clause, flags=re.I)
        ]
        if positions:
            distances[kind] = min(abs(position - center) for position in positions)
    if not distances:
        return set()
    closest = min(distances.values())
    kinds = {
        kind
        for kind, distance in distances.items()
        if distance <= closest + 4
    }
    prefix = clause[:local_start]
    if re.search(r"安装[^,，。；;\n]{0,20}(?:时限|期限|完成|货到)", prefix):
        kinds.add("installation")
    return kinds


def _candidate_evidence_supports_sentence(
    sentence: str,
    claims: list[dict[str, Any]],
    candidate_evidence_ids: set[str],
) -> bool:
    if not candidate_evidence_ids:
        return False
    compact = _normalize_fact_text(sentence)
    for claim in claims:
        evidence_ids = {
            str(value) for value in claim.get("evidence_ids") or []
        }
        if not (evidence_ids & candidate_evidence_ids):
            continue
        claim_text = _normalize_fact_text(str(claim.get("text") or ""))
        if claim_text and (claim_text in compact or compact in claim_text):
            return True
    if re.search(
        r"(?:我方|我公司|本公司|公司)[^，。；;\n]{0,48}"
        r"(?:已通过|已取得|已获得|具备|持有|符合)"
        r"[^，。；;\n]{0,48}(?:ISO\s*\d+|认证|证书|资质|体系|业绩)",
        sentence,
        re.I,
    ):
        return True
    return False


def _tender_personnel_statement_is_permitted(
    sentence: str,
    *,
    claims: list[dict[str, Any]],
    permissions: dict[str, Any],
) -> bool:
    """Allow only source-linked tender personnel wording, not invented prose."""
    sentence_text = _normalize_fact_text(sentence)
    if not sentence_text:
        return False
    for item in (
        permissions.get("effective_tender_facts")
        or permissions.get("allowed_tender_facts")
        or []
    ):
        if not isinstance(item, dict):
            continue
        allowed_text = str(item.get("text") or "")
        semantics = set(item.get("business_semantics") or [])
        if "personnel_requirement" not in semantics and not re.search(
            r"(?:项目经理|项目负责人|技术负责人|实施工程师|人员简历|人员配置|人员表)",
            allowed_text,
        ):
            continue
        allowed_normalized = _normalize_fact_text(allowed_text)
        if not (
            sentence_text in allowed_normalized
            or allowed_normalized in sentence_text
        ):
            continue
        source_ids = {
            str(value)
            for value in (
                list(item.get("source_ids") or [])
                or [item.get("source_id") or item.get("requirement_id")]
            )
            if value
        }
        if source_ids and any(
            str(claim.get("claim_type") or "") == "tender_fact"
            and source_ids
            & {
                str(value)
                for value in [
                    *list(claim.get("source_ids") or []),
                    *list(claim.get("evidence_ids") or []),
                ]
                if value
            }
            for claim in claims
        ):
            return True
    return False


def _has_extra_bidder_commitment(sentence: str) -> bool:
    return bool(_extra_bidder_commitment_spans(sentence))


def _extra_bidder_commitment_spans(sentence: str) -> list[str]:
    pattern = re.compile(
        r"(?:尽力|争取)?提前完成|"
        r"无条件整改直至满意|"
        r"额外免费提供(?:设备|服务|资源)?|"
        r"(?:\d+|[一二三四五六七八九十]+)个?工作日内(?:补齐|提交|整改)|"
        r"额外提供(?:设备|服务|资源)"
    )
    return list(
        dict.fromkeys(
            match.group(0) for match in pattern.finditer(str(sentence or ""))
        )
    )


def _tender_obligation_permits_statement(
    sentence: str,
    *,
    claims: list[dict[str, Any]],
    permissions: dict[str, Any],
) -> bool:
    audit = _audit_tender_obligation_spans(
        sentence,
        claims=claims,
        permissions=permissions,
    )
    return bool(audit["matched_tender_fact_ids"]) and not bool(
        audit["blocked_spans"]
    )


def _obligation_semantics(value: str) -> set[str]:
    text = str(value or "")
    patterns = {
        "delivery_location": r"(?:交付|交货|送货|到货).{0,16}(?:地点|地址|场所)",
        "random_documents": r"(?:随机资料|随机文件|[一-鿿]{1,12}(?:手册|指南|说明书|证书|合格证|参数表|报告|许可文件|记录|文档))",
        "training": r"(?:培训|知识转移|操作转移)",
        "acceptance": r"验收",
        "service_response": r"(?:技术响应|故障响应|到场|维修工程师|服务响应)",
        "warranty": r"(?:保修|质保|售后服务)",
        "delivery": r"(?:货到|到货|交货|交付)",
        "installation": r"安装",
    }
    return {
        semantic
        for semantic, pattern in patterns.items()
        if re.search(pattern, text, re.I)
    }


def extract_tender_obligation_items(
    text: str,
    *,
    source_id: str | None = None,
    requirement_id: str | None = None,
    deviation_status: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministically split document obligations into source-linked items."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    items: list[dict[str, Any]] = []
    suffix_pattern = r"(?:手册|指南|说明书|证书|合格证|参数表|报告|许可文件|资料|文件|记录|文档)"
    for segment in re.split(r"[，,、；;。\n]", value):
        segment = segment.strip()
        if not segment:
            continue
        shared_suffix = re.search(
            rf"(?P<left>[一-鿿]{{1,8}}?)(?:及|和)(?P<right>[一-鿿]{{1,8}}?)(?P<suffix>手册|指南|说明书)",
            segment,
        )
        if shared_suffix and not re.search(
            suffix_pattern, shared_suffix.group("left")
        ):
            for raw_object in (
                shared_suffix.group("left") + shared_suffix.group("suffix"),
                shared_suffix.group("right") + shared_suffix.group("suffix"),
            ):
                normalized_object = _clean_document_object(raw_object)
                if normalized_object:
                    items.append(
                        _document_obligation_item(
                            text=normalized_object,
                            normalized_object=normalized_object,
                            source_id=source_id,
                            requirement_id=requirement_id,
                            deviation_status=deviation_status,
                        )
                    )
            segment = segment.replace(shared_suffix.group(0), "")
        pieces = re.split(r"(?:以及|和|与|及)", segment)
        for piece in pieces:
            normalized_object = _clean_document_object(piece)
            if not re.search(rf"{suffix_pattern}$", normalized_object):
                continue
            if normalized_object in {"招标文件", "采购文件", "投标文件", "合同文件"}:
                continue
            items.append(
                _document_obligation_item(
                    text=normalized_object,
                    normalized_object=normalized_object,
                    source_id=source_id,
                    requirement_id=requirement_id,
                    deviation_status=deviation_status,
                )
            )
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        unique[
            (
                str(item.get("text") or ""),
                str(item.get("normalized_object") or ""),
            )
        ] = item
    return list(unique.values())


def _is_document_obligation_statement(value: str) -> bool:
    text = str(value or "")
    return bool(
        re.search(
            r"(?:我方|本公司|投标人|供应商|招标要求|采购要求)"
            r"[^。；;]{0,32}(?:提供|提交|交付|随附|包括)|"
            r"(?:本项目)?交付成果[^。；;]{0,20}(?:包括|含有)|"
            r"(?:提供|提交|交付|随货|随机资料)[^。；;]{0,36}"
            r"(?:手册|指南|说明书|证书|合格证|参数表|报告|资料|文件|记录|文档)",
            text,
        )
    )


def _clean_document_object(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    text = re.sub(
        r"^.*(?:提供|提交|交付|包括|含有|应|须|需)",
        "",
        text,
    )
    text = re.sub(r"^.*(?:明确要求的|按规定形成的|形成的)", "", text)
    text = re.sub(r"^(?:和|及|与|以及)", "", text)
    text = re.sub(r"^(?:(?:制造商|厂家|相关|配套|额外|免费|完整的|完整))+的?", "", text)
    text = re.sub(r"等$", "", text)
    text = text.replace("用户手册", "使用手册").replace("维护手册", "维修手册")
    return text


def _document_obligation_item(
    *,
    text: str,
    normalized_object: str,
    source_id: str | None,
    requirement_id: str | None,
    deviation_status: str | None,
) -> dict[str, Any]:
    return {
        "text": text,
        "normalized_object": _normalize_fact_text(normalized_object),
        "obligation_type": "required_document",
        "source_id": source_id,
        "requirement_id": requirement_id or source_id,
        "deviation_status": deviation_status,
    }


def json_safe_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            f"{key} {json_safe_text(item)}" for key, item in value.items()
        )
    if isinstance(value, list):
        return " ".join(json_safe_text(item) for item in value)
    return str(value or "")


def _sentences(content: str) -> list[str]:
    text = re.sub(r"(?m)^\s*(?:\|.*\||[-*+]\s+)", "", content)
    return [
        value.strip()
        for value in re.split(r"(?<=[。！？!?；;])|\n+", text)
        if value.strip()
    ]


def _is_cautious_or_placeholder(
    sentence: str,
    *,
    match: re.Match[str],
    claim_code: str,
) -> bool:
    prefix = sentence[: match.start()]
    local_prefix = prefix[-80:]
    conditional_patterns = (
        r"(?:若|如)[^，。；;]{1,40}(?:具备|满足|确认|核验|条件)",
        r"在[^，。；;]{1,35}(?:确认|核验|满足)[^，。；;]{0,10}(?:后|条件下)",
        r"根据[^，。；;]{1,24}(?:最终)?确认",
        r"以[^，。；;]{1,24}为准",
        r"建议[^，。；;]{0,24}$",
    )
    if any(re.search(pattern, local_prefix) for pattern in conditional_patterns):
        return True
    if "不构成承诺" in sentence:
        return True
    if "__PLACEHOLDER__" not in sentence:
        return False
    if claim_code != "unconfirmed_bidder_commitment":
        return False
    concrete = sentence.replace("__PLACEHOLDER__", "")
    return not re.search(
        r"(?:\d|高可用|业务不中断|国密|双因子认证|RBAC|多副本|"
        r"自动恢复|在线扩展|[\u4e00-\u9fff·]{2,4}负责本项目)",
        concrete,
        re.I,
    )


def _generic_candidate_binding(value: str) -> bool:
    return any(
        term in value
        for term in (
            "项目团队负责本项目",
            "项目人员负责本项目",
            "专人负责本项目",
            "项目经理负责项目",
            "技术负责人负责技术",
            "实施工程师负责设备",
        )
    )


def _placeholder_covers_personnel_identity(sentence: str) -> bool:
    placeholders = extract_placeholder_references(sentence)
    if not placeholders:
        return False
    normalized = sentence
    for placeholder in placeholders:
        normalized = normalized.replace(placeholder, "__PH__")
    return bool(
        re.search(
            r"(?:拟派)?(?:项目经理|项目负责人|技术负责人|团队成员|"
            r"实施工程师|人员规模)(?:为|包括|由)\s*__PH__",
            normalized,
        )
        or re.search(
            r"__PH__\s*(?:担任|负责)\s*(?:本项目|项目)",
            normalized,
        )
    )


def _placeholder_covers_personnel_fact(sentence: str) -> bool:
    placeholders = extract_placeholder_references(sentence)
    if not placeholders:
        return False
    normalized = sentence
    for placeholder in placeholders:
        normalized = normalized.replace(placeholder, "__PH__")
    asserted_patterns = (
        r"(?:具有|具备|从事|拥有)\s*(?!__PH__)[^，。；;]{0,12}\d+\s*年",
        r"(?:参与过|完成过|具有)\s*(?!__PH__)[^，。；;]{0,12}\d+\s*个",
        r"(?:持有|取得|具有)\s*(?!__PH__)[^，。；;]{0,24}(?:证书|资质|资格)",
    )
    if any(re.search(pattern, normalized) for pattern in asserted_patterns):
        return False
    return bool(
        re.search(r"__PH__\s*年", normalized)
        or re.search(r"__PH__\s*个", normalized)
        or re.search(r"(?:证书|资质|资格)[^，。；;]{0,12}(?:见|为)?\s*__PH__", normalized)
        or re.search(r"__PH__[^，。；;]{0,8}(?:证书|资质|资格)", normalized)
    )


def _is_personnel_fact_sentence(sentence: str) -> bool:
    return bool(
        re.search(
            r"(?:项目经理|负责人|拟派人员|工程师|人员|简历|工作年限|"
            r"项目管理年限|同类项目|证书|资质|资格|工作经历|项目经验)",
            sentence,
        )
    )


def _is_personnel_narrative_sentence(sentence: str) -> bool:
    if not _is_personnel_fact_sentence(sentence):
        return False
    normalized = re.sub(
        r"(?:项目经理|项目负责人|技术负责人|负责人|实施工程师|拟派人员|"
        r"团队成员|主要人员|人员|团队|简历)",
        "__ROLE__",
        sentence,
    )
    return "__ROLE__" in normalized and bool(
        re.search(
            r"(?:组织|设置|组建|安排|配置|负责|审核|检查|复核|归档|"
            r"具有|具备|熟悉|精通|持有|取得|投入|替换|调配|职责|汇报)",
            normalized,
        )
    )


def _is_generic_project_role_duty(sentence: str) -> bool:
    """Allow a role's neutral workflow action without asserting a person fact."""
    value = str(sentence or "")
    if not re.search(
        r"(?:项目经理|项目负责人|技术负责人|实施人员|实施工程师|"
        r"相关责任人|项目团队|技术团队)",
        value,
    ):
        return False
    if re.search(
        r"(?:姓名|担任|拟派|已确定|具有|具备|熟悉|精通|持有|取得|"
        r"经验|年限|证书|认证|资质|资格|资深|\d+\s*(?:人|名|年)|"
        r"[一二三四五六七八九十]+\s*(?:人|名|年)|备份人员|候补人员|"
        r"替换|全职|资源池|公司专家|随时调配|固定部门)",
        value,
    ):
        return False
    return bool(
        re.search(
            r"(?:负责|组织|审核|审查|归档|检查|复核|记录|开展|执行|"
            r"跟踪|协调|确认|提交|管理|指导|监测)[^，。；;\n]{0,40}",
            value,
        )
    )


def _specific_organization_boundary_code(sentence: str) -> str:
    value = str(sentence or "")
    if re.search(
        r"(?:项目组织)?采用[^，。；;\n]{0,24}矩阵式(?:组织)?结构|"
        r"(?:项目组织)?(?:将)?设立[^。；;\n]{0,64}(?:组|部门)|"
        r"质量保障组[^。；;\n]{0,32}独立于[^。；;\n]{0,24}(?:组|部门)",
        value,
    ):
        return "unconfirmed_staffing_assignment"
    if re.search(
        r"(?:获得|提供|确保|保障)[^，。；;\n]{0,20}"
        r"(?:技术与管理资源|技术资源|管理资源)[^，。；;\n]{0,12}(?:保障|支持)?",
        value,
    ):
        return "unconfirmed_resource_commitment"
    return ""


def _is_neutral_team_configuration_statement(
    sentence: str,
    *,
    registered_placeholder_ids: set[str],
) -> bool:
    value = str(sentence or "")
    if not re.search(r"(?:团队|人员|成员|岗位)", value):
        return False
    if re.search(
        r"(?:\d+\s*(?:人|名)|[一二三四五六七八九十]+\s*(?:人|名)|"
        r"具有|具备|熟悉|精通|经验|年限|证书|认证|资质|资格|资深|"
        r"已组建|固定部门|备份人员|候补人员|替换|全职|资源池|"
        r"公司专家|随时调配)",
        value,
    ):
        return False
    placeholder_ids = {
        reference.removeprefix("{{").removesuffix("}}")
        for reference in extract_placeholder_references(value)
        if reference.startswith("{{") and reference.endswith("}}")
    }
    deferred = bool(
        placeholder_ids
        and placeholder_ids <= registered_placeholder_ids
    ) or bool(
        re.search(
            r"(?:具体人员|成员清单|人员信息|岗位安排)[^。；;]{0,32}"
            r"(?:以确认后|以确认材料|详见|为准|配置表)",
            value,
        )
    )
    neutral_principle = bool(
        re.search(
            r"(?:专业分工|高效协作|协同实施|项目技术需求|实施复杂度|"
            r"按项目需求|依据项目需求|匹配团队|进行配置|多专业协同|"
            r"职责清晰|项目协调接口)",
            value,
        )
    )
    specific_organization = bool(
        re.search(
            r"(?:矩阵式(?:组织)?结构|设立[^。；;]{0,48}(?:组|部门)|"
            r"(?:组|部门)[^。；;]{0,24}独立于(?:组|部门)|"
            r"技术与管理资源保障|固定组织架构)",
            value,
        )
    )
    return neutral_principle and not specific_organization and (
        deferred or not re.search(r"(?:姓名|具体人数|已组建|现有部门)", value)
    )


def _schedule_frequency_is_permitted(
    sentence: str,
    frequency_span: str,
    *,
    permissions: dict[str, Any],
) -> bool:
    normalized_span = _normalize_fact_text(frequency_span)
    if not normalized_span:
        return False
    sentence_kinds = _numeric_semantic_kinds(
        sentence,
        max(0, sentence.find(frequency_span)),
        max(0, sentence.find(frequency_span)) + len(frequency_span),
    )
    for item in (
        permissions.get("effective_tender_facts")
        or permissions.get("allowed_tender_facts")
        or []
    ):
        if not isinstance(item, dict) or not _permission_source_ids([item]):
            continue
        allowed_text = str(item.get("text") or "")
        if normalized_span not in _normalize_fact_text(allowed_text):
            continue
        allowed_kinds = _numeric_semantic_kinds(
            allowed_text,
            max(0, allowed_text.find(frequency_span)),
            max(0, allowed_text.find(frequency_span)) + len(frequency_span),
        )
        if not sentence_kinds or not allowed_kinds or sentence_kinds & allowed_kinds:
            return True
    return False
    return bool(
        re.search(
            r"__ROLE__[^，。；;\n]{0,28}(?:负责|承担|组织|统筹|协调|管理|"
            r"审核|指导|配置|复核|汇报|隶属|投入|调配|替换|候补|"
            r"备份|拟派|担任|具有|具备|熟悉|精通|持有|取得|认证|经验|"
            r"年限|能力|技能)",
            normalized,
        )
        or re.search(r"__ROLE__(?:配置|安排)(?:为|包括|由|如下)", normalized)
    )


def _has_confirmed_personnel_context(confirmed_facts: dict[str, Any]) -> bool:
    return bool(
        re.search(
            r"(?:person|人员|项目经理|负责人|工作年限|项目管理年限|"
            r"同类项目|证书|简历|经验)",
            json_safe_text(confirmed_facts),
            re.I,
        )
    )


def _confirmed_personnel_fact_supports_sentence(
    sentence: str,
    confirmed_facts: dict[str, Any],
) -> bool:
    if not _is_personnel_fact_sentence(sentence):
        return False
    confirmed = json_safe_text(confirmed_facts)
    if not confirmed.strip():
        return False
    compact_sentence = _normalize_fact_text(sentence)
    compact_confirmed = _normalize_fact_text(confirmed)
    if compact_sentence and compact_sentence in compact_confirmed:
        return True
    required_tokens = _personnel_numeric_tokens(sentence)
    confirmed_tokens = _personnel_numeric_tokens(confirmed)
    if required_tokens and not required_tokens <= confirmed_tokens:
        return False
    if "同类项目" in sentence:
        return bool(required_tokens) and bool(
            re.search(r"(?:同类项目|similar_project)", confirmed, re.I)
        )
    if re.search(r"(?:工作|项目管理).{0,8}年限|年.{0,8}(?:经验|经历)", sentence):
        return bool(required_tokens) and bool(
            re.search(
                r"(?:工作|项目管理|经验|经历|work_years|management_years)",
                confirmed,
                re.I,
            )
        )
    certificate_terms = set(
        re.findall(
            r"\b[A-Z][A-Z0-9.+/-]{1,15}\b|"
            r"[\u4e00-\u9fff]{2,20}(?:证书|资格证|资质证)",
            sentence,
            flags=re.I,
        )
    )
    if certificate_terms:
        return all(
            _normalize_fact_text(term) in compact_confirmed
            for term in certificate_terms
        ) and (not required_tokens or required_tokens <= confirmed_tokens)
    if re.search(r"(?:另一名|两人|双人)", sentence):
        return bool(
            re.search(r"(?:另一名|两名|两人|双人|复核人员)", confirmed)
        )
    return False


def _personnel_numeric_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    tokens = {
        re.sub(r"\s+", "", item)
        for item in re.findall(
            r"\d+(?:\.\d+)?\s*(?:年|个月|月|个|人|名)",
            normalized,
        )
    }
    keyed_units = (
        (r"(?:work_years|management_years|工作年限|项目管理年限)"
         r"[^0-9]{0,12}(\d+(?:\.\d+)?)", "年"),
        (r"(?:similar_project_count|同类项目数量)"
         r"[^0-9]{0,12}(\d+)", "个"),
        (r"(?:staffing_count|人员数量)"
         r"[^0-9]{0,12}(\d+)", "人"),
        (r"(?:valid_until|expiry_date|有效期)"
         r"[^0-9]{0,12}(\d{4})", "年"),
    )
    for pattern, unit in keyed_units:
        tokens.update(
            f"{match}{unit}"
            for match in re.findall(pattern, normalized, flags=re.I)
        )
    return tokens


def _statement_is_substantiated(
    sentence: str,
    claims: list[dict[str, Any]],
    confirmed_text: str,
    *,
    candidate_evidence_ids: set[str],
    confirmed_evidence_ids: set[str],
) -> bool:
    compact = _normalize_fact_text(sentence)
    normalized_confirmed = _normalize_fact_text(confirmed_text)
    if compact and normalized_confirmed and (
        compact in normalized_confirmed or normalized_confirmed in compact
    ):
        return True
    capability_terms = (
        "在线扩展", "不中断业务", "业务不中断", "自动恢复", "多副本", "纠删码",
        "反亲和", "自动重启", "资源预留", "高可用", "堆叠", "mlag", "vpc",
        "分布式虚拟交换机", "ad", "备份系统", "容灾系统", "安全设备",
    )
    if any(
        _normalize_fact_text(term) in compact
        and _normalize_fact_text(term) in normalized_confirmed
        for term in capability_terms
    ):
        return True
    definitive_bidder_statement = bool(
        re.search(r"(?:我方|本公司)(?:郑重)?(?:承诺|保证|提供)", sentence)
        or re.search(r"保证在[^，。；;]{0,24}(?:内|前)", sentence)
    )
    for claim in claims:
        claim_text = _normalize_fact_text(str(claim.get("text") or ""))
        if not claim_text:
            continue
        overlap = claim_text in compact or compact in claim_text
        if not overlap:
            continue
        claim_type = str(claim.get("claim_type") or "")
        if (
            claim_type == "tender_fact"
            and claim.get("source_ids")
            and not definitive_bidder_statement
            and re.search(
                r"(?:招标文件|招标要求|采购要求|采购需求)"
                r"[^。！？；;\n]{0,24}(?:要求|规定|明确|应|须)",
                sentence,
            )
        ):
            return True
        if claim_type == "confirmed_enterprise_fact" and claim.get("evidence_ids"):
            evidence_ids = {
                str(value) for value in claim.get("evidence_ids") or []
            }
            if evidence_ids & candidate_evidence_ids:
                continue
            if evidence_ids & confirmed_evidence_ids:
                return True
        if (
            claim_type == "commitment_statement"
            and claim.get("deviation_status") == "positive_deviation_confirmed"
            and claim.get("source_ids")
            and not claim.get("human_review_required")
        ):
            return True
    return False


def _is_capability_test_or_source_boundary(sentence: str) -> bool:
    return bool(
        re.search(
            r"(?:(?:测试|试运行|验收|核验|验证)目标|"
            r"(?:测试|试运行|验收|核验|验证)[^。！？；;\n]{0,36}是否|"
            r"模拟故障[^。！？；;\n]{0,30}验证招标要求|"
            r"对确认后的技术应答项[^。！？；;\n]{0,20}(?:测试|核验))",
            sentence,
        )
        or re.search(
            r"以确认后的(?:技术应答表|产品配置|制造商资料)[^。！？；;\n]{0,16}为准",
            sentence,
        )
    )


def _is_document_or_process_outcome(sentence: str) -> bool:
    value = str(sentence or "")
    has_document_outcome = bool(
        re.search(
            r"(?:形成|输出|完成)[^。；;]{0,36}"
            r"(?:记录|清单|配置基线(?:文件)?|文档|报告|参考依据)|"
            r"(?:记录|清单|配置基线(?:文件)?|文档|报告)"
            r"[^。；;]{0,36}(?:用于|作为|便于|提供)(?:后续)?"
            r"(?:复核|运维管理|问题追踪|变更管理|参考)",
            value,
        )
    )
    if not has_document_outcome:
        return False
    return not bool(
        re.search(
            r"(?:自动故障切换|MLAG|在线扩容|在线扩展|业务不中断|纠删码|"
            r"多副本|反亲和|自动重启|自动恢复|吞吐|时延|性能指标|"
            r"协议|高可用|负载分担|链路冗余)",
            value,
            re.I,
        )
    )


def _is_design_principle_or_goal(sentence: str) -> bool:
    return bool(
        re.search(
            r"(?:以|围绕)[^。！？；;\n]{0,40}(?:为设计目标|目标进行方案设计)|"
            r"遵循[^。！？；;\n]{0,40}(?:原则|设计原则)",
            sentence,
        )
    )


def _exact_tender_required_commitment(
    sentence: str,
    claims: list[dict[str, Any]],
) -> bool:
    numeric_matches = _commitment_numeric_matches(sentence)
    if not numeric_matches:
        return False
    if re.search(
        r"(?:确保|保证)[^。！？；;\n]{0,24}(?:解决|修复|不中断|无感知|零故障)",
        sentence,
    ):
        return False
    allowed_tender_numeric_facts: list[dict[str, Any]] = []
    for claim in claims:
        if str(claim.get("claim_type") or "") != "tender_fact":
            continue
        if not claim.get("source_ids"):
            continue
        claim_text = str(claim.get("text") or "")
        policy = tender_response_policy(
            claim_text,
            source_id=str((claim.get("source_ids") or [""])[0]),
        )
        if not policy.get("default_no_deviation"):
            continue
        for required_match in _commitment_numeric_matches(claim_text):
            if re.fullmatch(
                r"7\s*[×xX*]\s*24\s*小时",
                required_match.group(0),
                flags=re.I,
            ):
                allowed_tender_numeric_facts.append(
                    {
                        "text": claim_text,
                        "value": "7×24",
                        "unit": "小时",
                        "source_id": str((claim.get("source_ids") or [""])[0]),
                        "fact_type": "tender_requirement",
                        "usage": "no_deviation_response",
                    }
                )
                continue
            comparable = _comparable_numeric_value(required_match.group(0))
            if comparable is None:
                continue
            number, dimension = comparable
            unit_match = re.search(
                r"(?:分钟|小时|工作日|日|天|周|个月|月|年)",
                required_match.group(0),
            )
            unit = unit_match.group(0) if unit_match else dimension
            allowed_tender_numeric_facts.append(
                {
                    "text": claim_text,
                    "value": number,
                    "unit": unit,
                    "source_id": str((claim.get("source_ids") or [""])[0]),
                    "fact_type": "tender_requirement",
                    "usage": "no_deviation_response",
                }
            )
    if not allowed_tender_numeric_facts:
        return False
    permissions = {
        "allowed_tender_numeric_facts": allowed_tender_numeric_facts,
    }
    return all(
        _numeric_claim_is_permitted(
            sentence,
            numeric.group(0),
            claims=claims,
            permissions=permissions,
            numeric_start=numeric.start(),
        )
        for numeric in numeric_matches
    )


def _exact_tender_required_service_level(
    sentence: str,
    claims: list[dict[str, Any]],
) -> bool:
    return _exact_tender_required_commitment(sentence, claims)


def _service_level_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).replace("x", "×").replace("X", "×")
    tokens = re.findall(
        r"(?:7\s*×\s*24\s*小时|"
        r"\d+\s*(?:分钟|小时|工作日|日|个月|月|年)"
        r"(?:内|到场|响应|保修|服务周期)?)",
        normalized,
        flags=re.I,
    )
    return {
        re.sub(r"\s+", "", token).lower()
        for token in tokens
    }


def _commitment_tokens(value: str) -> set[str]:
    normalized = (
        unicodedata.normalize("NFKC", str(value or ""))
        .replace("x", "×")
        .replace("X", "×")
    )
    tokens: set[str] = set()
    if re.search(r"7\s*×\s*24\s*小时", normalized, flags=re.I):
        tokens.add("7×24小时")
    for match in re.finditer(
        r"\d+(?:\.\d+)?\s*(?:个)?(?:分钟|小时|工作日|日|天|周|个月|月|年)",
        normalized,
        flags=re.I,
    ):
        comparable = _comparable_numeric_value(match.group(0))
        if comparable is None:
            continue
        number, dimension = comparable
        tokens.add(f"{number:g}:{dimension}")
    return tokens


def _comparable_numeric_value(value: str) -> tuple[float, str] | None:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:个)?(分钟|小时|工作日|日|天|周|个月|月|年)",
        normalized,
    )
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    factors = {
        "分钟": (1.0, "minutes"),
        "小时": (60.0, "minutes"),
        "工作日": (1.0, "working_days"),
        "日": (1.0, "calendar_days"),
        "天": (1.0, "calendar_days"),
        "周": (7.0, "calendar_days"),
        "个月": (1.0, "months"),
        "月": (1.0, "months"),
        "年": (12.0, "months"),
    }
    factor, dimension = factors[unit]
    return number * factor, dimension


def _numeric_interval(value: str) -> tuple[float, float, str] | None:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:至|到|[-~～])\s*"
        r"(\d+(?:\.\d+)?)\s*(分钟|小时|工作日|日|天|周|个月|月|年)",
        normalized,
    )
    if not match:
        return None
    low = _comparable_numeric_value(f"{match.group(1)}{match.group(3)}")
    high = _comparable_numeric_value(f"{match.group(2)}{match.group(3)}")
    if low is None or high is None or low[1] != high[1]:
        return None
    return low[0], high[0], low[1]


def inspect_unverified_standard_references(
    draft: dict[str, Any],
    *,
    web_verification: dict[str, Any] | None = None,
    confirmed_evidence_ids: set[str] | None = None,
) -> list[str]:
    claims = [item for item in draft.get("claims") or [] if isinstance(item, dict)]
    confirmed_ids = confirmed_evidence_ids or set()
    web_sufficient = (web_verification or {}).get("status") == "sufficient"
    standard_pattern = re.compile(
        r"\b(?:GB/?T|GB|ISO|IEC)\s*[-:]?\s*\d[\dA-Za-z./:-]*",
        re.I,
    )
    errors: list[str] = []
    for section in draft.get("sections") or []:
        heading = str(section.get("heading") or "")
        for sentence in _sentences(str(section.get("content") or "")):
            analyzable = sentence
            for placeholder in extract_placeholder_references(sentence):
                analyzable = analyzable.replace(placeholder, "")
            standards = standard_pattern.findall(analyzable)
            if not standards:
                continue
            compact = _normalize_fact_text(sentence)
            supported = False
            for claim in claims:
                claim_text = _normalize_fact_text(str(claim.get("text") or ""))
                if not claim_text or not (
                    claim_text in compact or compact in claim_text
                ):
                    continue
                claim_type = str(claim.get("claim_type") or "")
                evidence_ids = {
                    str(value) for value in claim.get("evidence_ids") or []
                }
                if claim_type == "tender_fact" and claim.get("source_ids"):
                    supported = True
                elif evidence_ids & confirmed_ids:
                    supported = True
                elif web_sufficient and claim.get("source_ids"):
                    supported = True
                if supported:
                    break
            if supported:
                continue
            keywords = ",".join(dict.fromkeys(standards))
            errors.append(
                f"fact_boundary:{heading}:unverified_standard_reference:{keywords}"
            )
    return list(dict.fromkeys(errors))


def _normalize_fact_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"(?m)^\s*#{1,6}\s*", "", normalized)
    normalized = normalized.replace("**", "").replace("__", "")
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE).casefold()


def _quality_policy(block_type: str) -> dict[str, float | int]:
    if block_type in TABLE_ORIENTED_BLOCK_TYPES:
        return QUALITY_POLICIES["list_or_table"]
    return QUALITY_POLICIES.get(
        block_type,
        {
            "min_prose_paragraphs": 1,
            "min_prose_characters": 60,
            "prose_ratio_warning": 0.40,
            "prose_ratio_error": 0.20,
            "structured_error_multiple": 2.0,
            "short_bullet_min_characters": 18,
        },
    )


def section_quality_floor(section: dict[str, Any]) -> dict[str, Any]:
    block_type = str(section.get("block_type") or "other")
    policy = _quality_policy(block_type)
    required_dimensions: list[str] = []
    if block_type == "risk_control":
        required_dimensions = [
            "风险识别",
            "风险影响",
            "预防措施",
            "应急措施",
            "升级处理",
        ]
    elif block_type in {"technical_solution", "implementation_plan"}:
        required_dimensions = [
            "目标与输入条件",
            "实施方法",
            "控制点",
            "成果或完成判据",
            "异常处理",
        ]
    elif block_type == "quality_assurance":
        required_dimensions = ["质量目标", "过程控制", "检查记录", "问题闭环"]
    return {
        "minimum_paragraph_count": int(policy["min_prose_paragraphs"]),
        "minimum_prose_characters": int(policy["min_prose_characters"]),
        "required_dimensions": required_dimensions,
    }


def _is_effective_prose_paragraph(
    value: str,
    *,
    numbered_step: bool = False,
    context_only: bool = False,
) -> bool:
    text = re.sub(r"^(?:\d+[.)、]|[（(]\d+[)）])\s*", "", str(value or "")).strip()
    compact = re.sub(r"\s+", "", text)
    normalized = re.sub(r"[：:；;，,。.!！?？\s]", "", text)
    transitions = {
        "具体如下",
        "包括以下内容",
        "主要措施如下",
        "详见下表",
        "以上内容将在后续展开",
        "具体内容如下",
        "主要内容如下",
        "说明如下",
    }
    if not normalized or normalized in transitions:
        return False
    minimum = 20 if context_only else (55 if numbered_step else 40)
    if len(compact) < minimum:
        return False
    categories = (
        ("目标", "目的", "用于", "确保", "保证", "旨在"),
        ("前提", "条件", "输入", "依赖", "接口", "具备", "确认后"),
        ("实施", "执行", "采用", "组织", "部署", "安装", "测试", "复核"),
        ("控制", "检查", "核对", "监测", "记录", "责任", "协同"),
        ("成果", "输出", "形成", "交付", "报告", "清单", "判据"),
        ("异常", "问题", "不满足", "整改", "处置", "关闭", "恢复"),
    )
    semantic_categories = sum(any(term in text for term in group) for group in categories)
    vague_terms = (
        "高度重视",
        "具有重要意义",
        "全面提升",
        "保驾护航",
        "不断发展",
        "总体而言",
        "综上所述",
        "科学合理",
        "先进可靠",
        "切实有效",
    )
    if sum(term in text for term in vague_terms) >= 2 and semantic_categories < 2:
        return False
    if context_only:
        return semantic_categories >= 1
    sentence_count = len([item for item in re.split(r"[。！？!?；;]", text) if item.strip()])
    return semantic_categories >= 2 and (sentence_count >= 1 or len(compact) >= 70)


def _has_effective_neighbor(
    blocks: list[dict[str, Any]],
    index: int,
    *,
    before: bool,
) -> bool:
    values = reversed(blocks[:index]) if before else iter(blocks[index + 1 :])
    ignored = {"heading", "quote", "placeholder", "standard_list"}
    for item in values:
        if item["kind"] in ignored:
            continue
        if item["kind"] == "prose":
            return _is_effective_prose_paragraph(
                item["text"],
                context_only=True,
            )
        if item["kind"] == "numbered":
            return any(
                _is_effective_prose_paragraph(text, numbered_step=True)
                for text in item.get("item_texts") or []
            )
        return False
    return False


def _has_table_intro_neighbor(
    blocks: list[dict[str, Any]],
    index: int,
) -> bool:
    for item in reversed(blocks[:index]):
        if item["kind"] in {"heading", "quote", "placeholder", "standard_list"}:
            continue
        if item["kind"] != "prose":
            return False
        text = str(item.get("text") or "").strip()
        if (
            len(re.sub(r"\s+", "", text)) <= 50
            and (
                re.fullmatch(
                    r"(?:表(?:[一二三四五六七八九十]+|\d+)[：:、.\-—]?\s*)?"
                    r"[\u4e00-\u9fffA-Za-z0-9（）()《》\-—]{2,40}表",
                    text,
                )
                or (
                    re.fullmatch(
                        r"表(?:[一二三四五六七八九十]+|\d+)"
                        r"[：:、.\-—]?\s*[\u4e00-\u9fffA-Za-z0-9（）()《》\-—]{2,40}",
                        text,
                    )
                    and not re.search(
                        r"(?:展示|列示|列明|说明|见|如下|用于|作为)",
                        text,
                    )
                )
            )
        ):
            continue
        return bool(
            re.search(
                r"(?:如下表所示|见下表|表中列示|具体安排如下|"
                r"如下表(?:列示|说明|汇总)|下表(?:列示|说明|汇总)|"
                r"下表(?:展示|列明)|下述表格?(?:[一二三四五六七八九十]+|\d+)?"
                r"[^。！？；;\n]{0,30}(?:展示|列示|列明)|"
                r"具体内容见表(?:[一二三四五六七八九十]+|\d+)|"
                r"表(?:[一二三四五六七八九十]+|\d+)[^。！？；;\n]{0,20}(?:列明|列示|展示)|"
                r"各[^。！？；;\n]{0,20}如下表所示|具体安排见下表|"
                r"(?:以下|本)[^。！？；;\n]{0,24}表[^。！？；;\n]{0,12}"
                r"(?:用于|便于|作为))",
                text,
            )
        )
    return False


def _prose_repeats_table_content(
    prose: str,
    table_blocks: list[dict[str, Any]],
) -> bool:
    prose_compact = re.sub(r"[\W_]+", "", str(prose), flags=re.UNICODE)
    if len(prose_compact) < 40:
        return False
    cells: list[str] = []
    for block in table_blocks:
        for line in str(block.get("text") or "").splitlines():
            if not _looks_like_table_row(line) or _is_separator_row(line):
                continue
            cells.extend(
                re.sub(r"[\W_]+", "", cell, flags=re.UNICODE)
                for cell in _split_markdown_row(line)
                if len(re.sub(r"[\W_]+", "", cell, flags=re.UNICODE)) >= 4
            )
    matched = {cell for cell in cells if cell and cell in prose_compact}
    matched_characters = sum(len(cell) for cell in matched)
    return len(matched) >= 2 and matched_characters >= len(prose_compact) * 0.55


def _markdown_blocks(content: str) -> list[dict[str, Any]]:
    raw_blocks = [
        value.strip()
        for value in re.split(r"\n\s*\n+", str(content or "").replace("\r\n", "\n"))
        if value.strip()
    ]
    result: list[dict[str, Any]] = []
    for value in raw_blocks:
        lines = [line for line in value.splitlines() if line.strip()]
        stripped_lines = [line.strip() for line in lines]
        compact = len(re.sub(r"\s+", "", value))
        if stripped_lines and all(_looks_like_table_row(line) for line in stripped_lines):
            kind = "table"
        elif stripped_lines and all(re.match(r"^[-*+]\s+", line) for line in stripped_lines):
            kind = "bullet"
        elif stripped_lines and all(re.match(r"^(?:\d+[.)、]|[（(]\d+[)）])\s*", line) for line in stripped_lines):
            kind = "numbered"
        elif stripped_lines and all(line.startswith(">") for line in stripped_lines):
            kind = "quote"
        elif stripped_lines and all(re.match(r"^#{1,6}\s+", line) for line in stripped_lines):
            kind = "heading"
        elif _pure_placeholder_block(value):
            kind = "placeholder"
        elif _pure_standard_number_list(stripped_lines):
            kind = "standard_list"
        else:
            kind = "prose"
        result.append(
            {
                "kind": kind,
                "text": value,
                "characters": compact,
                "item_count": len(lines) if kind in {"bullet", "numbered"} else 0,
                "item_texts": (
                    [
                        re.sub(
                            r"^(?:[-*+]\s+|\d+[.)、]\s*|[（(]\d+[)）]\s*)",
                            "",
                            line.strip(),
                        )
                        for line in lines
                    ]
                    if kind in {"bullet", "numbered"}
                    else []
                ),
            }
        )
    return result


def _pure_placeholder_block(value: str) -> bool:
    cleaned = str(value or "")
    for placeholder in extract_placeholder_references(cleaned):
        cleaned = cleaned.replace(placeholder, "")
    return bool(value.strip()) and len(re.sub(r"[\s：:；;，,。.!！?？\-—]", "", cleaned)) == 0


def _pure_standard_number_list(lines: list[str]) -> bool:
    if not lines:
        return False
    return all(
        bool(re.fullmatch(r"(?:GB/?T|GB|ISO|IEC)\s*[\d.:-]+", line, re.I))
        for line in lines
    )


def _aggregate_composition_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    integer_keys = (
        "prose_paragraph_count",
        "prose_character_count",
        "section_total_character_count",
        "prose_paragraphs_repeating_tables",
        "bullet_group_count",
        "bullet_item_count",
        "bullet_character_count",
        "short_bullet_item_count",
        "bullet_groups_without_intro",
        "bullet_groups_without_followup",
        "numbered_step_count",
        "inline_step_chain_count",
        "markdown_table_count",
        "markdown_table_character_count",
        "consecutive_list_group_count",
        "table_without_intro_count",
        "table_without_followup_count",
    )
    totals = {key: sum(int(item.get(key) or 0) for item in items) for key in integer_keys}
    totals["average_prose_paragraph_length"] = (
        round(
            totals["prose_character_count"] / totals["prose_paragraph_count"],
            2,
        )
        if totals["prose_paragraph_count"]
        else 0.0
    )
    totals["longest_prose_paragraph_length"] = max(
        (int(item.get("longest_prose_paragraph_length") or 0) for item in items),
        default=0,
    )
    denominator = (
        totals["prose_character_count"]
        + totals["bullet_character_count"]
        + totals["markdown_table_character_count"]
    )
    totals["prose_ratio"] = round(totals["prose_character_count"] / denominator, 4) if denominator else 0.0
    totals["bullet_ratio"] = round(totals["bullet_character_count"] / denominator, 4) if denominator else 0.0
    totals["table_ratio"] = round(totals["markdown_table_character_count"] / denominator, 4) if denominator else 0.0
    totals["sections_with_insufficient_prose"] = [
        item["section_title"] for item in items if item.get("insufficient_prose")
    ]
    totals["list_dominant_sections"] = [
        title
        for item in items
        for title in item.get("list_dominant_sections") or []
    ]
    return totals


def _validate_generated_table_metadata(
    draft: dict[str, Any],
    *,
    created: list[dict[str, Any]],
    section_metrics: list[dict[str, Any]],
    errors: list[str],
) -> None:
    metadata = [
        item
        for item in draft.get("generated_supporting_tables") or []
        if isinstance(item, dict)
    ]
    section_ids = {
        str(item.get("heading") or ""): str(
            item.get("section_id") or item.get("heading") or ""
        )
        for item in draft.get("sections") or []
        if isinstance(item, dict)
    }
    sections_by_reference: dict[str, dict[str, Any]] = {}
    for item in draft.get("sections") or []:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or "")
        section_id = str(item.get("section_id") or heading)
        sections_by_reference[heading] = item
        sections_by_reference[section_id] = item
    for created_table in created:
        section_title = str(created_table.get("section_title") or "")
        section_id = section_ids.get(section_title, section_title)
        match = next(
            (
                item
                for item in metadata
                if str(item.get("section_id") or item.get("section_title") or "")
                in {section_title, section_id}
            ),
            None,
        )
        if not match:
            errors.append(
                f"generated_supporting_table:{section_title}:missing_purpose_metadata"
            )
            continue
        if not str(match.get("title") or "").strip():
            errors.append(f"generated_supporting_table:{section_title}:missing_title")
        if not str(match.get("purpose") or "").strip():
            errors.append(f"generated_supporting_table:{section_title}:missing_purpose")
        if not str(match.get("why_table_is_better_than_prose") or "").strip():
            errors.append(
                f"generated_supporting_table:{section_title}:missing_table_rationale"
            )
    available_by_section: dict[str, int] = {}
    for item in created:
        section_title = str(item.get("section_title") or "")
        available_by_section[section_title] = (
            available_by_section.get(section_title, 0) + 1
        )
    consumed_by_section: dict[str, int] = {}
    for item in metadata:
        reference = str(
            item.get("section_id") or item.get("section_title") or ""
        )
        section = sections_by_reference.get(reference)
        section_title = str((section or {}).get("heading") or reference)
        consumed = consumed_by_section.get(section_title, 0)
        available = available_by_section.get(section_title, 0)
        if consumed >= available:
            errors.append(
                f"generated_supporting_table:{section_title}:declared_but_missing"
            )
            continue
        consumed_by_section[section_title] = consumed + 1
        title = str(item.get("title") or "").strip()
        content = str((section or {}).get("content") or "")
        if title and not _has_explicit_generated_table_title(content, title):
            errors.append(
                f"generated_supporting_table:{section_title}:title_not_matched"
            )


def _has_explicit_generated_table_title(content: str, title: str) -> bool:
    normalized_title = re.sub(r"\s+", "", str(title or ""))
    if not normalized_title:
        return False
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or _looks_like_table_row(line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = line.replace("**", "").strip()
        compact = re.sub(r"\s+", "", line)
        if compact == normalized_title:
            return True
        if re.fullmatch(
            r"表(?:[一二三四五六七八九十]+|\d+)[：:、.\-—]?"
            + re.escape(normalized_title),
            compact,
        ):
            return True
    return False


def detect_duplicate_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for left_index, left in enumerate(sections):
        left_paragraphs = _substantial_paragraphs(str(left.get("content") or ""))
        for right in sections[left_index + 1 :]:
            right_paragraphs = _substantial_paragraphs(str(right.get("content") or ""))
            best = 0.0
            duplicate_chars = 0
            for left_value in left_paragraphs:
                for right_value in right_paragraphs:
                    score = SequenceMatcher(None, left_value, right_value).ratio()
                    if score > best:
                        best = score
                        duplicate_chars = min(len(left_value), len(right_value))
            if best >= 0.80 and duplicate_chars >= 80:
                result.append(
                    {
                        "left_section": str(left.get("heading") or ""),
                        "right_section": str(right.get("heading") or ""),
                        "similarity": round(best, 3),
                        "duplicate_characters": duplicate_chars,
                        "severity": "severe" if best >= 0.92 and duplicate_chars >= 120 else "warning",
                    }
                )
    return result


def inspect_markdown_tables(markdown: str) -> dict[str, Any]:
    lines = str(markdown or "").splitlines()
    tables: list[dict[str, Any]] = []
    errors: list[str] = []
    index = 0
    while index < len(lines):
        if not _looks_like_table_row(lines[index]):
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and _looks_like_table_row(lines[index]):
            block.append(lines[index])
            index += 1
        if len(block) < 2 or not _is_separator_row(block[1]):
            errors.append("Markdown表格缺少合法的表头分隔行")
            continue
        parsed_rows = [_split_markdown_row(line) for line in block]
        column_count = len(parsed_rows[0])
        if column_count == 0:
            errors.append("Markdown表格没有列")
            continue
        if any(len(row) != column_count for row in parsed_rows):
            errors.append("Markdown表格各行列数不一致")
            continue
        if len(parsed_rows) < 3 or not any(any(cell.strip() for cell in row) for row in parsed_rows[2:]):
            errors.append("Markdown表格没有有效数据行")
            continue
        tables.append(
            {
                "headers": [cell.strip() for cell in parsed_rows[0]],
                "column_count": column_count,
                "row_count": len(parsed_rows) - 2,
                "character_count": len(
                    re.sub(r"\s+", "", "\n".join(block))
                ),
            }
        )
    return {"tables": tables, "errors": errors}


def _table_headers(table: dict[str, Any], rows: list[Any]) -> list[str]:
    headers = table.get("headers") or table.get("columns") or []
    if headers and isinstance(headers[0], dict):
        return [
            str(item.get("title") or item.get("name") or item.get("field") or "")
            for item in headers
        ]
    if headers:
        return [str(item) for item in headers]
    first = rows[0] if rows else None
    if isinstance(first, dict) and isinstance(first.get("cells"), list):
        return [str(cell.get("text") or "") for cell in first["cells"] if isinstance(cell, dict)]
    if isinstance(first, dict):
        return [str(item) for item in first]
    if isinstance(first, list):
        return [str(item) for item in first]
    return []


def _has_multi_level_header(rows: list[Any]) -> bool:
    if not rows or not isinstance(rows[0], dict):
        return False
    first_cells = list(rows[0].get("cells") or [])
    return any(
        isinstance(cell, dict)
        and (int(cell.get("rowspan") or 1) > 1 or int(cell.get("colspan") or 1) > 1)
        for cell in first_cells
    )


def _row_column_relationship(rows: list[Any], headers: list[str]) -> dict[str, Any]:
    return {
        "header_row_index": 0 if headers else None,
        "data_row_start_index": 1 if headers and rows else 0,
        "field_count": len(headers),
    }


def _source_table_index(source: dict[str, Any], fallback: int) -> int:
    value = str(source.get("row_or_cell") or "")
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else fallback


def _section_boundary(title: str) -> tuple[list[str], list[str]]:
    mapping = (
        (("总体", "规划", "概述"), ["目标、原则、阶段和管理机制"], ["专项操作步骤、逐项验收细节"]),
        (("组织", "人员", "团队"), ["岗位、职责、汇报关系和参与阶段"], ["虚构姓名、证书、履历或投入承诺"]),
        (("周期", "进度", "里程碑"), ["相对时间基准、阶段衔接和完成判据"], ["把招标最长时限写成最终承诺"]),
        (("安装", "部署", "实施"), ["具体操作、检查点、完成判据和过程成果"], ["重复总体原则和完整项目背景"]),
        (("测试", "试运行", "验收"), ["依据、步骤、记录、问题闭环和通过条件"], ["堆砌未经核验的标准编号"]),
        (("质量",), ["阶段质量控制点、检查成果和不满足时处理"], ["与风险章节重复完整流程"]),
        (("风险", "应急"), ["风险事项、预防、应急、责任和升级机制"], ["虚构概率或量化等级"]),
        (("交付", "成果"), ["招标必需资料和实施过程成果"], ["虚构份数、介质、签章要求"]),
    )
    for terms, focus, avoid in mapping:
        if any(term in title for term in terms):
            return focus, avoid
    return ["本小节标题对应的具体要求、动作、成果和检查方式"], ["其他小节已完整展开的流程和无关项目背景"]


def _created_table_candidates(nodes: list[dict[str, Any]], tender_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        title = str(node.get("title") or "")
        if any(term in title for term in ("周期", "进度", "组织", "人员", "风险", "质量", "交付")):
            result.append(
                {
                    "section_id": str(node.get("section_id") or node.get("id") or title),
                    "section_title": title,
                    "decision": "agent_may_create_if_useful",
                    "format": "standard_markdown",
                    "purpose": "仅在需要横向映射或比较时辅助正文；模型可以减少、增加或不采用。",
                }
            )
    return result


def _substantial_paragraphs(content: str) -> list[str]:
    values: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", content):
        normalized = re.sub(r"\s+", "", paragraph)
        if len(normalized) >= 80 and not normalized.startswith("|"):
            values.append(normalized)
    return values


def _looks_like_table_row(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_separator_row(value: str) -> bool:
    cells = _split_markdown_row(value)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _split_markdown_row(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [part.replace(r"\|", "|") for part in re.split(r"(?<!\\)\|", stripped)]


def _headers_match(expected: list[str], actual: list[str]) -> bool:
    left = [re.sub(r"\s+", "", value) for value in expected if value]
    right = [re.sub(r"\s+", "", value) for value in actual if value]
    return bool(left) and left == right


def _simple_table_was_reused(table: dict[str, Any], markdown_tables: list[dict[str, Any]]) -> bool:
    return any(_headers_match(table.get("headers") or [], item.get("headers") or []) for item in markdown_tables)


def _complex_table_was_simplified(table: dict[str, Any], markdown_tables: list[dict[str, Any]]) -> bool:
    expected = table.get("headers") or []
    return any(_headers_match(expected, item.get("headers") or []) for item in markdown_tables)

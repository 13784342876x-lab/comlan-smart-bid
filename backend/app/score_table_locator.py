import re
from copy import deepcopy
from typing import Any


BUSINESS_TEMPLATE_TABLE = {
    "FWSW01：商务详评细则": 3,
    "FWSW02：输变电工程设计类商务评分细则": 2,
    "FWSW03：输变电工程施工、监理类商务评分细则": 4,
}

TECHNICAL_TEMPLATE_TABLE = {
    "18.ZHFWJS02科技技术服务、科技项目技术详评模板V1.0": 11,
    "16.ZHFWJS01.LXFWJS01.YWFWJS01.SZHFWJS01服务技术详评模板（通用）V1.0": 9,
    "11.JS-GCZX01：电网工程咨询服务技术详评模板V1.0": 10,
}

def locate_score_table(
    parsed_text: str,
    tables: list[dict[str, Any]],
    lot_name: str | None,
    applicability_texts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = parsed_text or ""
    scoped_lot_name = str(lot_name or "").strip()
    warnings: list[str] = []

    if scoped_lot_name:
        business_context = find_lot_context(text, scoped_lot_name, ["商务详细评审标准", "附表三", "商务评分标准"], window=5200)
        technical_context = find_lot_context(text, scoped_lot_name, ["技术详细评审标准", "附表四", "技术评分标准"], window=5200)
        weight_context = find_weight_context(text, scoped_lot_name)
    else:
        business_context = find_document_context(
            text,
            ["商务详细评审标准", "附表三", "商务评分标准", *BUSINESS_TEMPLATE_TABLE],
            window=5200,
        )
        technical_context = find_document_context(
            text,
            ["技术详细评审标准", "附表四", "技术评分标准", *TECHNICAL_TEMPLATE_TABLE],
            window=5200,
        )
        weight_context = find_document_context(
            text,
            ["分值权重构成", "商务部分", "技术部分", "投标报价"],
            window=2600,
        )

    business_template = extract_template_name(business_context, BUSINESS_TEMPLATE_TABLE, scoped_lot_name)
    technical_template = extract_template_name(technical_context, TECHNICAL_TEMPLATE_TABLE, scoped_lot_name)
    weights = extract_weights(weight_context)

    classified = classify_score_tables(tables, text)
    technical_candidates = classified["technical"]
    business_candidates = classified["business"]
    price_candidates = classified["price"]

    # Fixed template names remain useful evidence, but are no longer required
    # before a structurally valid score table can be retained.
    if not business_candidates and business_template:
        table_index, block_text = find_best_score_table(tables, business_template, "business", text)
        if table_index and block_text:
            business_candidates = [legacy_score_candidate(table_index, block_text, business_template, "business")]
    if not technical_candidates and technical_template:
        table_index, block_text = find_best_score_table(tables, technical_template, "technical", text)
        if table_index and block_text:
            technical_candidates = [legacy_score_candidate(table_index, block_text, technical_template, "technical")]

    business_score_table = business_candidates[0] if business_candidates else None
    price_scoring_method = extract_price_scoring_method(price_candidates[0]) if price_candidates else None
    applicability = resolve_technical_template_applicability(
        technical_candidates,
        applicability_texts or [],
        scoped_lot_name,
    )
    if applicability["status"] == "unresolved" and technical_template:
        hinted_table_index = TECHNICAL_TEMPLATE_TABLE.get(technical_template)
        hinted_candidates = [
            item for item in technical_candidates if item.get("table_index") == hinted_table_index
        ]
        if len(hinted_candidates) == 1:
            applicability = {
                "status": "resolved_mapping",
                "selected_candidate_id": hinted_candidates[0]["candidate_id"],
                "reason": "exact legacy technical template name matched a structural table candidate",
                "evidence": [{"template_name": technical_template, "table_index": hinted_table_index}],
            }
    selected_technical = next(
        (
            item
            for item in technical_candidates
            if item["candidate_id"] == applicability.get("selected_candidate_id")
        ),
        None,
    )

    score_rules_extracted = bool(technical_candidates or business_score_table or price_scoring_method)
    unsupported_score_format = not score_rules_extracted
    technical_template_unresolved = bool(technical_candidates) and selected_technical is None
    component_weights_unresolved = not all(key in weights for key in ("business", "technical", "price"))

    if unsupported_score_format:
        warnings.append("未识别到受支持的技术、商务或价格评分结构")
    if not business_score_table:
        warnings.append("未识别到商务评分表")
    if not technical_candidates:
        warnings.append("未识别到技术评分表")
    elif technical_template_unresolved:
        warnings.append("已抽取技术评分候选，但无法确定本项目适用模板")
    if not price_scoring_method:
        warnings.append("未识别到价格评分办法")
    if component_weights_unresolved:
        warnings.append("评分规则已独立保留，但商务、技术、价格 component weights 尚未完整确认")

    confidence = 0.0
    confidence += 0.3 if business_score_table else 0
    confidence += 0.3 if technical_candidates else 0
    confidence += 0.2 if price_scoring_method else 0
    confidence += 0.1 if selected_technical else 0
    confidence += 0.1 if not component_weights_unresolved else 0

    business_table_index = business_score_table.get("table_index") if business_score_table else None
    business_block_text = business_score_table.get("block_text", "") if business_score_table else ""
    technical_table_index = selected_technical.get("table_index") if selected_technical else None
    technical_block_text = selected_technical.get("block_text", "") if selected_technical else ""

    return {
        "lot_name": lot_name,
        "localization_scope": "lot_context" if scoped_lot_name else "selected_score_source",
        "business_template_name": business_template,
        "technical_template_name": (
            selected_technical.get("heading") if selected_technical else technical_template
        ),
        "weights": weights,
        "business_block_text": business_block_text,
        "technical_block_text": technical_block_text,
        "weight_block_text": weight_context,
        "business_table_index": business_table_index,
        "technical_table_index": technical_table_index,
        "score_rules_extracted": score_rules_extracted,
        "technical_template_resolution_status": applicability["status"],
        "technical_template_unresolved": technical_template_unresolved,
        "component_weights_unresolved": component_weights_unresolved,
        "unsupported_score_format": unsupported_score_format,
        "selected_technical_candidate_id": applicability.get("selected_candidate_id"),
        "technical_template_selection_reason": applicability.get("reason"),
        "technical_template_evidence": applicability.get("evidence", []),
        "technical_score_table_candidates": technical_candidates,
        "business_score_table_candidates": business_candidates,
        "business_score_table": business_score_table,
        "price_scoring_method": price_scoring_method,
        "confidence": round(confidence, 2),
        "warnings": warnings,
        "candidate_business_blocks": candidate_contexts(text, scoped_lot_name, "商务详细评审标准"),
        "candidate_technical_blocks": candidate_contexts(text, scoped_lot_name, "技术详细评审标准"),
    }


def classify_score_tables(tables: list[dict[str, Any]], parsed_text: str) -> dict[str, list[dict[str, Any]]]:
    classified: dict[str, list[dict[str, Any]]] = {"technical": [], "business": [], "price": []}
    for table in tables:
        table_index = int(table.get("table_index", 0) or 0)
        rows = table.get("rows") or []
        if not table_index or not rows:
            continue
        headers = [clean_cell(cell) for cell in rows[0]]
        heading = score_section_heading(parsed_text, table_index)
        block_text = table_to_text(table)
        category, score, signals = classify_score_table(headers, heading, block_text, len(rows))
        if not category:
            continue
        classified[category].append(
            {
                "candidate_id": f"{category}_table_{table_index}",
                "category": category,
                "table_index": table_index,
                "heading": heading,
                "headers": headers,
                "row_count": len(rows),
                "confidence": round(min(score / 16.0, 1.0), 2),
                "selection_score": round(score, 2),
                "selection_signals": signals,
                "block_text": block_text,
            }
        )
    for candidates in classified.values():
        candidates.sort(key=lambda item: (-item["selection_score"], item["table_index"]))
    return classified


def score_section_heading(parsed_text: str, table_index: int) -> str:
    marker = f"=== Table {table_index} ==="
    marker_index = parsed_text.find(marker)
    if marker_index < 0:
        return ""
    prefix = parsed_text[max(0, marker_index - 900) : marker_index]
    lines = [clean_cell(line).rstrip("：:") for line in prefix.splitlines() if clean_cell(line)]
    for line in reversed(lines):
        if len(line) <= 120 and contains_any(line, ["技术评分", "商务评分", "报价评分", "价格评分"]):
            return line
    return ""


def classify_score_table(
    headers: list[str],
    heading: str,
    block_text: str,
    row_count: int,
) -> tuple[str | None, float, list[str]]:
    header_text = "|".join(headers)
    range_count = len(re.findall(r"-?\d+(?:\.\d+)?\s*[-－—~至]\s*-?\d+(?:\.\d+)?\s*分", block_text))
    evaluation_hits = sum(
        1
        for keyword in ("评审", "评分", "得分", "优秀", "优：", "良：", "一般", "最高分", "最低分")
        if keyword in block_text
    )

    price_score = 0.0
    price_signals: list[str] = []
    if contains_any(heading, ["报价评分", "价格评分"]):
        price_score += 8
        price_signals.append("section_heading:price_scoring")
    if contains_any(header_text, ["报价评分标准", "价格评分标准"]):
        price_score += 8
        price_signals.append("header:price_scoring_standard")
    for keyword in ("基准价", "价格部分得分", "有效投标人", "评标价"):
        if keyword in block_text:
            price_score += 1.5
            price_signals.append(f"semantic:{keyword}")
    if price_score >= 8:
        return "price", price_score, price_signals

    business_score = 0.0
    business_signals: list[str] = []
    required_business_headers = {"项目", "评审要素", "评审内容", "最低分", "最高分"}
    if required_business_headers.issubset(set(headers)):
        business_score += 10
        business_signals.append("headers:project,evaluation_factor,evaluation_content,min_score,max_score")
    if "商务评分" in heading:
        business_score += 7
        business_signals.append("section_heading:business_scoring")
    for keyword in ("诚信评价", "绿色低碳", "科研创新", "综合评价", "投标响应", "报价质量"):
        if keyword in block_text:
            business_score += 1
            business_signals.append(f"semantic:{keyword}")
    if business_score >= 9:
        return "business", business_score, business_signals

    technical_score = 0.0
    technical_signals: list[str] = []
    if "技术评分" in heading:
        technical_score += 8
        technical_signals.append("section_heading:technical_scoring")
    if {"评审要素", "评审内容"}.issubset(set(headers)):
        technical_score += 4
        technical_signals.append("headers:evaluation_factor,evaluation_content")
    if {"项目", "评分项", "评审细则"}.issubset(set(headers)):
        technical_score += 5
        technical_signals.append("headers:project,score_item,evaluation_rule")
    if range_count >= 2:
        technical_score += 2
        technical_signals.append(f"score_ranges:{range_count}")
    if evaluation_hits >= 2:
        technical_score += 2
        technical_signals.append(f"evaluation_semantics:{evaluation_hits}")
    if row_count >= 4:
        technical_score += 1
        technical_signals.append(f"structured_rows:{row_count}")
    if technical_score >= 9:
        return "technical", technical_score, technical_signals
    return None, 0.0, []


def legacy_score_candidate(
    table_index: int,
    block_text: str,
    template_name: str,
    category: str,
) -> dict[str, Any]:
    first_line = block_text.splitlines()[0] if block_text else ""
    return {
        "candidate_id": f"{category}_table_{table_index}",
        "category": category,
        "table_index": table_index,
        "heading": template_name,
        "headers": first_line.split("\t"),
        "row_count": len(block_text.splitlines()),
        "confidence": 0.7,
        "selection_score": 7.0,
        "selection_signals": ["legacy_exact_template_name"],
        "block_text": block_text,
    }


def resolve_technical_template_applicability(
    candidates: list[dict[str, Any]],
    applicability_sources: list[dict[str, Any]],
    lot_name: str,
) -> dict[str, Any]:
    if not candidates:
        return {"status": "not_found", "selected_candidate_id": None, "reason": "no technical score table found", "evidence": []}
    if len(candidates) == 1:
        return {
            "status": "resolved_unique",
            "selected_candidate_id": candidates[0]["candidate_id"],
            "reason": "only one structurally valid technical score table candidate",
            "evidence": [],
        }

    matches: dict[str, list[dict[str, Any]]] = {item["candidate_id"]: [] for item in candidates}
    mapping_headers = ("技术评分标准", "技术评分模板", "评分标准选用", "评分模板", "技术评分类型")
    mapping_actions = ("选用", "采用", "适用", "对应")
    for source in applicability_sources:
        source_file_id = str(source.get("file_id") or source.get("id") or "")
        for table in source.get("tables") or []:
            rows = table.get("rows") or []
            if not rows:
                continue
            header = "|".join(clean_cell(cell) for cell in rows[0])
            if not contains_any(header, list(mapping_headers)):
                continue
            for row_index, row in enumerate(rows[1:], start=1):
                row_text = "|".join(clean_cell(cell) for cell in row)
                if lot_name and lot_name not in row_text:
                    continue
                for candidate in candidates:
                    if candidate_matches_mapping(candidate, row_text):
                        matches[candidate["candidate_id"]].append(
                            {
                                "source_file_id": source_file_id,
                                "source_location": f"table:{table.get('table_index')}:row:{row_index}",
                                "text": row_text,
                            }
                        )
        for line_index, line in enumerate(str(source.get("parsed_text") or "").splitlines(), start=1):
            line = clean_cell(line)
            if not line or not contains_any(line, list(mapping_actions)):
                continue
            if lot_name and lot_name not in line:
                continue
            for candidate in candidates:
                if candidate_matches_mapping(candidate, line):
                    matches[candidate["candidate_id"]].append(
                        {
                            "source_file_id": source_file_id,
                            "source_location": f"line:{line_index}",
                            "text": line,
                        }
                    )

    matched = [(candidate_id, evidence) for candidate_id, evidence in matches.items() if evidence]
    if len(matched) == 1:
        candidate_id, evidence = matched[0]
        return {
            "status": "resolved_mapping",
            "selected_candidate_id": candidate_id,
            "reason": "unique explicit technical score template mapping found in scoped sources",
            "evidence": evidence,
        }
    return {
        "status": "unresolved",
        "selected_candidate_id": None,
        "reason": (
            "multiple explicit technical template mappings found in scoped sources"
            if len(matched) > 1
            else "multiple technical score tables were extracted but no explicit scoped-source applicability mapping was found"
        ),
        "evidence": [item for _, evidence in matched for item in evidence],
    }


def candidate_matches_mapping(candidate: dict[str, Any], text: str) -> bool:
    heading = clean_cell(candidate.get("heading") or "").rstrip("：:")
    labels = {heading}
    for suffix in ("技术评分标准", "技术评分", "评分标准", "评分模板"):
        if suffix in heading:
            labels.add(heading.split(suffix, 1)[0])
    labels = {label.strip(" ：:（）()") for label in labels if len(label.strip(" ：:（）()")) >= 2}
    return any(label in text for label in labels)


def extract_price_scoring_method(candidate: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(candidate.get("block_text") or "")
    lines = [clean_cell(line) for line in raw_text.splitlines() if clean_cell(line)]
    content_lines = [line for line in lines if line not in {"报价评分标准", "价格评分标准"}]
    method_name = next(
        (line for line in content_lines if not re.match(r"^\d+[.、]", line) and not line.startswith("计算")),
        "",
    )
    effective_rules = []
    for line in content_lines:
        if line.startswith("注：") or line.startswith("计算基准价"):
            break
        if re.match(r"^\d+[.、]", line):
            effective_rules.append(line)

    benchmark_match = re.search(r"计算基准价\s*(.*?)(?=计算价格部分得分)", raw_text, flags=re.S)
    benchmark_algorithm = clean_cell(benchmark_match.group(1)) if benchmark_match else ""
    formula_match = re.search(r"价格部分得分\s*=\s*([^\r\n；;]+)", raw_text)
    score_formula = f"价格部分得分={clean_cell(formula_match.group(1))}" if formula_match else ""
    parameters: dict[str, dict[str, Any]] = {}
    for name, value, percent in re.findall(
        r"\b(n1|n2|W1|W2|C)\s*=\s*(-?\d+(?:\.\d+)?)\s*(%)?",
        raw_text,
        flags=re.I,
    ):
        number = float(value)
        parameters[name] = {
            "value": int(number) if number.is_integer() else number,
            "unit": "percent" if percent else "coefficient",
        }
    return {
        "method_name": method_name,
        "table_index": candidate.get("table_index"),
        "heading": candidate.get("heading"),
        "headers": candidate.get("headers") or [],
        "effective_bid_range_rules": effective_rules,
        "benchmark_price_algorithm": benchmark_algorithm,
        "score_formula": score_formula,
        "parameters": parameters,
        "raw_text": raw_text,
        "confidence": candidate.get("confidence"),
        "selection_signals": candidate.get("selection_signals") or [],
    }


def build_price_score_rule(method: dict[str, Any] | None, weights: dict[str, Any]) -> dict[str, Any]:
    if not method:
        return {}
    price_weight = weights.get("price")
    return {
        "id": "price_scoring_method",
        "item_name": "价格评分",
        "category": "price",
        "score": price_weight or 0,
        "score_range": {"min": 0, "max": price_weight or 0},
        "score_rule": method.get("score_formula") or method.get("method_name") or "",
        "raw_text": method.get("raw_text") or "",
        "source_template_name": method.get("heading") or method.get("method_name") or "价格评分办法",
        "source_block_type": "price",
        "evidence_needed": ["开标一览表", "报价明细表", "报价一致性核对表"],
        "kb_mapping_hint": ["报价文件", "价格核对清单"],
        "target_section": "报价文件",
        "response_strategy": "核对投标总价、分项报价、税率、大小写金额和报价口径一致性。",
        "generation_instruction": "根据采购文件中的价格评分公式生成报价一致性与报价风险检查任务。",
        "risk_level": "high",
        "is_mandatory": True,
        "is_disqualifying": False,
        "price_scoring_method": method,
    }


def build_score_items_from_location(location: dict[str, Any], tables: list[dict[str, Any]]) -> dict[str, Any]:
    table_map = {int(table.get("table_index", 0)): table for table in tables if table.get("table_index")}
    business_candidate = location.get("business_score_table") or {}
    business_rows = rows_for_table(table_map.get(business_candidate.get("table_index")))
    raw_business_items = [
        score_item_from_row(
            row,
            index,
            "business",
            business_candidate.get("heading") or location.get("business_template_name") or "商务评分表",
            table_index=business_candidate.get("table_index"),
        )
        for index, row in enumerate(business_rows, start=1)
    ]
    business_items, business_aggregate_items = resolve_aggregate_items(raw_business_items)

    technical_candidates: list[dict[str, Any]] = []
    selected_candidate_id = location.get("selected_technical_candidate_id")
    technical_items: list[dict[str, Any]] = []
    technical_aggregate_items: list[dict[str, Any]] = []
    for candidate in location.get("technical_score_table_candidates") or []:
        rows = rows_for_table(table_map.get(candidate.get("table_index")))
        raw_items = [
            score_item_from_row(
                row,
                index,
                "technical",
                candidate.get("heading") or f"技术评分表 Table {candidate.get('table_index')}",
                table_index=candidate.get("table_index"),
            )
            for index, row in enumerate(rows, start=1)
        ]
        candidate_items, aggregate_items = resolve_aggregate_items(raw_items)
        structured_candidate = {
            **candidate,
            "score_items": candidate_items,
            "aggregate_score_items": aggregate_items,
            "selected": candidate.get("candidate_id") == selected_candidate_id,
        }
        technical_candidates.append(structured_candidate)
        if structured_candidate["selected"]:
            technical_items = candidate_items
            technical_aggregate_items = aggregate_items

    price_rule = build_price_score_rule(location.get("price_scoring_method"), location.get("weights") or {})
    aggregate_items = business_aggregate_items + technical_aggregate_items
    return materialize_score_item_aggregates({
        "business_score_items": business_items,
        "technical_score_items": technical_items,
        "technical_score_table_candidates": technical_candidates,
        "business_score_table": {**business_candidate, "score_items": business_items} if business_candidate else None,
        "price_scoring_method": location.get("price_scoring_method"),
        "price_score_rule": price_rule,
        "aggregate_score_items": aggregate_items,
        "business_aggregate_score_items": business_aggregate_items,
        "technical_aggregate_score_items": technical_aggregate_items,
        "total_score": 100,
        "score_scale": build_score_scale(business_items, technical_items, price_rule, location.get("weights", {})),
    })


def materialize_score_item_aggregates(score_parse: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the canonical score aggregates from the selected component lists.

    Technical-template selection may replace ``technical_score_items`` after
    the initial extraction.  The canonical combined list must always be
    materialized from those component lists; tender_project_builder then uses
    this list to create its existing task graph.
    """
    result = deepcopy(score_parse)
    business_items = list(result.get("business_score_items") or [])
    technical_items = list(result.get("technical_score_items") or [])
    price_rule = result.get("price_score_rule") or {}
    score_items = business_items + technical_items + ([price_rule] if price_rule else [])
    result["score_items"] = score_items
    result["generation_tasks"] = [
        item.get("generation_instruction", "")
        for item in score_items
        if item.get("generation_instruction") and not item.get("is_aggregate")
    ]
    result["disqualification_rules"] = [
        item.get("score_rule", "")
        for item in score_items
        if item.get("is_disqualifying") and not item.get("is_aggregate")
    ]
    return result



def build_score_scale(
    business_items: list[dict[str, Any]],
    technical_items: list[dict[str, Any]],
    price_rule: dict[str, Any],
    weights: dict[str, int],
) -> dict[str, Any]:
    return {
        "business_raw_total": 100,
        "technical_raw_total": 100,
        "price_raw_total": 100,
        "business_items_max_sum": sum(float(item.get("score", 0) or 0) for item in business_items if not item.get("is_aggregate")),
        "technical_items_max_sum": sum(float(item.get("score", 0) or 0) for item in technical_items if not item.get("is_aggregate")),
        "price_items_max_sum": float(price_rule.get("score", 0) or 0),
        "weights": weights,
        "final_total": 100,
    }


def find_lot_context(text: str, lot_name: str, keywords: list[str], window: int = 900) -> str:
    contexts = []
    for match in re.finditer(re.escape(lot_name), text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        context = text[start:end]
        score = sum(1 for keyword in keywords if keyword in context)
        if score:
            contexts.append((score, context))
    if not contexts:
        return ""
    contexts.sort(key=lambda item: item[0], reverse=True)
    return contexts[0][1]


def extract_template_name(context: str, templates: dict[str, int], lot_name: str | None) -> str | None:
    if not context:
        return None
    if not lot_name:
        candidates = [
            (context.find(template), template)
            for template in templates
            if context.find(template) >= 0
        ]
        return min(candidates)[1] if candidates else None
    candidates: list[tuple[int, str]] = []
    for lot_match in re.finditer(re.escape(lot_name), context):
        after_lot = context[lot_match.end() :]
        for template in templates:
            index = after_lot.find(template)
            if index >= 0:
                candidates.append((index, template))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def extract_weights(context: str) -> dict[str, int]:
    if not context:
        return {}
    return extract_weights_from_text(context)


def find_weight_context(text: str, lot_name: str) -> str:
    for match in re.finditer(re.escape(lot_name), text):
        context = text[match.start() : min(len(text), match.end() + 1800)]
        weights = extract_weights_from_text(context)
        if weights:
            return context
    return find_lot_context(text, lot_name, ["分值权重构成", "商务部分", "技术部分", "投标报价"], window=2600)


def find_document_context(text: str, keywords: list[str], window: int) -> str:
    matches: list[tuple[int, int, str]] = []
    for keyword in keywords:
        index = text.find(keyword)
        if index < 0:
            continue
        start = max(0, index - window)
        end = min(len(text), index + len(keyword) + window)
        context = text[start:end]
        score = sum(1 for item in keywords if item in context)
        matches.append((score, -index, context))
    if not matches:
        return ""
    matches.sort(reverse=True)
    return matches[0][2]


def extract_weights_from_text(context: str) -> dict[str, int]:
    mapping = {
        "business": r"商务部分[:：]\s*(\d+(?:\.\d+)?)\s*%",
        "technical": r"技术部分[:：]\s*(\d+(?:\.\d+)?)\s*%",
        "price": r"投标报价[:：]\s*(\d+(?:\.\d+)?)\s*%",
    }
    weights = {}
    for key, pattern in mapping.items():
        match = re.search(pattern, context)
        if match:
            weights[key] = int(float(match.group(1)))
    return weights


def rows_for_table(table: dict[str, Any] | None) -> list[list[str]]:
    if not table:
        return []
    rows = table.get("rows", [])
    return [row for row in rows[1:] if row and any(str(cell).strip() for cell in row)]


def table_to_text(table: dict[str, Any] | None) -> str:
    if not table:
        return ""
    lines = []
    for row in table.get("rows", []):
        lines.append("\t".join(str(cell).strip() for cell in row))
    return "\n".join(lines)


def find_best_score_table(
    tables: list[dict[str, Any]],
    template_name: str | None,
    category: str,
    parsed_text: str,
) -> tuple[int | None, str]:
    if not template_name:
        return None, ""

    scored: list[tuple[float, int, str]] = []
    for table in tables:
        table_index = int(table.get("table_index", 0) or 0)
        block_text = table_to_text(table)
        score = score_table_candidate(table, template_name, category, parsed_text)
        if table_index and block_text:
            scored.append((score, table_index, block_text))

    threshold = 7 if category == "business" else 6
    if not scored:
        return None, ""
    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    best_score, best_index, best_text = scored[0]
    if best_score < threshold:
        return None, ""
    return best_index, best_text



def score_table_candidate(
    table: dict[str, Any],
    template_name: str | None,
    category: str,
    parsed_text: str,
) -> float:
    table_index = int(table.get("table_index", 0) or 0)
    text = table_to_text(table)
    score = 0.0

    rows = table.get("rows") or []
    header = "\t".join(str(cell) for cell in rows[0]) if rows else ""
    header_keywords = ["\u9879\u76ee", "\u8bc4\u5206\u7ec6\u5219", "\u8bc4\u5206\u6807\u51c6", "\u5206\u503c"]
    score += sum(1.2 for keyword in header_keywords if keyword in header)
    if len(rows) >= 5:
        score += 2.0
    if len(rows) >= 9:
        score += 2.0

    if category == "business":
        business_keywords = ["\u8bda\u4fe1\u8bc4\u4ef7", "\u7eff\u8272\u4f4e\u78b3", "\u79d1\u7814\u521b\u65b0", "\u7efc\u5408\u8bc4\u4ef7", "\u6295\u6807\u54cd\u5e94", "\u62a5\u4ef7\u8d28\u91cf"]
        score += sum(1.5 for keyword in business_keywords if keyword in text)
        if "\u7efc\u5408\u8bc4\u4ef7" in text and "81\u5206" in text:
            score += 5
        if "\u7efc\u5408\u5b9e\u529b" in text and "26-36\u5206" in text:
            score += 4
        if "\u6295\u6807\u54cd\u5e94" in text and "38-45\u5206" in text:
            score += 4
        if "\u62a5\u4ef7\u8d28\u91cf" in text and all(item in text for item in ["\u4f18", "30-31\u5206", "\u826f", "28-29\u5206", "\u4e00\u822c", "26-27\u5206"]):
            score += 4
        wrong_business_markers = ["\u7efc\u5408\u8bc4\u4ef7\uff0831\u5206\uff09", "\u7efc\u5408\u8bc4\u4ef7 \uff0831\u5206\uff09", "\u7efc\u5408\u5b9e\u529b\uff087-12\u5206\uff09", "\u7efc\u5408\u5b9e\u529b \uff087-12\u5206\uff09", "\u6295\u6807\u54cd\u5e94\uff0811-19\u5206\uff09", "\u6295\u6807\u54cd\u5e94 \uff0811-19\u5206\uff09", "\u7ee9\u6548\u8bc4\u4ef7\uff0850\u5206\uff09", "\u7ee9\u6548\u8bc4\u4ef7 \uff0850\u5206\uff09"]
        score -= sum(3 for marker in wrong_business_markers if marker in text)
    else:
        technical_keywords = ["\u6280\u672f", "\u670d\u52a1\u65b9\u6848", "\u9879\u76ee\u56e2\u961f", "\u4e1a\u7ee9\u60c5\u51b5", "\u5de5\u4f5c\u8fdb\u5ea6", "\u5de5\u4f5c\u57fa\u7840", "\u4e13\u5229", "\u7ee9\u6548\u8bc4\u4ef7"]
        score += sum(1.5 for keyword in technical_keywords if keyword in text)
        if "\u5e94\u7b54\u6587\u4ef6\u6280\u672f\u90e8\u5206\u603b\u4f53\u8bc4\u4ef7" in text:
            score += 3
        if "\u5de5\u4f5c\u57fa\u7840" in text:
            score += 2

    ranges = re.findall(r"-?\d+(?:\.\d+)?\s*[-\uff0d\u2014~\u81f3]\s*-?\d+(?:\.\d+)?\s*\u5206", text)
    score += min(len(ranges), 12) * 0.4

    if template_name:
        hint_index = weak_template_hint_index(template_name, category)
        if hint_index and table_index == hint_index:
            score += 1.0
        score += table_distance_score(parsed_text, template_name, table_index)

    return score


def weak_template_hint_index(template_name: str, category: str) -> int | None:
    mapping = BUSINESS_TEMPLATE_TABLE if category == "business" else TECHNICAL_TEMPLATE_TABLE
    return mapping.get(template_name)


def table_distance_score(parsed_text: str, template_name: str, table_index: int) -> float:
    if not template_name or template_name not in parsed_text:
        return 0.0
    table_marker = f"=== Table {table_index} ==="
    marker_index = parsed_text.find(table_marker)
    if marker_index < 0:
        return 0.0
    template_indexes = [match.start() for match in re.finditer(re.escape(template_name), parsed_text)]
    previous = [idx for idx in template_indexes if idx < marker_index]
    if not previous:
        return 0.0
    distance = marker_index - max(previous)
    if distance < 1200:
        return 3.0
    if distance < 3500:
        return 2.0
    if distance < 7000:
        return 1.0
    return 0.0


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def score_item_from_row(
    row: list[str],
    index: int,
    category: str,
    template_name: str,
    *,
    table_index: int | None = None,
) -> dict[str, Any]:
    group = clean_cell(row[0]) if len(row) > 0 else ""
    if len(row) == 2:
        item_raw = group
        rule = clean_cell(row[1])
    else:
        item_raw = clean_cell(row[1] if len(row) > 1 else row[0])
        rule = clean_cell(row[2]) if len(row) > 2 else clean_cell(row[-1])
    base_item_name = clean_item_name(item_raw)
    group_name = clean_item_name(group)
    item_name = infer_unnamed_score_item_name(rule) if not base_item_name else normalize_item_name(base_item_name, rule)
    item_name_from_rule = item_name != base_item_name
    raw_text = "\n".join(clean_cell(cell) for cell in row if clean_cell(cell))
    item_score_range = first_non_zero_score_range(item_raw, group)
    rule_score_range = parse_score_range(rule)

    if item_name_from_rule:
        score_range = first_non_zero_score_range(rule, item_raw, group)
    elif group_name == base_item_name:
        score_range = first_non_zero_score_range(item_raw, group, rule)
    else:
        score_range = first_non_zero_score_range(item_raw, group, rule)

    score = score_range["max"]
    source_block_type = "business" if category == "business" else "technical"
    return {
        "id": f"{category}_{index:02d}_{slugify(item_name)}",
        "category": category,
        "item_name": item_name,
        "score": score,
        "score_range": score_range,
        "score_rule": rule,
        "raw_text": raw_text,
        "source_template_name": template_name,
        "source_table": {
            "table_index": int(table_index or 0),
            "heading": template_name,
        },
        # ``rows_for_table`` omits the table header, hence the source row is
        # one-based within the original table and starts at row 2.
        "source_row": {"table_row_index": index + 1, "data_row_index": index},
        "source_refs": [
            {
                "source_type": "score_table",
                "table_index": int(table_index or 0),
                "table_name": template_name,
                "row_or_cell": f"row {index + 1}",
            }
        ],
        "source_block_type": source_block_type,
        "evidence_needed": evidence_for_item(item_name, category),
        "kb_mapping_hint": kb_hint_for_item(item_name, category),
        "target_section": target_section_for_item(item_name, category),
        "response_strategy": response_strategy_for_item(item_name, category),
        "generation_instruction": generation_instruction_for_item(item_name, category),
        "risk_level": risk_level_for_item(item_name, rule),
        "is_mandatory": False,
        "is_disqualifying": contains_any(rule, ["\u5e9f\u6807", "\u5426\u51b3", "\u65e0\u6548", "\u4e0d\u5f97", "\u5fc5\u987b"]),
        "is_aggregate": False,
        "_group_name": group_name,
        "_base_item_name": base_item_name,
        "_item_score_range": item_score_range,
        "_rule_score_range": rule_score_range,
        "_item_name_from_rule": item_name_from_rule,
    }


def infer_unnamed_score_item_name(rule: str) -> str:
    """Name a blank continuation row from its own scoring criterion."""
    if "项目团队" in rule and "人员结构" in rule:
        return "项目团队人员结构"
    if "项目负责人" in rule:
        return "项目负责人"
    if "服务方案" in rule:
        return "服务方案"
    if "工作进度" in rule:
        return "工作进度及保证措施"
    return "未命名评分项"


def resolve_aggregate_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved = [dict(item) for item in items]
    aggregate_items: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[int]] = {}
    for index, item in enumerate(resolved):
        key = (str(item.get("_group_name") or ""), str(item.get("_base_item_name") or item.get("item_name") or ""))
        groups.setdefault(key, []).append(index)

    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        candidates = [idx for idx in indexes if not resolved[idx].get("_item_name_from_rule")]
        for parent_index in candidates:
            parent = resolved[parent_index]
            parent_range = parent.get("_item_score_range") or parent.get("score_range") or {"min": 0, "max": 0}
            if is_zero_score_range(parent_range):
                continue

            component_ranges: list[dict[str, float]] = []
            for idx in indexes:
                item = resolved[idx]
                if idx == parent_index:
                    component_range = item.get("_rule_score_range") or {"min": 0, "max": 0}
                else:
                    component_range = item.get("score_range") or {"min": 0, "max": 0}
                if not is_zero_score_range(component_range):
                    component_ranges.append(component_range)

            if len(component_ranges) < 2 or not ranges_sum_to_parent(parent_range, component_ranges):
                continue

            aggregate = dict(parent)
            aggregate["is_aggregate"] = True
            aggregate["aggregate_children"] = [resolved[idx].get("item_name") for idx in indexes if idx != parent_index]
            aggregate["score_range"] = parent_range
            aggregate["score"] = parent_range.get("max", 0)
            aggregate_items.append(aggregate)

            child_name = infer_embedded_child_name(parent.get("item_name", ""), parent.get("score_rule", ""), parent.get("category", ""))
            child_range = parent.get("_rule_score_range") or {"min": 0, "max": 0}
            parent["parent_item_name"] = parent.get("item_name")
            parent["aggregate_score_range"] = parent_range
            parent["item_name"] = child_name
            parent["id"] = f"{parent.get('category', 'score')}_{parent_index + 1:02d}_{slugify(child_name)}"
            parent["score_range"] = child_range
            parent["score"] = child_range.get("max", 0)
            parent["is_aggregate"] = False
            parent["evidence_needed"] = evidence_for_item(child_name, parent.get("category", ""))
            parent["kb_mapping_hint"] = kb_hint_for_item(child_name, parent.get("category", ""))
            parent["target_section"] = target_section_for_item(child_name, parent.get("category", ""))
            parent["response_strategy"] = response_strategy_for_item(child_name, parent.get("category", ""))
            parent["generation_instruction"] = generation_instruction_for_item(child_name, parent.get("category", ""))
            parent["risk_level"] = risk_level_for_item(child_name, parent.get("score_rule", ""))
            break

    final_items = [strip_internal_fields(item) for item in resolved if not item.get("is_aggregate")]
    aggregate_items = [strip_internal_fields(item) for item in aggregate_items]
    return final_items, aggregate_items


def ranges_sum_to_parent(parent_range: dict[str, Any], component_ranges: list[dict[str, Any]]) -> bool:
    min_sum = sum(float(score_range.get("min", 0) or 0) for score_range in component_ranges)
    max_sum = sum(float(score_range.get("max", 0) or 0) for score_range in component_ranges)
    return nearly_equal(min_sum, float(parent_range.get("min", 0) or 0)) and nearly_equal(max_sum, float(parent_range.get("max", 0) or 0))


def nearly_equal(left: float, right: float, tolerance: float = 0.01) -> bool:
    return abs(left - right) <= tolerance


def is_zero_score_range(score_range: dict[str, Any]) -> bool:
    return nearly_equal(float(score_range.get("min", 0) or 0), 0) and nearly_equal(float(score_range.get("max", 0) or 0), 0)


def infer_embedded_child_name(parent_name: str, rule: str, category: str) -> str:
    if "\u5546\u52a1\u6295\u6807\u6587\u4ef6" in rule:
        return "\u5546\u52a1\u6295\u6807\u6587\u4ef6\u6574\u4f53\u54cd\u5e94"
    if "\u6295\u6807\u6587\u4ef6" in rule and ("\u5b8c\u6574" in rule or "\u6574\u4f53" in rule):
        return "\u6295\u6807\u6587\u4ef6\u5b8c\u6574\u6027"
    if "\u5e94\u7b54\u6587\u4ef6" in rule and "\u6280\u672f" in rule:
        return "\u5e94\u7b54\u6587\u4ef6\u6280\u672f\u90e8\u5206\u603b\u4f53\u8bc4\u4ef7"
    if "\u5b8c\u6574" in rule or "\u6574\u4f53" in rule:
        return f"{parent_name}\u5b8c\u6574\u6027"
    return f"{parent_name}\u5b50\u9879"

def strip_internal_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def first_non_zero_score_range(*parts: str) -> dict[str, float]:
    for part in parts:
        score_range = parse_score_range(part or "")
        if score_range != {"min": 0, "max": 0}:
            return score_range
    return {"min": 0, "max": 0}

def parse_score_range(text: str) -> dict[str, float]:
    values: list[float] = []
    range_spans: list[tuple[int, int]] = []
    range_pattern = r"(-?\d+(?:\.\d+)?)\s*[-－—~至]\s*(-?\d+(?:\.\d+)?)\s*分"
    for match in re.finditer(range_pattern, text):
        low = float(match.group(1))
        high = float(match.group(2))
        values.extend([low, high])
        range_spans.append(match.span())
    single_pattern = r"(?<![-－—~至])(-?\d+(?:\.\d+)?)\s*分"
    for match in re.finditer(single_pattern, text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        values.append(float(match.group(1)))
    if not values:
        return {"min": 0, "max": 0}
    return {"min": min(values), "max": max(values)}


def clean_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_item_name(value: str) -> str:
    value = re.sub(r"^\d+[\.．、]?\s*", "", value)
    value = re.sub(r"[（(]\s*-?\d+(?:\.\d+)?(?:\s*[-－—~至]\s*-?\d+(?:\.\d+)?)?\s*分\s*[）)]", "", value)
    return value.strip() or "未命名评分项"


def normalize_item_name(item_name: str, rule: str) -> str:
    if item_name == "新激励机制":
        return "创新激励机制"
    if item_name == "项目团队" and "项目负责人" in rule:
        return "项目负责人情况"
    if item_name == "项目团队" and "人员结构" in rule:
        return "项目团队人员结构"
    if item_name == "投标响应" and "报价质量" in rule:
        return "报价质量"
    return item_name


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value).strip("_")
    return cleaned[:24] or "item"


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def evidence_for_item(item_name: str, category: str) -> list[str]:
    rules = [
        (["不良行为", "信用"], ["公共信用信息报告", "企业信用信息公示报告", "相关承诺函"]),
        (["绿色", "环境", "绿电"], ["绿色发展规划", "绿色管理体系认证", "环境影响评价材料", "绿电绿证"]),
        (["创新", "研发", "科研"], ["创新成果证明", "研发制度", "研发团队名单", "科研经费证明"]),
        (["综合实力"], ["企业资质证明", "人员与资源证明", "服务能力证明"]),
        (["投标响应", "应答文件"], ["投标响应文件", "偏差表", "响应承诺"]),
        (["报价"], ["报价明细表", "报价一致性核对表"]),
        (["负责人", "团队", "人员"], ["项目负责人简历", "团队人员名单", "资格证书", "社保证明"]),
        (["业绩"], ["项目合同", "中标通知书", "验收证明"]),
        (["服务方案", "进度", "措施", "工作基础"], ["服务方案", "进度计划", "保障措施", "项目基础材料"]),
        (["专利"], ["专利证书", "知识产权证明"]),
        (["绩效"], ["绩效评价证明", "历史评价结果"]),
    ]
    for keywords, evidence in rules:
        if contains_any(item_name, keywords):
            return evidence
    return ["评分项响应说明", "证明材料"]


def kb_hint_for_item(item_name: str, category: str) -> list[str]:
    if category == "business":
        return ["企业资质库", "业绩案例库", "常用素材库"]
    return ["产品资料库", "历史标书库", "常用素材库"]


def target_section_for_item(item_name: str, category: str) -> str:
    if category == "business":
        return "商务响应表" if "报价" not in item_name else "报价文件"
    if contains_any(item_name, ["团队", "负责人", "人员"]):
        return "项目团队"
    if contains_any(item_name, ["服务方案", "进度", "措施", "工作基础"]):
        return "技术方案"
    return "技术响应"


def response_strategy_for_item(item_name: str, category: str) -> str:
    return f"围绕“{item_name}”逐条响应评分标准，提供可核验材料并标明页码。"


def generation_instruction_for_item(item_name: str, category: str) -> str:
    return f"生成“{item_name}”评分响应内容，覆盖评分规则、证明材料、页码索引和得满分表达。"


def risk_level_for_item(item_name: str, rule: str) -> str:
    if contains_any(rule, ["废标", "否决", "无效", "不得", "必须"]):
        return "high"
    if contains_any(item_name, ["报价", "投标响应", "应答文件", "不良行为"]):
        return "medium"
    return "low"


def candidate_contexts(text: str, lot_name: str | None, marker: str) -> list[str]:
    blocks = []
    if not lot_name:
        for match in re.finditer(re.escape(marker), text):
            start = max(0, match.start() - 700)
            end = min(len(text), match.end() + 700)
            blocks.append(text[start:end])
        return blocks[:5]
    for match in re.finditer(re.escape(lot_name), text):
        start = max(0, match.start() - 700)
        end = min(len(text), match.end() + 700)
        context = text[start:end]
        if marker in context:
            blocks.append(context)
    return blocks[:5]


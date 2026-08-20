import re
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

PRICE_RULE = {
    "item_name": "价格评分",
    "category": "price",
    "score": 30,
    "score_range": {"min": 0, "max": 30},
    "score_rule": "该标段不在公式二适用标段中，适用其他标段的区间复合一次平均价法。",
    "raw_text": "其他标段适用区间复合一次平均价法。",
    "source_template_name": "招标文件价格评分公式",
    "source_block_type": "weight",
    "evidence_needed": ["开标一览表", "报价明细表", "报价一致性核对表"],
    "kb_mapping_hint": ["报价文件", "价格核对清单"],
    "target_section": "报价文件",
    "response_strategy": "核对投标总价、分项报价、税率、大小写金额和报价口径一致性。",
    "generation_instruction": "生成报价一致性检查任务，并按区间复合一次平均价法提示报价风险。",
    "risk_level": "high",
    "is_mandatory": True,
    "is_disqualifying": False,
}


def locate_score_table(parsed_text: str, tables: list[dict[str, Any]], lot_name: str) -> dict[str, Any]:
    text = parsed_text or ""
    table_map = {int(table.get("table_index", 0)): table for table in tables if table.get("table_index")}
    warnings: list[str] = []

    business_context = find_lot_context(text, lot_name, ["商务详细评审标准", "附表三", "商务评分标准"], window=5200)
    technical_context = find_lot_context(text, lot_name, ["技术详细评审标准", "附表四", "技术评分标准"], window=5200)
    weight_context = find_weight_context(text, lot_name)

    business_template = extract_template_name(business_context, BUSINESS_TEMPLATE_TABLE, lot_name)
    technical_template = extract_template_name(technical_context, TECHNICAL_TEMPLATE_TABLE, lot_name)
    weights = extract_weights(weight_context)

    business_table_index, business_block_text = find_best_score_table(tables, business_template, "business", text)
    technical_table_index, technical_block_text = find_best_score_table(tables, technical_template, "technical", text)

    if not business_template:
        warnings.append("无法自动确认商务评分模板")
    if not technical_template:
        warnings.append("无法自动确认技术评分模板")
    if not weights:
        warnings.append("无法自动确认分值权重")
    if business_template and not business_block_text:
        warnings.append(f"已匹配商务模板 {business_template}，但未找到对应评分表格")
    if technical_template and not technical_block_text:
        warnings.append(f"已匹配技术模板 {technical_template}，但未找到对应评分表格")

    confidence = 0.0
    confidence += 0.3 if business_template and business_block_text else 0
    confidence += 0.3 if technical_template and technical_block_text else 0
    confidence += 0.2 if weights else 0
    confidence += 0.2 if text.count(lot_name) >= 2 else 0

    return {
        "lot_name": lot_name,
        "business_template_name": business_template,
        "technical_template_name": technical_template,
        "weights": weights,
        "business_block_text": business_block_text,
        "technical_block_text": technical_block_text,
        "weight_block_text": weight_context,
        "business_table_index": business_table_index,
        "technical_table_index": technical_table_index,
        "confidence": round(confidence, 2),
        "warnings": warnings,
        "candidate_business_blocks": candidate_contexts(text, lot_name, "商务详细评审标准"),
        "candidate_technical_blocks": candidate_contexts(text, lot_name, "技术详细评审标准"),
    }


def build_score_items_from_location(location: dict[str, Any], tables: list[dict[str, Any]]) -> dict[str, Any]:
    table_map = {int(table.get("table_index", 0)): table for table in tables if table.get("table_index")}
    business_rows = rows_for_table(table_map.get(location.get("business_table_index")))
    technical_rows = rows_for_table(table_map.get(location.get("technical_table_index")))
    raw_business_items = [
        score_item_from_row(row, index, "business", location.get("business_template_name") or "\u5546\u52a1\u8bc4\u5206\u6a21\u677f")
        for index, row in enumerate(business_rows, start=1)
    ]
    raw_technical_items = [
        score_item_from_row(row, index, "technical", location.get("technical_template_name") or "\u6280\u672f\u8bc4\u5206\u6a21\u677f")
        for index, row in enumerate(technical_rows, start=1)
    ]
    business_items, business_aggregate_items = resolve_aggregate_items(raw_business_items)
    technical_items, technical_aggregate_items = resolve_aggregate_items(raw_technical_items)
    price_rule = {**PRICE_RULE, "id": "price_interval_composite_average", "score": location.get("weights", {}).get("price", 30)}
    score_items = business_items + technical_items + [price_rule]
    aggregate_items = business_aggregate_items + technical_aggregate_items
    return {
        "business_score_items": business_items,
        "technical_score_items": technical_items,
        "price_score_rule": price_rule,
        "score_items": score_items,
        "aggregate_score_items": aggregate_items,
        "business_aggregate_score_items": business_aggregate_items,
        "technical_aggregate_score_items": technical_aggregate_items,
        "total_score": 100,
        "score_scale": build_score_scale(business_items, technical_items, price_rule, location.get("weights", {})),
        "generation_tasks": [
            item.get("generation_instruction", "")
            for item in score_items
            if item.get("generation_instruction") and not item.get("is_aggregate")
        ],
        "disqualification_rules": [
            item.get("score_rule", "")
            for item in score_items
            if item.get("is_disqualifying") and not item.get("is_aggregate")
        ],
    }



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


def extract_template_name(context: str, templates: dict[str, int], lot_name: str) -> str | None:
    if not context:
        return None
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


def score_item_from_row(row: list[str], index: int, category: str, template_name: str) -> dict[str, Any]:
    group = clean_cell(row[0]) if len(row) > 0 else ""
    item_raw = clean_cell(row[1] if len(row) > 1 else row[0])
    rule = clean_cell(row[2]) if len(row) > 2 else clean_cell(row[-1])
    base_item_name = clean_item_name(item_raw)
    group_name = clean_item_name(group)
    item_name = normalize_item_name(base_item_name, rule)
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


def candidate_contexts(text: str, lot_name: str, marker: str) -> list[str]:
    blocks = []
    for match in re.finditer(re.escape(lot_name), text):
        start = max(0, match.start() - 700)
        end = min(len(text), match.end() + 700)
        context = text[start:end]
        if marker in context:
            blocks.append(context)
    return blocks[:5]


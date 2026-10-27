from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from app.technical_fulltext_composer import stable_json_sha256


PASS_TYPE = "cross_section_stable_fact_consistency"


def _stable_id(prefix: str, value: Any) -> str:
    return prefix + stable_json_sha256(value)[:16].upper()


def _unique(values: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _number_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _normalize_duration(value: str, unit: str) -> tuple[str, str, str]:
    number = _decimal(value)
    if number is None:
        return value, unit, "duration_unknown"
    if unit == "年":
        return _number_text(number * 12), "month", "calendar_duration"
    if unit in {"个月", "月"}:
        return _number_text(number), "month", "calendar_duration"
    if unit == "周":
        return _number_text(number * 7), "day", "day_duration"
    if unit in {"天", "日"}:
        return _number_text(number), "day", "day_duration"
    if unit in {"小时", "时"}:
        return _number_text(number * 60), "minute", "clock_duration"
    if unit in {"分钟", "分"}:
        return _number_text(number), "minute", "clock_duration"
    return _number_text(number), unit, "duration_unknown"


def _normalize_amount(value: str, unit: str) -> tuple[str, str, str]:
    number = _decimal(value)
    if number is None:
        return value, unit, "amount"
    multiplier = Decimal("10000") if unit == "万元" else Decimal("1")
    return _number_text(number * multiplier), "CNY", "amount"


def _normalize_percentage(value: str, unit: str) -> tuple[str, str, str]:
    number = _decimal(value)
    return (
        _number_text(number) if number is not None else value,
        "%",
        "percentage",
    )


def _normalize_quantity(value: str, unit: str) -> tuple[str, str, str]:
    number = _decimal(value)
    return (
        _number_text(number) if number is not None else value,
        unit,
        "quantity:" + unit,
    )


def _normalize_technical(value: str, unit: str) -> tuple[str, str, str]:
    number = _decimal(value)
    upper = unit.upper()
    if number is None:
        return value, upper, "technical:" + upper
    factors = {
        "TB": (Decimal("1024"), "GB", "capacity"),
        "GB": (Decimal("1"), "GB", "capacity"),
        "MB": (Decimal("0.0009765625"), "GB", "capacity"),
        "GBPS": (Decimal("1000"), "Mbps", "bandwidth"),
        "MBPS": (Decimal("1"), "Mbps", "bandwidth"),
    }
    if upper in factors:
        factor, canonical_unit, family = factors[upper]
        return _number_text(number * factor), canonical_unit, family
    return _number_text(number), upper or unit, "technical:" + (upper or unit)


def _normalize_identity(value: str, unit: str = "") -> tuple[str, str, str]:
    compact = re.sub(r"[\s，,。；;：:]", "", value).casefold()
    return compact, unit, "identity"


_ENTITY_TERMS = (
    "超融合互联交换机",
    "超融合节点产品",
    "超融合节点",
    "超融合系统",
    "管理平台",
    "存储系统",
    "交换机",
    "服务器",
    "所投产品",
    "产品",
    "项目团队",
    "实施团队",
    "部署团队",
    "运维团队",
    "服务团队",
)

_PHASE_TERMS = {
    "部署阶段": "deployment",
    "安装阶段": "deployment",
    "实施阶段": "implementation",
    "运维阶段": "operations",
    "运行阶段": "operations",
    "培训阶段": "training",
    "质保期内": "warranty",
    "保修期内": "warranty",
    "重大故障": "major_fault",
    "严重故障": "major_fault",
    "一般故障": "normal_fault",
}


def _nearby_entity(text: str, start: int, default: str) -> str:
    prefix = text[max(0, start - 36) : start]
    matches = [term for term in _ENTITY_TERMS if term in prefix]
    return max(matches, key=len) if matches else default


def _scope(text: str, start: int, end: int) -> str:
    context = text[max(0, start - 28) : min(len(text), end + 28)]
    matches = [value for term, value in _PHASE_TERMS.items() if term in context]
    return "+".join(sorted(set(matches)))


def _has_unresolved_value(value: str) -> bool:
    return bool(
        re.search(r"\{\{|PH-FIELD|placeholder|待确认|另行确定|最终确定", value, re.I)
    )


def _fact(
    *,
    paragraph: dict[str, Any],
    section: dict[str, Any],
    fact_type: str,
    entity: str,
    attribute: str,
    raw_value: str,
    normalized_value: str,
    unit: str,
    comparison_family: str,
    scope: str,
    comparator: str,
    span: tuple[int, int],
) -> dict[str, Any]:
    identity = {
        "paragraph_id": paragraph["paragraph_id"],
        "fact_type": fact_type,
        "entity": entity,
        "attribute": attribute,
        "raw_value": raw_value,
        "span": list(span),
    }
    return {
        "fact_id": _stable_id("SFC-", identity),
        "fact_type": fact_type,
        "entity": entity,
        "attribute": attribute,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "unit": unit,
        "comparison_family": comparison_family,
        "scope": scope,
        "comparator": comparator,
        "chapter_id": paragraph["chapter_id"],
        "section_id": paragraph["section_id"],
        "paragraph_id": paragraph["paragraph_id"],
        "paragraph_index": paragraph["paragraph_index"],
        "excerpt": str(paragraph.get("text") or "")[:800],
        "text_sha256": paragraph["text_sha256"],
        "requirement_ids": _unique(section.get("covered_requirement_ids") or []),
    }


def _extract_pattern(
    *,
    paragraph: dict[str, Any],
    section: dict[str, Any],
    pattern: re.Pattern[str],
    fact_type: str,
    attribute_map: dict[str, str],
    entity_for: Callable[[str, int, str], str],
    normalizer: Callable[[str, str], tuple[str, str, str]],
    default_entity: str,
    context_sensitive: set[str] | None = None,
    entity_by_attribute: dict[str, str] | None = None,
    fact_type_by_attribute: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    text = str(paragraph.get("text") or "")
    result: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        raw_value = match.group("value").strip()
        if _has_unresolved_value(raw_value):
            continue
        attribute_label = match.group("attribute")
        attribute = attribute_map[attribute_label]
        unit_value = (match.groupdict().get("unit") or "").strip()
        normalized, unit, family = normalizer(raw_value, unit_value)
        scope = _scope(text, match.start(), match.end()) if attribute in (context_sensitive or set()) else ""
        entity = (entity_by_attribute or {}).get(attribute) or entity_for(
            text, match.start(), default_entity
        )
        result.append(
            _fact(
                paragraph=paragraph,
                section=section,
                fact_type=(fact_type_by_attribute or {}).get(attribute, fact_type),
                entity=entity,
                attribute=attribute,
                raw_value=f"{raw_value}{unit_value}" if unit_value else raw_value,
                normalized_value=normalized,
                unit=unit,
                comparison_family=family,
                scope=scope,
                comparator=(match.groupdict().get("comparator") or "").strip(),
                span=match.span(),
            )
        )
    return result


_DURATION_ATTRIBUTES = {
    "服务期限": "service_duration",
    "服务期": "service_duration",
    "质保期": "warranty_duration",
    "保修期": "warranty_duration",
    "生命周期": "lifecycle_duration",
    "培训周期": "training_cycle",
}
_DURATION_ENTITIES = {
    "service_duration": "after_sales_service",
    "warranty_duration": "product_warranty",
    "lifecycle_duration": "product_lifecycle",
    "training_cycle": "training_service",
}
_DURATION_PATTERN = re.compile(
    r"(?P<attribute>服务期限|服务期|质保期|保修期|生命周期|培训周期)"
    r"\s*(?P<comparator>不低于|不少于|至少|不超过|为|是|：|:)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>年|个月|月|周|天|日|小时|时|分钟|分)"
)

_TIME_ATTRIBUTES = {
    "响应时间": "response_time",
    "到场时间": "arrival_time",
    "故障处理时间": "fault_handling_time",
    "故障处置时间": "fault_handling_time",
    "恢复时间": "recovery_time",
    "交货周期": "delivery_cycle",
    "交付周期": "delivery_cycle",
    "交货期": "delivery_cycle",
    "实施周期": "implementation_cycle",
    "部署周期": "deployment_cycle",
}
_TIME_ENTITIES = {
    "response_time": "after_sales_service",
    "arrival_time": "after_sales_service",
    "fault_handling_time": "after_sales_service",
    "recovery_time": "after_sales_service",
    "delivery_cycle": "project_delivery",
    "implementation_cycle": "project_implementation",
    "deployment_cycle": "system_deployment",
}
_TIME_PATTERN = re.compile(
    r"(?P<attribute>响应时间|到场时间|故障处理时间|故障处置时间|恢复时间|交货周期|交付周期|交货期|实施周期|部署周期)"
    r"\s*(?P<comparator>不低于|不少于|至少|不超过|以内|内|为|是|：|:)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>年|个月|月|周|天|日|小时|时|分钟|分)"
)

_QUANTITY_ATTRIBUTES = {
    "人员数量": "personnel_quantity",
    "人员": "personnel_quantity",
    "设备数量": "device_quantity",
    "设备": "device_quantity",
    "节点数量": "node_quantity",
    "节点": "node_quantity",
    "服务器数量": "server_quantity",
    "交换机数量": "switch_quantity",
}
_QUANTITY_PATTERN = re.compile(
    r"(?P<attribute>人员数量|人员|设备数量|设备|节点数量|节点|服务器数量|交换机数量)"
    r"\s*(?P<comparator>不低于|不少于|至少|不超过|为|是|共|：|:)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>人|名|台|套|个)"
)

_AMOUNT_ATTRIBUTES = {
    "项目总金额": "project_amount",
    "合同金额": "contract_amount",
    "报价金额": "bid_amount",
    "预算金额": "budget_amount",
}
_AMOUNT_PATTERN = re.compile(
    r"(?P<attribute>项目总金额|合同金额|报价金额|预算金额)"
    r"\s*(?P<comparator>不低于|不少于|至少|不超过|为|是|：|:)?\s*"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万元|元)"
)

_PERCENT_ATTRIBUTES = {
    "付款比例": "payment_percentage",
    "服务费比例": "service_fee_percentage",
    "优惠率": "discount_percentage",
    "折扣率": "discount_percentage",
    "违约金比例": "penalty_percentage",
    "可用性": "availability_percentage",
}
_PERCENT_PATTERN = re.compile(
    r"(?P<attribute>付款比例|服务费比例|优惠率|折扣率|违约金比例|可用性)"
    r"\s*(?P<comparator>不低于|不少于|至少|不超过|为|是|达到|：|:)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)"
)

_TECH_ATTRIBUTES = {
    "内存容量": "memory_capacity",
    "存储容量": "storage_capacity",
    "硬盘容量": "disk_capacity",
    "缓存容量": "cache_capacity",
    "网络带宽": "network_bandwidth",
    "带宽": "network_bandwidth",
    "吞吐量": "throughput",
    "IOPS": "iops",
    "CPU核心数": "cpu_core_count",
    "处理器数量": "processor_quantity",
    "端口数量": "port_quantity",
}
_TECH_PATTERN = re.compile(
    r"(?P<attribute>CPU核心数|处理器数量|端口数量|内存容量|存储容量|硬盘容量|缓存容量|网络带宽|带宽|吞吐量|IOPS)"
    r"\s*(?P<comparator>不低于|不少于|至少|不超过|为|是|达到|：|:)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>TB|GB|MB|Gbps|Mbps|个|核|路|口|IOPS)?",
    re.I,
)

_IDENTITY_ATTRIBUTES = {
    "产品名称": "product_name",
    "产品型号": "product_model",
    "型号": "product_model",
    "制造商": "manufacturer",
    "生产厂商": "manufacturer",
    "公司名称": "company_name",
    "投标人名称": "company_name",
    "供应商名称": "company_name",
    "服务机构名称": "service_organization_name",
    "服务机构地址": "service_organization_address",
    "公司地址": "company_address",
    "注册地址": "registered_address",
    "办公地址": "office_address",
}
_IDENTITY_PATTERN = re.compile(
    r"(?P<attribute>服务机构名称|服务机构地址|投标人名称|供应商名称|生产厂商|产品名称|产品型号|公司名称|注册地址|办公地址|公司地址|制造商|型号)"
    r"\s*(?P<comparator>为|是|：|:)\s*"
    r"(?P<value>[^，,。；;\n]{1,80})(?=[，,。；;\n]|$)"
)

_SERVICE_SCHEDULE_PATTERN = re.compile(
    r"(?P<value>\d+)\s*[×xX*]\s*(?P<hours>\d+)\s*小时(?:服务|技术支持|响应)?"
)
_BOOLEAN_COMMITMENT_PATTERN = re.compile(
    r"(?P<value>承诺提供|提供|不提供|无法提供)\s*"
    r"(?P<attribute>终身维修|免费上门服务|免费巡检)"
)


def extract_stable_fact_candidates(
    normalized_document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract cross-chapter stable facts from the already-normalized fulltext."""

    sections = {
        str(item.get("section_id") or ""): item
        for item in normalized_document.get("sections") or []
    }
    facts: list[dict[str, Any]] = []
    for paragraph in normalized_document.get("paragraphs") or []:
        section = sections.get(str(paragraph.get("section_id") or ""), {})

        duration_facts = _extract_pattern(
            paragraph=paragraph,
            section=section,
            pattern=_DURATION_PATTERN,
            fact_type="duration",
            attribute_map=_DURATION_ATTRIBUTES,
            entity_for=lambda _text, _start, default: default,
            normalizer=_normalize_duration,
            default_entity="stable_service",
            entity_by_attribute=_DURATION_ENTITIES,
        )
        facts.extend(duration_facts)

        # Entity defaults are attribute-specific; replace the generic extractor entity.
        time_facts = _extract_pattern(
            paragraph=paragraph,
            section=section,
            pattern=_TIME_PATTERN,
            fact_type="commitment_time",
            attribute_map=_TIME_ATTRIBUTES,
            entity_for=lambda text, start, default: _nearby_entity(text, start, default),
            normalizer=_normalize_duration,
            default_entity="stable_time_commitment",
            context_sensitive={
                "response_time", "arrival_time", "fault_handling_time", "recovery_time"
            },
            entity_by_attribute=_TIME_ENTITIES,
        )
        facts.extend(time_facts)

        facts.extend(
            _extract_pattern(
                paragraph=paragraph,
                section=section,
                pattern=_QUANTITY_PATTERN,
                fact_type="quantity",
                attribute_map=_QUANTITY_ATTRIBUTES,
                entity_for=lambda text, start, default: _nearby_entity(text, start, default),
                normalizer=_normalize_quantity,
                default_entity="project_resource",
                context_sensitive={"personnel_quantity", "device_quantity"},
            )
        )
        facts.extend(
            _extract_pattern(
                paragraph=paragraph,
                section=section,
                pattern=_AMOUNT_PATTERN,
                fact_type="amount",
                attribute_map=_AMOUNT_ATTRIBUTES,
                entity_for=lambda _text, _start, default: default,
                normalizer=_normalize_amount,
                default_entity="bid_project",
            )
        )
        facts.extend(
            _extract_pattern(
                paragraph=paragraph,
                section=section,
                pattern=_PERCENT_PATTERN,
                fact_type="percentage",
                attribute_map=_PERCENT_ATTRIBUTES,
                entity_for=lambda _text, _start, default: default,
                normalizer=_normalize_percentage,
                default_entity="bid_commitment",
            )
        )
        facts.extend(
            _extract_pattern(
                paragraph=paragraph,
                section=section,
                pattern=_TECH_PATTERN,
                fact_type="technical_parameter",
                attribute_map=_TECH_ATTRIBUTES,
                entity_for=lambda text, start, default: _nearby_entity(text, start, default),
                normalizer=_normalize_technical,
                default_entity="offered_product",
            )
        )

        identity_facts = _extract_pattern(
            paragraph=paragraph,
            section=section,
            pattern=_IDENTITY_PATTERN,
            fact_type="product_identity",
            attribute_map=_IDENTITY_ATTRIBUTES,
            entity_for=lambda text, start, default: _nearby_entity(text, start, default),
            normalizer=_normalize_identity,
            default_entity="offered_product",
            entity_by_attribute={
                "company_name": "bidder",
                "company_address": "bidder",
                "registered_address": "bidder",
                "office_address": "bidder",
                "service_organization_name": "service_organization",
                "service_organization_address": "service_organization",
            },
            fact_type_by_attribute={
                "company_name": "company_stable_fact",
                "company_address": "company_stable_fact",
                "registered_address": "company_stable_fact",
                "office_address": "company_stable_fact",
                "service_organization_name": "company_stable_fact",
                "service_organization_address": "company_stable_fact",
            },
        )
        for item in identity_facts:
            if _has_unresolved_value(item["raw_value"]):
                continue
            facts.append(item)

        text = str(paragraph.get("text") or "")
        for match in _SERVICE_SCHEDULE_PATTERN.finditer(text):
            normalized = f"{match.group('value')}x{match.group('hours')}"
            facts.append(
                _fact(
                    paragraph=paragraph,
                    section=section,
                    fact_type="explicit_service_commitment",
                    entity="after_sales_service",
                    attribute="service_schedule",
                    raw_value=match.group(0),
                    normalized_value=normalized,
                    unit="schedule",
                    comparison_family="service_schedule",
                    scope="",
                    comparator="commitment",
                    span=match.span(),
                )
            )
        for match in _BOOLEAN_COMMITMENT_PATTERN.finditer(text):
            raw = match.group("value")
            normalized = "no" if raw in {"不提供", "无法提供"} else "yes"
            facts.append(
                _fact(
                    paragraph=paragraph,
                    section=section,
                    fact_type="explicit_service_commitment",
                    entity="after_sales_service",
                    attribute=_normalize_identity(match.group("attribute"))[0],
                    raw_value=raw + match.group("attribute"),
                    normalized_value=normalized,
                    unit="boolean",
                    comparison_family="boolean",
                    scope="",
                    comparator="commitment",
                    span=match.span(),
                )
            )

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        by_key[(fact["entity"], fact["attribute"])].append(fact)

    candidates: list[dict[str, Any]] = []
    for (entity, attribute), group in by_key.items():
        # This pass is explicitly cross-section/cross-chapter. Local repetitions stay local.
        if len({item["chapter_id"] for item in group}) < 2:
            continue
        group = sorted(group, key=lambda item: item["fact_id"])
        decision, comparison_mode, reason_codes = _classify_group(group)
        candidate_id = _stable_id(
            "SAC-STABLE-",
            {
                "entity": entity,
                "attribute": attribute,
                "fact_ids": [item["fact_id"] for item in group],
            },
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_type": "cross_section_stable_fact",
                "fact_type": group[0]["fact_type"],
                "entity": entity,
                "attribute": attribute,
                "comparison_mode": comparison_mode,
                "deterministic_decision": decision,
                "normalized_values": _unique(
                    f"{item['normalized_value']} {item['unit']}".strip()
                    for item in group
                ),
                "chapter_ids": sorted({item["chapter_id"] for item in group}),
                "section_ids": sorted({item["section_id"] for item in group}),
                "requirement_ids": sorted(
                    {
                        requirement_id
                        for item in group
                        for requirement_id in item["requirement_ids"]
                    }
                ),
                "facts": group,
                "reason_codes": reason_codes,
            }
        )
    return sorted(candidates, key=lambda item: item["candidate_id"])


def _classify_group(
    facts: list[dict[str, Any]],
) -> tuple[str | None, str, list[str]]:
    values = {
        (item["normalized_value"], item["unit"], item["comparison_family"])
        for item in facts
    }
    if len(values) == 1:
        return "consistent", "deterministic", ["same_entity_attribute_value"]

    scopes = {item["scope"] for item in facts}
    nonempty_scopes = {value for value in scopes if value}
    context_sensitive = facts[0]["attribute"] in {
        "personnel_quantity",
        "device_quantity",
        "response_time",
        "arrival_time",
        "fault_handling_time",
        "recovery_time",
    }
    if context_sensitive and len(nonempty_scopes) >= 2 and "" not in scopes:
        scope_values: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        for item in facts:
            scope_values[item["scope"]].add(
                (item["normalized_value"], item["unit"], item["comparison_family"])
            )
        if all(len(items) == 1 for items in scope_values.values()):
            return "context_different", "deterministic", ["different_explicit_scope"]
        return "stable_fact_conflict", "deterministic", ["same_scope_conflicting_values"]

    families = {item["comparison_family"] for item in facts}
    if len(families) != 1 or (context_sensitive and nonempty_scopes and "" in scopes):
        return None, "semantic", ["attribute_or_scope_requires_semantic_adjudication"]
    return "stable_fact_conflict", "deterministic", ["same_entity_attribute_conflicting_values"]

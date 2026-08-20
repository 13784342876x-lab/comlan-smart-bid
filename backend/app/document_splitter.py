from __future__ import annotations

import re
import uuid
from typing import Any

from app.schemas import DetectedItem


QUALIFICATION_RULES = [
    {
        "name": "ISO9001质量管理体系认证",
        "patterns": [r"ISO\s*9001", r"质量管理体系认证"],
        "tags": ["ISO9001", "质量管理", "资格文件"],
        "usage": "用于质量管理体系和企业资质响应。",
    },
    {
        "name": "ISO14001环境管理体系认证",
        "patterns": [r"ISO\s*14001", r"环境管理体系认证"],
        "tags": ["ISO14001", "环境管理", "资格文件"],
        "usage": "用于环境管理体系和绿色低碳响应。",
    },
    {
        "name": "ISO20000信息技术服务管理体系认证",
        "patterns": [r"ISO\s*20000", r"信息技术服务管理体系认证"],
        "tags": ["ISO20000", "服务管理", "资格文件"],
        "usage": "用于信息技术服务管理能力响应。",
    },
    {
        "name": "ISO27001信息安全管理体系认证",
        "patterns": [r"ISO\s*27001", r"信息安全管理体系认证"],
        "tags": ["ISO27001", "信息安全", "资格文件"],
        "usage": "用于信息安全管理能力响应。",
    },
    {
        "name": "ISO27701隐私信息管理体系认证",
        "patterns": [r"ISO\s*27701", r"隐私信息管理体系认证"],
        "tags": ["ISO27701", "隐私信息管理", "资格文件"],
        "usage": "用于隐私信息管理能力响应。",
    },
    {
        "name": "ITSS信息技术服务标准符合性证书",
        "patterns": [r"\bITSS\b", r"信息技术服务标准符合性"],
        "tags": ["ITSS", "信息技术服务", "资格文件"],
        "usage": "用于信息技术服务运维能力响应。",
    },
    {
        "name": "CS3信息系统建设和服务能力等级证书",
        "patterns": [r"\bCS\s*3\b", r"CS3-", r"信息系统建设和服务能力"],
        "tags": ["CS3", "信息系统建设和服务", "资格文件"],
        "usage": "用于信息系统建设和服务能力响应。",
    },
    {
        "name": "信创数智技术服务能力等级证书",
        "patterns": [r"信创数智", r"信创工程实施能力"],
        "tags": ["信创数智", "信创", "资格文件"],
        "usage": "用于信创技术服务能力响应。",
    },
    {
        "name": "CCRC信息安全风险评估服务资质",
        "patterns": [r"CCRC.*风险评估", r"信息安全风险评估"],
        "tags": ["CCRC", "风险评估", "信息安全", "资格文件"],
        "usage": "用于信息安全风险评估服务资质响应。",
    },
    {
        "name": "CCRC信息安全应急处理服务资质",
        "patterns": [r"CCRC.*应急处理", r"信息安全应急处理"],
        "tags": ["CCRC", "应急处理", "信息安全", "资格文件"],
        "usage": "用于信息安全应急处理服务资质响应。",
    },
    {
        "name": "CCRC信息系统安全集成服务资质",
        "patterns": [r"CCRC.*安全集成", r"信息系统安全集成"],
        "tags": ["CCRC", "安全集成", "信息安全", "资格文件"],
        "usage": "用于信息系统安全集成服务资质响应。",
    },
    {
        "name": "CCRC信息系统安全运维服务资质",
        "patterns": [r"CCRC.*安全运维", r"信息系统安全运维"],
        "tags": ["CCRC", "安全运维", "信息安全", "资格文件"],
        "usage": "用于信息系统安全运维服务资质响应。",
    },
    {
        "name": "代理资质证明",
        "patterns": [r"代理资质证明", r"代理资质"],
        "tags": ["代理资质", "资格文件"],
        "usage": "用于代理资质和合作伙伴资格响应。",
    },
]

STRICT_GENERAL_RULES = [
    {
        "name": "营业执照",
        "patterns": [r"营业执照"],
        "category": "企业资质库",
        "document_type": "营业执照",
        "tags": ["营业执照", "资格文件"],
        "usage": "用于资格证明文件和商务响应。",
    },
    {
        "name": "AAA信用等级证书",
        "patterns": [r"AAA信用等级证书", r"信用等级证书"],
        "category": "企业资质库",
        "document_type": "信用等级证书",
        "tags": ["AAA", "信用等级"],
        "usage": "用于企业信誉和商务评分响应。",
    },
    {
        "name": "专利证书",
        "patterns": [r"专利证书", r"发明专利", r"实用新型专利"],
        "category": "产品资料库",
        "document_type": "专利证书",
        "tags": ["专利", "技术能力"],
        "usage": "用于技术实力、创新能力和评分项支撑。",
    },
    {
        "name": "软件著作权",
        "patterns": [r"软件著作权", r"软著"],
        "category": "产品资料库",
        "document_type": "软件著作权",
        "tags": ["软著", "软件著作权"],
        "usage": "用于产品能力和知识产权证明。",
    },
    {
        "name": "检测报告",
        "patterns": [r"检测报告", r"检验报告", r"测试报告"],
        "category": "产品资料库",
        "document_type": "检测报告",
        "tags": ["检测报告", "技术参数"],
        "usage": "用于技术参数响应和证明材料。",
    },
    {
        "name": "合同",
        "patterns": [r"项目合同", r"业绩合同"],
        "category": "业绩案例库",
        "document_type": "合同",
        "tags": ["合同", "业绩案例"],
        "usage": "用于同类业绩和商务评分响应。",
    },
]


def split_document(record: dict[str, Any]) -> list[DetectedItem]:
    parsed_text = record.get("parsed_text") or ""
    if not parsed_text.strip():
        raise ValueError("parsed_text is empty; run parse-file first")

    sources = build_evidence_sources(record)
    detected: list[DetectedItem] = []
    seen_names: set[str] = set()
    for rule in QUALIFICATION_RULES + STRICT_GENERAL_RULES:
        evidence = find_rule_evidence(rule, sources)
        if not evidence or rule["name"] in seen_names:
            continue
        detected.append(make_item(record, rule, evidence))
        seen_names.add(rule["name"])

    if not detected:
        detected.append(make_unclassified_item(record, parsed_text))
    return detected


def build_evidence_sources(record: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    parsed_text = record.get("parsed_text") or ""
    in_table = False
    for index, line in enumerate(parsed_text.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        if text.startswith("=== Table"):
            in_table = True
            continue
        source_type = "table_text" if in_table or "\t" in text else "parsed_text"
        sources.append({"text": text, "source_page": f"{source_type}_line_{index}", "source_type": source_type})

    for table_index, table in enumerate(record.get("tables", []) or [], start=1):
        rows = table.get("rows") or []
        for row_index, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                cells = [str(cell.get("value", "")).strip() for cell in row.get("cells", [])]
            else:
                cells = [str(cell).strip() for cell in row]
            text = "\t".join(cell for cell in cells if cell)
            if text:
                sources.append(
                    {
                        "text": text,
                        "source_page": f"table_{table_index}_row_{row_index}",
                        "source_type": "table_text",
                    }
                )

    for asset in record.get("assets", []) or []:
        text = str(asset.get("ocr_text") or "").strip()
        if text:
            sources.append(
                {
                    "text": text,
                    "source_page": str(asset.get("source_page") or asset.get("name") or "asset_ocr"),
                    "source_type": "ocr_text",
                    "asset_id": asset.get("id"),
                }
            )
    return dedupe_sources(sources)


def find_rule_evidence(rule: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for source in sources:
        text = source["text"]
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in rule["patterns"]):
            matches.append({**source, "evidence_level": evidence_level(source)})
    if not matches:
        return None
    matches.sort(key=evidence_sort_key)
    return matches[0]


def evidence_level(source: dict[str, Any]) -> str:
    source_type = source.get("source_type")
    text = str(source.get("text") or "")
    if source_type in {"table_text", "ocr_text"}:
        return "explicit"
    if "\t" in text or contains_any(text, ["证书编号", "有效期", "覆盖范围", "服务资质", "认证证书"]):
        return "explicit"
    if len(text) <= 80:
        return "title_only"
    return "explicit"


def evidence_sort_key(source: dict[str, Any]) -> tuple[int, int]:
    level_rank = 0 if source.get("evidence_level") == "explicit" else 1
    type_rank = 0 if source.get("source_type") == "table_text" else 1
    return (level_rank, type_rank)


def make_item(record: dict[str, Any], rule: dict[str, Any], evidence: dict[str, Any]) -> DetectedItem:
    category = rule.get("category", "企业资质库")
    document_type = rule.get("document_type", "资质证书")
    evidence_text = evidence["text"]
    return DetectedItem(
        id=f"split_{uuid.uuid4().hex[:12]}",
        source_file_id=record["id"],
        name=rule["name"],
        category=category,
        document_type=document_type,
        tags=rule.get("tags", []),
        valid_from=None,
        valid_to=None,
        source_page=str(evidence.get("source_page") or "parsed_text"),
        source_text=evidence_text,
        evidence_text=evidence_text,
        evidence_level=str(evidence.get("evidence_level") or "explicit"),
        related_asset_ids=[evidence["asset_id"]] if evidence.get("asset_id") else [],
        recommended_usage=rule.get("usage", ""),
        confidence=0.9 if evidence.get("evidence_level") == "explicit" else 0.62,
        status="pending",
    )


def make_unclassified_item(record: dict[str, Any], parsed_text: str) -> DetectedItem:
    return DetectedItem(
        id=f"split_{uuid.uuid4().hex[:12]}",
        source_file_id=record["id"],
        name="未分类资料",
        category=record.get("category", "常用素材库"),
        document_type="未分类资料",
        tags=["未分类", "待确认"],
        valid_from=None,
        valid_to=None,
        source_page="parsed_text",
        source_text=parsed_text[:500],
        evidence_text=parsed_text[:500],
        evidence_level="title_only",
        related_asset_ids=[],
        recommended_usage="待人工确认资料类型和用途。",
        confidence=0.35,
        status="pending",
    )


def dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for source in sources:
        text = " ".join(str(source.get("text") or "").split())
        if not text or text in seen:
            continue
        deduped.append({**source, "text": text})
        seen.add(text)
    return deduped


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)

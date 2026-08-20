from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.document_clause_indexer import build_clause_index
from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.material_matcher import find_project_by_lot_name, load_tender_projects
from app.tender_source_guard import is_tender_source_file, tender_source_type
from config import METADATA_PATH


SCHEMA_VERSION = "1.0"

REQUIREMENT_TYPES = {
    "potential_invalidity_risk",
    "mandatory_substantive_response",
    "qualification_requirement",
    "technical_mandatory_requirement",
    "signature_seal_requirement",
    "format_submission_requirement",
    "deviation_response_requirement",
    "special_table_requirement",
    "delivery_acceptance_commitment",
    "confidentiality_security_requirement",
    "score_related_requirement",
    "reference_only",
    "needs_review",
}

COMPLIANCE_LEVELS = {
    "fatal_if_missing",
    "must_respond",
    "evidence_required",
    "format_required",
    "score_related",
    "recommended",
    "reference_only",
    "needs_review",
}

RISK_TYPES = {
    "bid_invalidity_risk",
    "substantial_response_risk",
    "qualification_risk",
    "format_risk",
    "signature_seal_risk",
    "scoring_loss_risk",
    "material_missing_risk",
    "unclear_reference_risk",
    "none",
}

POLARITIES = {
    "positive_requirement",
    "negative_exception",
    "conditional_requirement",
    "informational",
    "uncertain",
}

NEGATION_TERMS = [
    "并不否决",
    "不作为否决条件",
    "不得作为否决依据",
    "不因此认定无效",
    "不影响投标有效性",
    "不否决",
]

FATAL_TERMS = [
    "否决其投标",
    "投标将被否决",
    "将被否决",
    "投标无效",
    "不予评审",
    "不通过资格审查",
    "作无效投标处理",
    "按无效投标处理",
]

CONDITION_TERMS = ["否则", "若", "如果", "仅当", "除", "未", "不符合", "不满足", "的，", "时"]

EXTERNAL_REFERENCE_PATTERNS = [
    re.compile(r"详见[^。；]{1,80}"),
    re.compile(r"见第[一二三四五六七八九十\d]+章[^。；]{0,80}"),
    re.compile(r"按附件[^。；]{0,80}(?:执行|填写|提供)?"),
    re.compile(r"按国家电网[^。；]{0,80}(?:标准|规定|要求)(?:执行)?"),
    re.compile(r"按[^。；]{1,60}(?:标准|规范|办法)(?:执行|规定)?"),
]

REQUIREMENT_HINTS = [
    "须",
    "必须",
    "不得",
    "要求",
    "提供",
    "提交",
    "填写",
    "响应",
    "承诺",
    "负责",
    "完成",
    "交付",
    "验收",
    "保密",
    "签字",
    "盖章",
    "否决",
    "无效",
    "评分",
    "得分",
    "详见",
]

FORMAT_TERMS = ["投标文件组成", "投标文件格式", "上传", "递交", "装订", "编制", "目录", "电子文件", "文件名称", "文件大小"]
SPECIAL_TABLE_TERMS = [
    "技术偏差表",
    "商务偏差表",
    "拟委任的主要人员汇总表",
    "主要人员简历表",
    "近年完成的类似项目情况表",
    "专项投标文件",
]
DELIVERY_TERMS = ["交付", "成果", "验收", "服务期限", "工期", "质量保证", "售后服务", "技术培训"]
SECURITY_TERMS = ["保密", "秘密", "数据安全", "信息安全", "网络安全", "隐私", "不得泄露"]

DERIVED_OUTPUTS = {
    "mandatory_response_items": lambda item: item.get("compliance_level") in {"fatal_if_missing", "must_respond", "evidence_required", "format_required"},
    "compliance_risk_items": lambda item: item.get("risk_type") != "none",
    "response_format_requirements": lambda item: item.get("requirement_type") in {"signature_seal_requirement", "format_submission_requirement", "deviation_response_requirement"},
    "special_table_requirements": lambda item: item.get("requirement_type") == "special_table_requirement",
    "manual_review_requirements": lambda item: bool(item.get("human_review_required")),
}


def build_tender_requirement_matrix(
    lot_name: str,
    output_dir: str | Path = "outputs",
    *,
    metadata: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    update_context_index: bool = True,
) -> dict[str, Any]:
    base = Path(output_dir)
    context_path = base / safe_path_name(lot_name) / "outline" / "technical_response_context.json"
    matrix_dir = base / safe_path_name(lot_name) / "requirements"
    matrix_path = matrix_dir / "tender_requirement_matrix.json"

    metadata = metadata or load_json(METADATA_PATH)
    project = project or find_project_by_lot_name(load_tender_projects(), lot_name)
    context = context or load_json(context_path)
    validate_context(context, lot_name)

    source_files = select_requirement_source_files(project, metadata, context)
    clauses = build_requirement_clause_set(source_files, lot_name)
    requirements = build_requirements(clauses, project, context, source_files)
    requirements.extend(build_score_requirements(project, context, source_files, requirements))
    requirements = dedupe_requirements(requirements)
    requirements.sort(key=requirement_sort_key)

    old_rule_warnings = detect_legacy_disqualification_issues(project)
    unresolved = [
        build_unresolved_reference(item)
        for item in requirements
        if isinstance(item.get("external_reference"), dict) and not item["external_reference"].get("resolved")
    ]
    global_manual = list(context.get("global_manual_confirm_items") or [])
    global_manual.extend(
        build_global_manual_item(item, index)
        for index, item in enumerate(requirements, start=1)
        if item.get("human_review_required")
    )
    global_manual = dedupe_dicts(global_manual, lambda item: str(item.get("confirm_id") or item.get("question") or ""))

    generated_at = datetime.now(timezone.utc).isoformat()
    matrix = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "project_ref": {
            "context_path": str(context_path),
            "context_schema_version": context.get("schema_version"),
            "project": dict(context.get("project") or {}),
            "project_conflicts": list(context.get("project_conflicts") or []),
            "source_type": "tender_original_fact",
        },
        "source_files": [matrix_source_file(item) for item in source_files],
        "requirements": requirements,
        "requirement_conflicts": build_requirement_conflicts(requirements),
        "unresolved_external_references": unresolved,
        "global_manual_confirm_items": global_manual,
        "statistics": build_statistics(requirements, context, unresolved),
        "build_warnings": unique(old_rule_warnings + build_warnings(requirements, context, unresolved)),
    }
    validate_matrix(matrix)
    ensure_no_decision_terms(matrix)

    matrix_dir.mkdir(parents=True, exist_ok=True)
    write_matrix_outputs(matrix, matrix_path)
    if update_context_index:
        update_technical_context_requirement_index(context, requirements, matrix_path, generated_at)
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "lot_name": lot_name,
        "schema_version": SCHEMA_VERSION,
        "output_dir": str(matrix_dir),
        "matrix_path": str(matrix_path),
        "files": output_file_map(matrix_dir),
        "statistics": matrix["statistics"],
        "build_warnings": matrix["build_warnings"],
    }


def inspect_tender_requirement_matrix(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    matrix_path = Path(output_dir) / safe_path_name(lot_name) / "requirements" / "tender_requirement_matrix.json"
    if not matrix_path.exists():
        raise FileNotFoundError(f"tender_requirement_matrix.json not found: {matrix_path}")
    matrix = load_json(matrix_path)
    requirements = list(matrix.get("requirements") or [])
    return {
        "lot_name": lot_name,
        "matrix_path": str(matrix_path),
        "statistics": matrix.get("statistics") or {},
        "fatal_if_missing": summarize_requirements(requirements, lambda item: item.get("compliance_level") == "fatal_if_missing"),
        "must_respond": summarize_requirements(requirements, lambda item: item.get("compliance_level") == "must_respond"),
        "unresolved_external_references": list(matrix.get("unresolved_external_references") or []),
        "human_review_count": len([item for item in requirements if item.get("human_review_required")]),
        "chapter_coverage": (matrix.get("statistics") or {}).get("context_chapter_coverage") or {},
        "files": {key: {"path": path, "exists": Path(path).exists()} for key, path in output_file_map(matrix_path.parent).items()},
    }


def select_requirement_source_files(
    project: dict[str, Any],
    metadata: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("id") or item.get("file_id") or ""): item
        for item in list(metadata.get("files") or [])
        if isinstance(item, dict)
    }
    ids: list[str] = []
    ids.extend(str(item.get("source_file_id") or "") for item in list(context.get("source_files") or []))
    ids.extend([str(project.get("tender_file_id") or ""), str(project.get("score_file_id") or "")])
    fields = ((project.get("tender_project_metadata") or {}).get("fields") or {}) if isinstance(project.get("tender_project_metadata"), dict) else {}
    ids.extend(str(item.get("source_file_id") or "") for item in fields.values() if isinstance(item, dict))
    candidates = ((project.get("tender_project_metadata") or {}).get("field_candidates") or {}) if isinstance(project.get("tender_project_metadata"), dict) else {}
    for rows in candidates.values():
        ids.extend(str(item.get("source_file_id") or "") for item in list(rows or []) if isinstance(item, dict))
    selected = []
    for file_id in unique(ids):
        item = by_id.get(file_id)
        if not item or not str(item.get("parsed_text") or "").strip():
            continue
        if is_tender_source_file(item) or file_id in {project.get("tender_file_id"), project.get("score_file_id")}:
            selected.append(item)
    return sorted(selected, key=source_file_priority)


def source_file_priority(item: dict[str, Any]) -> tuple[int, str]:
    source_type = tender_source_type(item)
    priority = {
        "full_tender_document": 0,
        "tender_notice": 1,
        "tender_notice_attachment": 2,
        "tender_attachment": 3,
        "technical_spec": 4,
        "procurement_package_table": 5,
        "score_table": 6,
    }.get(source_type, 9)
    return priority, str(item.get("id") or "")


def build_requirement_clause_set(source_files: list[dict[str, Any]], lot_name: str) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    for item in source_files:
        if tender_source_type(item) == "score_table":
            continue
        rows = build_clause_index(
            str(item.get("id") or ""),
            str(item.get("original_name") or ""),
            str(item.get("parsed_text") or ""),
            file_meta=item,
            lot_name=lot_name,
        )
        clauses.extend(row for row in rows if clause_is_in_scope(row, lot_name))
    return clauses


def clause_is_in_scope(clause: dict[str, Any], lot_name: str) -> bool:
    if clause.get("is_heading_only"):
        return False
    table_context = clause.get("table_context") if isinstance(clause.get("table_context"), dict) else {}
    if table_context and table_context.get("is_current_lot") is False:
        return False
    text = str(clause.get("clause_text") or "")
    lot_codes = set(re.findall(r"042513\d{7}-\d{4}", text))
    if lot_codes and lot_name not in lot_codes and clause.get("document_region") == "procurement_package_table":
        return False
    return True


def build_requirements(
    clauses: list[dict[str, Any]],
    project: dict[str, Any],
    context: dict[str, Any],
    source_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for clause in clauses:
        segments = split_requirement_segments(str(clause.get("clause_text") or ""))
        for index, segment in enumerate(segments):
            if not segment_is_requirement_candidate(segment, clause):
                continue
            context_before = segments[index - 1] if index > 0 else ""
            context_after = segments[index + 1] if index + 1 < len(segments) else ""
            requirement = classify_requirement(segment, clause, context, source_files, context_before, context_after)
            if (
                requirement
                and requirement.get("requirement_type") != "score_related_requirement"
                and not (
                    requirement.get("requirement_type") == "reference_only"
                    and not requirement.get("external_reference")
                    and requirement.get("polarity") != "negative_exception"
                )
            ):
                requirements.append(requirement)
    return requirements


def classify_requirement(
    raw_text: str,
    clause: dict[str, Any],
    context: dict[str, Any],
    source_files: list[dict[str, Any]],
    context_before: str = "",
    context_after: str = "",
) -> dict[str, Any] | None:
    text = normalize_text(raw_text)
    if not text:
        return None
    semantics = analyze_semantics(text, context_before, context_after)
    external_reference = resolve_external_reference(text, source_files, clause)
    requirement_type = infer_requirement_type(text, clause, semantics, external_reference)
    compliance_level = infer_compliance_level(text, clause, semantics, requirement_type, external_reference)
    risk_type = infer_risk_type(requirement_type, compliance_level, semantics, external_reference)
    chapter_ids, score_ids = relate_context(text, clause, requirement_type, context)
    special_table_ids = related_special_table_ids(text, requirement_type)
    human_review = should_require_human_review(requirement_type, compliance_level, semantics, external_reference, clause)
    questions = build_human_review_questions(text, requirement_type, external_reference, clause) if human_review else []
    source_ref = build_requirement_source_ref(clause, text, context_before, context_after)
    requirement_id = stable_requirement_id(str(clause.get("clause_id") or "clause"), text)
    normalized_requirement = normalize_requirement_summary(text, requirement_type)
    return {
        "requirement_id": requirement_id,
        "title": requirement_title(text, clause, requirement_type),
        "requirement_type": requirement_type,
        "raw_text": text,
        "normalized_requirement": {
            "text": normalized_requirement,
            "source_type": "system_judgement",
        },
        "required_action": {
            "text": required_action_for(requirement_type, compliance_level),
            "source_type": "suggested_writing" if requirement_type in {"mandatory_substantive_response", "technical_mandatory_requirement", "delivery_acceptance_commitment"} else "system_judgement",
        },
        "required_response_content": {
            "items": required_response_content_for(text, requirement_type),
            "source_type": "suggested_writing",
        },
        "required_materials": [
            {"material_type": item, "source_type": "suggested_material"}
            for item in required_materials_for(text, requirement_type)
        ],
        "source_ref": source_ref,
        "applicable_scope": infer_applicable_scope(clause, context),
        "compliance_level": compliance_level,
        "risk_type": risk_type,
        "risk_basis": {
            "text": build_risk_basis(semantics, compliance_level, requirement_type),
            "source_type": "system_judgement",
        },
        "related_score_item_ids": score_ids,
        "related_context_chapter_ids": chapter_ids,
        "related_special_table_ids": special_table_ids,
        "external_reference": external_reference,
        "source_type": "tender_original_fact",
        "confidence": classification_confidence(requirement_type, compliance_level, semantics, clause),
        "human_review_required": human_review,
        "human_review_questions": [
            {"question": question, "source_type": "human_confirmation_required"} for question in questions
        ],
        "status": "needs_review" if human_review else "identified",
        "detected_risk_terms": semantics["detected_risk_terms"],
        "detected_negation_terms": semantics["detected_negation_terms"],
        "detected_condition_terms": semantics["detected_condition_terms"],
        "polarity": semantics["polarity"],
        "classification_basis": {
            "text": semantics["classification_basis"],
            "source_type": "system_judgement",
        },
    }


def analyze_semantics(text: str, context_before: str = "", context_after: str = "") -> dict[str, Any]:
    window = normalize_text(" ".join([context_before, text, context_after]))
    negations = [term for term in NEGATION_TERMS if term in text]
    risks = [term for term in FATAL_TERMS if term in text]
    conditions = [term for term in CONDITION_TERMS if term in text]
    explicit_fatal = bool(risks) and not bool(negations) and has_explicit_fatal_relation(text)
    if negations:
        polarity = "negative_exception"
        basis = "命中明确否定/例外语义，完整句意表示不构成否决或无效依据。"
    elif explicit_fatal and conditions:
        polarity = "conditional_requirement"
        basis = "命中明确后果语义，并存在未满足、否则或条件触发关系。"
    elif conditions and (has_obligation_or_task(text) or any(term in text for term in REQUIREMENT_HINTS)):
        polarity = "conditional_requirement"
        basis = "条款包含条件或例外结构，需结合触发条件执行。"
    elif has_obligation_or_task(text) or any(term in text for term in REQUIREMENT_HINTS):
        polarity = "positive_requirement"
        basis = "条款包含明确响应、提供、提交或履约要求。"
    elif window:
        polarity = "informational"
        basis = "条款主要提供说明或引用信息。"
    else:
        polarity = "uncertain"
        basis = "无法稳定识别条款语义。"
    return {
        "detected_risk_terms": risks,
        "detected_negation_terms": negations,
        "detected_condition_terms": unique(conditions),
        "explicit_fatal": explicit_fatal,
        "polarity": polarity,
        "classification_basis": basis,
    }


def has_explicit_fatal_relation(text: str) -> bool:
    if any(term in text for term in NEGATION_TERMS):
        return False
    if not any(term in text for term in FATAL_TERMS):
        return False
    patterns = [
        r"未.{0,100}(?:否决其投标|投标无效|不予评审|不通过资格审查|作无效投标处理|按无效投标处理|将被否决)",
        r"不符合.{0,100}(?:否决其投标|投标无效|不予评审|不通过资格审查|将被否决)",
        r"否则.{0,100}(?:否决其投标|投标无效|不予评审|不通过资格审查|将被否决)",
        r"(?:均无效|相关投标均无效|投标将被否决|将被否决)",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def infer_requirement_type(
    text: str,
    clause: dict[str, Any],
    semantics: dict[str, Any],
    external_reference: dict[str, Any] | None,
) -> str:
    region = str(clause.get("document_region") or "")
    if semantics["polarity"] == "negative_exception":
        return "reference_only"
    if any(term in text for term in ["签字", "盖章", "签章", "法定代表人签名", "单位负责人签字"]):
        return "signature_seal_requirement"
    if any(term in text for term in ["授权委托书", "法定代表人授权", "被授权人"]):
        return "signature_seal_requirement"
    if any(term in text for term in ["资格要求", "资质要求", "资格条件", "业绩要求", "证明材料", "营业执照", "法人证书", "失信", "行贿犯罪", "破产", "资格审查", "资格评审", "主体不符", "资格不符"]):
        return "qualification_requirement"
    if semantics["explicit_fatal"] and any(term in text for term in ["实质性要求", "实质性响应", "满足性响应"]):
        return "mandatory_substantive_response"
    if semantics["explicit_fatal"]:
        return "potential_invalidity_risk"
    if "偏差表" in text:
        return "deviation_response_requirement"
    if any(term in text for term in SPECIAL_TABLE_TERMS):
        return "special_table_requirement"
    if region == "technical_spec" and has_obligation_or_task(text):
        if any(term in text for term in DELIVERY_TERMS) and any(term in text for term in ["应", "须", "必须", "负责", "完成", "提交", "提供", "承诺", "服务期限", "工期"]):
            return "delivery_acceptance_commitment"
        if any(term in text for term in SECURITY_TERMS) and has_obligation_or_task(text):
            return "confidentiality_security_requirement"
        return "technical_mandatory_requirement"
    if any(term in text for term in SECURITY_TERMS) and has_obligation_or_task(text):
        return "confidentiality_security_requirement"
    if any(term in text for term in DELIVERY_TERMS) and any(term in text for term in ["应", "须", "必须", "负责", "完成", "提交", "提供", "承诺", "服务期限", "工期"]):
        return "delivery_acceptance_commitment"
    if any(term in text for term in FORMAT_TERMS) or (
        "投标文件" in text and any(term in text for term in ["应", "须", "不得", "上传", "提交", "递交", "编制", "填写", "格式"])
    ):
        return "format_submission_requirement"
    if any(term in text for term in ["评分", "得分", "加分", "扣分"]):
        return "score_related_requirement"
    if external_reference:
        return "reference_only" if external_reference.get("resolved") else "needs_review"
    if any(term in text for term in ["应当对招标文件", "作出响应", "实质性要求"]):
        return "mandatory_substantive_response"
    if semantics["polarity"] == "uncertain":
        return "needs_review"
    if region in {"bidder_instruction", "business_file_format", "technical_file_format", "price_file_format"} and any(
        term in text for term in ["投标人应", "投标人须", "投标人不得", "我方承诺", "应当", "必须"]
    ):
        return "mandatory_substantive_response"
    return "reference_only"


def infer_compliance_level(
    text: str,
    clause: dict[str, Any],
    semantics: dict[str, Any],
    requirement_type: str,
    external_reference: dict[str, Any] | None,
) -> str:
    if semantics["polarity"] == "negative_exception":
        return "reference_only"
    if semantics["explicit_fatal"]:
        return "fatal_if_missing"
    if requirement_type == "score_related_requirement":
        return "score_related"
    if requirement_type == "qualification_requirement":
        return "evidence_required"
    if requirement_type in {"signature_seal_requirement", "format_submission_requirement", "special_table_requirement"}:
        return "format_required"
    if requirement_type in {"mandatory_substantive_response", "technical_mandatory_requirement", "deviation_response_requirement", "delivery_acceptance_commitment", "confidentiality_security_requirement"}:
        return "must_respond"
    if requirement_type == "reference_only" and external_reference and external_reference.get("resolved"):
        return "reference_only"
    if requirement_type == "reference_only":
        return "reference_only"
    if requirement_type == "needs_review" or (external_reference and not external_reference.get("resolved")):
        return "needs_review"
    return "recommended"


def infer_risk_type(
    requirement_type: str,
    compliance_level: str,
    semantics: dict[str, Any],
    external_reference: dict[str, Any] | None,
) -> str:
    if semantics["polarity"] == "negative_exception":
        return "none"
    if compliance_level == "fatal_if_missing":
        if requirement_type == "signature_seal_requirement":
            return "signature_seal_risk"
        if requirement_type == "qualification_requirement":
            return "qualification_risk"
        if requirement_type in {"format_submission_requirement", "special_table_requirement", "deviation_response_requirement"}:
            return "format_risk"
        if requirement_type == "mandatory_substantive_response":
            return "substantial_response_risk"
        return "bid_invalidity_risk"
    if external_reference and not external_reference.get("resolved"):
        return "unclear_reference_risk"
    if requirement_type == "qualification_requirement":
        return "qualification_risk"
    if requirement_type == "signature_seal_requirement":
        return "signature_seal_risk"
    if requirement_type in {"format_submission_requirement", "special_table_requirement", "deviation_response_requirement"}:
        return "format_risk"
    if requirement_type in {"mandatory_substantive_response", "technical_mandatory_requirement"}:
        return "substantial_response_risk"
    if requirement_type == "score_related_requirement":
        return "scoring_loss_risk"
    if requirement_type == "needs_review":
        return "unclear_reference_risk"
    return "none"


def split_requirement_segments(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    chunks = re.split(r"(?<=[。；！？])\s+|(?<=。)|(?<=；)|(?<=！)|(?<=？)", normalized)
    result: list[str] = []
    for chunk in chunks:
        chunk = normalize_text(chunk)
        if not chunk:
            continue
        if len(chunk) <= 700:
            result.append(chunk)
            continue
        result.extend(normalize_text(item) for item in re.split(r"(?=[（(]\d+[）)])|(?=\d+[.、])", chunk) if normalize_text(item))
    return result


def segment_is_requirement_candidate(text: str, clause: dict[str, Any]) -> bool:
    if len(text) < 8:
        return False
    if has_obligation_or_task(text) or any(term in text for term in REQUIREMENT_HINTS):
        return True
    if any(term in text for term in NEGATION_TERMS + FATAL_TERMS + SPECIAL_TABLE_TERMS + FORMAT_TERMS):
        return True
    if any(term in text for term in ["营业执照", "资质证书", "资格评审", "资格审查", "失信", "行贿犯罪", "破产", "类似项目", "项目负责人"]):
        return True
    if str(clause.get("document_region") or "") == "technical_spec" and has_obligation_or_task(text):
        return True
    return bool(resolve_reference_text(text))


def has_obligation_or_task(text: str) -> bool:
    if any(term in text for term in ["须", "必须", "不得", "负责", "完成", "提交", "提供", "承诺"]):
        return True
    if re.search(r"(?<![响相适])应(?:当|按|在|对|向|由|与|为|提供|提交|负责|保证|明确|配备|符合|遵守|采用|完成|满足|说明|填写|上传|响应|承诺|予以|具备|附|使用|包括|根据)", text):
        return True
    if any(subject in text for subject in ["本研究", "本项目研究", "研究内容包括"]) and any(action in text for action in ["研究", "设计", "构建", "实现", "支持", "形成", "开发"]):
        return True
    return bool(re.match(r"^(?:\d+[、.]|[（(]\d+[）)])?\s*(?:完成|设计|构建|实现|支持|研究)", text))


def resolve_reference_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in EXTERNAL_REFERENCE_PATTERNS)


def resolve_external_reference(
    text: str,
    source_files: list[dict[str, Any]],
    clause: dict[str, Any],
) -> dict[str, Any] | None:
    matches = [match.group(0) for pattern in EXTERNAL_REFERENCE_PATTERNS for match in pattern.finditer(text)]
    if not matches:
        return None
    reference_text = "；".join(unique(matches))
    candidates: list[dict[str, Any]] = []
    lowered = reference_text.lower()
    internal_reference = any(term in reference_text for term in ["第六章", "第三章", "投标人须知前附表", "下文所列", "本章"])
    for item in source_files:
        name = str(item.get("original_name") or "")
        source_type = tender_source_type(item)
        matched = False
        if "技术规范" in reference_text and source_type == "technical_spec":
            matched = True
        elif ("评标办法" in reference_text or "评分标准" in reference_text) and source_type == "score_table":
            matched = True
        elif ("需求一览表" in reference_text or "招标公告附件" in reference_text) and source_type in {"procurement_package_table", "tender_notice_attachment"}:
            matched = True
        elif "附件" in reference_text and any(token in name for token in ["附件", "需求一览表"]):
            matched = True
        elif internal_reference and source_type == "full_tender_document":
            matched = True
        elif "详见" in reference_text and reference_target_exists(reference_text, str(item.get("parsed_text") or "")):
            matched = True
        elif name and name.lower() in lowered:
            matched = True
        if matched:
            candidates.append(
                {
                    "source_file_id": item.get("id") or "",
                    "source_file_name": name,
                    "document_type": source_type,
                    "source_type": "tender_original_fact",
                }
            )
    external_standard = "国家电网" in reference_text and "标准" in reference_text
    resolved = bool(candidates) and not external_standard
    return {
        "reference_text": reference_text,
        "target_candidates": candidates,
        "resolved": resolved,
        "resolution_confidence": 0.9 if resolved else 0.25,
        "unresolved_reason": "" if resolved else "当前文件集中未定位到被引用标准、附件或具体章节内容。",
        "target_ref_ids": [str(item.get("source_file_id") or "") for item in candidates] if resolved else [],
        "human_review_required": not resolved,
        "source_type": "system_judgement",
    }


def reference_target_exists(reference_text: str, parsed_text: str) -> bool:
    target = re.sub(r"^(详见|见|按)", "", normalize_text(reference_text))
    target = re.sub(r"[“”\"'，,（(].*$", "", target).strip()
    return len(target) >= 5 and target in normalize_text(parsed_text)


def build_requirement_source_ref(
    clause: dict[str, Any],
    excerpt: str,
    context_before: str,
    context_after: str,
) -> dict[str, Any]:
    location = clause.get("source_location") if isinstance(clause.get("source_location"), dict) else {}
    paragraph_index = location.get("paragraph_index")
    precision = "clause_approximate" if clause.get("section_path") else "snippet_only"
    return {
        "source_file_id": clause.get("file_id") or clause.get("source_file_id") or "",
        "source_file_name": clause.get("file_name") or clause.get("source_file_name") or "",
        "document_type": clause.get("source_type") or clause.get("document_region") or "unknown",
        "clause_path": " > ".join(str(item) for item in list(clause.get("section_path") or [])),
        "source_location": {
            "paragraph_index": paragraph_index,
            "table_index": location.get("table_index"),
            "row_index": location.get("row_index"),
            "sheet_name": location.get("sheet_name"),
        },
        "location_precision": precision,
        "excerpt": excerpt,
        "context_before": context_before,
        "context_after": context_after,
        "clause_id": clause.get("clause_id") or "",
        "source_type": "tender_original_fact",
    }


def infer_applicable_scope(clause: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    region = str(clause.get("document_region") or "")
    source_type = str(clause.get("source_type") or "")
    table_context = clause.get("table_context") if isinstance(clause.get("table_context"), dict) else {}
    human_review = False
    if table_context.get("is_current_lot") is True or source_type == "technical_spec":
        scope_type = "current_package"
        basis = "当前包需求行或当前技术规范书。"
    elif region in {"technical_file_format", "technical_spec"}:
        scope_type = "technical_response"
        basis = "条款位于技术文件格式或技术规范区域。"
    elif region == "business_file_format":
        scope_type = "commercial_response"
        basis = "条款位于商务文件格式区域。"
    elif region == "price_file_format":
        scope_type = "price_response"
        basis = "条款位于价格文件格式区域。"
    elif str(clause.get("clause_type") or "") in {"qualification_requirement", "business_license", "personnel_requirement"}:
        scope_type = "qualification_documents"
        basis = "条款属于资格或证明材料要求。"
    elif source_type in {"full_tender_document", "tender_notice", "tender_attachment", "tender_notice_attachment"}:
        scope_type = "service_projects"
        basis = "通用服务招标文件要求，适用于本标段但不认定为当前包专属。"
    else:
        scope_type = "unknown"
        basis = "未能稳定判断条款适用范围。"
        human_review = True
    return {
        "scope_type": scope_type,
        "lot_name": context.get("lot_name") or "",
        "package_number": project.get("package_number") or "",
        "applicable": True,
        "applicability_basis": {"text": basis, "source_type": "system_judgement"},
        "human_review_required": human_review,
        "source_type": "system_judgement",
    }


def relate_context(
    text: str,
    clause: dict[str, Any],
    requirement_type: str,
    context: dict[str, Any],
) -> tuple[list[str], list[str]]:
    chapter_ids: list[str] = []
    score_ids: list[str] = []
    source_file_id = str(clause.get("file_id") or "")
    clause_path = " > ".join(str(item) for item in list(clause.get("section_path") or []))
    for chapter in list(context.get("chapters") or []):
        chapter_id = str(chapter.get("chapter_id") or "")
        refs = [
            item
            for key in ["tender_requirement_refs", "technical_spec_refs", "response_format_refs"]
            for item in list(chapter.get(key) or [])
        ]
        direct_ref = any(
            str(ref.get("source_file_id") or "") == source_file_id
            and (
                paths_overlap(str(ref.get("clause_path") or ""), clause_path)
                or excerpts_overlap(str(ref.get("excerpt") or ""), text)
            )
            for ref in refs
        )
        keyword_match = chapter_requirement_keyword_match(
            str(chapter.get("chapter_title") or ""),
            text,
            requirement_type,
            str(clause.get("document_region") or ""),
        )
        if direct_ref or keyword_match:
            chapter_ids.append(chapter_id)
            score_ids.extend(str(item.get("score_item_id") or "") for item in list(chapter.get("score_refs") or []) if item.get("score_item_id"))
    return unique(chapter_ids), unique(score_ids)


def chapter_requirement_keyword_match(chapter_title: str, text: str, requirement_type: str, document_region: str) -> bool:
    direct = [
        (["负责人"], ["负责人"]),
        (["团队", "组织"], ["团队", "主要人员", "人员汇总表"]),
        (["业绩"], ["业绩", "类似项目"]),
        (["专利", "知识产权"], ["专利", "知识产权"]),
        (["绩效"], ["供应商绩效", "绩效评价"]),
        (["进度"], ["进度", "工期", "服务期限", "里程碑"]),
        (["服务方案"], ["服务方案", "技术方案", "研究内容", "技术指标", "验收", "交付"]),
    ]
    if any(any(left in chapter_title for left in chapter_terms) and any(right in text for right in text_terms) for chapter_terms, text_terms in direct):
        return True
    if "总体评价" in chapter_title:
        if requirement_type == "mandatory_substantive_response" and document_region in {"bidder_instruction", "technical_file_format", "response_format"}:
            return True
        if requirement_type in {"signature_seal_requirement", "format_submission_requirement", "deviation_response_requirement", "special_table_requirement"} and document_region in {"technical_file_format", "response_format"}:
            return True
    if "服务方案" in chapter_title and requirement_type in {"technical_mandatory_requirement", "confidentiality_security_requirement"}:
        return True
    return False


def paths_overlap(left: str, right: str) -> bool:
    left = normalize_text(left)
    right = normalize_text(right)
    return bool(left and right and (left in right or right in left))


def excerpts_overlap(left: str, right: str) -> bool:
    left_terms = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,}", left))
    right_terms = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,}", right))
    return bool(left_terms.intersection(right_terms))


def build_score_requirements(
    project: dict[str, Any],
    context: dict[str, Any],
    source_files: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_by_id = {str(item.get("id") or ""): item for item in source_files}
    score_file_id = str(project.get("score_file_id") or "")
    score_file = source_by_id.get(score_file_id, {})
    result: list[dict[str, Any]] = []
    chapter_by_score = {
        str(ref.get("score_item_id") or ""): str(chapter.get("chapter_id") or "")
        for chapter in list(context.get("chapters") or [])
        for ref in list(chapter.get("score_refs") or [])
    }
    for item in list(project.get("score_items") or []):
        score_id = str(item.get("id") or "")
        raw_text = str(item.get("raw_text") or item.get("score_rule") or item.get("item_name") or "")
        if not raw_text:
            continue
        requirement_id = stable_requirement_id(f"score::{score_id}", raw_text)
        chapter_id = chapter_by_score.get(score_id)
        normalized_source_type = "rule_derived" if any(term in raw_text for term in ["每提供", "每增加", "每项", "上限", "最高加", "得分="]) else "system_judgement"
        result.append(
            {
                "requirement_id": requirement_id,
                "title": str(item.get("item_name") or "评分要求"),
                "requirement_type": "score_related_requirement",
                "raw_text": raw_text,
                "normalized_requirement": {"text": str(item.get("score_rule") or raw_text), "source_type": normalized_source_type},
                "required_action": {"text": "按评分规则准备对应响应内容和证明材料。", "source_type": "system_judgement"},
                "required_response_content": {"items": [str(item.get("score_rule") or raw_text)], "source_type": "suggested_writing"},
                "required_materials": [],
                "source_ref": {
                    "source_file_id": score_file_id,
                    "source_file_name": score_file.get("original_name") or "评标办法-服务.doc",
                    "document_type": "score_table",
                    "clause_path": str(item.get("source_template_name") or ""),
                    "source_location": {},
                    "location_precision": "file_only",
                    "excerpt": raw_text,
                    "context_before": "",
                    "context_after": "",
                    "clause_id": "",
                    "source_type": "tender_original_fact",
                },
                "applicable_scope": {
                    "scope_type": "current_lot",
                    "lot_name": context.get("lot_name") or "",
                    "package_number": (context.get("project") or {}).get("package_number") or "",
                    "applicable": True,
                    "applicability_basis": {"text": "评分项由当前标段评分表定位结果生成。", "source_type": "system_judgement"},
                    "human_review_required": False,
                    "source_type": "system_judgement",
                },
                "compliance_level": "score_related",
                "risk_type": "scoring_loss_risk",
                "risk_basis": {"text": "未响应可能影响对应评分项得分，不等同于否决风险。", "source_type": "system_judgement"},
                "related_score_item_ids": [score_id],
                "related_context_chapter_ids": [chapter_id] if chapter_id else [],
                "related_special_table_ids": [],
                "external_reference": None,
                "source_type": "tender_original_fact",
                "confidence": 0.95,
                "human_review_required": False,
                "human_review_questions": [],
                "status": "identified",
                "detected_risk_terms": [],
                "detected_negation_terms": [term for term in NEGATION_TERMS if term in raw_text],
                "detected_condition_terms": [term for term in CONDITION_TERMS if term in raw_text],
                "polarity": "negative_exception" if any(term in raw_text for term in NEGATION_TERMS) else "positive_requirement",
                "classification_basis": {"text": "当前标段评分项解析结果。", "source_type": "system_judgement"},
            }
        )
    return result


def related_special_table_ids(text: str, requirement_type: str) -> list[str]:
    mapping = {
        "技术偏差表": "technical_deviation_table",
        "商务偏差表": "commercial_deviation_table",
        "拟委任的主要人员汇总表": "key_personnel_summary_table",
        "主要人员简历表": "personnel_resume_table",
        "近年完成的类似项目情况表": "similar_project_table",
        "专项投标文件": "special_tender_document",
    }
    result = [table_id for phrase, table_id in mapping.items() if phrase in text]
    if requirement_type == "special_table_requirement" and not result:
        result.append("unresolved_special_table")
    return result


def required_materials_for(text: str, requirement_type: str) -> list[str]:
    result: list[str] = []
    mappings = [
        (["营业执照", "法人证书"], "business_license"),
        (["资质", "资格证书"], "qualification_certificate"),
        (["业绩", "类似项目"], "company_performance_evidence"),
        (["项目负责人", "主要人员", "简历表"], "personnel_resume_and_certificates"),
        (["授权委托书", "法定代表人授权"], "authorization_document"),
        (["签字", "盖章", "签章"], "signed_and_sealed_document"),
        (["验收"], "acceptance_material"),
        (["偏差表"], "completed_deviation_table"),
    ]
    for terms, material in mappings:
        if any(term in text for term in terms):
            result.append(material)
    if requirement_type == "special_table_requirement":
        result.append("completed_special_table")
    return unique(result)


def required_response_content_for(text: str, requirement_type: str) -> list[str]:
    if requirement_type == "reference_only":
        return []
    if requirement_type == "score_related_requirement":
        return ["逐项覆盖评分规则要求。"]
    if requirement_type == "deviation_response_requirement":
        return ["逐条列明偏差内容、偏差说明和响应结论。"]
    if requirement_type == "technical_mandatory_requirement":
        return ["在技术响应中逐条说明满足方式、实施方法和交付结果。"]
    return [normalize_requirement_summary(text, requirement_type)]


def required_action_for(requirement_type: str, compliance_level: str) -> str:
    if compliance_level == "fatal_if_missing":
        return "按原文条件完成响应并进行提交前合规复核。"
    mapping = {
        "mandatory_substantive_response": "在对应响应文件章节中作出明确、逐条的实质性响应。",
        "qualification_requirement": "准备并提交可核验的资格或证明材料。",
        "technical_mandatory_requirement": "在技术响应章节逐条说明满足方式。",
        "signature_seal_requirement": "按指定主体和位置完成签字、盖章或授权。",
        "format_submission_requirement": "按规定格式、组成和提交方式编制文件。",
        "deviation_response_requirement": "填写并复核对应偏差表。",
        "special_table_requirement": "填写、复核并提交指定专项表格。",
        "delivery_acceptance_commitment": "在响应中明确交付、工期、验收或服务承诺。",
        "confidentiality_security_requirement": "在响应中明确保密或安全措施并遵守原文要求。",
        "score_related_requirement": "按评分规则准备响应内容和证明材料。",
        "reference_only": "核对引用目标并按目标内容执行。",
        "needs_review": "人工核对原文语义、适用范围和响应动作。",
        "potential_invalidity_risk": "核对触发条件并完成合规响应。",
    }
    return mapping.get(requirement_type, "人工复核后确定响应动作。")


def normalize_requirement_summary(text: str, requirement_type: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:300] + ("..." if len(clean) > 300 else "")


def requirement_title(text: str, clause: dict[str, Any], requirement_type: str) -> str:
    title = str(clause.get("clause_title") or "").strip()
    if title and len(title) <= 80:
        return title
    labels = {
        "potential_invalidity_risk": "可能涉及无效或否决风险",
        "mandatory_substantive_response": "实质性响应要求",
        "qualification_requirement": "资格和证明材料要求",
        "technical_mandatory_requirement": "强制技术响应要求",
        "signature_seal_requirement": "签字盖章或授权要求",
        "format_submission_requirement": "文件格式与提交要求",
        "deviation_response_requirement": "偏差表响应要求",
        "special_table_requirement": "专项表格要求",
        "delivery_acceptance_commitment": "交付验收与服务承诺",
        "confidentiality_security_requirement": "保密与安全要求",
        "score_related_requirement": "评分相关要求",
        "reference_only": "引用或说明条款",
        "needs_review": "待人工复核要求",
    }
    return labels.get(requirement_type, text[:60])


def should_require_human_review(
    requirement_type: str,
    compliance_level: str,
    semantics: dict[str, Any],
    external_reference: dict[str, Any] | None,
    clause: dict[str, Any],
) -> bool:
    if requirement_type == "needs_review" or compliance_level == "needs_review":
        return True
    if external_reference and not external_reference.get("resolved"):
        return True
    if semantics["polarity"] == "uncertain":
        return True
    if str(clause.get("document_region") or "") == "unknown":
        return True
    return False


def build_human_review_questions(
    text: str,
    requirement_type: str,
    external_reference: dict[str, Any] | None,
    clause: dict[str, Any],
) -> list[str]:
    questions = []
    if external_reference and not external_reference.get("resolved"):
        questions.append("请确认被引用文件、附件、标准或章节是否已入库，并定位具体要求。")
    if requirement_type == "needs_review":
        questions.append("请核对完整上下文，确认该条款属于强制响应、格式要求还是仅供参考。")
    if str(clause.get("document_region") or "") == "unknown":
        questions.append("请确认该条款在招标文件中的章节归属和适用范围。")
    return unique(questions)


def build_risk_basis(semantics: dict[str, Any], compliance_level: str, requirement_type: str) -> str:
    if semantics["polarity"] == "negative_exception":
        return "原文明确表达不构成否决或无效依据，不归类为致命风险。"
    if compliance_level == "fatal_if_missing":
        return "原文包含明确条件与否决、无效、不予评审或资格审查不通过后果。"
    if requirement_type == "score_related_requirement":
        return "该要求影响评分准备，不自动推定为无效或否决风险。"
    return semantics["classification_basis"]


def classification_confidence(
    requirement_type: str,
    compliance_level: str,
    semantics: dict[str, Any],
    clause: dict[str, Any],
) -> float:
    if semantics["polarity"] == "negative_exception" or compliance_level == "fatal_if_missing":
        return 0.96
    if requirement_type == "needs_review" or str(clause.get("document_region") or "") == "unknown":
        return 0.45
    if requirement_type in {"signature_seal_requirement", "deviation_response_requirement", "special_table_requirement", "score_related_requirement"}:
        return 0.9
    return 0.78


def build_score_ref_map(context: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("score_item_id") or ""): str(chapter.get("chapter_id") or "")
        for chapter in list(context.get("chapters") or [])
        for item in list(chapter.get("score_refs") or [])
    }


def build_requirement_conflicts(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts = []
    by_text: dict[str, set[str]] = {}
    for item in requirements:
        key = normalize_text(str(item.get("raw_text") or ""))
        by_text.setdefault(key, set()).add(str(item.get("compliance_level") or ""))
    for text, levels in by_text.items():
        if len(levels) > 1:
            conflicts.append(
                {
                    "conflict_id": "conflict-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
                    "raw_text": text,
                    "compliance_levels": sorted(levels),
                    "status": "needs_human_confirmation",
                    "source_type": "human_confirmation_required",
                }
            )
    return conflicts


def build_statistics(
    requirements: list[dict[str, Any]],
    context: dict[str, Any],
    unresolved: list[dict[str, Any]],
) -> dict[str, Any]:
    chapter_ids = [str(item.get("chapter_id") or "") for item in list(context.get("chapters") or [])]
    linked = {
        chapter_id
        for item in requirements
        for chapter_id in list(item.get("related_context_chapter_ids") or [])
        if chapter_id
    }
    return {
        "requirement_count": len(requirements),
        "requirement_type_counts": dict(sorted(Counter(str(item.get("requirement_type")) for item in requirements).items())),
        "compliance_level_counts": dict(sorted(Counter(str(item.get("compliance_level")) for item in requirements).items())),
        "risk_type_counts": dict(sorted(Counter(str(item.get("risk_type")) for item in requirements).items())),
        "polarity_counts": dict(sorted(Counter(str(item.get("polarity")) for item in requirements).items())),
        "fatal_if_missing_count": len([item for item in requirements if item.get("compliance_level") == "fatal_if_missing"]),
        "must_respond_count": len([item for item in requirements if item.get("compliance_level") == "must_respond"]),
        "human_review_required_count": len([item for item in requirements if item.get("human_review_required")]),
        "unresolved_external_reference_count": len(unresolved),
        "context_chapter_coverage": {
            "chapter_count": len(chapter_ids),
            "linked_chapter_count": len(linked.intersection(chapter_ids)),
            "linked_chapter_ids": sorted(linked.intersection(chapter_ids)),
            "unlinked_chapter_ids": [item for item in chapter_ids if item not in linked],
        },
    }


def build_warnings(
    requirements: list[dict[str, Any]],
    context: dict[str, Any],
    unresolved: list[dict[str, Any]],
) -> list[str]:
    warnings = []
    if unresolved:
        warnings.append(f"存在 {len(unresolved)} 条未解析外部引用，需人工补充目标文件或章节。")
    if any(item.get("status") == "needs_human_confirmation" for item in list(context.get("project_conflicts") or [])):
        warnings.append("沿用 technical_response_context.json 中的项目字段冲突和人工确认状态。")
    if any(item.get("location_precision") in {"file_only", "unknown"} for item in [req.get("source_ref") or {} for req in requirements]):
        warnings.append("部分来源只能定位到文件级，未生成虚假页码或表格坐标。")
    return warnings


def detect_legacy_disqualification_issues(project: dict[str, Any]) -> list[str]:
    warnings = []
    for item in list(project.get("disqualification_rules") or []):
        text = str(item.get("requirement") if isinstance(item, dict) else item or "")
        if any(term in text for term in NEGATION_TERMS):
            warnings.append("旧 disqualification_rules 含明确不否决语义，矩阵已忽略旧分类并按 negative_exception/reference_only 重新判断。")
    return unique(warnings)


def build_unresolved_reference(item: dict[str, Any]) -> dict[str, Any]:
    ref = item.get("external_reference") or {}
    return {
        "requirement_id": item.get("requirement_id"),
        "reference_text": ref.get("reference_text") or "",
        "target_candidates": list(ref.get("target_candidates") or []),
        "resolution_confidence": ref.get("resolution_confidence"),
        "unresolved_reason": ref.get("unresolved_reason") or "",
        "human_review_required": True,
        "source_type": "human_confirmation_required",
    }


def build_global_manual_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    questions = list(item.get("human_review_questions") or [])
    question = str((questions[0] or {}).get("question") if questions and isinstance(questions[0], dict) else "请人工复核该要求。")
    return {
        "confirm_id": f"confirm-requirement-{index:04d}",
        "category": "tender_requirement_review",
        "question": question,
        "related_field": item.get("requirement_id") or "",
        "current_value": item.get("normalized_requirement") or {},
        "candidate_values": [],
        "source_refs": [item.get("source_ref") or {}],
        "reason": (item.get("classification_basis") or {}).get("text") or "",
        "priority": "high" if item.get("risk_type") != "none" else "medium",
        "source_type": "human_confirmation_required",
    }


def update_technical_context_requirement_index(
    context: dict[str, Any],
    requirements: list[dict[str, Any]],
    matrix_path: Path,
    generated_at: str,
) -> None:
    by_chapter: dict[str, list[str]] = {}
    for item in requirements:
        for chapter_id in list(item.get("related_context_chapter_ids") or []):
            by_chapter.setdefault(str(chapter_id), []).append(str(item.get("requirement_id") or ""))
    for chapter in list(context.get("chapters") or []):
        chapter_id = str(chapter.get("chapter_id") or "")
        chapter["requirement_ids"] = unique(by_chapter.get(chapter_id, []))
        for key in ["tender_requirement_refs", "technical_spec_refs", "response_format_refs"]:
            for ref in list(chapter.get(key) or []):
                matched = [
                    str(item.get("requirement_id") or "")
                    for item in requirements
                    if chapter_id in list(item.get("related_context_chapter_ids") or [])
                    and source_ref_matches_context_ref(item.get("source_ref") or {}, ref)
                ]
                ref["requirement_ids"] = unique(matched)
    context["requirement_matrix_index"] = {
        "schema_version": SCHEMA_VERSION,
        "matrix_path": str(matrix_path),
        "generated_at": generated_at,
        "requirement_count": len(requirements),
        "source_type": "system_judgement",
    }


def source_ref_matches_context_ref(source_ref: dict[str, Any], context_ref: dict[str, Any]) -> bool:
    if str(source_ref.get("source_file_id") or "") != str(context_ref.get("source_file_id") or ""):
        return False
    return paths_overlap(str(source_ref.get("clause_path") or ""), str(context_ref.get("clause_path") or "")) or excerpts_overlap(
        str(source_ref.get("excerpt") or ""), str(context_ref.get("excerpt") or "")
    )


def write_matrix_outputs(matrix: dict[str, Any], matrix_path: Path) -> None:
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_matrix_markdown(matrix)
    ensure_no_decision_terms(markdown)
    (matrix_path.parent / "tender_requirement_matrix.md").write_text(markdown, encoding="utf-8")
    for name, predicate in DERIVED_OUTPUTS.items():
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": matrix.get("generated_at"),
            "lot_name": matrix.get("lot_name"),
            "generated_from": str(matrix_path),
            "classification": name,
            "requirement_ids": [item.get("requirement_id") for item in list(matrix.get("requirements") or []) if predicate(item)],
            "requirements": [item for item in list(matrix.get("requirements") or []) if predicate(item)],
            "source_type": "system_judgement",
        }
        if name == "manual_review_requirements":
            payload["global_manual_confirm_items"] = list(matrix.get("global_manual_confirm_items") or [])
        (matrix_path.parent / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_matrix_markdown(matrix: dict[str, Any]) -> str:
    groups = [
        ("可能涉及无效/否决风险", lambda item: item.get("compliance_level") == "fatal_if_missing"),
        ("必须实质性响应", lambda item: item.get("requirement_type") == "mandatory_substantive_response"),
        ("资格和证明材料要求", lambda item: item.get("requirement_type") == "qualification_requirement"),
        ("强制技术要求", lambda item: item.get("requirement_type") == "technical_mandatory_requirement"),
        ("签字盖章和授权要求", lambda item: item.get("requirement_type") == "signature_seal_requirement"),
        ("格式、组成和递交要求", lambda item: item.get("requirement_type") == "format_submission_requirement"),
        ("偏差表和专项表格", lambda item: item.get("requirement_type") in {"deviation_response_requirement", "special_table_requirement"}),
        ("交付、验收、质量、保密和服务承诺", lambda item: item.get("requirement_type") in {"delivery_acceptance_commitment", "confidentiality_security_requirement"}),
        ("评分相关要求", lambda item: item.get("requirement_type") == "score_related_requirement"),
        ("需人工复核", lambda item: item.get("human_review_required")),
    ]
    lines = [
        "# 招标要求与合规约束矩阵",
        "",
        f"- 标段：{matrix.get('lot_name') or '-'}",
        f"- 要求总数：{(matrix.get('statistics') or {}).get('requirement_count', 0)}",
        "- 正式数据源：tender_requirement_matrix.json",
        "",
    ]
    requirements = list(matrix.get("requirements") or [])
    for heading, predicate in groups:
        rows = [item for item in requirements if predicate(item)]
        lines.extend([f"## {heading}", ""])
        if not rows:
            lines.extend(["- 当前未识别到对应条目。", ""])
            continue
        for item in rows:
            source = item.get("source_ref") or {}
            scope = item.get("applicable_scope") or {}
            questions = "；".join(
                str(question.get("question") or "") for question in list(item.get("human_review_questions") or []) if isinstance(question, dict)
            ) or "-"
            materials = "；".join(str(material.get("material_type") or "") for material in list(item.get("required_materials") or [])) or "-"
            lines.extend(
                [
                    f"### {item.get('title') or item.get('requirement_id')}",
                    "",
                    f"- requirement_id：`{item.get('requirement_id')}`",
                    f"- 原文摘录：{source.get('excerpt') or '-'}",
                    f"- 来源：{source.get('source_file_name') or '-'} / {source.get('clause_path') or '-'} / {source.get('location_precision') or 'unknown'}",
                    f"- 适用范围：{scope.get('scope_type') or 'unknown'}；{(scope.get('applicability_basis') or {}).get('text') or '-'}",
                    f"- 必须采取的动作：{(item.get('required_action') or {}).get('text') or '-'}",
                    f"- 响应或材料：{materials}",
                    f"- 风险级别：{item.get('compliance_level')} / {item.get('risk_type')}",
                    f"- 关联评分项：{'、'.join(item.get('related_score_item_ids') or []) or '-'}",
                    f"- 关联技术响应章节：{'、'.join(item.get('related_context_chapter_ids') or []) or '-'}",
                    f"- 人工复核问题：{questions}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def matrix_source_file(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file_id": item.get("id") or "",
        "source_file_name": item.get("original_name") or "",
        "document_type": tender_source_type(item),
        "source_type": "tender_original_fact",
    }


def output_file_map(matrix_dir: Path) -> dict[str, str]:
    return {
        "tender_requirement_matrix_json": str(matrix_dir / "tender_requirement_matrix.json"),
        "tender_requirement_matrix_markdown": str(matrix_dir / "tender_requirement_matrix.md"),
        **{name: str(matrix_dir / f"{name}.json") for name in DERIVED_OUTPUTS},
    }


def dedupe_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in requirements:
        key = (normalize_text(str(item.get("raw_text") or "")), str(item.get("requirement_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def requirement_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    level_order = {"fatal_if_missing": 0, "must_respond": 1, "evidence_required": 2, "format_required": 3, "score_related": 4, "needs_review": 5, "recommended": 6, "reference_only": 7}
    source = item.get("source_ref") or {}
    return level_order.get(str(item.get("compliance_level") or ""), 9), str(source.get("source_file_id") or ""), str(item.get("requirement_id") or "")


def validate_context(context: dict[str, Any], lot_name: str) -> None:
    if str(context.get("lot_name") or "") != lot_name:
        raise ValueError(f"technical_response_context lot_name mismatch: expected {lot_name}")
    if not isinstance(context.get("chapters"), list):
        raise ValueError("technical_response_context.chapters not found")


def validate_matrix(matrix: dict[str, Any]) -> None:
    allowed_precisions = {"page_exact", "table_cell_exact", "paragraph_exact", "clause_approximate", "snippet_only", "file_only", "unknown"}
    for item in list(matrix.get("requirements") or []):
        if item.get("requirement_type") not in REQUIREMENT_TYPES:
            raise ValueError(f"invalid requirement_type: {item.get('requirement_type')}")
        if item.get("compliance_level") not in COMPLIANCE_LEVELS:
            raise ValueError(f"invalid compliance_level: {item.get('compliance_level')}")
        if item.get("risk_type") not in RISK_TYPES:
            raise ValueError(f"invalid risk_type: {item.get('risk_type')}")
        if item.get("polarity") not in POLARITIES:
            raise ValueError(f"invalid polarity: {item.get('polarity')}")
        source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        if not source_ref or item.get("source_type") != "tender_original_fact":
            raise ValueError(f"requirement source missing: {item.get('requirement_id')}")
        if source_ref.get("location_precision") not in allowed_precisions:
            raise ValueError(f"invalid location_precision: {source_ref.get('location_precision')}")
        if item.get("compliance_level") == "fatal_if_missing" and not has_explicit_fatal_relation(str(item.get("raw_text") or "")):
            raise ValueError(f"fatal_if_missing lacks explicit fatal relation: {item.get('requirement_id')}")
        if item.get("polarity") == "negative_exception" and (item.get("compliance_level") == "fatal_if_missing" or item.get("risk_type") == "bid_invalidity_risk"):
            raise ValueError(f"negative exception misclassified: {item.get('requirement_id')}")


def ensure_no_decision_terms(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    for term in FORBIDDEN_DECISION_TERMS:
        if term.lower() in text.lower():
            raise ValueError(f"forbidden bid decision term found: {term}")


def summarize_requirements(requirements: list[dict[str, Any]], predicate: Any) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": item.get("requirement_id"),
            "title": item.get("title"),
            "raw_text": item.get("raw_text"),
            "source_ref": item.get("source_ref"),
            "risk_type": item.get("risk_type"),
        }
        for item in requirements
        if predicate(item)
    ]


def stable_requirement_id(clause_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{clause_id}|{normalize_text(text)}".encode("utf-8")).hexdigest()[:14]
    return f"req-{digest}"


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_path_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", str(value or "unknown")).strip("_") or "unknown"


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"JSON file not found: {target}")
    return json.loads(target.read_text(encoding="utf-8") or "{}")


def unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def dedupe_dicts(items: list[dict[str, Any]], key_func: Any) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = key_func(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

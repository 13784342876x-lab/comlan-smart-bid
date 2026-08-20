from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.tender_requirement_matrix import load_json, safe_path_name, unique


SCHEMA_VERSION = "1.0"

ACTION_KINDS = {
    "provide_evidence",
    "write_response",
    "fill_form",
    "comply_format",
    "manual_confirm",
    "external_reference_resolve",
    "no_direct_action",
}

ACTION_PRIORITIES = {"critical", "high", "medium", "low", "informational"}

EVIDENCE_SUBJECT_TYPES = {
    "enterprise_qualification",
    "management_system_certificate",
    "project_performance",
    "contract",
    "award_notice",
    "acceptance_certificate",
    "customer_certificate",
    "personnel_resume",
    "personnel_qualification",
    "personnel_certificate",
    "personnel_project_experience",
    "patent",
    "software_copyright",
    "technical_achievement",
    "financial_evidence",
    "credit_evidence",
    "service_capability",
    "equipment_or_resource",
    "organization_structure",
    "other_enterprise_evidence",
}

QUANTITY_RULE_TYPES = {
    "explicit_minimum",
    "explicit_maximum",
    "score_accumulation",
    "fixed_count",
    "not_explicit",
    "needs_review",
}

COMPLIANCE_ORDER = {
    "fatal_if_missing": 0,
    "must_respond": 1,
    "evidence_required": 2,
    "format_required": 3,
    "score_related": 4,
    "needs_review": 5,
    "recommended": 6,
    "reference_only": 7,
}

RISK_ORDER = {
    "bid_invalidity_risk": 0,
    "substantial_response_risk": 1,
    "qualification_risk": 2,
    "signature_seal_risk": 3,
    "format_risk": 4,
    "scoring_loss_risk": 5,
    "material_missing_risk": 6,
    "unclear_reference_risk": 7,
    "none": 8,
}

FORMAT_TYPES = {"signature_seal_requirement", "format_submission_requirement"}
FORM_TYPES = {"deviation_response_requirement", "special_table_requirement"}
WRITING_TYPES = {
    "mandatory_substantive_response",
    "technical_mandatory_requirement",
    "delivery_acceptance_commitment",
    "confidentiality_security_requirement",
}

EVIDENCE_TERM_MAP: list[tuple[list[str], str, list[str]]] = [
    (["管理体系", "体系认证"], "management_system_certificate", ["管理体系认证证书扫描件", "证书有效期页"]),
    (["类似项目", "项目业绩", "业绩证明", "业绩情况"], "project_performance", ["合同关键页", "中标通知书", "验收证明或用户证明"]),
    (["合同协议", "合同关键页", "合同复印件"], "contract", ["合同关键页"]),
    (["中标通知书"], "award_notice", ["中标通知书"]),
    (["验收报告", "验收证明"], "acceptance_certificate", ["验收证明"]),
    (["用户证明", "委托人出具的证明"], "customer_certificate", ["用户证明"]),
    (["人员简历", "简历表"], "personnel_resume", ["人员简历"]),
    (["项目负责人", "核心成员", "主要人员", "项目团队"], "personnel_qualification", ["人员简历", "项目任命或投入证明"]),
    (["身份证", "学历证", "职称证", "职业资格", "人员证书"], "personnel_certificate", ["人员证书扫描件", "证书有效期页"]),
    (["人员项目经验", "管理过的项目", "工作经验"], "personnel_project_experience", ["人员简历", "项目任命或投入证明", "相关项目合同关键页"]),
    (["专利", "发明专利", "实用新型"], "patent", ["专利证书"]),
    (["软件著作权", "软著"], "software_copyright", ["软件著作权证书"]),
    (["科技成果", "技术成果", "创新成果"], "technical_achievement", ["技术成果证明文件"]),
    (["财务", "审计报告", "研发投入", "科研经费"], "financial_evidence", ["财务或审计证明材料"]),
    (["信用信息", "信用报告", "失信", "企业信用"], "credit_evidence", ["信用信息报告", "企业信用信息公示报告"]),
    (["服务能力", "售后服务"], "service_capability", ["服务能力证明材料"]),
    (["设备设施", "设备配置", "资源配置"], "equipment_or_resource", ["设备或资源清单及证明"]),
    (["组织结构", "组织机构", "管理组织"], "organization_structure", ["组织结构及职责说明"]),
    (["营业执照", "资质证书", "资格证明", "高新技术企业"], "enterprise_qualification", ["营业执照或资质证书扫描件", "证书有效期页"]),
]

MATERIAL_TYPE_MAP = {
    "qualification_certificate": ("enterprise_qualification", ["资质证书扫描件", "证书有效期页"]),
    "business_license": ("enterprise_qualification", ["营业执照扫描件"]),
    "company_performance_evidence": ("project_performance", ["合同关键页", "中标通知书", "验收证明或用户证明"]),
    "acceptance_material": ("acceptance_certificate", ["验收证明"]),
    "personnel_resume_and_certificates": ("personnel_resume", ["人员简历", "人员证书扫描件", "项目任命或投入证明"]),
}

FORM_TERM_MAP = {
    "技术偏差表": "technical_deviation_table",
    "商务偏差表": "commercial_deviation_table",
    "拟委任的主要人员汇总表": "key_personnel_summary_table",
    "主要人员简历表": "personnel_resume_table",
    "近年完成的类似项目情况表": "similar_project_table",
    "专项投标文件": "special_tender_document",
    "报价表": "price_table",
    "开标一览表": "bid_opening_summary_table",
}

QUALITY_PATTERNS = [
    re.compile(r"(?:有效期内|在有效期内|证书有效期[^，。；]{0,30})"),
    re.compile(r"(?:近|最近)[一二三四五六七八九十\d]+年(?:内)?"),
    re.compile(r"(?:采购相关|项目相关|与采购项目有关|类似项目|同类项目)"),
    re.compile(r"(?:合同关键页|合同协议书|中标通知书|验收证明|用户证明|委托人出具的证明文件)"),
    re.compile(r"(?:学历证|职称证|身份证|社保缴费证明|职业资格证)[^，。；]{0,25}"),
]


def build_actionable_requirement_index(
    lot_name: str,
    output_dir: str | Path = "outputs",
    *,
    matrix: dict[str, Any] | None = None,
    update_context_index: bool = True,
) -> dict[str, Any]:
    base = Path(output_dir)
    lot_dir = base / safe_path_name(lot_name)
    matrix_path = lot_dir / "requirements" / "tender_requirement_matrix.json"
    index_path = lot_dir / "requirements" / "actionable_requirement_index.json"
    markdown_path = lot_dir / "requirements" / "actionable_requirement_index.md"
    context_path = lot_dir / "outline" / "technical_response_context.json"

    matrix = deepcopy(matrix) if matrix is not None else load_json(matrix_path)
    validate_matrix_source(matrix, lot_name)
    action_groups, ungrouped, merge_warnings = build_action_groups(list(matrix.get("requirements") or []))
    unresolved_conflicts = build_unresolved_conflicts(matrix, action_groups)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": str(matrix_path),
        "project_ref": deepcopy(matrix.get("project_ref") or {}),
        "action_groups": action_groups,
        "ungrouped_requirements": ungrouped,
        "unresolved_conflicts": unresolved_conflicts,
        "statistics": build_statistics(matrix, action_groups, ungrouped),
        "build_warnings": unique(
            merge_warnings
            + (["存在尚未解决的项目字段或要求冲突，相关行动组已标记人工复核。"] if unresolved_conflicts else [])
        ),
    }
    validate_index(payload)
    ensure_no_decision_terms(payload)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_actionable_requirement_index_markdown(payload), encoding="utf-8")

    if update_context_index and context_path.exists():
        context = load_json(context_path)
        update_technical_context_action_index(context, action_groups, index_path, generated_at)
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "lot_name": lot_name,
        "schema_version": SCHEMA_VERSION,
        "index_path": str(index_path),
        "markdown_path": str(markdown_path),
        "statistics": payload["statistics"],
        "build_warnings": payload["build_warnings"],
    }


def inspect_actionable_requirement_index(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    path = Path(output_dir) / safe_path_name(lot_name) / "requirements" / "actionable_requirement_index.json"
    if not path.exists():
        raise FileNotFoundError(f"actionable_requirement_index.json not found: {path}")
    payload = load_json(path)
    groups = list(payload.get("action_groups") or [])
    return {
        "lot_name": lot_name,
        "index_path": str(path),
        "statistics": payload.get("statistics") or {},
        "provide_evidence": summarize_groups(groups, "provide_evidence"),
        "write_response": summarize_groups(groups, "write_response"),
        "fill_form": summarize_groups(groups, "fill_form"),
        "critical": [summarize_group(item) for item in groups if item.get("action_priority") == "critical"],
        "unresolved_conflicts": list(payload.get("unresolved_conflicts") or []),
        "human_review_count": len([item for item in groups if item.get("human_review_required")]),
        "chapter_coverage": (payload.get("statistics") or {}).get("context_chapter_coverage") or {},
    }


def build_action_groups(requirements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    grouped: list[dict[str, Any]] = []
    ungrouped: list[dict[str, Any]] = []
    warnings: list[str] = []
    buckets: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = {}

    for requirement in requirements:
        requirement_id = str(requirement.get("requirement_id") or "")
        if not requirement_id or not isinstance(requirement.get("source_ref"), dict):
            ungrouped.append({"requirement_id": requirement_id, "reason": "缺少 requirement_id 或 source_ref，无法安全建立执行组。"})
            continue
        action_kind, secondary = infer_action_kinds(requirement)
        semantic_key = semantic_fingerprint(requirement)
        scope_key = scope_fingerprint(requirement)
        polarity = str(requirement.get("polarity") or "uncertain")
        condition_key = condition_fingerprint(requirement)
        quantity = extract_quantity_rule(requirement)
        quantity_key = quantity_fingerprint(quantity)
        bucket_key = (action_kind, scope_key, polarity, condition_key, quantity_key, semantic_key[:40])
        candidates = buckets.setdefault(bucket_key, [])
        target = next((item for item in candidates if requirements_can_merge(item["_seed"], requirement, action_kind)), None)
        if target is None:
            target = new_action_group(requirement, action_kind, secondary, quantity)
            target["_seed"] = requirement
            candidates.append(target)
            grouped.append(target)
        else:
            merge_requirement_into_group(target, requirement, secondary)

    for group in grouped:
        group.pop("_seed", None)
        finalize_action_group(group)
    grouped.sort(key=action_group_sort_key)
    if ungrouped:
        warnings.append(f"{len(ungrouped)} 条 requirement 因缺少稳定来源未分组。")
    return grouped, ungrouped, warnings


def infer_action_kinds(requirement: dict[str, Any]) -> tuple[str, list[str]]:
    requirement_type = str(requirement.get("requirement_type") or "")
    raw = requirement_text(requirement)
    external = requirement.get("external_reference") if isinstance(requirement.get("external_reference"), dict) else None
    human_review = bool(requirement.get("human_review_required")) or bool((requirement.get("applicable_scope") or {}).get("human_review_required"))

    actions: list[str] = []
    if external and not external.get("resolved"):
        actions.append("external_reference_resolve")
    if human_review or requirement_type == "needs_review":
        actions.append("manual_confirm")
    if requirement_type in FORM_TYPES or related_form_ids(requirement):
        actions.append("fill_form")
    if requirement_type in FORMAT_TYPES:
        actions.append("comply_format")
    if requirement_type == "qualification_requirement" and requires_enterprise_evidence(requirement):
        actions.append("provide_evidence")
    if requirement_type == "score_related_requirement":
        if evidence_subjects_for(requirement)[0]:
            actions.append("provide_evidence")
        if score_requires_written_response(raw):
            actions.append("write_response")
    if requirement_type in WRITING_TYPES and bidder_has_direct_action(raw, requirement):
        actions.append("write_response")
    if requirement_type == "potential_invalidity_risk":
        actions.append("comply_format" if any(term in raw for term in ["报价", "清单", "文件", "递交"]) else "manual_confirm")
    if requirement_type in FORM_TYPES and any_evidence_material(requirement):
        actions.append("provide_evidence")
    if requirement_type in {"deviation_response_requirement", "special_table_requirement"} and any(term in raw for term in ["上传", "PDF", "签字", "盖章"]):
        actions.append("comply_format")

    if requirement.get("polarity") == "negative_exception" or requirement_type == "reference_only":
        actions = [item for item in actions if item in {"external_reference_resolve", "manual_confirm"}]
        if not actions:
            actions.append("no_direct_action")
    if refers_only_to_other_lot(raw):
        actions = ["manual_confirm"]
    if not actions:
        actions.append("no_direct_action")

    actions = unique(actions)
    precedence = [
        "external_reference_resolve",
        "manual_confirm",
        "fill_form",
        "provide_evidence",
        "write_response",
        "comply_format",
        "no_direct_action",
    ]
    actions.sort(key=lambda value: precedence.index(value))
    return actions[0], actions[1:]


def new_action_group(
    requirement: dict[str, Any],
    action_kind: str,
    secondary: list[str],
    quantity_rule: dict[str, Any],
) -> dict[str, Any]:
    requirement_id = str(requirement.get("requirement_id") or "")
    semantic = semantic_fingerprint(requirement)
    action_group_id = stable_action_group_id(action_kind, scope_fingerprint(requirement), semantic, requirement_id)
    subjects, expected = evidence_subjects_for(requirement)
    if "provide_evidence" not in [action_kind] + secondary:
        subjects, expected = [], []
    questions = human_questions_for(requirement)
    return {
        "action_group_id": action_group_id,
        "canonical_title": str(requirement.get("title") or canonical_title_for(requirement, action_kind)),
        "canonical_requirement": {
            "text": normalized_requirement_text(requirement),
            "source_type": "system_judgement",
        },
        "member_requirement_ids": [requirement_id],
        "member_source_refs": [deepcopy(requirement.get("source_ref") or {})],
        "requirement_types": [str(requirement.get("requirement_type") or "")],
        "compliance_levels": [str(requirement.get("compliance_level") or "")],
        "risk_types": [str(requirement.get("risk_type") or "")],
        "applicable_scope": deepcopy(requirement.get("applicable_scope") or {}),
        "action_kind": action_kind,
        "primary_action_kind": action_kind,
        "secondary_action_kinds": secondary,
        "action_priority": infer_action_priority(requirement, action_kind),
        "evidence_subject_types": subjects,
        "expected_evidence_types": expected,
        "response_content_needed": response_content_for(requirement, action_kind, secondary),
        "form_or_table_refs": build_form_refs(requirement),
        "related_score_item_ids": unique(list(requirement.get("related_score_item_ids") or [])),
        "related_context_chapter_ids": unique(list(requirement.get("related_context_chapter_ids") or [])),
        "quantity_rule": quantity_rule,
        "quality_rule": extract_quality_rule(requirement),
        "merge_basis": {
            "method": "conservative_semantic_fingerprint",
            "semantic_fingerprint": semantic,
            "scope_fingerprint": scope_fingerprint(requirement),
            "polarity": requirement.get("polarity") or "uncertain",
            "source_type": "system_judgement",
        },
        "conflicts": [],
        "human_review_required": bool(questions) or action_kind in {"manual_confirm", "external_reference_resolve"},
        "human_review_questions": questions,
        "status": "needs_review" if questions or action_kind in {"manual_confirm", "external_reference_resolve"} else "ready_for_dispatch",
        "source_type": "system_judgement",
    }


def merge_requirement_into_group(group: dict[str, Any], requirement: dict[str, Any], secondary: list[str]) -> None:
    group["member_requirement_ids"] = unique(group["member_requirement_ids"] + [requirement.get("requirement_id")])
    group["member_source_refs"].append(deepcopy(requirement.get("source_ref") or {}))
    group["requirement_types"] = unique(group["requirement_types"] + [requirement.get("requirement_type")])
    group["compliance_levels"] = unique(group["compliance_levels"] + [requirement.get("compliance_level")])
    group["risk_types"] = unique(group["risk_types"] + [requirement.get("risk_type")])
    group["secondary_action_kinds"] = unique(group["secondary_action_kinds"] + secondary)
    subjects, expected = evidence_subjects_for(requirement)
    if "provide_evidence" in [group["action_kind"]] + group["secondary_action_kinds"] + secondary:
        group["evidence_subject_types"] = unique(group["evidence_subject_types"] + subjects)
        group["expected_evidence_types"] = unique(group["expected_evidence_types"] + expected)
    group["response_content_needed"] = unique(group["response_content_needed"] + response_content_for(requirement, group["action_kind"], secondary))
    group["form_or_table_refs"] = merge_form_refs(group["form_or_table_refs"], build_form_refs(requirement))
    group["related_score_item_ids"] = unique(group["related_score_item_ids"] + list(requirement.get("related_score_item_ids") or []))
    group["related_context_chapter_ids"] = unique(group["related_context_chapter_ids"] + list(requirement.get("related_context_chapter_ids") or []))
    group["human_review_questions"] = unique(group["human_review_questions"] + human_questions_for(requirement))
    group["human_review_required"] = bool(group["human_review_questions"]) or bool(requirement.get("human_review_required"))
    group["action_priority"] = stricter_priority(group["action_priority"], infer_action_priority(requirement, group["action_kind"]))


def finalize_action_group(group: dict[str, Any]) -> None:
    group["member_source_refs"] = dedupe_source_refs(group["member_source_refs"])
    group["compliance_levels"].sort(key=lambda value: COMPLIANCE_ORDER.get(value, 99))
    group["risk_types"].sort(key=lambda value: RISK_ORDER.get(value, 99))
    group["merge_basis"]["merged_member_count"] = len(group["member_requirement_ids"])
    if group["human_review_required"]:
        group["status"] = "needs_review"


def requirements_can_merge(seed: dict[str, Any], candidate: dict[str, Any], action_kind: str) -> bool:
    if infer_action_kinds(candidate)[0] != action_kind:
        return False
    if scope_fingerprint(seed) != scope_fingerprint(candidate):
        return False
    if str(seed.get("polarity") or "uncertain") != str(candidate.get("polarity") or "uncertain"):
        return False
    if condition_fingerprint(seed) != condition_fingerprint(candidate):
        return False
    if quantity_fingerprint(extract_quantity_rule(seed)) != quantity_fingerprint(extract_quantity_rule(candidate)):
        return False
    left = semantic_fingerprint(seed)
    right = semantic_fingerprint(candidate)
    if left == right:
        return True
    if numeric_fingerprint(requirement_text(seed)) != numeric_fingerprint(requirement_text(candidate)):
        return False
    return semantic_similarity(left, right) >= 0.94


def semantic_fingerprint(requirement: dict[str, Any]) -> str:
    text = normalized_requirement_text(requirement)
    text = re.sub(r"^\s*(?:第?[一二三四五六七八九十\d]+[章节条款、.．:]|[（(][一二三四五六七八九十\d]+[）)])\s*", "", text)
    text = re.sub(r"(?:应当|必须|须|应)", "需", text)
    text = re.sub(r"[\s，。；：、,.!！?？（）()\[\]【】\-—_]+", "", text)
    return text.lower()


def semantic_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    left_grams = {left[index : index + 3] for index in range(max(1, len(left) - 2))}
    right_grams = {right[index : index + 3] for index in range(max(1, len(right) - 2))}
    return len(left_grams & right_grams) / max(1, len(left_grams | right_grams))


def scope_fingerprint(requirement: dict[str, Any]) -> str:
    scope = requirement.get("applicable_scope") if isinstance(requirement.get("applicable_scope"), dict) else {}
    raw = requirement_text(requirement)
    package_markers = unique(re.findall(r"(?:包|标包)\s*0?\d+", raw))
    other_marker = "other_lot" if refers_only_to_other_lot(raw) else ""
    return "|".join(
        [
            str(scope.get("scope_type") or "unknown"),
            str(scope.get("lot_name") or ""),
            str(scope.get("package_number") or ""),
            str(scope.get("applicable")),
            ",".join(package_markers),
            other_marker,
        ]
    )


def condition_fingerprint(requirement: dict[str, Any]) -> str:
    terms = unique(list(requirement.get("detected_condition_terms") or []))
    negations = unique(list(requirement.get("detected_negation_terms") or []))
    risks = unique(list(requirement.get("detected_risk_terms") or []))
    return "|".join([",".join(sorted(terms)), ",".join(sorted(negations)), ",".join(sorted(risks))])


def numeric_fingerprint(text: str) -> str:
    return "|".join(re.findall(r"\d+(?:\.\d+)?%?|[一二三四五六七八九十]+(?:年|月|日|项|名|份)", text))


def extract_quantity_rule(requirement: dict[str, Any]) -> dict[str, Any]:
    text = requirement_text(requirement)
    requirement_id = str(requirement.get("requirement_id") or "")
    base = {
        "rule_type": "not_explicit",
        "required_count": None,
        "max_count_for_score": None,
        "score_per_item": None,
        "raw_rule": "",
        "derived_from_requirement_ids": [requirement_id],
        "source_type": "system_judgement",
    }

    accumulation = re.search(r"每(?:多)?提供\s*([一二三四五六七八九十\d]+)\s*项[^。；]{0,30}?(?:加|得)\s*(\d+(?:\.\d+)?)\s*分", text)
    max_items = re.search(r"(?:上限不超过|最高(?:计|按)?|最多(?:计)?)\s*(\d+)\s*(?:项|个|份)?", text)
    max_points = re.search(r"最高加\s*(\d+(?:\.\d+)?)\s*分", text)
    base_items = re.search(r"具有\s*(\d+)\s*项[^。；]{0,20}?得\s*(\d+(?:\.\d+)?)\s*分", text)
    if accumulation:
        step_count = parse_count(accumulation.group(1))
        score_per_item = float(accumulation.group(2)) / step_count
        max_count = int(max_items.group(1)) if max_items else None
        required_count = max_count
        if max_points:
            base_count = int(base_items.group(1)) if base_items else 0
            required_count = base_count + int(float(max_points.group(1)) / score_per_item)
            max_count = required_count
        return {
            **base,
            "rule_type": "score_accumulation",
            "required_count": required_count,
            "max_count_for_score": max_count,
            "score_per_item": score_per_item,
            "raw_rule": text,
            "source_type": "rule_derived",
        }

    tier_counts = [int(value) for value in re.findall(r"(?:取得|拥有|提供|配备|具有|不少于|至少|≥|不低于)\s*(\d+)\s*(?:项|名|人|个|份|类)", text)]
    if tier_counts:
        count = max(tier_counts) if str(requirement.get("compliance_level")) == "score_related" else min(tier_counts)
        rule_type = "fixed_count" if re.search(r"取得\s*\d+\s*项[^。；]{0,20}得", text) else "explicit_minimum"
        return {
            **base,
            "rule_type": rule_type,
            "required_count": count,
            "max_count_for_score": count if str(requirement.get("compliance_level")) == "score_related" else None,
            "raw_rule": text,
            "source_type": "rule_derived",
        }

    at_most = re.search(r"(?:最多|不超过|上限为)\s*(\d+)\s*(?:项|名|人|个|份)", text)
    if at_most:
        return {
            **base,
            "rule_type": "explicit_maximum",
            "max_count_for_score": int(at_most.group(1)),
            "raw_rule": text,
            "source_type": "rule_derived",
        }
    return base


def quantity_fingerprint(rule: dict[str, Any]) -> str:
    return "|".join(
        str(rule.get(key))
        for key in ["rule_type", "required_count", "max_count_for_score", "score_per_item"]
    )


def extract_quality_rule(requirement: dict[str, Any]) -> dict[str, Any]:
    text = requirement_text(requirement)
    conditions = unique([match.group(0) for pattern in QUALITY_PATTERNS for match in pattern.finditer(text)])
    return {
        "conditions": conditions,
        "raw_rule": text if conditions else "",
        "source_type": "tender_original_fact" if conditions else "system_judgement",
        "status": "explicit" if conditions else "not_explicit",
    }


def evidence_subjects_for(requirement: dict[str, Any]) -> tuple[list[str], list[str]]:
    text = requirement_text(requirement)
    subjects: list[str] = []
    expected: list[str] = []
    for terms, subject, evidence_types in EVIDENCE_TERM_MAP:
        if any(term in text for term in terms):
            subjects.append(subject)
            expected.extend(evidence_types)
    for material in list(requirement.get("required_materials") or []):
        material_type = str(material.get("material_type") or "") if isinstance(material, dict) else str(material)
        mapped = MATERIAL_TYPE_MAP.get(material_type)
        if mapped:
            subjects.append(mapped[0])
            expected.extend(mapped[1])
    return unique(subjects), unique(expected)


def requires_enterprise_evidence(requirement: dict[str, Any]) -> bool:
    raw = requirement_text(requirement)
    subjects, _ = evidence_subjects_for(requirement)
    if any_evidence_material(requirement) or subjects:
        return True
    return any(term in raw for term in ["提供证明", "证明文件", "证书", "业绩", "人员简历", "营业执照"])


def any_evidence_material(requirement: dict[str, Any]) -> bool:
    ignored = {"completed_deviation_table", "completed_special_table", "signed_and_sealed_document", "authorization_document"}
    materials = [
        str(item.get("material_type") or "") if isinstance(item, dict) else str(item)
        for item in list(requirement.get("required_materials") or [])
    ]
    return any(item and item not in ignored for item in materials)


def score_requires_written_response(text: str) -> bool:
    return any(term in text for term in ["技术部分总体评价", "服务方案", "工作进度", "保证措施", "工作基础", "绿色发展规划", "创新激励"])


def bidder_has_direct_action(text: str, requirement: dict[str, Any]) -> bool:
    if any(term in text for term in ["招标人不得", "评标委员会应", "评标委员会完成", "招标代理机构应"]):
        return "投标人" in text and any(term in text for term in ["投标人应", "投标人须", "投标文件应"])
    return bool(requirement.get("related_context_chapter_ids")) or any(
        term in text for term in ["投标人", "投标文件", "本研究", "本项目研究", "技术方案", "服务方案", "应答文件", "我方", "承诺"]
    )


def related_form_ids(requirement: dict[str, Any]) -> list[str]:
    ids = unique(list(requirement.get("related_special_table_ids") or []))
    raw = requirement_text(requirement)
    ids.extend(table_id for term, table_id in FORM_TERM_MAP.items() if term in raw)
    return unique(ids)


def build_form_refs(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    raw = requirement_text(requirement)
    subjects, _ = evidence_subjects_for(requirement)
    result = []
    for table_id in related_form_ids(requirement):
        field_categories = []
        if "personnel" in table_id:
            field_categories.append("人员基础信息、资格证书和项目经历")
        if "project" in table_id:
            field_categories.append("项目名称、合同、时间、金额和验收信息")
        if "deviation" in table_id:
            field_categories.append("偏差条款、偏差说明和响应结论")
        if "price" in table_id or "opening" in table_id:
            field_categories.append("报价和项目基础信息")
        result.append(
            {
                "table_id": table_id,
                "title": next((term for term, value in FORM_TERM_MAP.items() if value == table_id), table_id),
                "required_field_categories": field_categories or ["按原表字段填写"],
                "depends_on_enterprise_evidence": bool(subjects),
                "special_table_recognition_available": table_id != "unresolved_special_table",
                "human_review_required": table_id == "unresolved_special_table" or "如有" in raw,
                "source_type": "system_judgement",
            }
        )
    return result


def merge_form_refs(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = deepcopy(left)
    by_id = {str(item.get("table_id") or ""): item for item in result}
    for item in right:
        table_id = str(item.get("table_id") or "")
        if table_id not in by_id:
            result.append(deepcopy(item))
            by_id[table_id] = result[-1]
            continue
        current = by_id[table_id]
        current["required_field_categories"] = unique(current.get("required_field_categories", []) + item.get("required_field_categories", []))
        current["depends_on_enterprise_evidence"] = bool(current.get("depends_on_enterprise_evidence") or item.get("depends_on_enterprise_evidence"))
        current["human_review_required"] = bool(current.get("human_review_required") or item.get("human_review_required"))
    return result


def response_content_for(requirement: dict[str, Any], primary: str, secondary: list[str]) -> list[str]:
    if "write_response" not in [primary] + secondary:
        return []
    content = requirement.get("required_response_content") if isinstance(requirement.get("required_response_content"), dict) else {}
    return unique(list(content.get("items") or []) or [normalized_requirement_text(requirement)])


def human_questions_for(requirement: dict[str, Any]) -> list[str]:
    result = []
    for item in list(requirement.get("human_review_questions") or []):
        result.append(str(item.get("question") or "") if isinstance(item, dict) else str(item))
    if refers_only_to_other_lot(requirement_text(requirement)):
        result.append("请确认该条款是否仅适用于其他分标，以及是否应从当前标段执行动作中排除。")
    external = requirement.get("external_reference") if isinstance(requirement.get("external_reference"), dict) else None
    if external and not external.get("resolved"):
        result.append(f"请补录或定位外部引用：{external.get('reference_text') or '未命名引用'}。")
    return unique(result)


def infer_action_priority(requirement: dict[str, Any], action_kind: str) -> str:
    level = str(requirement.get("compliance_level") or "")
    risk = str(requirement.get("risk_type") or "")
    requirement_type = str(requirement.get("requirement_type") or "")
    if action_kind == "no_direct_action":
        return "informational"
    if level == "fatal_if_missing" or risk in {"bid_invalidity_risk", "substantial_response_risk"}:
        return "critical"
    if action_kind in {"external_reference_resolve", "manual_confirm"}:
        return "high"
    if level in {"evidence_required", "score_related"} or requirement_type in FORM_TYPES:
        return "high"
    if requirement_type in {"technical_mandatory_requirement", "mandatory_substantive_response"}:
        return "high"
    if requirement_type in {"delivery_acceptance_commitment", "confidentiality_security_requirement"}:
        return "medium"
    if action_kind == "comply_format":
        return "low"
    if action_kind == "no_direct_action":
        return "informational"
    return "medium"


def stricter_priority(left: str, right: str) -> str:
    order = ["critical", "high", "medium", "low", "informational"]
    return min([left, right], key=lambda value: order.index(value))


def build_unresolved_conflicts(matrix: dict[str, Any], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts = deepcopy(list(matrix.get("requirement_conflicts") or []))
    project_conflicts = list((matrix.get("project_ref") or {}).get("project_conflicts") or [])
    for item in project_conflicts:
        if item.get("status") == "needs_human_confirmation":
            conflicts.append(
                {
                    "conflict_id": f"project-{item.get('field') or 'field'}",
                    "conflict_type": "project_field_conflict",
                    "field": item.get("field"),
                    "selected_value": item.get("selected_value"),
                    "candidate_values": item.get("candidate_values") or [],
                    "reason": item.get("reason") or "项目基础字段存在多个候选值。",
                    "human_review_required": True,
                    "source_type": "human_confirmation_required",
                }
            )
    return conflicts


def build_statistics(
    matrix: dict[str, Any],
    groups: list[dict[str, Any]],
    ungrouped: list[dict[str, Any]],
) -> dict[str, Any]:
    requirements = list(matrix.get("requirements") or [])
    action_counts = Counter()
    for group in groups:
        for action in unique([group.get("action_kind")] + list(group.get("secondary_action_kinds") or [])):
            action_counts[action] += 1
    primary_action_counts = Counter(str(item.get("action_kind") or "") for item in groups)
    priority_counts = Counter(str(item.get("action_priority") or "") for item in groups)
    chapter_ids = unique(
        [
            chapter_id
            for item in groups
            for chapter_id in list(item.get("related_context_chapter_ids") or [])
        ]
    )
    chapter_action_counts = Counter(
        chapter_id
        for item in groups
        for chapter_id in list(item.get("related_context_chapter_ids") or [])
    )
    merged_count = sum(max(0, len(item.get("member_requirement_ids") or []) - 1) for item in groups)
    explicit_quantity_count = len([item for item in groups if (item.get("quantity_rule") or {}).get("rule_type") not in {"not_explicit", "needs_review"}])
    not_explicit_quantity_count = len([item for item in groups if (item.get("quantity_rule") or {}).get("rule_type") == "not_explicit"])
    result = {
        "raw_requirement_count": len(requirements),
        "action_group_count": len(groups),
        "merged_requirement_count": merged_count,
        "ungrouped_requirement_count": len(ungrouped),
        **{f"{kind}_count": action_counts.get(kind, 0) for kind in sorted(ACTION_KINDS)},
        "action_kind_counts": dict(sorted(primary_action_counts.items())),
        "action_dispatch_counts": dict(sorted(action_counts.items())),
        "action_priority_counts": dict(sorted(priority_counts.items())),
        "critical_count": priority_counts.get("critical", 0),
        "high_count": priority_counts.get("high", 0),
        "human_review_count": len([item for item in groups if item.get("human_review_required")]),
        "requirements_with_score_links": len([item for item in requirements if item.get("related_score_item_ids")]),
        "requirements_with_context_chapter_links": len([item for item in requirements if item.get("related_context_chapter_ids")]),
        "explicit_quantity_rule_count": explicit_quantity_count,
        "not_explicit_quantity_rule_count": not_explicit_quantity_count,
        "context_chapter_coverage": {
            "linked_chapter_count": len(chapter_ids),
            "chapter_action_group_counts": dict(sorted(chapter_action_counts.items())),
        },
        "unresolved_conflict_count": len(list(matrix.get("requirement_conflicts") or []))
        + len(
            [
                item
                for item in list((matrix.get("project_ref") or {}).get("project_conflicts") or [])
                if item.get("status") == "needs_human_confirmation"
            ]
        ),
        "evidence_subject_type_counts": dict(
            sorted(
                Counter(
                    subject
                    for item in groups
                    if group_has_action(item, "provide_evidence")
                    for subject in list(item.get("evidence_subject_types") or [])
                ).items()
            )
        ),
    }
    return result


def update_technical_context_action_index(
    context: dict[str, Any],
    groups: list[dict[str, Any]],
    index_path: Path,
    generated_at: str,
) -> None:
    by_chapter: dict[str, list[str]] = {}
    for group in groups:
        for chapter_id in list(group.get("related_context_chapter_ids") or []):
            by_chapter.setdefault(str(chapter_id), []).append(str(group.get("action_group_id") or ""))
    for chapter in list(context.get("chapters") or []):
        chapter_id = str(chapter.get("chapter_id") or "")
        chapter["chapter_action_group_ids"] = unique(by_chapter.get(chapter_id, []))
    context["actionable_requirement_index_path"] = str(index_path)
    context["actionable_requirement_index"] = {
        "path": str(index_path),
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "action_group_count": len(groups),
        "source_type": "system_judgement",
    }


def render_actionable_requirement_index_markdown(payload: dict[str, Any]) -> str:
    groups = list(payload.get("action_groups") or [])
    sections = [
        ("关键无效/否决风险动作", lambda item: item.get("action_priority") == "critical"),
        ("需要企业证明材料", lambda item: group_has_action(item, "provide_evidence")),
        ("需要撰写响应", lambda item: group_has_action(item, "write_response")),
        ("需要填写表格", lambda item: group_has_action(item, "fill_form")),
        ("格式、签章和递交检查", lambda item: group_has_action(item, "comply_format")),
        ("需要人工确认", lambda item: group_has_action(item, "manual_confirm")),
        ("待解析外部引用", lambda item: group_has_action(item, "external_reference_resolve")),
        ("无直接动作的参考要求", lambda item: group_has_action(item, "no_direct_action")),
    ]
    statistics = payload.get("statistics") or {}
    lines = [
        f"# 要求行动索引：{payload.get('lot_name') or '-'}",
        "",
        f"- 正式要求事实源：`{payload.get('generated_from') or '-'}`",
        f"- 原始要求：{statistics.get('raw_requirement_count', 0)}",
        f"- 行动组：{statistics.get('action_group_count', 0)}",
        f"- 自动归并的重复要求：{statistics.get('merged_requirement_count', 0)}",
        "",
    ]
    for title, predicate in sections:
        lines.extend([f"## {title}", ""])
        selected = [item for item in groups if predicate(item)]
        if not selected:
            lines.extend(["- 无", ""])
            continue
        for item in selected:
            refs = list(item.get("member_source_refs") or [])
            sources = unique([str(ref.get("source_file_name") or "-") for ref in refs])
            scope = item.get("applicable_scope") or {}
            quantity = item.get("quantity_rule") or {}
            quality = item.get("quality_rule") or {}
            response = unique(
                list(item.get("expected_evidence_types") or [])
                + list(item.get("response_content_needed") or [])
                + [str(ref.get("title") or ref.get("table_id") or "") for ref in list(item.get("form_or_table_refs") or [])]
            )
            questions = "；".join(item.get("human_review_questions") or []) or "-"
            lines.extend(
                [
                    f"### {item.get('canonical_title') or '-'}",
                    "",
                    f"- 原始 requirement 数量：{len(item.get('member_requirement_ids') or [])}",
                    f"- 原始 requirement_id：{'、'.join(item.get('member_requirement_ids') or [])}",
                    f"- 来源文件：{'、'.join(sources) or '-'}",
                    f"- 适用范围：{scope.get('scope_type') or 'unknown'} / {scope.get('package_number') or '-'}",
                    f"- 要采取的动作：{'、'.join(unique([item.get('action_kind')] + list(item.get('secondary_action_kinds') or [])))}",
                    f"- 优先级：{item.get('action_priority') or '-'}",
                    f"- 需要的材料或响应：{'；'.join(response) or '-'}",
                    f"- 关联评分项：{'、'.join(item.get('related_score_item_ids') or []) or '-'}",
                    f"- 关联技术响应章节：{'、'.join(item.get('related_context_chapter_ids') or []) or '-'}",
                    f"- 数量规则：{quantity.get('rule_type') or '-'}；{quantity.get('raw_rule') or '原文未明确数量'}",
                    f"- 质量规则：{'；'.join(quality.get('conditions') or []) or '未识别到额外质量条件'}",
                    f"- 人工复核问题：{questions}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def validate_matrix_source(matrix: dict[str, Any], lot_name: str) -> None:
    if str(matrix.get("lot_name") or "") != lot_name:
        raise ValueError(f"tender_requirement_matrix lot_name mismatch: expected {lot_name}")
    if not isinstance(matrix.get("requirements"), list):
        raise ValueError("tender_requirement_matrix.requirements not found")


def validate_index(payload: dict[str, Any]) -> None:
    requirement_ids = {
        str(item.get("requirement_id") or "")
        for item in list(load_json(payload["generated_from"]).get("requirements") or [])
    } if Path(payload["generated_from"]).exists() else {
        str(requirement_id)
        for group in list(payload.get("action_groups") or [])
        for requirement_id in list(group.get("member_requirement_ids") or [])
    }
    for group in list(payload.get("action_groups") or []):
        if group.get("action_kind") not in ACTION_KINDS:
            raise ValueError(f"invalid action_kind: {group.get('action_kind')}")
        if group.get("action_priority") not in ACTION_PRIORITIES:
            raise ValueError(f"invalid action_priority: {group.get('action_priority')}")
        if not set(group.get("secondary_action_kinds") or []).issubset(ACTION_KINDS):
            raise ValueError(f"invalid secondary_action_kinds: {group.get('action_group_id')}")
        if not set(group.get("evidence_subject_types") or []).issubset(EVIDENCE_SUBJECT_TYPES):
            raise ValueError(f"invalid evidence_subject_types: {group.get('action_group_id')}")
        quantity = group.get("quantity_rule") or {}
        if quantity.get("rule_type") not in QUANTITY_RULE_TYPES:
            raise ValueError(f"invalid quantity rule: {group.get('action_group_id')}")
        if quantity.get("rule_type") == "not_explicit" and quantity.get("required_count") is not None:
            raise ValueError(f"invented quantity count: {group.get('action_group_id')}")
        if not set(group.get("member_requirement_ids") or []).issubset(requirement_ids):
            raise ValueError(f"unknown member requirement ID: {group.get('action_group_id')}")
        if len(group.get("member_source_refs") or []) == 0:
            raise ValueError(f"missing member source refs: {group.get('action_group_id')}")


def ensure_no_decision_terms(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False)
    for term in FORBIDDEN_DECISION_TERMS:
        if term.lower() in text.lower():
            raise ValueError(f"forbidden bid decision term found: {term}")


def summarize_groups(groups: list[dict[str, Any]], action_kind: str) -> list[dict[str, Any]]:
    return [summarize_group(item) for item in groups if group_has_action(item, action_kind)]


def summarize_group(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_group_id": item.get("action_group_id"),
        "canonical_title": item.get("canonical_title"),
        "member_requirement_ids": item.get("member_requirement_ids") or [],
        "action_kind": item.get("action_kind"),
        "secondary_action_kinds": item.get("secondary_action_kinds") or [],
        "action_priority": item.get("action_priority"),
        "evidence_subject_types": item.get("evidence_subject_types") or [],
        "related_context_chapter_ids": item.get("related_context_chapter_ids") or [],
    }


def group_has_action(group: dict[str, Any], action_kind: str) -> bool:
    return action_kind in unique([group.get("action_kind")] + list(group.get("secondary_action_kinds") or []))


def normalized_requirement_text(requirement: dict[str, Any]) -> str:
    normalized = requirement.get("normalized_requirement") if isinstance(requirement.get("normalized_requirement"), dict) else {}
    return normalize_text(normalized.get("text") or requirement.get("raw_text") or requirement.get("title") or "")


def requirement_text(requirement: dict[str, Any]) -> str:
    return normalize_text(requirement.get("raw_text") or normalized_requirement_text(requirement))


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_title_for(requirement: dict[str, Any], action_kind: str) -> str:
    labels = {
        "provide_evidence": "准备企业证明材料",
        "write_response": "编写要求响应",
        "fill_form": "填写规定表格",
        "comply_format": "执行格式合规检查",
        "manual_confirm": "人工确认要求",
        "external_reference_resolve": "解析外部引用",
        "no_direct_action": "参考要求",
    }
    return labels[action_kind]


def stable_action_group_id(action_kind: str, scope: str, semantic: str, fallback: str) -> str:
    digest = hashlib.sha1(f"{action_kind}|{scope}|{semantic or fallback}".encode("utf-8")).hexdigest()[:14]
    return f"action-{digest}"


def dedupe_source_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for ref in refs:
        key = (
            str(ref.get("source_file_id") or ""),
            str(ref.get("clause_id") or ""),
            str(ref.get("excerpt") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def refers_only_to_other_lot(text: str) -> bool:
    return any(
        term in text
        for term in [
            "其他标段适用",
            "其它标段适用",
            "其他分标适用",
            "其它分标适用",
            "仅适用于其他标段",
            "仅适用于其它标段",
            "仅适用于其他分标",
            "仅适用于其它分标",
            "其它标段：",
            "其他标段：",
        ]
    )


def parse_count(value: str) -> int:
    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(value, 0)


def action_group_sort_key(group: dict[str, Any]) -> tuple[int, str, str]:
    priorities = ["critical", "high", "medium", "low", "informational"]
    actions = ["external_reference_resolve", "manual_confirm", "provide_evidence", "write_response", "fill_form", "comply_format", "no_direct_action"]
    return (
        priorities.index(str(group.get("action_priority") or "informational")),
        str(actions.index(str(group.get("action_kind") or "no_direct_action"))),
        str(group.get("action_group_id") or ""),
    )

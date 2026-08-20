from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.actionable_requirement_index import group_has_action
from app.enterprise_knowledge_query import query_enterprise_evidence_candidates
from app.evidence_resolution import (
    apply_resolution_decisions,
    build_material_intake_plan,
    build_resolution_queue,
    ensure_decision_store,
    initialize_verification_fields,
    update_context_resolution_index,
    write_resolution_artifacts,
)
from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.material_matcher import find_project_by_lot_name, load_tender_projects
from app.tender_requirement_matrix import load_json, safe_path_name, unique
from config import METADATA_PATH, STORAGE_DIR


SCHEMA_VERSION = "1.0"

SUPPORT_STRENGTHS = {"strong", "medium", "weak", "none"}
USABILITY_STATUSES = {"confirmed", "candidate", "needs_review", "unusable"}
FULL_SCORE_STATUSES = {"yes", "mostly", "no", "needs_review", "not_applicable", "unknown"}
PACKAGE_COMPLETENESS = {"complete", "mostly_complete", "incomplete", "needs_review"}

SCORE_EVIDENCE_MAP: list[tuple[list[str], list[str], list[str]]] = [
    (["不良行为", "信用信息", "失信", "公正性行为"], ["credit_evidence"], ["公共信用信息报告", "企业信用信息公示报告"]),
    (["绿色管理体系"], ["management_system_certificate"], ["能源、质量、职业健康安全和环境管理体系证书", "证书有效期页"]),
    (["环境影响评价"], ["other_enterprise_evidence"], ["ESG报告", "环评或能评报告", "废水废气废固报告"]),
    (["绿电绿证"], ["other_enterprise_evidence"], ["绿色电力证书", "绿电使用证明"]),
    (["绿色发展规划"], ["other_enterprise_evidence"], ["绿色发展规划及执行证明"]),
    (["企业创新成果"], ["technical_achievement"], ["科技创新成果证明"]),
    (["创新激励"], ["other_enterprise_evidence"], ["创新激励制度及执行记录"]),
    (["研发团队规模"], ["organization_structure", "personnel_qualification", "personnel_certificate"], ["研发团队名单", "高级职称或高级技师证书", "人员关系证明"]),
    (["科研经费"], ["financial_evidence"], ["审计报告或研发投入专项证明"]),
    (["综合实力"], ["enterprise_qualification", "personnel_qualification", "service_capability"], ["企业资质证明", "质量奖证明", "高新技术企业证书", "人员与服务能力证明"]),
    (["项目负责人"], ["personnel_resume", "personnel_qualification", "personnel_project_experience"], ["项目负责人简历", "人员证书", "项目任命或投入证明", "个人项目经历证明"]),
    (["项目团队", "人员结构"], ["personnel_resume", "personnel_qualification", "personnel_certificate"], ["项目团队人员简历", "人员证书", "人员投入证明"]),
    (["业绩情况"], ["project_performance", "contract", "award_notice", "acceptance_certificate", "customer_certificate"], ["合同关键页", "中标通知书", "验收证明或用户证明"]),
    (["工作基础"], ["technical_achievement", "project_performance"], ["产品化证明", "科技成果证明", "专题研究证明"]),
    (["专利数量"], ["patent"], ["发明专利或实用新型专利证书"]),
    (["绩效评价"], ["service_capability", "customer_certificate"], ["最近一年度供应商绩效评价结果"]),
]

EVIDENCE_TYPE_SUBJECTS = {
    "management_system_certificate": {"enterprise_qualification", "management_system_certificate"},
    "enterprise_qualification_certificate": {"enterprise_qualification"},
    "personnel_resume": {"personnel_resume", "personnel_qualification", "personnel_project_experience", "organization_structure"},
    "personnel_certificate": {"personnel_certificate", "personnel_qualification"},
    "company_performance_summary": {"project_performance", "service_capability", "technical_achievement"},
    "contract": {"project_performance", "contract"},
    "award_notice": {"project_performance", "award_notice"},
    "acceptance_certificate": {"project_performance", "acceptance_certificate"},
    "customer_certificate": {"project_performance", "customer_certificate", "service_capability"},
    "patent": {"patent"},
    "software_copyright": {"software_copyright", "technical_achievement"},
    "technical_achievement": {"technical_achievement"},
    "credit_evidence": {"credit_evidence"},
    "financial_evidence": {"financial_evidence"},
    "other_enterprise_evidence": {"other_enterprise_evidence"},
}

MANAGEMENT_CERT_NAMES = {
    "质量管理体系": ["ISO 9001", "ISO9001", "质量管理体系"],
    "环境管理体系": ["ISO 14001", "ISO14001", "环境管理体系"],
    "职业健康安全管理体系": ["ISO 45001", "ISO45001", "职业健康安全管理体系"],
    "能源管理体系": ["ISO 50001", "ISO50001", "能源管理体系"],
}


def build_evidence_coverage(
    lot_name: str,
    output_dir: str | Path = "outputs",
    *,
    metadata: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    requirement_matrix: dict[str, Any] | None = None,
    action_index: dict[str, Any] | None = None,
    update_context_index: bool = True,
    decision_store_path: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(output_dir)
    lot_dir = base / safe_path_name(lot_name)
    evidence_dir = lot_dir / "evidence"
    context_path = lot_dir / "outline" / "technical_response_context.json"
    requirement_matrix_path = lot_dir / "requirements" / "tender_requirement_matrix.json"
    action_index_path = lot_dir / "requirements" / "actionable_requirement_index.json"
    catalog_path = evidence_dir / "enterprise_evidence_catalog.json"
    matrix_path = evidence_dir / "evidence_coverage_matrix.json"
    previous_matrix = load_json(matrix_path) if matrix_path.exists() else None

    metadata = deepcopy(metadata) if metadata is not None else load_json(METADATA_PATH)
    project = deepcopy(project) if project is not None else find_project_by_lot_name(load_tender_projects(), lot_name)
    context = deepcopy(context) if context is not None else load_json(context_path)
    requirement_matrix = deepcopy(requirement_matrix) if requirement_matrix is not None else load_json(requirement_matrix_path)
    action_index = deepcopy(action_index) if action_index is not None else load_json(action_index_path)
    validate_inputs(lot_name, context, requirement_matrix, action_index)

    score_items = list(project.get("score_items") or [])
    evidence_requirements = build_evidence_requirements(score_items, action_index, requirement_matrix)
    query_results = query_candidates_for_requirements(evidence_requirements, metadata)
    catalog = build_enterprise_evidence_catalog(lot_name, metadata, query_results)
    support_relations = build_support_relations(evidence_requirements, catalog)
    evidence_packages = build_evidence_packages(catalog, support_relations)
    apply_complete_package_support(support_relations, evidence_packages, catalog)
    initialize_verification_fields(catalog, support_relations)
    preliminary_manual_review = build_evidence_manual_review(catalog, evidence_requirements, support_relations, evidence_packages)
    decision_store, persistent_decision_path = ensure_decision_store(lot_name, decision_store_path)
    preliminary_matrix = {
        "lot_name": lot_name,
        "evidence_requirements": evidence_requirements,
        "support_relations": support_relations,
        "evidence_packages": evidence_packages,
        "global_manual_review_items": preliminary_manual_review,
    }
    preliminary_queue = build_resolution_queue(
        lot_name,
        preliminary_matrix,
        catalog,
        decision_store,
        persistent_decision_path,
    )
    decision_warnings = apply_resolution_decisions(
        catalog,
        evidence_requirements,
        support_relations,
        evidence_packages,
        preliminary_queue,
        decision_store,
    )
    score_coverage = build_score_item_coverage(score_items, evidence_requirements, support_relations, catalog, evidence_packages)
    requirement_coverage = build_requirement_coverage(evidence_requirements, support_relations, catalog, evidence_packages)
    missing_requests = build_missing_evidence_requests(evidence_requirements, score_coverage, requirement_coverage)
    manual_review = build_evidence_manual_review(catalog, evidence_requirements, support_relations, evidence_packages)
    generated_at = datetime.now(timezone.utc).isoformat()

    matrix = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": {
            "technical_response_context": str(context_path),
            "tender_requirement_matrix": str(requirement_matrix_path),
            "actionable_requirement_index": str(action_index_path),
            "tender_projects": "data/tender_projects.json",
            "metadata": str(METADATA_PATH),
        },
        "evidence_catalog_path": str(catalog_path),
        "evidence_requirements": evidence_requirements,
        "support_relations": support_relations,
        "evidence_packages": evidence_packages,
        "score_item_coverage": score_coverage,
        "requirement_coverage": requirement_coverage,
        "global_missing_evidence": missing_requests,
        "global_manual_review_items": manual_review,
        "statistics": build_coverage_statistics(catalog, evidence_requirements, support_relations, evidence_packages, score_coverage, missing_requests, manual_review),
        "build_warnings": unique(build_warnings(project, catalog, support_relations) + decision_warnings),
    }
    validate_catalog(catalog)
    validate_coverage_matrix(matrix, catalog)
    ensure_no_decision_terms(matrix)

    catalog["generated_at"] = generated_at
    catalog["statistics"] = build_catalog_statistics(catalog)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    write_derived_outputs(evidence_dir, matrix)
    resolution_queue = build_resolution_queue(
        lot_name,
        matrix,
        catalog,
        decision_store,
        persistent_decision_path,
    )
    resolution_queue["build_warnings"] = decision_warnings
    intake_plan = build_material_intake_plan(lot_name, matrix)
    resolution_paths = write_resolution_artifacts(
        evidence_dir,
        resolution_queue,
        intake_plan,
        matrix,
        previous_matrix,
    )

    if update_context_index:
        update_technical_context_evidence_index(context, evidence_requirements, support_relations, catalog_path, matrix_path, generated_at)
        update_context_resolution_index(
            context,
            resolution_queue,
            evidence_dir / "evidence_resolution_queue.json",
            evidence_dir / "material_intake_plan.json",
            persistent_decision_path,
        )
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "lot_name": lot_name,
        "schema_version": SCHEMA_VERSION,
        "enterprise_evidence_catalog_path": str(catalog_path),
        "evidence_coverage_matrix_path": str(matrix_path),
        "evidence_resolution_queue_path": resolution_paths["evidence_resolution_queue"],
        "material_intake_plan_path": resolution_paths["material_intake_plan"],
        "evidence_resolution_decision_store_path": str(persistent_decision_path),
        "files": evidence_output_paths(evidence_dir),
        "statistics": matrix["statistics"],
        "build_warnings": matrix["build_warnings"],
    }


def inspect_evidence_coverage(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    evidence_dir = Path(output_dir) / safe_path_name(lot_name) / "evidence"
    matrix_path = evidence_dir / "evidence_coverage_matrix.json"
    catalog_path = evidence_dir / "enterprise_evidence_catalog.json"
    matrix = load_json(matrix_path)
    catalog = load_json(catalog_path)
    explicit = []
    for item in list(matrix.get("score_item_coverage") or []):
        if item.get("required_count") is not None:
            explicit.append(
                {
                    "score_item_id": item.get("score_item_id"),
                    "score_item_name": item.get("score_item_name"),
                    "required_count": item.get("required_count"),
                    "confirmed_count": item.get("confirmed_count"),
                    "candidate_count": item.get("candidate_count"),
                    "remaining_confirmed_gap": item.get("remaining_confirmed_gap"),
                }
            )
    no_location = [item for item in list(catalog.get("evidence_items") or []) if not item.get("source_location") or not item.get("excerpt")]
    return {
        "lot_name": lot_name,
        "matrix_path": str(matrix_path),
        "catalog_path": str(catalog_path),
        "statistics": matrix.get("statistics") or {},
        "evidence_type_counts": (catalog.get("statistics") or {}).get("evidence_type_counts") or {},
        "score_item_coverage": list(matrix.get("score_item_coverage") or []),
        "explicit_quantity_coverage": explicit,
        "evidence_package_completeness": (matrix.get("statistics") or {}).get("evidence_package_completeness_counts") or {},
        "missing_evidence_request_count": len(list(matrix.get("global_missing_evidence") or [])),
        "manual_review_count": len(list(matrix.get("global_manual_review_items") or [])),
        "evidence_without_location_or_excerpt_count": len(no_location),
        "files": {name: {"path": path, "exists": Path(path).exists()} for name, path in evidence_output_paths(evidence_dir).items()},
    }


def build_evidence_requirements(
    score_items: list[dict[str, Any]],
    action_index: dict[str, Any],
    requirement_matrix: dict[str, Any],
) -> list[dict[str, Any]]:
    all_groups = list(action_index.get("action_groups") or [])
    evidence_groups = [
        group
        for group in all_groups
        if group_has_action(group, "provide_evidence")
    ]
    requirements_by_id = {
        str(item.get("requirement_id") or ""): item
        for item in list(requirement_matrix.get("requirements") or [])
    }
    result: list[dict[str, Any]] = []
    groups_by_score: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in evidence_groups:
        for score_id in list(group.get("related_score_item_ids") or []):
            groups_by_score[str(score_id)].append(group)

    score_ids = {str(item.get("id") or "") for item in score_items}
    consumed_action_ids: set[str] = set()
    for score_item in score_items:
        score_id = str(score_item.get("id") or "")
        score_title_key = normalize_identity(score_item.get("item_name"))
        exact_title_groups = [
            group
            for group in all_groups
            if score_title_key and normalize_identity(group.get("canonical_title")) == score_title_key
        ]
        directly_linked_groups = groups_by_score.get(score_id, [])
        groups = exact_title_groups or [
            group
            for group in directly_linked_groups
            if score_title_key
            and (
                score_title_key in normalize_identity(group.get("canonical_title"))
                or normalize_identity(group.get("canonical_title")) in score_title_key
            )
        ]
        subjects, expected = score_evidence_profile(score_item)
        if not subjects and not expected:
            for group in groups:
                subjects.extend(list(group.get("evidence_subject_types") or []))
                expected.extend(list(group.get("expected_evidence_types") or []))
        subjects, expected = unique(subjects), unique(expected)
        if not subjects and not expected:
            continue
        consumed_action_ids.update(
            str(group.get("action_group_id") or "")
            for group in groups
            if group_has_action(group, "provide_evidence")
        )
        quantity = select_quantity_rule(groups)
        source_requirement_ids = unique(
            [requirement_id for group in groups for requirement_id in list(group.get("member_requirement_ids") or [])]
        )
        source_action_ids = unique([group.get("action_group_id") for group in groups])
        related_chapters = unique(
            [chapter_id for group in groups for chapter_id in list(group.get("related_context_chapter_ids") or [])]
        )
        conditions = evidence_conditions_for_score(score_item)
        result.append(
            evidence_requirement_record(
                canonical_title=str(score_item.get("item_name") or score_id),
                origin="score_and_requirement" if groups else "score_item",
                score_ids=[score_id],
                action_ids=source_action_ids,
                requirement_ids=source_requirement_ids,
                subjects=subjects,
                expected=expected,
                quantity=quantity,
                quality=merge_quality_rules(groups, conditions),
                chapters=related_chapters,
                priority="high",
                conditions=conditions,
            )
        )

    for group in evidence_groups:
        if str(group.get("action_group_id") or "") in consumed_action_ids:
            continue
        linked_current_scores = [score_id for score_id in list(group.get("related_score_item_ids") or []) if score_id in score_ids]
        if linked_current_scores:
            continue
        member_ids = unique(list(group.get("member_requirement_ids") or []))
        member_requirements = [requirements_by_id[item_id] for item_id in member_ids if item_id in requirements_by_id]
        requirement_types = {str(item.get("requirement_type") or "") for item in member_requirements}
        origin = "qualification_requirement" if "qualification_requirement" in requirement_types else "mandatory_requirement"
        result.append(
            evidence_requirement_record(
                canonical_title=str(group.get("canonical_title") or "企业证明材料要求"),
                origin=origin,
                score_ids=[],
                action_ids=[str(group.get("action_group_id") or "")],
                requirement_ids=member_ids,
                subjects=list(group.get("evidence_subject_types") or []),
                expected=list(group.get("expected_evidence_types") or []),
                quantity=deepcopy(group.get("quantity_rule") or not_explicit_quantity_rule()),
                quality=deepcopy(group.get("quality_rule") or {}),
                chapters=list(group.get("related_context_chapter_ids") or []),
                priority=str(group.get("action_priority") or "high"),
                conditions=[],
            )
        )
    return dedupe_evidence_requirements(result)


def evidence_requirement_record(
    *,
    canonical_title: str,
    origin: str,
    score_ids: list[str],
    action_ids: list[str],
    requirement_ids: list[str],
    subjects: list[str],
    expected: list[str],
    quantity: dict[str, Any],
    quality: dict[str, Any],
    chapters: list[str],
    priority: str,
    conditions: list[str],
) -> dict[str, Any]:
    digest = stable_id("evidence-requirement", "|".join(sorted(score_ids + action_ids + requirement_ids)) or canonical_title)
    time_conditions = [item for item in conditions if any(term in item for term in ["年", "月", "日前", "截止日"])]
    validity_conditions = [item for item in conditions if "有效" in item or "授权状态" in item]
    subject_conditions = [item for item in conditions if any(term in item for term in ["投标人", "供应商", "人员", "负责人", "权利人", "持证"])]
    relevance_conditions = [item for item in conditions if any(term in item for term in ["相关", "类似", "采购项目", "项目内容"])]
    return {
        "evidence_requirement_id": digest,
        "canonical_title": canonical_title,
        "requirement_origin": origin,
        "source_score_item_ids": unique(score_ids),
        "source_action_group_ids": unique(action_ids),
        "source_requirement_ids": unique(requirement_ids),
        "expected_evidence_subject_types": unique(subjects),
        "expected_evidence_types": unique(expected),
        "required_conditions": unique(conditions),
        "quantity_rule": quantity,
        "quality_rule": quality,
        "time_conditions": unique(time_conditions),
        "validity_conditions": unique(validity_conditions),
        "subject_conditions": unique(subject_conditions),
        "relevance_conditions": unique(relevance_conditions),
        "related_context_chapter_ids": unique(chapters),
        "priority": priority if priority in {"critical", "high", "medium", "low", "informational"} else "high",
        "source_type": "rule_derived" if quantity.get("source_type") == "rule_derived" else "system_judgement",
        "human_review_required": False,
        "human_review_questions": [],
    }


def score_evidence_profile(score_item: dict[str, Any]) -> tuple[list[str], list[str]]:
    text = " ".join(
        [
            str(score_item.get("item_name") or ""),
            str(score_item.get("score_rule") or ""),
        ]
    )
    subjects: list[str] = []
    expected: list[str] = []
    for terms, mapped_subjects, mapped_expected in SCORE_EVIDENCE_MAP:
        if any(term in text for term in terms):
            subjects.extend(mapped_subjects)
            expected.extend(mapped_expected)
    return unique(subjects), unique(expected)


def evidence_conditions_for_score(score_item: dict[str, Any]) -> list[str]:
    text = str(score_item.get("score_rule") or score_item.get("raw_text") or "")
    patterns = [
        r"(?:近|最近)\s*\d+\s*年(?:内)?",
        r"有效期内",
        r"与采购项目有关",
        r"采购相关",
        r"类似项目",
        r"高级及以上职称人员和高级技师人员",
        r"发明专利或实用新型专利",
        r"最近一年度",
    ]
    return unique([match.group(0) for pattern in patterns for match in re.finditer(pattern, text)])


def select_quantity_rule(groups: list[dict[str, Any]]) -> dict[str, Any]:
    explicit = [
        deepcopy(group.get("quantity_rule") or {})
        for group in groups
        if (group.get("quantity_rule") or {}).get("rule_type") not in {None, "not_explicit", "needs_review"}
    ]
    if not explicit:
        return not_explicit_quantity_rule()
    explicit.sort(key=lambda item: (item.get("required_count") is None, -(int(item.get("required_count") or 0))))
    return explicit[0]


def not_explicit_quantity_rule() -> dict[str, Any]:
    return {
        "rule_type": "not_explicit",
        "required_count": None,
        "max_count_for_score": None,
        "score_per_item": None,
        "raw_rule": "",
        "derived_from_requirement_ids": [],
        "source_type": "system_judgement",
    }


def merge_quality_rules(groups: list[dict[str, Any]], score_conditions: list[str]) -> dict[str, Any]:
    conditions = unique(
        score_conditions
        + [condition for group in groups for condition in list((group.get("quality_rule") or {}).get("conditions") or [])]
    )
    return {
        "conditions": conditions,
        "source_type": "tender_original_fact" if conditions else "system_judgement",
        "status": "explicit" if conditions else "not_explicit",
    }


def dedupe_evidence_requirements(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("evidence_requirement_id") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def query_candidates_for_requirements(
    requirements: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []
    for requirement in requirements:
        query = " ".join(
            [
                str(requirement.get("canonical_title") or ""),
                " ".join(requirement.get("expected_evidence_types") or []),
                " ".join(requirement.get("required_conditions") or []),
            ]
        )
        result = query_enterprise_evidence_candidates(
            query,
            metadata,
            evidence_subject_types=list(requirement.get("expected_evidence_subject_types") or []),
            expected_evidence_types=list(requirement.get("expected_evidence_types") or []),
        )
        result["evidence_requirement_id"] = requirement.get("evidence_requirement_id")
        results.append(result)
    return results


def build_enterprise_evidence_catalog(
    lot_name: str,
    metadata: dict[str, Any],
    query_results: list[dict[str, Any]],
) -> dict[str, Any]:
    by_file: dict[str, dict[str, Any]] = {}
    for query in query_results:
        for candidate in list(query.get("candidates") or []):
            file_id = str(candidate.get("file_id") or "")
            if file_id:
                by_file[file_id] = candidate
    items: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    for candidate in by_file.values():
        record = candidate.get("record") if isinstance(candidate.get("record"), dict) else {}
        source_files.append(
            {
                "source_file_id": record.get("id"),
                "source_file_name": record.get("original_name"),
                "storage_key": record.get("storage_key"),
                "physical_file_exists": bool(candidate.get("physical_file_exists")),
                "material_group": candidate.get("material_group"),
                "source_type": "enterprise_existing_fact",
            }
        )
        if not candidate.get("physical_file_exists"):
            warnings.append(f"企业材料物理文件不存在：{record.get('id')} / {record.get('original_name')}")
        items.extend(extract_evidence_items_from_record(record, bool(candidate.get("physical_file_exists"))))
    deduped, duplicate_groups, identity_conflicts = dedupe_evidence_items(items)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "",
        "lot_name": lot_name,
        "source_files": source_files,
        "evidence_items": deduped,
        "duplicate_groups": duplicate_groups,
        "unresolved_identity_conflicts": identity_conflicts,
        "statistics": {},
        "build_warnings": unique(warnings),
    }


def extract_evidence_items_from_record(record: dict[str, Any], physical_exists: bool) -> list[dict[str, Any]]:
    profile = record.get("material_profile") if isinstance(record.get("material_profile"), dict) else {}
    group = str(profile.get("material_group") or "")
    if record.get("resume_parse") or group == "personnel":
        return extract_personnel_evidence(record, physical_exists)
    if record.get("company_performance") or group == "company_performance" or "业绩" in str(record.get("category") or ""):
        return extract_performance_evidence(record, physical_exists)
    if group == "company_qualification" or any(term in str(record.get("parsed_text") or "") for term in ["ISO 9001", "管理体系认证证书"]):
        return extract_qualification_evidence(record, physical_exists)
    return [generic_evidence_item(record, physical_exists)]


def extract_qualification_evidence(record: dict[str, Any], physical_exists: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for table in list(record.get("tables") or []):
        rows = list(table.get("rows") or []) if isinstance(table, dict) else []
        if not rows:
            continue
        headers = [str(item or "") for item in rows[0]]
        if "证书编号" not in headers:
            continue
        for row_index, row in enumerate(rows[1:], start=2):
            values = [str(item or "").strip() for item in row]
            mapping = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
            title = mapping.get("信息事项") or "企业资质证书"
            certificate_no = mapping.get("证书编号") or ""
            issuer = mapping.get("认证机构") or ""
            scope = mapping.get("认证事项范围") or ""
            validity = mapping.get("有效期限") or ""
            valid_until = parse_date_value(validity)
            evidence_type = "management_system_certificate" if any(term in title for terms in MANAGEMENT_CERT_NAMES.values() for term in terms) else "enterprise_qualification_certificate"
            excerpt = "；".join(f"{key}：{value}" for key, value in mapping.items() if value)
            questions = ["请核对证书扫描件中的持证主体是否与投标主体完全一致。"]
            if not physical_exists:
                questions.append("请恢复或重新入库该证书的物理源文件。")
            items.append(
                catalog_item(
                    record,
                    evidence_type=evidence_type,
                    title=title,
                    source_location={"table_index": table.get("table_index"), "row_index": row_index},
                    location_precision="clause_approximate",
                    excerpt=excerpt,
                    structured_facts={
                        "certificate_name": title,
                        "certificate_number": certificate_no,
                        "issuer": issuer,
                        "scope": scope,
                        "validity_text": validity,
                    },
                    related_certificate=certificate_no,
                    related_qualification=title,
                    valid_until=valid_until,
                    extraction_confidence=0.9 if certificate_no and issuer else 0.72,
                    identity_status="needs_review",
                    human_review_required=True,
                    human_review_questions=questions,
                    identity_suffix=f"table-{table.get('table_index')}-{row_index}-{certificate_no or title}",
                )
            )
    return items or [generic_evidence_item(record, physical_exists)]


def extract_personnel_evidence(record: dict[str, Any], physical_exists: bool) -> list[dict[str, Any]]:
    resume = record.get("resume_parse") if isinstance(record.get("resume_parse"), dict) else {}
    profile = record.get("personnel_profile") if isinstance(record.get("personnel_profile"), dict) else {}
    person = str(resume.get("person_name") or profile.get("person_name") or "")
    projects = list(resume.get("projects") or resume.get("project_experience") or [])
    skills = list(resume.get("skills") or [])
    excerpt = personnel_excerpt(resume)
    questions = []
    if profile.get("department_confirm_status") != "manual_confirmed":
        questions.append(f"请确认{person or '该人员'}当前所属部门。")
    questions.append(f"请确认{person or '该人员'}是否可投入本项目及拟任角色。")
    if not physical_exists:
        questions.append("请恢复或重新入库该简历物理源文件。")
    items = [
        catalog_item(
            record,
            evidence_type="personnel_resume",
            title=f"{person or record.get('original_name')}人员简历",
            source_location={"clause_path": "简历结构化解析", "table_indexes": [1, 2]},
            location_precision="clause_approximate",
            excerpt=excerpt,
            structured_facts={
                "person_name": person,
                "department": profile.get("department") or resume.get("department"),
                "department_confirm_status": profile.get("department_confirm_status") or "unknown",
                "work_years": resume.get("work_years"),
                "skills": skills,
                "projects": projects,
                "role_tags": profile.get("role_tags") or [],
                "availability_status": "unknown",
            },
            related_person=person,
            extraction_confidence=0.92 if person and excerpt else 0.7,
            identity_status="identified" if person else "needs_review",
            human_review_required=True,
            human_review_questions=questions,
            identity_suffix=f"resume-{person or record.get('id')}",
        )
    ]
    for index, certificate in enumerate(list(resume.get("certificates") or []), start=1):
        if not isinstance(certificate, dict):
            continue
        title = str(certificate.get("display_name") or certificate.get("standard_name") or certificate.get("normalized_name") or certificate.get("name") or "待识别人员证书")
        cert_no = str(certificate.get("certificate_no") or extract_certificate_number(str(certificate.get("evidence_text") or "")) or "")
        valid_until = parse_date_value(certificate.get("expiry_date") or extract_valid_until(str(certificate.get("evidence_text") or "")))
        excerpt = normalize_text(certificate.get("evidence_text") or "")
        source_location = {
            "asset_id": certificate.get("asset_id"),
            "asset_location": certificate.get("source_location"),
            "structured_index": index,
        }
        cert_questions = []
        if not excerpt:
            cert_questions.append("证书图片尚无OCR摘录，请人工识别证书名称、编号、持有人和有效期。")
        if not valid_until:
            cert_questions.append("请确认人员证书有效期。")
        items.append(
            catalog_item(
                record,
                evidence_type="personnel_certificate",
                title=title,
                source_location=source_location,
                location_precision="snippet_only" if excerpt else "file_only",
                excerpt=excerpt,
                structured_facts=deepcopy(certificate),
                related_person=person,
                related_certificate=cert_no,
                valid_until=valid_until,
                extraction_confidence=certificate_confidence(certificate, excerpt),
                identity_status="identified" if person and title != "待识别人员证书" else "needs_review",
                human_review_required=bool(cert_questions),
                human_review_questions=cert_questions,
                identity_suffix=f"certificate-{person}-{cert_no or title}-{certificate.get('asset_id') or index}",
            )
        )
    return items


def extract_performance_evidence(record: dict[str, Any], physical_exists: bool) -> list[dict[str, Any]]:
    facts = record.get("company_performance") if isinstance(record.get("company_performance"), dict) else {}
    if not facts:
        facts = {}
    excerpt = normalize_text(record.get("parsed_text") or "")[:1200]
    project_name = str(facts.get("project_name") or extract_labeled_value(excerpt, "项目名称") or record.get("original_name") or "")
    is_mock = "mock" in normalize_text(record.get("original_name") or "").lower()
    questions = ["请提供并核验该业绩对应的合同关键页、中标通知书、验收证明或用户证明。"]
    if is_mock:
        questions.append("该文件名包含 mock，仅可作为测试线索，不得作为正式企业业绩。")
    profile = record.get("material_profile") if isinstance(record.get("material_profile"), dict) else {}
    material_types = set(str(item) for item in list(profile.get("material_types") or []))
    type_map = {
        "company_contract": "contract",
        "acceptance_report": "acceptance_certificate",
        "winning_notice": "award_notice",
        "customer_certificate": "customer_certificate",
        "service_certificate": "customer_certificate",
    }
    evidence_types = unique([type_map[item] for item in material_types if item in type_map])
    if not evidence_types or "performance_case" in material_types:
        evidence_types.append("company_performance_summary")
    items = []
    for evidence_type in unique(evidence_types):
        title = project_name if evidence_type == "company_performance_summary" else f"{project_name}-{evidence_type}"
        items.append(catalog_item(
            record,
            evidence_type=evidence_type,
            title=title,
            source_location={"section_locations": [item.get("source_location") for item in list(record.get("sections") or []) if isinstance(item, dict)]},
            location_precision="clause_approximate",
            excerpt=excerpt,
            structured_facts=deepcopy(facts),
            related_project=project_name,
            related_enterprise=str(facts.get("owner_unit") or ""),
            extraction_confidence=0.45 if is_mock else 0.76,
            identity_status="needs_review",
            human_review_required=True,
            human_review_questions=questions,
            identity_suffix=f"performance-{project_name}-{evidence_type}",
        ))
    return items


def generic_evidence_item(record: dict[str, Any], physical_exists: bool) -> dict[str, Any]:
    excerpt = normalize_text(record.get("parsed_text") or "")[:800]
    return catalog_item(
        record,
        evidence_type="other_enterprise_evidence",
        title=str(record.get("original_name") or "企业材料"),
        source_location={"file_id": record.get("id")},
        location_precision="snippet_only" if excerpt else "file_only",
        excerpt=excerpt,
        structured_facts={},
        extraction_confidence=0.5,
        identity_status="needs_review",
        human_review_required=True,
        human_review_questions=["请确认该文件的材料类型、主体和适用范围。"],
        identity_suffix="generic",
    )


def catalog_item(
    record: dict[str, Any],
    *,
    evidence_type: str,
    title: str,
    source_location: dict[str, Any],
    location_precision: str,
    excerpt: str,
    structured_facts: dict[str, Any],
    related_enterprise: str = "",
    related_person: str = "",
    related_project: str = "",
    related_certificate: str = "",
    related_qualification: str = "",
    issue_date: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    evidence_owner: str = "",
    extraction_confidence: float = 0.5,
    identity_status: str = "needs_review",
    human_review_required: bool = True,
    human_review_questions: list[str] | None = None,
    identity_suffix: str = "",
) -> dict[str, Any]:
    file_id = str(record.get("id") or "")
    evidence_id = stable_id("evidence", f"{file_id}|{evidence_type}|{identity_suffix or title}")
    physical_exists = physical_source_exists(record)
    questions = unique(list(human_review_questions or []))
    if not physical_exists:
        questions.append("物理源文件不存在，不能确认该证据。")
        human_review_required = True
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "title": title,
        "normalized_title": normalize_identity(title),
        "source_file_id": file_id,
        "source_file_name": record.get("original_name") or "",
        "source_location": source_location,
        "location_precision": location_precision,
        "excerpt": excerpt,
        "structured_facts": structured_facts,
        "related_enterprise": related_enterprise,
        "related_person": related_person,
        "related_project": related_project,
        "related_certificate": related_certificate,
        "related_qualification": related_qualification,
        "issue_date": issue_date,
        "valid_from": valid_from or record.get("valid_from"),
        "valid_until": valid_until or record.get("valid_to"),
        "evidence_owner": evidence_owner or related_enterprise or related_person,
        "source_type": "enterprise_existing_fact",
        "extraction_confidence": round(float(extraction_confidence), 3),
        "identity_status": identity_status,
        "human_review_required": bool(human_review_required),
        "human_review_questions": questions,
        "physical_file_exists": physical_exists,
        "storage_key": record.get("storage_key"),
        "verification_status": "unverified",
        "decision_ref_ids": [],
        "verification_method": None,
        "manually_verified_fields": {},
        "visual_source_ref": None,
        "verification_missing_fields": [],
    }


def dedupe_evidence_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    exact: dict[tuple[str, str, str], dict[str, Any]] = {}
    same_file_titles: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    cross_file: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        identity = evidence_identity_key(item)
        key = (str(item.get("source_file_id") or ""), str(item.get("evidence_type") or ""), identity)
        normalized_title = normalize_identity(item.get("title"))
        if normalized_title in {normalize_identity("待识别人员证书"), normalize_identity("企业材料")}:
            normalized_title = ""
        title_key = (
            str(item.get("source_file_id") or ""),
            str(item.get("evidence_type") or ""),
            normalize_identity(item.get("related_person")),
            normalized_title,
        )
        retained = exact.get(key) if identity else None
        if retained is None and title_key[-1]:
            retained = same_file_titles.get(title_key)
        if retained is not None:
            duplicate_groups.append(
                {
                    "duplicate_group_id": stable_id("duplicate", "|".join(title_key)),
                    "retained_evidence_id": retained.get("evidence_id"),
                    "duplicate_evidence_id": item.get("evidence_id"),
                    "source_file_id": item.get("source_file_id"),
                    "basis": "同一文件、同一证据类型，且证书编号/人员项目身份或规范化标题一致。",
                    "source_type": "system_judgement",
                }
            )
            if len(str(item.get("excerpt") or "")) > len(str(retained.get("excerpt") or "")):
                retained["excerpt"] = item.get("excerpt")
                retained["source_location"] = item.get("source_location")
                retained["location_precision"] = item.get("location_precision")
            continue
        if identity:
            exact[key] = item
        if title_key[-1]:
            same_file_titles[title_key] = item
        result.append(item)
        cross_file[(str(item.get("evidence_type") or ""), identity)].append(item)
    for (evidence_type, identity), candidates in cross_file.items():
        file_ids = unique([item.get("source_file_id") for item in candidates])
        if identity and len(file_ids) > 1:
            conflicts.append(
                {
                    "conflict_id": stable_id("identity-conflict", f"{evidence_type}|{identity}"),
                    "evidence_type": evidence_type,
                    "identity_key": identity,
                    "evidence_ids": [item.get("evidence_id") for item in candidates],
                    "source_file_ids": file_ids,
                    "reason": "不同文件具有相同名称或身份线索，但缺少足够编号/哈希依据，未自动合并。",
                    "human_review_required": True,
                    "source_type": "human_confirmation_required",
                }
            )
    return result, duplicate_groups, conflicts


def evidence_identity_key(item: dict[str, Any]) -> str:
    facts = item.get("structured_facts") if isinstance(item.get("structured_facts"), dict) else {}
    number = str(facts.get("certificate_number") or item.get("related_certificate") or "")
    if number and len(normalize_identity(number)) >= 5:
        return normalize_identity(number)
    if item.get("related_person") and item.get("evidence_type") == "personnel_resume":
        return normalize_identity(item.get("related_person"))
    if item.get("related_project"):
        return normalize_identity(item.get("related_project"))
    if item.get("identity_status") == "identified" and item.get("normalized_title"):
        return str(item.get("normalized_title"))
    return ""


def build_support_relations(
    requirements: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for requirement in requirements:
        for evidence in list(catalog.get("evidence_items") or []):
            relation = evaluate_support_relation(requirement, evidence)
            if relation is not None:
                relations.append(relation)
    return relations


def evaluate_support_relation(
    requirement: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    expected_subjects = set(str(item) for item in list(requirement.get("expected_evidence_subject_types") or []))
    evidence_type = str(evidence.get("evidence_type") or "")
    supported_subjects = EVIDENCE_TYPE_SUBJECTS.get(evidence_type, set())
    overlap = expected_subjects & supported_subjects
    wrong_performance_type = "project_performance" in expected_subjects and evidence_type in {"personnel_resume", "personnel_certificate"}
    if not overlap and not wrong_performance_type:
        return None

    matched_conditions: list[str] = []
    unmet_conditions: list[str] = []
    match_basis: list[dict[str, str]] = []
    rejection_reasons: list[str] = []
    questions: list[str] = []
    strength = "strong" if overlap else "none"
    usability = "candidate"
    confidence = min(float(evidence.get("extraction_confidence") or 0.0), 0.95)

    match_basis.append(
        {
            "basis_type": "类型匹配",
            "detail": f"证据类型 {evidence_type} 对应需求主题：{'、'.join(sorted(overlap)) or '无'}。",
            "source_type": "system_judgement",
        }
    )
    match_basis.append(
        {
            "basis_type": "文件摘录匹配",
            "detail": f"来源文件 {evidence.get('source_file_name') or '-'}；摘录：{preview(evidence.get('excerpt'), 180) or '无可用摘录'}。",
            "source_type": "enterprise_existing_fact",
        }
    )

    if wrong_performance_type:
        strength = "none"
        usability = "unusable"
        rejection_reasons.append("人员简历或人员证书不能作为企业合同业绩证明。")
        unmet_conditions.append("缺少企业层面的合同、中标通知书、验收证明或用户证明。")
    elif evidence_type == "company_performance_summary":
        strength = "medium"
        facts = evidence.get("structured_facts") or {}
        project_name = str(facts.get("project_name") or evidence.get("related_project") or "")
        if project_name:
            matched_conditions.append(f"存在项目名称：{project_name}")
            match_basis.append(
                {
                    "basis_type": "项目主题匹配",
                    "detail": f"项目名称和内容包含区块链、工业数据或相关技术服务线索：{project_name}。",
                    "source_type": "system_judgement",
                }
            )
        unmet_conditions.extend(["缺少合同关键页", "缺少中标通知书", "缺少验收证明或用户证明"])
        usability = "unusable" if "mock" in str(evidence.get("source_file_name") or "").lower() else "needs_review"
        rejection_reasons.append("当前文件仅为业绩摘要，不能独立构成完整企业业绩证据包。")
        if usability == "unusable":
            rejection_reasons.append("文件名包含 mock，正式覆盖判断中不可用。")
    elif evidence_type == "management_system_certificate":
        title = str(evidence.get("title") or "")
        requested_management = any("management_system_certificate" == item for item in expected_subjects)
        recognized = [name for name, aliases in MANAGEMENT_CERT_NAMES.items() if any(alias in title for alias in aliases)]
        if requested_management and not recognized:
            return None
        if recognized:
            matched_conditions.append(f"证书名称属于评分要求的管理体系类型：{recognized[0]}")
            match_basis.append(
                {
                    "basis_type": "证书或资质名称匹配",
                    "detail": f"证书名称为“{title}”，对应“{recognized[0]}”。",
                    "source_type": "system_judgement",
                }
            )
        apply_validity(evidence, matched_conditions, unmet_conditions, match_basis, questions, rejection_reasons)
        if evidence.get("identity_status") != "identified":
            unmet_conditions.append("持证主体尚未从证书行中确认。")
            questions.append("请核对持证主体是否与投标主体一致。")
        if rejection_reasons:
            usability = "unusable"
        elif recognized and evidence.get("identity_status") == "identified" and not questions and evidence.get("excerpt"):
            usability = "confirmed"
        else:
            usability = "candidate"
    elif evidence_type == "enterprise_qualification_certificate":
        apply_validity(evidence, matched_conditions, unmet_conditions, match_basis, questions, rejection_reasons)
        facts = evidence.get("structured_facts") or {}
        match_basis.append(
            {
                "basis_type": "证书或资质名称匹配",
                "detail": f"目录提取证书名称“{facts.get('certificate_name') or evidence.get('title')}”、编号“{facts.get('certificate_number') or '-'}”。",
                "source_type": "enterprise_existing_fact",
            }
        )
        if evidence.get("identity_status") != "identified":
            unmet_conditions.append("持证主体未确认。")
            questions.append("请核对持证主体和投标主体。")
        if rejection_reasons:
            usability = "unusable"
        elif evidence.get("identity_status") == "identified" and not questions and evidence.get("excerpt"):
            usability = "confirmed"
        else:
            usability = "candidate"
    elif evidence_type == "personnel_resume":
        facts = evidence.get("structured_facts") or {}
        person = str(evidence.get("related_person") or "")
        matched_conditions.extend(
            unique(
                [
                    f"人员姓名：{person}" if person else "",
                    f"工作年限：{facts.get('work_years')}年" if facts.get("work_years") is not None else "",
                    f"部门线索：{facts.get('department')}" if facts.get("department") else "",
                ]
            )
        )
        match_basis.append(
            {
                "basis_type": "人员身份匹配",
                "detail": f"结构化简历识别人员“{person or '-'}”、工作年限“{facts.get('work_years') or '-'}”、部门“{facts.get('department') or '-'}”。",
                "source_type": "enterprise_existing_fact",
            }
        )
        if facts.get("department_confirm_status") != "manual_confirmed":
            unmet_conditions.append("人员部门尚未人工确认。")
            questions.append(f"请确认{person or '该人员'}当前部门。")
        if facts.get("availability_status") != "confirmed":
            unmet_conditions.append("人员本项目投入状态未知。")
            questions.append(f"请确认{person or '该人员'}可投入本项目及拟任角色。")
        usability = "confirmed" if not questions and not evidence.get("human_review_required") and evidence.get("excerpt") else "needs_review"
    elif evidence_type == "personnel_certificate":
        person = str(evidence.get("related_person") or "")
        if person:
            matched_conditions.append(f"证书归属人员：{person}")
        apply_validity(evidence, matched_conditions, unmet_conditions, match_basis, questions, rejection_reasons)
        if not evidence.get("excerpt"):
            strength = "weak"
            unmet_conditions.append("证书图片无OCR摘录。")
            questions.append("请人工识别证书图片并确认名称、编号、持有人和有效期。")
        if rejection_reasons:
            usability = "unusable"
        elif not questions and evidence.get("identity_status") == "identified" and evidence.get("excerpt"):
            usability = "confirmed"
        else:
            usability = "needs_review"
    elif evidence_type in {"contract", "award_notice", "acceptance_certificate", "customer_certificate"}:
        project = str(evidence.get("related_project") or "")
        if project:
            matched_conditions.append(f"材料关联项目：{project}")
        else:
            unmet_conditions.append("项目身份未确认。")
            questions.append("请确认该材料对应的项目名称。")
        match_basis.append(
            {
                "basis_type": "项目主题匹配",
                "detail": f"材料类型为 {evidence_type}，项目线索为“{project or '-'}”；需与同项目的其他证明共同组包。",
                "source_type": "system_judgement",
            }
        )
        usability = "candidate" if evidence.get("excerpt") and project else "needs_review"
    else:
        usability = "needs_review" if evidence.get("human_review_required") else "candidate"

    if not evidence.get("physical_file_exists"):
        usability = "unusable"
        rejection_reasons.append("物理源文件不存在。")
    if not evidence.get("excerpt"):
        if usability == "confirmed":
            usability = "needs_review"
        unmet_conditions.append("缺少可定位摘录。")
    if evidence.get("human_review_required") and usability == "confirmed":
        usability = "needs_review"
    if usability == "candidate" and (questions or evidence.get("human_review_required")):
        # Candidate remains distinct from a system-indeterminate needs_review decision.
        questions.extend(list(evidence.get("human_review_questions") or []))

    quantity_eligible = bool(usability == "confirmed" and strength in {"strong", "medium"} and evidence_is_independent(evidence))
    can_write = usability == "confirmed" and strength in {"strong", "medium"}
    permitted_claims = [f"可引用材料“{evidence.get('title')}”中已确认且有摘录支撑的事实。"] if can_write else []
    restricted_claims = []
    if not can_write:
        restricted_claims.append(f"不得将“{evidence.get('title')}”作为已确认企业能力或满分条件写入正文。")
    if evidence_type == "personnel_resume":
        restricted_claims.append("不得把人员简历中的项目经历表述为企业合同业绩。")
    if evidence_type == "company_performance_summary":
        restricted_claims.append("不得仅凭业绩摘要声称已具备合同、中标或验收证明。")

    questions = unique(questions + list(evidence.get("human_review_questions") or []))
    relation_id = stable_id("support", f"{requirement.get('evidence_requirement_id')}|{evidence.get('evidence_id')}")
    return {
        "support_id": relation_id,
        "evidence_requirement_id": requirement.get("evidence_requirement_id"),
        "evidence_id": evidence.get("evidence_id"),
        "matched_score_item_ids": list(requirement.get("source_score_item_ids") or []),
        "matched_action_group_ids": list(requirement.get("source_action_group_ids") or []),
        "matched_requirement_ids": list(requirement.get("source_requirement_ids") or []),
        "match_basis": match_basis,
        "matched_conditions": unique(matched_conditions),
        "unmet_conditions": unique(unmet_conditions),
        "support_strength": strength,
        "usability_status": usability,
        "confidence": round(confidence, 3),
        "quantity_eligible": quantity_eligible,
        "can_be_used_in_writing": can_write,
        "permitted_claims": permitted_claims,
        "restricted_claims": unique(restricted_claims),
        "rejection_reasons": unique(rejection_reasons),
        "human_review_required": bool(questions or usability == "needs_review"),
        "human_review_questions": questions,
        "source_type": "system_judgement",
        "verification_status": "system_verified" if usability == "confirmed" else "unverified",
        "decision_ref_ids": [],
        "verification_method": None,
    }


def apply_validity(
    evidence: dict[str, Any],
    matched: list[str],
    unmet: list[str],
    basis: list[dict[str, str]],
    questions: list[str],
    rejection: list[str],
) -> None:
    valid_until = parse_date_value(evidence.get("valid_until"))
    if valid_until:
        parsed = parse_iso_date(valid_until)
        if parsed and parsed >= date.today():
            matched.append(f"有效期至{valid_until}，当前未过期。")
            basis.append({"basis_type": "有效期匹配", "detail": f"有效期至{valid_until}。", "source_type": "enterprise_existing_fact"})
        elif parsed:
            unmet.append(f"证据已于{valid_until}过期。")
            rejection.append("证据已过期。")
    else:
        unmet.append("有效期未确认。")
        questions.append("请核对材料有效期。")


def evidence_is_independent(evidence: dict[str, Any]) -> bool:
    if evidence.get("evidence_type") == "company_performance_summary":
        return bool(evidence.get("related_project"))
    if evidence.get("evidence_type") == "patent":
        return bool(evidence.get("related_certificate"))
    return True


def build_evidence_packages(
    catalog: dict[str, Any],
    support_relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    packages = []
    component_types = {
        "company_performance_summary",
        "contract",
        "award_notice",
        "acceptance_certificate",
        "customer_certificate",
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence in list(catalog.get("evidence_items") or []):
        if evidence.get("evidence_type") not in component_types:
            continue
        project = str(evidence.get("related_project") or "")
        identity = normalize_identity(project)
        if not identity:
            identity = f"unresolved:{evidence.get('evidence_id')}"
        grouped[identity].append(evidence)

    for identity, members_data in grouped.items():
        project = next((str(item.get("related_project") or "") for item in members_data if item.get("related_project")), "")
        members = unique([str(item.get("evidence_id") or "") for item in members_data])
        present_types = {str(item.get("evidence_type") or "") for item in members_data}
        missing = []
        if "contract" not in present_types:
            missing.append("合同关键页")
        if not present_types.intersection({"award_notice", "acceptance_certificate", "customer_certificate"}):
            missing.append("中标通知书、验收证明或用户证明至少一类")
        if not project:
            missing.append("项目名称")
        components_with_excerpt = {
            str(item.get("evidence_type") or "")
            for item in members_data
            if item.get("source_file_id") and item.get("source_location") and item.get("excerpt")
        }
        if not missing and {"contract", "acceptance_certificate"}.issubset(components_with_excerpt):
            completeness = "complete"
        elif not missing:
            completeness = "needs_review"
            missing.append("合同或验收材料缺少可定位摘录")
        elif "contract" in present_types or present_types.intersection({"award_notice", "acceptance_certificate", "customer_certificate"}):
            completeness = "incomplete"
        else:
            completeness = "needs_review"
        packages.append(
            {
                "evidence_package_id": stable_id("evidence-package", identity),
                "package_type": "project_performance",
                "title": project or "待确认业绩证据包",
                "member_evidence_ids": members,
                "package_completeness": completeness,
                "missing_package_components": missing,
                "independent_identity_key": normalize_identity(project) or identity,
                "human_review_required": completeness != "complete",
                "source_type": "system_judgement",
            }
        )
    return packages


def apply_complete_package_support(
    support_relations: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> None:
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    for package in packages:
        if package.get("package_completeness") != "complete":
            continue
        member_ids = list(package.get("member_evidence_ids") or [])
        eligible_members = [
            evidence_by_id[item_id]
            for item_id in member_ids
            if item_id in evidence_by_id
            and evidence_by_id[item_id].get("identity_status") == "identified"
            and not evidence_by_id[item_id].get("human_review_required")
        ]
        if len(eligible_members) < 2:
            package["package_completeness"] = "needs_review"
            package["human_review_required"] = True
            package.setdefault("missing_package_components", []).append("证据包成员身份或适用性仍需人工确认")
            continue
        representative = next((item for item in eligible_members if item.get("evidence_type") == "contract"), eligible_members[0])
        for relation in support_relations:
            if relation.get("evidence_id") != representative.get("evidence_id"):
                continue
            requirement_subjects = set()
            for basis in list(relation.get("match_basis") or []):
                if basis.get("basis_type") == "类型匹配":
                    requirement_subjects.add(str(basis.get("detail") or ""))
            if not any("project_performance" in item or "contract" in item for item in requirement_subjects):
                continue
            relation["evidence_package_id"] = package.get("evidence_package_id")
            relation["package_member_evidence_ids"] = member_ids
            relation["support_strength"] = "strong"
            relation["usability_status"] = "confirmed"
            relation["quantity_eligible"] = True
            relation["can_be_used_in_writing"] = True
            relation["human_review_required"] = False
            relation["human_review_questions"] = []
            relation.setdefault("matched_conditions", []).append("合同与验收/客户证明构成可追溯的完整独立业绩证据包。")
            relation.setdefault("match_basis", []).append(
                {
                    "basis_type": "评分规则条件匹配",
                    "detail": f"证据包 {package.get('evidence_package_id')} 包含合同及验收/客户证明，按一个独立业绩计数。",
                    "source_type": "system_judgement",
                }
            )
            relation["permitted_claims"] = [f"可引用证据包“{package.get('title')}”中可追溯的合同与验收事实。"]
            relation["restricted_claims"] = []


def build_score_item_coverage(
    score_items: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    catalog: dict[str, Any],
    packages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    for score in score_items:
        score_id = str(score.get("id") or "")
        reqs = [item for item in requirements if score_id in list(item.get("source_score_item_ids") or [])]
        req_ids = {str(item.get("evidence_requirement_id") or "") for item in reqs}
        score_relations = [item for item in relations if item.get("evidence_requirement_id") in req_ids]
        quantity = select_coverage_quantity(reqs)
        counts = relation_status_counts(score_relations, evidence_by_id, packages, score_id)
        required_count = quantity.get("required_count")
        remaining = max(0, int(required_count) - counts["confirmed_count"]) if required_count is not None else None
        quality_gaps = unique([condition for item in score_relations for condition in list(item.get("unmet_conditions") or [])])
        if reqs and not score_relations:
            quality_gaps.append("企业知识库中未找到对应证据类型的材料。")
        status = full_score_support_status(reqs, required_count, remaining, counts, quality_gaps)
        result.append(
            {
                "score_item_id": score_id,
                "score_item_name": score.get("item_name") or "",
                "max_score": (score.get("score_range") or {}).get("max") if isinstance(score.get("score_range"), dict) else score.get("score"),
                "full_score_condition": score.get("score_rule") or score.get("raw_text") or "",
                "evidence_requirement_ids": [item.get("evidence_requirement_id") for item in reqs],
                "confirmed_evidence_ids": counts["confirmed_evidence_ids"],
                "candidate_evidence_ids": counts["candidate_evidence_ids"],
                "weak_evidence_ids": counts["weak_evidence_ids"],
                "unusable_evidence_ids": counts["unusable_evidence_ids"],
                "required_count": required_count,
                "confirmed_count": counts["confirmed_count"],
                "candidate_count": counts["candidate_count"],
                "needs_review_count": counts["needs_review_count"],
                "remaining_confirmed_gap": remaining,
                "quality_gaps": quality_gaps,
                "can_support_full_score_writing": status,
                "coverage_summary": coverage_summary(required_count, counts, remaining, bool(reqs)),
                "allowed_claims": unique([claim for item in score_relations for claim in list(item.get("permitted_claims") or [])]),
                "cautious_claims": cautious_claims(score_relations, evidence_by_id),
                "forbidden_claims": unique([claim for item in score_relations for claim in list(item.get("restricted_claims") or [])]),
                "human_review_required": any(item.get("human_review_required") for item in score_relations),
                "human_review_questions": unique([q for item in score_relations for q in list(item.get("human_review_questions") or [])]),
                "source_type": "system_judgement",
            }
        )
    return result


def build_requirement_coverage(
    requirements: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    catalog: dict[str, Any],
    packages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    result = []
    for requirement in requirements:
        req_id = str(requirement.get("evidence_requirement_id") or "")
        req_relations = [item for item in relations if item.get("evidence_requirement_id") == req_id]
        counts = relation_status_counts(req_relations, evidence_by_id, packages)
        required_count = (requirement.get("quantity_rule") or {}).get("required_count")
        remaining = max(0, int(required_count) - counts["confirmed_count"]) if required_count is not None else None
        result.append(
            {
                "evidence_requirement_id": req_id,
                "canonical_title": requirement.get("canonical_title"),
                "source_score_item_ids": requirement.get("source_score_item_ids") or [],
                "source_action_group_ids": requirement.get("source_action_group_ids") or [],
                "source_requirement_ids": requirement.get("source_requirement_ids") or [],
                "confirmed_evidence_ids": counts["confirmed_evidence_ids"],
                "candidate_evidence_ids": counts["candidate_evidence_ids"],
                "needs_review_evidence_ids": counts["needs_review_evidence_ids"],
                "unusable_evidence_ids": counts["unusable_evidence_ids"],
                "required_count": required_count,
                "confirmed_count": counts["confirmed_count"],
                "candidate_count": counts["candidate_count"],
                "needs_review_count": counts["needs_review_count"],
                "remaining_confirmed_gap": remaining,
                "quality_gaps": unique([condition for item in req_relations for condition in list(item.get("unmet_conditions") or [])]) or (["未找到候选材料。"] if not req_relations else []),
                "human_review_required": any(item.get("human_review_required") for item in req_relations),
                "source_type": "system_judgement",
            }
        )
    return result


def relation_status_counts(
    relations: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    packages: list[dict[str, Any]],
    score_id: str = "",
) -> dict[str, Any]:
    by_status: dict[str, list[str]] = defaultdict(list)
    weak: list[str] = []
    for relation in relations:
        evidence_id = str(relation.get("evidence_id") or "")
        by_status[str(relation.get("usability_status") or "unusable")].append(evidence_id)
        if relation.get("support_strength") == "weak":
            weak.append(evidence_id)
    confirmed_eligible = [
        relation
        for relation in relations
        if relation.get("usability_status") == "confirmed" and relation.get("quantity_eligible")
    ]
    confirmed_keys = unique([quantity_identity_key(evidence_by_id.get(str(item.get("evidence_id") or ""), {}), packages, score_id) for item in confirmed_eligible])
    candidate_ids = unique(by_status.get("candidate", []))
    needs_review_ids = unique(by_status.get("needs_review", []))
    return {
        "confirmed_evidence_ids": unique(by_status.get("confirmed", [])),
        "candidate_evidence_ids": candidate_ids,
        "needs_review_evidence_ids": needs_review_ids,
        "weak_evidence_ids": unique(weak),
        "unusable_evidence_ids": unique(by_status.get("unusable", [])),
        "confirmed_count": len(confirmed_keys),
        "candidate_count": len(unique([quantity_identity_key(evidence_by_id.get(item, {}), packages, score_id) for item in candidate_ids])),
        "needs_review_count": len(unique([quantity_identity_key(evidence_by_id.get(item, {}), packages, score_id) for item in needs_review_ids])),
    }


def quantity_identity_key(evidence: dict[str, Any], packages: list[dict[str, Any]], score_id: str = "") -> str:
    evidence_id = str(evidence.get("evidence_id") or "")
    if "研发团队规模" in score_id and evidence.get("related_person"):
        return f"person:{normalize_identity(evidence.get('related_person'))}"
    package = next((item for item in packages if evidence_id in list(item.get("member_evidence_ids") or [])), None)
    if package:
        return str(package.get("independent_identity_key") or package.get("evidence_package_id") or evidence_id)
    return evidence_identity_key(evidence) or evidence_id


def select_coverage_quantity(requirements: list[dict[str, Any]]) -> dict[str, Any]:
    explicit = [item.get("quantity_rule") or {} for item in requirements if (item.get("quantity_rule") or {}).get("required_count") is not None]
    if not explicit:
        return not_explicit_quantity_rule()
    explicit.sort(key=lambda item: -int(item.get("required_count") or 0))
    return explicit[0]


def full_score_support_status(
    requirements: list[dict[str, Any]],
    required_count: int | None,
    remaining: int | None,
    counts: dict[str, Any],
    quality_gaps: list[str],
) -> str:
    if not requirements:
        return "not_applicable"
    if required_count is not None:
        if remaining == 0 and not quality_gaps:
            return "yes"
        if counts["confirmed_count"] > 0:
            return "mostly"
        if counts["candidate_count"] or counts["needs_review_count"]:
            return "needs_review"
        return "no"
    if counts["confirmed_count"] and not quality_gaps:
        return "mostly"
    if counts["candidate_count"] or counts["needs_review_count"]:
        return "needs_review"
    return "no"


def coverage_summary(required_count: int | None, counts: dict[str, Any], remaining: int | None, applicable: bool) -> str:
    if not applicable:
        return "该评分项当前不以企业证据为直接覆盖条件。"
    if required_count is None:
        return f"原文未明确数量；确认 {counts['confirmed_count']} 项，候选 {counts['candidate_count']} 项，待复核 {counts['needs_review_count']} 项。"
    return f"满分数量 {required_count}；确认 {counts['confirmed_count']} 项，候选 {counts['candidate_count']} 项，待复核 {counts['needs_review_count']} 项，确认口径仍缺 {remaining} 项。"


def cautious_claims(relations: list[dict[str, Any]], evidence_by_id: dict[str, dict[str, Any]]) -> list[str]:
    return unique(
        [
            f"材料“{evidence_by_id.get(str(item.get('evidence_id') or ''), {}).get('title') or item.get('evidence_id')}”仅可作为待复核线索，不得表述为已确认事实。"
            for item in relations
            if item.get("usability_status") in {"candidate", "needs_review"}
        ]
    )


def build_missing_evidence_requests(
    requirements: list[dict[str, Any]],
    score_coverage: list[dict[str, Any]],
    requirement_coverage: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    coverage_by_req = {str(item.get("evidence_requirement_id") or ""): item for item in requirement_coverage}
    result = []
    for requirement in requirements:
        req_id = str(requirement.get("evidence_requirement_id") or "")
        coverage = coverage_by_req.get(req_id, {})
        required_count = coverage.get("required_count")
        confirmed_count = int(coverage.get("confirmed_count") or 0)
        remaining = coverage.get("remaining_confirmed_gap") if required_count is not None else "not_explicit"
        quality_gaps = list(coverage.get("quality_gaps") or [])
        if required_count is not None and not remaining and not quality_gaps:
            continue
        if required_count is None and confirmed_count and not quality_gaps:
            continue
        result.append(
            {
                "missing_request_id": stable_id("missing-evidence", req_id),
                "evidence_requirement_id": req_id,
                "related_evidence_requirement_ids": [req_id],
                "title": f"补充：{requirement.get('canonical_title') or '企业证明材料'}",
                "related_score_item_ids": list(requirement.get("source_score_item_ids") or []),
                "related_action_group_ids": list(requirement.get("source_action_group_ids") or []),
                "related_requirement_ids": list(requirement.get("source_requirement_ids") or []),
                "missing_evidence_type": list(requirement.get("expected_evidence_subject_types") or []),
                "missing_package_components": quality_gaps,
                "required_count": required_count,
                "confirmed_count": confirmed_count,
                "remaining_gap": remaining,
                "quality_gap": quality_gaps,
                "reason_needed": "现有 confirmed 证据不足以覆盖数量或关键质量条件。",
                "suggested_materials": list(requirement.get("expected_evidence_types") or []),
                "suggested_owner": suggested_owner(requirement),
                "priority": requirement.get("priority") or "high",
                "affects_full_score_writing": bool(requirement.get("source_score_item_ids")),
                "affects_mandatory_response": requirement.get("requirement_origin") in {"qualification_requirement", "mandatory_requirement", "score_and_requirement"},
                "human_review_required": bool(coverage.get("needs_review_count")),
                "source_type": "suggested_material",
            }
        )
    return result


def suggested_owner(requirement: dict[str, Any]) -> str:
    subjects = set(requirement.get("expected_evidence_subject_types") or [])
    if subjects & {"personnel_resume", "personnel_qualification", "personnel_certificate", "organization_structure"}:
        return "人力资源或项目管理部门"
    if subjects & {"financial_evidence", "credit_evidence"}:
        return "财务或法务合规部门"
    if subjects & {"project_performance", "contract", "award_notice", "acceptance_certificate", "customer_certificate"}:
        return "销售、合同或项目交付部门"
    return "企业资质或综合管理部门"


def build_evidence_manual_review(
    catalog: dict[str, Any],
    requirements: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    relation_by_evidence = defaultdict(list)
    for relation in relations:
        relation_by_evidence[str(relation.get("evidence_id") or "")].append(relation)
    for evidence in list(catalog.get("evidence_items") or []):
        evidence_id = str(evidence.get("evidence_id") or "")
        for question in list(evidence.get("human_review_questions") or []):
            related = relation_by_evidence.get(evidence_id, [])
            result.append(
                manual_review_item(
                    category=manual_category(question),
                    evidence_id=evidence_id,
                    requirement_id=str(related[0].get("evidence_requirement_id") or "") if related else "",
                    score_ids=unique([score_id for item in related for score_id in list(item.get("matched_score_item_ids") or [])]),
                    source_requirement_ids=unique([req_id for item in related for req_id in list(item.get("matched_requirement_ids") or [])]),
                    question=str(question),
                    current_judgement=str(evidence.get("identity_status") or "needs_review"),
                )
            )
    for relation in relations:
        evidence_questions = set(
            str(item)
            for item in list(evidence_by_id.get(str(relation.get("evidence_id") or ""), {}).get("human_review_questions") or [])
        )
        for question in list(relation.get("human_review_questions") or []):
            if str(question) in evidence_questions:
                continue
            result.append(
                manual_review_item(
                    category=manual_category(question),
                    evidence_id=str(relation.get("evidence_id") or ""),
                    requirement_id=str(relation.get("evidence_requirement_id") or ""),
                    score_ids=list(relation.get("matched_score_item_ids") or []),
                    source_requirement_ids=list(relation.get("matched_requirement_ids") or []),
                    question=str(question),
                    current_judgement=f"{relation.get('support_strength')} / {relation.get('usability_status')}",
                )
            )
    for package in packages:
        if package.get("package_completeness") != "complete":
            result.append(
                manual_review_item(
                    category="package_completeness",
                    evidence_id=str((package.get("member_evidence_ids") or [""])[0]),
                    requirement_id="",
                    score_ids=[],
                    source_requirement_ids=[],
                    question=f"请补齐业绩证据包“{package.get('title')}”：{'、'.join(package.get('missing_package_components') or [])}。",
                    current_judgement=str(package.get("package_completeness")),
                )
            )
    for conflict in list(catalog.get("unresolved_identity_conflicts") or []):
        result.append(
            manual_review_item(
                category="duplicate_evidence",
                evidence_id=str((conflict.get("evidence_ids") or [""])[0]),
                requirement_id="",
                score_ids=[],
                source_requirement_ids=[],
                question="请确认不同来源文件中的同名材料是否为同一证据。",
                current_judgement="未自动合并",
            )
        )
    deduped = []
    seen = set()
    for item in result:
        key = (item["category"], item["evidence_id"], item["evidence_requirement_id"], item["question"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def manual_review_item(
    *,
    category: str,
    evidence_id: str,
    requirement_id: str,
    score_ids: list[str],
    source_requirement_ids: list[str],
    question: str,
    current_judgement: str,
) -> dict[str, Any]:
    return {
        "manual_review_id": stable_id("evidence-review", f"{category}|{evidence_id}|{requirement_id}|{question}"),
        "category": category,
        "evidence_id": evidence_id,
        "evidence_requirement_id": requirement_id,
        "score_item_ids": unique(score_ids),
        "requirement_ids": unique(source_requirement_ids),
        "question": question,
        "current_judgement": current_judgement,
        "confirmation_needed": question,
        "source_type": "human_confirmation_required",
    }


def manual_category(question: str) -> str:
    mappings = [
        (["同一证据", "重复"], "duplicate_evidence"),
        (["有效期", "过期"], "evidence_validity"),
        (["部门"], "personnel_department"),
        (["投入", "可投入", "拟任"], "personnel_availability"),
        (["项目相关", "相关性"], "project_relevance"),
        (["完整", "补齐", "合同关键页", "验收"], "package_completeness"),
        (["OCR", "摘录", "位置"], "source_location"),
        (["数量", "独立"], "quantity_independence"),
        (["类型", "证书名称"], "evidence_type"),
        (["主体", "持证", "身份", "人员"], "evidence_identity"),
        (["评分", "适用"], "score_applicability"),
    ]
    for terms, category in mappings:
        if any(term in question for term in terms):
            return category
    return "other"


def build_catalog_statistics(catalog: dict[str, Any]) -> dict[str, Any]:
    items = list(catalog.get("evidence_items") or [])
    return {
        "evidence_count": len(items),
        "source_file_count": len(list(catalog.get("source_files") or [])),
        "evidence_type_counts": dict(sorted(Counter(str(item.get("evidence_type") or "unknown") for item in items).items())),
        "identity_status_counts": dict(sorted(Counter(str(item.get("identity_status") or "unknown") for item in items).items())),
        "human_review_count": len([item for item in items if item.get("human_review_required")]),
        "duplicate_group_count": len(list(catalog.get("duplicate_groups") or [])),
        "unresolved_identity_conflict_count": len(list(catalog.get("unresolved_identity_conflicts") or [])),
        "missing_location_or_excerpt_count": len([item for item in items if not item.get("source_location") or not item.get("excerpt")]),
    }


def build_coverage_statistics(
    catalog: dict[str, Any],
    requirements: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    score_coverage: list[dict[str, Any]],
    missing_requests: list[dict[str, Any]],
    manual_review: list[dict[str, Any]],
) -> dict[str, Any]:
    usability = Counter(str(item.get("usability_status") or "unknown") for item in relations)
    strength = Counter(str(item.get("support_strength") or "unknown") for item in relations)
    return {
        "evidence_requirement_count": len(requirements),
        "enterprise_evidence_count": len(list(catalog.get("evidence_items") or [])),
        "support_relation_count": len(relations),
        "usability_status_counts": dict(sorted(usability.items())),
        "support_strength_counts": dict(sorted(strength.items())),
        "confirmed_count": usability.get("confirmed", 0),
        "candidate_count": usability.get("candidate", 0),
        "needs_review_count": usability.get("needs_review", 0),
        "unusable_count": usability.get("unusable", 0),
        "strong_count": strength.get("strong", 0),
        "medium_count": strength.get("medium", 0),
        "weak_count": strength.get("weak", 0),
        "none_count": strength.get("none", 0),
        "score_item_coverage_count": len(score_coverage),
        "explicit_quantity_score_count": len([item for item in score_coverage if item.get("required_count") is not None]),
        "missing_evidence_request_count": len(missing_requests),
        "manual_review_count": len(manual_review),
        "evidence_package_count": len(packages),
        "evidence_package_completeness_counts": dict(sorted(Counter(str(item.get("package_completeness") or "unknown") for item in packages).items())),
        "confirmed_without_source_or_excerpt_count": len(
            [
                item
                for item in relations
                if item.get("usability_status") == "confirmed"
                and not confirmed_relation_has_source(item, catalog)
            ]
        ),
    }


def build_warnings(
    project: dict[str, Any],
    catalog: dict[str, Any],
    relations: list[dict[str, Any]],
) -> list[str]:
    warnings = list(catalog.get("build_warnings") or [])
    material_analysis = project.get("material_analysis") if isinstance(project.get("material_analysis"), dict) else {}
    if material_analysis:
        warnings.append("旧 material_analysis 仅作为兼容数据保留；match_status=matched 未用于 confirmed 判定。")
    if any("mock" in str(item.get("source_file_name") or "").lower() for item in list(catalog.get("evidence_items") or [])):
        warnings.append("检测到 mock 企业材料；正式覆盖中已标记 unusable，不计入数量。")
    if not any(item.get("usability_status") == "confirmed" for item in relations):
        warnings.append("当前没有达到 confirmed 标准的支撑关系，后续正文不得把候选材料写成已确认企业事实。")
    return unique(warnings)


def write_derived_outputs(evidence_dir: Path, matrix: dict[str, Any]) -> None:
    generated_from = str(evidence_dir / "evidence_coverage_matrix.json")
    score_summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": matrix.get("generated_at"),
        "lot_name": matrix.get("lot_name"),
        "generated_from": generated_from,
        "score_item_coverage": matrix.get("score_item_coverage") or [],
        "statistics": {
            "score_item_count": len(list(matrix.get("score_item_coverage") or [])),
            "explicit_quantity_score_count": (matrix.get("statistics") or {}).get("explicit_quantity_score_count", 0),
        },
    }
    missing = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": matrix.get("generated_at"),
        "lot_name": matrix.get("lot_name"),
        "generated_from": generated_from,
        "requests": matrix.get("global_missing_evidence") or [],
        "statistics": {"request_count": len(list(matrix.get("global_missing_evidence") or []))},
    }
    manual = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": matrix.get("generated_at"),
        "lot_name": matrix.get("lot_name"),
        "generated_from": generated_from,
        "review_items": matrix.get("global_manual_review_items") or [],
        "groups": group_manual_review_items(list(matrix.get("global_manual_review_items") or [])),
        "statistics": {"review_count": len(list(matrix.get("global_manual_review_items") or []))},
    }
    (evidence_dir / "score_evidence_summary.json").write_text(json.dumps(score_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "missing_evidence_request.json").write_text(json.dumps(missing, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "evidence_manual_review.json").write_text(json.dumps(manual, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "evidence_coverage_matrix.md").write_text(render_evidence_coverage_markdown(matrix), encoding="utf-8")


def group_manual_review_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    categories = [
        "evidence_identity",
        "evidence_type",
        "evidence_validity",
        "project_relevance",
        "personnel_department",
        "personnel_availability",
        "score_applicability",
        "quantity_independence",
        "package_completeness",
        "source_location",
        "duplicate_evidence",
        "other",
    ]
    return {category: [item for item in items if item.get("category") == category] for category in categories}


def render_evidence_coverage_markdown(matrix: dict[str, Any]) -> str:
    requirements = {str(item.get("evidence_requirement_id") or ""): item for item in list(matrix.get("evidence_requirements") or [])}
    relations_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in list(matrix.get("support_relations") or []):
        relations_by_req[str(relation.get("evidence_requirement_id") or "")].append(relation)
    catalog_path = Path(str(matrix.get("evidence_catalog_path") or ""))
    catalog = load_json(catalog_path) if catalog_path.exists() else {"evidence_items": []}
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    score_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score in list(matrix.get("score_item_coverage") or []):
        for req_id in list(score.get("evidence_requirement_ids") or []):
            score_by_req[str(req_id)].append(score)
    coverage_by_req = {str(item.get("evidence_requirement_id") or ""): item for item in list(matrix.get("requirement_coverage") or [])}
    lines = [
        f"# 企业证据覆盖矩阵：{matrix.get('lot_name') or '-'}",
        "",
        f"- 正式覆盖数据源：`{Path(str(matrix.get('evidence_catalog_path') or '')).parent / 'evidence_coverage_matrix.json'}`",
        f"- 企业证据目录：`{matrix.get('evidence_catalog_path') or '-'}`",
        f"- 证据需求：{len(requirements)}",
        f"- 支撑关系：{len(list(matrix.get('support_relations') or []))}",
        "",
    ]
    for req_id, requirement in requirements.items():
        coverage = coverage_by_req.get(req_id, {})
        score_rows = score_by_req.get(req_id, [])
        score_names = unique([item.get("score_item_name") for item in score_rows])
        quantity = requirement.get("quantity_rule") or {}
        lines.extend(
            [
                f"## {requirement.get('canonical_title') or req_id}",
                "",
                f"- 评分项或要求：{'、'.join(score_names) or '资格/证明材料要求'}",
                f"- 满分或证明条件：{'；'.join(requirement.get('required_conditions') or []) or '按原评分规则和证明要求'}",
                f"- 需要证据：{'、'.join(requirement.get('expected_evidence_types') or []) or '-'}",
                f"- 明确数量要求：{quantity.get('required_count') if quantity.get('required_count') is not None else '未明确'}",
                f"- 当前确认/候选/待复核：{coverage.get('confirmed_count', 0)} / {coverage.get('candidate_count', 0)} / {coverage.get('needs_review_count', 0)}",
                f"- 确认口径缺口：{coverage.get('remaining_confirmed_gap') if coverage.get('remaining_confirmed_gap') is not None else '不按数量计算'}",
                f"- 质量缺口：{'；'.join(coverage.get('quality_gaps') or []) or '-'}",
                "",
            ]
        )
        relations = relations_by_req.get(req_id, [])
        if not relations:
            lines.extend(["- 当前未找到候选企业材料。", ""])
            continue
        for relation in relations:
            evidence = evidence_by_id.get(str(relation.get("evidence_id") or ""), {})
            basis = "；".join(str(item.get("detail") or "") for item in list(relation.get("match_basis") or []) if isinstance(item, dict))
            lines.extend(
                [
                    f"### {evidence.get('title') or relation.get('evidence_id')}",
                    "",
                    f"- 来源文件：{evidence.get('source_file_name') or '-'} / {json.dumps(evidence.get('source_location') or {}, ensure_ascii=False)}",
                    f"- 摘录：{evidence.get('excerpt') or '-'}",
                    f"- 匹配依据：{basis or '-'}",
                    f"- 支撑强度 / 可用状态：{relation.get('support_strength')} / {relation.get('usability_status')}",
                    f"- 是否计入数量：{'是' if relation.get('quantity_eligible') else '否'}",
                    f"- 未满足条件：{'；'.join(relation.get('unmet_conditions') or []) or '-'}",
                    f"- 人工复核：{'；'.join(relation.get('human_review_questions') or []) or '-'}",
                    f"- 正文允许写：{'；'.join(relation.get('permitted_claims') or []) or '当前无'}",
                    f"- 正文不能写：{'；'.join(relation.get('restricted_claims') or []) or '-'}",
                    "",
                ]
            )
        if score_rows:
            lines.append(f"- 是否足以支撑满分写作：{'、'.join(unique([item.get('can_support_full_score_writing') for item in score_rows]))}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def update_technical_context_evidence_index(
    context: dict[str, Any],
    requirements: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    catalog_path: Path,
    matrix_path: Path,
    generated_at: str,
) -> None:
    requirements_by_chapter: dict[str, list[str]] = defaultdict(list)
    relations_by_chapter: dict[str, list[str]] = defaultdict(list)
    req_by_id = {str(item.get("evidence_requirement_id") or ""): item for item in requirements}
    for requirement in requirements:
        for chapter_id in list(requirement.get("related_context_chapter_ids") or []):
            requirements_by_chapter[str(chapter_id)].append(str(requirement.get("evidence_requirement_id") or ""))
    for relation in relations:
        requirement = req_by_id.get(str(relation.get("evidence_requirement_id") or ""), {})
        for chapter_id in list(requirement.get("related_context_chapter_ids") or []):
            relations_by_chapter[str(chapter_id)].append(str(relation.get("support_id") or ""))
    for chapter in list(context.get("chapters") or []):
        chapter_id = str(chapter.get("chapter_id") or "")
        chapter["evidence_requirement_ids"] = unique(requirements_by_chapter.get(chapter_id, []))
        chapter["support_relation_ids"] = unique(relations_by_chapter.get(chapter_id, []))
    context["evidence_coverage_matrix_path"] = str(matrix_path)
    context["enterprise_evidence_catalog_path"] = str(catalog_path)
    context["evidence_coverage_index"] = {
        "matrix_path": str(matrix_path),
        "catalog_path": str(catalog_path),
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "formal_evidence_source": "evidence_coverage_matrix.json",
        "legacy_evidence_refs_policy": "compatibility_only; legacy material_analysis matched is not confirmed evidence",
        "source_type": "system_judgement",
    }


def evidence_output_paths(evidence_dir: Path) -> dict[str, str]:
    return {
        "enterprise_evidence_catalog": str(evidence_dir / "enterprise_evidence_catalog.json"),
        "evidence_coverage_matrix": str(evidence_dir / "evidence_coverage_matrix.json"),
        "evidence_coverage_markdown": str(evidence_dir / "evidence_coverage_matrix.md"),
        "score_evidence_summary": str(evidence_dir / "score_evidence_summary.json"),
        "missing_evidence_request": str(evidence_dir / "missing_evidence_request.json"),
        "evidence_manual_review": str(evidence_dir / "evidence_manual_review.json"),
        "evidence_resolution_queue": str(evidence_dir / "evidence_resolution_queue.json"),
        "evidence_resolution_queue_markdown": str(evidence_dir / "evidence_resolution_queue.md"),
        "material_intake_plan": str(evidence_dir / "material_intake_plan.json"),
        "material_intake_plan_markdown": str(evidence_dir / "material_intake_plan.md"),
        "evidence_resolution_summary": str(evidence_dir / "evidence_resolution_summary.json"),
    }


def validate_inputs(lot_name: str, context: dict[str, Any], matrix: dict[str, Any], action_index: dict[str, Any]) -> None:
    for name, value in [
        ("technical_response_context", context),
        ("tender_requirement_matrix", matrix),
        ("actionable_requirement_index", action_index),
    ]:
        if str(value.get("lot_name") or "") != lot_name:
            raise ValueError(f"{name} lot_name mismatch: expected {lot_name}")


def validate_catalog(catalog: dict[str, Any]) -> None:
    for item in list(catalog.get("evidence_items") or []):
        if item.get("source_type") != "enterprise_existing_fact":
            raise ValueError(f"catalog evidence source_type invalid: {item.get('evidence_id')}")
        if not item.get("source_file_id") or not item.get("source_file_name"):
            raise ValueError(f"catalog evidence source missing: {item.get('evidence_id')}")


def validate_coverage_matrix(matrix: dict[str, Any], catalog: dict[str, Any]) -> None:
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    for relation in list(matrix.get("support_relations") or []):
        if relation.get("support_strength") not in SUPPORT_STRENGTHS:
            raise ValueError(f"invalid support_strength: {relation.get('support_id')}")
        if relation.get("usability_status") not in USABILITY_STATUSES:
            raise ValueError(f"invalid usability_status: {relation.get('support_id')}")
        if not relation.get("match_basis") or relation.get("evidence_id") not in evidence_by_id:
            raise ValueError(f"support relation lacks basis or evidence: {relation.get('support_id')}")
        if relation.get("usability_status") == "confirmed":
            if not confirmed_relation_has_source(relation, catalog):
                raise ValueError(f"confirmed support lacks traceable source: {relation.get('support_id')}")
    for item in list(matrix.get("score_item_coverage") or []):
        if item.get("can_support_full_score_writing") not in FULL_SCORE_STATUSES:
            raise ValueError(f"invalid full-score support status: {item.get('score_item_id')}")
    for package in list(matrix.get("evidence_packages") or []):
        if package.get("package_completeness") not in PACKAGE_COMPLETENESS:
            raise ValueError(f"invalid package completeness: {package.get('evidence_package_id')}")


def confirmed_relation_has_source(relation: dict[str, Any], catalog: dict[str, Any]) -> bool:
    evidence = next((item for item in list(catalog.get("evidence_items") or []) if item.get("evidence_id") == relation.get("evidence_id")), {})
    text_traceable = bool(evidence.get("source_file_id") and evidence.get("source_location") and evidence.get("excerpt"))
    visual_traceable = bool(
        evidence.get("source_file_id")
        and evidence.get("visual_source_ref")
        and evidence.get("manually_verified_fields")
        and evidence.get("verification_method") == "human_visual_review"
    )
    return text_traceable or visual_traceable


def ensure_no_decision_terms(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False)
    for term in FORBIDDEN_DECISION_TERMS:
        if term.lower() in text.lower():
            raise ValueError(f"forbidden bid decision term found: {term}")


def physical_source_exists(record: dict[str, Any]) -> bool:
    storage_key = str(record.get("storage_key") or "").replace("\\", "/").lstrip("/")
    if not storage_key:
        return False
    path = Path(storage_key)
    if path.parts and path.parts[0].lower() == "storage":
        path = Path(*path.parts[1:])
    return (STORAGE_DIR / path).exists()


def personnel_excerpt(resume: dict[str, Any]) -> str:
    person = str(resume.get("person_name") or "")
    years = resume.get("work_years")
    skills = unique(
        [str(item.get("name") or item.get("raw_text") or "") if isinstance(item, dict) else str(item) for item in list(resume.get("skills") or [])]
    )
    projects = unique(
        [str(item.get("name") or item.get("raw_text") or "") if isinstance(item, dict) else str(item) for item in list(resume.get("projects") or [])]
    )
    return "；".join(
        item
        for item in [
            f"姓名：{person}" if person else "",
            f"工作年限：{years}年" if years is not None else "",
            f"技能：{'、'.join(skills[:8])}" if skills else "",
            f"项目经历：{'、'.join(projects[:6])}" if projects else "",
        ]
        if item
    )


def certificate_confidence(certificate: dict[str, Any], excerpt: str) -> float:
    value = certificate.get("standardization_confidence")
    mapping = {"high": 0.9, "medium": 0.72, "low": 0.48}
    if isinstance(value, str):
        return mapping.get(value.lower(), 0.55)
    return 0.75 if excerpt else 0.35


def parse_date_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    english = re.search(r"(?i)(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})\s*,?\s*(\d{4})", text)
    if english:
        months = {name: index for index, name in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
        return f"{english.group(3)}-{months[english.group(1).title()]:02d}-{int(english.group(2)):02d}"
    match = re.search(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    iso = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    return iso.group(0) if iso else None


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def extract_certificate_number(text: str) -> str:
    match = re.search(r"(?i)(?:Certificate\s*ID|证书编号)\s*[:：]\s*([A-Za-z0-9-]+)", text)
    return match.group(1) if match else ""


def extract_valid_until(text: str) -> str:
    match = re.search(r"(?i)Valid\s*Until\s*[:：]\s*([^\n]+)", text)
    return match.group(1).strip() if match else ""


def extract_labeled_value(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^；。\n]+)", text)
    return match.group(1).strip() if match else ""


def normalize_identity(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:14]
    return f"{prefix}-{digest}"


def preview(value: Any, limit: int = 180) -> str:
    text = normalize_text(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."

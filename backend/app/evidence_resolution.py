from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.tender_requirement_matrix import load_json, safe_path_name, unique
from config import DATA_DIR


SCHEMA_VERSION = "1.0"

DECISIONS = {"confirm", "reject", "needs_more_material", "not_applicable", "defer", "partially_confirm"}
VERIFICATION_METHODS = {
    "source_text_review",
    "structured_fact_review",
    "human_visual_review",
    "cross_document_review",
    "external_confirmation",
    "other",
}
DECISION_STATUSES = {"active", "superseded", "withdrawn"}
VERIFICATION_STATUSES = {"unverified", "human_confirmed", "system_verified", "rejected", "superseded"}
RESOLUTION_SUBJECT_TYPES = {
    "enterprise_identity",
    "person_identity",
    "person_department",
    "person_availability",
    "person_role",
    "evidence_document",
    "certificate_identity",
    "certificate_validity",
    "project_identity",
    "project_relevance",
    "evidence_package",
    "evidence_duplicate",
    "quantity_independence",
    "support_applicability",
    "source_location",
    "other",
}
RESOLUTION_STATUSES = {
    "pending",
    "partially_resolved",
    "resolved",
    "rejected",
    "needs_more_material",
    "deferred",
    "superseded",
}
BLOCKING_LEVELS = {
    "blocks_confirmed_evidence",
    "blocks_full_score_assessment",
    "blocks_mandatory_response",
    "does_not_block_writing",
    "informational",
}
INTAKE_STATUSES = {
    "not_requested",
    "requested",
    "partially_received",
    "received_pending_review",
    "complete",
    "closed_not_available",
    "deferred",
}


def evidence_decision_store_path(lot_name: str, data_dir: str | Path = DATA_DIR) -> Path:
    return Path(data_dir) / "manual_reviews" / safe_path_name(lot_name) / "evidence_resolution_decisions.json"


def empty_decision_store(lot_name: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "lot_name": lot_name,
        "decisions": [],
        "superseded_decision_ids": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def ensure_decision_store(lot_name: str, path: str | Path | None = None) -> tuple[dict[str, Any], Path]:
    store_path = Path(path) if path is not None else evidence_decision_store_path(lot_name)
    if store_path.exists():
        store = load_json(store_path)
        validate_decision_store(store, lot_name)
        return store, store_path
    store = empty_decision_store(lot_name)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return store, store_path


def validate_decision_store(store: dict[str, Any], lot_name: str) -> None:
    if str(store.get("lot_name") or "") != lot_name:
        raise ValueError(f"decision store lot_name mismatch: expected {lot_name}")
    if not isinstance(store.get("decisions"), list):
        raise ValueError("decision store decisions must be a list")
    seen: set[str] = set()
    for item in store.get("decisions") or []:
        validate_decision(item)
        decision_id = str(item.get("decision_id") or "")
        if decision_id in seen:
            raise ValueError(f"duplicate decision_id: {decision_id}")
        seen.add(decision_id)


def validate_decision(item: dict[str, Any]) -> None:
    for field in ["decision_id", "resolution_id", "subject_id", "decision", "reviewer", "decided_at", "verification_method", "status"]:
        if not str(item.get(field) or "").strip():
            raise ValueError(f"decision missing required field: {field}")
    if item.get("decision") not in DECISIONS:
        raise ValueError(f"invalid decision: {item.get('decision')}")
    if item.get("verification_method") not in VERIFICATION_METHODS:
        raise ValueError(f"invalid verification_method: {item.get('verification_method')}")
    if item.get("status") not in DECISION_STATUSES:
        raise ValueError(f"invalid decision status: {item.get('status')}")
    if not isinstance(item.get("field_updates") or {}, dict):
        raise ValueError("decision field_updates must be an object")
    if not isinstance(item.get("source_refs") or [], list):
        raise ValueError("decision source_refs must be a list")
    if item.get("decision") == "reject" and not str(item.get("reason") or "").strip():
        raise ValueError("reject decision requires a non-empty reason")
    if item.get("decision") == "needs_more_material":
        requested = (item.get("field_updates") or {}).get("requested_materials")
        if not str(item.get("reason") or "").strip() or not isinstance(requested, list) or not requested:
            raise ValueError("needs_more_material decision requires reason and field_updates.requested_materials")


def merge_decision_input(
    lot_name: str,
    input_payload: dict[str, Any] | list[dict[str, Any]],
    resolution_queue: dict[str, Any],
    *,
    store_path: str | Path | None = None,
    dry_run: bool = False,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(store_path) if store_path is not None else evidence_decision_store_path(lot_name)
    if dry_run:
        store = load_json(path) if path.exists() else empty_decision_store(lot_name)
        validate_decision_store(store, lot_name)
    else:
        store, path = ensure_decision_store(lot_name, path)
    store = deepcopy(store)
    if not isinstance(input_payload, dict):
        raise ValueError("decision input must be an object with schema_version, lot_name and decisions")
    if str(input_payload.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError(f"unsupported decision input schema_version: {input_payload.get('schema_version')}")
    if str(input_payload.get("lot_name") or "") != lot_name:
        raise ValueError(f"decision input lot_name mismatch: expected {lot_name}")
    incoming = list(input_payload.get("decisions") or [])
    if not incoming:
        raise ValueError("decision input contains no decisions")
    cases = {str(item.get("resolution_id") or ""): item for item in list(resolution_queue.get("resolution_cases") or [])}
    stored_by_id = {str(item.get("decision_id") or ""): item for item in list(store.get("decisions") or [])}
    applied_ids: list[str] = []
    superseded_ids: list[str] = list(store.get("superseded_decision_ids") or [])
    previews: list[dict[str, Any]] = []
    catalog_ids = {
        str(item.get("evidence_id") or "")
        for item in list((catalog or {}).get("evidence_items") or [])
    }

    for raw in incoming:
        item = deepcopy(raw)
        item.setdefault("status", "active")
        item.setdefault("field_updates", {})
        item.setdefault("reason", "")
        item.setdefault("source_refs", [])
        item.setdefault("applicable_scope", {})
        item.setdefault("supersedes_decision_id", None)
        validate_decision(item)
        resolution_id = str(item.get("resolution_id") or "")
        case = cases.get(resolution_id)
        if not case:
            raise ValueError(f"resolution_id not found in current queue: {resolution_id}")
        if str(item.get("subject_id") or "") != str(case.get("subject_id") or ""):
            raise ValueError(f"subject_id mismatch for resolution_id: {resolution_id}")
        scoped_evidence_ids = decision_evidence_scope(item, case)
        unknown_case_evidence = [evidence_id for evidence_id in scoped_evidence_ids if catalog is not None and evidence_id not in catalog_ids]
        if unknown_case_evidence:
            raise ValueError(f"decision references unknown evidence_id: {'、'.join(unknown_case_evidence)}")
        decision_id = str(item.get("decision_id") or "")
        if decision_id in stored_by_id:
            raise ValueError(f"decision_id already exists: {decision_id}")
        supersedes = str(item.get("supersedes_decision_id") or "")
        if supersedes:
            previous = stored_by_id.get(supersedes)
            if not previous:
                raise ValueError(f"superseded decision not found: {supersedes}")
            if previous.get("status") != "active":
                raise ValueError(f"superseded decision is not active: {supersedes}")
            previous["status"] = "superseded"
            superseded_ids.append(supersedes)
        else:
            active_same_case = [
                existing
                for existing in stored_by_id.values()
                if existing.get("status") == "active" and existing.get("resolution_id") == resolution_id
            ]
            if active_same_case:
                raise ValueError(
                    f"active decision already exists for {resolution_id}; provide supersedes_decision_id"
                )
        store["decisions"].append(item)
        stored_by_id[decision_id] = item
        applied_ids.append(decision_id)
        previews.append(preview_decision_change(item, case, catalog))

    store["superseded_decision_ids"] = unique(superseded_ids)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    validate_decision_store(store, lot_name)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "lot_name": lot_name,
        "decision_store_path": str(path),
        "dry_run": dry_run,
        "validation_passed": True,
        "applied_decision_ids": [] if dry_run else applied_ids,
        "would_apply_decision_ids": applied_ids if dry_run else [],
        "superseded_decision_ids": store["superseded_decision_ids"],
        "active_decision_count": len(active_decisions(store)),
        "predicted_changes": previews,
        "minimum_field_check_passed": all(
            not item.get("missing_task_fields")
            and all(not fields for fields in (item.get("evidence_confirmation_missing_fields") or {}).values())
            for item in previews
            if item.get("predicted_resolution_status") in {"resolved", "partially_resolved"}
        ),
        "decision_store_written": not dry_run,
    }


def decision_evidence_scope(decision: dict[str, Any], case: dict[str, Any]) -> list[str]:
    scope = decision.get("applicable_scope") if isinstance(decision.get("applicable_scope"), dict) else {}
    scoped = list(scope.get("evidence_ids") or [])
    case_ids = list(case.get("related_evidence_ids") or [])
    if scoped:
        outside = [item for item in scoped if item not in case_ids]
        if outside:
            raise ValueError(f"applicable_scope evidence_id is outside resolution case: {'、'.join(outside)}")
    return unique(scoped or case_ids)


def preview_decision_change(
    decision: dict[str, Any],
    case: dict[str, Any],
    catalog: dict[str, Any] | None,
) -> dict[str, Any]:
    updates = decision.get("field_updates") or {}
    task_missing = []
    if decision.get("decision") in {"confirm", "partially_confirm"}:
        required_fields = unique(
            list(case.get("required_fields_for_confirmation") or [])
            + list(decision.get("manual_required_fields") or [])
        )
        task_missing = [
            field
            for field in required_fields
            if not case_field_available(field, case, updates)
        ]
        for field in list(decision.get("manual_fields_must_be_entered") or []):
            if not manually_entered_field_present(
                field,
                updates,
                decision_evidence_scope(decision, case),
            ):
                task_missing.append(field)
        task_missing = unique(task_missing)
    evidence_missing: dict[str, list[str]] = {}
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in list((catalog or {}).get("evidence_items") or [])
    }
    for evidence_id in decision_evidence_scope(decision, case):
        evidence = evidence_by_id.get(evidence_id)
        if not evidence or decision.get("decision") not in {"confirm", "partially_confirm"}:
            continue
        candidate = deepcopy(evidence)
        apply_field_updates(candidate, decision)
        evidence_missing[evidence_id] = evidence_minimum_missing_fields(candidate)
    predicted_status = {
        "confirm": "resolved" if not task_missing else "partially_resolved",
        "partially_confirm": "partially_resolved",
        "reject": "rejected",
        "needs_more_material": "needs_more_material",
        "not_applicable": "resolved",
        "defer": "deferred",
    }.get(str(decision.get("decision") or ""), "pending")
    return {
        "decision_id": decision.get("decision_id"),
        "resolution_id": case.get("resolution_id"),
        "current_status": case.get("current_status"),
        "predicted_resolution_status": predicted_status,
        "related_evidence_ids": decision_evidence_scope(decision, case),
        "related_support_relation_count": len(list(case.get("related_support_relation_ids") or [])),
        "missing_task_fields": task_missing,
        "evidence_confirmation_missing_fields": evidence_missing,
        "could_produce_confirmed_evidence": bool(
            decision.get("decision") == "confirm"
            and not task_missing
            and evidence_missing
            and all(not fields for fields in evidence_missing.values())
        ),
    }


def case_field_available(field: str, case: dict[str, Any], updates: dict[str, Any]) -> bool:
    if "或" in field:
        return any(case_field_available(item, case, updates) for item in field.split("或"))
    if required_field_present(field, updates):
        return True
    aliases = {
        "certificate_name": ["title", "certificate_name"],
        "certificate_number": ["related_certificate", "certificate_number"],
        "holder": ["related_enterprise", "holder"],
        "person_name": ["related_person", "person_name"],
        "valid_until": ["valid_until"],
        "validity_status": ["validity_status"],
        "department": ["department"],
        "department_confirm_status": ["department_confirm_status"],
        "availability_status": ["availability_status"],
        "intended_role": ["intended_role"],
    }
    for facts in list(case.get("current_facts") or []):
        structured = facts.get("structured_facts") if isinstance(facts.get("structured_facts"), dict) else {}
        for key in aliases.get(field, [field]):
            if facts.get(key) not in (None, "", []) or structured.get(key) not in (None, "", []):
                return True
    return False


def manually_entered_field_present(field: str, updates: dict[str, Any], evidence_ids: list[str]) -> bool:
    if required_field_present(field, updates):
        return True
    per_evidence = updates.get("evidence_updates")
    if not isinstance(per_evidence, dict) or not evidence_ids:
        return False
    return all(
        isinstance(per_evidence.get(evidence_id), dict)
        and required_field_present(field, per_evidence[evidence_id])
        for evidence_id in evidence_ids
    )


def active_decisions(store: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in list(store.get("decisions") or []) if item.get("status") == "active"]


def initialize_verification_fields(catalog: dict[str, Any], relations: list[dict[str, Any]]) -> None:
    for evidence in list(catalog.get("evidence_items") or []):
        evidence.setdefault("verification_status", "unverified")
        evidence.setdefault("decision_ref_ids", [])
        evidence.setdefault("verification_method", None)
        evidence.setdefault("manually_verified_fields", {})
        evidence.setdefault("visual_source_ref", None)
        evidence.setdefault("verification_missing_fields", [])
    for relation in relations:
        relation.setdefault("verification_status", "system_verified" if relation.get("usability_status") == "confirmed" else "unverified")
        relation.setdefault("decision_ref_ids", [])
        relation.setdefault("verification_method", None)


def apply_resolution_decisions(
    catalog: dict[str, Any],
    requirements: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    resolution_queue: dict[str, Any],
    decision_store: dict[str, Any],
) -> list[str]:
    initialize_verification_fields(catalog, relations)
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    relation_by_id = {str(item.get("support_id") or ""): item for item in relations}
    cases = {str(item.get("resolution_id") or ""): item for item in list(resolution_queue.get("resolution_cases") or [])}
    warnings: list[str] = []

    for decision in active_decisions(decision_store):
        decision_id = str(decision.get("decision_id") or "")
        case = cases.get(str(decision.get("resolution_id") or ""))
        if not case:
            warnings.append(f"人工决定 {decision_id} 引用的 resolution_id 已不存在，未应用。")
            continue
        try:
            evidence_ids = decision_evidence_scope(decision, case)
        except ValueError as exc:
            warnings.append(f"人工决定 {decision_id} 的适用证据范围无效：{exc}，未应用。")
            continue
        missing_evidence_ids = [item for item in evidence_ids if item not in evidence_by_id]
        if missing_evidence_ids:
            warnings.append(f"人工决定 {decision_id} 引用不存在的 evidence_id：{'、'.join(missing_evidence_ids)}，未完整应用。")
        valid_evidence = [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
        scoped_relation_ids = decision_relation_scope(decision, case)
        valid_relations = [
            relation_by_id[item]
            for item in scoped_relation_ids
            if item in relation_by_id and relation_by_id[item].get("evidence_id") in evidence_ids
        ]

        if decision.get("decision") in {"confirm", "partially_confirm"}:
            for evidence in valid_evidence:
                apply_field_updates(evidence, decision)
                evidence["decision_ref_ids"] = unique(list(evidence.get("decision_ref_ids") or []) + [decision_id])
                evidence["verification_method"] = decision.get("verification_method")
                missing = evidence_minimum_missing_fields(evidence)
                evidence["verification_missing_fields"] = missing
                if decision.get("decision") == "confirm" and not missing:
                    evidence["verification_status"] = "human_confirmed"
                    evidence["human_review_required"] = False
                else:
                    evidence["verification_status"] = "unverified"
                    if missing:
                        warnings.append(
                            f"人工决定 {decision_id} 未满足证据 {evidence.get('evidence_id')} 的最低确认字段：{'、'.join(missing)}。"
                        )
            for relation in valid_relations:
                apply_confirm_to_relation(relation, evidence_by_id.get(str(relation.get("evidence_id") or "")), decision)
        elif decision.get("decision") == "reject":
            reject_reason = str(decision.get("reason") or "人工复核驳回该证据或支撑关系。")
            if case.get("resolution_subject_type") == "support_applicability":
                reject_relations(valid_relations, decision_id, reject_reason, decision.get("verification_method"))
            else:
                for evidence in valid_evidence:
                    evidence["verification_status"] = "rejected"
                    evidence["decision_ref_ids"] = unique(list(evidence.get("decision_ref_ids") or []) + [decision_id])
                    evidence["verification_method"] = decision.get("verification_method")
                reject_relations(
                    [item for item in relations if item.get("evidence_id") in evidence_ids],
                    decision_id,
                    reject_reason,
                    decision.get("verification_method"),
                )
        elif decision.get("decision") == "not_applicable":
            reject_relations(valid_relations, decision_id, str(decision.get("reason") or "人工确认该支撑关系不适用。"), decision.get("verification_method"))
        else:
            for evidence in valid_evidence:
                evidence["decision_ref_ids"] = unique(list(evidence.get("decision_ref_ids") or []) + [decision_id])
                evidence["verification_method"] = decision.get("verification_method")

    return unique(warnings)


def decision_relation_scope(decision: dict[str, Any], case: dict[str, Any]) -> list[str]:
    scope = decision.get("applicable_scope") if isinstance(decision.get("applicable_scope"), dict) else {}
    scoped = list(scope.get("support_relation_ids") or [])
    return unique(scoped or list(case.get("related_support_relation_ids") or []))


def apply_field_updates(evidence: dict[str, Any], decision: dict[str, Any]) -> None:
    all_updates = deepcopy(decision.get("field_updates") or {})
    per_evidence = all_updates.pop("evidence_updates", {})
    updates = {
        **all_updates,
        **(
            per_evidence.get(str(evidence.get("evidence_id") or ""), {})
            if isinstance(per_evidence, dict)
            and isinstance(per_evidence.get(str(evidence.get("evidence_id") or ""), {}), dict)
            else {}
        ),
    }
    facts = evidence.setdefault("structured_facts", {})
    direct_map = {
        "certificate_name": "title",
        "holder": "related_enterprise",
        "enterprise_name": "related_enterprise",
        "certificate_number": "related_certificate",
        "person_name": "related_person",
        "project_name": "related_project",
        "valid_until": "valid_until",
    }
    for key, value in updates.items():
        if key in direct_map and value not in {None, ""}:
            evidence[direct_map[key]] = value
        facts[key] = value
    if decision.get("verification_method") == "human_visual_review":
        visual_ref = updates.get("visual_source_ref") or first_source_ref(decision.get("source_refs") or [])
        evidence["visual_source_ref"] = visual_ref
        evidence["manually_verified_fields"] = {
            key: value for key, value in updates.items() if key not in {"visual_source_ref", "unreadable_fields"}
        }
        evidence["unreadable_fields"] = list(updates.get("unreadable_fields") or [])
    else:
        evidence["manually_verified_fields"] = updates


def first_source_ref(source_refs: list[Any]) -> Any:
    return deepcopy(source_refs[0]) if source_refs else None


def evidence_minimum_missing_fields(evidence: dict[str, Any]) -> list[str]:
    evidence_type = str(evidence.get("evidence_type") or "")
    facts = evidence.get("structured_facts") if isinstance(evidence.get("structured_facts"), dict) else {}
    missing: list[str] = []

    def require(value: Any, label: str) -> None:
        if value is None or value == "" or value == []:
            missing.append(label)

    require(evidence.get("source_file_id"), "source_file_id")
    if evidence_type in {"management_system_certificate", "enterprise_qualification_certificate"}:
        require(facts.get("certificate_name") or evidence.get("title"), "证书名称")
        require(facts.get("holder") or evidence.get("related_enterprise"), "持证主体")
        require(facts.get("certificate_number") or evidence.get("related_certificate"), "证书编号")
        require(evidence.get("valid_until") or facts.get("validity_status"), "有效期或有效状态")
    elif evidence_type == "personnel_certificate":
        require(facts.get("certificate_name") or evidence.get("title") if evidence.get("title") != "待识别人员证书" else None, "证书名称")
        require(facts.get("holder") or facts.get("person_name") or evidence.get("related_person"), "持证人")
        require(facts.get("certificate_number") or evidence.get("related_certificate") or facts.get("certificate_status"), "证书编号或状态")
        if not evidence.get("excerpt"):
            require(evidence.get("visual_source_ref"), "visual_source_ref")
    elif evidence_type == "personnel_resume":
        require(facts.get("person_name") or evidence.get("related_person"), "人员姓名")
        require(facts.get("department") if facts.get("department_confirm_status") in {"manual_confirmed", "human_confirmed"} else None, "人工确认部门")
        require(facts.get("intended_role"), "拟任角色或用途")
        require(facts.get("availability_status") if facts.get("availability_status") == "confirmed" else None, "可投入性确认")
        require(facts.get("projects") or facts.get("skills") or facts.get("certificates"), "相关经历或证书")
    elif evidence_type in {"company_performance_summary"}:
        missing.extend(["完整企业业绩证据包", "合同或中标证明", "验收或用户证明"])
    elif evidence_type in {"contract", "award_notice", "acceptance_certificate", "customer_certificate"}:
        require(evidence.get("related_project"), "独立项目身份")
        require(facts.get("customer") or facts.get("client"), "客户")
        require(facts.get("project_time") or facts.get("contract_date"), "项目时间")
        require(facts.get("project_content") or evidence.get("excerpt"), "项目内容")
    elif evidence_type == "patent":
        require(facts.get("patent_name") or evidence.get("title"), "专利名称")
        require(facts.get("patent_number") or evidence.get("related_certificate"), "专利编号")
        require(facts.get("rights_holder") or evidence.get("related_enterprise"), "权利人")
        require(facts.get("authorization_status"), "授权状态")
    else:
        require(evidence.get("excerpt") or evidence.get("visual_source_ref"), "可定位摘录或视觉来源")
    return unique(missing)


def apply_confirm_to_relation(relation: dict[str, Any], evidence: dict[str, Any] | None, decision: dict[str, Any]) -> None:
    decision_id = str(decision.get("decision_id") or "")
    relation["decision_ref_ids"] = unique(list(relation.get("decision_ref_ids") or []) + [decision_id])
    relation["verification_method"] = decision.get("verification_method")
    if not evidence or evidence.get("verification_status") != "human_confirmed":
        relation["verification_status"] = "unverified"
        if relation.get("usability_status") == "candidate":
            relation["usability_status"] = "needs_review"
        return
    if relation.get("usability_status") == "unusable" or relation.get("support_strength") not in {"strong", "medium"}:
        return
    relation["verification_status"] = "human_confirmed"
    relation["usability_status"] = "confirmed"
    relation["quantity_eligible"] = True
    relation["can_be_used_in_writing"] = True
    relation["human_review_required"] = False
    relation["human_review_questions"] = []
    relation["rejection_reasons"] = []
    relation["permitted_claims"] = [f"可引用人工核验材料“{evidence.get('title')}”中已确认的字段。"]


def reject_relations(
    relations: list[dict[str, Any]],
    decision_id: str,
    reason: str,
    method: str | None,
) -> None:
    for relation in relations:
        relation["usability_status"] = "unusable"
        relation["verification_status"] = "rejected"
        relation["decision_ref_ids"] = unique(list(relation.get("decision_ref_ids") or []) + [decision_id])
        relation["verification_method"] = method
        relation["quantity_eligible"] = False
        relation["can_be_used_in_writing"] = False
        relation["human_review_required"] = False
        relation["human_review_questions"] = []
        relation["rejection_reasons"] = unique(list(relation.get("rejection_reasons") or []) + [reason])
        relation["permitted_claims"] = []


def build_resolution_queue(
    lot_name: str,
    matrix: dict[str, Any],
    catalog: dict[str, Any],
    decision_store: dict[str, Any],
    decision_store_path: str | Path,
) -> dict[str, Any]:
    requirements = {str(item.get("evidence_requirement_id") or ""): item for item in list(matrix.get("evidence_requirements") or [])}
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in list(catalog.get("evidence_items") or [])}
    relations = list(matrix.get("support_relations") or [])
    relations_by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in relations:
        relations_by_evidence[str(relation.get("evidence_id") or "")].append(relation)

    seeds: list[dict[str, Any]] = []
    for review in list(matrix.get("global_manual_review_items") or []):
        evidence_id = str(review.get("evidence_id") or "")
        evidence = evidence_by_id.get(evidence_id, {})
        impacted = relations_by_evidence.get(evidence_id, [])
        for subject_type in resolution_subject_types(review, evidence):
            subject_id = resolution_subject_id(subject_type, evidence, review)
            canonical = canonical_resolution_question(subject_type, evidence, review)
            seeds.append(
                {
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "canonical_question": canonical,
                    "evidence_ids": [evidence_id] if evidence_id else [],
                    "requirement_ids": unique([review.get("evidence_requirement_id")] + [item.get("evidence_requirement_id") for item in impacted]),
                    "relation_ids": unique([item.get("support_id") for item in impacted]),
                    "score_ids": unique(list(review.get("score_item_ids") or []) + [score_id for item in impacted for score_id in list(item.get("matched_score_item_ids") or [])]),
                    "action_ids": unique([action_id for item in impacted for action_id in list(item.get("matched_action_group_ids") or [])]),
                    "source_requirement_ids": unique(list(review.get("requirement_ids") or []) + [req_id for item in impacted for req_id in list(item.get("matched_requirement_ids") or [])]),
                    "chapter_ids": unique([chapter_id for item in impacted for chapter_id in list(requirements.get(str(item.get("evidence_requirement_id") or ""), {}).get("related_context_chapter_ids") or [])]),
                    "notes": [str(review.get("question") or "")],
                }
            )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for seed in seeds:
        grouped[(seed["subject_type"], seed["subject_id"], seed["canonical_question"])].append(seed)

    active_by_resolution: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in active_decisions(decision_store):
        active_by_resolution[str(decision.get("resolution_id") or "")].append(decision)

    cases: list[dict[str, Any]] = []
    for (subject_type, subject_id, canonical), group in grouped.items():
        resolution_id = stable_id("resolution", f"{subject_type}|{subject_id}|{canonical}")
        evidence_ids = unique([item for seed in group for item in seed["evidence_ids"]])
        impacted_relations = unique([item for seed in group for item in seed["relation_ids"]])
        decisions = active_by_resolution.get(resolution_id, [])
        case = {
            "resolution_id": resolution_id,
            "resolution_subject_type": subject_type,
            "subject_id": subject_id,
            "canonical_question": canonical,
            "current_status": resolution_case_status(decisions, subject_type, evidence_ids, evidence_by_id),
            "current_facts": [evidence_current_facts(evidence_by_id[item]) for item in evidence_ids if item in evidence_by_id],
            "source_refs": unique_dicts([evidence_source_ref(evidence_by_id[item]) for item in evidence_ids if item in evidence_by_id]),
            "related_evidence_ids": evidence_ids,
            "related_evidence_requirement_ids": unique([item for seed in group for item in seed["requirement_ids"]]),
            "related_support_relation_ids": impacted_relations,
            "related_score_item_ids": unique([item for seed in group for item in seed["score_ids"]]),
            "related_action_group_ids": unique([item for seed in group for item in seed["action_ids"]]),
            "related_requirement_ids": unique([item for seed in group for item in seed["source_requirement_ids"]]),
            "related_context_chapter_ids": unique([item for seed in group for item in seed["chapter_ids"]]),
            "affected_claims": affected_claims(impacted_relations, relations),
            "allowed_decisions": ["confirm", "reject", "needs_more_material", "not_applicable", "defer", "partially_confirm"],
            "required_fields_for_confirmation": required_fields_for_subject(subject_type),
            "suggested_reviewer_role": reviewer_role(subject_type),
            "priority": resolution_priority(subject_type, group),
            "blocking_level": blocking_level(subject_type, group),
            "decision_refs": [item.get("decision_id") for item in decisions],
            "human_review_notes": unique([item for seed in group for item in seed["notes"]]),
        }
        validate_resolution_case(case)
        cases.append(case)

    known_resolution_ids = {str(item.get("resolution_id") or "") for item in cases}
    for decision in active_decisions(decision_store):
        if str(decision.get("resolution_id") or "") in known_resolution_ids:
            continue
        repair_case = stale_decision_repair_case(decision)
        validate_resolution_case(repair_case)
        cases.append(repair_case)

    cases.sort(key=lambda item: (status_order(item["current_status"]), priority_order(item["priority"]), item["resolution_id"]))
    status_counts = Counter(str(item.get("current_status") or "pending") for item in cases)
    subject_counts = Counter(str(item.get("resolution_subject_type") or "other") for item in cases)
    blocking_counts = Counter(str(item.get("blocking_level") or "informational") for item in cases)
    resolved_refs = [
        {"resolution_id": item["resolution_id"], "current_status": item["current_status"], "decision_refs": item["decision_refs"]}
        for item in cases
        if item["current_status"] in {"resolved", "rejected", "superseded"}
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lot_name": lot_name,
        "generated_from": str(Path("outputs") / safe_path_name(lot_name) / "evidence" / "evidence_coverage_matrix.json"),
        "decision_store_path": str(decision_store_path),
        "resolution_cases": cases,
        "resolved_case_refs": resolved_refs,
        "unresolved_conflicts": list(catalog.get("unresolved_identity_conflicts") or []),
        "statistics": {
            "raw_manual_review_count": len(list(matrix.get("global_manual_review_items") or [])),
            "resolution_case_count": len(cases),
            "resolved_case_count": len(resolved_refs),
            "subject_type_counts": dict(sorted(subject_counts.items())),
            "status_counts": dict(sorted(status_counts.items())),
            "blocking_level_counts": dict(sorted(blocking_counts.items())),
            "blocks_confirmed_evidence_count": blocking_counts.get("blocks_confirmed_evidence", 0),
        },
        "build_warnings": [],
    }


def resolution_subject_types(review: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    question = str(review.get("question") or "")
    if "投入" in question and "拟任角色" in question:
        return ["person_availability", "person_role"]
    return [resolution_subject_type(review, evidence)]


def resolution_subject_type(review: dict[str, Any], evidence: dict[str, Any]) -> str:
    question = str(review.get("question") or "")
    category = str(review.get("category") or "")
    evidence_type = str(evidence.get("evidence_type") or "")
    if "mock" in question.lower() or "不得作为" in question or "不可用" in question:
        return "support_applicability"
    if "部门" in question or category == "personnel_department":
        return "person_department"
    if "投入" in question or category == "personnel_availability":
        return "person_availability"
    if "拟任角色" in question:
        return "person_role"
    if "OCR" in question or "人工识别证书图片" in question or (evidence_type == "personnel_certificate" and not evidence.get("excerpt")):
        return "evidence_document"
    if "有效期" in question or category == "evidence_validity":
        return "certificate_validity"
    if "持证主体" in question or "证书" in question and "主体" in question:
        return "certificate_identity"
    if category == "package_completeness" or "证据包" in question:
        return "evidence_package"
    if category == "duplicate_evidence" or "同一证据" in question:
        return "evidence_duplicate"
    if "项目名称" in question or "项目身份" in question:
        return "project_identity"
    if "相关" in question or "适用" in question:
        return "project_relevance"
    if "位置" in question or "摘录" in question:
        return "source_location"
    if category == "evidence_identity":
        return "enterprise_identity" if not evidence.get("related_person") else "person_identity"
    return "other"


def stale_decision_repair_case(decision: dict[str, Any]) -> dict[str, Any]:
    decision_id = str(decision.get("decision_id") or "")
    return {
        "resolution_id": stable_id("resolution-repair", decision_id),
        "resolution_subject_type": "other",
        "subject_id": f"decision:{decision_id}",
        "canonical_question": f"修复人工决定 {decision_id} 引用的失效复核对象。",
        "current_status": "pending",
        "current_facts": [{"stale_resolution_id": decision.get("resolution_id"), "subject_id": decision.get("subject_id")}],
        "source_refs": list(decision.get("source_refs") or []),
        "related_evidence_ids": [],
        "related_evidence_requirement_ids": [],
        "related_support_relation_ids": [],
        "related_score_item_ids": [],
        "related_action_group_ids": [],
        "related_requirement_ids": [],
        "related_context_chapter_ids": [],
        "affected_claims": {"currently_allowed": [], "currently_restricted": []},
        "allowed_decisions": ["defer", "not_applicable", "needs_more_material"],
        "required_fields_for_confirmation": ["有效resolution_id", "有效subject_id"],
        "suggested_reviewer_role": "证据数据管理员",
        "priority": "high",
        "blocking_level": "blocks_confirmed_evidence",
        "decision_refs": [decision_id],
        "human_review_notes": ["原人工决定未被静默应用；需将其取代为指向当前复核对象的新决定。"],
    }


def resolution_subject_id(subject_type: str, evidence: dict[str, Any], review: dict[str, Any]) -> str:
    person = normalize(evidence.get("related_person"))
    if subject_type in {"person_identity", "person_department", "person_availability", "person_role"} and person:
        return f"person:{person}"
    if subject_type == "evidence_document" and evidence.get("evidence_type") == "personnel_certificate":
        return f"visual-certificates:{evidence.get('source_file_id')}:{person or 'unknown'}"
    if subject_type in {"certificate_identity", "certificate_validity"}:
        return f"certificate:{evidence.get('evidence_id') or review.get('evidence_id')}"
    if subject_type in {"project_identity", "project_relevance"}:
        return f"project:{normalize(evidence.get('related_project')) or evidence.get('evidence_id')}"
    if subject_type == "evidence_package":
        return f"package:{normalize(evidence.get('related_project')) or review.get('evidence_id')}"
    return f"evidence:{evidence.get('evidence_id') or review.get('evidence_id') or review.get('manual_review_id')}"


def canonical_resolution_question(subject_type: str, evidence: dict[str, Any], review: dict[str, Any]) -> str:
    person = str(evidence.get("related_person") or "该人员")
    title = str(evidence.get("title") or "该材料")
    templates = {
        "person_department": f"确认{person}当前所属部门。",
        "person_availability": f"确认{person}是否可投入本项目。",
        "person_role": f"确认{person}在本项目中的拟任角色或用途。",
        "evidence_document": f"人工查看{person}的证书图片，记录可辨认字段和无法辨认字段。",
        "certificate_identity": f"确认“{title}”的证书名称、编号和持证主体。",
        "certificate_validity": f"确认“{title}”的有效期或当前有效状态。",
        "project_identity": f"确认“{title}”对应的独立项目身份。",
        "project_relevance": f"确认“{title}”与当前评分或证明要求的相关性。",
        "evidence_package": f"确认“{title}”证据包的组成和完整性。",
        "evidence_duplicate": f"确认“{title}”是否与其他记录属于同一证据。",
        "source_location": f"确认“{title}”的可追溯位置或视觉来源。",
        "enterprise_identity": f"确认“{title}”所属企业主体。",
        "person_identity": f"确认“{title}”所属人员身份。",
    }
    return templates.get(subject_type, str(review.get("question") or f"复核“{title}”。"))


def required_fields_for_subject(subject_type: str) -> list[str]:
    return {
        "person_department": ["department", "department_confirm_status"],
        "person_availability": ["availability_status"],
        "person_role": ["intended_role"],
        "evidence_document": ["visual_source_ref", "certificate_name", "person_name", "unreadable_fields"],
        "certificate_identity": ["certificate_name", "certificate_number", "holder"],
        "certificate_validity": ["valid_until或validity_status"],
        "project_identity": ["project_name", "customer", "project_time"],
        "project_relevance": ["relevance_basis"],
        "evidence_package": ["member_evidence_ids", "package_completeness"],
        "evidence_duplicate": ["duplicate_status"],
        "source_location": ["source_location或visual_source_ref"],
    }.get(subject_type, ["review_result"])


def resolution_case_status(
    decisions: list[dict[str, Any]],
    subject_type: str,
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
) -> str:
    if not decisions:
        return "pending"
    latest = sorted(decisions, key=lambda item: str(item.get("decided_at") or ""))[-1]
    decision = latest.get("decision")
    if decision == "reject":
        return "rejected"
    if decision == "needs_more_material":
        return "needs_more_material"
    if decision == "defer":
        return "deferred"
    if decision == "not_applicable":
        return "resolved"
    if decision == "partially_confirm":
        return "partially_resolved"
    if decision == "confirm":
        required = required_fields_for_subject(subject_type)
        updates = latest.get("field_updates") or {}
        if all(required_field_present(field, updates) for field in required):
            return "resolved"
        if evidence_ids and all(evidence_by_id.get(item, {}).get("verification_status") == "human_confirmed" for item in evidence_ids):
            return "resolved"
        return "partially_resolved"
    return "pending"


def required_field_present(field: str, updates: dict[str, Any]) -> bool:
    if "或" in field:
        return any(required_field_present(item, updates) for item in field.split("或"))
    value = updates.get(field)
    return value is not None and value != "" and value != []


def evidence_current_facts(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence.get("evidence_id"),
        "evidence_type": evidence.get("evidence_type"),
        "title": evidence.get("title"),
        "related_enterprise": evidence.get("related_enterprise"),
        "related_person": evidence.get("related_person"),
        "related_project": evidence.get("related_project"),
        "related_certificate": evidence.get("related_certificate"),
        "valid_until": evidence.get("valid_until"),
        "verification_status": evidence.get("verification_status") or "unverified",
        "structured_facts": evidence.get("structured_facts") or {},
    }


def evidence_source_ref(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence.get("evidence_id"),
        "source_file_id": evidence.get("source_file_id"),
        "source_file_name": evidence.get("source_file_name"),
        "source_location": evidence.get("source_location"),
        "location_precision": evidence.get("location_precision"),
        "visual_source_ref": evidence.get("visual_source_ref"),
    }


def affected_claims(relation_ids: list[str], relations: list[dict[str, Any]]) -> dict[str, list[str]]:
    selected = [item for item in relations if item.get("support_id") in relation_ids]
    return {
        "currently_allowed": unique([claim for item in selected for claim in list(item.get("permitted_claims") or [])]),
        "currently_restricted": unique([claim for item in selected for claim in list(item.get("restricted_claims") or [])]),
    }


def reviewer_role(subject_type: str) -> str:
    if subject_type.startswith("person_"):
        return "人力资源或项目管理部门"
    if subject_type.startswith("certificate_") or subject_type == "enterprise_identity":
        return "企业资质或综合管理部门"
    if subject_type.startswith("project_") or subject_type == "evidence_package":
        return "销售、合同或项目交付部门"
    if subject_type == "evidence_document":
        return "材料管理员或证书持有人"
    return "业务复核人员"


def resolution_priority(subject_type: str, group: list[dict[str, Any]]) -> str:
    if subject_type in {"certificate_identity", "certificate_validity", "evidence_package", "person_availability"}:
        return "high"
    if any(seed["score_ids"] for seed in group):
        return "high"
    return "medium"


def blocking_level(subject_type: str, group: list[dict[str, Any]]) -> str:
    if subject_type in {"certificate_identity", "certificate_validity", "person_department", "person_availability", "person_role", "evidence_document", "source_location"}:
        return "blocks_confirmed_evidence"
    if subject_type in {"evidence_package", "quantity_independence", "project_relevance", "project_identity"}:
        return "blocks_full_score_assessment"
    if any(seed["source_requirement_ids"] for seed in group):
        return "blocks_mandatory_response"
    return "does_not_block_writing"


def validate_resolution_case(case: dict[str, Any]) -> None:
    if case.get("resolution_subject_type") not in RESOLUTION_SUBJECT_TYPES:
        raise ValueError(f"invalid resolution_subject_type: {case.get('resolution_id')}")
    if case.get("current_status") not in RESOLUTION_STATUSES:
        raise ValueError(f"invalid resolution status: {case.get('resolution_id')}")
    if case.get("blocking_level") not in BLOCKING_LEVELS:
        raise ValueError(f"invalid blocking_level: {case.get('resolution_id')}")


def build_material_intake_plan(lot_name: str, matrix: dict[str, Any]) -> dict[str, Any]:
    requirements = {str(item.get("evidence_requirement_id") or ""): item for item in list(matrix.get("evidence_requirements") or [])}
    coverage = {str(item.get("evidence_requirement_id") or ""): item for item in list(matrix.get("requirement_coverage") or [])}
    missing = list(matrix.get("global_missing_evidence") or [])
    score_coverage = {
        str(item.get("score_item_id") or ""): item
        for item in list(matrix.get("score_item_coverage") or [])
    }
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in missing:
        req_id = str((request.get("related_evidence_requirement_ids") or [request.get("evidence_requirement_id") or ""])[0] or "")
        req = requirements.get(req_id, {})
        key = intake_group_key(req, request)
        groups[key].append({"request": request, "requirement": req, "coverage": coverage.get(req_id, {})})

    packages: list[dict[str, Any]] = []
    for key, rows in groups.items():
        reqs = [item["requirement"] for item in rows]
        covers = [item["coverage"] for item in rows]
        requests = [item["request"] for item in rows]
        subjects = unique([subject for item in reqs for subject in list(item.get("expected_evidence_subject_types") or [])])
        score_ids = unique([score_id for item in reqs for score_id in list(item.get("source_score_item_ids") or [])])
        req_ids = unique([item.get("evidence_requirement_id") for item in reqs])
        linked_score_coverage = [score_coverage[item] for item in score_ids if item in score_coverage]
        count_sources = linked_score_coverage or covers
        required_counts = [item.get("required_count") for item in count_sources if item.get("required_count") is not None]
        confirmed = max([int(item.get("confirmed_count") or 0) for item in count_sources] or [0])
        candidate = max([int(item.get("candidate_count") or 0) + int(item.get("needs_review_count") or 0) for item in count_sources] or [0])
        requested_count = max(required_counts) if required_counts else None
        remaining = max(0, int(requested_count) - confirmed) if requested_count is not None else None
        title = intake_title(key, reqs)
        components = intake_components(key, reqs, requests)
        package = {
            "intake_package_id": stable_id("intake", key),
            "title": title,
            "evidence_subject_type": subjects[0] if len(subjects) == 1 else subjects,
            "related_evidence_requirement_ids": req_ids,
            "related_score_item_ids": score_ids,
            "related_action_group_ids": unique([action_id for item in reqs for action_id in list(item.get("source_action_group_ids") or [])]),
            "related_requirement_ids": unique([source_id for item in reqs for source_id in list(item.get("source_requirement_ids") or [])]),
            "target_owner": intake_owner(subjects),
            "priority": highest_priority([str(item.get("priority") or "medium") for item in reqs]),
            "requested_count": requested_count,
            "confirmed_count": confirmed,
            "candidate_count": candidate,
            "remaining_count": remaining if requested_count is not None else "not_explicit",
            "requested_material_components": components,
            "completeness_criteria": intake_completeness_criteria(key, requested_count, components),
            "accepted_file_types_or_material_types": accepted_material_types(key, subjects),
            "existing_candidate_evidence_ids": unique([evidence_id for item in covers for evidence_id in list(item.get("candidate_evidence_ids") or []) + list(item.get("needs_review_evidence_ids") or [])]),
            "reasons_current_evidence_is_insufficient": unique([gap for item in covers for gap in list(item.get("quality_gaps") or [])] + [str(item.get("reason_needed") or "") for item in requests]),
            "affects_full_score_writing": any(bool(item.get("affects_full_score_writing")) for item in requests),
            "affects_mandatory_response": any(bool(item.get("affects_mandatory_response")) for item in requests),
            "intake_status": intake_status(requested_count, confirmed, candidate),
            "human_review_required": bool(candidate),
            "generated_from": "evidence_coverage_matrix.json",
        }
        if package["intake_status"] not in INTAKE_STATUSES:
            raise ValueError(f"invalid intake_status: {package['intake_package_id']}")
        packages.append(package)

    owner_counts = Counter(str(item.get("target_owner") or "未指定") for item in packages)
    status_counts = Counter(str(item.get("intake_status") or "not_requested") for item in packages)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lot_name": lot_name,
        "generated_from": str(Path("outputs") / safe_path_name(lot_name) / "evidence" / "evidence_coverage_matrix.json"),
        "material_intake_packages": sorted(packages, key=lambda item: (priority_order(item["priority"]), item["intake_package_id"])),
        "statistics": {
            "intake_package_count": len(packages),
            "owner_counts": dict(sorted(owner_counts.items())),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "build_warnings": [],
    }


def intake_group_key(requirement: dict[str, Any], request: dict[str, Any]) -> str:
    title = str(requirement.get("canonical_title") or request.get("title") or "")
    score_ids = set(requirement.get("source_score_item_ids") or [])
    if "业绩情况" in title or "technical_04_业绩情况" in score_ids:
        return "project-performance-package"
    if "专利数量" in title or "technical_08_专利数量" in score_ids:
        return "patent-package"
    if "绿色管理体系认证" in title:
        return "management-system-certificates"
    if "研发团队规模" in title:
        return "research-team-package"
    subjects = unique(list(requirement.get("expected_evidence_subject_types") or []))
    owner = intake_owner(subjects)
    primary = subjects[0] if subjects else "other_enterprise_evidence"
    return f"{normalize(owner)}|{primary}|{normalize(title)}"


def intake_title(key: str, requirements: list[dict[str, Any]]) -> str:
    special = {
        "project-performance-package": "企业业绩完整证据包",
        "patent-package": "采购主题相关专利证据包",
        "management-system-certificates": "四类管理体系认证核验与补录包",
        "research-team-package": "研发团队不少于30人材料包",
    }
    return special.get(key, str(requirements[0].get("canonical_title") or "企业证明材料补录包"))


def intake_components(key: str, requirements: list[dict[str, Any]], requests: list[dict[str, Any]]) -> list[str]:
    if key == "project-performance-package":
        return ["合同关键页", "中标通知书（如有）", "验收证明或用户证明", "项目名称、客户、时间、金额和工作内容", "与技术开发或技术服务要求的相关性说明"]
    if key == "patent-package":
        return ["专利证书", "专利名称和编号", "权利人", "授权状态", "与评分主题相关性说明"]
    if key == "management-system-certificates":
        return ["ISO 9001、ISO 14001候选证书的持证主体、编号、有效状态和适用范围确认", "职业健康安全管理体系证书", "能源管理体系证书", "证书扫描件及有效期页"]
    if key == "research-team-package":
        return ["部门人员名单", "可投入人员名单", "姓名、部门、岗位、职称和证书", "相关项目经验", "可投入确认", "必要简历和证书材料"]
    return unique([component for req in requirements for component in list(req.get("expected_evidence_types") or [])] + [component for request in requests for component in list(request.get("suggested_materials") or [])])


def intake_completeness_criteria(key: str, requested_count: int | None, components: list[str]) -> list[str]:
    if key == "project-performance-package":
        return [f"形成{requested_count or 5}个身份独立的项目证据包", "每个项目至少包含合同及验收/用户证明", "项目主体、时间、内容和相关性可追溯"]
    if key == "patent-package":
        return [f"形成{requested_count or 2}项不重复专利", "权利人、授权状态和采购主题相关性均已确认"]
    if key == "management-system-certificates":
        return ["四类证书分别对应能源、质量、职业健康安全、环境管理体系", "持证主体、编号、有效状态和适用范围已确认"]
    if key == "research-team-package":
        return ["按人员身份去重", "不少于30人满足高级及以上职称或高级技师条件", "部门、岗位、证书和可投入状态可追溯"]
    return [f"材料组件完整：{'、'.join(components)}"] if components else ["满足对应证据需求的关键质量条件"]


def accepted_material_types(key: str, subjects: list[str]) -> list[str]:
    special = {
        "project-performance-package": ["company_contract", "winning_notice", "acceptance_report", "customer_certificate", "performance_case"],
        "patent-package": ["patent_certificate", "patent_record"],
        "management-system-certificates": ["company_qualification", "management_system_certificate"],
        "research-team-package": ["personnel_resume", "personnel_certificate", "organization_structure"],
    }
    return special.get(key, subjects or ["other_enterprise_evidence"])


def intake_owner(subjects: list[str]) -> str:
    values = set(subjects)
    if values & {"personnel_resume", "personnel_qualification", "personnel_certificate", "personnel_project_experience", "organization_structure"}:
        return "人力资源或项目管理部门"
    if values & {"project_performance", "contract", "award_notice", "acceptance_certificate", "customer_certificate"}:
        return "销售、合同或项目交付部门"
    if values & {"financial_evidence", "credit_evidence"}:
        return "财务或法务合规部门"
    return "企业资质或综合管理部门"


def intake_status(required_count: int | None, confirmed: int, candidate: int) -> str:
    if required_count is not None and confirmed >= required_count:
        return "complete"
    if candidate:
        return "received_pending_review"
    return "not_requested"


def build_evidence_review_template(
    lot_name: str,
    output_dir: str | Path = "outputs",
) -> dict[str, Any]:
    evidence_dir = Path(output_dir) / safe_path_name(lot_name) / "evidence"
    queue_path = evidence_dir / "evidence_resolution_queue.json"
    catalog_path = evidence_dir / "enterprise_evidence_catalog.json"
    matrix_path = evidence_dir / "evidence_coverage_matrix.json"
    intake_path = evidence_dir / "material_intake_plan.json"
    queue = load_json(queue_path)
    catalog = load_json(catalog_path)
    matrix = load_json(matrix_path)
    intake = load_json(intake_path)
    store_path = evidence_decision_store_path(lot_name)
    store = load_json(store_path) if store_path.exists() else empty_decision_store(lot_name)
    validate_decision_store(store, lot_name)

    cases = {str(item.get("resolution_id") or ""): item for item in list(queue.get("resolution_cases") or [])}
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in list(catalog.get("evidence_items") or [])
    }
    selected = {
        "iso9001": require_resolution_case(cases, "resolution-7509850d29fa09"),
        "iso14001": require_resolution_case(cases, "resolution-8cdb0fee4236c3"),
        "person_department": require_resolution_case(cases, "resolution-95c3f18793cd85"),
        "person_availability": require_resolution_case(cases, "resolution-bbc452f269aefc"),
        "person_role": require_resolution_case(cases, "resolution-374973f2c9e67c"),
        "visual": require_resolution_case(cases, "resolution-950720b049235c"),
        "mock": require_resolution_case(cases, "resolution-8da737fc06ef39"),
        "performance_package": require_resolution_case(cases, "resolution-148e3a32a56b92"),
    }
    visual_evidence_id = "evidence-16fa90eefdbf61"
    if visual_evidence_id not in selected["visual"].get("related_evidence_ids", []):
        raise ValueError("selected visual evidence is not present in the current resolution case")
    visual_evidence = evidence_by_id.get(visual_evidence_id)
    if not visual_evidence or visual_evidence.get("excerpt"):
        raise ValueError("selected visual evidence must be a real no-text evidence item")

    tasks = [
        review_template_task("review-task-iso9001-identity", "ISO 9001 证书身份确认", selected["iso9001"], "decision-slot-iso9001", ["certificate_name", "certificate_number", "holder"]),
        review_template_task("review-task-iso9001-validity", "ISO 9001 证书有效期确认", selected["iso9001"], "decision-slot-iso9001", ["valid_until", "validity_status"]),
        review_template_task("review-task-iso14001-identity", "ISO 14001 证书身份确认", selected["iso14001"], "decision-slot-iso14001", ["certificate_name", "certificate_number", "holder"]),
        review_template_task("review-task-iso14001-validity", "ISO 14001 证书有效期确认", selected["iso14001"], "decision-slot-iso14001", ["valid_until", "validity_status"]),
        review_template_task("review-task-person-department", "吴云畅部门确认", selected["person_department"], "decision-slot-person-department", ["department", "department_confirm_status"]),
        review_template_task("review-task-person-availability", "吴云畅可投入性确认", selected["person_availability"], "decision-slot-person-availability", ["availability_status"]),
        review_template_task("review-task-person-role", "吴云畅拟任角色确认", selected["person_role"], "decision-slot-person-role", ["intended_role"]),
        review_template_task(
            "review-task-visual-certificate",
            "吴云畅无文本证书图片人工视觉确认",
            selected["visual"],
            "decision-slot-visual-certificate",
            ["visual_source_ref", "certificate_name", "person_name", "certificate_number_or_status", "unreadable_fields"],
            selected_evidence_ids=[visual_evidence_id],
        ),
        review_template_task("review-task-mock-reject", "mock 业绩材料驳回复核", selected["mock"], "decision-slot-mock-reject", ["review_result", "reason"]),
        review_template_task(
            "review-task-performance-package",
            "企业业绩完整证据包补材料确认",
            selected["performance_package"],
            "decision-slot-performance-package",
            ["requested_materials", "reason"],
        ),
    ]

    decisions = [
        decision_template_slot(
            "decision-slot-iso9001",
            selected["iso9001"],
            {
                "certificate_name": None,
                "certificate_number": None,
                "holder": None,
                "valid_until": None,
                "validity_status": None,
                "applicable_scope": None,
            },
            manual_fields=["certificate_name", "certificate_number", "holder", "valid_until或validity_status"],
        ),
        decision_template_slot(
            "decision-slot-iso14001",
            selected["iso14001"],
            {
                "certificate_name": None,
                "certificate_number": None,
                "holder": None,
                "valid_until": None,
                "validity_status": None,
                "applicable_scope": None,
            },
            manual_fields=["certificate_name", "certificate_number", "holder", "valid_until或validity_status"],
        ),
        decision_template_slot(
            "decision-slot-person-department",
            selected["person_department"],
            {"department": None, "department_confirm_status": None},
            manual_fields=["department", "department_confirm_status"],
        ),
        decision_template_slot(
            "decision-slot-person-availability",
            selected["person_availability"],
            {"availability_status": None},
            manual_fields=["availability_status"],
        ),
        decision_template_slot(
            "decision-slot-person-role",
            selected["person_role"],
            {"intended_role": None},
            manual_fields=["intended_role"],
        ),
        decision_template_slot(
            "decision-slot-visual-certificate",
            selected["visual"],
            {
                "evidence_updates": {
                    visual_evidence_id: {
                        "visual_source_ref": {
                            "evidence_id": visual_evidence_id,
                            "source_file_id": visual_evidence.get("source_file_id"),
                            "source_file_name": visual_evidence.get("source_file_name"),
                            "source_location": deepcopy(visual_evidence.get("source_location") or {}),
                        },
                        "certificate_name": None,
                        "person_name": None,
                        "certificate_number": None,
                        "issuer": None,
                        "valid_until": None,
                        "certificate_status": None,
                        "unreadable_fields": [],
                    }
                }
            },
            verification_method="human_visual_review",
            applicable_scope={"evidence_ids": [visual_evidence_id]},
            manual_fields=["certificate_name", "person_name", "certificate_number或certificate_status", "unreadable_fields"],
        ),
        decision_template_slot(
            "decision-slot-mock-reject",
            selected["mock"],
            {"review_result": None},
            suggested_decision="reject",
        ),
        decision_template_slot(
            "decision-slot-performance-package",
            selected["performance_package"],
            {"requested_materials": performance_package_materials(intake)},
            suggested_decision="needs_more_material",
        ),
    ]

    template_path = evidence_dir / "evidence_review_decision_template.json"
    instructions_path = evidence_dir / "evidence_review_instructions.md"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lot_name": lot_name,
        "template_only": True,
        "generated_from": [str(queue_path), str(intake_path), str(catalog_path), str(matrix_path)],
        "decision_store_path": str(store_path),
        "decision_store_active_count_at_generation": len(active_decisions(store)),
        "review_task_count": len(tasks),
        "decision_slot_count": len(decisions),
        "selection_notes": [
            "ISO 9001 和 ISO 14001 在当前队列中各只有一个证书身份 resolution_case；身份与有效期核查共用各自真实 resolution_id 和一个决策槽。",
            "模板中的现有解析值仅是待人工核对线索；所有 field_updates 中的人工事实字段均保持为空。",
            "mock 业绩与完整业绩包仅预填 suggested_decision，导入前仍需人工填写决定、原因、复核人和时间。",
        ],
        "selected_review_tasks": tasks,
        "decisions": decisions,
    }
    template_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    instructions_path.write_text(render_evidence_review_instructions(lot_name, template_path), encoding="utf-8")
    return {
        "lot_name": lot_name,
        "template_path": str(template_path),
        "instructions_path": str(instructions_path),
        "review_task_count": len(tasks),
        "decision_slot_count": len(decisions),
        "resolution_ids": [item["resolution_id"] for item in tasks],
        "decision_store_active_count": len(active_decisions(store)),
        "confirmed_count": int((matrix.get("statistics") or {}).get("confirmed_count") or 0),
    }


def require_resolution_case(cases: dict[str, dict[str, Any]], resolution_id: str) -> dict[str, Any]:
    case = cases.get(resolution_id)
    if not case:
        raise ValueError(f"required real resolution case not found: {resolution_id}")
    return case


def review_template_task(
    task_id: str,
    title: str,
    case: dict[str, Any],
    decision_slot_id: str,
    task_required_fields: list[str],
    *,
    selected_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "review_task_id": task_id,
        "title": title,
        "resolution_id": case.get("resolution_id"),
        "resolution_subject_type": case.get("resolution_subject_type"),
        "subject_id": case.get("subject_id"),
        "related_evidence_ids": deepcopy(case.get("related_evidence_ids") or []),
        "selected_evidence_ids": deepcopy(selected_evidence_ids or case.get("related_evidence_ids") or []),
        "source_refs": deepcopy(case.get("source_refs") or []),
        "current_question": case.get("canonical_question"),
        "required_fields_for_confirmation": deepcopy(case.get("required_fields_for_confirmation") or []),
        "task_required_fields_for_confirmation": task_required_fields,
        "decision_slot_id": decision_slot_id,
        "human_input_required": True,
    }


def decision_template_slot(
    slot_id: str,
    case: dict[str, Any],
    field_updates: dict[str, Any],
    *,
    suggested_decision: str | None = None,
    verification_method: str | None = None,
    applicable_scope: dict[str, Any] | None = None,
    manual_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "template_slot_id": slot_id,
        "decision_id": None,
        "resolution_id": case.get("resolution_id"),
        "subject_id": case.get("subject_id"),
        "decision": suggested_decision,
        "suggested_decision": suggested_decision,
        "field_updates": field_updates,
        "manual_required_fields": manual_fields or [],
        "manual_fields_must_be_entered": manual_fields or [],
        "reason": None,
        "reviewer": None,
        "decided_at": None,
        "source_refs": deepcopy(case.get("source_refs") or []),
        "verification_method": verification_method,
        "applicable_scope": applicable_scope or {},
        "supersedes_decision_id": None,
        "status": "active",
    }


def performance_package_materials(intake: dict[str, Any]) -> list[str]:
    for item in list(intake.get("material_intake_packages") or []):
        if item.get("title") == "企业业绩完整证据包":
            return deepcopy(item.get("requested_material_components") or [])
    return ["合同关键页", "中标通知书（如有）", "验收证明或用户证明", "项目基础信息与相关性说明"]


def render_evidence_review_instructions(lot_name: str, template_path: Path) -> str:
    return f"""# 企业证据人工决策录入说明

> 本文件只用于人工操作说明，不参与证据匹配、状态判断或下游计算。

## 使用边界

- 决策模板：`{template_path}`
- 正式决策存储：`data/manual_reviews/{lot_name}/evidence_resolution_decisions.json`
- 模板包含 10 个核查事项和 8 个决策槽。两张 ISO 证书各自的身份与有效期核查共用一个真实 resolution_id，请在同一决策中一次填写。
- `current_facts`、原摘录或解析值只是核对线索，不代表人工确认结果。不得直接把空字段批量替换为解析值。

## decision 含义

- `confirm`：人工确认指定事实；仍需通过对应证据类型的最低字段门槛，才能成为 confirmed。
- `partially_confirm`：只确认部分字段，未满足最低门槛时保持 candidate 或 needs_review。
- `reject`：证据或支撑关系错误、不适用、过期、重复或主体不一致。必须填写具体 `reason`。
- `needs_more_material`：现有材料不足。必须填写 `reason` 和 `field_updates.requested_materials`。
- `not_applicable`：该复核对象对当前范围不适用。
- `defer`：暂缓处理，不改变证据为 confirmed。

每条决策必须填写：`decision_id`、`decision`、`reason`、`reviewer`、`decided_at`、`verification_method`。`decided_at` 使用带时区的 ISO 8601 时间。更新已有决定时填写 `supersedes_decision_id`，不要删除历史决定。

## 最低确认字段

ISO 管理体系证书：证书名称、证书编号、持证主体、有效期或明确有效状态、适用范围，以及与当前证据需求的对应关系。模板中的有效期解析值必须由人工对照来源后重新填写或明确确认。

人员：姓名、人工确认的部门、可投入状态、拟任角色或用途；相关简历、经历和证书仍需各自来源。部门、可投入性和角色是三个独立决定，不要用一个字段替代另两个事实。

图片证书视觉确认：使用 `verification_method=human_visual_review`，保留 `visual_source_ref`，填写可辨认的证书名称、持证人、编号、发证机构、有效期或状态；无法辨认的字段写入 `unreadable_fields`。不得编造文本 `excerpt`。

## 驳回和补材料

`reject` 的 `reason` 应说明具体原因，例如材料为测试文件、主体错误、证据类型错误、过期、重复或不支撑指定要求。模板对 mock 材料仅给出建议决定，仍须人工核查并填写理由。

`needs_more_material` 应在 `reason` 中说明现有证据缺什么，并在 `field_updates.requested_materials` 中列出可执行的补录组件。企业业绩证据包应按独立项目准备合同关键页、可用的中标通知书、验收或用户证明，以及项目名称、客户、时间、金额、工作内容和相关性说明。

## 校验与导入

先执行只读校验；该命令不写入决策存储，也不重建覆盖矩阵：

```powershell
python cli.py apply-evidence-review-decisions --lot-name {lot_name} --input "{template_path}" --dry-run
```

确认 dry-run 的 schema、ID、枚举、最低字段和 supersede 检查结果后，再去掉 `--dry-run` 正式导入：

```powershell
python cli.py apply-evidence-review-decisions --lot-name {lot_name} --input "{template_path}"
```

正式导入会保存人工决定并重建证据覆盖。随后检查：

```powershell
python cli.py inspect-evidence-resolution-queue --lot-name {lot_name}
python cli.py inspect-evidence-coverage --lot-name {lot_name}
```

Markdown 仅供人工阅读，业务计算仍以正式 JSON 和持久化决策存储为准。
"""


def write_resolution_artifacts(
    evidence_dir: Path,
    queue: dict[str, Any],
    intake_plan: dict[str, Any],
    matrix: dict[str, Any],
    previous_matrix: dict[str, Any] | None = None,
) -> dict[str, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    queue_path = evidence_dir / "evidence_resolution_queue.json"
    intake_path = evidence_dir / "material_intake_plan.json"
    summary_path = evidence_dir / "evidence_resolution_summary.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    intake_path.write_text(json.dumps(intake_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "evidence_resolution_queue.md").write_text(render_resolution_queue_markdown(queue), encoding="utf-8")
    (evidence_dir / "material_intake_plan.md").write_text(render_material_intake_plan_markdown(intake_plan), encoding="utf-8")
    summary = build_resolution_summary(queue, intake_plan, matrix, previous_matrix)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "evidence_resolution_queue": str(queue_path),
        "evidence_resolution_queue_markdown": str(evidence_dir / "evidence_resolution_queue.md"),
        "material_intake_plan": str(intake_path),
        "material_intake_plan_markdown": str(evidence_dir / "material_intake_plan.md"),
        "evidence_resolution_summary": str(summary_path),
    }


def build_resolution_summary(
    queue: dict[str, Any],
    intake_plan: dict[str, Any],
    matrix: dict[str, Any],
    previous_matrix: dict[str, Any] | None,
) -> dict[str, Any]:
    current_stats = matrix.get("statistics") or {}
    previous_stats = (previous_matrix or {}).get("statistics") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lot_name": matrix.get("lot_name"),
        "generated_from": "evidence_coverage_matrix.json",
        "resolution_statistics": queue.get("statistics") or {},
        "material_intake_statistics": intake_plan.get("statistics") or {},
        "confirmed_change": {
            "previous": int(previous_stats.get("confirmed_count") or 0),
            "current": int(current_stats.get("confirmed_count") or 0),
            "delta": int(current_stats.get("confirmed_count") or 0) - int(previous_stats.get("confirmed_count") or 0),
        },
        "quantity_gap_changes": quantity_gap_changes(previous_matrix or {}, matrix),
    }


def quantity_gap_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    old = {str(item.get("score_item_id") or ""): item for item in list(previous.get("score_item_coverage") or [])}
    result = []
    for item in list(current.get("score_item_coverage") or []):
        if item.get("required_count") is None:
            continue
        before = old.get(str(item.get("score_item_id") or ""), {})
        result.append(
            {
                "score_item_id": item.get("score_item_id"),
                "score_item_name": item.get("score_item_name"),
                "previous_remaining_gap": before.get("remaining_confirmed_gap"),
                "current_remaining_gap": item.get("remaining_confirmed_gap"),
            }
        )
    return result


def render_resolution_queue_markdown(queue: dict[str, Any]) -> str:
    lines = [
        f"# 证据确认队列：{queue.get('lot_name') or '-'}",
        "",
        f"- 正式来源：`{queue.get('generated_from') or '-'}`",
        f"- 决策存储：`{queue.get('decision_store_path') or '-'}`",
        f"- 原始复核项：{(queue.get('statistics') or {}).get('raw_manual_review_count', 0)}",
        f"- 归并任务：{(queue.get('statistics') or {}).get('resolution_case_count', 0)}",
        "",
    ]
    for case in list(queue.get("resolution_cases") or []):
        lines.extend(
            [
                f"## {case.get('canonical_question')}",
                "",
                f"- ID：`{case.get('resolution_id')}`",
                f"- 对象：{case.get('resolution_subject_type')} / {case.get('subject_id')}",
                f"- 状态：{case.get('current_status')}",
                f"- 阻塞：{case.get('blocking_level')}",
                f"- 影响证据：{'、'.join(case.get('related_evidence_ids') or []) or '-'}",
                f"- 影响评分项：{'、'.join(case.get('related_score_item_ids') or []) or '-'}",
                f"- 必填字段：{'、'.join(case.get('required_fields_for_confirmation') or []) or '-'}",
                f"- 复核角色：{case.get('suggested_reviewer_role')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_material_intake_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        f"# 企业材料补录计划：{plan.get('lot_name') or '-'}",
        "",
        f"- 正式来源：`{plan.get('generated_from') or '-'}`",
        f"- 补录包：{(plan.get('statistics') or {}).get('intake_package_count', 0)}",
        "",
    ]
    for package in list(plan.get("material_intake_packages") or []):
        lines.extend(
            [
                f"## {package.get('title')}",
                "",
                f"- 责任部门：{package.get('target_owner')}",
                f"- 状态：{package.get('intake_status')}",
                f"- 请求/确认/候选/剩余：{package.get('requested_count')} / {package.get('confirmed_count')} / {package.get('candidate_count')} / {package.get('remaining_count')}",
                f"- 材料组件：{'、'.join(package.get('requested_material_components') or []) or '-'}",
                f"- 完整标准：{'；'.join(package.get('completeness_criteria') or []) or '-'}",
                f"- 当前不足：{'；'.join(package.get('reasons_current_evidence_is_insufficient') or []) or '-'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def inspect_resolution_outputs(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    evidence_dir = Path(output_dir) / safe_path_name(lot_name) / "evidence"
    queue = load_json(evidence_dir / "evidence_resolution_queue.json")
    intake = load_json(evidence_dir / "material_intake_plan.json")
    summary = load_json(evidence_dir / "evidence_resolution_summary.json")
    matrix = load_json(evidence_dir / "evidence_coverage_matrix.json")
    catalog = load_json(evidence_dir / "enterprise_evidence_catalog.json")
    return {
        "lot_name": lot_name,
        "queue_path": str(evidence_dir / "evidence_resolution_queue.json"),
        "decision_store_path": queue.get("decision_store_path"),
        "material_intake_plan_path": str(evidence_dir / "material_intake_plan.json"),
        "statistics": queue.get("statistics") or {},
        "material_intake_statistics": intake.get("statistics") or {},
        "confirmation_status_by_evidence_type": confirmation_status_by_evidence_type(catalog),
        "confirmation_traceability": confirmation_traceability(catalog),
        "person_confirmation_status": subject_statuses(queue, {"person_identity", "person_department", "person_availability", "person_role"}),
        "certificate_confirmation_status": subject_statuses(queue, {"certificate_identity", "certificate_validity", "evidence_document"}),
        "project_package_confirmation_status": subject_statuses(queue, {"project_identity", "project_relevance", "evidence_package"}),
        "intake_tasks_by_owner": intake_tasks_by_owner(intake),
        "confirmed_change": summary.get("confirmed_change") or {},
        "quantity_gap_changes": summary.get("quantity_gap_changes") or [],
        "current_confirmed_count": (matrix.get("statistics") or {}).get("confirmed_count", 0),
    }


def build_resolution_outputs_from_existing(
    lot_name: str,
    output_dir: str | Path = "outputs",
    *,
    decision_store_path: str | Path | None = None,
) -> dict[str, Any]:
    evidence_dir = Path(output_dir) / safe_path_name(lot_name) / "evidence"
    matrix_path = evidence_dir / "evidence_coverage_matrix.json"
    catalog_path = evidence_dir / "enterprise_evidence_catalog.json"
    matrix = load_json(matrix_path)
    catalog = load_json(catalog_path)
    if str(matrix.get("lot_name") or "") != lot_name or str(catalog.get("lot_name") or "") != lot_name:
        raise ValueError("evidence outputs lot_name mismatch")
    store, store_path = ensure_decision_store(lot_name, decision_store_path)
    queue = build_resolution_queue(lot_name, matrix, catalog, store, store_path)
    intake = build_material_intake_plan(lot_name, matrix)
    paths = write_resolution_artifacts(evidence_dir, queue, intake, matrix, matrix)
    return {
        "lot_name": lot_name,
        "generated_from": str(matrix_path),
        "decision_store_path": str(store_path),
        "statistics": queue.get("statistics") or {},
        "material_intake_statistics": intake.get("statistics") or {},
        "files": paths,
    }


def confirmation_status_by_evidence_type(catalog: dict[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, Counter[str]] = defaultdict(Counter)
    for item in list(catalog.get("evidence_items") or []):
        result[str(item.get("evidence_type") or "unknown")][str(item.get("verification_status") or "unverified")] += 1
    return {key: dict(sorted(value.items())) for key, value in sorted(result.items())}


def confirmation_traceability(catalog: dict[str, Any]) -> dict[str, int]:
    result = Counter()
    for item in list(catalog.get("evidence_items") or []):
        confirmed = item.get("verification_status") in {"human_confirmed", "system_verified"}
        if confirmed and item.get("excerpt"):
            result["confirmed_with_text_excerpt"] += 1
        elif confirmed and item.get("verification_method") == "human_visual_review" and item.get("visual_source_ref"):
            result["confirmed_by_human_visual_review"] += 1
        elif not item.get("excerpt") and not item.get("visual_source_ref"):
            result["unconfirmed_without_text_or_visual_review"] += 1
    return {
        "confirmed_with_text_excerpt": result.get("confirmed_with_text_excerpt", 0),
        "confirmed_by_human_visual_review": result.get("confirmed_by_human_visual_review", 0),
        "unconfirmed_without_text_or_visual_review": result.get("unconfirmed_without_text_or_visual_review", 0),
    }


def subject_statuses(queue: dict[str, Any], subject_types: set[str]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                str(item.get("current_status") or "pending")
                for item in list(queue.get("resolution_cases") or [])
                if item.get("resolution_subject_type") in subject_types
            ).items()
        )
    )


def intake_tasks_by_owner(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in list(plan.get("material_intake_packages") or []):
        result[str(item.get("target_owner") or "未指定")].append(
            {
                "intake_package_id": item.get("intake_package_id"),
                "title": item.get("title"),
                "priority": item.get("priority"),
                "intake_status": item.get("intake_status"),
            }
        )
    return dict(sorted(result.items()))


def update_context_resolution_index(
    context: dict[str, Any],
    queue: dict[str, Any],
    queue_path: Path,
    intake_path: Path,
    decision_store_path: Path,
) -> None:
    unresolved = [
        item
        for item in list(queue.get("resolution_cases") or [])
        if item.get("current_status") not in {"resolved", "rejected", "superseded"}
    ]
    context["evidence_resolution_queue_path"] = str(queue_path)
    context["material_intake_plan_path"] = str(intake_path)
    context["evidence_resolution_decision_store_path"] = str(decision_store_path)
    context["unresolved_resolution_ids"] = [item.get("resolution_id") for item in unresolved]
    by_chapter: dict[str, list[str]] = defaultdict(list)
    for item in unresolved:
        if item.get("blocking_level") == "does_not_block_writing":
            continue
        for chapter_id in list(item.get("related_context_chapter_ids") or []):
            by_chapter[str(chapter_id)].append(str(item.get("resolution_id") or ""))
    for chapter in list(context.get("chapters") or []):
        chapter["chapter_blocking_resolution_ids"] = unique(by_chapter.get(str(chapter.get("chapter_id") or ""), []))


def normalize(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:14]
    return f"{prefix}-{digest}"


def unique_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def highest_priority(values: list[str]) -> str:
    return sorted(values or ["medium"], key=priority_order)[0]


def priority_order(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}.get(value, 2)


def status_order(value: str) -> int:
    return {"pending": 0, "partially_resolved": 1, "needs_more_material": 2, "deferred": 3, "resolved": 4, "rejected": 5, "superseded": 6}.get(value, 7)

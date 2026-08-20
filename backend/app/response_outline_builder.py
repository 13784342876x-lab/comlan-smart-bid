from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.document_clause_indexer import build_clause_index, build_heading_outline
from app.file_id_utils import existing_file_ids
from app.clause_relevance_guard import (
    clause_guard_reason,
    is_clause_applicable,
    missing_clause_message,
    missing_compliance_message,
    section_type_for_task,
)
from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.material_matcher import TENDER_PROJECTS_PATH, find_project_by_lot_name, load_tender_projects, save_tender_projects
from app.material_taxonomy import build_material_profile
from app.personnel_taxonomy import build_personnel_profile, infer_required_departments_from_tender
from app.scoring_action_planner import (
    build_scoring_action_plan_from_context,
    build_section_scoring_action_plan,
    render_section_plan_markdown,
    write_scoring_action_plan,
)
from app.tender_source_guard import is_tender_source_file
from app.structured_clause_index import summarize_structured_clauses
from config import METADATA_PATH


OUTPUT_KINDS = ["technical_response_context", "technical_response_outline", "outline_debug"]
MISSING_TENDER_REF_MESSAGE = "未定位到具体招标正文条款，需确认技术规范书/委托人要求附件是否已入库并解析。"

SOURCE_CANDIDATE_NAME_KEYWORDS = ["招标", "技术规范", "技术标准", "委托人要求", "服务要求", "附件", "评分", "评标", "合同"]
SOURCE_CANDIDATE_TEXT_KEYWORDS = ["技术标准详见技术规范书", "技术规范书", "委托人要求", "服务内容", "服务要求", "项目负责人", "业绩"]
TECH_SOURCE_HINTS = ["技术规范", "技术标准", "委托人要求", "服务要求", "服务内容", "技术要求", "附件"]
STRONG_PROJECT_RELEVANCE_KEYWORDS = ["区块链", "工业数据", "数据安全", "隐私保护", "访问控制", "监管", "智能合约", "安全分析", "性能测试", "研发负责人"]
WEAK_PROJECT_RELEVANCE_KEYWORDS = ["Python", "研发"]
QUALIFICATION_FILE_IDS = {"file_2308aa547dd9"}
STALE_SUMMARY_FILE_IDS = {"file_a5eee523007a", "file_a0a571b442b0"}
QUALIFICATION_CERTIFICATE_NAMES = [
    "ISO 9001质量管理体系认证证书",
    "ISO 14001环境管理体系认证证书",
    "ISO 20000信息技术服务管理体系认证证书",
    "ISO 27001信息安全管理体系认证证书",
    "ISO 27701隐私信息管理体系认证证书",
    "ITSS信息技术服务标准符合性证书（二级）",
    "CS信息系统建设和服务能力等级证书（CS3）",
    "信创数智技术服务能力等级证书（一级）",
    "CCRC信息安全风险评估三级服务资质",
    "CCRC信息安全应急处理三级服务资质",
    "CCRC信息系统安全集成三级服务资质",
    "CCRC信息系统安全运维三级服务资质",
    "代理资质证明",
]


def build_technical_outline_from_directory(
    input_dir: str | Path,
    *,
    output_dir: str | Path = "outputs",
    output_key: str | None = None,
) -> dict[str, Any]:
    """Build the v2 technical-only outline from a manually selected file directory."""
    from app.technical_outline_builder import build_technical_outline

    return build_technical_outline(input_dir, output_dir=output_dir, output_key=output_key)


def build_response_outline(
    project: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    output_dir: str | Path = "outputs",
) -> dict[str, Any]:
    metadata = metadata or load_metadata()
    tender_file = find_file_by_id(metadata, project.get("tender_file_id"), "tender_file_id")
    if not str(tender_file.get("parsed_text") or "").strip():
        raise ValueError(f"parsed_text not found for tender_file_id={project.get('tender_file_id')}")

    source_files = select_outline_source_files(project, metadata, tender_file)
    clause_indexes = build_source_clause_indexes(source_files, str(project.get("lot_name") or ""))
    material_profile_map = material_profiles_by_file_id(metadata)
    personnel_profile_map = personnel_profiles_by_file_id(metadata)
    department_match = infer_outline_required_departments(project, source_files)
    material_map = material_map_by_task_id(project, metadata)
    score_map = score_items_by_id(project)
    technical_sections = build_technical_sections(
        project,
        clause_indexes,
        material_map,
        score_map,
        material_profile_map,
        personnel_profile_map,
        department_match,
    )
    for section in technical_sections:
        section["scoring_action_plan"] = build_section_scoring_action_plan(project, section)
    summary = build_summary(technical_sections, source_files, clause_indexes)
    project_metadata = project.get("tender_project_metadata") if isinstance(project.get("tender_project_metadata"), dict) else {}
    project_name_info = project_name_info_from_metadata(project_metadata, metadata, project, source_files)
    render_project = dict(project)
    render_project["project_name"] = project_name_info.get("project_name")
    render_project["project_name_source"] = project_name_info.get("source")
    render_project["tender_project_metadata"] = project_metadata
    render_project["project_info_fields"] = project_name_info.get("project_info_fields") or {}
    global_compliance = match_compliance_clause(clause_indexes)

    lot_name = str(project.get("lot_name") or "unknown_lot")
    outline_dir = Path(output_dir) / safe_path_name(lot_name) / "outline"
    outline_dir.mkdir(parents=True, exist_ok=True)

    global_format_refs = build_global_format_requirement_refs(clause_indexes)
    context = build_technical_response_context(
        render_project,
        source_files,
        technical_sections,
        summary,
        global_compliance,
        global_format_refs,
        metadata,
    )
    scoring_action_plan = build_scoring_action_plan_from_context(context)
    markdown = render_technical_response_outline_from_context(context)
    debug = build_debug_payload(render_project, source_files, clause_indexes, technical_sections, summary, global_compliance)
    filtered_candidates = collect_filtered_clause_candidates(technical_sections)
    ensure_no_decision_terms(markdown)
    ensure_no_decision_terms(context)
    ensure_no_decision_terms(debug)

    context_path = outline_dir / "technical_response_context.json"
    md_path = outline_dir / "technical_response_outline.md"
    debug_path = outline_dir / "outline_debug.json"
    filtered_path = outline_dir / "filtered_clause_candidates.json"
    scoring_paths = write_scoring_action_plan(scoring_action_plan, outline_dir)
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    debug_path.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
    filtered_path.write_text(json.dumps(filtered_candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "lot_name": project.get("lot_name"),
        "version": "v1.5",
        "output_dir": str(outline_dir),
        "files": [
            {"kind": "technical_response_context", "path": str(context_path)},
            {"kind": "technical_response_outline", "path": str(md_path)},
            {"kind": "outline_debug", "path": str(debug_path)},
            {"kind": "filtered_clause_candidates", "path": str(filtered_path)},
            {"kind": "scoring_action_plan_json", "path": scoring_paths["json"]},
            {"kind": "scoring_action_plan_markdown", "path": scoring_paths["markdown"]},
        ],
        "summary": summary,
    }
    ensure_no_decision_terms(result)
    return result


def build_technical_response_context(
    project: dict[str, Any],
    source_files: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    summary: dict[str, Any],
    global_compliance: dict[str, Any],
    global_format_refs: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata_files = {
        str(item.get("id") or item.get("file_id") or ""): item
        for item in list(metadata.get("files") or [])
        if isinstance(item, dict)
    }
    conflicts = build_project_conflicts(project)
    chapters = [
        build_context_chapter(section, order, project, metadata_files)
        for order, section in enumerate(sections, start=1)
    ]
    metadata_manual = list(((project.get("tender_project_metadata") or {}).get("manual_confirm_items") or []))
    global_manual = [
        normalize_manual_confirm_item(item, "project-metadata", index)
        for index, item in enumerate(metadata_manual, start=1)
    ]
    global_manual.extend(conflict_to_manual_confirm(item) for item in conflicts if item.get("status") == "needs_human_confirmation")
    global_missing = dedupe_context_items(
        [item for chapter in chapters for item in list(chapter.get("missing_materials") or [])],
        "missing_id",
    )
    context = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lot_name": project.get("lot_name"),
        "project": build_context_project(project),
        "project_conflicts": conflicts,
        "source_files": [build_context_source_file(item) for item in source_files],
        "chapters": chapters,
        "global_missing_materials": global_missing,
        "global_manual_confirm_items": global_manual,
        "build_warnings": build_context_warnings(chapters, conflicts),
        "summary": dict(summary),
        "global_compliance_ref": normalize_global_compliance_ref(global_compliance),
        "global_response_format_refs": [
            normalize_requirement_ref(item, f"global-format-{index:03d}")
            for index, item in enumerate(global_format_refs, start=1)
        ],
    }
    validate_context_source_types(context)
    return context


def build_context_project(project: dict[str, Any]) -> dict[str, Any]:
    info = project.get("project_info_fields") if isinstance(project.get("project_info_fields"), dict) else {}
    score_scale = project.get("score_scale") if isinstance(project.get("score_scale"), dict) else {}
    return {
        "tender_number": info.get("tender_no") or "",
        "project_name": project.get("project_name") or info.get("project_name") or "",
        "lot_number": info.get("lot_no") or project.get("lot_name") or "",
        "lot_name": info.get("lot_name") or project.get("lot_name") or "",
        "package_number": info.get("package_no") or "",
        "package_name": info.get("package_name") or "",
        "special_qualification": info.get("special_qualification_requirement") or "",
        "score_weights": dict(score_scale.get("weights") or {}),
        "technical_topic_name": info.get("technical_topic_name") or "",
        "tenderee": info.get("tenderee") or "",
        "tender_agency": info.get("tender_agency") or "",
        "service_period": info.get("service_period") or "",
        "max_price": info.get("max_price") or "",
        "source_type": "tender_original_fact",
    }


def build_context_source_file(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file_id": item.get("id") or item.get("file_id") or "",
        "source_file_name": item.get("original_name") or item.get("source_file_name") or "",
        "category": item.get("category") or "",
        "source_folder": item.get("source_folder") or "",
        "source_type": "tender_original_fact",
    }


def build_project_conflicts(project: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = project.get("tender_project_metadata") if isinstance(project.get("tender_project_metadata"), dict) else {}
    fields = metadata.get("fields") if isinstance(metadata.get("fields"), dict) else {}
    candidates_by_field = metadata.get("field_candidates") if isinstance(metadata.get("field_candidates"), dict) else {}
    conflicts: list[dict[str, Any]] = []
    for field_name, candidates in candidates_by_field.items():
        rows = [item for item in list(candidates or []) if isinstance(item, dict) and str(item.get("value") or "").strip()]
        values = unique_terms([str(item.get("value") or "") for item in rows])
        if len(values) < 2:
            continue
        selected = fields.get(field_name) if isinstance(fields.get(field_name), dict) else {}
        selected_value = str(selected.get("value") or "")
        status = "needs_human_confirmation" if field_name == "package_name" else "informational"
        conflicts.append(
            {
                "field": field_name,
                "selected_value": selected_value,
                "candidate_values": values,
                "candidate_sources": [
                    {
                        "value": item.get("value"),
                        "source_file_id": item.get("source_file_id") or "",
                        "source_file_name": item.get("source_file_name") or "",
                        "source_document_type": item.get("source_type") or "",
                        "confidence": item.get("confidence"),
                        "source_type": "tender_original_fact",
                    }
                    for item in rows
                ],
                "reason": "同一字段存在多个招标来源候选值；本轮保留既有选择结果，不重新执行名称选择。",
                "status": status,
                "human_confirm_question": f"请确认{context_field_label(field_name)}最终采用“{selected_value}”还是其他候选值。",
                "source_type": "human_confirmation_required" if status == "needs_human_confirmation" else "system_judgement",
            }
        )
    return conflicts


def conflict_to_manual_confirm(conflict: dict[str, Any]) -> dict[str, Any]:
    return {
        "confirm_id": f"confirm-project-{conflict.get('field') or 'field'}",
        "category": "project_field_conflict",
        "question": conflict.get("human_confirm_question") or "请确认冲突字段。",
        "related_field": conflict.get("field") or "",
        "current_value": conflict.get("selected_value") or "",
        "candidate_values": list(conflict.get("candidate_values") or []),
        "source_refs": list(conflict.get("candidate_sources") or []),
        "reason": conflict.get("reason") or "",
        "priority": "high",
        "source_type": "human_confirmation_required",
    }


def build_context_chapter(
    section: dict[str, Any],
    chapter_order: int,
    project: dict[str, Any],
    metadata_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    chapter_id = str(section.get("section_id") or section.get("task_id") or f"chapter-{chapter_order:02d}")
    score_refs = [normalize_score_ref(section.get("score_ref") or {}, project, section)]
    all_requirement_refs = list(section.get("response_requirement_refs") or [])
    tender_ref = section.get("tender_requirement_ref") if isinstance(section.get("tender_requirement_ref"), dict) else {}
    if tender_ref and tender_ref.get("evidence_quality") not in {"not_found", "heading_only", "invalid_clause"}:
        all_requirement_refs.append(requirement_ref_from_tender_ref(tender_ref, requirement_type_for_tender_ref(tender_ref, str(section.get("task_name") or "")), str(section.get("task_name") or "")))
    normalized_refs = dedupe_context_refs(
        [normalize_requirement_ref(item, f"{chapter_id}-ref-{index:03d}") for index, item in enumerate(all_requirement_refs, start=1)]
    )
    tender_refs: list[dict[str, Any]] = []
    technical_refs: list[dict[str, Any]] = []
    format_refs: list[dict[str, Any]] = []
    for ref in normalized_refs:
        if ref.get("requirement_type") == "format_form" or ref.get("document_region") in {"response_format", "technical_file_format"}:
            format_refs.append(ref)
        elif ref.get("document_region") == "technical_spec" or "技术规范" in str(ref.get("source_file_name") or ""):
            technical_refs.append(ref)
        else:
            tender_refs.append(ref)

    evidence_refs = normalize_evidence_refs(section, chapter_id, metadata_files)
    missing_materials = [
        normalize_missing_material(item, chapter_id, index, score_refs, normalized_refs)
        for index, item in enumerate(list(section.get("missing_materials") or []), start=1)
        if isinstance(item, dict)
    ]
    manual_items = [
        normalize_manual_confirm_item(item, chapter_id, index)
        for index, item in enumerate(list(section.get("manual_review_items") or []), start=1)
    ]
    writing_tasks = build_context_writing_tasks(section, chapter_id, score_refs, normalized_refs, evidence_refs, missing_materials)
    warnings = []
    if tender_ref.get("evidence_quality") in {"not_found", "heading_only", "invalid_clause", "weak_clause"}:
        warnings.append("本章节招标正文定位精度不足，需人工复核原始文件。")
    if any(item.get("evidence_status") != "confirmed" for item in evidence_refs):
        warnings.append("候选、弱、不可用或待复核证据不得直接进入正式正文。")
    return {
        "chapter_id": chapter_id,
        "chapter_title": section.get("section_title") or "",
        "chapter_order": chapter_order,
        "score_refs": score_refs,
        "tender_requirement_refs": tender_refs,
        "technical_spec_refs": technical_refs,
        "response_format_refs": format_refs,
        "evidence_refs": evidence_refs,
        "missing_materials": missing_materials,
        "manual_confirm_items": manual_items,
        "writing_tasks": writing_tasks,
        "build_warnings": unique_terms(warnings),
        "tender_ref_status": {
            "evidence_quality": tender_ref.get("evidence_quality") or "not_found",
            "requirement_interpretation": tender_ref.get("requirement_interpretation") or "",
            "source_snippet": tender_ref.get("source_snippet") or "",
            "section_type": tender_ref.get("section_type") or section_type_for_task(str(section.get("task_name") or "")),
            "source_type": "system_judgement",
        },
        "action_plan": normalize_action_plan_for_context(section.get("scoring_action_plan") or {}),
        "person_candidates": list(section.get("person_candidates") or []),
        "department_match": dict(section.get("department_match") or {}),
        "material_note": section.get("material_note") or "",
    }


def normalize_score_ref(ref: dict[str, Any], project: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    score_item_id = str(ref.get("score_item_id") or "")
    score_item = next(
        (
            item
            for item in list(project.get("score_items") or [])
            if str(item.get("id") or "") == score_item_id
        ),
        {},
    )
    if not score_item:
        score_item = next(
            (
                item
                for item in list(project.get("score_items") or [])
                if str(item.get("item_name") or "") == str(ref.get("score_item") or "")
            ),
            {},
        )
    source_file_id = str(project.get("score_file_id") or "")
    source_location = build_source_location(source_file_id, "", str(score_item.get("raw_text") or ref.get("score_description") or ""), "file_only")
    return {
        "score_item_id": score_item.get("id") or score_item_id,
        "score_item_name": score_item.get("item_name") or ref.get("score_item") or section.get("task_name") or "",
        "score": score_item.get("score"),
        "score_range": score_item.get("score_range") or ref.get("score_range") or {"min": None, "max": None},
        "raw_score_text": score_item.get("raw_text") or ref.get("score_description") or "",
        "score_rule": score_item.get("score_rule") or ref.get("score_description") or "",
        "source_template_name": score_item.get("source_template_name") or ref.get("score_source_file") or "",
        "source_location": source_location,
        "source_type": "tender_original_fact",
    }


def normalize_requirement_ref(ref: dict[str, Any], ref_id: str) -> dict[str, Any]:
    file_id = str(ref.get("source_file_id") or "")
    path = str(ref.get("section_path") or ref.get("clause_path") or "")
    excerpt = str(ref.get("original_excerpt") or ref.get("source_snippet") or ref.get("snippet") or "")
    quality = str(ref.get("evidence_quality") or "")
    document_region = str(ref.get("source_type") or ref.get("document_region") or "")
    precision = "clause_approximate" if path or ref.get("clause_id") else "snippet_only" if excerpt else "file_only" if file_id else "unknown"
    confidence = ref.get("confidence")
    if confidence is None:
        confidence = 0.9 if quality in {"可直接引用", "confirmed_clause"} else 0.55 if quality else None
    return {
        "ref_id": ref.get("clause_id") or ref_id,
        "title": path or ref.get("requirement_type") or "招标要求",
        "source_file_id": file_id,
        "source_file_name": ref.get("source_file_name") or "",
        "clause_path": path,
        "source_location": build_source_location(file_id, path, excerpt, precision),
        "excerpt": excerpt,
        "source_type": "tender_original_fact",
        "confidence": confidence,
        "needs_human_review": quality not in {"可直接引用", "confirmed_clause"},
        "requirement_type": ref.get("requirement_type") or "content_response",
        "document_region": document_region,
        "applies_to": list(ref.get("applies_to") or []),
    }


def normalize_evidence_refs(
    section: dict[str, Any],
    chapter_id: str,
    metadata_files: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = section.get("evidence_groups") if isinstance(section.get("evidence_groups"), dict) else {}
    status_by_group = {"confirmed": "confirmed", "candidate": "candidate", "weak": "weak", "unusable": "unusable"}
    for group_name, proposed_status in status_by_group.items():
        for item in list(groups.get(group_name) or []):
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or item.get("source_file_id") or "")
            file_record = metadata_files.get(file_id, {})
            excerpt = str(item.get("snippet") or item.get("excerpt") or "")
            match_basis = str(item.get("applicability_reason") or item.get("match_reason") or item.get("usable_as") or "")
            status = proposed_status
            if status == "confirmed" and not confirmed_evidence_is_traceable(section, item, file_id, excerpt, match_basis):
                status = "needs_review"
            needs_review = status != "confirmed"
            rows.append(
                {
                    "evidence_id": f"{chapter_id}-evidence-{len(rows) + 1:03d}",
                    "evidence_type": item.get("material_type") or item.get("fact_type") or "unknown",
                    "title": item.get("title") or item.get("material_name") or file_record.get("original_name") or "",
                    "source_file_id": file_id,
                    "source_file_name": item.get("source_file_name") or file_record.get("original_name") or item.get("title") or "",
                    "source_location": build_source_location(file_id, str(item.get("source") or ""), excerpt, "snippet_only" if excerpt else "file_only" if file_id else "unknown"),
                    "excerpt": excerpt,
                    "related_person": item.get("person") or (item.get("personnel_profile") or {}).get("person_name") or "",
                    "related_project": item.get("project_name") or "",
                    "related_qualification": item.get("qualification_name") or "",
                    "match_basis": match_basis,
                    "evidence_status": status,
                    "confidence": item.get("confidence"),
                    "source_type": "enterprise_existing_fact" if file_id else "system_judgement",
                    "needs_human_review": needs_review,
                    "human_review_reason": "" if not needs_review else match_basis or "证据来源、定位或与评分项的对应关系尚不足。",
                }
            )
    return rows


def confirmed_evidence_is_traceable(
    section: dict[str, Any],
    item: dict[str, Any],
    file_id: str,
    excerpt: str,
    match_basis: str,
) -> bool:
    if not file_id or not excerpt or not match_basis:
        return False
    if str(item.get("fact_type") or "") == "aggregated_person_candidate":
        return False
    title = str(section.get("section_title") or "")
    material_group = str(item.get("material_group") or (item.get("material_profile") or {}).get("material_group") or "")
    if "业绩" in title and material_group in {"personnel", "company_qualification"}:
        return False
    if "专利" in title and material_group in {"personnel", "company_qualification"}:
        return False
    if "绩效" in title and material_group in {"personnel", "company_qualification", "company_performance"}:
        return False
    return True


def normalize_missing_material(
    item: dict[str, Any],
    chapter_id: str,
    index: int,
    score_refs: list[dict[str, Any]],
    requirement_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    material_type = str(item.get("material_type") or item.get("requested_material_type") or "material")
    return {
        "missing_id": f"{chapter_id}-missing-{index:03d}",
        "title": item.get("title") or material_type,
        "description": item.get("suggested_action") or item.get("description") or "请补充对应材料。",
        "related_score_item_ids": [str(ref.get("score_item_id") or "") for ref in score_refs if ref.get("score_item_id")],
        "related_requirement_ref_ids": [str(ref.get("ref_id") or "") for ref in requirement_refs if ref.get("ref_id")],
        "reason": item.get("reason") or "当前未定位到可用于本章节的材料。",
        "requested_material_type": material_type,
        "suggested_owner": item.get("suggested_owner") or suggested_owner_for_material(material_type),
        "priority": item.get("priority") or ("high" if material_type in {"company_contract", "patent_certificate", "supplier_performance_evaluation"} else "medium"),
        "source_type": "suggested_material",
    }


def normalize_manual_confirm_item(item: Any, chapter_id: str, index: int) -> dict[str, Any]:
    question = str(item.get("question") if isinstance(item, dict) else item or "请人工复核。")
    return {
        "confirm_id": f"{chapter_id}-confirm-{index:03d}",
        "category": item.get("category") if isinstance(item, dict) else "chapter_review",
        "question": question,
        "related_field": item.get("related_field") if isinstance(item, dict) else "",
        "current_value": item.get("current_value") if isinstance(item, dict) else "",
        "candidate_values": list(item.get("candidate_values") or []) if isinstance(item, dict) else [],
        "source_refs": list(item.get("source_refs") or []) if isinstance(item, dict) else [],
        "reason": item.get("reason") if isinstance(item, dict) else question,
        "priority": item.get("priority") if isinstance(item, dict) else "medium",
        "source_type": "human_confirmation_required",
    }


def build_context_writing_tasks(
    section: dict[str, Any],
    chapter_id: str,
    score_refs: list[dict[str, Any]],
    requirement_refs: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    missing_materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = [str(item.get("evidence_id")) for item in evidence_refs if item.get("evidence_status") == "confirmed"]
    restricted = [str(item.get("evidence_id")) for item in evidence_refs if item.get("evidence_status") != "confirmed"]
    structures = list(section.get("suggested_structure") or [])
    description = str(section.get("writing_goal") or "")
    if structures:
        description = (description + " 建议结构：" + "；".join(str(item) for item in structures)).strip()
    return [
        {
            "task_id": f"{chapter_id}-writing-001",
            "title": f"编制{section.get('section_title') or '技术响应'}章节",
            "description": description,
            "related_score_item_ids": [str(item.get("score_item_id") or "") for item in score_refs if item.get("score_item_id")],
            "related_requirement_ref_ids": [str(item.get("ref_id") or "") for item in requirement_refs if item.get("ref_id")],
            "allowed_evidence_ids": allowed,
            "restricted_evidence_ids": restricted,
            "missing_material_ids": [str(item.get("missing_id") or "") for item in missing_materials],
            "source_type": "suggested_writing",
        }
    ]


def build_source_location(file_id: str, clause_path: str, snippet: str, precision: str) -> dict[str, Any]:
    return {
        "source_file_id": file_id,
        "clause_path": clause_path,
        "snippet": snippet,
        "location_precision": precision,
    }


def normalize_global_compliance_ref(ref: dict[str, Any]) -> dict[str, Any]:
    if not ref:
        return {}
    return {
        "ref_id": ref.get("clause_id") or "global-compliance",
        "title": ref.get("source") or "通用合规与响应条件",
        "source_file_id": ref.get("source_file_id") or "",
        "source_file_name": ref.get("source_file_name") or "",
        "clause_path": ref.get("clause_path") or "",
        "source_location": build_source_location(
            str(ref.get("source_file_id") or ""),
            str(ref.get("clause_path") or ""),
            str(ref.get("snippet") or ""),
            "clause_approximate" if ref.get("clause_path") else "snippet_only" if ref.get("snippet") else "unknown",
        ),
        "excerpt": ref.get("snippet") or "",
        "source_type": "tender_original_fact",
        "confidence": ref.get("confidence"),
        "needs_human_review": not bool(ref.get("snippet")),
    }


def build_context_warnings(chapters: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> list[str]:
    warnings = [warning for chapter in chapters for warning in list(chapter.get("build_warnings") or [])]
    if any(item.get("status") == "needs_human_confirmation" for item in conflicts):
        warnings.append("项目字段存在冲突，已保留当前选择值并加入人工确认。")
    return unique_terms(warnings)


def dedupe_context_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for ref in refs:
        key = (str(ref.get("source_file_id") or ""), str(ref.get("clause_path") or ""), str(ref.get("excerpt") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def dedupe_context_items(items: list[dict[str, Any]], id_field: str) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("requested_material_type") or item.get("title") or ""), str(item.get("reason") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def suggested_owner_for_material(material_type: str) -> str:
    if "person" in material_type or "resume" in material_type:
        return "相关业务部门"
    if "patent" in material_type:
        return "知识产权/研发管理"
    if "performance_evaluation" in material_type:
        return "供应商管理/商务支持"
    if material_type in {"company_contract", "acceptance_report", "winning_notice_or_customer_certificate"}:
        return "项目管理/公司业绩管理"
    return "相关业务部门"


def validate_context_source_types(context: dict[str, Any]) -> None:
    allowed = {
        "tender_original_fact",
        "enterprise_existing_fact",
        "rule_derived",
        "system_judgement",
        "human_confirmation_required",
        "suggested_material",
        "suggested_writing",
    }
    for chapter in list(context.get("chapters") or []):
        for key in [
            "score_refs",
            "tender_requirement_refs",
            "technical_spec_refs",
            "response_format_refs",
            "evidence_refs",
            "missing_materials",
            "manual_confirm_items",
            "writing_tasks",
        ]:
            for item in list(chapter.get(key) or []):
                if item.get("source_type") not in allowed:
                    raise ValueError(f"invalid source_type in {key}: {item.get('source_type')}")


def normalize_action_plan_for_context(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_action_plan_for_context(item) for item in value]
    if not isinstance(value, dict):
        return value
    allowed = {
        "tender_original_fact",
        "enterprise_existing_fact",
        "rule_derived",
        "system_judgement",
        "human_confirmation_required",
        "suggested_material",
        "suggested_writing",
    }
    result = {key: normalize_action_plan_for_context(item) for key, item in value.items()}
    legacy_type = result.get("source_type")
    if legacy_type and legacy_type not in allowed:
        result["source_document_type"] = legacy_type
        result["source_type"] = "tender_original_fact"
    return result


def context_field_label(field_name: str) -> str:
    return {"package_name": "包名称", "project_name": "项目名称", "lot_name": "分标名称"}.get(field_name, field_name)


def render_technical_response_outline_from_context(context: dict[str, Any]) -> str:
    project = context_project_for_rendering(context)
    sections = [context_chapter_for_rendering(chapter) for chapter in list(context.get("chapters") or [])]
    global_ref = context.get("global_compliance_ref") if isinstance(context.get("global_compliance_ref"), dict) else {}
    global_compliance = {
        "source": global_ref.get("title") or "",
        "snippet": global_ref.get("excerpt") or "",
    }
    global_format_refs = [context_requirement_for_rendering(item) for item in list(context.get("global_response_format_refs") or [])]
    return render_technical_response_outline(
        project,
        sections,
        dict(context.get("summary") or {}),
        global_compliance,
        global_format_refs,
    )


def context_project_for_rendering(context: dict[str, Any]) -> dict[str, Any]:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    manual_items = [str(item.get("question") or "") for item in list(context.get("global_manual_confirm_items") or [])]
    return {
        "lot_name": context.get("lot_name"),
        "project_name": project.get("project_name"),
        "project_info_fields": {
            "tender_no": project.get("tender_number"),
            "project_name": project.get("project_name"),
            "lot_no": project.get("lot_number"),
            "lot_name": project.get("lot_name"),
            "package_no": project.get("package_number"),
            "package_name": project.get("package_name"),
            "technical_topic_name": project.get("technical_topic_name"),
            "tenderee": project.get("tenderee"),
            "tender_agency": project.get("tender_agency"),
            "service_period": project.get("service_period"),
            "max_price": project.get("max_price"),
            "special_qualification_requirement": project.get("special_qualification"),
            "manual_confirm_items": manual_items,
        },
    }


def context_chapter_for_rendering(chapter: dict[str, Any]) -> dict[str, Any]:
    score = (list(chapter.get("score_refs") or []) or [{}])[0]
    requirements = [
        context_requirement_for_rendering(item)
        for key in ["tender_requirement_refs", "technical_spec_refs", "response_format_refs"]
        for item in list(chapter.get(key) or [])
    ]
    primary = (requirements or [{}])[0]
    tender_status = chapter.get("tender_ref_status") if isinstance(chapter.get("tender_ref_status"), dict) else {}
    evidence_groups = {"confirmed": [], "candidate": [], "weak": [], "unusable": []}
    status_group = {"confirmed": "confirmed", "candidate": "candidate", "needs_review": "candidate", "weak": "weak", "unusable": "unusable", "missing": "unusable"}
    for item in list(chapter.get("evidence_refs") or []):
        evidence_groups[status_group.get(str(item.get("evidence_status") or ""), "candidate")].append(context_evidence_for_rendering(item))
    writing = (list(chapter.get("writing_tasks") or []) or [{}])[0]
    description = str(writing.get("description") or "")
    suggested_structure = []
    if "建议结构：" in description:
        suggested_structure = [item for item in description.split("建议结构：", 1)[1].split("；") if item]
    return {
        "section_id": chapter.get("chapter_id"),
        "section_title": chapter.get("chapter_title"),
        "task_id": chapter.get("chapter_id"),
        "task_name": score.get("score_item_name") or chapter.get("chapter_title"),
        "tender_requirement_ref": {
            "source_file_id": primary.get("source_file_id") or "",
            "source_file_name": primary.get("source_file_name") or "",
            "clause_id": primary.get("clause_id") or "",
            "clause_path": primary.get("section_path") or "",
            "source_snippet": primary.get("original_excerpt") or tender_status.get("source_snippet") or "",
            "evidence_quality": "confirmed_clause" if primary and primary.get("evidence_quality") == "可直接引用" else tender_status.get("evidence_quality") or "not_found",
            "requirement_interpretation": tender_status.get("requirement_interpretation") or "",
            "clause_type": primary.get("requirement_type") or "",
            "section_type": tender_status.get("section_type") or section_type_for_task(str(score.get("score_item_name") or chapter.get("chapter_title") or "")),
        },
        "compliance_ref": {},
        "response_requirement_refs": requirements,
        "response_requirement_ignored_candidates": [],
        "score_ref": {
            "score_item_id": score.get("score_item_id"),
            "score_item": score.get("score_item_name"),
            "score_range": score.get("score_range"),
            "score_description": score.get("score_rule") or score.get("raw_score_text"),
            "score_source_file": score.get("source_template_name"),
        },
        "writing_goal": description.split(" 建议结构：", 1)[0],
        "suggested_structure": suggested_structure,
        "evidence_refs": [context_evidence_for_rendering(item) for item in list(chapter.get("evidence_refs") or [])],
        "material_basis": {
            "groups": evidence_groups,
            "person_candidates": list(chapter.get("person_candidates") or []),
            "missing_materials": [context_missing_for_rendering(item) for item in list(chapter.get("missing_materials") or [])],
            "material_note": chapter.get("material_note") or "",
        },
        "evidence_groups": evidence_groups,
        "person_candidates": list(chapter.get("person_candidates") or []),
        "missing_materials": [context_missing_for_rendering(item) for item in list(chapter.get("missing_materials") or [])],
        "material_note": chapter.get("material_note") or "",
        "department_match": dict(chapter.get("department_match") or {}),
        "manual_review_items": [str(item.get("question") or "") for item in list(chapter.get("manual_confirm_items") or [])],
        "scoring_action_plan": dict(chapter.get("action_plan") or {}),
    }


def context_requirement_for_rendering(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "requirement_type": item.get("requirement_type") or "content_response",
        "source_file_id": item.get("source_file_id") or "",
        "source_file_name": item.get("source_file_name") or "",
        "source_type": item.get("document_region") or "tender_source",
        "section_path": item.get("clause_path") or "",
        "original_excerpt": item.get("excerpt") or "",
        "applies_to": list(item.get("applies_to") or []),
        "usage": "作为本章节原文响应依据。",
        "evidence_quality": "标题级依据，需下钻" if item.get("needs_human_review") else "可直接引用",
        "must_quote": not bool(item.get("needs_human_review")),
        "clause_id": item.get("ref_id") or "",
    }


def context_evidence_for_rendering(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": item.get("source_file_id") or "",
        "title": item.get("title") or item.get("source_file_name") or "",
        "material_type": item.get("evidence_type") or "unknown",
        "fact_type": item.get("evidence_type") or "unknown",
        "source": (item.get("source_location") or {}).get("clause_path") or "",
        "snippet": item.get("excerpt") or "",
        "confidence": item.get("confidence"),
        "applicability_reason": item.get("match_basis") or item.get("human_review_reason") or "",
        "evidence_strength": item.get("evidence_status") or "candidate",
        "usable_as": "已确认材料" if item.get("evidence_status") == "confirmed" else "需人工复核",
    }


def context_missing_for_rendering(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_type": item.get("requested_material_type") or "material",
        "reason": item.get("reason") or "",
        "suggested_action": item.get("description") or "",
    }


def save_response_outline_index(
    lot_name: str,
    response_outline: dict[str, Any],
    path: Path = TENDER_PROJECTS_PATH,
) -> dict[str, Any]:
    data = load_tender_projects(path)
    project = find_project_by_lot_name(data, lot_name)
    project["response_outline"] = {
        "version": "v1.5",
        "output_dir": response_outline.get("output_dir"),
        "files": response_outline.get("files", []),
        "summary": response_outline.get("summary", {}),
    }
    save_tender_projects(data, path)
    return project["response_outline"]


def inspect_parsed_text(
    file_id: str,
    head: int = 3000,
    tail: int = 3000,
    find_terms: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or load_metadata()
    file_record = find_file_by_id(metadata, file_id, "file_id")
    parsed_text = str(file_record.get("parsed_text") or "")
    terms = [term for term in list(find_terms or []) if str(term).strip()]
    matches = {term: find_term_contexts(parsed_text, term) for term in terms}
    return {
        "file_id": file_record.get("id"),
        "original_name": file_record.get("original_name"),
        "source_folder": file_record.get("source_folder"),
        "parsed_text_len": len(parsed_text),
        "tables_len": len(list(file_record.get("tables") or [])),
        "asset_count": len(list(file_record.get("assets") or [])),
        "head": parsed_text[:head],
        "tail": parsed_text[-tail:] if tail > 0 else "",
        "matches": matches,
        "heading_outline": build_heading_outline(
            str(file_record.get("id") or ""),
            str(file_record.get("original_name") or ""),
            parsed_text,
        ),
    }


def inspect_project_text_sources(
    project: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or load_metadata()
    candidates = find_project_text_source_candidates(project, metadata)
    return {
        "lot_name": project.get("lot_name"),
        "tender_file_id": project.get("tender_file_id"),
        "score_file_id": project.get("score_file_id"),
        "candidates": candidates,
    }


def load_metadata(path: Path = METADATA_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"metadata file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8") or '{"files": []}')


def material_profiles_by_file_id(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for item in list(metadata.get("files") or []):
        file_id = str(item.get("id") or item.get("file_id") or "")
        if not file_id:
            continue
        profile = item.get("material_profile")
        profiles[file_id] = dict(profile) if isinstance(profile, dict) else build_material_profile(item)
    return profiles


def personnel_profiles_by_file_id(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for item in list(metadata.get("files") or []):
        file_id = str(item.get("id") or item.get("file_id") or "")
        if not file_id:
            continue
        material_profile = item.get("material_profile") if isinstance(item.get("material_profile"), dict) else {}
        existing = item.get("personnel_profile")
        if isinstance(existing, dict):
            profiles[file_id] = dict(existing)
        elif material_profile.get("material_group") == "personnel" or isinstance(item.get("resume_parse"), dict):
            profiles[file_id] = build_personnel_profile(item)
    return profiles


def infer_outline_required_departments(project: dict[str, Any], source_files: list[dict[str, Any]]) -> dict[str, Any]:
    tender_text = " ".join(str(item.get("parsed_text") or "") for item in source_files if item.get("id") == project.get("tender_file_id"))
    technical_spec_text = " ".join(str(item.get("parsed_text") or "") for item in source_files if item.get("id") != project.get("tender_file_id"))
    return infer_required_departments_from_tender(tender_text, list(project.get("score_items") or []), technical_spec_text)


def infer_project_name(metadata: dict[str, Any], project: dict[str, Any], source_files: list[dict[str, Any]]) -> dict[str, str]:
    existing = str(project.get("project_name") or "").strip()
    if existing and existing != "-":
        return {"project_name": existing, "source": "tender_project.project_name"}

    for item in source_files:
        name = str(item.get("original_name") or "")
        if "技术规范" not in name and "技术标准" not in name:
            continue
        extracted = extract_project_name_from_filename(name)
        if extracted:
            return {"project_name": extracted, "source": "技术规范书文件名"}
        extracted = extract_project_name_from_text(str(item.get("parsed_text") or ""))
        if extracted:
            return {"project_name": extracted, "source": "技术规范书正文"}

    for item in source_files:
        extracted = extract_project_name_from_text(str(item.get("parsed_text") or ""))
        if extracted:
            return {"project_name": extracted, "source": f"parsed_text:{item.get('id')}"}

    score_text = json.dumps(project.get("score_items") or [], ensure_ascii=False)
    extracted = extract_project_name_from_text(score_text)
    if extracted:
        return {"project_name": extracted, "source": "score_items"}

    lot_name = str(project.get("lot_name") or "unknown_lot")
    return {"project_name": lot_name, "source": "lot_name"}


def project_name_info_from_metadata(
    tender_project_metadata: dict[str, Any],
    metadata: dict[str, Any],
    project: dict[str, Any],
    source_files: list[dict[str, Any]],
) -> dict[str, Any]:
    fields = tender_project_metadata.get("fields") if isinstance(tender_project_metadata.get("fields"), dict) else {}
    project_name_field = fields.get("project_name") if isinstance(fields.get("project_name"), dict) else {}
    source_type = str(project_name_field.get("source_type") or "")
    value = str(project_name_field.get("value") or "").strip()
    project_name = value if value and source_type != "technical_spec" else "未定位到项目总名称，需从招标公告或招标文件正文确认"

    technical_topic = first_field_value(fields, "technical_topic_name") or first_field_value(fields, "package_name")
    if not technical_topic:
        for item in source_files:
            name = str(item.get("original_name") or "")
            if "技术规范书" in name or "技术标准" in name:
                technical_topic = extract_project_name_from_filename(name) or extract_project_name_from_text(str(item.get("parsed_text") or ""))
                if technical_topic:
                    break

    project_info_fields = {
        "tender_no": first_field_value(fields, "tender_no"),
        "project_name": project_name,
        "lot_no": first_field_value(fields, "lot_no"),
        "lot_name": first_field_value(fields, "lot_name"),
        "package_no": first_field_value(fields, "package_no"),
        "package_name": first_field_value(fields, "package_name"),
        "technical_topic_name": technical_topic,
        "tenderee": first_field_value(fields, "tenderee"),
        "tender_agency": first_field_value(fields, "tender_agency"),
        "service_period": first_field_value(fields, "service_period"),
        "max_price": first_field_value(fields, "max_price"),
        "special_qualification_requirement": first_field_value(fields, "special_qualification_requirement"),
        "manual_confirm_items": list(tender_project_metadata.get("manual_confirm_items") or []),
    }
    return {
        "project_name": project_name,
        "source": source_type or "tender_project_metadata",
        "project_info_fields": project_info_fields,
    }


def first_field_value(fields: dict[str, Any], name: str) -> str:
    field = fields.get(name) if isinstance(fields.get(name), dict) else {}
    return str(field.get("value") or "").strip()


def extract_project_name_from_filename(name: str) -> str:
    stem = re.sub(r"\.(docx?|pdf|xlsx?)$", "", str(name or ""), flags=re.IGNORECASE).strip()
    match = re.search(r"[（(]([^()（）]{4,80})[）)]", stem)
    if match:
        return normalize_project_name(match.group(1))
    parts = re.split(r"[-_]", stem)
    for part in reversed(parts):
        cleaned = normalize_project_name(part)
        if cleaned and not any(skip in cleaned for skip in ["技术规范书", "招标文件", "评标办法"]):
            return cleaned
    return ""


def extract_project_name_from_text(text: str) -> str:
    patterns = [
        r"([\u4e00-\u9fa5A-Za-z0-9_（）()、· -]{4,80}?项目)\s*招标文件",
        r"本部分为([\u4e00-\u9fa5A-Za-z0-9_（）()、· -]{4,80}?项目)招标文件",
        r"项目名称[：:]\s*([\u4e00-\u9fa5A-Za-z0-9_（）()、· -]{4,80})",
        r"([\u4e00-\u9fa5A-Za-z0-9_（）()、· -]{4,80}?技术研究)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return normalize_project_name(match.group(1))
    return ""


def normalize_project_name(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    text = re.sub(r"^(技术规范书|技术标准|招标文件)[（(]?", "", text)
    text = re.sub(r"[）)]$", "", text)
    return text.strip(" _-")


def find_file_by_id(metadata: dict[str, Any], file_id: Any, label: str) -> dict[str, Any]:
    if not file_id:
        raise ValueError(f"{label} not found")
    for item in list(metadata.get("files") or []):
        if item.get("id") == file_id:
            return item
    raise ValueError(f"file not found in metadata: {file_id}")


def find_project_text_source_candidates(project: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    lot_name = str(project.get("lot_name") or "")
    tender_file = find_file_by_id(metadata, project.get("tender_file_id"), "tender_file_id")
    tender_folder = str(tender_file.get("source_folder") or "")
    candidates: list[dict[str, Any]] = []
    for item in list(metadata.get("files") or []):
        name = str(item.get("original_name") or "")
        text = str(item.get("parsed_text") or "")
        folder = str(item.get("source_folder") or "")
        reasons: list[str] = []
        matched_names = [key for key in SOURCE_CANDIDATE_NAME_KEYWORDS if key in name]
        matched_text = [key for key in SOURCE_CANDIDATE_TEXT_KEYWORDS if key in text]
        if matched_names:
            reasons.append("original_name:" + ",".join(matched_names))
        if lot_name and lot_name in text:
            reasons.append("parsed_text:lot_name")
        if "技术标准详见技术规范书" in text:
            reasons.append("parsed_text:tech_spec_pointer")
        if matched_text:
            reasons.append("parsed_text:" + ",".join(matched_text[:5]))
        if tender_folder and folder and folder == tender_folder and item.get("id") != tender_file.get("id"):
            reasons.append("source_folder:near_tender_file")
        if reasons:
            candidates.append(
                {
                    "file_id": item.get("id"),
                    "original_name": item.get("original_name"),
                    "source_folder": item.get("source_folder"),
                    "parsed_text_len": len(text),
                    "candidate_reason": "; ".join(reasons),
                }
            )
    return sorted(candidates, key=lambda row: (-int(row.get("parsed_text_len") or 0), str(row.get("original_name") or "")))


def select_outline_source_files(project: dict[str, Any], metadata: dict[str, Any], tender_file: dict[str, Any]) -> list[dict[str, Any]]:
    source_files = [tender_file]
    score_file_id = project.get("score_file_id")
    for candidate in find_project_text_source_candidates(project, metadata):
        file_id = candidate.get("file_id")
        if file_id == tender_file.get("id") or file_id == score_file_id:
            continue
        name = str(candidate.get("original_name") or "")
        reason = str(candidate.get("candidate_reason") or "")
        if any(hint in name or hint in reason for hint in TECH_SOURCE_HINTS):
            source_files.append(find_file_by_id(metadata, file_id, "candidate_file_id"))
    return source_files


def build_source_clause_indexes(source_files: list[dict[str, Any]], lot_name: str = "") -> dict[str, list[dict[str, Any]]]:
    return {
        str(item.get("id") or ""): build_clause_index(
            str(item.get("id") or ""),
            str(item.get("original_name") or ""),
            str(item.get("parsed_text") or ""),
            item,
            lot_name,
        )
        for item in source_files
    }


def build_technical_sections(
    project: dict[str, Any],
    clause_indexes: dict[str, list[dict[str, Any]]],
    material_map: dict[str, dict[str, Any]],
    score_map: dict[str, dict[str, Any]],
    material_profile_map: dict[str, dict[str, Any]] | None = None,
    personnel_profile_map: dict[str, dict[str, Any]] | None = None,
    department_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    material_profile_map = material_profile_map or {}
    personnel_profile_map = personnel_profile_map or {}
    department_match = department_match or {}
    tasks = [item for item in list(project.get("generation_tasks") or []) if item.get("task_type") == "technical"]
    sections: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        task_name = str(task.get("task_name") or infer_task_name(task) or task_id)
        score = first_related_score_item(task, score_map)
        material = material_map.get(task_id, {})
        tender_ref = match_tender_clause(task, task_name, score, clause_indexes)
        compliance_ref = match_section_compliance_clause(task_name, clause_indexes)
        response_requirement_ignored_candidates: list[dict[str, Any]] = []
        response_requirement_refs = build_response_requirement_refs(
            project,
            task_name,
            tender_ref,
            compliance_ref,
            clause_indexes,
            response_requirement_ignored_candidates,
        )
        evidence_refs = [
            classify_evidence(
                item,
                material_profile_map.get(str(item.get("file_id") or "")),
                personnel_profile_map.get(str(item.get("file_id") or "")),
            )
            for item in list(material.get("matched_materials") or [])
            if isinstance(item, dict)
        ]
        missing_materials = [dict(item) for item in list(material.get("missing_materials") or []) if isinstance(item, dict)]
        material_basis = build_material_basis(task_name, evidence_refs, missing_materials, department_match)
        sections.append(
            {
                "section_id": task_id,
                "section_title": title_for_task(task_name),
                "task_id": task_id,
                "task_name": task_name,
                "tender_requirement_ref": tender_ref,
                "compliance_ref": compliance_ref,
                "response_requirement_refs": response_requirement_refs,
                "response_requirement_ignored_candidates": response_requirement_ignored_candidates,
                "score_ref": build_score_ref(task, score, material),
                "writing_goal": writing_goal_for_task(task, task_name),
                "suggested_structure": suggested_structure_for_task(task_name),
                "evidence_refs": evidence_refs,
                "material_basis": material_basis,
                "evidence_groups": material_basis["groups"],
                "person_candidates": material_basis["person_candidates"],
                "missing_materials": material_basis["missing_materials"],
                "material_note": material_basis.get("material_note", ""),
                "department_match": department_match if is_personnel_task(task_name) else {},
                "manual_review_items": build_manual_review_items(tender_ref, material_basis, material_basis["missing_materials"]),
            }
        )
    return sections


def match_tender_clause(
    task: dict[str, Any],
    task_name: str,
    score: dict[str, Any],
    clause_indexes: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    terms = build_search_terms(task, task_name, score)
    section_type = section_type_for_task(task_name)
    candidates: list[tuple[float, dict[str, Any]]] = []
    heading_only_hits: list[dict[str, Any]] = []
    ignored_candidates: list[dict[str, Any]] = []
    for clauses in clause_indexes.values():
        for clause in clauses:
            score_value = rank_clause(clause, terms)
            if score_value <= 0:
                continue
            if not is_clause_applicable(clause, section_type):
                ignored_candidates.append(ignored_clause_candidate(clause, clause_guard_reason(clause, section_type), score_value))
                continue
            if clause.get("is_invalid"):
                heading_only_hits.append(clause)
                continue
            if clause.get("is_heading_only"):
                heading_only_hits.append(clause)
                continue
            if clause.get("clause_type") in {"scoring_clause", "price_clause"}:
                continue
            candidates.append((score_value, clause))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        score_value, clause = candidates[0]
        quality = "confirmed_clause" if score_value >= 4 else "weak_clause"
        return tender_ref_from_clause(clause, quality, terms, score_value, task_name, ignored_candidates)

    if heading_only_hits and section_type == "generic":
        best = heading_only_hits[0]
        quality = "invalid_clause" if best.get("is_invalid") else "heading_only"
        return tender_ref_from_clause(best, quality, terms, 0, task_name, ignored_candidates)

    return {
        "source_file_id": "",
        "source_file_name": "",
        "clause_id": "",
        "clause_path": "",
        "source_snippet": missing_clause_message(section_type),
        "requirement_interpretation": interpretation_for_task(task_name, {}, "not_found"),
        "evidence_quality": "not_found",
        "matched_terms": terms[:10],
        "match_score": 0,
        "section_type": section_type,
        "ignored_candidates": ignored_candidates[:10],
    }


def match_compliance_clause(clause_indexes: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    terms = ["响应文件组成", "签字", "盖章", "偏差表", "无效响应", "否决", "投标文件格式", "应答文件格式"]
    candidates: list[tuple[float, dict[str, Any]]] = []
    for clauses in clause_indexes.values():
        for clause in clauses:
            if clause.get("is_invalid") or clause.get("is_heading_only"):
                continue
            if clause.get("clause_type") in {"scoring_clause", "price_clause"}:
                continue
            if clause_guard_reason(clause, "technical_overall"):
                continue
            text = str(clause.get("clause_text") or "")
            score = sum(2.0 for term in terms if term in text)
            if any(term in text for term in ["响应文件", "应答文件", "投标文件", "签字", "盖章", "偏差", "无效", "否决"]):
                score += 3.0
            if score > 0:
                candidates.append((score, clause))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, clause = candidates[0]
        return {
            "source": clause.get("file_name"),
            "clause_path": clean_clause_path(list(clause.get("heading_path") or []), str(clause.get("clause_title") or "")),
            "snippet": preview(clause.get("clause_text"), 360),
            "needs_manual_review": False,
        }
    return {
        "source": "",
        "clause_path": "",
        "snippet": "当前未在已解析招标正文中定位到完整响应文件组成/无效响应/签章盖章等条款，需确认完整招标文件是否已入库。",
        "needs_manual_review": True,
    }


def match_section_compliance_clause(task_name: str, clause_indexes: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    terms = compliance_terms_for_task(task_name)
    section_type = section_type_for_task(task_name)
    if not terms:
        return no_section_compliance_ref(task_name)
    candidates: list[tuple[float, dict[str, Any]]] = []
    ignored_candidates: list[dict[str, Any]] = []
    for clauses in clause_indexes.values():
        for clause in clauses:
            if clause.get("is_invalid") or clause.get("is_heading_only"):
                continue
            if clause.get("clause_type") in {"scoring_clause", "price_clause"}:
                continue
            text = str(clause.get("clause_text") or "")
            score = sum(2.0 for term in terms if term in text)
            if score <= 0:
                continue
            if not is_clause_applicable(clause, section_type, for_compliance=True):
                ignored_candidates.append(ignored_clause_candidate(clause, clause_guard_reason(clause, section_type, for_compliance=True), score))
                continue
            if not is_testing_acceptance_task(task_name) and any(term in text for term in ["测试验收基本要求", "测试验收内容"]):
                continue
            candidates.append((score, clause))
    if not candidates:
        return no_section_compliance_ref(task_name, ignored_candidates)
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, clause = candidates[0]
    return {
        "source": clause.get("file_name"),
        "clause_path": clean_text_for_markdown(clean_clause_path(list(clause.get("heading_path") or []), str(clause.get("clause_title") or ""))),
        "snippet": clean_text_for_markdown(preview(clause.get("clause_text"), 360)),
        "needs_manual_review": False,
        "ignored_candidates": ignored_candidates[:10],
    }


def compliance_terms_for_task(task_name: str) -> list[str]:
    if is_personnel_task(task_name):
        return ["人员简历", "证书", "社保", "劳动关系", "任职证明", "项目负责人", "项目团队"]
    if is_company_performance_task(task_name):
        return ["业绩证明", "合同", "验收证明", "中标通知书", "客户证明", "时间范围", "金额"]
    if is_patent_task(task_name):
        return ["专利证书", "专利号", "申请号", "权利人", "专利状态"]
    if is_supplier_performance_task(task_name):
        return ["供应商绩效评价", "绩效评价证明", "评价结果"]
    if is_testing_acceptance_task(task_name):
        return ["测试验收基本要求", "测试验收内容", "验收测试", "验收材料"]
    return []


def is_testing_acceptance_task(task_name: str) -> bool:
    return "验收" in task_name or "测试" in task_name


def no_section_compliance_ref(task_name: str, ignored_candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "source": "",
        "clause_path": "",
        "snippet": missing_compliance_message(section_type_for_task(task_name)),
        "needs_manual_review": True,
        "ignored_candidates": list(ignored_candidates or [])[:10],
    }


def tender_ref_from_clause(
    clause: dict[str, Any],
    quality: str,
    terms: list[str],
    score_value: float,
    task_name: str,
    ignored_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if quality in {"heading_only", "invalid_clause"}:
        snippet = MISSING_TENDER_REF_MESSAGE
        interpretation = interpretation_for_task(task_name, clause, quality)
    else:
        snippet = preview(clean_text_for_markdown(clause.get("clause_text")), 520)
        if is_title_only_text(snippet):
            quality = "heading_only"
        interpretation = interpretation_for_task(task_name, clause, quality)
    return {
        "source_file_id": clause.get("file_id"),
        "source_file_name": clause.get("file_name"),
        "clause_id": clause.get("clause_id"),
        "clause_path": clean_text_for_markdown(clean_clause_path(list(clause.get("heading_path") or []), str(clause.get("clause_title") or ""))),
        "source_snippet": clean_text_for_markdown(snippet),
        "requirement_interpretation": interpretation,
        "evidence_quality": quality,
        "matched_terms": terms[:10],
        "match_score": score_value,
        "clause_type": clause.get("clause_type"),
        "section_type": section_type_for_task(task_name),
        "ignored_candidates": list(ignored_candidates or [])[:10],
    }


def ignored_clause_candidate(clause: dict[str, Any], reason: str, score_value: float) -> dict[str, Any]:
    return {
        "reason": reason,
        "filter_reason": reason,
        "source_file_id": clause.get("file_id"),
        "source_file_name": clause.get("file_name"),
        "candidate_clause_id": clause.get("clause_id"),
        "document_region": clause.get("document_region"),
        "section_path": list(clause.get("section_path") or clause.get("heading_path") or []),
        "clause_type": clause.get("clause_type"),
        "table_context": clause.get("table_context"),
        "clause_path": clean_text_for_markdown(clean_clause_path(list(clause.get("heading_path") or []), str(clause.get("clause_title") or ""))),
        "text_preview": clean_text_for_markdown(preview(clause.get("clause_text"), 160)),
        "snippet": clean_text_for_markdown(preview(clause.get("clause_text"), 160)),
        "match_score": score_value,
        "would_have_matched_by": [],
        "recoverable": True,
    }


REQUIREMENT_TYPE_LABELS = {
    "content_response": "内容响应要求",
    "parameter_response": "参数/指标响应要求",
    "deviation_response": "偏差响应要求",
    "format_form": "格式/表单要求",
    "personnel_requirement": "人员要求",
    "certificate_requirement": "证书/资质要求",
    "performance_proof": "业绩证明要求",
    "patent_proof": "专利/知识产权证明要求",
    "supplier_performance": "供应商绩效证明要求",
    "deliverable_requirement": "成果交付要求",
    "acceptance_requirement": "验收要求",
    "schedule_requirement": "进度/服务期限要求",
    "package_constraint": "当前包级约束",
    "operation_only": "系统/平台/流程操作要求",
}

OPERATION_ONLY_TERMS = [
    "开标时间和地点",
    "投标截止时间",
    "投标文件递交",
    "电子招标投标交易平台",
    "ECP",
    "CA证书",
    "电子钥匙",
    "投标工具",
    "平台解密",
    "逾期送达",
    "投标文件修改与撤回",
    "开标纪律",
    "唱标人",
    "记录人",
    "监标人",
    "投标保证金递交",
    "电子病毒",
    "弃标函",
    "邮寄工程量清单",
    "递交方式",
    "下载专区",
    "新手指引",
]


def build_response_requirement_refs(
    project: dict[str, Any],
    task_name: str,
    tender_ref: dict[str, Any],
    compliance_ref: dict[str, Any],
    clause_indexes: dict[str, list[dict[str, Any]]],
    ignored_out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    section_type = section_type_for_task(task_name)
    refs: list[dict[str, Any]] = []
    if is_usable_main_requirement_ref(tender_ref, section_type):
        refs.append(requirement_ref_from_tender_ref(tender_ref, requirement_type_for_tender_ref(tender_ref, task_name), task_name))
    refs.extend(find_structured_requirement_refs(task_name, section_type, clause_indexes, ignored_out))
    refs.extend(package_requirement_refs(project, task_name))
    return dedupe_requirement_refs(refs)


def is_usable_main_requirement_ref(ref: dict[str, Any], section_type: str) -> bool:
    if not ref or ref.get("evidence_quality") in {"not_found", "heading_only", "invalid_clause"}:
        return False
    text = " ".join([str(ref.get("clause_path") or ""), str(ref.get("source_snippet") or ""), str(ref.get("clause_type") or "")])
    if any(term in text for term in OPERATION_ONLY_TERMS):
        return False
    if any(term in text for term in ["服务类供应商", "重质量", "讲诚信", "投标函", "如我方中标", "履约担保", "中标代理服务费", "投标保证金", "投标函附表"]):
        return False
    if "10. 其他" in text:
        return False
    if section_type == "technical_overall" and any(term in text for term in ["资质业绩凭证单", "开标时间", "资格审查资料"]):
        return False
    if section_type == "supplier_performance" and any(term in text for term in ["十不准", "绩效评价人员", "招标采购相关从业人员"]):
        return False
    return True


def requirement_type_for_tender_ref(ref: dict[str, Any], task_name: str) -> str:
    clause_type = str(ref.get("clause_type") or "")
    text = " ".join([str(ref.get("clause_path") or ""), str(ref.get("source_snippet") or "")])
    if clause_type in {"team_summary_form", "personnel_resume_form"}:
        return "format_form"
    if clause_type == "personnel_requirement":
        return "personnel_requirement"
    if clause_type == "patent_requirement" or is_patent_task(task_name):
        return "patent_proof"
    if clause_type == "supplier_performance_requirement" or is_supplier_performance_task(task_name):
        return "supplier_performance"
    if clause_type == "schedule_requirement":
        return "schedule_requirement"
    if "验收" in text:
        return "acceptance_requirement"
    if any(term in text for term in ["成果", "提交", "交付"]):
        return "deliverable_requirement"
    if any(term in text for term in ["技术指标", "参数", "指标"]):
        return "parameter_response"
    if "偏差" in text:
        return "deviation_response"
    return "content_response"


def requirement_ref_from_tender_ref(ref: dict[str, Any], requirement_type: str, task_name: str) -> dict[str, Any]:
    quality = "标题级依据，需下钻" if is_title_level_requirement(ref) else quality_label(ref.get("evidence_quality"))
    return {
        "requirement_type": requirement_type,
        "source_file_id": ref.get("source_file_id") or "",
        "source_file_name": ref.get("source_file_name") or "",
        "source_type": str(ref.get("document_region") or ref.get("source_type") or "tender_source"),
        "section_path": ref.get("clause_path") or "",
        "original_excerpt": ref.get("source_snippet") or "",
        "applies_to": [section_type_for_task(task_name)],
        "usage": usage_for_requirement_type(requirement_type, task_name),
        "evidence_quality": quality,
        "must_quote": quality == "可直接引用",
        "clause_id": ref.get("clause_id") or "",
    }


def find_structured_requirement_refs(
    task_name: str,
    section_type: str,
    clause_indexes: dict[str, list[dict[str, Any]]],
    ignored_out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    clauses = [clause for values in clause_indexes.values() for clause in values]
    for clause in clauses:
        if clause.get("is_invalid") or clause.get("is_heading_only"):
            continue
        if is_empty_or_other_heading_clause(clause):
            continue
        guard_reason = clause_guard_reason(clause, section_type)
        if guard_reason:
            if ignored_out is not None:
                ignored_out.append(ignored_clause_candidate(clause, guard_reason, 0.0))
            continue
        if str(clause.get("clause_type") or "") == "operation_only":
            if ignored_out is not None:
                ignored_out.append(ignored_clause_candidate(clause, "operation_only_not_applicable_to_technical_section", 0.0))
            continue
        if str(clause.get("clause_type") or "") in {"business_quality_commitment", "price_file_or_bid_letter_commitment"}:
            if ignored_out is not None:
                reason = f"{clause.get('clause_type')}_not_applicable_to_technical_section"
                ignored_out.append(ignored_clause_candidate(clause, reason, 0.0))
            continue
        req_type = structured_requirement_type_for_clause(clause, task_name, section_type)
        if not req_type:
            continue
        refs.append(requirement_ref_from_clause(clause, req_type, task_name))
        if len(refs) >= 5:
            break
    return refs


def is_empty_or_other_heading_clause(clause: dict[str, Any]) -> bool:
    title = str(clause.get("clause_title") or clause.get("heading") or "")
    text = str(clause.get("clause_text") or "")
    path = " > ".join(str(item) for item in list(clause.get("heading_path") or clause.get("section_path") or []))
    if re.match(r"^10[.．、]\s*其他\s*$", title) and len(text) < 80:
        return True
    if "10. 其他" in title and "1.3.3 质量标准" in text:
        return True
    if (
        str(clause.get("document_region") or "") == "technical_file_format"
        and re.match(r"^1\.3\.3\s*质量标准", title)
        and "专项投标文件" in path
    ):
        return True
    return False


def structured_requirement_type_for_clause(clause: dict[str, Any], task_name: str, section_type: str) -> str:
    text = str(clause.get("clause_text") or "")
    clause_type = str(clause.get("clause_type") or "")
    if section_type in {"personnel", "team"}:
        if clause_type in {"team_summary_form", "personnel_resume_form"} or all(term in text for term in ["拟委任", "简历表"]):
            return "format_form"
        if clause_type == "personnel_requirement" and any(term in text for term in ["项目负责人", "主要人员", "职称", "学历", "社保", "工作年限", "工作经验"]):
            return "personnel_requirement"
    if is_company_performance_task(task_name) and clause_type == "company_performance_requirement":
        return "performance_proof"
    if is_patent_task(task_name) and ("受理发明专利" in text or clause_type == "patent_requirement"):
        return "patent_proof"
    if is_supplier_performance_task(task_name) and clause_type == "supplier_performance_requirement":
        return "supplier_performance"
    if is_writing_only_task(task_name):
        if clause_type in {"technical_solution_requirement", "technical_requirement", "service_requirement"}:
            return "content_response"
        if any(term in text for term in ["技术指标", "参数", "指标"]):
            return "parameter_response"
        if any(term in text for term in ["成果", "提交", "技术报告", "标准初稿"]):
            return "deliverable_requirement"
        if any(term in text for term in ["验收", "测试"]):
            return "acceptance_requirement"
    if "进度" in task_name and clause_type in {"schedule_requirement", "technical_solution_requirement"}:
        return "schedule_requirement"
    if section_type == "technical_overall" and clause_type in {"deviation_form_requirement", "response_format_requirement"} and any(term in text for term in ["技术文件", "技术偏差", "响应文件", "应答文件"]):
        return "format_form"
    return ""


def requirement_ref_from_clause(clause: dict[str, Any], requirement_type: str, task_name: str) -> dict[str, Any]:
    excerpt = preview(clean_text_for_markdown(clause.get("clause_text")), 360)
    quality = "标题级依据，需下钻" if is_title_only_text(excerpt) else "可直接引用"
    return {
        "requirement_type": requirement_type,
        "source_file_id": clause.get("file_id") or "",
        "source_file_name": clause.get("file_name") or "",
        "source_type": clause.get("document_region") or clause.get("source_type") or "tender_source",
        "section_path": clean_text_for_markdown(clean_clause_path(list(clause.get("heading_path") or []), str(clause.get("clause_title") or ""))),
        "original_excerpt": excerpt,
        "applies_to": [section_type_for_task(task_name)],
        "usage": usage_for_requirement_type(requirement_type, task_name),
        "evidence_quality": quality,
        "must_quote": quality == "可直接引用",
        "clause_id": clause.get("clause_id") or "",
    }


def package_requirement_refs(project: dict[str, Any], task_name: str) -> list[dict[str, Any]]:
    fields = ((project.get("tender_project_metadata") or {}).get("fields") or {}) if isinstance(project.get("tender_project_metadata"), dict) else {}
    refs: list[dict[str, Any]] = []
    if "进度" in task_name or "服务方案" in task_name or "实施方案" in task_name:
        service_period = first_field_value(fields, "service_period")
        if service_period:
            refs.append(package_constraint_ref("服务期限", service_period, fields.get("service_period"), task_name, "用于确定实施计划、阶段安排和交付时间边界。"))
    if is_company_performance_task(task_name):
        special = first_field_value(fields, "special_qualification_requirement")
        if special:
            refs.append(package_constraint_ref("专用资格要求", special, fields.get("special_qualification_requirement"), task_name, "用于确认本章公司业绩材料的时间范围和业绩类型要求。"))
    return refs


def package_constraint_ref(label: str, value: str, field: Any, task_name: str, usage: str) -> dict[str, Any]:
    field = field if isinstance(field, dict) else {}
    return {
        "requirement_type": "package_constraint",
        "source_file_id": field.get("source_file_id") or "",
        "source_file_name": field.get("source_file_name") or "tender_project_metadata",
        "source_type": field.get("source_type") or "tender_project_metadata",
        "section_path": label,
        "original_excerpt": f"{label}：{value}",
        "applies_to": [section_type_for_task(task_name)],
        "usage": usage,
        "evidence_quality": "可直接引用",
        "must_quote": True,
        "clause_id": "",
    }


def usage_for_requirement_type(requirement_type: str, task_name: str) -> str:
    if requirement_type == "format_form":
        return "用于确认本章节应准备的表单、附件或技术文件格式。"
    if requirement_type == "personnel_requirement":
        return "用于确认人员专业、年限、经验、证书或简历材料要求。"
    if requirement_type == "performance_proof":
        return "用于确认公司业绩证明材料口径。"
    if requirement_type == "patent_proof":
        return "用于确认专利、受理通知书或知识产权证明材料。"
    if requirement_type == "supplier_performance":
        return "用于确认供应商绩效评价证明材料。"
    if requirement_type == "schedule_requirement":
        return "用于确认实施计划、进度安排和服务期限。"
    if requirement_type == "deliverable_requirement":
        return "用于确认成果提交和交付物。"
    if requirement_type == "acceptance_requirement":
        return "用于确认测试验收和验收材料。"
    return "用于确认本章技术响应内容边界。"


def is_title_level_requirement(ref: dict[str, Any]) -> bool:
    text = str(ref.get("source_snippet") or "")
    path = str(ref.get("clause_path") or "")
    return is_title_only_text(text) or bool(re.fullmatch(r"\d+(?:\.\d+)*\s*对投标人的技术要求", text.strip())) or path.endswith("对投标人的技术要求")


def dedupe_requirement_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for ref in refs:
        if not ref.get("original_excerpt"):
            continue
        key = (
            str(ref.get("requirement_type") or ""),
            str(ref.get("source_file_id") or ""),
            str(ref.get("section_path") or ""),
            str(ref.get("original_excerpt") or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result[:8]


def is_title_only_text(value: Any) -> bool:
    text = clean_text_for_markdown(value)
    if len(text) > 45:
        return False
    return bool(re.match(r"^(\d+(?:\.\d+)*\.?\s*)?[\u4e00-\u9fa5A-Za-z（）() /]+$", text)) and not any(
        key in text for key in ["应", "须", "需", "要求", "提供", "包括", "负责", "完成"]
    )


def build_search_terms(task: dict[str, Any], task_name: str, score: dict[str, Any]) -> list[str]:
    raw_parts = [
        task_name,
        score.get("item_name"),
        score.get("score_rule"),
        score.get("raw_text"),
        task.get("generation_instruction"),
        " ".join(str(item) for item in list(task.get("related_requirements") or [])[:2]),
    ]
    terms: list[str] = []
    for part in raw_parts:
        terms.extend(extract_terms(str(part or "")))
    terms.extend(synonyms_for_task(task_name))
    return unique_terms(terms)


def extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.split(r"[\s,，。；;：:（）()【】\[\]、/]+", text):
        token = token.strip()
        if 2 <= len(token) <= 16 and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?分?", token):
            terms.append(token)
    for phrase in ["项目负责人", "项目团队", "人员结构", "服务方案", "工作进度", "保证措施", "工作基础", "项目经验", "专利", "绩效评价", "技术部分", "响应文件"]:
        if phrase in text:
            terms.append(phrase)
    return terms


def synonyms_for_task(task_name: str) -> list[str]:
    groups = [
        (["负责人", "项目经理"], ["项目负责人", "项目经理", "人员要求", "简历", "类似项目经验"]),
        (["团队", "人员结构"], ["项目团队", "人员要求", "团队组成", "专业分工"]),
        (["服务方案", "实施方案"], ["服务方案", "实施方案", "服务内容", "技术要求", "服务要求"]),
        (["进度"], ["进度安排", "服务期限", "实施计划", "保障措施"]),
        (["质量"], ["质量保障", "验收", "服务质量"]),
        (["风险", "沟通"], ["风险控制", "沟通机制", "安全", "保密"]),
        (["工作基础"], ["工作基础", "既有能力", "服务内容"]),
        (["业绩", "项目经验"], ["业绩", "项目经验", "案例", "合同", "验收证明"]),
        (["专利", "创新", "知识产权"], ["专利", "知识产权", "创新成果", "附件"]),
        (["绩效"], ["绩效评价", "履约评价", "验收"]),
    ]
    for triggers, values in groups:
        if any(trigger in task_name for trigger in triggers):
            return values
    return []


def rank_clause(clause: dict[str, Any], terms: list[str]) -> float:
    if clause.get("is_invalid"):
        return -999.0
    text = " ".join(
        [
            str(clause.get("clause_title") or ""),
            " ".join(str(item) for item in list(clause.get("heading_path") or [])),
            str(clause.get("clause_text") or ""),
        ]
    )
    score = 0.0
    for term in terms:
        if not term:
            continue
        if term in text:
            score += 2.0 if len(term) >= 4 else 1.0
    if clause.get("clause_type") in {"technical_requirement", "service_requirement", "personnel_requirement", "performance_requirement", "response_file_requirement", "appendix"}:
        score += 1.0
    if any(bad in text for bad in ["特别声明", "免责条款", "解释权"]):
        score -= 20.0
    if any(term in terms for term in ["服务方案", "实施方案"]):
        if "服务方案" in text or "实施方案" in text:
            score += 6.0
        if any(good in text for good in ["招标要求", "投标人的技术要求", "概述", "投标方职责", "研究内容", "技术指标", "技术培训", "验收测试", "质量保证", "售后服务", "服务内容", "服务要求"]):
            score += 4.0
    if any(term in terms for term in ["进度安排", "实施计划", "保障措施"]):
        if any(good in text for good in ["项目实施", "技术服务计划", "投标方职责", "测试验收", "质量保证", "售后服务", "服务期限"]):
            score += 4.0
    if clause.get("is_heading_only"):
        score -= 2.0
    return score


def build_score_ref(task: dict[str, Any], score: dict[str, Any], material: dict[str, Any]) -> dict[str, Any]:
    return {
        "score_item_id": score.get("id") or first_text(task.get("related_score_item_ids")),
        "score_item": score.get("item_name") or task.get("task_name") or infer_task_name(task),
        "score": score.get("score"),
        "score_range": normalize_score_range(material.get("score_range") or score.get("score_range") or score.get("score")),
        "raw_score_text": score.get("raw_text") or "",
        "score_description": score.get("score_rule") or first_text(task.get("related_requirements")) or "",
        "score_source_file": score.get("source_template_name") or "score_items",
    }


def classify_evidence(
    item: dict[str, Any],
    material_profile: dict[str, Any] | None = None,
    personnel_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(item)
    if is_stale_or_mock_summary(result):
        result["material_type"] = "unusable_summary"
        result["fact_type"] = "stale_or_mock_summary"
        result["evidence_strength"] = "unusable"
        result["usable_as"] = "stale_or_mock_summary / 不可直接使用"
        result["applicability_reason"] = "旧 Mock 泛化摘要只能说明曾有汇总文本，不能触发专利、业绩、合同、绩效或资质 confirmed。"
        return result
    if isinstance(material_profile, dict) and material_profile.get("material_group"):
        result = apply_material_profile_to_evidence(result, material_profile)
    if isinstance(personnel_profile, dict) and personnel_profile:
        result["personnel_profile"] = personnel_profile
        if personnel_profile.get("department"):
            result["person_department"] = personnel_profile.get("department")
            result["person_department_confirm_status"] = personnel_profile.get("department_confirm_status")
    if is_known_qualification_file(result):
        result["material_type"] = "company_qualification"
        result["fact_type"] = "qualification_certificate"
        result["evidence_strength"] = "confirmed"
        result["usable_as"] = "企业资质证书 / 代理资质证明"
        result["qualification_certificates"] = list(QUALIFICATION_CERTIFICATE_NAMES)
        result["snippet"] = "；".join(QUALIFICATION_CERTIFICATE_NAMES)
    marker_text = " ".join(str(result.get(key) or "") for key in ["source", "title", "snippet", "fact_type"]).lower()
    material_type = str(result.get("material_type") or "")
    fact_type = str(result.get("fact_type") or "")
    confidence = safe_float(result.get("confidence"))
    if "mock word解析文本".lower() in marker_text or "mock_source" in marker_text or "mock" in marker_text:
        result["evidence_strength"] = "mock_source"
        result["usable_as"] = "不可直接使用材料"
    elif material_type == "generic_document" or "weak" in fact_type or "weak" in marker_text or (confidence is not None and confidence < 0.75):
        result["evidence_strength"] = "weak_evidence"
        result["usable_as"] = "弱证据 / 需人工复核"
    else:
        result["evidence_strength"] = "confirmed"
        result["usable_as"] = "已确认材料"
    return result


def apply_material_profile_to_evidence(item: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    material_types = [str(value) for value in list(profile.get("material_types") or [])]
    group = str(profile.get("material_group") or "")
    result["material_profile"] = {
        "material_group": group,
        "material_types": material_types,
        "classification_source": profile.get("classification_source"),
        "not_usable_for": list(profile.get("not_usable_for") or []),
    }
    result["material_group"] = group
    result["taxonomy_material_types"] = material_types
    result["original_material_type"] = result.get("material_type")
    result["original_fact_type"] = result.get("fact_type")
    if group == "personnel":
        result["material_type"] = personnel_material_type_for_evidence(result, material_types)
    elif group == "company_qualification":
        result["material_type"] = "company_qualification"
        result["fact_type"] = first_profile_type(material_types, "qualification_certificate")
    elif group == "company_performance":
        result["material_type"] = "company_performance"
        result["fact_type"] = first_profile_type(material_types, "performance_case")
    elif group == "winning_case":
        result["material_type"] = "winning_case"
        result["fact_type"] = first_profile_type(material_types, "winning_notice")
    elif group == "intellectual_property":
        result["material_type"] = "intellectual_property"
        result["fact_type"] = first_profile_type(material_types, "patent_record")
    elif group == "supplier_performance":
        result["material_type"] = "supplier_performance"
        result["fact_type"] = first_profile_type(material_types, "supplier_performance_evaluation")
    elif group == "company_product":
        result["material_type"] = "company_product"
        result["fact_type"] = first_profile_type(material_types, "solution_document")
    elif group == "technical_writing_asset":
        result["material_type"] = "technical_writing_asset"
        result["fact_type"] = first_profile_type(material_types, "service_plan_template")
    elif group == "tender_source":
        result["material_type"] = "tender_source"
        result["fact_type"] = first_profile_type(material_types, "tender_document")
    elif group == "business_compliance":
        result["material_type"] = "business_compliance"
        result["fact_type"] = first_profile_type(material_types, "credit_report")
    elif group == "tender_source":
        result["material_type"] = "tender_source"
        result["fact_type"] = first_profile_type(material_types, "tender_main")
    return result


def personnel_material_type_for_evidence(item: dict[str, Any], material_types: list[str]) -> str:
    marker = " ".join(
        str(item.get(key) or "")
        for key in ["material_type", "fact_type", "source", "title", "snippet"]
    ).lower()
    if "certificate" in marker or "证书" in marker or "璇佷功" in marker:
        return "personal_certificate" if "personal_certificate" in material_types else "person_resume"
    if "project_experience" in marker or "项目经验" in marker or "椤圭洰缁忛獙" in marker:
        return "person_project_experience" if "person_project_experience" in material_types else "person_resume"
    return "person_resume" if "person_resume" in material_types else first_profile_type(material_types, "person_resume")


def first_profile_type(material_types: list[str], fallback: str) -> str:
    return material_types[0] if material_types else fallback


def build_material_basis(
    task_name: str,
    evidence_refs: list[dict[str, Any]],
    upstream_missing: list[dict[str, Any]],
    department_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if is_personnel_task(task_name):
        candidates = aggregate_person_candidates(evidence_refs, department_match)
        confirmed = [candidate_to_evidence(item) for item in candidates if item.get("relevance_to_project") == "matched"]
        candidate_refs = [candidate_to_evidence(item) for item in candidates if item.get("relevance_to_project") != "matched"]
        missing = list(upstream_missing)
        if not candidates:
            missing.append(missing_item("person_resume", "未找到可聚合的候选人员简历或证书材料。", "补充候选项目负责人/团队成员简历、技能和证书材料。"))
        missing.extend(missing_department_personnel_items(candidates, department_match))
        return {
            "groups": {"confirmed": confirmed, "candidate": candidate_refs, "weak": [], "unusable": []},
            "person_candidates": candidates,
            "missing_materials": missing,
            "material_note": "",
        }

    if is_writing_only_task(task_name):
        return {
            "groups": {"confirmed": [], "candidate": [], "weak": [], "unusable": []},
            "person_candidates": [],
            "missing_materials": list(upstream_missing),
            "material_note": "本章节主要依据招标条款和评分点撰写，当前无必须引用的企业证明材料；需后续补充项目化方案内容。",
        }

    confirmed: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    unusable: list[dict[str, Any]] = []
    missing = list(upstream_missing)

    for item in evidence_refs:
        bucket, reason = classify_evidence_for_task(task_name, item)
        item = dict(item)
        item["applicability_reason"] = reason
        if bucket == "confirmed":
            item["evidence_strength"] = "confirmed"
            item["usable_as"] = "已确认材料"
            confirmed.append(item)
        elif bucket == "candidate":
            item["evidence_strength"] = "candidate_needs_review"
            item["usable_as"] = "候选材料 / 需人工复核"
            candidate.append(item)
        elif bucket == "weak":
            item["evidence_strength"] = "weak_evidence"
            item["usable_as"] = "弱证据 / 需人工复核"
            weak.append(item)
        else:
            item["evidence_strength"] = "unusable"
            item["usable_as"] = "不可替代材料"
            unusable.append(item)

    if is_company_performance_task(task_name) and not confirmed:
        missing.extend(
            [
                missing_item("company_contract", "未找到明确公司业绩合同。", "补充公司项目业绩合同或关键页。"),
                missing_item("acceptance_report", "未找到验收证明。", "补充验收报告、验收单或客户盖章证明。"),
                missing_item("winning_notice_or_customer_certificate", "未找到中标通知书或客户证明。", "补充中标通知书、客户证明或绩效证明。"),
            ]
        )
    if is_patent_task(task_name) and not confirmed:
        missing.append(missing_item("patent_certificate", "未找到包含专利名称、专利号/申请号、权利人和状态的明确专利证明。", "补充专利证书、专利清单或官方查询记录。"))
    if is_supplier_performance_task(task_name) and not confirmed:
        missing.append(missing_item("supplier_performance_evaluation", "未找到最近一年度供应商绩效评价结果证明。", "补充平台绩效评价截图、招标人评价结果或供应商绩效证明。"))

    return {
        "groups": {"confirmed": confirmed, "candidate": candidate, "weak": weak, "unusable": unusable},
        "person_candidates": [],
        "missing_materials": missing,
        "material_note": "",
    }


def classify_evidence_for_task(task_name: str, item: dict[str, Any]) -> tuple[str, str]:
    text = evidence_text(item)
    material_type = str(item.get("material_type") or "")
    fact_type = str(item.get("fact_type") or "")
    if item.get("evidence_strength") in {"mock_source", "unusable"} or fact_type == "stale_or_mock_summary":
        return "unusable", item.get("applicability_reason") or "Mock/过期泛化摘要只能作为线索，不能作为确认材料。"
    if is_company_performance_task(task_name):
        if material_type in {"company_qualification", "qualification_certificate"} or fact_type == "qualification_certificate":
            return "unusable", "企业资质证书不能替代公司业绩合同、验收证明、中标通知书或客户证明。"
        if material_type in {"person_resume", "personal_certificate"} or "resume" in fact_type or "简历" in text:
            return "weak", "人员项目经验不能替代公司业绩证明。"
        if any(key in fact_type for key in ["company_contract", "acceptance_report", "winning_notice", "customer_certificate", "performance_certificate"]):
            return "confirmed", "明确公司业绩证明材料。"
        if has_company_performance_proof(text) and material_type != "generic_document":
            return "confirmed", "文本包含公司业绩证明要素。"
        return "weak", "未识别到公司业绩合同、验收、中标或客户证明要素。"
    if is_patent_task(task_name):
        if material_type in {"company_qualification", "qualification_certificate"} or fact_type == "qualification_certificate":
            return "unusable", "资质+代理证书是企业资质材料，不是专利证明。"
        if has_patent_identifier(text):
            return "confirmed", "文本包含专利名称或专利号/申请号等明确要素。"
        if "专利" in text:
            return "weak", "仅出现专利泛化表述，缺少专利号、权利人或状态。"
        return "unusable", "材料类型与专利证明不匹配。"
    if is_supplier_performance_task(task_name):
        if material_type in {"company_qualification", "qualification_certificate"} or fact_type == "qualification_certificate":
            return "unusable", "企业资质证书不能替代供应商绩效评价结果证明。"
        if any(key in text for key in ["绩效评价", "评价结果", "供应商绩效", "履约评价"]) and material_type not in {"person_resume", "personal_certificate"}:
            return "confirmed", "文本包含供应商绩效评价要素。"
        return "unusable", "简历、资质证书或项目经验不能用于供应商绩效评价。"
    if is_work_foundation_task(task_name):
        if material_type in {"person_resume", "personal_certificate"} or "person_project_experience" in fact_type or "resume" in fact_type:
            return "weak", "人员材料可作能力线索，但不能直接证明工作基础。"
        if material_type == "company_qualification":
            return "candidate", "资质证书可作为能力背景线索，但不能直接证明产品化、科技成果或专题研究。"
        if any(key in text for key in ["产品", "科技成果", "专题研究", "软著", "软件著作权", "专利", "研发项目"]):
            return "candidate", "可作为工作基础候选材料，需核验证明力度。"
    if material_type == "generic_document" or item.get("evidence_strength") == "weak_evidence":
        return "weak", "generic_document 或弱证据需人工复核。"
    return "confirmed", "材料类型与章节需求初步匹配。"


def classify_evidence_for_task(task_name: str, item: dict[str, Any]) -> tuple[str, str]:
    text = evidence_text(item)
    material_type = str(item.get("material_type") or "")
    fact_type = str(item.get("fact_type") or "")
    material_group = str(item.get("material_group") or (item.get("material_profile") or {}).get("material_group") or "")
    taxonomy_types = set(str(value) for value in list(item.get("taxonomy_material_types") or []))
    if material_group == "tender_source" or material_type == "tender_source":
        return "unusable", "招标文件正文只能作为招标来源依据，不进入企业证明材料池。"
    if item.get("evidence_strength") in {"mock_source", "unusable"} or fact_type == "stale_or_mock_summary":
        return "unusable", item.get("applicability_reason") or "Mock/过期泛化摘要只能作为线索，不能作为确认材料。"

    if is_company_performance_task(task_name):
        if material_group == "personnel":
            return "weak", "人员项目经验只能作为人员经历线索，不能替代公司业绩合同、验收证明、中标通知书或客户证明。"
        if material_group == "company_qualification":
            return "unusable", "企业资质证书不能替代公司业绩材料。"
        if material_group == "company_performance":
            if taxonomy_types.intersection({"company_contract", "acceptance_report", "customer_certificate", "performance_case", "service_certificate"}):
                return "confirmed", "material_profile 标记为公司业绩材料。"
            return "candidate", "公司业绩材料缺少合同、验收或客户证明细类，需人工复核。"
        if material_group == "winning_case":
            return "candidate", "中标成功案例可作为线索，但不等同于完整公司业绩；仍需合同、验收或客户证明。"
        if material_group and material_group != "unknown":
            return "unusable", f"{material_group} 不能替代公司业绩证明。"
        if material_type in {"company_qualification", "qualification_certificate"} or fact_type == "qualification_certificate":
            return "unusable", "企业资质证书不能替代公司业绩合同、验收证明、中标通知书或客户证明。"
        if material_type in {"person_resume", "personal_certificate"} or "resume" in fact_type or "简历" in text:
            return "weak", "人员项目经验不能替代公司业绩证明。"
        if any(key in fact_type for key in ["company_contract", "acceptance_report", "winning_notice", "customer_certificate", "performance_certificate"]):
            return "confirmed", "明确公司业绩证明材料。"
        if has_company_performance_proof(text) and material_type != "generic_document":
            return "confirmed", "文本包含公司业绩证明要素。"
        return "weak", "未识别到公司业绩合同、验收、中标或客户证明要素。"

    if is_patent_task(task_name):
        if material_group == "intellectual_property":
            if taxonomy_types.intersection({"patent_certificate", "patent_record"}) and has_patent_identifier(text):
                return "confirmed", "material_profile 标记为知识产权材料，且文本包含专利明确要素。"
            return "weak", "知识产权材料缺少专利名称、专利号/申请号、权利人或状态，需人工复核。"
        if material_group and material_group != "unknown":
            return "unusable", f"{material_group} 不能替代专利证书或官方专利记录。"
        if material_type in {"company_qualification", "qualification_certificate"} or fact_type == "qualification_certificate":
            return "unusable", "资质证书是企业资质材料，不是专利证明。"
        if has_patent_identifier(text):
            return "confirmed", "文本包含专利名称或专利号/申请号等明确要素。"
        if "专利" in text:
            return "weak", "仅出现专利泛化表述，缺少专利号、权利人或状态。"
        return "unusable", "材料类型与专利证明不匹配。"

    if is_supplier_performance_task(task_name):
        if material_group == "supplier_performance":
            return "confirmed", "material_profile 标记为供应商绩效评价材料。"
        if material_group and material_group != "unknown":
            return "unusable", f"{material_group} 不能替代供应商绩效评价结果证明。"
        if material_type in {"company_qualification", "qualification_certificate"} or fact_type == "qualification_certificate":
            return "unusable", "企业资质证书不能替代供应商绩效评价结果证明。"
        if any(key in text for key in ["绩效评价", "评价结果", "供应商绩效", "履约评价"]) and material_type not in {"person_resume", "personal_certificate"}:
            return "confirmed", "文本包含供应商绩效评价要素。"
        return "unusable", "简历、资质证书或项目经验不能用于供应商绩效评价。"

    if is_work_foundation_task(task_name):
        if material_group in {"company_product", "intellectual_property", "technical_writing_asset"}:
            return "candidate", "material_profile 与工作基础可用材料组匹配，需核验证明力度。"
        if material_group == "personnel":
            return "weak", "人员材料仅保留为调试线索，不进入工作基础主体。"
        if material_group == "company_qualification":
            return "unusable", "企业资质不能直接证明产品化成果、科技成果、专题研究、软著或研发项目。"
        if material_group and material_group != "unknown":
            return "unusable", f"{material_group} 不属于工作基础章节可用材料组。"
        if material_type in {"person_resume", "personal_certificate"} or "person_project_experience" in fact_type or "resume" in fact_type:
            return "weak", "人员材料可作能力线索，但不能直接证明工作基础。"
        if material_type == "company_qualification":
            return "unusable", "资质证书不能直接证明产品化、科技成果或专题研究。"
        if any(key in text for key in ["产品", "科技成果", "专题研究", "软著", "软件著作权", "专利", "研发项目"]):
            return "candidate", "可作为工作基础候选材料，需核验证明力度。"

    if material_group == "personnel" and not is_personnel_task(task_name):
        return "weak", "人员材料只适用于项目负责人或团队章节。"
    if material_group == "company_qualification":
        return "unusable", "企业资质不能替代业绩、专利或绩效等其他证明材料。"
    if material_type == "generic_document" or item.get("evidence_strength") == "weak_evidence":
        return "weak", "generic_document 或弱证据需人工复核。"
    return "confirmed", "材料类型与章节需求初步匹配。"


def aggregate_person_candidates(evidence_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in evidence_refs:
        if item.get("material_group") != "personnel" and item.get("material_type") not in {"person_resume", "personal_certificate", "person_project_experience"}:
            continue
        key = str(item.get("file_id") or item.get("title") or "unknown")
        candidate = grouped.setdefault(
            key,
            {
                "name": extract_person_name(item) or str(item.get("person") or item.get("title") or key),
                "file_id": item.get("file_id"),
                "work_years": "",
                "skills": [],
                "certificates": [],
                "project_experience": [],
                "relevance_to_project": "candidate_needs_review",
                "match_reason": "",
                "missing_or_weak_points": [],
                "suggested_usage": "暂不用于本章节",
                "source_refs": [],
            },
        )
        text = evidence_text(item)
        candidate["name"] = extract_person_name(item) or candidate["name"]
        if not candidate["work_years"]:
            candidate["work_years"] = extract_first(r"工作年限[：:]?\s*([0-9一二三四五六七八九十]+年)", text)
        candidate["skills"].extend(extract_list_after("技能", text))
        if item.get("material_type") == "personal_certificate":
            candidate["certificates"].append(str(item.get("title") or preview(text, 40)))
        candidate["certificates"].extend(extract_list_after("证书", text))
        candidate["project_experience"].extend(extract_list_after("项目经验", text))
        candidate["source_refs"].append(item)

    for candidate in grouped.values():
        blob = " ".join(
            [
                " ".join(candidate.get("skills") or []),
                " ".join(candidate.get("certificates") or []),
                " ".join(candidate.get("project_experience") or []),
                " ".join(evidence_text(item) for item in candidate.get("source_refs") or []),
            ]
        )
        strong_hits = [keyword for keyword in STRONG_PROJECT_RELEVANCE_KEYWORDS if keyword.lower() in blob.lower()]
        weak_hits = [keyword for keyword in WEAK_PROJECT_RELEVANCE_KEYWORDS if keyword.lower() in blob.lower()]
        if strong_hits:
            candidate["relevance_to_project"] = "matched"
            candidate["match_reason"] = "命中强相关项目关键词：" + "、".join(strong_hits[:6])
            candidate["suggested_usage"] = "项目负责人候选 / 团队成员候选"
        elif weak_hits:
            candidate["match_reason"] = "仅命中弱相关关键词：" + "、".join(weak_hits[:6]) + "；需人工确认是否匹配本项目。"
            candidate["missing_or_weak_points"].append("仅有弱相关线索，不能直接确认项目负责人或核心团队角色。")
            candidate["suggested_usage"] = "团队成员候选"
        else:
            candidate["match_reason"] = "未发现区块链、工业数据、数据安全、隐私保护、访问控制、监管、智能合约、安全分析、性能测试、研发负责人等强相关线索。"
            candidate["missing_or_weak_points"].append("需人工确认该人员经历与本项目技术方向的相关性。")
            candidate["suggested_usage"] = "暂不用于本章节"
        candidate["skills"] = unique_terms([str(item) for item in candidate.get("skills") or []])[:12]
        candidate["certificates"] = unique_terms([str(item) for item in candidate.get("certificates") or []])[:12]
        candidate["project_experience"] = unique_terms([str(item) for item in candidate.get("project_experience") or []])[:8]
    return list(grouped.values())


def aggregate_person_candidates(
    evidence_refs: list[dict[str, Any]],
    department_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    department_match = department_match or {}
    primary_departments = set(str(value) for value in list(department_match.get("primary_departments") or []))
    secondary_departments = set(str(value) for value in list(department_match.get("secondary_departments") or []))
    grouped: dict[str, dict[str, Any]] = {}
    for item in evidence_refs:
        if item.get("material_group") != "personnel" and item.get("material_type") not in {"person_resume", "personal_certificate", "person_project_experience"}:
            continue
        profile = item.get("personnel_profile") if isinstance(item.get("personnel_profile"), dict) else {}
        key = str(item.get("file_id") or item.get("title") or "unknown")
        candidate = grouped.setdefault(
            key,
            {
                "name": profile.get("person_name") or extract_person_name(item) or str(item.get("person") or item.get("title") or key),
                "file_id": item.get("file_id"),
                "department": profile.get("department") or "",
                "department_confirm_status": profile.get("department_confirm_status") or "unknown",
                "department_match_status": "needs_review",
                "work_years": profile.get("work_years") or "",
                "skills": [],
                "certificates": [],
                "project_experience": [],
                "relevance_to_project": "candidate_needs_review",
                "match_reason": "",
                "missing_or_weak_points": [],
                "suggested_usage": "暂不用于本章节",
                "source_refs": [],
            },
        )
        text = evidence_text(item)
        candidate["name"] = profile.get("person_name") or extract_person_name(item) or candidate["name"]
        if profile.get("department") and not candidate.get("department"):
            candidate["department"] = profile.get("department")
        if profile.get("department_confirm_status") and candidate.get("department_confirm_status") == "unknown":
            candidate["department_confirm_status"] = profile.get("department_confirm_status")
        if not candidate["work_years"]:
            candidate["work_years"] = profile.get("work_years") or extract_first(r"工作年限[：:]?\s*([0-9一二三四五六七八九十]+年)", text)
        candidate["skills"].extend(list(profile.get("skill_tags") or []))
        candidate["skills"].extend(extract_list_after("技能", text))
        if item.get("material_type") == "personal_certificate":
            candidate["certificates"].append(str(item.get("title") or preview(text, 40)))
        candidate["certificates"].extend(list(profile.get("certificate_tags") or []))
        candidate["certificates"].extend(extract_list_after("证书", text))
        candidate["project_experience"].extend(list((profile.get("matched_resume_fields") or {}).get("project_experience") or [])[:6])
        candidate["project_experience"].extend(extract_list_after("项目经验", text))
        candidate["source_refs"].append(item)

    for candidate in grouped.values():
        candidate["skills"] = unique_terms([str(item) for item in candidate.get("skills") or []])[:12]
        candidate["certificates"] = unique_terms([str(item) for item in candidate.get("certificates") or []])[:12]
        candidate["project_experience"] = unique_terms([preview(item, 120) for item in candidate.get("project_experience") or []])[:8]
        blob = " ".join(
            [
                " ".join(candidate.get("skills") or []),
                " ".join(candidate.get("certificates") or []),
                " ".join(candidate.get("project_experience") or []),
                " ".join(evidence_text(item) for item in candidate.get("source_refs") or []),
            ]
        )
        strong_hits = [keyword for keyword in STRONG_PROJECT_RELEVANCE_KEYWORDS if keyword.lower() in blob.lower()]
        weak_hits = [keyword for keyword in WEAK_PROJECT_RELEVANCE_KEYWORDS if keyword.lower() in blob.lower()]
        department = str(candidate.get("department") or "")
        if department in primary_departments:
            candidate["department_match_status"] = "primary_department_candidate"
        elif department in secondary_departments:
            candidate["department_match_status"] = "secondary_department_candidate"
        elif department:
            candidate["department_match_status"] = "cross_department_candidate"
            candidate["missing_or_weak_points"].append(f"所属部门为{department}，不在本项目建议主责/协同部门内。")
        else:
            candidate["department_match_status"] = "needs_review"
            candidate["missing_or_weak_points"].append("未确认所属部门。")

        if strong_hits and candidate["department_match_status"] in {"primary_department_candidate", "secondary_department_candidate"}:
            candidate["relevance_to_project"] = "matched"
            candidate["match_reason"] = f"{candidate['department_match_status']}，并命中强相关项目关键词：" + "、".join(strong_hits[:6])
            candidate["suggested_usage"] = "项目负责人候选 / 团队成员候选"
        elif weak_hits:
            candidate["match_reason"] = "仅命中弱相关关键词：" + "、".join(weak_hits[:6]) + "；需人工确认是否匹配本项目。"
            candidate["missing_or_weak_points"].append("仅有弱相关线索，不能直接确认项目负责人或核心团队角色。")
            candidate["suggested_usage"] = "跨部门候选 / 需复核" if candidate["department_match_status"] == "cross_department_candidate" else "团队成员候选 / 需复核"
        else:
            candidate["match_reason"] = "未发现区块链、工业数据、数据安全、隐私保护、访问控制、监管、智能合约、安全分析、性能测试、研发负责人等强相关线索。"
            candidate["missing_or_weak_points"].append("需人工确认该人员经历与本项目技术方向的相关性。")
            if candidate["department_match_status"] == "cross_department_candidate":
                candidate["suggested_usage"] = "跨部门候选 / 需复核"
    return list(grouped.values())


def missing_department_personnel_items(
    candidates: list[dict[str, Any]],
    department_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    department_match = department_match or {}
    weak_departments = set(str(value) for value in list(department_match.get("weak_candidate_departments") or []))
    candidate_departments = [
        str(item.get("department") or "")
        for item in list(department_match.get("candidate_departments") or [])
        if isinstance(item, dict) and str(item.get("department") or "") not in weak_departments
    ]
    required_departments = (
        list(department_match.get("primary_departments") or [])
        + list(department_match.get("secondary_departments") or [])
        + candidate_departments
    )
    existing_departments = {str(item.get("department") or "") for item in candidates}
    missing: list[dict[str, Any]] = []
    for department in unique_terms([str(value) for value in required_departments]):
        if department and department not in existing_departments:
            missing.append(
                missing_item(
                    "person_resume",
                    f"缺少 {department} 已确认人员简历。",
                    f"建议补充 {department} 人员简历、证书和与本项目相关的项目经验。",
                )
            )
    return missing


def candidate_to_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": candidate.get("file_id"),
        "title": candidate.get("name"),
        "material_type": "person_candidate",
        "fact_type": "aggregated_person_candidate",
        "source": "material_analysis.person_resume/personal_certificate",
        "snippet": f"{candidate.get('department') or '-'} / {candidate.get('department_match_status') or '-'} / {candidate.get('match_reason') or '-'}",
        "evidence_strength": "confirmed" if candidate.get("relevance_to_project") == "matched" else "candidate_needs_review",
        "usable_as": "候选人员",
    }


def is_personnel_task(task_name: str) -> bool:
    return any(key in task_name for key in ["项目负责人", "项目经理", "项目团队", "人员结构", "团队人员"])


def is_company_performance_task(task_name: str) -> bool:
    return any(key in task_name for key in ["业绩", "项目经验"]) and "人员" not in task_name


def is_patent_task(task_name: str) -> bool:
    return any(key in task_name for key in ["专利", "知识产权", "创新成果"])


def is_supplier_performance_task(task_name: str) -> bool:
    return "绩效评价" in task_name or "绩效" in task_name


def is_work_foundation_task(task_name: str) -> bool:
    return "工作基础" in task_name


def is_writing_only_task(task_name: str) -> bool:
    return any(key in task_name for key in ["服务方案", "工作进度", "保证措施", "总体评价", "技术总体评价", "应答文件技术部分总体评价"])


def has_company_performance_proof(text: str) -> bool:
    if "简历" in text or "人员简历" in text:
        return False
    return any(key in text for key in ["合同编号", "合同名称", "验收报告", "验收证明", "中标通知书", "客户证明", "业绩证明"])


def has_patent_identifier(text: str) -> bool:
    return bool(re.search(r"(专利号|申请号|公开号|授权公告号|ZL\d|CN\d|权利人)", text)) and "专利" in text


def is_known_qualification_file(item: dict[str, Any]) -> bool:
    text = evidence_text(item)
    return str(item.get("file_id") or "") in QUALIFICATION_FILE_IDS or (
        "资质+代理证书.docx" in text
        and any(key in text for key in ["ISO 9001", "ISO9001", "ITSS", "CS3", "CCRC", "代理资质证明"])
        and "Mock Word解析文本" not in text
    )


def is_stale_or_mock_summary(item: dict[str, Any]) -> bool:
    text = evidence_text(item)
    if str(item.get("file_id") or "") in STALE_SUMMARY_FILE_IDS:
        return True
    return "Mock Word解析文本" in text and "包含营业执照" in text and "等资料" in text


def missing_item(material_type: str, reason: str, suggested_action: str) -> dict[str, str]:
    return {"material_type": material_type, "reason": reason, "suggested_action": suggested_action}


def evidence_text(item: dict[str, Any]) -> str:
    certs = " ".join(str(value) for value in list(item.get("qualification_certificates") or []))
    return " ".join(str(item.get(key) or "") for key in ["title", "source", "fact_type", "snippet", "person"]) + " " + certs


def extract_person_name(item: dict[str, Any]) -> str:
    text = evidence_text(item)
    match = re.search(r"姓名[：: ]+([\u4e00-\u9fa5A-Za-z·]{2,20})", text)
    if match:
        return match.group(1)
    person = str(item.get("person") or "").strip()
    if person:
        return person
    title = str(item.get("title") or "")
    if title.endswith((".docx", ".doc", ".pdf")):
        title = re.sub(r"\.(docx?|pdf)$", "", title, flags=re.IGNORECASE)
    return title


def extract_first(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def extract_list_after(label: str, text: str) -> list[str]:
    match = re.search(label + r"[：:]\s*([^；;。\\n]+)", text)
    if not match:
        return []
    return [item.strip() for item in re.split(r"[,，、/ ]+", match.group(1)) if item.strip()]


def group_evidence(evidence_refs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "confirmed": [item for item in evidence_refs if item.get("evidence_strength") == "confirmed"],
        "weak": [item for item in evidence_refs if item.get("evidence_strength") == "weak_evidence"],
        "mock_source": [item for item in evidence_refs if item.get("evidence_strength") == "mock_source"],
    }


def build_summary(sections: list[dict[str, Any]], source_files: list[dict[str, Any]], clause_indexes: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    evidence_refs = collect_material_basis_evidence_refs(sections)
    tender_refs = [section["tender_requirement_ref"] for section in sections]
    missing_material_items = collect_missing_materials(sections)
    manual_review_items = collect_manual_review_items(sections)
    structured_summary = summarize_structured_clauses(clause_indexes or {})
    response_refs = [ref for section in sections for ref in list(section.get("response_requirement_refs") or [])]
    summary = {
        "technical_sections": len(sections),
        "response_requirement_refs": len(response_refs),
        "confirmed_response_requirement_refs": len([ref for ref in response_refs if ref.get("evidence_quality") == "可直接引用"]),
        "review_response_requirement_refs": len([ref for ref in response_refs if ref.get("evidence_quality") != "可直接引用"]),
        "score_only_sections": len([section for section in sections if not list(section.get("response_requirement_refs") or [])]),
        "valid_tender_requirement_refs": len([item for item in tender_refs if item.get("evidence_quality") == "confirmed_clause"]),
        "invalid_tender_requirement_refs": len([item for item in tender_refs if item.get("evidence_quality") in {"invalid_clause", "heading_only"}]),
        "missing_valid_tender_requirement_refs": len([item for item in tender_refs if item.get("evidence_quality") == "not_found"]),
        "confirmed_tender_requirement_refs": len([item for item in tender_refs if item.get("evidence_quality") == "confirmed_clause"]),
        "weak_tender_requirement_refs": len([item for item in tender_refs if item.get("evidence_quality") in {"weak_clause", "heading_only", "invalid_clause"}]),
        "missing_tender_requirement_refs": len([item for item in tender_refs if item.get("evidence_quality") == "not_found"]),
        "filtered_mismatch_tender_refs": len([item for item in tender_refs if item.get("ignored_candidates")]),
        "score_refs": len([section for section in sections if section.get("score_ref")]),
        "confirmed_evidence_refs": len([item for item in evidence_refs if item.get("evidence_strength") == "confirmed"]),
        "candidate_evidence_refs": len([item for item in evidence_refs if item.get("evidence_strength") == "candidate_needs_review"]),
        "weak_evidence_refs": len([item for item in evidence_refs if item.get("evidence_strength") == "weak_evidence"]),
        "unusable_evidence_refs": len([item for item in evidence_refs if item.get("evidence_strength") in {"mock_source", "unusable"}]),
        "mock_source_refs": len([item for item in evidence_refs if item.get("evidence_strength") == "mock_source"]),
        "missing_material_items": len(missing_material_items),
        "manual_review_items": len(manual_review_items),
        "person_candidates": sum(len(section.get("person_candidates") or []) for section in sections),
        "company_performance_confirmed": len([item for section in sections if is_company_performance_task(str(section.get("task_name") or "")) for item in section["material_basis"]["groups"].get("confirmed", [])]),
        "company_performance_missing": len([item for item in missing_material_items if item["missing"].get("material_type") in {"company_contract", "acceptance_report", "winning_notice_or_customer_certificate"}]),
        "patent_confirmed": len([item for section in sections if is_patent_task(str(section.get("task_name") or "")) for item in section["material_basis"]["groups"].get("confirmed", [])]),
        "patent_missing": len([item for item in missing_material_items if item["missing"].get("material_type") == "patent_certificate"]),
        "supplier_performance_missing": len([item for item in missing_material_items if item["missing"].get("material_type") == "supplier_performance_evaluation"]),
        "source_files_used": [
            {
                "file_id": item.get("id"),
                "original_name": item.get("original_name"),
                "parsed_text_len": len(str(item.get("parsed_text") or "")),
            }
            for item in source_files
        ],
    }
    filtered_rows = collect_filtered_clause_candidates(sections)
    summary.update(structured_summary)
    summary["filtered_mismatch_tender_refs"] = len(filtered_rows)
    summary["filtered_reason_counts"] = filtered_reason_counts(filtered_rows)
    return summary


def render_technical_response_outline(
    project: dict[str, Any],
    sections: list[dict[str, Any]],
    summary: dict[str, Any],
    global_compliance: dict[str, Any] | None = None,
    global_format_refs: list[dict[str, Any]] | None = None,
) -> str:
    lines = doc_header(project, "技术响应大纲（Clause-based Outline V1.5）")
    lines.extend(
        [
            "## 生成说明",
            "本文档为技术响应大纲与响应框架，不生成正式正文。",
            "招标正文依据来自已入库文件 parsed_text 的 clause index；评分点依据来自 score_items；材料依据来自 material_analysis。",
            "",
            "## 大纲使用说明",
            "本大纲用于指导技术响应文件编写。每个章节按照评分依据、原文响应要求与证据定位、本章响应任务、建议写作结构、知识库材料与缺失材料组织。",
            "",
            "## 摘要",
            f"- 技术响应章节数：{summary.get('technical_sections')}",
            f"- 原文响应要求数量：{summary.get('response_requirement_refs')}",
            f"- 可直接引用原文要求：{summary.get('confirmed_response_requirement_refs')}",
            f"- 需复核原文要求：{summary.get('review_response_requirement_refs')}",
            f"- 被过滤系统/商务/流程条款数量：{summary.get('filtered_mismatch_tender_refs')}",
            f"- 仅评分点驱动章节数量：{summary.get('score_only_sections')}",
            f"- 材料缺口数量：{summary.get('missing_material_items')}",
            "",
        ]
    )
    lines.extend(render_global_compliance(global_compliance or {}))
    lines.extend(render_global_format_requirements(sections, global_format_refs or []))
    for index, section in enumerate(sections, start=1):
        lines.extend(render_section(index, section))
    lines.extend(render_appendix_evidence(sections))
    lines.extend(render_appendix_missing(sections))
    lines.extend(render_appendix_missing_tender_refs(sections))
    return join_lines(lines)


def render_section(index: int, section: dict[str, Any]) -> list[str]:
    score = section["score_ref"]
    lines = [
        f"## {index}. {section['section_title']}",
        "",
        "### 1. 评分依据",
        f"- 评分项：{score.get('score_item') or '-'}",
        f"- 分值范围：{format_score_range(score.get('score_range'))}",
        f"- 评分说明：{preview(score.get('score_description'), 320) or '-'}",
        f"- 评分来源文件：{score.get('score_source_file') or '-'}",
        "",
        "### 2. 原文响应要求与证据定位",
        *render_response_requirement_groups(section),
        "",
        "### 3. 评分项行动分析 / 材料准备清单",
        *render_scoring_action_plan_content(section),
        "",
        "### 4. 本章响应任务",
        *[f"- {item}" for item in response_tasks_for_section(section)],
        "",
        "### 5. 建议写作结构",
        *[f"- {item}" for item in list(section.get("suggested_structure") or [])],
        "",
        "### 6. 知识库材料 / 候选材料 / 缺失材料",
        *render_section_materials_content(section, section["evidence_groups"]),
        "",
        "### 7. 待补充 / 待人工确认",
        *[f"- {item}" for item in list(section.get("manual_review_items") or [])],
        "",
    ]
    return lines


def render_scoring_action_plan_content(section: dict[str, Any]) -> list[str]:
    plan = section.get("scoring_action_plan") if isinstance(section.get("scoring_action_plan"), dict) else {}
    if not plan:
        return ["- 未生成评分项行动分析。"]
    lines = render_section_plan_markdown(plan, heading_level=4)
    return [line for line in lines if not line.startswith("#### ")]


def render_response_requirement_groups(section: dict[str, Any]) -> list[str]:
    refs = [
        ref for ref in list(section.get("response_requirement_refs") or [])
        if ref.get("requirement_type") not in {"format_form", "deviation_response"}
    ]
    section_type = str((section.get("tender_requirement_ref") or {}).get("section_type") or section_type_for_task(str(section.get("task_name") or "")))
    groups = [
        ("2.1 内容响应要求", ["content_response", "deliverable_requirement", "acceptance_requirement"], missing_original_requirement_message(section, "content_response")),
        ("2.2 参数 / 指标响应要求", ["parameter_response"], "未定位到本章适用参数/指标原文要求。"),
        ("2.3 人员 / 证书 / 资格要求", ["personnel_requirement", "certificate_requirement"], personnel_missing_message(section_type)),
        ("2.5 证明材料要求", ["performance_proof", "patent_proof", "supplier_performance"], proof_missing_message(section_type)),
        ("2.6 本章相关包级约束", ["package_constraint", "schedule_requirement"], "未定位到本章适用包级约束。"),
    ]
    lines: list[str] = []
    if not refs:
        return ["- 未定位到本章专属原文响应要求，按评分依据和材料缺口组织本章。"]
    for title, types, missing in groups:
        matched = [ref for ref in refs if ref.get("requirement_type") in types]
        if matched:
            lines.append(f"#### {title}")
            for ref in matched[:3]:
                lines.extend(response_requirement_ref_lines(ref))
            lines.append("")
    return lines


def response_requirement_ref_lines(ref: dict[str, Any]) -> list[str]:
    return [
        f"- 来源文件：{ref.get('source_file_name') or '-'}（{ref.get('source_file_id') or '-'}）",
        f"  - 条款路径：{clean_text_for_markdown(ref.get('section_path') or '-')}",
        f"  - 原文摘录：{clean_text_for_markdown(ref.get('original_excerpt') or '-')}",
        f"  - 使用方式：{clean_text_for_markdown(ref.get('usage') or '-')}",
        f"  - 证据质量：{ref.get('evidence_quality') or '-'}",
    ]


def missing_original_requirement_message(section: dict[str, Any], requirement_type: str) -> str:
    section_type = section_type_for_task(str(section.get("task_name") or ""))
    if section_type == "work_basis":
        return "未定位到本章专属原文响应要求，按评分点要求准备产品化成果、科技成果、专题研究等证明材料。"
    if section_type == "supplier_performance":
        return "未定位到本章专属招标正文条款。"
    if section_type == "technical_overall":
        return "未定位到本章专属内容响应条款。"
    return "未定位到本章适用内容响应条款。"


def personnel_missing_message(section_type: str) -> str:
    if section_type == "personnel":
        return "未定位到项目负责人专属证书/社保/职称要求。"
    if section_type == "team":
        return "未定位到项目团队专属证书/社保/职称要求。"
    return "未定位到本章适用人员/证书/资格原文要求。"


def proof_missing_message(section_type: str) -> str:
    if section_type == "intellectual_property":
        return "未定位到本章专属专利证明材料原文要求，按评分点要求补充专利证书、受理通知书、官方查询记录或专利清单。"
    if section_type == "supplier_performance":
        return "未定位到本章专属供应商绩效证明材料原文要求，按评分点要求补充平台截图、招标人评价结果或供应商绩效证明。"
    if section_type == "work_basis":
        return "未定位到本章专属证明材料原文要求，按评分点准备产品化成果、科技成果、专题研究证明。"
    return "未定位到本章适用证明材料原文要求。"


def render_global_compliance(global_compliance: dict[str, Any]) -> list[str]:
    return [
        "## 通用合规与响应条件",
        "",
        "- 响应文件组成：当前大纲仅基于已解析文件抽取，需人工核对完整招标文件要求。",
        "- 签字盖章与格式要求：需结合完整招标文件的响应格式、签章、授权和附件要求确认。",
        "- 偏差表：如技术规范书要求对偏差做书面说明，应在技术偏差表中逐条列明。",
        "- 服务期限、成果提交与验收要求：按技术规范书、合同条款和评分点共同核对。",
        f"- 已定位通用条款来源：{global_compliance.get('source') or '-'}",
        f"- 已定位通用条款摘录：{clean_text_for_markdown(global_compliance.get('snippet') or '当前已解析文件中未完整定位响应文件组成、签字盖章、无效响应/否决条款等通用要求，需人工确认完整招标文件是否已入库。')}",
        "",
    ]


def render_global_format_requirements(sections: list[dict[str, Any]], global_refs: list[dict[str, Any]] | None = None) -> list[str]:
    refs = dedupe_global_format_refs(list(global_refs or []) + collect_global_format_requirement_refs(sections))
    lines = ["## 技术文件格式与表单要求", ""]
    if not refs:
        lines.extend(["- 未定位到可集中展示的技术文件格式或表单要求。", ""])
        return lines
    for ref in refs:
        applies_to = "、".join(str(item) for item in list(ref.get("applies_to_sections") or ref.get("applies_to") or []) if str(item).strip()) or "-"
        lines.extend(
            [
                f"- {format_requirement_title(ref)}",
                f"  - 来源文件：{ref.get('source_file_name') or '-'}（{ref.get('source_file_id') or '-'}）",
                f"  - 条款路径：{clean_text_for_markdown(ref.get('section_path') or '-')}",
                f"  - 原文摘录：{clean_text_for_markdown(ref.get('original_excerpt') or '-')}",
                f"  - 适用章节：{applies_to}",
            ]
        )
    lines.append("")
    return lines


def collect_global_format_requirement_refs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for section in sections:
        section_title = str(section.get("section_title") or "")
        for ref in list(section.get("response_requirement_refs") or []):
            if ref.get("requirement_type") not in {"format_form", "deviation_response"}:
                continue
            key = (
                str(ref.get("source_file_id") or ""),
                str(ref.get("section_path") or ""),
                str(ref.get("original_excerpt") or "")[:120],
            )
            row = by_key.setdefault(key, dict(ref, applies_to_sections=[]))
            if section_title and section_title not in row["applies_to_sections"]:
                row["applies_to_sections"].append(section_title)
    return sorted(by_key.values(), key=lambda item: format_requirement_sort_key(str(item.get("original_excerpt") or item.get("section_path") or "")))


def build_global_format_requirement_refs(clause_indexes: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for clause in [clause for values in clause_indexes.values() for clause in values]:
        if clause.get("is_invalid") or clause.get("is_heading_only"):
            continue
        if is_empty_or_other_heading_clause(clause):
            continue
        if str(clause.get("document_region") or "") not in {"technical_file_format", "response_format"}:
            continue
        text = " ".join([str(clause.get("clause_title") or ""), str(clause.get("clause_text") or ""), " ".join(str(item) for item in list(clause.get("heading_path") or []))])
        if any(term in text for term in ["服务类供应商", "重质量", "讲诚信", "投标函", "如我方中标", "投标保证金"]):
            continue
        if not any(term in text for term in ["技术偏差表", "拟委任的主要人员汇总表", "主要人员简历表", "近年完成的类似项目情况表", "业绩文件", "其他应提交的文件"]):
            continue
        refs.append(
            {
                "requirement_type": "format_form",
                "source_file_id": clause.get("file_id") or "",
                "source_file_name": clause.get("file_name") or "",
                "source_type": clause.get("document_region") or clause.get("source_type") or "tender_source",
                "section_path": clean_text_for_markdown(clean_clause_path(list(clause.get("heading_path") or []), str(clause.get("clause_title") or ""))),
                "original_excerpt": preview(clean_text_for_markdown(clause.get("clause_text")), 300),
                "applies_to_sections": applies_to_sections_for_format_text(text),
                "usage": "用于集中确认技术文件格式、表单或附件要求。",
                "evidence_quality": "可直接引用",
                "must_quote": True,
                "clause_id": clause.get("clause_id") or "",
            }
        )
    return dedupe_global_format_refs(refs)


def applies_to_sections_for_format_text(text: str) -> list[str]:
    if "拟委任的主要人员汇总表" in text or "主要人员简历表" in text or "项目团队情况" in text:
        return ["项目负责人情况", "项目组织与团队配置"]
    if "近年完成的类似项目情况表" in text or "业绩文件" in text:
        return ["业绩与项目经验"]
    if "技术偏差表" in text:
        return ["应答文件技术部分总体评价", "总体服务方案", "实施计划与进度保障"]
    if "其他应提交的文件" in text:
        return ["总体服务方案", "实施计划与进度保障", "专利 / 知识产权 / 创新成果"]
    return ["技术响应章节"]


def dedupe_global_format_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in sorted(refs, key=lambda item: format_requirement_sort_key(str(item.get("original_excerpt") or item.get("section_path") or ""))):
        title = format_requirement_title(ref)
        if title in seen:
            continue
        seen.add(title)
        result.append(ref)
    return result


def format_requirement_sort_key(text: str) -> tuple[int, str]:
    order = [
        "技术偏差表",
        "项目团队情况",
        "拟委任的主要人员汇总表",
        "主要人员简历表",
        "业绩文件",
        "近年完成的类似项目情况表",
        "其他应提交的文件",
    ]
    for index, term in enumerate(order):
        if term in text:
            return (index, text)
    return (99, text)


def format_requirement_title(ref: dict[str, Any]) -> str:
    text = str(ref.get("original_excerpt") or ref.get("section_path") or "")
    if "技术偏差表" in text:
        return "技术偏差表"
    if "拟委任的主要人员汇总表" in text or "主要人员简历表" in text or "项目团队情况" in text:
        return "项目团队情况：表1 拟委任的主要人员汇总表；表2 主要人员简历表"
    if "近年完成的类似项目情况表" in text or "业绩文件" in text:
        return "业绩文件：近年完成的类似项目情况表"
    if "其他应提交的文件" in text or "技术规范书规定" in text:
        return "招标文件“第五章 技术规范（技术标准和要求）”中技术规范书规定的其他应提交的文件"
    return preview(text, 60) or "格式/表单要求"


def quality_label(value: Any) -> str:
    labels = {
        "confirmed_clause": "可直接引用",
        "weak_clause": "需人工复核",
        "heading_only": "仅为标题，不建议单独引用",
        "not_found": "未定位到明确条款",
        "invalid_clause": "无效条款，不引用",
    }
    return labels.get(str(value or ""), "需人工复核")


def writing_goal_for_section(section: dict[str, Any]) -> str:
    task_name = str(section.get("task_name") or "")
    if is_personnel_task(task_name) and ("负责人" in task_name or "经理" in task_name):
        return "突出项目负责人工作经验、技术能力、相关证书、项目组织能力及与本项目技术方向的匹配性。"
    if is_personnel_task(task_name):
        return "说明项目组织结构、角色分工、人员能力覆盖情况及团队保障机制。"
    if is_company_performance_task(task_name):
        return "围绕评分要求整理公司同类项目业绩，优先引用合同、验收证明、中标通知书或客户证明。"
    if "总体评价" in task_name or "应答文件技术部分" in task_name:
        return "确保技术响应文件结构完整、逻辑清晰、条款响应充分，并为后续正文撰写预留证据索引和待确认事项。"
    if "进度" in task_name or "保证措施" in task_name:
        return "围绕项目周期、阶段计划、里程碑、资源投入和进度风险控制，形成可执行的实施进度保障框架。"
    if "服务方案" in task_name or "实施方案" in task_name:
        return "结合技术规范书的研究内容、技术指标、培训、验收和售后要求，形成可执行的服务与实施方案。"
    if is_writing_only_task(task_name):
        return "围绕招标条款和评分点形成章节响应框架，并明确后续需要补充的项目化方案内容。"
    if is_patent_task(task_name):
        return "整理与评分要求对应的专利、软著、标准、科技成果等证明材料。"
    if is_supplier_performance_task(task_name):
        return "整理供应商绩效评价证明，未提供时明确列为材料缺口。"
    if is_work_foundation_task(task_name):
        return "梳理公司层面的产品化成果、科技成果、专题研究和研发能力证明，避免使用个人项目经验替代公司能力。"
    return "围绕本章节评分点和已定位条款组织技术响应框架，并标注材料依据与待确认事项。"


def render_appendix_evidence(sections: list[dict[str, Any]]) -> list[str]:
    refs = collect_applicable_evidence_refs(sections)
    lines = [
        "## 附录 A：证据索引",
        "",
        "| evidence_strength | usable_as | file_id | title | source | material_type | fact_type | confidence | snippet |",
        "|---|---|---|---|---|---|---|---:|---|",
    ]
    if not refs:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    for item in refs:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_cell(item.get("evidence_strength")),
                    md_cell(item.get("usable_as")),
                    md_cell(item.get("file_id")),
                    md_cell(item.get("title")),
                    md_cell(item.get("source")),
                    md_cell(item.get("material_type")),
                    md_cell(item.get("fact_type")),
                    md_cell(item.get("confidence")),
                    md_cell(evidence_display_snippet(item)),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def department_match_lines(department_match: dict[str, Any]) -> list[str]:
    if not department_match:
        return ["- 主责部门：-", "- 强协同部门：-", "- 弱候选部门数量：0", "- 是否需要人工确认：True", "- 简要原因：未推断。"]
    return [
        "- 主责部门：" + compact_values(department_match.get("primary_departments")),
        "- 强协同部门：" + compact_values(department_match.get("secondary_departments")),
        "- 其他建议检索部门：" + compact_values(
            [
                item.get("department")
                for item in list(department_match.get("candidate_departments") or [])
                if isinstance(item, dict)
                and item.get("department") not in set(department_match.get("primary_departments") or [])
                and item.get("department") not in set(department_match.get("secondary_departments") or [])
                and item.get("department") not in set(department_match.get("weak_candidate_departments") or [])
            ]
        ),
        f"- 弱候选部门数量：{department_match.get('weak_candidate_count') or 0}",
        f"- 是否需要人工确认：{department_match.get('requires_manual_confirm')}",
        f"- 简要原因：{department_match.get('routing_summary_reason') or '-'}",
    ]


def compact_values(values: Any) -> str:
    items = [str(item) for item in list(values or []) if str(item).strip()] if isinstance(values, list) else []
    return "、".join(items) if items else "-"


def render_section_materials(section: dict[str, Any], groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    task_name = str(section.get("task_name") or "")
    if is_personnel_task(task_name):
        title = "人员候选与依据" if "负责人" in task_name or "项目经理" in task_name else "团队候选与人员材料"
        return [
            f"### 6. {title}",
            "",
            "#### 本项目建议检索部门",
            *department_match_lines(section.get("department_match") or {}),
            "",
            "#### 候选人员表",
            *person_candidate_lines(section.get("person_candidates") or []),
            "",
            "#### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_company_performance_task(task_name):
        return [
            "### 6. 公司业绩材料",
            "- 已确认公司业绩材料：无" if not groups.get("confirmed") else "- 已确认公司业绩材料：",
            *evidence_lines(groups.get("confirmed") or []),
            "- 人员项目经验不在本章节可引用材料中。",
            *non_substitution_lines(section),
            "#### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_patent_task(task_name):
        return [
            "### 6. 专利/知识产权材料",
            "- 已确认专利/知识产权材料：无" if not groups.get("confirmed") else "- 已确认专利/知识产权材料：",
            *evidence_lines(groups.get("confirmed") or []),
            *non_substitution_lines(section),
            "#### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_supplier_performance_task(task_name):
        return [
            "### 6. 供应商绩效评价材料",
            "- 已确认供应商绩效评价材料：无" if not groups.get("confirmed") else "- 已确认供应商绩效评价材料：",
            *evidence_lines(groups.get("confirmed") or []),
            *non_substitution_lines(section),
            "#### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_writing_only_task(task_name):
        return [
            "### 6. 写作输入说明",
            "- 本章节主要依据招标条款和评分点撰写，当前无必须引用的企业证明材料；需后续补充项目化方案内容。",
            "#### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_work_foundation_task(task_name):
        visible = [
            item
            for item in list(groups.get("confirmed") or []) + list(groups.get("candidate") or [])
            if item.get("material_type") not in {"person_resume", "personal_certificate", "unusable_summary"}
            and "person_project_experience" not in str(item.get("fact_type") or "")
        ]
        return [
            "### 6. 知识库材料 / 候选材料 / 缺失材料",
            "- 公司层面候选材料：",
            *evidence_lines(visible),
            "- 人员简历项目经验不进入本章节主大纲，仅保留在 debug 中供排查。",
            "#### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    visible = list(groups.get("confirmed") or []) + list(groups.get("candidate") or []) + list(groups.get("weak") or [])
    return [
        "### 6. 知识库材料 / 候选材料 / 缺失材料",
        "- confirmed / candidate / weak_evidence",
        *evidence_lines(visible),
        *non_substitution_lines(section),
        "#### 缺失材料",
        *missing_material_lines(section.get("missing_materials") or []),
        *material_note_lines(section.get("material_note")),
    ]


def render_section_materials_content(section: dict[str, Any], groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    task_name = str(section.get("task_name") or "")
    if is_personnel_task(task_name):
        title = "人员候选与依据" if "负责人" in task_name or "项目经理" in task_name else "团队候选与人员材料"
        return [
            f"#### {title}",
            "",
            "##### 本项目建议检索部门",
            *department_match_lines(section.get("department_match") or {}),
            "",
            "##### 候选人员表",
            *person_candidate_lines(section.get("person_candidates") or []),
            "",
            "##### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_company_performance_task(task_name):
        return [
            "#### 公司业绩材料",
            "- 已确认公司业绩材料：无" if not groups.get("confirmed") else "- 已确认公司业绩材料：",
            *evidence_lines(groups.get("confirmed") or []),
            "- 人员项目经验不能替代公司业绩合同、验收证明、中标通知书或客户证明。",
            "##### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_patent_task(task_name):
        return [
            "#### 专利/知识产权材料",
            "- 已确认专利/知识产权材料：无" if not groups.get("confirmed") else "- 已确认专利/知识产权材料：",
            *evidence_lines(groups.get("confirmed") or []),
            "- 仅出现“包含专利”等泛化文本不能替代专利证书、受理通知书或官方专利记录。",
            "##### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_supplier_performance_task(task_name):
        return [
            "#### 供应商绩效评价材料",
            "- 已确认供应商绩效评价材料：无" if not groups.get("confirmed") else "- 已确认供应商绩效评价材料：",
            *evidence_lines(groups.get("confirmed") or []),
            "- 简历、资质证书、项目经验不能替代供应商绩效评价结果证明。",
            "##### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_writing_only_task(task_name):
        return [
            "#### 写作输入说明",
            "- 本章节主要依据评分点和已定位原文要求形成方案框架；后续需补充项目化服务方案内容。",
            "##### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    if is_work_foundation_task(task_name):
        visible = [
            item
            for item in list(groups.get("confirmed") or []) + list(groups.get("candidate") or [])
            if item.get("material_type") not in {"person_resume", "personal_certificate", "unusable_summary"}
            and "person_project_experience" not in str(item.get("fact_type") or "")
        ]
        return [
            "#### 公司层面候选材料",
            *evidence_lines(visible),
            "- 人员简历项目经验不进入本章节主大纲，仅保留在 debug 中供排查。",
            "##### 缺失材料",
            *missing_material_lines(section.get("missing_materials") or []),
        ]
    visible = list(groups.get("confirmed") or []) + list(groups.get("candidate") or []) + list(groups.get("weak") or [])
    return [
        "#### confirmed / candidate / weak_evidence",
        *evidence_lines(visible),
        "##### 缺失材料",
        *missing_material_lines(section.get("missing_materials") or []),
        *material_note_lines(section.get("material_note")),
    ]


def response_tasks_for_section(section: dict[str, Any]) -> list[str]:
    task_name = str(section.get("task_name") or "")
    if "总体评价" in task_name or "应答文件技术部分" in task_name:
        return [
            "保证技术文件结构完整、逻辑清晰、目录与评分点对应。",
            "建立评分点到章节的索引。",
            "避免遗漏技术偏差、证明材料和成果响应。",
        ]
    if is_personnel_task(task_name) and ("负责人" in task_name or "经理" in task_name):
        return [
            "写明项目负责人专业背景、工作年限、工作经验、同类项目经验、职责分工。",
            "如有相关证书，只作为个人能力材料，不替代企业资质、公司业绩或绩效评价。",
        ]
    if is_personnel_task(task_name):
        return [
            "说明团队结构、专业分工、人员能力覆盖和交付保障机制。",
            "人员材料仅作为人员章节依据，不替代公司业绩、企业资质或绩效评价。",
        ]
    if is_company_performance_task(task_name):
        return [
            "准备公司层面的技术开发或技术服务业绩。",
            "优先整理合同关键页、验收证明、中标通知书或客户证明。",
            "人员项目经验不能替代公司业绩。",
        ]
    if is_writing_only_task(task_name) and "进度" in task_name:
        return ["围绕服务期限拆分阶段计划、里程碑、资源投入和进度跟踪机制。"]
    if is_writing_only_task(task_name):
        return ["围绕评分点和原文要求组织服务方案、实施路径、质量控制、风险控制和沟通机制。"]
    if is_work_foundation_task(task_name):
        return ["按评分点准备产品化成果、科技成果、专题研究和研发能力证明。"]
    if is_patent_task(task_name):
        return ["整理与采购相关的发明专利、实用新型专利、受理通知书、官方查询记录或专利清单。"]
    if is_supplier_performance_task(task_name):
        return ["保留评分点要求，准备最近一年度平台绩效评价截图、招标人评价结果或供应商绩效证明。"]
    return [writing_goal_for_section(section)]


def render_appendix_missing(sections: list[dict[str, Any]]) -> list[str]:
    missing = collect_missing_materials(sections)
    manual = collect_manual_review_items(sections)
    lines = ["## 附录 B：材料缺口与人工确认事项", "", "### 材料缺口"]
    if not missing:
        lines.extend(["- 当前没有经过 evidence_applicability 过滤后识别出的明确材料缺口。", ""])
    for item in missing:
        material = item["missing"]
        lines.extend(
            [
                f"- {item['section_title']} / {material.get('material_type') or '-'}",
                f"  - reason: {material.get('reason') or '-'}",
                f"  - suggested_action: {material.get('suggested_action') or '-'}",
            ]
        )
    lines.extend(["", "### 人工确认事项"])
    if not manual:
        lines.append("- 当前没有额外人工确认事项。")
    for item in manual:
        lines.append(f"- {item['section_title']}：{item['message']}")
    lines.append("")
    return lines


def render_appendix_missing_tender_refs(sections: list[dict[str, Any]]) -> list[str]:
    lines = ["## 附录 C：招标正文依据未定位章节", ""]
    items = [
        section for section in sections
        if section["tender_requirement_ref"].get("evidence_quality") in {"not_found", "heading_only"}
    ]
    if not items:
        lines.append("- 当前主 Markdown 中未展开 filtered candidates；详细过滤记录见 filtered_clause_candidates.json。")
    for section in items:
        tender = section["tender_requirement_ref"]
        lines.extend(
            [
                f"### {section['section_title']}",
                f"- 依据质量：{quality_label(tender.get('evidence_quality'))}",
                f"- 当前说明：{tender.get('source_snippet')}",
                "- 处理建议：按评分点、包级约束和已定位证明材料要求组织本章。",
            ]
        )
        lines.append("")
    return lines


def collect_filtered_clause_candidates(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for section in sections:
        section_title = str(section.get("section_title") or "")
        refs = [section.get("tender_requirement_ref") or {}, section.get("compliance_ref") or {}]
        for ref in refs:
            for item in list(ref.get("ignored_candidates") or []):
                if not isinstance(item, dict):
                    continue
                row = {
                    "section_title": section_title,
                    "candidate_clause_id": item.get("candidate_clause_id") or item.get("clause_id") or "",
                    "source_file_name": item.get("source_file_name"),
                    "source_file_id": item.get("source_file_id"),
                    "document_region": item.get("document_region"),
                    "section_path": list(item.get("section_path") or []),
                    "clause_path": item.get("clause_path"),
                    "clause_type": item.get("clause_type"),
                    "text_preview": item.get("text_preview") or item.get("snippet"),
                    "filter_reason": item.get("filter_reason") or item.get("reason"),
                    "would_have_matched_by": list(item.get("would_have_matched_by") or []),
                    "table_context": item.get("table_context"),
                    "recoverable": item.get("recoverable", True),
                    "match_score": item.get("match_score"),
                }
                key = (section_title, str(row["candidate_clause_id"]), str(row["filter_reason"]))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        for item in list(section.get("response_requirement_ignored_candidates") or []):
            if not isinstance(item, dict):
                continue
            row = {
                "section_title": section_title,
                "candidate_clause_id": item.get("candidate_clause_id") or item.get("clause_id") or "",
                "source_file_name": item.get("source_file_name"),
                "source_file_id": item.get("source_file_id"),
                "document_region": item.get("document_region"),
                "section_path": list(item.get("section_path") or []),
                "clause_path": item.get("clause_path"),
                "clause_type": item.get("clause_type"),
                "text_preview": item.get("text_preview") or item.get("snippet"),
                "filter_reason": item.get("filter_reason") or item.get("reason"),
                "would_have_matched_by": list(item.get("would_have_matched_by") or []),
                "table_context": item.get("table_context"),
                "recoverable": item.get("recoverable", True),
                "match_score": item.get("match_score"),
            }
            key = (section_title, str(row["candidate_clause_id"]), str(row["filter_reason"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def filtered_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("filter_reason") or row.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def build_debug_payload(
    project: dict[str, Any],
    source_files: list[dict[str, Any]],
    clause_indexes: dict[str, list[dict[str, Any]]],
    sections: list[dict[str, Any]],
    summary: dict[str, Any],
    global_compliance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": "v1.5",
        "lot_name": project.get("lot_name"),
        "project_name": project.get("project_name"),
        "project_name_source": project.get("project_name_source"),
        "global_compliance": global_compliance or {},
        "source_files": [
            {
                "file_id": item.get("id"),
                "original_name": item.get("original_name"),
                "parsed_text_len": len(str(item.get("parsed_text") or "")),
                "clause_count": len(clause_indexes.get(str(item.get("id") or ""), [])),
            }
            for item in source_files
        ],
        "summary": summary,
        "structured_clause_summary": summarize_structured_clauses(clause_indexes),
        "filtered_clause_candidates": collect_filtered_clause_candidates(sections)[:50],
        "sections": [
            {
                "section_title": section.get("section_title"),
                "task_id": section.get("task_id"),
                "tender_requirement_ref": section.get("tender_requirement_ref"),
                "response_requirement_refs": section.get("response_requirement_refs"),
                "response_requirement_ignored_candidates": section.get("response_requirement_ignored_candidates"),
                "scoring_action_plan": section.get("scoring_action_plan"),
                "score_ref": section.get("score_ref"),
                "evidence_counts": {key: len(value) for key, value in section.get("evidence_groups", {}).items()},
                "department_match": section.get("department_match"),
                "person_candidates": section.get("person_candidates"),
                "material_basis": section.get("material_basis"),
                "missing_materials": section.get("missing_materials"),
            }
            for section in sections
        ],
    }


def material_map_by_task_id(project: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    material_analysis = project.get("material_analysis") if isinstance(project.get("material_analysis"), dict) else {}
    allowed_ids = existing_file_ids(metadata or {}) if metadata is not None else set()
    files_by_id = {
        str(item.get("id") or item.get("file_id") or ""): item
        for item in list((metadata or {}).get("files") or [])
        if isinstance(item, dict)
    }
    result: dict[str, dict[str, Any]] = {}
    for item in list(material_analysis.get("task_material_map") or []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue
        result[task_id] = filter_material_map_item_to_existing_files(item, allowed_ids, files_by_id)
    return result


def filter_material_map_item_to_existing_files(item: dict[str, Any], allowed_ids: set[str], files_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not allowed_ids:
        return dict(item)
    result = dict(item)
    matched: list[dict[str, Any]] = []
    ignored: list[str] = []
    for material in list(result.get("matched_materials") or []):
        if not isinstance(material, dict):
            continue
        file_id = str(material.get("file_id") or material.get("source_file_id") or "")
        if file_id and file_id not in allowed_ids:
            ignored.append(file_id)
            continue
        if file_id and files_by_id and is_tender_source_file(files_by_id.get(file_id, {})):
            ignored.append(file_id)
            continue
        matched.append(material)
    result["matched_materials"] = matched
    if ignored:
        result["orphaned_file_ids_ignored"] = sorted(set(ignored))
    return {
        key: value
        for key, value in result.items()
        if key != "matched_materials" or isinstance(value, list)
    }


def score_items_by_id(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in list(project.get("score_items") or [])
        if isinstance(item, dict)
    }


def first_related_score_item(task: dict[str, Any], score_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for item_id in list(task.get("related_score_item_ids") or []):
        if str(item_id) in score_map:
            return score_map[str(item_id)]
    return {}


def build_manual_review_items(tender_ref: dict[str, Any], material_basis: dict[str, Any], missing_materials: list[dict[str, Any]]) -> list[str]:
    items = ["核对招标正文原文页码、条款号和适用范围。"]
    if tender_ref.get("evidence_quality") == "not_found":
        items.append("未定位到本章专属原文要求时，按评分点、包级约束和材料缺口组织本章。")
    if tender_ref.get("evidence_quality") == "heading_only":
        items.append("当前仅命中目录、标题或跳转语，需补充具体正文条款。")
    groups = material_basis.get("groups") or {}
    if groups.get("weak") or groups.get("unusable") or groups.get("candidate"):
        items.append("候选材料、弱证据和不可直接使用材料只能作为线索，需人工核验正式材料。")
    if missing_materials:
        items.append("存在材料缺口，需补充后再进入正文撰写。")
    return items


def collect_evidence_refs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for section in sections:
        for item in list(section.get("evidence_refs") or []):
            key = (
                str(item.get("file_id") or ""),
                str(item.get("material_type") or ""),
                str(item.get("fact_type") or ""),
                str(item.get("snippet") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(item)
    return refs


def collect_applicable_evidence_refs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for section in sections:
        for item in get_markdown_visible_evidence(section):
            key = (
                str(item.get("file_id") or ""),
                str(item.get("material_type") or ""),
                str(item.get("fact_type") or ""),
                str(item.get("snippet") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(item)
    return refs


def collect_material_basis_evidence_refs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for section in sections:
        groups = (section.get("material_basis") or {}).get("groups") or {}
        for group_name in ["confirmed", "candidate", "weak", "unusable"]:
            for item in list(groups.get(group_name) or []):
                key = (
                    str(item.get("file_id") or ""),
                    str(item.get("material_type") or ""),
                    str(item.get("fact_type") or ""),
                    str(item.get("snippet") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                refs.append(item)
    return refs


def get_markdown_visible_evidence(section: dict[str, Any]) -> list[dict[str, Any]]:
    task_name = str(section.get("task_name") or "")
    groups = (section.get("material_basis") or {}).get("groups") or {}
    if is_personnel_task(task_name):
        return []
    if is_company_performance_task(task_name):
        return list(groups.get("confirmed") or [])
    if is_patent_task(task_name):
        return list(groups.get("confirmed") or [])
    if is_supplier_performance_task(task_name):
        return list(groups.get("confirmed") or [])
    if is_work_foundation_task(task_name):
        return [
            item
            for item in list(groups.get("confirmed") or []) + list(groups.get("candidate") or [])
            if item.get("material_type") not in {"person_resume", "personal_certificate", "unusable_summary"}
            and "person_project_experience" not in str(item.get("fact_type") or "")
        ]
    if is_writing_only_task(task_name):
        return []
    return list(groups.get("confirmed") or []) + list(groups.get("candidate") or []) + list(groups.get("weak") or [])


def collect_missing_materials(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for section in sections:
        for missing in list(section.get("missing_materials") or []):
            items.append({"section_title": section.get("section_title"), "missing": missing})
    return items


def person_candidate_lines(candidates: list[dict[str, Any]]) -> list[str]:
    if not candidates:
        return ["- 无候选人员聚合结果。"]
    lines = [
        "| name | file_id | department | department_confirm_status | department_match_status | work_years | skills | certificates | project_experience | relevance_to_project | match_reason | missing_or_weak_points | suggested_usage |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in candidates:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_cell(item.get("name")),
                    md_cell(item.get("file_id")),
                    md_cell(item.get("department")),
                    md_cell(item.get("department_confirm_status")),
                    md_cell(item.get("department_match_status")),
                    md_cell(item.get("work_years")),
                    md_cell(", ".join(item.get("skills") or [])),
                    md_cell(", ".join(item.get("certificates") or [])),
                    md_cell(", ".join(item.get("project_experience") or [])),
                    md_cell(item.get("relevance_to_project")),
                    md_cell(item.get("match_reason")),
                    md_cell(", ".join(item.get("missing_or_weak_points") or [])),
                    md_cell(item.get("suggested_usage")),
                ]
            )
            + " |"
        )
    return lines


def missing_material_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- 无"]
    return [
        f"- {item.get('material_type') or '-'}：{item.get('reason') or '-'}；建议：{item.get('suggested_action') or '-'}"
        for item in items
    ]


def material_note_lines(note: Any) -> list[str]:
    text = str(note or "").strip()
    return [f"- {text}"] if text else []


def non_substitution_lines(section: dict[str, Any]) -> list[str]:
    task_name = str(section.get("task_name") or "")
    lines: list[str] = []
    if is_company_performance_task(task_name):
        lines.append("  - 人员项目经验不能替代公司业绩合同、验收证明、中标通知书或客户证明。")
    if "资质" in task_name:
        lines.append("  - 个人证书不能替代企业资质。")
    if is_supplier_performance_task(task_name):
        lines.append("  - 简历、资质证书、项目经验不能替代供应商绩效评价结果证明。")
    if is_patent_task(task_name):
        lines.append("  - 仅出现“包含专利”等泛化文本不能替代专利证书或官方专利记录。")
    return lines


def collect_manual_review_items(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for section in sections:
        for message in list(section.get("manual_review_items") or []):
            items.append({"section_title": str(section.get("section_title") or ""), "message": str(message)})
    return items


def interpret_clause(clause: dict[str, Any]) -> str:
    clause_type = clause.get("clause_type")
    if clause_type == "personnel_requirement":
        return "围绕人员资历、团队配置、简历和证书材料组织响应。"
    if clause_type == "performance_requirement":
        return "围绕项目经验、合同、验收或绩效评价材料组织响应。"
    if clause_type == "service_requirement":
        return "围绕服务范围、服务内容和交付责任组织响应。"
    if clause_type == "technical_requirement":
        return "围绕技术要求、实施方案、质量进度与安全保障组织响应。"
    if clause_type == "response_file_requirement":
        return "围绕响应文件技术部分格式、附件和证明材料索引组织响应。"
    return "根据该条款提炼响应条件，并与评分点和材料证据逐项对齐。"


def interpretation_for_task(task_name: str, clause: dict[str, Any], quality: str) -> str:
    if quality in {"heading_only", "invalid_clause"}:
        return "仅命中封面、目录、标题、免责声明或跳转语，不作为有效招标正文依据。"
    if is_personnel_task(task_name):
        return "本章节重点核对项目负责人/团队成员的简历、分工、技能与证书材料；若技术规范书未列明人员专业、年限或证书要求，则以评分点为主并人工确认。"
    if is_company_performance_task(task_name):
        return "本章节主要核对公司业绩合同、验收证明、中标通知书或客户证明；人员项目经验不能替代公司业绩证明。"
    if is_patent_task(task_name):
        return "本章节主要核对专利/知识产权证明材料；需有专利名称、专利号/申请号、权利人和状态等明确要素。"
    if is_supplier_performance_task(task_name):
        return "本章节主要核对最近一年度招标人或平台供应商绩效评价结果证明；简历、资质证书和项目经验不能替代。"
    if is_writing_only_task(task_name):
        return "本章节主要依据招标/技术条款和评分点组织方案框架，后续需要补充项目化服务方案内容。"
    if is_work_foundation_task(task_name):
        return "本章节主要核对公司层面的产品化成果、科技成果、专题研究、专利、软著或研发项目证明；人员简历项目经验只作调试线索。"
    if quality == "not_found":
        return "本章节主要由评分点驱动，未在技术规范书中定位到单独条款；需结合评分点和材料清单人工确认。"
    return interpret_clause(clause)


def suggested_structure_for_task(task_name: str) -> list[str]:
    if "负责人" in task_name or "项目经理" in task_name:
        return ["人员基本情况", "工作年限与专业背景", "相关项目经验", "相关个人证书", "与项目要求的对应关系"]
    if "团队" in task_name or "人员结构" in task_name:
        return ["团队组成", "专业分工", "技能覆盖", "项目经验", "交付保障"]
    if "服务方案" in task_name or "实施方案" in task_name:
        return ["服务目标理解", "总体实施思路", "关键工作内容", "质量控制", "风险控制", "沟通机制"]
    if "进度" in task_name:
        return ["进度目标", "阶段计划", "资源安排", "进度跟踪", "保障措施"]
    if "质量" in task_name:
        return ["质量目标", "质量控制流程", "验收配合", "问题闭环", "持续改进"]
    if "业绩" in task_name or "项目经验" in task_name:
        return ["案例清单", "项目背景", "服务内容", "验收或评价材料", "与项目需求的关联"]
    if "专利" in task_name or "创新" in task_name:
        return ["成果清单", "证明材料", "应用场景", "与技术方案的关系"]
    return ["招标要求理解", "评分点响应", "材料引用计划", "待确认事项"]


def writing_goal_for_task(task: dict[str, Any], task_name: str) -> str:
    instruction = str(task.get("generation_instruction") or "").strip()
    if instruction:
        return instruction.replace("得满分表达", "响应表达")
    return f"围绕“{task_name}”形成技术响应大纲，标注招标条款、评分点、材料依据和待确认事项。"


def title_for_task(task_name: str) -> str:
    if "负责人" in task_name:
        return "项目负责人情况"
    if "团队" in task_name or "人员结构" in task_name:
        return "项目组织与团队配置"
    if "服务方案" in task_name:
        return "总体服务方案"
    if "进度" in task_name:
        return "实施计划与进度保障"
    if "质量" in task_name:
        return "质量保障措施"
    if "风险" in task_name or "沟通" in task_name:
        return "风险控制与沟通机制"
    if "工作基础" in task_name:
        return "工作基础与既有能力"
    if "业绩" in task_name or "项目经验" in task_name:
        return "业绩与项目经验"
    if "专利" in task_name or "创新" in task_name:
        return "专利 / 知识产权 / 创新成果"
    if "绩效" in task_name:
        return "绩效评价材料"
    return task_name


def infer_task_name(task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "")
    parts = task_id.split("_")
    return parts[-1] if parts else task_id


def normalize_score_range(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"min": value.get("min"), "max": value.get("max")}
    if isinstance(value, (int, float)):
        return {"min": 0, "max": value}
    return {"min": None, "max": None}


def first_text(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "")


def unique_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def find_term_contexts(text: str, term: str, window: int = 180, limit: int = 8) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    if not term:
        return contexts
    for match in re.finditer(re.escape(term), text):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        contexts.append({"start": match.start(), "end": match.end(), "context": text[start:end]})
        if len(contexts) >= limit:
            break
    return contexts


def doc_header(project: dict[str, Any], title: str) -> list[str]:
    info = project.get("project_info_fields") if isinstance(project.get("project_info_fields"), dict) else {}
    manual_items = list(info.get("manual_confirm_items") or [])
    manual_text = "；".join(str(item) for item in manual_items if str(item).strip()) or "-"
    service_period = normalize_service_period_for_display(str(info.get("service_period") or ""))
    max_price = normalize_max_price_for_display(str(info.get("max_price") or ""))
    special_qualification = str(info.get("special_qualification_requirement") or "").strip() or "-"
    return [
        f"# {title}",
        "",
        f"- 标段/包号：{project.get('lot_name') or '-'}",
        f"- 招标编号：{info.get('tender_no') or '-'}",
        f"- 项目名称：{project.get('project_name') or '-'}",
        f"- 分标编号：{info.get('lot_no') or '-'}",
        f"- 分标名称：{info.get('lot_name') or '-'}",
        f"- 包号：{info.get('package_no') or '-'}",
        f"- 包名称：{info.get('package_name') or '-'}",
        f"- 包/技术专题名称：{info.get('technical_topic_name') or '-'}",
        f"- 招标人：{info.get('tenderee') or '-'}",
        f"- 招标代理机构：{info.get('tender_agency') or '-'}",
        f"- 元信息待确认项：{manual_text}",
        f"- 生成类型：Markdown Outline V1.5",
        "",
        "## 项目关键约束",
        f"- 服务期限：{service_period or '-'}",
        f"- 最高限价：{max_price or '-'}",
        f"- 专用资格要求：{special_qualification}",
        "",
    ]


def normalize_service_period_for_display(value: str) -> str:
    return str(value or "").replace(",", "，").strip()


def normalize_max_price_for_display(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if any(unit in text for unit in ["万元", "元"]):
        return text
    return f"{text}万元"


def evidence_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["  - 无"]
    return [
        f"  - `{item.get('file_id') or '-'}` | {item.get('title') or '-'} | {item.get('material_type') or '-'} | {evidence_display_snippet(item)} | {item.get('applicability_reason') or item.get('usable_as') or '-'}"
        for item in items
    ]


def evidence_display_snippet(item: dict[str, Any]) -> str:
    if item.get("qualification_certificates"):
        return "；".join(str(value) for value in list(item.get("qualification_certificates") or []))
    if item.get("fact_type") == "stale_or_mock_summary":
        return "stale_or_mock_summary：旧泛化摘要不可作为确认材料"
    return preview(item.get("snippet"), 140)


def clean_clause_path(heading_path: list[str], fallback: str) -> str:
    cleaned = [
        clean_text_for_markdown(item)
        for item in heading_path
        if not any(bad in str(item) for bad in ["项目招标文件模板", "封面", "目 录", "目录", "特别声明", "免责条款", "解释权"])
    ]
    if cleaned:
        return " > ".join(cleaned)
    fallback = clean_text_for_markdown(fallback)
    return fallback if fallback and not any(bad in fallback for bad in ["项目招标文件模板", "封面", "目 录", "目录"]) else "-"


def clean_text_for_markdown(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = re.sub(r"^(\d+(?:\.\d+)+)([^\s.、：:])", r"\1 \2", text)
    text = re.sub(r"^(\d+)[.．](?!\d)\s*", r"\1. ", text)
    text = re.sub(r"(\d+(?:\.\d+)+\s*[\u4e00-\u9fa5A-Za-z、（）()]{2,40})\s+\d+(?=\s|$)", r"\1", text)
    text = re.sub(r"(\d+\.\s*[\u4e00-\u9fa5A-Za-z、（）()]{2,40})\s+\d+(?=\s|$)", r"\1", text)
    text = re.sub(r"(\b第[\u4e00-\u9fa5一二三四五六七八九十\d]+[章节条]\s*[\u4e00-\u9fa5A-Za-z、（）()]{0,40})\s+\d+(?=\s|$)", r"\1", text)
    text = re.sub(r"\b\d+\.\s*(投标须知|范围|规范标准|技术规范与工程内容|技术培训|验收测试|包装、运输、安装和现场服务|质量保证要求与售后服务)\s+(?=\d+(?:\.\d+)*)", "", text)
    text = re.sub(r"附件\d*[：:、]?\s*项目招标文件模板[^。；\n]{0,80}", "", text)
    text = text.replace("目 录", "").replace("目录", "")
    return " ".join(text.split()).strip()


def format_score_range(value: Any) -> str:
    if isinstance(value, dict):
        min_value = value.get("min")
        max_value = value.get("max")
        if min_value is None and max_value is None:
            return "-"
        return f"{min_value} - {max_value}"
    return "-"


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def md_cell(value: Any) -> str:
    return preview(value, 180).replace("|", "\\|").replace("\n", " ")


def preview(value: Any, limit: int = 180) -> str:
    text = clean_text_for_markdown(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def safe_path_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", value).strip("_") or "unknown_lot"


def join_lines(lines: list[str]) -> str:
    text = "\n".join(lines).rstrip() + "\n"
    ensure_no_decision_terms(text)
    return text


def ensure_no_decision_terms(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()
    for term in FORBIDDEN_DECISION_TERMS:
        if term.lower() in text:
            raise ValueError("response outline contains forbidden bid decision language")

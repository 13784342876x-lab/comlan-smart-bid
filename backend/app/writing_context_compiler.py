from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.material_matcher import find_project_by_lot_name, load_tender_projects
from app.tender_requirement_matrix import load_json, safe_path_name, unique


SCHEMA_VERSION = "1.0"

TASK_TYPES = {
    "proposal_drafting",
    "evidence_based_drafting",
    "mixed_drafting",
    "placeholder_only",
    "not_applicable",
}
BLOCKING_STATUSES = {
    "ready",
    "partially_ready",
    "blocked_by_evidence",
    "blocked_by_manual_review",
    "ready_with_placeholders",
    "not_applicable",
}
ALLOWED_USAGE = {
    "factual_claim",
    "supporting_reference",
    "cautious_reference",
    "placeholder_only",
    "prohibited",
}

PROPOSAL_SCORE_IDS = {
    "technical_01_应答文件技术部分总体评价",
    "technical_05_服务方案",
    "technical_06_工作进度及保证措施",
}
PERSON_SCORE_IDS = {
    "technical_02_项目负责人情况",
    "technical_03_项目团队人员结构",
}
WRITING_SCOPES = {"technical_response", "current_package", "current_lot"}
WRITING_REQUIREMENT_TYPES = {
    "mandatory_substantive_response",
    "technical_mandatory_requirement",
    "delivery_acceptance_commitment",
    "confidentiality_security_requirement",
    "score_related_requirement",
}

PROPOSAL_CONTENT: dict[str, list[str]] = {
    "technical_01_应答文件技术部分总体评价": [
        "项目背景与目标理解",
        "技术响应总体思路",
        "响应完整性与条款对应关系",
        "技术路线和总体架构",
        "质量、安全、保密和交付总体承诺",
    ],
    "technical_02_项目负责人情况": ["岗位职责", "能力要求", "管理机制", "工作界面", "人员选择原则"],
    "technical_03_项目团队人员结构": ["团队组织原则", "岗位分工", "协作机制", "人员替补机制", "投入管理规则"],
    "technical_04_业绩情况": ["材料呈现规则", "业绩与采购主题的对应说明框架", "证据包索引结构"],
    "technical_05_服务方案": [
        "服务组织",
        "响应机制",
        "问题处理流程",
        "质量控制",
        "沟通汇报",
        "风险处置",
        "服务承诺",
        "测试、验收、培训和交付安排",
    ],
    "technical_06_工作进度及保证措施": ["里程碑计划", "任务分解", "进度跟踪", "资源保障", "延期预警", "纠偏措施"],
    "technical_07_工作基础": ["工作基础材料组织方式", "技术成果与采购主题对应框架", "待补材料索引"],
    "technical_08_专利数量": ["专利清单结构", "专利与采购主题相关性说明框架", "证明材料索引"],
    "technical_09_绩效评价": ["绩效评价材料说明框架", "评价年度和主体核对规则", "证明材料索引"],
}

RECOMMENDED_STRUCTURE: dict[str, list[str]] = {
    "technical_01_应答文件技术部分总体评价": ["项目理解", "总体响应策略", "技术路线", "条款响应索引", "质量与风险总则"],
    "technical_02_项目负责人情况": ["岗位定位", "任职能力要求", "候选人事实占位", "职责与接口", "证明材料索引"],
    "technical_03_项目团队人员结构": ["组织架构", "岗位配置原则", "团队事实占位", "协作与投入机制", "证明材料索引"],
    "technical_04_业绩情况": ["评分规则响应", "业绩证据包清单", "逐项相关性说明", "数量覆盖检查", "缺失材料占位"],
    "technical_05_服务方案": ["服务目标", "服务组织", "技术实施方案", "质量控制", "风险管理", "沟通汇报", "测试验收", "服务承诺"],
    "technical_06_工作进度及保证措施": ["进度目标", "阶段和里程碑", "任务计划", "资源配置", "监控预警", "纠偏和保障措施"],
    "technical_07_工作基础": ["评分条件响应", "产品化基础", "科技成果", "专题研究", "证据与缺口索引"],
    "technical_08_专利数量": ["评分规则响应", "专利清单", "相关性说明", "证明材料索引", "数量缺口"],
    "technical_09_绩效评价": ["评价规则响应", "评价年度与主体", "评价结果占位", "证明材料索引", "人工复核事项"],
}


def build_writing_context(
    lot_name: str,
    output_dir: str | Path = "outputs",
    *,
    update_context_index: bool = True,
) -> dict[str, Any]:
    base = Path(output_dir)
    lot_dir = base / safe_path_name(lot_name)
    paths = writing_input_paths(lot_dir)
    inputs = {name: load_json(path) for name, path in paths.items() if name != "tender_projects"}
    project = find_project_by_lot_name(load_tender_projects(), lot_name)
    validate_inputs(lot_name, inputs)

    compiled = compile_writing_artifacts(
        lot_name,
        context=inputs["technical_response_context"],
        requirement_matrix=inputs["tender_requirement_matrix"],
        action_index=inputs["actionable_requirement_index"],
        catalog=inputs["enterprise_evidence_catalog"],
        coverage=inputs["evidence_coverage_matrix"],
        resolution_queue=inputs["evidence_resolution_queue"],
        intake_plan=inputs["material_intake_plan"],
        project=project,
        source_paths={key: str(value) for key, value in paths.items()},
    )
    writing_dir = lot_dir / "writing"
    writing_dir.mkdir(parents=True, exist_ok=True)
    output_paths = writing_output_paths(writing_dir)
    for key in ["writing_context_pack", "technical_response_outline_for_agent", "evidence_use_map", "draft_guardrails", "chapter_task_packets"]:
        output_paths[key].write_text(json.dumps(compiled[key], ensure_ascii=False, indent=2), encoding="utf-8")
    output_paths["technical_response_outline_for_agent_markdown"].write_text(
        render_agent_outline_markdown(compiled["technical_response_outline_for_agent"], compiled["chapter_task_packets"]),
        encoding="utf-8",
    )

    context = inputs["technical_response_context"]
    if update_context_index:
        update_context_writing_index(context, compiled["chapter_task_packets"], output_paths)
        paths["technical_response_context"].write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "schema_version": SCHEMA_VERSION,
        "lot_name": lot_name,
        "files": {key: str(value) for key, value in output_paths.items()},
        "statistics": compiled["chapter_task_packets"]["statistics"],
        "build_warnings": compiled["writing_context_pack"]["build_warnings"],
    }


def inspect_writing_context(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    writing_dir = Path(output_dir) / safe_path_name(lot_name) / "writing"
    paths = writing_output_paths(writing_dir)
    pack = load_json(paths["writing_context_pack"])
    evidence_map = load_json(paths["evidence_use_map"])
    guardrails = load_json(paths["draft_guardrails"])
    packets = load_json(paths["chapter_task_packets"])
    tasks = list(packets.get("chapter_tasks") or [])
    return {
        "lot_name": lot_name,
        "files": {key: str(value) for key, value in paths.items()},
        "statistics": packets.get("statistics") or {},
        "evidence_statistics": evidence_map.get("statistics") or {},
        "proposal_ready_chapters": [task_summary(item) for item in tasks if item.get("task_type") == "proposal_drafting"],
        "fact_blocked_chapters": [task_summary(item) for item in tasks if item.get("blocking_status") in {"blocked_by_evidence", "blocked_by_manual_review"}],
        "fatal_risk_count": len(list(guardrails.get("fatal_risk_items") or [])),
        "mandatory_requirement_count": len(list(pack.get("mandatory_requirements") or [])),
        "unresolved_resolution_count": len(list(pack.get("unresolved_reviews") or [])),
        "chapters": [
            {
                **task_summary(item),
                "score_item_ids": list((item.get("input_context") or {}).get("score_item_ids") or []),
                "requirement_ids": [req.get("requirement_id") for req in list(item.get("mandatory_requirements") or [])],
                "placeholder_count": len(list(item.get("placeholders") or [])),
            }
            for item in tasks
        ],
    }


def compile_writing_artifacts(
    lot_name: str,
    *,
    context: dict[str, Any],
    requirement_matrix: dict[str, Any],
    action_index: dict[str, Any],
    catalog: dict[str, Any],
    coverage: dict[str, Any],
    resolution_queue: dict[str, Any],
    intake_plan: dict[str, Any],
    project: dict[str, Any],
    source_paths: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    generated_at = datetime.now(timezone.utc).isoformat()
    requirements = list(requirement_matrix.get("requirements") or [])
    requirement_by_id = {str(item.get("requirement_id") or ""): item for item in requirements}
    evidence_requirements = list(coverage.get("evidence_requirements") or [])
    selected_actions = select_writing_actions(list(action_index.get("action_groups") or []))
    selected_requirement_ids = set(
        member_id
        for action in selected_actions
        for member_id in list(action.get("member_requirement_ids") or [])
    )
    for item in evidence_requirements:
        selected_requirement_ids.update(str(value) for value in list(item.get("source_requirement_ids") or []))
    selected_requirement_ids.update(
        str(item.get("requirement_id") or "")
        for item in requirements
        if is_fatal_requirement(item)
    )
    selected_requirement_ids.update(
        str(item.get("requirement_id") or "")
        for item in requirements
        if item.get("requirement_type") == "score_related_requirement"
        and any(str(score_id).startswith("technical_") for score_id in list(item.get("related_score_item_ids") or []))
    )
    selected_requirements = [item for item in requirements if str(item.get("requirement_id") or "") in selected_requirement_ids]
    evidence_map = build_evidence_use_map(
        lot_name,
        catalog,
        coverage,
        resolution_queue,
        generated_at,
    )
    guardrails = build_draft_guardrails(
        lot_name,
        requirements,
        context,
        evidence_map,
        generated_at,
    )
    score_items = build_score_items(context, project)
    chapters, tasks = build_chapters_and_tasks(
        context=context,
        score_items=score_items,
        selected_requirements=selected_requirements,
        selected_actions=selected_actions,
        evidence_requirements=evidence_requirements,
        coverage=coverage,
        evidence_map=evidence_map,
        resolution_queue=resolution_queue,
        intake_plan=intake_plan,
        project_conflicts=list(context.get("project_conflicts") or []),
    )
    outline = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": "writing_context_pack.json",
        "chapters": chapters,
        "statistics": {
            "chapter_count": len(chapters),
            "score_item_coverage_count": len(unique([score_id for item in chapters for score_id in item["related_score_item_ids"]])),
        },
        "build_warnings": [],
    }
    packets = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": "technical_response_outline_for_agent.json",
        "chapter_tasks": tasks,
        "execution_order": [item["task_id"] for item in tasks],
        "dependencies": build_task_dependencies(tasks),
        "statistics": build_task_statistics(tasks),
        "build_warnings": [],
    }
    global_rules = build_global_writing_rules()
    mandatory = [requirement_summary(item) for item in selected_requirements if item.get("compliance_level") in {"fatal_if_missing", "must_respond", "evidence_required"}]
    compliance_risks = [requirement_summary(item) for item in selected_requirements if item.get("risk_type") not in {None, "none"}]
    warnings = build_warnings(context, evidence_map, guardrails)
    pack = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "project": deepcopy(context.get("project") or {}),
        "project_conflicts": deepcopy(context.get("project_conflicts") or []),
        "source_paths": source_paths or {},
        "score_items": score_items,
        "mandatory_requirements": mandatory,
        "compliance_risks": compliance_risks,
        "actionable_requirements": [action_summary(item) for item in selected_actions],
        "evidence_summary": deepcopy(evidence_map.get("statistics") or {}),
        "evidence_usage_rules": evidence_usage_rules(),
        "unresolved_reviews": [resolution_summary(item) for item in list(resolution_queue.get("resolution_cases") or []) if item.get("current_status") not in {"resolved", "rejected", "superseded"}],
        "material_gaps": [intake_summary(item) for item in list(intake_plan.get("material_intake_packages") or []) if item.get("intake_status") != "complete"],
        "chapters": chapters,
        "global_writing_rules": global_rules,
        "build_warnings": warnings,
    }
    validate_compiled_outputs(pack, outline, evidence_map, guardrails, packets)
    return {
        "writing_context_pack": pack,
        "technical_response_outline_for_agent": outline,
        "evidence_use_map": evidence_map,
        "draft_guardrails": guardrails,
        "chapter_task_packets": packets,
    }


def writing_input_paths(lot_dir: Path) -> dict[str, Path]:
    return {
        "technical_response_context": lot_dir / "outline" / "technical_response_context.json",
        "tender_requirement_matrix": lot_dir / "requirements" / "tender_requirement_matrix.json",
        "actionable_requirement_index": lot_dir / "requirements" / "actionable_requirement_index.json",
        "enterprise_evidence_catalog": lot_dir / "evidence" / "enterprise_evidence_catalog.json",
        "evidence_coverage_matrix": lot_dir / "evidence" / "evidence_coverage_matrix.json",
        "evidence_resolution_queue": lot_dir / "evidence" / "evidence_resolution_queue.json",
        "material_intake_plan": lot_dir / "evidence" / "material_intake_plan.json",
        "tender_projects": Path("data") / "tender_projects.json",
    }


def writing_output_paths(writing_dir: Path) -> dict[str, Path]:
    return {
        "writing_context_pack": writing_dir / "writing_context_pack.json",
        "technical_response_outline_for_agent": writing_dir / "technical_response_outline_for_agent.json",
        "evidence_use_map": writing_dir / "evidence_use_map.json",
        "draft_guardrails": writing_dir / "draft_guardrails.json",
        "chapter_task_packets": writing_dir / "chapter_task_packets.json",
        "technical_response_outline_for_agent_markdown": writing_dir / "technical_response_outline_for_agent.md",
    }


def validate_inputs(lot_name: str, inputs: dict[str, dict[str, Any]]) -> None:
    for name, payload in inputs.items():
        if str(payload.get("lot_name") or "") != lot_name:
            raise ValueError(f"{name} lot_name mismatch: expected {lot_name}")
        if not payload.get("schema_version"):
            raise ValueError(f"{name} schema_version missing")
    if len(list(inputs["technical_response_context"].get("chapters") or [])) != 9:
        raise ValueError("technical_response_context must contain 9 technical score chapters")


def select_writing_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for item in actions:
        kinds = {str(item.get("action_kind") or "")} | set(item.get("secondary_action_kinds") or [])
        scope = (item.get("applicable_scope") or {}).get("scope_type")
        if "write_response" not in kinds:
            continue
        if not item.get("related_context_chapter_ids"):
            continue
        if scope not in WRITING_SCOPES:
            continue
        selected.append(item)
    return selected


def build_score_items(context: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    project_scores = {str(item.get("id") or ""): item for item in list(project.get("score_items") or [])}
    result = []
    for chapter in list(context.get("chapters") or []):
        for ref in list(chapter.get("score_refs") or []):
            score_id = str(ref.get("score_item_id") or "")
            raw = project_scores.get(score_id, {})
            result.append(
                {
                    "score_item_id": score_id,
                    "score_item_name": ref.get("score_item_name") or raw.get("item_name"),
                    "score": ref.get("score") if ref.get("score") is not None else raw.get("score"),
                    "score_range": deepcopy(ref.get("score_range") or raw.get("score_range") or {}),
                    "score_rule": ref.get("score_rule") or raw.get("score_rule"),
                    "source_location": deepcopy(ref.get("source_location") or {}),
                    "source_type": "tender_original_fact",
                    "chapter_id": chapter.get("chapter_id"),
                }
            )
    return result


def build_evidence_use_map(
    lot_name: str,
    catalog: dict[str, Any],
    coverage: dict[str, Any],
    resolution_queue: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    relations_by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in list(coverage.get("support_relations") or []):
        relations_by_evidence[str(relation.get("evidence_id") or "")].append(relation)
    evidence_requirements = {
        str(item.get("evidence_requirement_id") or ""): item
        for item in list(coverage.get("evidence_requirements") or [])
    }
    resolutions_by_evidence: dict[str, list[str]] = defaultdict(list)
    for case in list(resolution_queue.get("resolution_cases") or []):
        if case.get("current_status") in {"resolved", "rejected", "superseded"}:
            continue
        for evidence_id in list(case.get("related_evidence_ids") or []):
            resolutions_by_evidence[str(evidence_id)].append(str(case.get("resolution_id") or ""))

    items = []
    for evidence in list(catalog.get("evidence_items") or []):
        evidence_id = str(evidence.get("evidence_id") or "")
        relations = relations_by_evidence.get(evidence_id, [])
        statuses = {str(item.get("usability_status") or "unusable") for item in relations}
        is_mock = "mock" in f"{evidence.get('title', '')} {evidence.get('source_file_name', '')}".lower()
        rejected = evidence.get("verification_status") == "rejected"
        strict_confirmed = any(
            item.get("usability_status") == "confirmed"
            and evidence_has_traceable_source(evidence)
            and item.get("verification_status") != "rejected"
            for item in relations
        )
        if is_mock or rejected:
            allowed = "prohibited"
            aggregate_status = "unusable"
        elif strict_confirmed:
            allowed = "factual_claim"
            aggregate_status = "confirmed"
        elif "candidate" in statuses:
            allowed = "cautious_reference"
            aggregate_status = "candidate"
        elif "needs_review" in statuses:
            allowed = "placeholder_only"
            aggregate_status = "needs_review"
        else:
            allowed = "prohibited"
            aggregate_status = "unusable"
        relevant_relations = (
            relations
            if allowed == "prohibited"
            else [item for item in relations if item.get("usability_status") in {"confirmed", "candidate", "needs_review"}]
        )
        requirement_ids = unique([value for item in relevant_relations for value in list(item.get("matched_requirement_ids") or [])])
        score_ids = unique([value for item in relevant_relations for value in list(item.get("matched_score_item_ids") or [])])
        chapter_ids = unique(
            [
                chapter_id
                for item in relevant_relations
                for chapter_id in list(evidence_requirements.get(str(item.get("evidence_requirement_id") or ""), {}).get("related_context_chapter_ids") or [])
            ]
        )
        strengths = [str(item.get("support_strength") or "none") for item in relations]
        permitted = unique([value for item in relations for value in list(item.get("permitted_claims") or [])])
        restricted = unique([value for item in relations for value in list(item.get("restricted_claims") or [])])
        rejection_reasons = unique([value for item in relations for value in list(item.get("rejection_reasons") or [])])
        items.append(
            {
                "evidence_id": evidence_id,
                "evidence_type": evidence.get("evidence_type"),
                "title": evidence.get("title"),
                "source_ref": evidence_source_ref(evidence),
                "usability_status": aggregate_status,
                "verification_status": evidence.get("verification_status") or "unverified",
                "support_strength": strongest_strength(strengths),
                "related_score_item_ids": score_ids,
                "related_requirement_ids": requirement_ids,
                "related_chapter_ids": chapter_ids,
                "allowed_usage": allowed,
                "allowed_claims": permitted if allowed in {"factual_claim", "supporting_reference"} else [],
                "cautious_claims": restricted if allowed in {"cautious_reference", "placeholder_only"} else [],
                "forbidden_claims": unique(rejection_reasons + (["该材料不得作为企业确定事实使用。"] if allowed == "prohibited" else [])),
                "quantity_eligible": bool(allowed in {"factual_claim", "supporting_reference"} and any(item.get("quantity_eligible") for item in relations)),
                "citation_instruction": citation_instruction(evidence, allowed),
                "decision_ref_ids": unique(list(evidence.get("decision_ref_ids") or []) + [value for item in relations for value in list(item.get("decision_ref_ids") or [])]),
                "unresolved_resolution_ids": unique(resolutions_by_evidence.get(evidence_id, [])),
                "usage_priority": "high" if any(str(value).startswith("technical_") for value in score_ids) else "medium",
                "source_type": "system_judgement",
            }
        )
    counts = Counter(str(item.get("allowed_usage") or "prohibited") for item in items)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": "evidence_coverage_matrix.json",
        "evidence_usage_items": items,
        "blocked_evidence_ids": [item["evidence_id"] for item in items if item["allowed_usage"] == "prohibited"],
        "unresolved_evidence_ids": [item["evidence_id"] for item in items if item["unresolved_resolution_ids"]],
        "statistics": {
            "evidence_count": len(items),
            "confirmed_evidence_count": counts.get("factual_claim", 0) + counts.get("supporting_reference", 0),
            "cautious_evidence_count": counts.get("cautious_reference", 0) + counts.get("placeholder_only", 0),
            "prohibited_evidence_count": counts.get("prohibited", 0),
            "allowed_usage_counts": dict(sorted(counts.items())),
        },
        "build_warnings": [],
    }


def build_draft_guardrails(
    lot_name: str,
    requirements: list[dict[str, Any]],
    context: dict[str, Any],
    evidence_map: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    fatal = [requirement_summary(item) for item in requirements if is_fatal_requirement(item)]
    mandatory = [requirement_summary(item) for item in requirements if item.get("compliance_level") == "must_respond" and is_applicable(item)]
    format_constraints = [
        requirement_summary(item)
        for item in requirements
        if item.get("requirement_type") in {"signature_seal_requirement", "format_submission_requirement"} and is_applicable(item)
    ]
    chapter_guardrails = []
    for chapter in list(context.get("chapters") or []):
        score_ids = [str(item.get("score_item_id") or "") for item in list(chapter.get("score_refs") or [])]
        chapter_guardrails.append(
            {
                "chapter_id": chapter.get("chapter_id"),
                "score_item_ids": score_ids,
                "rules": chapter_specific_rules(score_ids[0] if score_ids else ""),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": ["tender_requirement_matrix.json", "evidence_use_map.json"],
        "global_forbidden_claims": [
            "编造企业业绩。",
            "编造项目负责人或团队人员。",
            "编造人员部门、角色和投入状态。",
            "编造证书名称、编号和有效期。",
            "编造专利、软件著作权和技术成果。",
            "编造管理体系认证或绩效评价结果。",
            "使用 mock 业绩。",
            "将人员简历中的项目经历表述为企业业绩。",
            "将 candidate 或 needs_review 材料表述为 confirmed。",
            "将缺失材料表述为已提供。",
            "在证据覆盖不足时声称满足满分条件。",
            "生成投标决策性结论。",
        ],
        "forbidden_evidence_ids": deepcopy(evidence_map.get("blocked_evidence_ids") or []),
        "unresolved_fact_patterns": ["人员身份、部门、角色或投入状态待确认", "证书主体、编号或有效期待确认", "业绩、专利、绩效或成果材料缺失"],
        "mandatory_response_items": mandatory,
        "fatal_risk_items": fatal,
        "signature_and_format_constraints": format_constraints,
        "proposal_expression_rules": ["使用拟采取、将建立、将实施、计划、方案、承诺等未来时或方案性表达。", "不得把拟议机制写成企业既有案例或已完成事实。"],
        "enterprise_fact_expression_rules": ["只有 evidence_use_map.allowed_usage 为 factual_claim 或 supporting_reference 的材料可形成确定事实。", "cautious_reference 和 placeholder_only 只能进入复核提示或显式占位。"],
        "placeholder_rules": ["占位符必须保留关联证据需求、resolution 和补录包 ID。", "关键占位符未替换前不得进入正式终稿。"],
        "citation_rules": ["企业事实必须引用 evidence_id 和真实 source_ref。", "招标要求必须引用 requirement_id 或 score_item_id，不得虚构页码和单元格。"],
        "chapter_specific_guardrails": chapter_guardrails,
        "validation_rules": [
            "confirmed_facts 中不得出现非 confirmed 证据。",
            "forbidden_evidence_ids 不得进入 allowed claims。",
            "每章必须保留 score_item_id 和适用 requirement_id。",
            "不得从 Markdown 读取或恢复业务事实。",
            "不得生成正式章节正文。",
        ],
    }


def build_chapters_and_tasks(
    *,
    context: dict[str, Any],
    score_items: list[dict[str, Any]],
    selected_requirements: list[dict[str, Any]],
    selected_actions: list[dict[str, Any]],
    evidence_requirements: list[dict[str, Any]],
    coverage: dict[str, Any],
    evidence_map: dict[str, Any],
    resolution_queue: dict[str, Any],
    intake_plan: dict[str, Any],
    project_conflicts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    score_by_chapter = {str(item.get("chapter_id") or ""): item for item in score_items}
    requirements_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected_requirements:
        for chapter_id in list(item.get("related_context_chapter_ids") or []):
            requirements_by_chapter[str(chapter_id)].append(item)
    actions_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected_actions:
        for chapter_id in list(item.get("related_context_chapter_ids") or []):
            actions_by_chapter[str(chapter_id)].append(item)
    evidence_requirements_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evidence_requirements:
        for chapter_id in list(item.get("related_context_chapter_ids") or []):
            evidence_requirements_by_chapter[str(chapter_id)].append(item)
    usage_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in list(evidence_map.get("evidence_usage_items") or []):
        for chapter_id in list(item.get("related_chapter_ids") or []):
            usage_by_chapter[str(chapter_id)].append(item)
    resolutions = list(resolution_queue.get("resolution_cases") or [])
    intakes = list(intake_plan.get("material_intake_packages") or [])

    chapters: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for source_chapter in sorted(list(context.get("chapters") or []), key=lambda item: item.get("chapter_order") or 0):
        chapter_id = str(source_chapter.get("chapter_id") or "")
        score = score_by_chapter.get(chapter_id, {})
        score_id = str(score.get("score_item_id") or "")
        chapter_requirements = unique_dicts_by_id(requirements_by_chapter.get(chapter_id, []), "requirement_id")
        chapter_actions = unique_dicts_by_id(actions_by_chapter.get(chapter_id, []), "action_group_id")
        chapter_evidence_requirements = unique_dicts_by_id(evidence_requirements_by_chapter.get(chapter_id, []), "evidence_requirement_id")
        chapter_requirement_ids = unique(
            [str(item.get("requirement_id") or "") for item in chapter_requirements]
            + [str(value) for item in chapter_evidence_requirements for value in list(item.get("source_requirement_ids") or [])]
        )
        chapter_resolutions = [
            item
            for item in resolutions
            if item.get("current_status") not in {"resolved", "rejected", "superseded"}
            and (
                chapter_id in list(item.get("related_context_chapter_ids") or [])
                or bool(set(item.get("related_evidence_requirement_ids") or []) & {req.get("evidence_requirement_id") for req in chapter_evidence_requirements})
            )
        ]
        evidence_requirement_ids = {str(item.get("evidence_requirement_id") or "") for item in chapter_evidence_requirements}
        chapter_intakes = [
            item
            for item in intakes
            if bool(set(item.get("related_evidence_requirement_ids") or []) & evidence_requirement_ids)
            or score_id in list(item.get("related_score_item_ids") or [])
        ]
        usage = usage_by_chapter.get(chapter_id, [])
        placeholders = build_chapter_placeholders(
            score_id,
            chapter_evidence_requirements,
            chapter_resolutions,
            chapter_intakes,
            coverage,
            project_conflicts,
        )
        task_type, blocking_status = chapter_task_status(score_id, usage, chapter_resolutions, placeholders)
        confirmed = [item for item in usage if item.get("allowed_usage") in {"factual_claim", "supporting_reference"}]
        cautious = [item for item in usage if item.get("allowed_usage") in {"cautious_reference", "placeholder_only"}]
        forbidden = [item for item in usage if item.get("allowed_usage") == "prohibited"]
        related_risks = [item for item in chapter_requirements if item.get("risk_type") not in {None, "none"}]
        proposal = PROPOSAL_CONTENT.get(score_id, ["按招标要求组织响应内容"])
        structure = RECOMMENDED_STRUCTURE.get(score_id, ["评分要求", "响应内容", "证明材料", "复核事项"])
        chapter = {
            "chapter_id": chapter_id,
            "chapter_order": source_chapter.get("chapter_order"),
            "chapter_title": source_chapter.get("chapter_title"),
            "chapter_purpose": chapter_purpose(score_id, source_chapter.get("chapter_title")),
            "related_score_item_ids": [score_id] if score_id else [],
            "related_requirement_ids": chapter_requirement_ids,
            "related_action_group_ids": [item.get("action_group_id") for item in chapter_actions],
            "related_compliance_risk_ids": [item.get("requirement_id") for item in related_risks],
            "related_technical_spec_refs": deepcopy(source_chapter.get("technical_spec_refs") or []),
            "scoring_goals": [score_goal(score)] if score else [],
            "mandatory_response_goals": [requirement_summary(item) for item in chapter_requirements if item.get("compliance_level") in {"fatal_if_missing", "must_respond", "evidence_required"}],
            "must_write_points": build_must_write_points(score, chapter_actions, chapter_requirements),
            "optional_enhancement_points": proposal[1:] if len(proposal) > 1 else [],
            "proposal_content_to_generate": proposal,
            "confirmed_evidence_ids": [item.get("evidence_id") for item in confirmed],
            "cautious_evidence_ids": [item.get("evidence_id") for item in cautious],
            "forbidden_evidence_ids": [item.get("evidence_id") for item in forbidden],
            "unresolved_resolution_ids": [item.get("resolution_id") for item in chapter_resolutions],
            "missing_evidence_requirement_ids": [item.get("evidence_requirement_id") for item in chapter_evidence_requirements if not chapter_requirement_has_confirmed(item, coverage)],
            "material_intake_package_ids": [item.get("intake_package_id") for item in chapter_intakes],
            "recommended_structure": structure,
            "writing_strategy": writing_strategy(score_id, blocking_status),
            "claim_boundaries": claim_boundaries(score_id, bool(confirmed)),
            "required_placeholders": placeholders,
            "expected_output_sections": structure,
            "review_checkpoints": review_checkpoints(score_id, chapter_requirements, placeholders),
            "source_type": "system_judgement",
        }
        task_id = f"writing-{stable_suffix(chapter_id)}"
        task = {
            "task_id": task_id,
            "chapter_id": chapter_id,
            "chapter_title": source_chapter.get("chapter_title"),
            "task_type": task_type,
            "input_context": {
                "content_categories": ["tender_fact", "confirmed_enterprise_fact", "cautious_enterprise_reference", "proposal_content", "missing_or_blocked_fact"],
                "project": deepcopy(context.get("project") or {}),
                "project_conflicts": [deepcopy(item) for item in project_conflicts if item.get("status") == "needs_human_confirmation"],
                "score_item_ids": [score_id] if score_id else [],
                "requirement_ids": chapter_requirement_ids,
                "action_group_ids": [item.get("action_group_id") for item in chapter_actions],
                "evidence_requirement_ids": [item.get("evidence_requirement_id") for item in chapter_evidence_requirements],
            },
            "scoring_targets": deepcopy(chapter["scoring_goals"]),
            "mandatory_requirements": deepcopy(chapter["mandatory_response_goals"]),
            "compliance_constraints": [requirement_summary(item) for item in related_risks],
            "technical_requirements": deepcopy(source_chapter.get("technical_spec_refs") or []),
            "content_plan": structure,
            "proposal_content_allowed": proposal,
            "confirmed_facts": [usage_fact_summary(item) for item in confirmed],
            "cautious_references": [usage_fact_summary(item) for item in cautious],
            "forbidden_facts": [usage_fact_summary(item) for item in forbidden],
            "missing_materials": [intake_summary(item) for item in chapter_intakes],
            "placeholders": placeholders,
            "drafting_instructions": drafting_instructions(score_id, task_type),
            "output_contract": {
                "artifact_type": "chapter_drafting_input",
                "required_sections": structure,
                "must_preserve_ids": ["score_item_id", "requirement_id", "evidence_id", "placeholder_id"],
                "formal_body_generation_allowed": False,
            },
            "validation_checklist": task_validation_checklist(score_id),
            "blocking_status": blocking_status,
        }
        chapters.append(chapter)
        tasks.append(task)
    return chapters, tasks


def build_chapter_placeholders(
    score_id: str,
    evidence_requirements: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    intakes: list[dict[str, Any]],
    coverage: dict[str, Any],
    project_conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    placeholders: list[dict[str, Any]] = []
    conflict = next((item for item in project_conflicts if item.get("field") == "package_name" and item.get("status") == "needs_human_confirmation"), None)
    if conflict:
        placeholders.append(
            make_placeholder(
                "project_conflict",
                "[待确认：当前包名称冲突，章节标题和项目称谓须在终稿前统一]",
                "确认 package_name 的最终使用值",
                [],
                ["confirm-project-package_name"],
                [],
                "blocks_final_export",
            )
        )
    score_coverage = {str(item.get("score_item_id") or ""): item for item in list(coverage.get("score_item_coverage") or [])}.get(score_id, {})
    if score_id == "technical_04_业绩情况":
        text = f"[待补充：{score_coverage.get('required_count') or 5}项符合评分规则的企业业绩证据包]"
        required_fact = "独立企业业绩及合同、验收或用户证明"
    elif score_id == "technical_08_专利数量":
        text = f"[待补充：{score_coverage.get('required_count') or 2}项与采购主题相关的有效专利证明]"
        required_fact = "专利名称、编号、权利人、授权状态和相关性证明"
    elif score_id == "technical_02_项目负责人情况":
        text = "[待确认：项目负责人姓名、部门、拟任角色及可投入性]"
        required_fact = "项目负责人身份、部门、角色、投入状态、经历和证书"
    elif score_id == "technical_03_项目团队人员结构":
        text = "[待确认：项目团队人员名单、部门、岗位、专业结构及可投入性]"
        required_fact = "项目团队人员结构和可投入证明"
    elif score_id == "technical_07_工作基础":
        text = "[待补充：与采购项目相关的产品化、科技成果或专题研究证明]"
        required_fact = "工作基础对应的产品、成果或专题研究证明"
    elif score_id == "technical_09_绩效评价":
        text = "[待补充：最近一年度适用主体的供应商绩效评价证明]"
        required_fact = "绩效评价年度、主体、等级或分值及来源证明"
    else:
        text = ""
        required_fact = ""
    if text and evidence_requirements:
        placeholders.append(
            make_placeholder(
                "evidence_gap",
                text,
                required_fact,
                [item.get("evidence_requirement_id") for item in evidence_requirements],
                [item.get("resolution_id") for item in resolutions],
                [item.get("intake_package_id") for item in intakes],
                "blocks_enterprise_fact",
            )
        )
    return unique_dicts_by_id(placeholders, "placeholder_id")


def make_placeholder(
    placeholder_type: str,
    display_text: str,
    required_fact: str,
    evidence_requirement_ids: list[Any],
    resolution_ids: list[Any],
    intake_ids: list[Any],
    blocking_level: str,
) -> dict[str, Any]:
    return {
        "placeholder_id": f"placeholder-{stable_suffix(f'{placeholder_type}|{display_text}')}",
        "placeholder_type": placeholder_type,
        "display_text": display_text,
        "required_fact": required_fact,
        "related_evidence_requirement_ids": unique(evidence_requirement_ids),
        "related_resolution_ids": unique(resolution_ids),
        "related_intake_package_ids": unique(intake_ids),
        "blocking_level": blocking_level,
        "replacement_rule": "仅在对应事实完成确认且正式证据覆盖矩阵更新后替换；否则保留占位并阻止进入终稿。",
        "source_type": "human_confirmation_required" if placeholder_type == "project_conflict" else "suggested_material",
    }


def chapter_task_status(
    score_id: str,
    usage: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    placeholders: list[dict[str, Any]],
) -> tuple[str, str]:
    if score_id in PROPOSAL_SCORE_IDS:
        return "proposal_drafting", "ready_with_placeholders" if placeholders else "ready"
    if score_id in PERSON_SCORE_IDS:
        return "mixed_drafting", "blocked_by_manual_review" if resolutions else "blocked_by_evidence"
    confirmed = any(item.get("allowed_usage") in {"factual_claim", "supporting_reference"} for item in usage)
    if confirmed:
        return "evidence_based_drafting", "partially_ready" if placeholders else "ready"
    return "evidence_based_drafting", "blocked_by_evidence"


def update_context_writing_index(
    context: dict[str, Any],
    packets: dict[str, Any],
    paths: dict[str, Path],
) -> None:
    context["writing_context_pack_path"] = str(paths["writing_context_pack"])
    context["agent_outline_path"] = str(paths["technical_response_outline_for_agent"])
    context["evidence_use_map_path"] = str(paths["evidence_use_map"])
    context["draft_guardrails_path"] = str(paths["draft_guardrails"])
    context["chapter_task_packets_path"] = str(paths["chapter_task_packets"])
    task_by_chapter = {str(item.get("chapter_id") or ""): item for item in list(packets.get("chapter_tasks") or [])}
    for chapter in list(context.get("chapters") or []):
        task = task_by_chapter.get(str(chapter.get("chapter_id") or ""))
        chapter["chapter_task_ids"] = [task.get("task_id")] if task else []
        chapter["chapter_blocking_status"] = task.get("blocking_status") if task else "not_applicable"


def render_agent_outline_markdown(outline: dict[str, Any], packets: dict[str, Any]) -> str:
    tasks = {str(item.get("chapter_id") or ""): item for item in list(packets.get("chapter_tasks") or [])}
    lines = [
        f"# 撰写 Agent 技术响应大纲：{outline.get('lot_name')}",
        "",
        "> 本文件仅供人工查看，由正式 JSON 派生，不参与下游计算，也不包含正式技术响应正文。",
        "",
    ]
    for chapter in list(outline.get("chapters") or []):
        task = tasks.get(str(chapter.get("chapter_id") or ""), {})
        lines.extend(
            [
                f"## {chapter.get('chapter_order')}. {chapter.get('chapter_title')}",
                "",
                f"- task_id：`{task.get('task_id') or '-'}`",
                f"- task_type：`{task.get('task_type') or '-'}`",
                f"- blocking_status：`{task.get('blocking_status') or '-'}`",
                f"- 评分项：{'、'.join(chapter.get('related_score_item_ids') or []) or '-'}",
                f"- 写作要求：{len(chapter.get('related_requirement_ids') or [])} 条",
                f"- 可确定使用证据：{len(chapter.get('confirmed_evidence_ids') or [])}",
                f"- 谨慎/占位证据：{len(chapter.get('cautious_evidence_ids') or [])}",
                f"- 禁用证据：{len(chapter.get('forbidden_evidence_ids') or [])}",
                f"- 推荐结构：{'；'.join(chapter.get('recommended_structure') or [])}",
                "",
            ]
        )
        for placeholder in list(chapter.get("required_placeholders") or []):
            lines.append(f"- {placeholder.get('display_text')}")
        if chapter.get("required_placeholders"):
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_global_writing_rules() -> dict[str, Any]:
    return {
        "writing_goal": "形成按章节可执行、可追溯的撰写任务输入，不生成正式技术响应正文。",
        "response_tone": ["专业", "准确", "逐条响应", "方案性表达清晰", "不夸大企业能力"],
        "factuality_rules": ["招标事实保持 requirement_id、score_item_id 和来源定位。", "归纳、分类和建议不得伪装为招标原文。"],
        "evidence_rules": ["企业已有事实只能使用 confirmed evidence。", "candidate 和 needs_review 不得写成确定事实。", "rejected、unusable 和 mock 材料禁止使用。"],
        "proposal_language_rules": ["方案内容使用拟采取、将建立、将实施、计划或承诺等表达。", "不得把方案写成已实施案例。"],
        "mandatory_response_rules": ["必须响应事项逐项保留 requirement_id。", "不得遗漏技术规范、交付、验收、保密和安全要求。"],
        "score_alignment_rules": ["每章围绕关联评分项和满分条件组织。", "证据不足时不得声称满足满分条件。"],
        "prohibited_content": ["编造业绩、人员、证书、专利、绩效或材料状态。", "生成投标决策性结论。", "虚构页码、章节或表格坐标。"],
        "placeholder_rules": ["未确认事实使用显式结构化占位符。", "关键占位符未替换前阻止进入终稿。"],
        "citation_rules": ["招标事实引用 requirement_id 或 score_item_id。", "企业事实引用 evidence_id 和 source_ref。"],
        "manual_review_rules": ["包名称冲突和证据 resolution 未解决时保留人工确认标记。", "不得由撰写 Agent 自行补齐人工确认字段。"],
    }


def evidence_usage_rules() -> dict[str, Any]:
    return {
        "confirmed_enterprise_fact": "仅 usability_status=confirmed、来源可追溯且未被 reject 的证据。",
        "cautious_enterprise_reference": "candidate 或 needs_review 只能用于复核提示、备选材料和待确认占位。",
        "missing_or_blocked_fact": "unusable、rejected、missing、mock 和未解决人工确认项不得生成事实。",
        "proposal_content": "可根据招标要求生成未来时、方案性和承诺性内容，但不得推导企业既有能力。",
    }


def is_fatal_requirement(item: dict[str, Any]) -> bool:
    text = str(item.get("raw_text") or "")
    if item.get("compliance_level") != "fatal_if_missing":
        return False
    if item.get("polarity") == "negative_exception":
        return False
    return not any(term in text for term in ["并不否决", "不否决", "不作为否决条件", "不得作为否决依据", "不影响投标有效性"])


def is_applicable(item: dict[str, Any]) -> bool:
    return (item.get("applicable_scope") or {}).get("applicable") is not False


def evidence_has_traceable_source(item: dict[str, Any]) -> bool:
    return bool(item.get("source_file_id") and (item.get("excerpt") or item.get("visual_source_ref")))


def evidence_source_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file_id": item.get("source_file_id"),
        "source_file_name": item.get("source_file_name"),
        "source_location": deepcopy(item.get("source_location") or {}),
        "location_precision": item.get("location_precision") or "unknown",
        "excerpt": item.get("excerpt") or "",
        "visual_source_ref": deepcopy(item.get("visual_source_ref")),
        "source_type": "enterprise_existing_fact",
    }


def citation_instruction(evidence: dict[str, Any], allowed: str) -> str:
    if allowed == "prohibited":
        return "禁止引用。"
    if allowed in {"cautious_reference", "placeholder_only"}:
        return "仅在人工复核提示中引用 evidence_id 和来源，不得形成企业确定事实。"
    if evidence.get("excerpt"):
        return "引用 evidence_id、source_file_id、source_location 和摘录；仅陈述已确认字段。"
    return "仅可引用人工视觉确认记录及 visual_source_ref，不得补写不存在的文本摘录。"


def strongest_strength(values: list[str]) -> str:
    order = {"strong": 0, "medium": 1, "weak": 2, "none": 3}
    return sorted(values or ["none"], key=lambda value: order.get(value, 3))[0]


def requirement_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "requirement_id": item.get("requirement_id"),
        "title": item.get("title"),
        "requirement_type": item.get("requirement_type"),
        "compliance_level": item.get("compliance_level"),
        "risk_type": item.get("risk_type"),
        "raw_text": item.get("raw_text"),
        "required_action": deepcopy(item.get("required_action") or {}),
        "source_ref": deepcopy(item.get("source_ref") or {}),
        "related_score_item_ids": deepcopy(item.get("related_score_item_ids") or []),
        "related_context_chapter_ids": deepcopy(item.get("related_context_chapter_ids") or []),
        "source_type": "tender_original_fact",
    }


def action_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_group_id": item.get("action_group_id"),
        "canonical_title": item.get("canonical_title"),
        "member_requirement_ids": deepcopy(item.get("member_requirement_ids") or []),
        "action_kind": item.get("action_kind"),
        "secondary_action_kinds": deepcopy(item.get("secondary_action_kinds") or []),
        "action_priority": item.get("action_priority"),
        "response_content_needed": deepcopy(item.get("response_content_needed") or []),
        "related_score_item_ids": deepcopy(item.get("related_score_item_ids") or []),
        "related_context_chapter_ids": deepcopy(item.get("related_context_chapter_ids") or []),
        "source_type": "system_judgement",
    }


def resolution_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_id": item.get("resolution_id"),
        "resolution_subject_type": item.get("resolution_subject_type"),
        "canonical_question": item.get("canonical_question"),
        "current_status": item.get("current_status"),
        "blocking_level": item.get("blocking_level"),
        "related_evidence_ids": deepcopy(item.get("related_evidence_ids") or []),
        "related_context_chapter_ids": deepcopy(item.get("related_context_chapter_ids") or []),
        "source_type": "human_confirmation_required",
    }


def intake_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "intake_package_id": item.get("intake_package_id"),
        "title": item.get("title"),
        "related_evidence_requirement_ids": deepcopy(item.get("related_evidence_requirement_ids") or []),
        "related_score_item_ids": deepcopy(item.get("related_score_item_ids") or []),
        "requested_count": item.get("requested_count"),
        "confirmed_count": item.get("confirmed_count"),
        "remaining_count": item.get("remaining_count"),
        "requested_material_components": deepcopy(item.get("requested_material_components") or []),
        "intake_status": item.get("intake_status"),
        "source_type": "suggested_material",
    }


def usage_fact_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "evidence_type": item.get("evidence_type"),
        "title": item.get("title"),
        "allowed_usage": item.get("allowed_usage"),
        "source_ref": deepcopy(item.get("source_ref") or {}),
        "allowed_claims": deepcopy(item.get("allowed_claims") or []),
        "cautious_claims": deepcopy(item.get("cautious_claims") or []),
        "forbidden_claims": deepcopy(item.get("forbidden_claims") or []),
        "unresolved_resolution_ids": deepcopy(item.get("unresolved_resolution_ids") or []),
    }


def score_goal(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "score_item_id": item.get("score_item_id"),
        "score_item_name": item.get("score_item_name"),
        "max_score": (item.get("score_range") or {}).get("max", item.get("score")),
        "score_rule": item.get("score_rule"),
        "source_location": deepcopy(item.get("source_location") or {}),
        "source_type": "tender_original_fact",
    }


def build_must_write_points(score: dict[str, Any], actions: list[dict[str, Any]], requirements: list[dict[str, Any]]) -> list[str]:
    points = []
    if score.get("score_rule"):
        points.append(f"按评分规则逐项响应：{score.get('score_rule')}")
    points.extend(value for item in actions for value in list(item.get("response_content_needed") or []))
    points.extend(str((item.get("normalized_requirement") or {}).get("text") or item.get("raw_text") or "") for item in requirements if item.get("compliance_level") == "must_respond")
    return unique([value for value in points if value])


def chapter_requirement_has_confirmed(item: dict[str, Any], coverage: dict[str, Any]) -> bool:
    req_id = str(item.get("evidence_requirement_id") or "")
    relations = [relation for relation in list(coverage.get("support_relations") or []) if str(relation.get("evidence_requirement_id") or "") == req_id]
    return any(relation.get("usability_status") == "confirmed" for relation in relations)


def chapter_purpose(score_id: str, title: Any) -> str:
    if score_id in PROPOSAL_SCORE_IDS:
        return f"围绕“{title}”评分规则、技术规范和必须响应要求形成可执行方案任务。"
    if score_id in PERSON_SCORE_IDS:
        return f"建立“{title}”的岗位与组织方案骨架，并等待人员事实确认后补充确定内容。"
    return f"建立“{title}”的证据化响应结构，在材料确认前仅保留规则、索引和占位。"


def writing_strategy(score_id: str, blocking_status: str) -> str:
    if score_id in PROPOSAL_SCORE_IDS:
        return "优先编制完整方案结构；企业经历和既有能力仅在 confirmed evidence 存在时引用。"
    if score_id in PERSON_SCORE_IDS:
        return "可编制职责、能力要求和管理机制；具体姓名、部门、角色、投入状态和经历保持占位。"
    return f"以证据清单和评分条件为核心，当前状态为 {blocking_status}；不得用建议内容替代企业事实。"


def claim_boundaries(score_id: str, has_confirmed: bool) -> dict[str, list[str]]:
    allowed = ["引用招标评分规则和技术要求。", "生成未来时、方案性或材料组织说明。"]
    if has_confirmed:
        allowed.append("在 evidence_use_map 允许范围内引用已确认企业事实。")
    forbidden = ["把 candidate、needs_review 或缺失材料写成企业已具备事实。", "声称证据不足的评分项已经满足满分条件。"]
    if score_id in PERSON_SCORE_IDS:
        forbidden.append("在人工确认前写入具体人员姓名、部门、角色、投入状态、证书或经历。")
    return {"allowed": allowed, "forbidden": forbidden}


def review_checkpoints(score_id: str, requirements: list[dict[str, Any]], placeholders: list[dict[str, Any]]) -> list[str]:
    result = ["核对 score_item_id 和 requirement_id 是否完整保留。", "核对所有企业事实是否来自 confirmed evidence。"]
    if requirements:
        result.append("核对必须响应要求是否逐条覆盖且未改变原文语义。")
    if placeholders:
        result.append("核对关键占位符是否仍存在；存在时不得进入终稿。")
    if score_id in PERSON_SCORE_IDS:
        result.append("核对人员姓名、部门、角色和投入状态是否已有有效人工决定。")
    return result


def drafting_instructions(score_id: str, task_type: str) -> list[str]:
    result = ["本任务只定义后续撰写输入和边界，本阶段不得生成正式正文。", "保留所有评分、要求、证据和占位符 ID。"]
    if task_type == "proposal_drafting":
        result.append("方案内容可完整规划，但统一使用未来时、方案性或承诺性表达。")
    else:
        result.append("企业事实仅从 confirmed_facts 读取；cautious_references 不得改写为确定事实。")
    if score_id in PERSON_SCORE_IDS:
        result.append("未确认人员信息只生成岗位职责、能力要求、管理机制和工作界面。")
    return result


def task_validation_checklist(score_id: str) -> list[str]:
    result = ["confirmed_facts 均来自 allowed_usage=factual_claim/supporting_reference。", "mock、rejected 和 unusable 证据只出现在 forbidden_facts。", "没有投标决策性结论。", "没有虚构来源位置。"]
    if score_id in {"technical_04_业绩情况", "technical_08_专利数量", "technical_09_绩效评价"}:
        result.append("数量和质量缺口保持为占位，未声称满足满分条件。")
    return result


def chapter_specific_rules(score_id: str) -> list[str]:
    if score_id in PERSON_SCORE_IDS:
        return ["人员事实未确认前，不得写具体姓名、部门、角色、投入状态、证书和项目经历。", "允许生成岗位职责、能力要求和组织管理机制。"]
    if score_id == "technical_04_业绩情况":
        return ["人员简历项目经历不得作为企业业绩。", "独立业绩数量只统计 confirmed 且完整的证据包。"]
    if score_id == "technical_08_专利数量":
        return ["不得编造专利名称、编号、权利人、授权状态或相关性。"]
    if score_id == "technical_09_绩效评价":
        return ["不得编造评价年度、等级、分值或评价主体。"]
    return ["方案性内容使用未来时，不得声称已有未确认案例或能力。"]


def build_task_dependencies(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first = tasks[0]["task_id"] if tasks else None
    return [
        {"task_id": item["task_id"], "depends_on": [] if item["task_id"] == first else ([first] if first else []), "dependency_type": "outline_consistency"}
        for item in tasks
    ]


def build_task_statistics(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts = Counter(str(item.get("task_type") or "not_applicable") for item in tasks)
    status_counts = Counter(str(item.get("blocking_status") or "not_applicable") for item in tasks)
    placeholders = [placeholder for item in tasks for placeholder in list(item.get("placeholders") or [])]
    return {
        "chapter_count": len(tasks),
        "task_type_counts": dict(sorted(type_counts.items())),
        "blocking_status_counts": dict(sorted(status_counts.items())),
        "placeholder_count": len({str(item.get("placeholder_id") or "") for item in placeholders}),
        "placeholder_occurrence_count": len(placeholders),
        "mandatory_requirement_coverage_count": len({str(req.get("requirement_id") or "") for item in tasks for req in list(item.get("mandatory_requirements") or [])}),
        "unresolved_resolution_count": len({str(value) for item in tasks for placeholder in list(item.get("placeholders") or []) for value in list(placeholder.get("related_resolution_ids") or [])}),
    }


def build_warnings(context: dict[str, Any], evidence_map: dict[str, Any], guardrails: dict[str, Any]) -> list[str]:
    warnings = []
    if any(item.get("field") == "package_name" and item.get("status") == "needs_human_confirmation" for item in list(context.get("project_conflicts") or [])):
        warnings.append("包名称仍需人工确认；所有章节保留同一项目字段冲突占位，终稿前必须统一。")
    if (evidence_map.get("statistics") or {}).get("confirmed_evidence_count", 0) == 0:
        warnings.append("当前无 confirmed enterprise evidence；方案型章节可继续，企业事实型章节仅生成结构和占位。")
    if guardrails.get("fatal_risk_items"):
        warnings.append("存在明确 fatal 风险条目，已进入全局 guardrails；本阶段不作投标决策。")
    return warnings


def validate_compiled_outputs(
    pack: dict[str, Any],
    outline: dict[str, Any],
    evidence_map: dict[str, Any],
    guardrails: dict[str, Any],
    packets: dict[str, Any],
) -> None:
    if len(list(outline.get("chapters") or [])) != 9 or len(list(packets.get("chapter_tasks") or [])) != 9:
        raise ValueError("writing outputs must contain 9 technical score chapters")
    for item in list(evidence_map.get("evidence_usage_items") or []):
        if item.get("allowed_usage") not in ALLOWED_USAGE:
            raise ValueError(f"invalid allowed_usage: {item.get('allowed_usage')}")
    for task in list(packets.get("chapter_tasks") or []):
        if task.get("task_type") not in TASK_TYPES:
            raise ValueError(f"invalid task_type: {task.get('task_type')}")
        if task.get("blocking_status") not in BLOCKING_STATUSES:
            raise ValueError(f"invalid blocking_status: {task.get('blocking_status')}")
        if any(item.get("allowed_usage") not in {"factual_claim", "supporting_reference"} for item in list(task.get("confirmed_facts") or [])):
            raise ValueError("non-confirmed evidence found in confirmed_facts")
    fatal_ids = {str(item.get("requirement_id") or "") for item in list(guardrails.get("fatal_risk_items") or [])}
    for item in list(pack.get("compliance_risks") or []):
        if item.get("polarity") == "negative_exception" and item.get("requirement_id") in fatal_ids:
            raise ValueError("negative exception included in fatal guardrails")


def unique_dicts_by_id(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = str(item.get(field) or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def stable_suffix(value: Any) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:14]


def task_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": item.get("task_id"),
        "chapter_id": item.get("chapter_id"),
        "chapter_title": item.get("chapter_title"),
        "task_type": item.get("task_type"),
        "blocking_status": item.get("blocking_status"),
    }

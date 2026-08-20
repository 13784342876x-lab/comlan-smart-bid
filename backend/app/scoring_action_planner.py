from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


REFERENCE_PATTERNS = [
    ("第三章评标办法前附表之四“技术评分标准”", "score_table_or_evaluation_standard"),
    ("第三章评标办法附表“评分标准”", "score_table_or_evaluation_standard"),
    ("第一章招标公告专用资格要求", "special_qualification"),
    ("招标公告附件", "tender_notice_attachment"),
    ("招标需求一览表", "procurement_package_table"),
    ("技术规范书规定的其他应提交的文件", "technical_spec_attachment_requirement"),
]


def build_scoring_action_plan(project: dict[str, Any], sections: list[dict[str, Any]]) -> dict[str, Any]:
    plans = [build_section_scoring_action_plan(project, section) for section in sections]
    return {
        "version": "v0",
        "lot_name": project.get("lot_name"),
        "project_name": project.get("project_name"),
        "sections": plans,
        "external_references": [ref for plan in plans for ref in list(plan.get("external_references") or [])],
        "summary": {
            "section_count": len(plans),
            "missing_reference_count": len(
                [ref for plan in plans for ref in list(plan.get("external_references") or []) if not ref.get("resolved")]
            ),
            "missing_action_count": len([item for plan in plans for item in list(plan.get("missing_actions") or [])]),
        },
    }


def build_scoring_action_plan_from_context(context: dict[str, Any]) -> dict[str, Any]:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    plans = [dict(chapter.get("action_plan") or {}) for chapter in list(context.get("chapters") or [])]
    return {
        "version": "v0",
        "lot_name": context.get("lot_name"),
        "project_name": project.get("project_name"),
        "generated_from": "technical_response_context.json",
        "sections": plans,
        "external_references": [ref for plan in plans for ref in list(plan.get("external_references") or [])],
        "summary": {
            "section_count": len(plans),
            "missing_reference_count": len(
                [ref for plan in plans for ref in list(plan.get("external_references") or []) if not ref.get("resolved")]
            ),
            "missing_action_count": len([item for plan in plans for item in list(plan.get("missing_actions") or [])]),
        },
    }


def build_section_scoring_action_plan(project: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    score_ref = section.get("score_ref") if isinstance(section.get("score_ref"), dict) else {}
    task_name = str(section.get("task_name") or score_ref.get("score_item") or "")
    section_title = str(section.get("section_title") or task_name)
    score_rule = str(score_ref.get("score_description") or "")
    score_range = normalize_score_range(score_ref.get("score_range"))
    material_findings = kb_material_findings(section)
    plan = {
        "section_title": section_title,
        "task_name": task_name,
        "score_item": score_ref.get("score_item") or task_name,
        "score_range": score_range,
        "scoring_rule_summary": summarize_scoring_rule(score_rule),
        "max_score_preparation": default_preparation_target(section_title),
        "scoring_focus": scoring_focus_for_task(task_name, score_rule),
        "qualification_constraints": qualification_constraints(project, section),
        "required_evidence_package": evidence_package_for_task(task_name),
        "kb_material_findings": material_findings,
        "manual_review_questions": manual_review_questions_for_task(task_name),
        "missing_actions": missing_actions_for_task(task_name, material_findings, project),
        "external_references": resolve_external_references(project, section),
        "delivery_requirements": delivery_requirements_for_task(task_name, section),
        "notes": notes_for_task(task_name, section),
    }
    if is_company_performance_task(task_name):
        plan["max_score_preparation"] = company_performance_preparation(score_rule, score_range)
        plan["qualified_performance_criteria"] = [
            "公司层面业绩，不能用人员项目经验替代",
            "近5年",
            "技术开发或技术服务",
            "证明材料信息完整",
            "合同等关键页能体现专用资格要求和技术评分标准要求的要素点",
        ]
    elif is_patent_task(task_name):
        plan["max_score_preparation"] = patent_preparation(score_rule, score_range)
    return plan


def resolve_external_references(project: dict[str, Any], section: dict[str, Any]) -> list[dict[str, Any]]:
    text = " ".join(
        [
            json.dumps(section.get("score_ref") or {}, ensure_ascii=False),
            json.dumps(section.get("response_requirement_refs") or [], ensure_ascii=False),
            json.dumps(section.get("material_basis") or {}, ensure_ascii=False),
        ]
    )
    results: list[dict[str, Any]] = []
    for reference_text, missing_type in REFERENCE_PATTERNS:
        if reference_text not in text:
            continue
        results.append(resolve_reference(project, section, reference_text, missing_type))
    return dedupe_references(results)


def resolve_reference(project: dict[str, Any], section: dict[str, Any], reference_text: str, missing_type: str) -> dict[str, Any]:
    if missing_type == "score_table_or_evaluation_standard":
        score_ref = section.get("score_ref") if isinstance(section.get("score_ref"), dict) else {}
        if list(project.get("score_items") or []):
            return {
                "reference_text": reference_text,
                "resolved": True,
                "resolved_source": "score_items",
                "source_file_id": project.get("score_file_id"),
                "source_file_name": score_ref.get("score_source_file") or "score_items",
                "matched_score_item": score_ref.get("score_item"),
                "confidence": "probable_reference",
                "note": "评分表已解析为 score_items；作为该引用的候选解析来源，仍需人工确认是否对应附表之四。",
            }
    if missing_type == "special_qualification":
        special = field_value(project, "special_qualification_requirement")
        if special:
            field = field_obj(project, "special_qualification_requirement")
            return {
                "reference_text": reference_text,
                "resolved": True,
                "resolved_source": "tender_project_metadata",
                "source_file_id": field.get("source_file_id"),
                "source_file_name": field.get("source_file_name"),
                "matched_text": special,
                "confidence": "high",
                "note": "已从当前包级元信息中定位专用资格要求。",
            }
    return {
        "reference_text": reference_text,
        "resolved": False,
        "missing_source_type": missing_type,
        "impact": "不能完整判断引用来源中的全部要素，相关材料清单只能按已知原文和已解析评分点生成。",
        "action": missing_reference_action(missing_type),
    }


def company_performance_preparation(score_rule: str, score_range: list[Any]) -> dict[str, Any]:
    base_count = 1
    base_score = first_number_after(score_rule, [r"1项合格业绩[^\d]{0,20}(\d+(?:\.\d+)?)分", r"1项[^\d]{0,20}(\d+(?:\.\d+)?)分"])
    increment = first_number_after(score_rule, [r"每多(?:提供)?一项[^\d]{0,20}(\d+(?:\.\d+)?)分", r"每增加1项[^\d]{0,20}(\d+(?:\.\d+)?)分"])
    bonus_cap = first_number_after(score_rule, [r"最高加(\d+(?:\.\d+)?)分", r"最高不超过(\d+(?:\.\d+)?)分"])
    max_score = score_range[1] if len(score_range) > 1 and isinstance(score_range[1], (int, float)) else None
    if base_score is None:
        base_score = score_range[0] if score_range and isinstance(score_range[0], (int, float)) and score_range[0] else 12
    if increment is None:
        increment = 2
    if bonus_cap is None and isinstance(max_score, (int, float)):
        bonus_cap = max(float(max_score) - float(base_score), 0)
    if bonus_cap is None:
        bonus_cap = 8
    extra_count = math.ceil(float(bonus_cap) / float(increment)) if increment else 0
    total_count = base_count + extra_count
    return {
        "target": f"达到该评分项上限需准备{total_count}项合格公司业绩",
        "calculation": f"1项={format_number(base_score)}分；最高加{format_number(bonus_cap)}分；每增加1项+{format_number(increment)}分；因此共需1+{extra_count}={total_count}项",
        "not_a_score_prediction": True,
    }


def patent_preparation(score_rule: str, score_range: list[Any]) -> dict[str, Any]:
    increment = first_number_after(score_rule, [r"每(?:提供)?1项[^\d]{0,20}(\d+(?:\.\d+)?)分", r"每项加(\d+(?:\.\d+)?)分"]) or 1
    cap = first_number_after(score_rule, [r"上限(?:不超过)?(\d+(?:\.\d+)?)分", r"最高(?:不超过)?(\d+(?:\.\d+)?)分"])
    if cap is None and len(score_range) > 1 and isinstance(score_range[1], (int, float)):
        cap = score_range[1]
    if cap is None:
        cap = 2
    count = max(1, math.ceil(float(cap) / float(increment)))
    return {
        "target": f"达到该项上限需准备{count}项采购相关专利",
        "calculation": f"每项加{format_number(increment)}分；上限{format_number(cap)}分；因此需准备{count}项采购相关发明专利或实用新型专利",
        "not_a_score_prediction": True,
    }


def evidence_package_for_task(task_name: str) -> list[dict[str, Any]]:
    if is_company_performance_task(task_name):
        return [
            {
                "material_type": "company_contract",
                "required_or_recommended": "required",
                "check_points": ["合同主体", "项目名称", "服务内容", "签署/履行时间", "技术开发或技术服务内容"],
            },
            {
                "material_type": "acceptance_report",
                "required_or_recommended": "recommended",
                "check_points": ["验收主体", "验收时间", "项目名称", "是否与合同对应"],
            },
            {
                "material_type": "winning_notice_or_customer_certificate",
                "required_or_recommended": "recommended",
                "check_points": ["项目名称", "客户/招标人", "服务内容", "时间"],
            },
            {
                "material_type": "identity_change_proof",
                "required_or_recommended": "conditional",
                "check_points": ["仅在合同或证明材料名称与投标人名称不一致时补充"],
            },
        ]
    if is_personnel_owner_task(task_name):
        return [
            {"material_type": "project_leader_resume", "required_or_recommended": "recommended", "check_points": ["专业", "工作年限", "工作经验", "职责分工"]},
            {"material_type": "personal_certificate", "required_or_recommended": "optional", "check_points": ["证书真实性", "有效期", "与本项目相关性"]},
        ]
    if is_personnel_task(task_name):
        return [
            {"material_type": "team_summary_form", "required_or_recommended": "recommended", "check_points": ["拟委任主要人员汇总表", "角色分工", "专业结构"]},
            {"material_type": "person_resume", "required_or_recommended": "recommended", "check_points": ["主要人员简历表", "项目经验", "证书"]},
        ]
    if is_patent_task(task_name):
        return [
            {"material_type": "patent_certificate", "required_or_recommended": "recommended", "check_points": ["专利名称", "专利号/申请号", "权利人", "状态"]},
            {"material_type": "patent_acceptance_notice", "required_or_recommended": "recommended", "check_points": ["受理号", "申请人", "申请日期"]},
            {"material_type": "official_patent_record", "required_or_recommended": "recommended", "check_points": ["官方查询记录", "状态", "权利人"]},
        ]
    if is_supplier_performance_task(task_name):
        return [
            {"material_type": "supplier_performance_evaluation", "required_or_recommended": "recommended", "check_points": ["最近一年度", "评价主体", "评价结果"]},
            {"material_type": "platform_screenshot", "required_or_recommended": "recommended", "check_points": ["平台绩效评价截图", "时间", "主体名称"]},
        ]
    if is_work_foundation_task(task_name):
        return [
            {"material_type": "product_proof", "required_or_recommended": "recommended", "check_points": ["产品化证明", "与本项目相关性"]},
            {"material_type": "research_achievement", "required_or_recommended": "recommended", "check_points": ["科技成果证明", "专题研究报告", "立项/结题材料"]},
        ]
    return [
        {"material_type": "technical_solution_inputs", "required_or_recommended": "recommended", "check_points": ["技术路线", "研究内容", "质量控制", "风险控制", "交付成果"]},
    ]


def scoring_focus_for_task(task_name: str, score_rule: str) -> list[str]:
    if is_personnel_owner_task(task_name):
        return ["专业", "工作年限", "工作经验"]
    if is_personnel_task(task_name):
        return ["团队专业结构", "工作年限", "工作经验"]
    if is_company_performance_task(task_name):
        return ["合格公司业绩数量", "专用资格匹配", "证明材料完整性"]
    if is_patent_task(task_name):
        return ["采购相关发明专利或实用新型专利", "每项加1分", "上限2分"]
    if is_supplier_performance_task(task_name):
        return ["最近一年度供应商绩效评价结果"]
    if is_work_foundation_task(task_name):
        return ["产品化", "科技成果", "专题研究"]
    if "进度" in task_name:
        return ["合理性", "可操作性", "服务期限匹配"]
    if "服务方案" in task_name:
        return ["先进性", "创新性", "可操作性", "技术性", "经济性", "风险"]
    if "总体评价" in task_name or "技术部分" in task_name:
        return ["规范性", "完整性", "逻辑性"]
    return extract_focus_from_rule(score_rule)


def manual_review_questions_for_task(task_name: str) -> list[str]:
    if is_company_performance_task(task_name):
        return [
            "该业绩是否发生在近5年？",
            "合同或证明材料是否能体现技术开发或技术服务？",
            "证明材料是否能体现评分标准要求的所有要素点？",
            "项目名称、服务对象、服务内容、时间是否清晰？",
            "人员项目经验是否被误用为公司业绩？",
        ]
    if is_personnel_owner_task(task_name):
        return [
            "项目负责人是否来自建议主责/协同部门？",
            "是否具备本项目相关经验？",
            "证书是否真实、有效？",
            "人员项目经验是否仅用于人员章节，未替代公司业绩？",
        ]
    if is_personnel_task(task_name):
        return [
            "团队专业结构是否覆盖研究、数据管理、安全和项目管理？",
            "主要人员简历表和汇总表是否完整？",
            "每人相关项目经验和证书是否可核验？",
        ]
    if is_patent_task(task_name):
        return [
            "专利是否与采购内容相关？",
            "是否至少准备2项采购相关专利用于冲击该项评分上限？",
            "专利证书、受理通知书或官方查询记录是否能体现权利人和状态？",
            "交付成果中的受理发明专利要求是否与既有专利评分材料分开处理？",
        ]
    if is_supplier_performance_task(task_name):
        return [
            "是否有最近一年度供应商绩效评价结果？",
            "评价证明是否来自平台、招标人或有效评价主体？",
            "是否误用简历、资质证书或业绩合同替代绩效评价？",
        ]
    return ["评分点对应材料是否齐全？", "原文要求和材料证据是否逐项可追溯？"]


def missing_actions_for_task(task_name: str, findings: dict[str, list[dict[str, Any]]], project: dict[str, Any]) -> list[dict[str, Any]]:
    confirmed = findings.get("confirmed_usable") or []
    actions: list[dict[str, Any]] = []
    if is_company_performance_task(task_name) and not confirmed:
        actions.append(
            {
                "target_material": "company_contract",
                "ask_to": "公司业绩/项目管理/相关业务部门",
                "question": "请补充近5年技术开发或技术服务类公司业绩合同关键页，优先补足5项。",
                "reason": "用于满足专用资格和业绩评分材料要求。",
            }
        )
        actions.append(
            {
                "target_material": "acceptance_report_or_customer_certificate",
                "ask_to": "项目管理/相关业务部门",
                "question": "请补充与合同对应的验收证明、验收报告、中标通知书或客户证明。",
                "reason": "用于补强业绩真实性、服务内容和履行时间。",
            }
        )
    elif is_patent_task(task_name) and not confirmed:
        actions.append(
            {
                "target_material": "patent_certificate_or_official_record",
                "ask_to": "知识产权/研发管理/相关业务部门",
                "question": "请补充采购相关发明专利或实用新型专利证书、受理通知书或官方查询记录，优先准备2项。",
                "reason": "用于支撑专利评分材料要求。",
            }
        )
    elif is_supplier_performance_task(task_name) and not confirmed:
        actions.append(
            {
                "target_material": "supplier_performance_evaluation",
                "ask_to": "供应商管理/项目管理/商务支持部门",
                "question": "请补充最近一年度供应商绩效评价截图、招标人评价结果或供应商绩效证明。",
                "reason": "用于支撑供应商绩效评价评分材料要求。",
            }
        )
    elif is_personnel_task(task_name) and not confirmed:
        actions.append(
            {
                "target_material": "person_resume",
                "ask_to": "主责部门/协同部门/项目管理",
                "question": "请补充候选项目负责人和团队成员简历、相关项目经验及证书材料。",
                "reason": "用于支撑人员专业、工作年限和工作经验评分。",
            }
        )
    return actions


def qualification_constraints(project: dict[str, Any], section: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_company_performance_task(str(section.get("task_name") or "")):
        return []
    field = field_obj(project, "special_qualification_requirement")
    value = str(field.get("value") or "").strip()
    if not value:
        return []
    return [
        {
            "type": "special_qualification",
            "text": value,
            "source_file_id": field.get("source_file_id"),
            "source_file_name": field.get("source_file_name"),
            "source_type": field.get("source_type"),
        }
    ]


def delivery_requirements_for_task(task_name: str, section: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_patent_task(task_name):
        return []
    refs = section.get("response_requirement_refs") if isinstance(section.get("response_requirement_refs"), list) else []
    results = []
    for ref in refs:
        excerpt = str(ref.get("original_excerpt") or "")
        if "受理发明专利" in excerpt:
            results.append(
                {
                    "type": "patent_acceptance_delivery",
                    "text": "受理发明专利1项",
                    "source_file_id": ref.get("source_file_id"),
                    "source_file_name": ref.get("source_file_name"),
                    "note": "交付成果要求，需与既有专利评分材料分开处理。",
                }
            )
    return results


def kb_material_findings(section: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups = section.get("evidence_groups") if isinstance(section.get("evidence_groups"), dict) else {}
    return {
        "confirmed_usable": evidence_summaries(groups.get("confirmed") or []),
        "needs_manual_review": evidence_summaries((groups.get("candidate") or []) + (groups.get("weak") or [])),
        "not_usable": evidence_summaries(groups.get("unusable") or []),
    }


def evidence_summaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items[:8]:
        result.append(
            {
                "file_id": item.get("file_id") or item.get("source_file_id"),
                "title": item.get("title") or item.get("file_name") or item.get("source"),
                "material_type": item.get("material_type"),
                "evidence_strength": item.get("evidence_strength"),
                "reason": item.get("applicability_reason") or item.get("usable_as"),
                "snippet": preview(item.get("snippet") or item.get("text") or "", 120),
            }
        )
    return result


def render_scoring_action_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        f"# 评分项行动分析 / 材料准备清单（{plan.get('lot_name') or '-'}）",
        "",
        "本文档用于把评分点、原文要求、包级约束和知识库材料转换为材料准备动作，不生成正式技术响应正文。",
        "",
    ]
    for section in list(plan.get("sections") or []):
        lines.extend(render_section_plan_markdown(section, heading_level=2))
    return "\n".join(lines).rstrip() + "\n"


def render_section_plan_markdown(plan: dict[str, Any], heading_level: int = 4) -> list[str]:
    heading = "#" * heading_level
    lines = [
        f"{heading} {plan.get('section_title') or plan.get('score_item') or '-'}",
        "",
        f"- 评分项：{plan.get('score_item') or '-'}",
        f"- 评分规则摘要：{plan.get('scoring_rule_summary') or '-'}",
    ]
    target = plan.get("max_score_preparation") or {}
    if target.get("target"):
        lines.append(f"- 评分项上限准备目标：{target.get('target')}。")
    if target.get("calculation"):
        lines.append(f"- 计算依据：{target.get('calculation')}。")
    focus = list(plan.get("scoring_focus") or [])
    if focus:
        lines.append(f"- 评分关注：{'；'.join(str(item) for item in focus)}。")
    criteria = list(plan.get("qualified_performance_criteria") or [])
    if criteria:
        lines.append(f"- 合格业绩口径：{'；'.join(str(item) for item in criteria)}。")
    constraints = list(plan.get("qualification_constraints") or [])
    for item in constraints:
        lines.append(f"- 当前包专用资格要求：{item.get('text')}")
    delivery = list(plan.get("delivery_requirements") or [])
    for item in delivery:
        lines.append(f"- 交付成果要求：{item.get('text')}（{item.get('note')}）")
    lines.extend(render_evidence_package_lines(plan))
    lines.extend(render_kb_findings_lines(plan))
    questions = list(plan.get("manual_review_questions") or [])
    if questions:
        lines.append("- 人工复核问题：")
        lines.extend([f"  - {item}" for item in questions])
    actions = list(plan.get("missing_actions") or [])
    if actions:
        lines.append("- 缺失补充动作：")
        for item in actions:
            lines.append(f"  - {item.get('target_material')}: {item.get('question')}（{item.get('ask_to')}；{item.get('reason')}）")
    refs = list(plan.get("external_references") or [])
    if refs:
        lines.append("- 外部引用解析：")
        for ref in refs:
            status = "已解析" if ref.get("resolved") else "未解析"
            target = ref.get("resolved_source") or ref.get("missing_source_type") or "-"
            lines.append(f"  - {ref.get('reference_text')}: {status}；来源/缺口：{target}；说明：{ref.get('note') or ref.get('action') or '-'}")
    notes = list(plan.get("notes") or [])
    for note in notes:
        lines.append(f"- 说明：{note}")
    lines.append("")
    return lines


def render_evidence_package_lines(plan: dict[str, Any]) -> list[str]:
    package = list(plan.get("required_evidence_package") or [])
    if not package:
        return []
    lines = ["- 材料准备清单："]
    for item in package:
        points = "、".join(str(value) for value in list(item.get("check_points") or []))
        lines.append(f"  - {item.get('material_type')}（{item.get('required_or_recommended')}）：{points}")
    return lines


def render_kb_findings_lines(plan: dict[str, Any]) -> list[str]:
    findings = plan.get("kb_material_findings") if isinstance(plan.get("kb_material_findings"), dict) else {}
    confirmed = list(findings.get("confirmed_usable") or [])
    review = list(findings.get("needs_manual_review") or [])
    unusable = list(findings.get("not_usable") or [])
    lines = []
    if confirmed:
        lines.append(f"- 当前知识库状态：已找到{len(confirmed)}项可用材料。")
    elif review:
        lines.append(f"- 当前知识库状态：未找到明确可用材料；有{len(review)}项候选/弱证据需人工复核。")
    else:
        lines.append("- 当前知识库状态：未找到明确可用材料。")
    if unusable:
        lines.append(f"- 不可替代材料提示：{len(unusable)}项材料不可直接用于本评分项。")
    return lines


def write_scoring_action_plan(plan: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    json_path = path / "scoring_action_plan.json"
    md_path = path / "scoring_action_plan.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_scoring_action_plan_markdown(plan), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def inspect_scoring_action_plan(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    base = Path(output_dir) / safe_path_name(lot_name) / "outline"
    plan_path = base / "scoring_action_plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"scoring_action_plan.json not found: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8") or "{}")
    sections = list(plan.get("sections") or [])
    return {
        "lot_name": plan.get("lot_name"),
        "version": plan.get("version"),
        "files": {
            "json": str(plan_path),
            "markdown": str(base / "scoring_action_plan.md"),
            "json_exists": plan_path.exists(),
            "markdown_exists": (base / "scoring_action_plan.md").exists(),
        },
        "summary": plan.get("summary") or {},
        "sections": [
            {
                "section_title": item.get("section_title"),
                "score_item": item.get("score_item"),
                "score_range": item.get("score_range"),
                "max_score_preparation": item.get("max_score_preparation"),
                "material_status": {
                    key: len(value or [])
                    for key, value in (item.get("kb_material_findings") or {}).items()
                },
                "missing_actions": item.get("missing_actions") or [],
                "manual_review_questions": item.get("manual_review_questions") or [],
                "external_references": item.get("external_references") or [],
            }
            for item in sections
        ],
    }


def summarize_scoring_rule(rule: str) -> str:
    text = " ".join(str(rule or "").split())
    return preview(text, 220) or "未定位到详细评分规则，按评分项名称和已知材料要求组织。"


def default_preparation_target(section_title: str) -> dict[str, Any]:
    return {"target": "", "calculation": "", "not_a_score_prediction": True}


def notes_for_task(task_name: str, section: dict[str, Any]) -> list[str]:
    if is_personnel_owner_task(task_name):
        return ["未定位到招标文件对项目负责人特定证书/社保/职称的硬性要求；可按评分点准备专业、年限、经验和个人证书材料。"]
    if is_supplier_performance_task(task_name):
        return ["简历、资质证书、业绩合同不能替代供应商绩效评价。"]
    if is_company_performance_task(task_name):
        return ["人员项目经验不得替代公司业绩；个人证书、企业资质证书、供应商绩效评价也不得替代公司业绩。"]
    return []


def missing_reference_action(missing_type: str) -> str:
    return {
        "score_table_or_evaluation_standard": "补充评标办法/技术评分标准原文，或确认当前评分表是否即为附表之四。",
        "special_qualification": "补充招标公告专用资格要求或当前包需求一览表。",
        "tender_notice_attachment": "补充或确认招标公告附件解析结果。",
        "procurement_package_table": "补充或确认当前包的招标需求一览表。",
        "technical_spec_attachment_requirement": "补充技术规范书或确认相关附件是否已入库。",
    }.get(missing_type, "补充对应引用来源文件。")


def normalize_score_range(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [value.get("min"), value.get("max")]
    if isinstance(value, (list, tuple)):
        return list(value[:2])
    if isinstance(value, (int, float)):
        return [0, value]
    return [None, None]


def field_obj(project: dict[str, Any], name: str) -> dict[str, Any]:
    metadata = project.get("tender_project_metadata") if isinstance(project.get("tender_project_metadata"), dict) else {}
    fields = metadata.get("fields") if isinstance(metadata.get("fields"), dict) else {}
    value = fields.get(name)
    return value if isinstance(value, dict) else {}


def field_value(project: dict[str, Any], name: str) -> str:
    return str(field_obj(project, name).get("value") or "").strip()


def first_number_after(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def extract_focus_from_rule(rule: str) -> list[str]:
    result = []
    for key in ["先进性", "创新性", "可操作性", "技术性", "经济性", "风险", "合理性", "完整性", "逻辑性"]:
        if key in rule:
            result.append(key)
    return result or ["评分点对应内容完整性"]


def is_personnel_owner_task(task_name: str) -> bool:
    return "负责人" in task_name or "项目经理" in task_name


def is_personnel_task(task_name: str) -> bool:
    return any(key in task_name for key in ["项目负责人", "项目经理", "项目团队", "人员结构", "团队人员", "团队配置"])


def is_company_performance_task(task_name: str) -> bool:
    return any(key in task_name for key in ["业绩", "项目经验"]) and "人员" not in task_name


def is_patent_task(task_name: str) -> bool:
    return any(key in task_name for key in ["专利", "知识产权", "创新成果"])


def is_supplier_performance_task(task_name: str) -> bool:
    return "绩效评价" in task_name or "绩效" in task_name


def is_work_foundation_task(task_name: str) -> bool:
    return "工作基础" in task_name


def dedupe_references(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = str(item.get("reference_text") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def preview(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def safe_path_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", str(value or "unknown")).strip("_") or "unknown"

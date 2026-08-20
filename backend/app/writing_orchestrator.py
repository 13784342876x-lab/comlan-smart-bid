from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.file_id_utils import existing_file_ids
from app.material_matcher import TENDER_PROJECTS_PATH, find_project_by_lot_name, load_tender_projects, save_tender_projects
from app.tender_source_guard import is_tender_source_file


DECISION_TERMS = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go", "bid / no-bid decision"]


def build_writing_plan(project: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    material_analysis = project.get("material_analysis")
    if not isinstance(material_analysis, dict):
        raise ValueError("material_analysis not found; run match-materials --save first")

    allowed_ids = existing_file_ids(metadata or {}) if metadata is not None else set()
    files_by_id = {
        str(item.get("id") or item.get("file_id") or ""): item
        for item in list((metadata or {}).get("files") or [])
        if isinstance(item, dict)
    }
    material_by_task_id = {}
    for item in list(material_analysis.get("task_material_map") or []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        material_by_task_id[task_id] = filter_material_item_to_existing_files(item, allowed_ids, files_by_id)
    draft_plan: list[dict[str, Any]] = []
    draft_sections: list[dict[str, Any]] = []

    for task in list(project.get("generation_tasks") or []):
        task_id = str(task.get("task_id") or "")
        material_item = material_by_task_id.get(task_id, {})
        plan_item = build_plan_item(task, material_item)
        draft_plan.append(plan_item)
        draft_sections.append(build_draft_section(plan_item))

    result = {
        "lot_name": project.get("lot_name"),
        "summary": build_summary(draft_sections),
        "draft_plan": draft_plan,
        "draft_sections": draft_sections,
    }
    assert_no_decision_terms(result)
    return result


def filter_material_item_to_existing_files(item: dict[str, Any], allowed_ids: set[str], files_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
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
    return result


def save_writing_plan_to_project(
    lot_name: str,
    writing_plan: dict[str, Any],
    path: Path = TENDER_PROJECTS_PATH,
) -> dict[str, Any]:
    data = load_tender_projects(path)
    project = find_project_by_lot_name(data, lot_name)
    project["writing_plan"] = {
        "summary": writing_plan.get("summary", {}),
        "draft_plan": writing_plan.get("draft_plan", []),
        "draft_sections": writing_plan.get("draft_sections", []),
    }
    save_tender_projects(data, path)
    return project["writing_plan"]


def build_plan_item(task: dict[str, Any], material_item: dict[str, Any]) -> dict[str, Any]:
    match_status = str(material_item.get("match_status") or "missing")
    task_name = str(material_item.get("task_name") or task_name_from_task(task))
    task_type = normalize_task_type(task.get("task_type"))
    section_type = section_type_for_status(match_status)
    writer_agent = writer_agent_for_task(task_type, section_type)
    evidence_refs = evidence_refs_from_materials(list(material_item.get("matched_materials") or []))
    missing_materials = list(material_item.get("missing_materials") or [])
    outline = outline_for_task(task_name, task_type, section_type, evidence_refs, missing_materials)
    notes = notes_for_task(match_status, task_type, material_item)

    return {
        "task_id": str(task.get("task_id") or material_item.get("task_id") or ""),
        "task_name": task_name,
        "task_type": task_type,
        "match_status": match_status,
        "section_type": section_type,
        "writer_agent": writer_agent,
        "writing_goal": writing_goal_for_task(task_name, task_type, section_type),
        "evidence_refs": evidence_refs,
        "missing_materials": missing_materials,
        "outline": outline,
        "notes": notes,
    }


def build_draft_section(plan_item: dict[str, Any]) -> dict[str, Any]:
    section_type = str(plan_item.get("section_type") or "")
    if section_type == "missing_blocked":
        status = "blocked_by_missing_material"
    elif section_type == "not_applicable":
        status = "not_applicable"
    else:
        status = "draft_skeleton"

    return {
        "task_id": plan_item.get("task_id"),
        "task_name": plan_item.get("task_name"),
        "section_title": section_title(plan_item),
        "section_type": section_type,
        "writer_agent": plan_item.get("writer_agent"),
        "draft_text": draft_text_for_plan(plan_item),
        "evidence_refs": plan_item.get("evidence_refs") or [],
        "missing_materials": plan_item.get("missing_materials") or [],
        "status": status,
    }


def evidence_refs_from_materials(materials: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for material in materials:
        key = (
            str(material.get("file_id") or ""),
            str(material.get("material_type") or ""),
            str(material.get("snippet") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "file_id": material.get("file_id"),
                "source": material.get("source"),
                "title": material.get("title"),
                "snippet": preview(material.get("snippet"), 180),
                "material_type": material.get("material_type"),
                "confidence": safe_float(material.get("confidence")),
            }
        )
        if len(refs) >= limit:
            break
    return refs


def section_type_for_status(match_status: str) -> str:
    if match_status in {"matched", "partial"}:
        return "evidence_backed"
    if match_status == "writing_required":
        return "writing_required"
    if match_status == "not_applicable":
        return "not_applicable"
    return "missing_blocked"


def writer_agent_for_task(task_type: str, section_type: str) -> str:
    if section_type == "not_applicable":
        return "none"
    if section_type == "missing_blocked":
        return "none"
    if task_type == "business":
        return "business_writer"
    if task_type == "technical":
        return "technical_writer"
    if task_type == "price":
        return "price_writer" if section_type != "not_applicable" else "none"
    return "none"


def outline_for_task(
    task_name: str,
    task_type: str,
    section_type: str,
    evidence_refs: list[dict[str, Any]],
    missing_materials: list[dict[str, Any]],
) -> list[str]:
    if section_type == "missing_blocked":
        return ["列明缺失材料", "说明当前不可展开的事实边界", "补充材料后再形成响应内容"]
    if section_type == "not_applicable":
        return ["记录报价类任务范围", "保留后续价格文件核对入口", "V0 不做报价测算"]

    technical = technical_outline(task_name)
    if technical:
        return technical
    if task_type == "business":
        return ["评分要求理解", "已匹配材料摘要", "证据引用与页码占位", "响应表述边界", "后续补充事项"]
    if task_type == "price":
        return ["报价文件完整性核对", "一致性检查占位", "后续价格策略输入"]
    if evidence_refs:
        return ["任务要求理解", "证据摘要", "响应骨架", "待复核事项"]
    return ["写作目标", "章节结构", "待补充输入", "复核要点"]


def technical_outline(task_name: str) -> list[str]:
    if contains_any(task_name, ["项目负责人情况", "项目负责人"]):
        return ["人员基本情况", "工作年限与专业背景", "相关项目经验", "相关个人证书", "与本项目的匹配性"]
    if contains_any(task_name, ["项目团队人员结构", "团队人员", "人员结构"]):
        return ["团队组成", "专业分工", "技能覆盖", "项目经验", "交付保障"]
    if contains_any(task_name, ["业绩情况", "项目业绩"]):
        return ["业绩材料范围", "项目背景与规模", "合同或验收证据", "与采购内容的相关性", "页码索引占位"]
    if contains_any(task_name, ["服务方案"]):
        return ["服务目标理解", "总体实施思路", "关键工作内容", "质量控制", "风险控制", "沟通机制"]
    if contains_any(task_name, ["工作进度及保证措施", "工作进度"]):
        return ["进度阶段划分", "里程碑安排", "资源投入", "进度保障措施", "风险应对"]
    if contains_any(task_name, ["工作基础"]):
        return ["既有产品或成果基础", "专题研究基础", "相关案例或经验", "可复用能力", "与本项目的适配性"]
    if contains_any(task_name, ["专利数量", "专利"]):
        return ["专利或知识产权清单", "权属与有效性", "与采购内容的相关性", "证明材料引用", "数量统计占位"]
    if contains_any(task_name, ["绩效评价"]):
        return ["绩效评价材料范围", "评价主体与年度", "评价结果引用", "证明材料页码占位", "待复核事项"]
    return []


def writing_goal_for_task(task_name: str, task_type: str, section_type: str) -> str:
    if section_type == "evidence_backed":
        return f"基于已匹配材料形成“{task_name}”的响应骨架，并保留证据引用。"
    if section_type == "writing_required":
        return f"为“{task_name}”形成后续章节写作提纲和草稿骨架。"
    if section_type == "missing_blocked":
        return f"记录“{task_name}”的材料缺口，等待补充材料后再展开正文。"
    if task_type == "price":
        return f"记录“{task_name}”为报价类任务，V0 不进行报价测算。"
    return f"记录“{task_name}”的 V0 处理范围。"


def draft_text_for_plan(plan_item: dict[str, Any]) -> str:
    task_name = str(plan_item.get("task_name") or "")
    section_type = str(plan_item.get("section_type") or "")
    outline = list(plan_item.get("outline") or [])
    evidence_refs = list(plan_item.get("evidence_refs") or [])
    missing_materials = list(plan_item.get("missing_materials") or [])

    if section_type == "evidence_backed":
        evidence_lines = [
            f"- [{ref.get('material_type')}] {ref.get('title') or ref.get('file_id')}：{preview(ref.get('snippet'), 90)}"
            for ref in evidence_refs
        ]
        return "\n".join(
            [
                f"【{task_name}】草稿骨架",
                "根据已匹配材料，本章节可围绕以下事实展开；后续写作时需逐条核对原文、页码和适用边界。",
                "",
                "提纲：",
                *[f"- {item}" for item in outline],
                "",
                "证据引用：",
                *(evidence_lines or ["- 当前无可引用证据。"]),
                "",
                "写作边界：仅使用上述材料支撑表述，不将个人证书写作企业资质，不将人员项目经历等同于公司业绩合同。",
            ]
        )

    if section_type == "writing_required":
        return "\n".join(
            [
                f"【{task_name}】写作骨架",
                "该任务主要需要后续章节撰写，V0 先形成可检查提纲，不作为硬材料缺口。",
                "",
                "提纲：",
                *[f"- {item}" for item in outline],
                "",
                "后续输入：招标要求、项目实施思路、可引用材料页码、内部确认后的表达口径。",
            ]
        )

    if section_type == "missing_blocked":
        missing_lines = [
            f"- {item.get('material_type')}: {item.get('reason') or item.get('suggested_action') or '需补充材料'}"
            for item in missing_materials
        ]
        return "\n".join(
            [
                f"【{task_name}】材料缺口提示",
                "当前缺少支撑材料，V0 不生成事实性正文，也不编造企业资质、个人证书、项目合同、专利、绩效评价、信用报告或体系认证。",
                "",
                "缺口清单：",
                *(missing_lines or ["- 未提供明确缺口项，请复核 material_analysis。"]),
                "",
                "建议动作：补充或复核材料后重新运行材料匹配，再进入章节写作。",
            ]
        )

    return "\n".join(
        [
            f"【{task_name}】V0 范围说明",
            "报价测算与价格策略不在 writing_orchestrator V0 范围内；本节仅保留后续价格文件核对入口，不进行报价测算，不输出价格测算结论。",
            "",
            "提纲：",
            *[f"- {item}" for item in outline],
        ]
    )


def notes_for_task(match_status: str, task_type: str, material_item: dict[str, Any]) -> list[str]:
    notes = list(material_item.get("notes") or [])
    if match_status == "partial":
        notes.append("材料部分匹配，后续写作需显式保留缺口。")
    if match_status == "missing":
        notes.append("材料缺失，V0 不编造正文。")
    if match_status == "not_applicable" or task_type == "price":
        notes.append("报价测算与价格策略不在 writing_orchestrator V0 范围内。")
    return dedupe_strings(notes)


def build_summary(draft_sections: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total_tasks": len(draft_sections),
        "draft_sections": len(draft_sections),
        "evidence_backed_sections": 0,
        "writing_required_sections": 0,
        "missing_blocked_sections": 0,
        "not_applicable_sections": 0,
    }
    for section in draft_sections:
        key = f"{section.get('section_type')}_sections"
        if key in summary:
            summary[key] += 1
    return summary


def section_title(plan_item: dict[str, Any]) -> str:
    return str(plan_item.get("task_name") or plan_item.get("task_id") or "未命名章节")


def task_name_from_task(task: dict[str, Any]) -> str:
    related = list(task.get("related_score_item_ids") or [])
    return str(related[0] if related else task.get("target_section") or task.get("task_id") or "")


def normalize_task_type(value: Any) -> str:
    text = str(value or "unknown").lower()
    return text if text in {"business", "technical", "price"} else "unknown"


def contains_any(text: Any, terms: list[str]) -> bool:
    value = str(text or "").lower()
    return any(str(term).lower() in value for term in terms)


def safe_float(value: Any) -> float:
    try:
        return round(float(value), 2)
    except Exception:
        return 0.0


def preview(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def assert_no_decision_terms(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False).lower()
    forbidden = [term.lower() for term in DECISION_TERMS]
    if any(term in text for term in forbidden):
        raise ValueError("writing plan contains forbidden bid decision language")

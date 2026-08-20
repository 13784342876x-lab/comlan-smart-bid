from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.material_matcher import TENDER_PROJECTS_PATH, find_project_by_lot_name, load_tender_projects, save_tender_projects


OUTPUT_KINDS = [
    "technical_draft",
    "general_response_draft",
    "business_handoff",
    "price_handoff",
    "missing_materials",
    "evidence_index",
]


def export_markdown_drafts(
    project: dict[str, Any],
    output_dir: str | Path = "outputs",
    source: str = "auto",
) -> dict[str, Any]:
    resolved_source, sections, plan_by_task_id = resolve_draft_source(project, source)
    lot_name = str(project.get("lot_name") or "unknown_lot")
    draft_dir = Path(output_dir) / safe_path_name(lot_name) / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)

    contents = build_markdown_contents(project, sections, plan_by_task_id, resolved_source)
    files: list[dict[str, Any]] = []
    for kind in OUTPUT_KINDS:
        path = draft_dir / f"{kind}.md"
        text = contents[kind]
        ensure_no_decision_terms(text)
        path.write_text(text, encoding="utf-8")
        files.append({"kind": kind, "path": str(path), "source": resolved_source})

    result = {
        "lot_name": project.get("lot_name"),
        "source": resolved_source,
        "output_dir": str(draft_dir),
        "files": files,
        "summary": {
            "total_files": len(files),
            "section_count": len(sections),
            "technical_sections": len([item for item in sections if item.get("task_type") == "technical"]),
            "business_sections": len([item for item in sections if item.get("task_type") == "business"]),
            "price_sections": len([item for item in sections if item.get("task_type") == "price" or item.get("section_type") == "not_applicable"]),
            "missing_sections": len([item for item in sections if item.get("section_type") == "missing_blocked" or item.get("status") == "blocked_by_missing_material"]),
            "evidence_refs": len(collect_evidence_refs(sections)),
        },
    }
    ensure_no_decision_terms(result)
    return result


def save_markdown_drafts_index(
    lot_name: str,
    markdown_drafts: dict[str, Any],
    path: Path = TENDER_PROJECTS_PATH,
) -> dict[str, Any]:
    data = load_tender_projects(path)
    project = find_project_by_lot_name(data, lot_name)
    project["markdown_drafts"] = {
        "output_dir": markdown_drafts.get("output_dir"),
        "files": markdown_drafts.get("files", []),
        "source": markdown_drafts.get("source"),
        "summary": markdown_drafts.get("summary", {}),
    }
    save_tender_projects(data, path)
    return project["markdown_drafts"]


def resolve_draft_source(project: dict[str, Any], source: str) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    value = str(source or "auto")
    if value not in {"auto", "agent_drafts", "writing_plan"}:
        raise ValueError("source must be one of: auto, agent_drafts, writing_plan")

    if value in {"auto", "agent_drafts"} and isinstance(project.get("agent_drafts"), dict):
        sections = list(project["agent_drafts"].get("agent_draft_sections") or [])
        if sections or value == "agent_drafts":
            return "agent_drafts", [normalize_agent_section(item) for item in sections], {}

    if value in {"auto", "writing_plan"} and isinstance(project.get("writing_plan"), dict):
        writing_plan = project["writing_plan"]
        plan_by_task_id = {
            str(item.get("task_id") or ""): item
            for item in list(writing_plan.get("draft_plan") or [])
            if isinstance(item, dict)
        }
        sections = [normalize_writing_section(item, plan_by_task_id) for item in list(writing_plan.get("draft_sections") or [])]
        return "writing_plan", sections, plan_by_task_id

    raise ValueError("no usable agent_drafts or writing_plan found for markdown export")


def build_markdown_contents(
    project: dict[str, Any],
    sections: list[dict[str, Any]],
    plan_by_task_id: dict[str, dict[str, Any]],
    source: str,
) -> dict[str, str]:
    technical = [item for item in sections if item.get("task_type") == "technical" and item.get("section_type") != "missing_blocked"]
    general = [
        item
        for item in sections
        if item.get("task_type") not in {"technical", "business", "price"} and item.get("section_type") != "missing_blocked"
    ]
    business = [item for item in sections if item.get("task_type") == "business"]
    price = [item for item in sections if item.get("task_type") == "price" or item.get("section_type") == "not_applicable"]
    missing = [item for item in sections if item.get("section_type") == "missing_blocked" or item.get("status") == "blocked_by_missing_material"]
    evidence_refs = collect_evidence_refs(sections)

    return {
        "technical_draft": build_technical_draft(project, technical, plan_by_task_id, source),
        "general_response_draft": build_general_response_draft(project, general, plan_by_task_id, source),
        "business_handoff": build_business_handoff(project, business, plan_by_task_id, source),
        "price_handoff": build_price_handoff(project, price, source),
        "missing_materials": build_missing_materials_doc(project, missing, source),
        "evidence_index": build_evidence_index(project, evidence_refs, source),
    }


def build_technical_draft(
    project: dict[str, Any],
    sections: list[dict[str, Any]],
    plan_by_task_id: dict[str, dict[str, Any]],
    source: str,
) -> str:
    lines = doc_header(project, "技术初稿（Markdown V0）", source)
    lines.extend(
        [
            "## 生成说明",
            "本文档为可编辑技术初稿骨架，优先使用 mock Agent 草稿；需人工复核事实、页码、适用范围和最终表述。",
            "",
        ]
    )
    if not sections:
        lines.extend(["## 技术评分项章节", "当前没有可导出的技术类章节。", ""])
        return join_lines(lines)

    lines.append("## 技术评分项章节")
    for section in sections:
        plan = plan_by_task_id.get(str(section.get("task_id") or ""), {})
        lines.extend(section_block(section, plan, include_text=True))
    return join_lines(lines)


def build_general_response_draft(
    project: dict[str, Any],
    sections: list[dict[str, Any]],
    plan_by_task_id: dict[str, dict[str, Any]],
    source: str,
) -> str:
    lines = doc_header(project, "通用响应初稿（Markdown V0）", source)
    lines.extend(["## 生成说明", "本文件收纳非技术、非商务、非报价且可撰写的通用响应骨架。", ""])
    if not sections:
        lines.extend(["当前没有通用响应章节。", ""])
        return join_lines(lines)
    for section in sections:
        lines.extend(section_block(section, plan_by_task_id.get(str(section.get("task_id") or ""), {}), include_text=True))
    return join_lines(lines)


def build_business_handoff(
    project: dict[str, Any],
    sections: list[dict[str, Any]],
    plan_by_task_id: dict[str, dict[str, Any]],
    source: str,
) -> str:
    lines = doc_header(project, "商务部门交接清单（Markdown V0）", source)
    lines.extend(
        [
            "## 交接说明",
            "商务部分主要由商务部门撰写。本文档仅整理评分项、材料状态、证据和缺口，不生成商务承诺正文，不替商务部门做判断。",
            "",
            "## 商务评分项清单",
        ]
    )
    if not sections:
        lines.extend(["当前没有商务类评分项。", ""])
        return join_lines(lines)
    for section in sections:
        plan = plan_by_task_id.get(str(section.get("task_id") or ""), {})
        lines.extend(
            [
                f"### {section.get('task_name') or section.get('task_id')}",
                f"- 当前状态：{section.get('status') or '-'}",
                f"- 章节类型：{section.get('section_type') or '-'}",
                f"- 写作目标：{plan.get('writing_goal') or '-'}",
                "- 已匹配材料：",
                *evidence_lines(section.get("evidence_refs") or []),
                "- 缺失材料：",
                *missing_lines(section.get("missing_materials") or []),
                "- 需商务部门确认事项：材料适用性、承诺口径、签章要求、页码索引。",
                "",
            ]
        )
    return join_lines(lines)


def build_price_handoff(project: dict[str, Any], sections: list[dict[str, Any]], source: str) -> str:
    lines = doc_header(project, "财务报价交接清单（Markdown V0）", source)
    lines.extend(
        [
            "## 交接说明",
            "报价部分主要由财务部门撰写。V0 不做报价测算，不形成报价内容或价格结论。",
            "",
            "## 报价相关评分项",
        ]
    )
    if not sections:
        lines.extend(["当前没有报价类或不适用于 V0 的报价相关任务。", ""])
        return join_lines(lines)
    for section in sections:
        lines.extend(
            [
                f"### {section.get('task_name') or section.get('task_id')}",
                f"- 当前状态：{section.get('status') or '-'}",
                f"- 章节类型：{section.get('section_type') or '-'}",
                "- 财务部门需处理事项：核对报价文件完整性、金额一致性、税率、大小写金额和授权签章要求。",
                "- V0 范围：不做报价测算，不形成报价内容或价格结论。",
                "",
            ]
        )
    return join_lines(lines)


def build_missing_materials_doc(project: dict[str, Any], sections: list[dict[str, Any]], source: str) -> str:
    lines = doc_header(project, "缺失材料清单（Markdown V0）", source)
    lines.extend(["## 说明", "本文件仅列出缺口提示，不编造企业资质、个人证书、项目合同、专利、信用报告或体系认证。", ""])
    if not sections:
        lines.extend(["当前没有 missing_blocked 任务。", ""])
        return join_lines(lines)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        grouped.setdefault(str(section.get("task_type") or "unknown"), []).append(section)
    for task_type, items in grouped.items():
        lines.extend([f"## {task_type}", ""])
        for section in items:
            missing = list(section.get("missing_materials") or [])
            lines.extend([f"### {section.get('task_name') or section.get('task_id')}", "- 分组：missing_blocked"])
            if missing:
                for item in missing:
                    lines.extend(
                        [
                            f"- missing_material_type: {item.get('material_type') or '-'}",
                            f"  - reason: {item.get('reason') or '-'}",
                            f"  - suggested_action: {item.get('suggested_action') or '-'}",
                        ]
                    )
            else:
                lines.append("- 未提供明确缺口项，请复核上游材料分析。")
            lines.append("")
    return join_lines(lines)


def build_evidence_index(project: dict[str, Any], evidence_refs: list[dict[str, Any]], source: str) -> str:
    lines = doc_header(project, "证据索引（Markdown V0）", source)
    lines.extend(["## 证据汇总", ""])
    if not evidence_refs:
        lines.extend(["当前没有 evidence_refs。", ""])
        return join_lines(lines)
    lines.extend(["| file_id | title | source | material_type | confidence | snippet |", "|---|---|---|---|---:|---|"])
    for ref in evidence_refs:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_cell(ref.get("file_id")),
                    md_cell(ref.get("title")),
                    md_cell(ref.get("source")),
                    md_cell(ref.get("material_type")),
                    md_cell(ref.get("confidence")),
                    md_cell(ref.get("snippet")),
                ]
            )
            + " |"
        )
    lines.append("")
    return join_lines(lines)


def section_block(section: dict[str, Any], plan: dict[str, Any], include_text: bool) -> list[str]:
    lines = [
        f"### {section.get('task_name') or section.get('task_id')}",
        f"- 评分项名称：{section.get('task_name') or '-'}",
        f"- 分值范围：{format_score_range(plan.get('score_range'))}",
        f"- 写作目标：{plan.get('writing_goal') or '-'}",
        "",
    ]
    if include_text:
        lines.extend(["#### 初稿正文", str(section.get("draft_text") or "当前无初稿正文。"), ""])
    lines.extend(
        [
            "#### 证据引用",
            *evidence_lines(section.get("evidence_refs") or []),
            "",
            "#### 待人工确认事项",
            "- 核对证据原文、页码和适用边界。",
            "- 核对人员证书、企业资质、项目案例等材料类型是否准确。",
            "- 根据最终标书格式调整标题层级和表述。",
            "",
        ]
    )
    return lines


def doc_header(project: dict[str, Any], title: str, source: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"- 标段号：{project.get('lot_name') or '-'}",
        f"- 项目名称：{project.get('project_name') or '-'}",
        f"- 数据来源：{source}",
        "",
    ]


def evidence_lines(refs: list[dict[str, Any]]) -> list[str]:
    if not refs:
        return ["  - 无"]
    return [
        f"  - `{ref.get('file_id') or '-'}` | {ref.get('title') or '-'} | {ref.get('material_type') or '-'} | {preview(ref.get('snippet'), 100)}"
        for ref in refs
    ]


def missing_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["  - 无"]
    return [
        f"  - {item.get('material_type') or '-'}：{item.get('reason') or item.get('suggested_action') or '-'}"
        for item in items
    ]


def collect_evidence_refs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for section in sections:
        for ref in list(section.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            key = (str(ref.get("file_id") or ""), str(ref.get("material_type") or ""), str(ref.get("snippet") or ""))
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def normalize_agent_section(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def normalize_writing_section(item: dict[str, Any], plan_by_task_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    section = dict(item)
    plan = plan_by_task_id.get(str(section.get("task_id") or ""), {})
    section.setdefault("task_type", plan.get("task_type") or "unknown")
    section.setdefault("status", section.get("status") or "draft_skeleton")
    return section


def format_score_range(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value.get('min')} - {value.get('max')}"
    return "-"


def md_cell(value: Any) -> str:
    text = preview(value, 160)
    return text.replace("|", "\\|").replace("\n", " ")


def preview(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
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
            raise ValueError("markdown draft contains forbidden bid decision language")

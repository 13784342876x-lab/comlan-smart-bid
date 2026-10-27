from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent_runner import run_agent
from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.material_matcher import TENDER_PROJECTS_PATH, find_project_by_lot_name, load_tender_projects, save_tender_projects


CALLABLE_WRITERS = {"business_writer", "technical_writer"}


def generate_agent_drafts(
    project: dict[str, Any],
    provider: str | None = None,
    agents_dir: str = "agents",
    model_profile: str | None = None,
) -> dict[str, Any]:
    writing_plan = project.get("writing_plan")
    if not isinstance(writing_plan, dict):
        raise ValueError("writing_plan not found; run plan-writing --save first")

    draft_plan_by_task = {
        str(item.get("task_id") or ""): item
        for item in list(writing_plan.get("draft_plan") or [])
        if isinstance(item, dict)
    }
    sections: list[dict[str, Any]] = []
    for draft_section in list(writing_plan.get("draft_sections") or []):
        if not isinstance(draft_section, dict):
            continue
        plan_item = draft_plan_by_task.get(str(draft_section.get("task_id") or ""), {})
        sections.append(build_agent_draft_section(draft_section, plan_item, provider, agents_dir, model_profile))

    result = {
        "lot_name": project.get("lot_name"),
        "summary": build_summary(sections),
        "agent_draft_sections": sections,
    }
    return result


def save_agent_drafts_to_project(
    lot_name: str,
    agent_drafts: dict[str, Any],
    path: Path = TENDER_PROJECTS_PATH,
) -> dict[str, Any]:
    data = load_tender_projects(path)
    project = find_project_by_lot_name(data, lot_name)
    project["agent_drafts"] = {
        "summary": agent_drafts.get("summary", {}),
        "agent_draft_sections": agent_drafts.get("agent_draft_sections", []),
    }
    save_tender_projects(data, path)
    return project["agent_drafts"]


def build_agent_draft_section(
    draft_section: dict[str, Any],
    plan_item: dict[str, Any],
    provider: str | None,
    agents_dir: str,
    model_profile: str | None = None,
) -> dict[str, Any]:
    section_type = str(draft_section.get("section_type") or plan_item.get("section_type") or "")
    writer_agent = str(draft_section.get("writer_agent") or plan_item.get("writer_agent") or "none")
    base = {
        "task_id": draft_section.get("task_id"),
        "task_name": draft_section.get("task_name"),
        "task_type": plan_item.get("task_type") or "unknown",
        "section_type": section_type,
        "writer_agent": writer_agent,
        "status": "failed",
        "draft_text": "",
        "evidence_refs": list(draft_section.get("evidence_refs") or []),
        "missing_materials": list(draft_section.get("missing_materials") or []),
        "agent_result": {},
        "errors": [],
    }

    if section_type == "missing_blocked":
        return {
            **base,
            "status": "blocked_by_missing_material",
            "draft_text": str(draft_section.get("draft_text") or ""),
        }
    if section_type == "not_applicable":
        return {
            **base,
            "status": "not_applicable",
            "draft_text": str(draft_section.get("draft_text") or ""),
        }
    if writer_agent not in CALLABLE_WRITERS:
        return {
            **base,
            "status": "not_applicable",
            "draft_text": str(draft_section.get("draft_text") or ""),
            "errors": [] if writer_agent == "none" else [f"writer_agent not callable in V0: {writer_agent}"],
        }

    input_data = build_agent_input(draft_section, plan_item)
    options = {"model_profile": model_profile} if model_profile else {}
    run_result = run_agent(writer_agent, input_data, provider=provider, agents_dir=agents_dir, **options)
    base["run_result"] = run_result
    base["is_mock"] = run_result.get("is_mock", provider == "mock")
    returned = run_result.get("result")
    agent_result = returned if isinstance(returned, dict) else {}
    if run_result.get("status") != "success":
        return {
            **base,
            "status": "failed",
            "draft_text": str(agent_result.get("content") or run_result.get("raw_output") or draft_section.get("draft_text") or ""),
            "agent_result": run_result.get("result") or {},
            "errors": list(run_result.get("errors") or []),
        }

    return {
        **base,
        "status": "generated_mock" if base["is_mock"] else "generated",
        "draft_text": str(agent_result.get("content") or draft_section.get("draft_text") or ""),
        "agent_result": agent_result,
        "errors": [],
    }


def build_agent_input(draft_section: dict[str, Any], plan_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": draft_section.get("task_id"),
        "task_name": draft_section.get("task_name"),
        "task_type": plan_item.get("task_type") or "unknown",
        "section_type": draft_section.get("section_type"),
        "writing_goal": plan_item.get("writing_goal"),
        "outline": list(plan_item.get("outline") or []),
        "evidence_refs": list(draft_section.get("evidence_refs") or []),
        "missing_materials": list(draft_section.get("missing_materials") or []),
        "draft_text": draft_section.get("draft_text") or "",
    }


def build_summary(sections: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total_sections": len(sections),
        "agent_generated_sections": 0,
        "blocked_sections": 0,
        "not_applicable_sections": 0,
        "failed_sections": 0,
    }
    for section in sections:
        status = str(section.get("status") or "")
        if status in {"generated_mock", "generated"}:
            summary["agent_generated_sections"] += 1
        elif status == "blocked_by_missing_material":
            summary["blocked_sections"] += 1
        elif status == "not_applicable":
            summary["not_applicable_sections"] += 1
        elif status == "failed":
            summary["failed_sections"] += 1
    return summary


def ensure_no_decision_terms(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False).lower()
    for term in FORBIDDEN_DECISION_TERMS:
        if term.lower() in text:
            raise ValueError("agent drafts contain forbidden bid decision language")

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.chapter_draft_schema import load_chapter_draft_schema
from app.llm_client import FORBIDDEN_DECISION_TERMS, LLMClient, ModelCallResult, get_llm_client
from app.llm_errors import ModelConfigurationError, ModelError
from app.tender_requirement_matrix import load_json, safe_path_name, unique


SCHEMA_VERSION = "1.0"

DRAFT_MODES = {"ai_generated", "deterministic_preview", "dry_run", "failed"}
GENERATION_STATUSES = {"generated", "generated_with_placeholders", "partial", "blocked", "failed", "skipped"}
GENERATION_STRATEGIES = {
    "full_proposal",
    "proposal_with_placeholders",
    "evidence_skeleton",
    "manual_review_skeleton",
    "not_applicable",
}
BLOCK_TYPES = {
    "introduction",
    "requirement_response",
    "technical_solution",
    "implementation_plan",
    "organization_plan",
    "quality_assurance",
    "risk_control",
    "service_commitment",
    "evidence_table",
    "evidence_explanation",
    "placeholder_section",
    "conclusion",
    "other",
}
SOURCE_TYPES = {
    "tender_original_fact",
    "enterprise_existing_fact",
    "rule_derived",
    "system_judgement",
    "human_confirmation_required",
    "suggested_material",
    "suggested_writing",
}
CLAIM_TYPES = {
    "tender_fact",
    "confirmed_enterprise_fact",
    "proposal_statement",
    "commitment_statement",
    "cautious_reference",
    "placeholder",
    "prohibited_claim",
}
RESPONSE_METHODS = {
    "explicit_response",
    "incorporated_in_solution",
    "placeholder_pending",
    "not_applicable",
    "blocked",
}

CHAPTER_FILES = {
    "technical_01_": "chapter_01_technical_overview.md",
    "technical_02_": "chapter_02_project_manager.md",
    "technical_03_": "chapter_03_project_team.md",
    "technical_04_": "chapter_04_project_performance.md",
    "technical_05_": "chapter_05_service_solution.md",
    "technical_06_": "chapter_06_schedule_and_assurance.md",
    "technical_07_": "chapter_07_work_foundation.md",
    "technical_08_": "chapter_08_patents_and_innovation.md",
    "technical_09_": "chapter_09_performance_evaluation.md",
}

NEUTRAL_PROJECT_TERMS = ["本项目", "采购项目", "区块链数据管理研究项目"]
COMPLETED_ENTERPRISE_PATTERNS = [
    r"我(?:方|司)已(?:完成|实施|建立|拥有|获得|取得)",
    r"我司具有丰富",
    r"我司现有成熟",
    r"我(?:方|司)具备多个同类",
]


def build_technical_draft(
    lot_name: str,
    output_dir: str | Path = "outputs",
    *,
    dry_run: bool = False,
    preview: bool = False,
    profile_name: str | None = None,
    llm_client: LLMClient | None = None,
    update_context_index: bool = True,
) -> dict[str, Any]:
    if dry_run and preview:
        raise ValueError("--dry-run and --preview cannot be used together")
    base = Path(output_dir)
    lot_dir = base / safe_path_name(lot_name)
    input_paths = drafting_input_paths(lot_dir)
    inputs = {name: load_json(path) for name, path in input_paths.items()}
    validate_drafting_inputs(lot_name, inputs)
    prompts = build_chapter_prompts(inputs)

    if dry_run:
        return {
            "schema_version": SCHEMA_VERSION,
            "lot_name": lot_name,
            "draft_mode": "dry_run",
            "model_info": {
                "provider": None,
                "model": None,
                "profile_name": profile_name,
                "configuration_source": "not_loaded_in_dry_run",
                "generation_available": False,
                "generation_timestamp": None,
                "thinking_config": None,
                "timeout_seconds": None,
                "max_retries": None,
                "max_output_tokens": None,
                "requested_thinking_mode": None,
                "requested_max_output_tokens": None,
                "requested_timeout_seconds": None,
                "actual_usage": {"reasoning_tokens": None, "output_tokens": None},
            },
            "chapter_count": len(prompts),
            "chapters": [prompt_summary(item) for item in prompts],
            "would_write": [str(path) for path in drafting_output_paths(lot_dir / "drafts").values()],
            "model_called": False,
            "files_written": [],
            "validation_passed": True,
        }

    if preview:
        client = None
        model = preview_model_info()
    else:
        client = llm_client or get_llm_client(role="drafting", profile_name=profile_name)
        if getattr(client, "is_mock", False):
            raise ModelConfigurationError("MockLLMClient cannot be used for ai_generated drafts")
        model = client_model_info(client, profile_name)

    compiled = generate_draft_artifacts(
        lot_name,
        inputs=inputs,
        prompts=prompts,
        model_info=model,
        llm_client=client,
        preview=preview,
    )
    drafts_dir = lot_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    output_paths = drafting_output_paths(drafts_dir)
    output_paths["chapter_drafts"].write_text(
        json.dumps(compiled["chapter_drafts"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_paths["draft_manifest"].write_text(
        json.dumps(compiled["draft_manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_paths["draft_generation_report"].write_text(
        json.dumps(compiled["draft_generation_report"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    chapter_by_score = {
        str(item.get("source_score_item_ids", [""])[0]): item
        for item in list(compiled["chapter_drafts"].get("chapters") or [])
        if item.get("source_score_item_ids")
    }
    for prefix, filename in CHAPTER_FILES.items():
        chapter = next((item for score_id, item in chapter_by_score.items() if score_id.startswith(prefix)), None)
        if chapter:
            (drafts_dir / filename).write_text(render_chapter_markdown(chapter), encoding="utf-8")
    output_paths["combined_draft"].write_text(
        render_combined_draft_markdown(compiled["chapter_drafts"]), encoding="utf-8"
    )

    if update_context_index:
        context = inputs["technical_response_context"]
        update_context_draft_index(context, compiled["chapter_drafts"], output_paths)
        input_paths["technical_response_context"].write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "lot_name": lot_name,
        "draft_mode": compiled["chapter_drafts"]["draft_mode"],
        "model_info": compiled["chapter_drafts"]["model_info"],
        "files": {key: str(value) for key, value in output_paths.items()},
        "statistics": compiled["chapter_drafts"]["statistics"],
        "report": compiled["draft_generation_report"],
    }


def inspect_technical_draft(lot_name: str, output_dir: str | Path = "outputs") -> dict[str, Any]:
    drafts_dir = Path(output_dir) / safe_path_name(lot_name) / "drafts"
    paths = drafting_output_paths(drafts_dir)
    drafts = load_json(paths["chapter_drafts"])
    report = load_json(paths["draft_generation_report"])
    chapters = list(drafts.get("chapters") or [])
    return {
        "lot_name": lot_name,
        "draft_mode": drafts.get("draft_mode"),
        "model_info": drafts.get("model_info") or {},
        "files": {key: str(value) for key, value in paths.items()},
        "statistics": drafts.get("statistics") or {},
        "report": report,
        "chapters": [
            {
                "chapter_id": item.get("chapter_id"),
                "chapter_title": item.get("chapter_title"),
                "task_type": item.get("task_type"),
                "blocking_status": item.get("blocking_status"),
                "generation_status": item.get("generation_status"),
                "character_count": chapter_character_count(item),
                "score_items": item.get("source_score_item_ids") or [],
                "score_coverage": item.get("score_point_coverage") or [],
                "requirement_coverage": requirement_coverage_stats(item),
                "placeholder_ids": [value.get("placeholder_id") for value in list(item.get("placeholders") or [])],
                "confirmed_fact_count": count_claims(item, "confirmed_enterprise_fact"),
                "proposal_statement_count": count_claims(item, "proposal_statement"),
                "unsupported_claim_count": len(list((item.get("validation_results") or {}).get("unsupported_claim_ids") or [])),
                "prohibited_evidence_attempts": len(list((item.get("validation_results") or {}).get("prohibited_evidence_attempts") or [])),
                "errors": list((item.get("validation_results") or {}).get("errors") or []),
            }
            for item in chapters
        ],
    }


def generate_draft_artifacts(
    lot_name: str,
    *,
    inputs: dict[str, dict[str, Any]],
    prompts: list[dict[str, Any]],
    model_info: dict[str, Any],
    llm_client: LLMClient | None,
    preview: bool,
) -> dict[str, dict[str, Any]]:
    generated_at = datetime.now(timezone.utc).isoformat()
    response_schema = load_chapter_draft_schema()
    chapters: list[dict[str, Any]] = []
    for prompt in prompts:
        if preview:
            chapter = build_deterministic_chapter(prompt)
        else:
            if llm_client is None:
                raise ModelConfigurationError("real drafting client is required outside preview mode")
            chapter = build_ai_chapter(prompt, llm_client=llm_client, response_schema=response_schema)
        chapter["validation_results"] = validate_chapter_draft(chapter, prompt)
        apply_validation_status(chapter)
        chapters.append(chapter)
    if preview:
        draft_mode = "deterministic_preview"
    else:
        draft_mode = "failed" if all(item.get("generation_status") == "failed" for item in chapters) else "ai_generated"
    global_placeholders = unique_dicts(
        [placeholder for chapter in chapters for placeholder in list(chapter.get("placeholders") or [])],
        "placeholder_id",
    )
    confirmed_claim_count = len(
        [
            claim
            for chapter in chapters
            for claim in list(chapter.get("claims") or [])
            if claim.get("claim_type") == "confirmed_enterprise_fact"
        ]
    )
    warnings = [
        "自动生成测试草稿，包含待确认和待补充占位符，不得直接作为正式投标终稿。",
    ]
    if confirmed_claim_count == 0:
        warnings.append("当前没有可写入草稿的 confirmed enterprise fact。")
    else:
        warnings.append(f"本次草稿引用 {confirmed_claim_count} 条 confirmed enterprise fact claim。")
    if preview:
        warnings.append("本次由显式 preview 模式生成，未调用真实模型或 MockLLMClient。")
    elif draft_mode == "failed":
        warnings.append("真实模型调用失败；未自动回退 mock 或 deterministic_preview。")
    model_info_with_usage = deepcopy(model_info)
    model_info_with_usage["actual_usage"] = aggregate_model_usage(chapters)
    chapter_drafts = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "generated_from": {
            "chapter_task_packets": "chapter_task_packets.json",
            "writing_context_pack": "writing_context_pack.json",
            "evidence_use_map": "evidence_use_map.json",
            "draft_guardrails": "draft_guardrails.json",
        },
        "model_info": {**model_info_with_usage, "generation_timestamp": generated_at},
        "draft_mode": draft_mode,
        "chapters": chapters,
        "combined_draft": {
            "chapter_draft_ids": [item.get("chapter_id") for item in chapters],
            "rendered_from": "chapter_drafts.json.chapters",
            "contains_placeholders": bool(global_placeholders),
            "formal_final": False,
        },
        "global_placeholders": global_placeholders,
        "global_warnings": warnings,
        "statistics": build_draft_statistics(chapters, global_placeholders),
    }
    report = build_generation_report(chapter_drafts)
    manifest = build_draft_manifest(lot_name, chapter_drafts, generated_at)
    validate_chapter_drafts_payload(chapter_drafts)
    return {
        "chapter_drafts": chapter_drafts,
        "draft_manifest": manifest,
        "draft_generation_report": report,
    }


def drafting_input_paths(lot_dir: Path) -> dict[str, Path]:
    return {
        "technical_response_context": lot_dir / "outline" / "technical_response_context.json",
        "writing_context_pack": lot_dir / "writing" / "writing_context_pack.json",
        "technical_response_outline_for_agent": lot_dir / "writing" / "technical_response_outline_for_agent.json",
        "evidence_use_map": lot_dir / "writing" / "evidence_use_map.json",
        "draft_guardrails": lot_dir / "writing" / "draft_guardrails.json",
        "chapter_task_packets": lot_dir / "writing" / "chapter_task_packets.json",
    }


def drafting_output_paths(drafts_dir: Path) -> dict[str, Path]:
    result = {
        "chapter_drafts": drafts_dir / "chapter_drafts.json",
        "draft_manifest": drafts_dir / "draft_manifest.json",
        "combined_draft": drafts_dir / "technical_response_draft.md",
        "draft_generation_report": drafts_dir / "draft_generation_report.json",
    }
    for filename in CHAPTER_FILES.values():
        result[Path(filename).stem] = drafts_dir / filename
    return result


def validate_drafting_inputs(lot_name: str, inputs: dict[str, dict[str, Any]]) -> None:
    for name, payload in inputs.items():
        if str(payload.get("lot_name") or "") != lot_name:
            raise ValueError(f"{name} lot_name mismatch: expected {lot_name}")
        if not payload.get("schema_version"):
            raise ValueError(f"{name} schema_version missing")
    tasks = list(inputs["chapter_task_packets"].get("chapter_tasks") or [])
    if len(tasks) != 9:
        raise ValueError("chapter_task_packets must contain 9 chapter tasks")
    if len(list(inputs["writing_context_pack"].get("chapters") or [])) != 9:
        raise ValueError("writing_context_pack must contain 9 chapters")


def preview_model_info() -> dict[str, Any]:
    return {
        "provider": "deterministic",
        "protocol": "local_template",
        "profile_name": None,
        "model": "structured-preview-v1",
        "api_mode": None,
        "configuration_source": "explicit_preview",
        "generation_available": False,
        "generation_timestamp": None,
        "thinking_config": None,
        "timeout_seconds": None,
        "max_retries": None,
        "max_output_tokens": None,
        "requested_thinking_mode": None,
        "requested_max_output_tokens": None,
        "requested_timeout_seconds": None,
        "actual_usage": {"reasoning_tokens": None, "output_tokens": None},
    }


def client_model_info(client: LLMClient, profile_name: str | None) -> dict[str, Any]:
    profile = getattr(client, "profile", None)
    send_thinking = bool(getattr(profile, "send_thinking", False)) if profile is not None else False
    extra_body = dict(getattr(profile, "extra_body", {}) or {}) if profile is not None else {}
    thinking = extra_body.get("thinking")
    thinking_mode = thinking.get("type") if isinstance(thinking, dict) else thinking
    return {
        "provider": getattr(profile, "provider", client.__class__.__name__),
        "protocol": getattr(profile, "protocol", "structured_client"),
        "profile_name": getattr(profile, "name", profile_name),
        "model": getattr(profile, "model", None),
        "api_mode": getattr(profile, "api_mode", None),
        "configuration_source": "llm_profile" if profile is not None else "injected_client",
        "generation_available": True,
        "generation_timestamp": None,
        "thinking_config": {
            "sent": send_thinking,
            "mode": str(thinking_mode or "not_configured") if send_thinking else "not_sent",
        }
        if profile is not None
        else None,
        "timeout_seconds": getattr(profile, "timeout_seconds", None),
        "max_retries": getattr(profile, "max_retries", None),
        "max_output_tokens": getattr(profile, "max_output_tokens", None),
        "requested_thinking_mode": (
            str(thinking_mode or "not_configured") if send_thinking else "not_sent"
        )
        if profile is not None
        else None,
        "requested_max_output_tokens": getattr(profile, "max_output_tokens", None),
        "requested_timeout_seconds": getattr(profile, "timeout_seconds", None),
        "actual_usage": {
            "reasoning_tokens": None,
            "output_tokens": None,
        },
    }


def aggregate_model_usage(chapters: list[dict[str, Any]]) -> dict[str, int | None]:
    totals: dict[str, int | None] = {"reasoning_tokens": None, "output_tokens": None}
    for key in totals:
        values = [
            (item.get("model_call") or {}).get("usage", {}).get(key)
            for item in chapters
            if isinstance(item.get("model_call"), dict)
        ]
        numeric = [int(value) for value in values if value is not None]
        totals[key] = sum(numeric) if numeric else None
    return totals


def build_chapter_prompts(inputs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    pack = inputs["writing_context_pack"]
    evidence_map = inputs["evidence_use_map"]
    guardrails = inputs["draft_guardrails"]
    outline = inputs["technical_response_outline_for_agent"]
    packet = inputs["chapter_task_packets"]
    pack_chapters = {str(item.get("chapter_id") or ""): item for item in list(pack.get("chapters") or [])}
    outline_chapters = {str(item.get("chapter_id") or ""): item for item in list(outline.get("chapters") or [])}
    chapter_guardrails = {
        str(item.get("chapter_id") or ""): item for item in list(guardrails.get("chapter_specific_guardrails") or [])
    }
    usage_by_id = {
        str(item.get("evidence_id") or ""): item for item in list(evidence_map.get("evidence_usage_items") or [])
    }
    prompts = []
    for task in list(packet.get("chapter_tasks") or []):
        chapter_id = str(task.get("chapter_id") or "")
        pack_chapter = pack_chapters.get(chapter_id, {})
        outline_chapter = outline_chapters.get(chapter_id, {})
        allowed_evidence_ids = [str(item.get("evidence_id") or "") for item in list(task.get("confirmed_facts") or [])]
        cautious_ids = [str(item.get("evidence_id") or "") for item in list(task.get("cautious_references") or [])]
        prohibited_ids = [str(item.get("evidence_id") or "") for item in list(task.get("forbidden_facts") or [])]
        prompt = {
            "task_id": task.get("task_id"),
            "task_name": task.get("chapter_title"),
            "section_type": task.get("task_type"),
            "chapter_task": deepcopy(task),
            "scoring_targets": deepcopy(task.get("scoring_targets") or []),
            "must_write_points": deepcopy(pack_chapter.get("must_write_points") or []),
            "mandatory_requirements": deepcopy(task.get("mandatory_requirements") or []),
            "technical_requirements": deepcopy(task.get("technical_requirements") or []),
            "chapter_guardrails": deepcopy(chapter_guardrails.get(chapter_id, {}).get("rules") or []),
            "proposal_content_allowed": deepcopy(task.get("proposal_content_allowed") or []),
            "confirmed_facts": [deepcopy(usage_by_id[item]) for item in allowed_evidence_ids if item in usage_by_id],
            "cautious_evidence_ids": cautious_ids,
            "prohibited_evidence_ids": unique(prohibited_ids + list(guardrails.get("forbidden_evidence_ids") or [])),
            "placeholders": deepcopy(task.get("placeholders") or []),
            "claim_boundaries": deepcopy(outline_chapter.get("claim_boundaries") or {}),
            "project_conflicts": deepcopy((task.get("input_context") or {}).get("project_conflicts") or []),
            "neutral_project_terms": NEUTRAL_PROJECT_TERMS,
            "output_contract": chapter_agent_output_contract(),
        }
        prompts.append(prompt)
    return prompts


def prompt_summary(prompt: dict[str, Any]) -> dict[str, Any]:
    task = prompt.get("chapter_task") or {}
    return {
        "task_id": prompt.get("task_id"),
        "chapter_id": task.get("chapter_id"),
        "chapter_title": task.get("chapter_title"),
        "task_type": task.get("task_type"),
        "blocking_status": task.get("blocking_status"),
        "scoring_target_count": len(list(prompt.get("scoring_targets") or [])),
        "must_write_point_count": len(list(prompt.get("must_write_points") or [])),
        "mandatory_requirement_count": len(list(prompt.get("mandatory_requirements") or [])),
        "technical_requirement_count": len(list(prompt.get("technical_requirements") or [])),
        "confirmed_fact_count": len(list(prompt.get("confirmed_facts") or [])),
        "placeholder_count": len(list(prompt.get("placeholders") or [])),
        "prohibited_evidence_count": len(list(prompt.get("prohibited_evidence_ids") or [])),
    }


def chapter_agent_output_contract() -> dict[str, Any]:
    return load_chapter_draft_schema()


def build_deterministic_chapter(prompt: dict[str, Any]) -> dict[str, Any]:
    task = prompt["chapter_task"]
    strategy = strategy_for_task(task)
    score_ids = list((task.get("input_context") or {}).get("score_item_ids") or [])
    requirement_ids = list((task.get("input_context") or {}).get("requirement_ids") or [])
    action_ids = list((task.get("input_context") or {}).get("action_group_ids") or [])
    blocks = deterministic_blocks(prompt, strategy)
    claims = deterministic_claims(prompt, blocks)
    claim_ids_by_block: dict[str, list[str]] = {}
    for claim in claims:
        block_id = str(claim.pop("content_block_id", ""))
        if block_id:
            claim_ids_by_block.setdefault(block_id, []).append(str(claim.get("claim_id") or ""))
    for block in blocks:
        block["claim_ids"] = unique(claim_ids_by_block.get(str(block.get("block_id") or ""), []))
    requirement_coverage = build_requirement_coverage(prompt, blocks, strategy)
    score_coverage = build_score_coverage(prompt, blocks)
    status = generation_status_for_strategy(strategy, bool(prompt.get("placeholders")))
    warnings = []
    if task.get("blocking_status") in {"blocked_by_manual_review", "blocked_by_evidence"}:
        warnings.append("本章仅生成受约束骨架；企业事实尚未确认或证据不足。")
    if prompt.get("cautious_evidence_ids"):
        warnings.append("存在 cautious evidence，仅列入人工复核，不进入正文主体。")
    return {
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_order": chapter_order_from_task(task),
        "chapter_title": task.get("chapter_title"),
        "task_type": task.get("task_type"),
        "blocking_status": task.get("blocking_status"),
        "generation_status": status,
        "generation_strategy": strategy,
        "source_score_item_ids": score_ids,
        "source_requirement_ids": requirement_ids,
        "source_action_group_ids": action_ids,
        "source_technical_spec_refs": deepcopy(task.get("technical_requirements") or []),
        "content_blocks": blocks,
        "placeholders": deepcopy(prompt.get("placeholders") or []),
        "claims": claims,
        "evidence_ids_used": [],
        "cautious_evidence_ids_referenced": [],
        "prohibited_evidence_ids_checked": deepcopy(prompt.get("prohibited_evidence_ids") or []),
        "requirement_coverage": requirement_coverage,
        "score_point_coverage": score_coverage,
        "must_write_point_coverage": build_must_write_coverage(prompt, blocks),
        "validation_results": {},
        "human_review_items": build_human_review_items(prompt),
        "generation_warnings": warnings,
        "model_call": None,
    }


def strategy_for_task(task: dict[str, Any]) -> str:
    task_type = str(task.get("task_type") or "")
    blocking = str(task.get("blocking_status") or "")
    has_placeholders = bool(task.get("placeholders"))
    if task_type == "proposal_drafting":
        return "proposal_with_placeholders" if has_placeholders else "full_proposal"
    if blocking == "blocked_by_manual_review" or task_type == "mixed_drafting":
        return "manual_review_skeleton"
    if blocking == "blocked_by_evidence" or task_type == "evidence_based_drafting":
        return "evidence_skeleton"
    if task_type == "not_applicable":
        return "not_applicable"
    return "proposal_with_placeholders"


def generation_status_for_strategy(strategy: str, has_placeholders: bool) -> str:
    if strategy == "not_applicable":
        return "skipped"
    if strategy in {"manual_review_skeleton", "evidence_skeleton"}:
        return "partial"
    return "generated_with_placeholders" if has_placeholders else "generated"


def deterministic_blocks(prompt: dict[str, Any], strategy: str) -> list[dict[str, Any]]:
    task = prompt["chapter_task"]
    score_id = str(((task.get("input_context") or {}).get("score_item_ids") or [""])[0])
    requirement_ids = list((task.get("input_context") or {}).get("requirement_ids") or [])
    score_ids = list((task.get("input_context") or {}).get("score_item_ids") or [])
    if strategy in {"full_proposal", "proposal_with_placeholders"}:
        definitions = proposal_block_definitions(score_id)
    elif strategy == "manual_review_skeleton":
        definitions = manual_review_block_definitions(score_id)
    elif strategy == "evidence_skeleton":
        definitions = evidence_skeleton_block_definitions(score_id)
    else:
        definitions = [("范围说明", "other", "本章当前不适用，不生成正文内容。")]
    blocks = []
    for index, (heading, block_type, content) in enumerate(definitions, start=1):
        block_id = f"{task.get('task_id')}-block-{index:02d}"
        blocks.append(
            {
                "block_id": block_id,
                "heading": heading,
                "block_type": block_type,
                "content": content,
                "source_basis": {
                    "score_item_ids": score_ids,
                    "requirement_ids": requirement_ids,
                    "technical_spec_ref_ids": [item.get("ref_id") for item in list(prompt.get("technical_requirements") or [])],
                    "basis_type": "chapter_task_compilation",
                },
                "covered_score_item_ids": score_ids,
                "covered_requirement_ids": requirement_ids if block_type == "requirement_response" else [],
                "used_evidence_ids": [],
                "placeholder_ids": [],
                "claim_ids": [],
                "source_type": "suggested_writing",
                "generation_notes": ["deterministic_preview", "使用中性项目称谓，未采用未确认包名称。"],
            }
        )
    if prompt.get("placeholders"):
        blocks.append(placeholder_block(prompt, len(blocks) + 1))
    return blocks


def proposal_block_definitions(score_id: str) -> list[tuple[str, str, str]]:
    if score_id.startswith("technical_01_"):
        return [
            ("项目理解与总体目标", "introduction", "本技术响应以采购项目的研究目标、技术规范和交付要求为主线，围绕区块链数据可信、数据治理、权限控制、安全审计和技术标准研究组织整体方案。各项工作将保持需求、技术路线、实施活动、成果文件和验收依据之间的一致性，并通过可追溯的响应索引确保内容完整、逻辑清晰。"),
            ("总体技术路线", "technical_solution", "本方案拟采用“需求与标准分析、数据治理设计、区块链可信机制研究、验证与评估、成果固化”的技术路线。研究过程中将明确数据对象、责任主体、权限边界和审计要求，形成可验证的规则、模型和技术成果，避免将单一平台实现替代标准研究目标。"),
            ("招标要求响应机制", "requirement_response", "我方将建立评分项、技术规范和必须响应要求的对应清单，对每项要求明确响应内容、实施动作、交付物和检查方式。涉及尚待确认的企业材料时仅保留结构化占位，不以方案性描述替代证明事实。"),
            ("质量与一致性控制", "quality_assurance", "为确保技术响应的规范性和完整性，项目实施过程中将设置需求基线、方案评审、成果校核和交付前一致性检查。章节之间将统一术语、项目边界和数据口径，并对技术要求、进度计划、验收依据和风险措施进行交叉核验。"),
            ("风险与合规控制", "risk_control", "项目将对数据安全、权限越界、接口适配、研究成果可验证性和进度偏差进行持续识别。针对包名称等未确认事实统一使用中性项目称谓，所有招标事实保留来源 ID，企业事实仅在确认后引用。"),
            ("总体承诺", "service_commitment", "我方将按照采购文件及技术规范组织项目实施，按计划完成研究、验证、成果编制和验收配合工作，并通过全过程记录和阶段评审保证交付成果可追溯、可检查、可验收。"),
        ]
    if score_id.startswith("technical_05_"):
        return [
            ("服务目标与原则", "introduction", "本项目服务方案将以研究任务落地、数据可信管理和技术成果可验收为目标，遵循需求导向、标准牵引、安全可控、过程可追溯和成果可复用原则。方案不依赖未确认的企业案例，而以本项目拟采取的组织、技术和质量措施展开。"),
            ("区块链数据可信技术方案", "technical_solution", "本方案拟围绕数据目录、数据权属、访问权限、可信记录和审计追踪开展设计。区块链机制用于记录关键数据操作和责任链路，结合身份认证、最小权限、数据完整性校验与审计规则，支撑工业或电力数据管理场景下的可信共享研究。具体对象和标准名称将在需求确认后统一。"),
            ("数据治理与标准研究", "technical_solution", "项目实施过程中将梳理数据分类分级、元数据、质量规则、交换接口和安全控制要求，形成标准条款候选、术语定义、技术指标和验证方法。对外部标准和制度引用将保留来源并核验适用范围，不自行扩展未解析条款。"),
            ("服务组织与沟通", "organization_plan", "我方将建立项目统筹、技术研究、数据治理、安全审计、测试验证和成果管理等工作界面，通过启动会、专题评审、阶段汇报和问题闭环机制保持各方信息一致。具体人员信息在完成确认前不写入正文。"),
            ("实施与问题处理", "implementation_plan", "服务过程将按照需求澄清、方案设计、研究验证、成果编制、内部评审和验收支持推进。问题按影响程度登记、分析、分派和关闭；对关键问题设置责任人、完成时限和验证标准，确保处理过程可追踪。"),
            ("质量保证", "quality_assurance", "为确保研究成果质量，将实施输入检查、过程评审、文档校核、测试验证和交付检查。技术结论需有分析依据，标准条款需有场景与验证支撑，交付材料需满足格式、版本、签审和可追溯要求。"),
            ("安全与风险控制", "risk_control", "项目将建立数据访问控制、敏感信息保护、操作留痕、成果文件权限管理和安全事件处置机制。对需求变化、接口条件、研究结论不确定性和进度偏差设置预警与调整流程，避免过度承诺。"),
            ("服务与验收承诺", "service_commitment", "我方将按采购要求提供全过程沟通、阶段成果汇报、问题响应、测试配合、验收资料整理和成果移交服务。所有承诺以招标要求和最终确认的实施边界为准，不将待确认材料表述为现有能力。"),
        ]
    return [
        ("进度目标与编制原则", "introduction", "本项目进度计划将以采购要求、研究任务依赖关系和成果验收节点为约束，采用阶段化、可检查和可调整的组织方式。计划中的时间、责任和交付物将在项目启动后结合确认需求形成基线。"),
        ("阶段与里程碑", "implementation_plan", "项目拟划分为启动与需求确认、技术研究与方案设计、验证与成果编制、评审完善与验收移交等阶段。各阶段将设置输入条件、关键活动、里程碑成果和完成判据，前置成果未通过评审时不直接进入下一阶段。"),
        ("任务分解与协同", "organization_plan", "我方将把研究、数据治理、区块链可信机制、安全审计、标准成果和测试验证任务分解到可执行工作包，明确接口关系和协同节奏。具体人员安排待人工确认后补充。"),
        ("进度监控与预警", "implementation_plan", "项目实施过程中将采用周度跟踪、里程碑检查、问题清单和变更记录监控进展。对关键路径延误、输入不足和评审未通过等情况设置预警，及时分析影响并形成纠偏计划。"),
        ("进度保证措施", "quality_assurance", "为确保计划执行，将通过需求基线控制、资源协调、并行工作安排、阶段评审和交付前检查降低返工。关键成果预留校核和修改时间，确保研究质量不因压缩周期而降低。"),
        ("风险与纠偏", "risk_control", "发生需求变化、外部条件延迟或技术验证偏差时，我方将评估对范围、进度和质量的影响，提出调整顺序、补充资源或优化验证路径等措施，并在获得确认后更新计划基线。"),
        ("进度承诺", "service_commitment", "我方将依据最终确认的项目计划组织实施，按期提交阶段成果和验收材料，并保持进度信息真实、透明、可追溯。"),
        ("必须响应要求", "requirement_response", "本章涉及的进度、保证、交付及验收要求将分别映射到里程碑、工作包和检查点；非技术正文的格式签章要求保留在全局合规检查中。"),
    ]


def manual_review_block_definitions(score_id: str) -> list[tuple[str, str, str]]:
    if score_id.startswith("technical_02_"):
        return [
            ("岗位定位", "introduction", "项目负责人承担总体协调、需求与范围管理、技术路线统筹、进度质量控制和对外沟通职责，是项目决策与执行衔接的责任岗位。"),
            ("能力要求", "organization_plan", "项目负责人应具备与采购任务相适应的专业背景、工作年限、技术研究与项目管理能力，能够组织区块链数据管理、数据治理、安全审计和标准成果研究工作。具体人员事实待人工确认。"),
            ("职责与工作界面", "organization_plan", "项目负责人拟负责计划审批、重大问题协调、阶段成果评审和验收组织，并与技术研究、数据治理、安全、测试及成果编制岗位建立明确工作界面。"),
            ("管理机制", "requirement_response", "项目实施过程中将通过目标分解、例会沟通、问题升级、变更控制和阶段评审支持项目负责人履职。姓名、部门、拟任角色、投入状态、证书和经历不在确认前写入。"),
        ]
    return [
        ("团队配置原则", "introduction", "项目团队将围绕研究管理、区块链技术、数据治理、安全审计、测试验证和成果标准化等工作配置岗位，确保专业覆盖与任务分工相匹配。"),
        ("组织结构与分工", "organization_plan", "团队拟采用项目负责人统筹、专业岗位协作的组织方式，明确任务接口、成果责任和沟通路径。具体人员名单、部门和岗位信息待人工确认。"),
        ("协作与投入管理", "organization_plan", "项目实施过程中将建立人员投入确认、任务分派、工作交接和替补机制，按阶段需求协调资源，避免未经确认即声称固定团队规模或人员可投入。"),
        ("能力与质量要求", "requirement_response", "参与人员应满足岗位所需的专业、经验和质量要求，相关简历、证书和项目经历仅在形成 confirmed evidence 后进入人员事实部分。"),
    ]


def evidence_skeleton_block_definitions(score_id: str) -> list[tuple[str, str, str]]:
    if score_id.startswith("technical_04_"):
        return [
            ("评分响应说明", "introduction", "本节用于按评分规则组织企业业绩证明。当前未形成可用于正文的 confirmed 企业业绩，故不填写项目名称、客户、合同金额、时间或验收结论。"),
            ("业绩证据包结构", "evidence_table", "每个独立业绩证据包应包含项目名称、客户、时间、金额、工作内容、合同关键页，以及可用的中标通知书、验收证明或用户证明。各项目需单独编号并核验独立性。"),
            ("相关性说明方法", "evidence_explanation", "材料确认后，应分别说明项目内容与技术开发或技术服务要求、区块链数据管理研究主题及本项目工作范围的对应关系，不以人员简历中的项目经历替代企业合同业绩。"),
            ("数量与完整性检查", "requirement_response", "业绩数量只统计符合条件且证据包相互独立的 confirmed 项目。candidate、needs_review、mock 或缺少关键证明的材料不计入已满足数量。"),
        ]
    if score_id.startswith("technical_07_"):
        return [
            ("评分响应说明", "introduction", "本节用于呈现与采购项目相关的产品化基础、科技成果或专题研究。当前无 confirmed 工作基础证据，不生成企业已有产品或成果事实。"),
            ("材料组织结构", "evidence_table", "后续材料应按产品化证明、科技成果证明和专题研究证明分类，记录成果名称、形成时间、权属或主体、适用范围、来源文件及与采购主题的关系。"),
            ("适用性说明方法", "evidence_explanation", "材料确认后，应基于真实摘录说明成果如何支撑区块链数据可信、数据治理、安全审计或技术标准研究，不从文件名或关键词推导既有能力。"),
            ("使用边界", "requirement_response", "未经确认的个人经历、一般资质或无来源摘要不得作为工作基础确定事实；缺少证明时保留占位。"),
        ]
    if score_id.startswith("technical_08_"):
        return [
            ("评分响应说明", "introduction", "本节用于按评分规则组织与采购主题相关的有效专利证明。当前无 confirmed 专利，不生成具体专利名称、编号、权利人或授权状态。"),
            ("专利材料结构", "evidence_table", "每项专利应记录名称、编号、类型、权利人、授权状态、来源文件和与采购主题的相关性，并按独立专利去重。"),
            ("相关性说明方法", "evidence_explanation", "材料确认后，应依据专利摘要或权利要求中可定位内容说明其与区块链、数据治理、权限控制或安全审计的关系，不仅凭标题相似认定。"),
            ("数量检查", "requirement_response", "数量只统计符合全部条件的 confirmed 独立专利，candidate 和 needs_review 不计入已满足数量。"),
        ]
    return [
        ("评分响应说明", "introduction", "本节用于呈现最近一年度适用主体的供应商绩效评价材料。当前无 confirmed 评价结果，不生成评价等级、分值、年度或评价结论。"),
        ("评价材料结构", "evidence_table", "后续应记录评价主体、适用年度、被评价企业、评价等级或分值、来源文件及位置，并核对联合体适用规则。"),
        ("适用性核验", "evidence_explanation", "材料确认后，应核对评价主体、年份和投标主体是否符合评分规则；无法确认时继续保留人工复核状态。"),
        ("使用边界", "requirement_response", "未参加评价、评价缺失或主体不一致等情形应按招标规则如实响应，不得自行推定已有评价结果。"),
    ]


def placeholder_block(prompt: dict[str, Any], index: int) -> dict[str, Any]:
    task = prompt["chapter_task"]
    placeholders = list(prompt.get("placeholders") or [])
    return {
        "block_id": f"{task.get('task_id')}-block-{index:02d}",
        "heading": "待确认与待补充事项",
        "block_type": "placeholder_section",
        "content": "\n".join(format_placeholder(item) for item in placeholders),
        "source_basis": {
            "score_item_ids": list((task.get("input_context") or {}).get("score_item_ids") or []),
            "requirement_ids": list((task.get("input_context") or {}).get("requirement_ids") or []),
            "placeholder_ids": [item.get("placeholder_id") for item in placeholders],
            "basis_type": "stage4_structured_placeholders",
        },
        "covered_score_item_ids": list((task.get("input_context") or {}).get("score_item_ids") or []),
        "covered_requirement_ids": [],
        "used_evidence_ids": [],
        "placeholder_ids": [item.get("placeholder_id") for item in placeholders],
        "claim_ids": [],
        "source_type": "suggested_material",
        "generation_notes": ["直接复用阶段 4 placeholder_id，未生成新占位符体系。"],
    }


def deterministic_claims(prompt: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task = prompt["chapter_task"]
    score_ids = list((task.get("input_context") or {}).get("score_item_ids") or [])
    requirement_ids = list((task.get("input_context") or {}).get("requirement_ids") or [])
    claims: list[dict[str, Any]] = []
    sequence = 1
    for target in list(prompt.get("scoring_targets") or []):
        block = next((item for item in blocks if item.get("block_type") in {"introduction", "requirement_response"}), blocks[0])
        claims.append(
            make_claim(
                task,
                sequence,
                f"本章响应评分项“{target.get('score_item_name') or target.get('score_item_id')}”。",
                "tender_fact",
                [target.get("score_item_id")],
                [],
                block.get("block_id"),
            )
        )
        sequence += 1
    for block in blocks:
        if block.get("block_type") == "placeholder_section":
            continue
        content = str(block.get("content") or "")
        claim_type = "commitment_statement" if any(term in content for term in ["我方将", "项目将", "将建立", "将按照"]) else "proposal_statement"
        claims.append(
            make_claim(
                task,
                sequence,
                first_sentence(content),
                claim_type,
                unique(score_ids + requirement_ids),
                [],
                block.get("block_id"),
            )
        )
        sequence += 1
    for placeholder in list(prompt.get("placeholders") or []):
        block = next(item for item in blocks if placeholder.get("placeholder_id") in list(item.get("placeholder_ids") or []))
        claim = make_claim(
            task,
            sequence,
            format_placeholder(placeholder),
            "placeholder",
            unique(
                list(placeholder.get("related_evidence_requirement_ids") or [])
                + list(placeholder.get("related_resolution_ids") or [])
            ),
            [],
            block.get("block_id"),
        )
        claim["related_placeholder_id"] = placeholder.get("placeholder_id")
        claim["human_review_required"] = True
        claims.append(claim)
        sequence += 1
    return claims


def make_claim(
    task: dict[str, Any],
    sequence: int,
    text: str,
    claim_type: str,
    source_ids: list[Any],
    evidence_ids: list[Any],
    block_id: Any,
) -> dict[str, Any]:
    return {
        "claim_id": f"{task.get('task_id')}-claim-{sequence:03d}",
        "text": text,
        "claim_type": claim_type,
        "source_ids": unique(source_ids),
        "evidence_ids": unique(evidence_ids),
        "verification_status": "source_linked" if claim_type == "tender_fact" else "generated_proposal" if claim_type in {"proposal_statement", "commitment_statement"} else "pending",
        "allowed": claim_type != "prohibited_claim",
        "restriction_reason": None,
        "related_placeholder_id": None,
        "human_review_required": claim_type in {"cautious_reference", "placeholder", "prohibited_claim"},
        "content_block_id": block_id,
    }


def build_requirement_coverage(
    prompt: dict[str, Any], blocks: list[dict[str, Any]], strategy: str
) -> list[dict[str, Any]]:
    coverage = []
    for requirement in list(prompt.get("mandatory_requirements") or []):
        text = f"{requirement.get('title', '')} {requirement.get('raw_text', '')}"
        block = select_response_block(blocks, text)
        if strategy in {"manual_review_skeleton", "evidence_skeleton"}:
            method = "placeholder_pending" if prompt.get("placeholders") else "blocked"
            responded = False
            reason = "企业事实或证明材料尚未确认，已保留结构化占位。"
        else:
            method = "explicit_response" if requirement.get("compliance_level") == "fatal_if_missing" else "incorporated_in_solution"
            responded = True
            reason = None
        coverage.append(
            {
                "requirement_id": requirement.get("requirement_id"),
                "responded": responded,
                "content_block_ids": [block.get("block_id")] if block else [],
                "response_method": method,
                "unresolved_reason": reason,
                "compliance_level": requirement.get("compliance_level"),
            }
        )
    return coverage


def build_score_coverage(prompt: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    block_ids = [item.get("block_id") for item in blocks if item.get("block_type") != "placeholder_section"]
    return [
        {
            "score_item_id": target.get("score_item_id"),
            "covered": bool(block_ids),
            "content_block_ids": block_ids,
            "coverage_method": "proposal_and_structure" if not prompt.get("confirmed_facts") else "proposal_and_confirmed_evidence",
            "full_score_claimed": False,
        }
        for target in list(prompt.get("scoring_targets") or [])
    ]


def build_must_write_coverage(prompt: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, point in enumerate(list(prompt.get("must_write_points") or []), start=1):
        block = select_response_block(blocks, str(point))
        result.append(
            {
                "point_id": f"{prompt.get('task_id')}-must-{index:03d}",
                "point": point,
                "covered": block is not None,
                "content_block_ids": [block.get("block_id")] if block else [],
            }
        )
    return result


def select_response_block(blocks: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    mappings = [
        (["进度", "工期", "里程碑"], "implementation_plan"),
        (["质量", "验收", "测试"], "quality_assurance"),
        (["安全", "保密", "风险"], "risk_control"),
        (["组织", "人员", "负责人"], "organization_plan"),
        (["服务", "承诺", "响应"], "service_commitment"),
        (["证据", "材料", "业绩", "专利", "绩效"], "evidence_table"),
    ]
    for terms, block_type in mappings:
        if any(term in text for term in terms):
            match = next((item for item in blocks if item.get("block_type") == block_type), None)
            if match:
                return match
    return next((item for item in blocks if item.get("block_type") == "requirement_response"), None) or next(
        (item for item in blocks if item.get("block_type") != "placeholder_section"), None
    )


def build_human_review_items(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for placeholder in list(prompt.get("placeholders") or []):
        items.append(
            {
                "review_type": "placeholder_resolution",
                "placeholder_id": placeholder.get("placeholder_id"),
                "question": placeholder.get("required_fact"),
                "related_resolution_ids": deepcopy(placeholder.get("related_resolution_ids") or []),
                "blocking_level": placeholder.get("blocking_level"),
            }
        )
    if prompt.get("cautious_evidence_ids"):
        items.append(
            {
                "review_type": "cautious_evidence",
                "evidence_ids": deepcopy(prompt.get("cautious_evidence_ids") or []),
                "question": "确认候选材料身份、有效性和适用范围后，再决定是否进入正文。",
                "blocking_level": "blocks_confirmed_evidence",
            }
        )
    return items


def build_ai_chapter(
    prompt: dict[str, Any],
    *,
    llm_client: LLMClient,
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    task = prompt["chapter_task"]
    try:
        call = llm_client.generate_structured(
            system_prompt=chapter_drafting_system_prompt(),
            user_prompt=chapter_prompt_to_text(prompt),
            response_schema=response_schema,
            request_metadata={
                "purpose": "technical_chapter_drafting",
                "lot_name": str((task.get("input_context") or {}).get("lot_name") or ""),
                "chapter_id": str(task.get("chapter_id") or ""),
                "task_id": str(task.get("task_id") or ""),
                "chapter_title": str(task.get("chapter_title") or ""),
            },
        )
        if call.provider == "mock" or call.finish_status == "mock":
            raise ModelConfigurationError("MockLLMClient result cannot become an AI draft")
        result = call.parsed_output
        errors = validate_agent_contract(result, prompt)
        if errors:
            return failed_chapter(task, prompt, errors, model_call=call)
        return normalize_ai_chapter(task, prompt, result, model_call=call)
    except ModelError as exc:
        return failed_chapter(task, prompt, [str(exc)], error_type=exc.__class__.__name__)


def chapter_drafting_system_prompt() -> str:
    return (
        "你是章节级技术响应撰写 Agent。严格依据输入的章节任务、评分项、要求、技术规范、"
        "confirmed facts、占位符和 guardrails 生成结构化结果。企业既有事实只能来自 confirmed evidence；"
        "candidate、needs_review、unusable、rejected、mock 不得写成确定事实。方案内容使用拟、将、计划、承诺等表达。"
        "不得自行确定未确认包名称，不得输出投标决策性结论。只返回符合正式章节 Schema 的 JSON。"
    )


def chapter_prompt_to_text(prompt: dict[str, Any]) -> str:
    payload = deepcopy(prompt)
    payload.pop("output_contract", None)
    return "请生成以下单章任务的结构化技术响应测试草稿：\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def validate_agent_contract(result: dict[str, Any], prompt: dict[str, Any]) -> list[str]:
    errors = []
    for field in chapter_agent_output_contract()["required"]:
        if field not in result:
            errors.append(f"missing model output field: {field}")
    if not isinstance(result.get("sections"), list):
        errors.append("model output sections must be an array")
    if not isinstance(result.get("claims"), list):
        errors.append("model output claims must be an array")
    allowed_placeholder_ids = {str(item.get("placeholder_id") or "") for item in list(prompt.get("placeholders") or [])}
    unknown_placeholders = [value for value in list(result.get("placeholders_used") or []) if str(value) not in allowed_placeholder_ids]
    if unknown_placeholders:
        errors.append(f"model used unknown placeholder ids: {unknown_placeholders}")
    return errors


def normalize_ai_chapter(
    task: dict[str, Any],
    prompt: dict[str, Any],
    result: dict[str, Any],
    *,
    model_call: ModelCallResult,
) -> dict[str, Any]:
    blocks = []
    for index, section in enumerate(list(result.get("sections") or []), start=1):
        block_type = str(section.get("block_type") or "other") if isinstance(section, dict) else "other"
        if block_type not in BLOCK_TYPES:
            block_type = "other"
        blocks.append(
            {
                "block_id": f"{task.get('task_id')}-block-{index:02d}",
                "heading": section.get("heading") if isinstance(section, dict) else f"小节{index}",
                "block_type": block_type,
                "content": section.get("content") if isinstance(section, dict) else str(section),
                "source_basis": {"basis_type": "structured_model_output"},
                "covered_score_item_ids": deepcopy(result.get("score_items_covered") or []),
                "covered_requirement_ids": deepcopy(result.get("requirements_covered") or []),
                "used_evidence_ids": deepcopy(section.get("used_evidence_ids") or []) if isinstance(section, dict) else [],
                "placeholder_ids": deepcopy(section.get("placeholder_ids") or []) if isinstance(section, dict) else [],
                "claim_ids": [],
                "source_type": "suggested_writing",
                "generation_notes": ["structured_model_output"],
            }
        )
    required_placeholders = deepcopy(prompt.get("placeholders") or [])
    represented_placeholder_ids = {
        str(value)
        for block in blocks
        for value in list(block.get("placeholder_ids") or [])
    }
    if any(str(item.get("placeholder_id") or "") not in represented_placeholder_ids for item in required_placeholders):
        blocks.append(placeholder_block(prompt, len(blocks) + 1))
    claims = []
    for index, item in enumerate(list(result.get("claims") or []), start=1):
        claim_type = str(item.get("claim_type") or "proposal_statement") if isinstance(item, dict) else "proposal_statement"
        if claim_type not in CLAIM_TYPES:
            claim_type = "prohibited_claim"
        claims.append(
            {
                "claim_id": f"{task.get('task_id')}-claim-{index:03d}",
                "text": item.get("text") if isinstance(item, dict) else str(item),
                "claim_type": claim_type,
                "source_ids": deepcopy(item.get("source_ids") or []) if isinstance(item, dict) else [],
                "evidence_ids": deepcopy(item.get("evidence_ids") or []) if isinstance(item, dict) else [],
                "verification_status": item.get("verification_status") if isinstance(item, dict) else "unverified",
                "allowed": bool(item.get("allowed", claim_type != "prohibited_claim")) if isinstance(item, dict) else True,
                "restriction_reason": item.get("restriction_reason") if isinstance(item, dict) else None,
                "related_placeholder_id": item.get("related_placeholder_id") if isinstance(item, dict) else None,
                "human_review_required": bool(item.get("human_review_required")) if isinstance(item, dict) else False,
            }
        )
    return {
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_order": chapter_order_from_task(task),
        "chapter_title": task.get("chapter_title"),
        "task_type": task.get("task_type"),
        "blocking_status": task.get("blocking_status"),
        "generation_status": "generated_with_placeholders" if required_placeholders else "generated",
        "generation_strategy": strategy_for_task(task),
        "source_score_item_ids": deepcopy((task.get("input_context") or {}).get("score_item_ids") or []),
        "source_requirement_ids": deepcopy((task.get("input_context") or {}).get("requirement_ids") or []),
        "source_action_group_ids": deepcopy((task.get("input_context") or {}).get("action_group_ids") or []),
        "source_technical_spec_refs": deepcopy(task.get("technical_requirements") or []),
        "content_blocks": blocks,
        "placeholders": required_placeholders,
        "claims": claims,
        "evidence_ids_used": unique(
            [value for block in blocks for value in list(block.get("used_evidence_ids") or [])]
            + [value for claim in claims for value in list(claim.get("evidence_ids") or [])]
        ),
        "cautious_evidence_ids_referenced": [],
        "prohibited_evidence_ids_checked": deepcopy(prompt.get("prohibited_evidence_ids") or []),
        "requirement_coverage": build_model_requirement_coverage(prompt, result, blocks),
        "score_point_coverage": build_model_score_coverage(prompt, result, blocks),
        "must_write_point_coverage": build_must_write_coverage(prompt, blocks),
        "validation_results": {},
        "human_review_items": build_human_review_items(prompt),
        "generation_warnings": deepcopy(result.get("warnings") or []),
        "model_call": model_call.as_dict(include_parsed_output=False),
    }


def failed_chapter(
    task: dict[str, Any],
    prompt: dict[str, Any],
    errors: list[str],
    *,
    error_type: str = "ModelResponseError",
    model_call: ModelCallResult | None = None,
) -> dict[str, Any]:
    return {
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_order": chapter_order_from_task(task),
        "chapter_title": task.get("chapter_title"),
        "task_type": task.get("task_type"),
        "blocking_status": task.get("blocking_status"),
        "generation_status": "failed",
        "generation_strategy": strategy_for_task(task),
        "source_score_item_ids": deepcopy((task.get("input_context") or {}).get("score_item_ids") or []),
        "source_requirement_ids": deepcopy((task.get("input_context") or {}).get("requirement_ids") or []),
        "source_action_group_ids": deepcopy((task.get("input_context") or {}).get("action_group_ids") or []),
        "source_technical_spec_refs": deepcopy(task.get("technical_requirements") or []),
        "content_blocks": [],
        "placeholders": deepcopy(prompt.get("placeholders") or []),
        "claims": [],
        "evidence_ids_used": [],
        "cautious_evidence_ids_referenced": [],
        "prohibited_evidence_ids_checked": deepcopy(prompt.get("prohibited_evidence_ids") or []),
        "requirement_coverage": [],
        "score_point_coverage": [],
        "must_write_point_coverage": [],
        "validation_results": {"passed": False, "errors": errors, "warnings": [], "checks": [], "unsupported_claim_ids": [], "prohibited_evidence_attempts": []},
        "human_review_items": build_human_review_items(prompt),
        "generation_warnings": errors,
        "model_call": model_call.as_dict(include_parsed_output=False) if model_call else None,
        "model_error_type": error_type,
    }


def build_model_requirement_coverage(
    prompt: dict[str, Any], result: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    covered = {str(value) for value in list(result.get("requirements_covered") or [])}
    block_ids = [item.get("block_id") for item in blocks]
    return [
        {
            "requirement_id": item.get("requirement_id"),
            "responded": str(item.get("requirement_id")) in covered,
            "content_block_ids": block_ids if str(item.get("requirement_id")) in covered else [],
            "response_method": "incorporated_in_solution" if str(item.get("requirement_id")) in covered else "blocked",
            "unresolved_reason": None if str(item.get("requirement_id")) in covered else "模型未返回该 requirement_id 的覆盖记录。",
            "compliance_level": item.get("compliance_level"),
        }
        for item in list(prompt.get("mandatory_requirements") or [])
    ]


def build_model_score_coverage(
    prompt: dict[str, Any], result: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    covered = {str(value) for value in list(result.get("score_items_covered") or [])}
    return [
        {
            "score_item_id": item.get("score_item_id"),
            "covered": str(item.get("score_item_id")) in covered,
            "content_block_ids": [block.get("block_id") for block in blocks] if str(item.get("score_item_id")) in covered else [],
            "coverage_method": "structured_model_output",
            "full_score_claimed": False,
        }
        for item in list(prompt.get("scoring_targets") or [])
    ]


def validate_chapter_draft(chapter: dict[str, Any], prompt: dict[str, Any]) -> dict[str, Any]:
    if chapter.get("generation_status") == "failed":
        return deepcopy(chapter.get("validation_results") or {})
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    unsupported_claim_ids: list[str] = []
    used_evidence = set(chapter.get("evidence_ids_used") or [])
    confirmed_ids = {str(item.get("evidence_id") or "") for item in list(prompt.get("confirmed_facts") or [])}
    prohibited_ids = {str(value) for value in list(prompt.get("prohibited_evidence_ids") or [])}
    prohibited_attempts = sorted(used_evidence & prohibited_ids)
    add_check(checks, "prohibited_evidence_not_used", not prohibited_attempts, prohibited_attempts)
    if prohibited_attempts:
        errors.append(f"使用了 prohibited evidence: {prohibited_attempts}")
    nonconfirmed_attempts = sorted(used_evidence - confirmed_ids)
    add_check(checks, "only_confirmed_evidence_used_in_body", not nonconfirmed_attempts, nonconfirmed_attempts)
    if nonconfirmed_attempts:
        errors.append(f"正文使用了非 confirmed evidence: {nonconfirmed_attempts}")

    for claim in list(chapter.get("claims") or []):
        claim_id = str(claim.get("claim_id") or "")
        claim_type = str(claim.get("claim_type") or "")
        evidence_ids = {str(value) for value in list(claim.get("evidence_ids") or [])}
        if claim_type == "confirmed_enterprise_fact" and (not evidence_ids or not evidence_ids.issubset(confirmed_ids)):
            unsupported_claim_ids.append(claim_id)
            errors.append(f"confirmed enterprise fact 缺少 confirmed evidence: {claim_id}")
        if claim_type == "tender_fact" and not claim.get("source_ids"):
            unsupported_claim_ids.append(claim_id)
            errors.append(f"tender fact 缺少来源 ID: {claim_id}")
    add_check(checks, "enterprise_fact_has_confirmed_evidence", not unsupported_claim_ids, unsupported_claim_ids)

    content = "\n".join(str(item.get("content") or "") for item in list(chapter.get("content_blocks") or []))
    expected_placeholder_ids = {str(item.get("placeholder_id") or "") for item in list(prompt.get("placeholders") or [])}
    rendered_placeholder_ids = {
        str(value)
        for block in list(chapter.get("content_blocks") or [])
        for value in list(block.get("placeholder_ids") or [])
    }
    missing_placeholders = sorted(expected_placeholder_ids - rendered_placeholder_ids)
    add_check(checks, "stage4_placeholders_preserved", not missing_placeholders, missing_placeholders)
    if missing_placeholders:
        errors.append(f"阶段4占位符被删除: {missing_placeholders}")

    conflict_values = unique(
        [
            value
            for item in list(prompt.get("project_conflicts") or [])
            if item.get("field") == "package_name" and item.get("status") == "needs_human_confirmation"
            for value in list(item.get("candidate_values") or [])
        ]
    )
    selected_conflict_values = [value for value in conflict_values if value and value in content]
    add_check(checks, "package_name_conflict_not_resolved_by_writer", not selected_conflict_values, selected_conflict_values)
    if selected_conflict_values:
        errors.append("草稿自行采用了未确认包名称候选值。")

    score_id = str((chapter.get("source_score_item_ids") or [""])[0])
    if score_id.startswith(("technical_02_", "technical_03_")):
        person_tokens = candidate_person_tokens(prompt)
        used_tokens = [value for value in person_tokens if value in content]
        add_check(checks, "unconfirmed_person_not_in_body", not used_tokens, used_tokens)
        if used_tokens:
            errors.append(f"草稿使用了未确认人员信息: {used_tokens}")
    if score_id.startswith("technical_03_") and re.search(r"(?:已有|现有|具备)\s*30\s*人", content):
        errors.append("草稿无证据声称已有30人团队。")
    if score_id.startswith("technical_04_") and any(term in content for term in ["合同金额为", "客户为", "已通过验收"]):
        errors.append("业绩章节包含未经证据确认的项目事实。")
    if score_id.startswith("technical_08_") and re.search(r"专利(?:名称|编号)[：:]", content):
        errors.append("专利章节包含未经证据确认的具体专利事实。")
    if score_id.startswith("technical_09_") and re.search(r"绩效评价(?:为|结果为|等级为)", content):
        errors.append("绩效章节包含未经证据确认的评价结果。")

    completed_patterns = [pattern for pattern in COMPLETED_ENTERPRISE_PATTERNS if re.search(pattern, content)]
    add_check(checks, "proposal_not_written_as_completed_enterprise_fact", not completed_patterns, completed_patterns)
    if completed_patterns and not confirmed_ids:
        errors.append("方案内容被表述为已完成企业事实。")
    forbidden_terms = [term for term in FORBIDDEN_DECISION_TERMS if term.lower() in content.lower()]
    add_check(checks, "no_bid_decision_language", not forbidden_terms, forbidden_terms)
    if forbidden_terms:
        errors.append("草稿包含投标决策性结论。")

    uncovered_must = [item.get("point_id") for item in list(chapter.get("must_write_point_coverage") or []) if not item.get("covered")]
    add_check(checks, "must_write_points_covered", not uncovered_must, uncovered_must)
    if uncovered_must:
        errors.append(f"must_write_points 未覆盖: {uncovered_must}")
    missing_requirement_status = [item.get("requirement_id") for item in list(chapter.get("requirement_coverage") or []) if item.get("response_method") not in RESPONSE_METHODS]
    add_check(checks, "mandatory_requirements_have_status", not missing_requirement_status, missing_requirement_status)
    if missing_requirement_status:
        errors.append(f"mandatory requirement 缺少响应状态: {missing_requirement_status}")
    uncovered_scores = [item.get("score_item_id") for item in list(chapter.get("score_point_coverage") or []) if not item.get("covered")]
    add_check(checks, "score_items_covered", not uncovered_scores, uncovered_scores)
    if uncovered_scores:
        errors.append(f"评分项未覆盖: {uncovered_scores}")
    if prompt.get("cautious_evidence_ids"):
        warnings.append("候选证据未进入正文；需人工确认后重新生成。")
    return {
        "passed": not errors,
        "errors": unique(errors),
        "warnings": unique(warnings),
        "checks": checks,
        "unsupported_claim_ids": unique(unsupported_claim_ids),
        "prohibited_evidence_attempts": prohibited_attempts,
    }


def apply_validation_status(chapter: dict[str, Any]) -> None:
    validation = chapter.get("validation_results") or {}
    if validation.get("passed"):
        return
    if chapter.get("generation_status") == "failed":
        return
    errors = list(validation.get("errors") or [])
    severe = any(
        any(
            term in error
            for term in [
                "prohibited evidence",
                "非 confirmed evidence",
                "confirmed enterprise fact",
                "占位符被删除",
                "未确认包名称",
                "投标决策性",
            ]
        )
        for error in errors
    )
    chapter["generation_status"] = "failed" if severe else "partial"
    chapter["generation_warnings"] = unique(list(chapter.get("generation_warnings") or []) + errors)


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, details: Any) -> None:
    checks.append({"check": name, "passed": passed, "details": details})


def candidate_person_tokens(prompt: dict[str, Any]) -> list[str]:
    tokens = []
    for item in list(prompt.get("chapter_task", {}).get("cautious_references") or []):
        title = str(item.get("title") or "")
        match = re.match(r"([\u4e00-\u9fff]{2,4})(?:人员简历|简历|证书)", title)
        if match:
            tokens.append(match.group(1))
    return unique(tokens)


def build_draft_statistics(chapters: list[dict[str, Any]], placeholders: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(item.get("generation_status") or "failed") for item in chapters)
    strategies = Counter(str(item.get("generation_strategy") or "not_applicable") for item in chapters)
    claims = [claim for chapter in chapters for claim in list(chapter.get("claims") or [])]
    claim_counts = Counter(str(item.get("claim_type") or "prohibited_claim") for item in claims)
    return {
        "chapter_count": len(chapters),
        "generation_status_counts": dict(sorted(statuses.items())),
        "generation_strategy_counts": dict(sorted(strategies.items())),
        "placeholder_count": len(placeholders),
        "claim_type_counts": dict(sorted(claim_counts.items())),
        "character_count": sum(chapter_character_count(item) for item in chapters),
    }


def build_generation_report(chapter_drafts: dict[str, Any]) -> dict[str, Any]:
    chapters = list(chapter_drafts.get("chapters") or [])
    claims = [claim for chapter in chapters for claim in list(chapter.get("claims") or [])]
    claim_counts = Counter(str(item.get("claim_type") or "prohibited_claim") for item in claims)
    status_counts = Counter(str(item.get("generation_status") or "failed") for item in chapters)
    requirements = [item for chapter in chapters for item in list(chapter.get("requirement_coverage") or [])]
    scores = [item for chapter in chapters for item in list(chapter.get("score_point_coverage") or [])]
    requirements_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in requirements:
        requirement_id = str(item.get("requirement_id") or "")
        if requirement_id:
            requirements_by_id.setdefault(requirement_id, []).append(item)
    unique_requirement_rows = [
        {
            "requirement_id": requirement_id,
            "covered": any(bool(item.get("responded")) for item in rows),
            "compliance_levels": unique([item.get("compliance_level") for item in rows]),
        }
        for requirement_id, rows in requirements_by_id.items()
    ]
    must_respond_rows = [item for item in unique_requirement_rows if "must_respond" in item["compliance_levels"]]
    fatal_rows = [item for item in unique_requirement_rows if "fatal_if_missing" in item["compliance_levels"]]
    prohibited_attempts = unique(
        [value for chapter in chapters for value in list((chapter.get("validation_results") or {}).get("prohibited_evidence_attempts") or [])]
    )
    unsupported = unique(
        [value for chapter in chapters for value in list((chapter.get("validation_results") or {}).get("unsupported_claim_ids") or [])]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": chapter_drafts.get("generated_at"),
        "lot_name": chapter_drafts.get("lot_name"),
        "generated_from": "chapter_drafts.json",
        "total_chapters": len(chapters),
        "generated_count": status_counts.get("generated", 0),
        "generated_with_placeholders_count": status_counts.get("generated_with_placeholders", 0),
        "partial_count": status_counts.get("partial", 0),
        "blocked_count": status_counts.get("blocked", 0),
        "failed_count": status_counts.get("failed", 0),
        "placeholder_count": len(list(chapter_drafts.get("global_placeholders") or [])),
        "tender_fact_claim_count": claim_counts.get("tender_fact", 0),
        "confirmed_enterprise_fact_claim_count": claim_counts.get("confirmed_enterprise_fact", 0),
        "proposal_statement_count": claim_counts.get("proposal_statement", 0),
        "commitment_statement_count": claim_counts.get("commitment_statement", 0),
        "cautious_reference_count": claim_counts.get("cautious_reference", 0),
        "prohibited_claim_count": claim_counts.get("prohibited_claim", 0),
        "requirements_total": len(unique_requirement_rows),
        "requirements_covered": len([item for item in unique_requirement_rows if item["covered"]]),
        "requirements_pending": len([item for item in unique_requirement_rows if not item["covered"]]),
        "requirement_reference_total": len(requirements),
        "requirement_reference_covered": len([item for item in requirements if item.get("responded")]),
        "requirement_reference_pending": len([item for item in requirements if not item.get("responded")]),
        "must_respond_total": len(must_respond_rows),
        "must_respond_covered": len([item for item in must_respond_rows if item["covered"]]),
        "must_respond_pending": len([item for item in must_respond_rows if not item["covered"]]),
        "fatal_requirement_total": len(fatal_rows),
        "fatal_requirement_covered": len([item for item in fatal_rows if item["covered"]]),
        "fatal_requirement_pending": len([item for item in fatal_rows if not item["covered"]]),
        "score_items_total": len(scores),
        "score_items_covered": len([item for item in scores if item.get("covered")]),
        "evidence_ids_used": unique([value for chapter in chapters for value in list(chapter.get("evidence_ids_used") or [])]),
        "prohibited_evidence_attempts": prohibited_attempts,
        "unsupported_claims_detected": unsupported,
        "chapter_results": [
            {
                "chapter_id": item.get("chapter_id"),
                "chapter_title": item.get("chapter_title"),
                "generation_status": item.get("generation_status"),
                "character_count": chapter_character_count(item),
                "placeholder_count": len(list(item.get("placeholders") or [])),
                "validation_passed": bool((item.get("validation_results") or {}).get("passed")),
                "errors": deepcopy((item.get("validation_results") or {}).get("errors") or []),
            }
            for item in chapters
        ],
        "build_warnings": deepcopy(chapter_drafts.get("global_warnings") or []),
    }


def build_draft_manifest(lot_name: str, chapter_drafts: dict[str, Any], generated_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "formal_source": "chapter_drafts.json",
        "draft_mode": chapter_drafts.get("draft_mode"),
        "chapter_files": [
            {
                "chapter_id": chapter.get("chapter_id"),
                "chapter_title": chapter.get("chapter_title"),
                "markdown_file": chapter_markdown_filename(chapter),
                "generation_status": chapter.get("generation_status"),
                "rendered_from": "chapter_drafts.json",
            }
            for chapter in list(chapter_drafts.get("chapters") or [])
        ],
        "combined_markdown_file": "technical_response_draft.md",
        "markdown_is_data_source": False,
        "formal_final": False,
    }


def update_context_draft_index(
    context: dict[str, Any], chapter_drafts: dict[str, Any], output_paths: dict[str, Path]
) -> None:
    context["chapter_drafts_path"] = str(output_paths["chapter_drafts"])
    context["draft_manifest_path"] = str(output_paths["draft_manifest"])
    context["combined_draft_path"] = str(output_paths["combined_draft"])
    draft_by_chapter = {
        str(item.get("chapter_id") or ""): item for item in list(chapter_drafts.get("chapters") or [])
    }
    for chapter in list(context.get("chapters") or []):
        draft = draft_by_chapter.get(str(chapter.get("chapter_id") or ""))
        chapter["chapter_draft_ids"] = [draft.get("chapter_id")] if draft else []
        chapter["chapter_generation_status"] = draft.get("generation_status") if draft else "skipped"
        chapter["unresolved_draft_placeholder_ids"] = [
            item.get("placeholder_id") for item in list((draft or {}).get("placeholders") or [])
        ]


def render_chapter_markdown(chapter: dict[str, Any]) -> str:
    lines = [
        f"# {chapter.get('chapter_order')}. {chapter.get('chapter_title')}",
        "",
        "> 自动生成测试草稿，包含待确认和待补充占位符，不得直接作为正式投标终稿。",
        "",
    ]
    if chapter.get("generation_status") in {"partial", "blocked", "failed"}:
        lines.extend(
            [
                "<!-- INTERNAL_REVIEW_START -->",
                f"生成状态：{chapter.get('generation_status')}；任务阻塞状态：{chapter.get('blocking_status')}。",
                *[f"- {item}" for item in list(chapter.get("generation_warnings") or [])],
                "<!-- INTERNAL_REVIEW_END -->",
                "",
            ]
        )
    for block in list(chapter.get("content_blocks") or []):
        lines.extend([f"## {block.get('heading')}", "", str(block.get("content") or ""), ""])
    return "\n".join(lines).rstrip() + "\n"


def render_combined_draft_markdown(chapter_drafts: dict[str, Any]) -> str:
    lines = [
        "# 技术响应测试草稿",
        "",
        "> 自动生成测试草稿，包含待确认和待补充占位符，不得直接作为正式投标终稿。",
        "",
        f"- draft_mode：`{chapter_drafts.get('draft_mode')}`",
        f"- 正式结构化来源：`chapter_drafts.json`",
        "",
    ]
    for chapter in sorted(list(chapter_drafts.get("chapters") or []), key=lambda item: item.get("chapter_order") or 0):
        lines.extend(render_chapter_markdown(chapter).splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_placeholder(placeholder: dict[str, Any]) -> str:
    display = str(placeholder.get("display_text") or "[待确认]").strip()
    placeholder_id = str(placeholder.get("placeholder_id") or "placeholder-unknown")
    if display.endswith("]"):
        display = display[:-1]
    return f"{display}｜{placeholder_id}]"


def validate_chapter_drafts_payload(payload: dict[str, Any]) -> None:
    if payload.get("draft_mode") not in DRAFT_MODES:
        raise ValueError(f"invalid draft_mode: {payload.get('draft_mode')}")
    if len(list(payload.get("chapters") or [])) != 9:
        raise ValueError("chapter_drafts must contain 9 chapters")
    for chapter in list(payload.get("chapters") or []):
        if chapter.get("generation_status") not in GENERATION_STATUSES:
            raise ValueError(f"invalid generation_status: {chapter.get('generation_status')}")
        if chapter.get("generation_strategy") not in GENERATION_STRATEGIES:
            raise ValueError(f"invalid generation_strategy: {chapter.get('generation_strategy')}")
        for block in list(chapter.get("content_blocks") or []):
            if block.get("block_type") not in BLOCK_TYPES:
                raise ValueError(f"invalid block_type: {block.get('block_type')}")
            if block.get("source_type") not in SOURCE_TYPES:
                raise ValueError(f"invalid source_type: {block.get('source_type')}")
        for claim in list(chapter.get("claims") or []):
            if claim.get("claim_type") not in CLAIM_TYPES:
                raise ValueError(f"invalid claim_type: {claim.get('claim_type')}")


def chapter_markdown_filename(chapter: dict[str, Any]) -> str:
    score_id = str((chapter.get("source_score_item_ids") or [""])[0])
    for prefix, filename in CHAPTER_FILES.items():
        if score_id.startswith(prefix):
            return filename
    return f"chapter_{chapter.get('chapter_order', 0):02d}.md"


def chapter_order_from_task(task: dict[str, Any]) -> int:
    chapter_id = str(task.get("chapter_id") or "")
    match = re.search(r"task_(\d+)_", chapter_id)
    if match:
        return max(1, int(match.group(1)) - 14)
    match = re.search(r"chapter-(\d+)", chapter_id)
    return int(match.group(1)) if match else 0


def chapter_character_count(chapter: dict[str, Any]) -> int:
    return sum(len(re.sub(r"\s+", "", str(item.get("content") or ""))) for item in list(chapter.get("content_blocks") or []))


def requirement_coverage_stats(chapter: dict[str, Any]) -> dict[str, int]:
    items = list(chapter.get("requirement_coverage") or [])
    return {
        "total": len(items),
        "covered": len([item for item in items if item.get("responded")]),
        "pending": len([item for item in items if not item.get("responded")]),
    }


def count_claims(chapter: dict[str, Any], claim_type: str) -> int:
    return len([item for item in list(chapter.get("claims") or []) if item.get("claim_type") == claim_type])


def first_sentence(text: str) -> str:
    value = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[。！？])", value, maxsplit=1)
    return parts[0] if parts else value


def unique_dicts(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = str(item.get(field) or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(deepcopy(item))
    return result

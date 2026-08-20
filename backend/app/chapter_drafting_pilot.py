from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.chapter_draft_schema import load_chapter_draft_schema
from app.chapter_drafting_agent import (
    build_chapter_prompts,
    drafting_input_paths,
    load_json,
    validate_drafting_inputs,
)
from app.llm_client import LLMClient, ModelCallResult, get_llm_client
from app.llm_errors import ModelConfigurationError, ModelError
from app.llm_profiles import LLMProfile, resolve_profile


SCHEMA_VERSION = "1.0"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONTENT_SCHEMA_PATH = BACKEND_ROOT / "agents" / "technical_writer" / "chapter_content_draft_schema.json"
OUTPUT_BUDGETS = {"short": 8000, "standard": 12000, "long": 16000}
NEUTRAL_PROJECT_TERMS = ["本项目", "采购项目", "区块链数据管理研究项目"]
CONFLICTING_PROJECT_TERMS = ["工业数据管理", "电力系统数据管理"]
ENTERPRISE_FACT_PATTERNS = [
    r"我(?:司|方)已(?:完成|实施|承建|获得|取得|拥有|具备)",
    r"我(?:司|方)现有",
    r"已有(?:项目|业绩|专利|证书|认证|产品)",
    r"成功实施(?:过|了)?",
    r"合同金额",
    r"中标通知书",
]


def build_technical_draft_pilot(
    lot_name: str,
    chapter_id: str,
    *,
    output_dir: str | Path = "outputs",
    draft_profile_name: str | None = None,
    normalize_profile_name: str | None = None,
    draft_client: LLMClient | None = None,
    normalize_client: LLMClient | None = None,
    require_deep_thinking: bool = True,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    lot_dir = Path(output_dir) / safe_name(lot_name)
    inputs = load_pilot_inputs(lot_name, lot_dir)
    prompt = select_chapter_prompt(inputs, chapter_id)
    task = prompt["chapter_task"]
    budget = chapter_budget(task)
    pilot_dir = lot_dir / "drafts" / "pilot" / "service_solution"
    pilot_dir.mkdir(parents=True, exist_ok=True)
    paths = pilot_paths(pilot_dir)
    remove_stale_success_outputs(paths)

    try:
        draft_client, draft_profile = prepare_draft_client(
            draft_client,
            profile_name=draft_profile_name,
            recommended_max_output_tokens=budget["recommended_max_output_tokens"],
        )
        if require_deep_thinking and thinking_mode(draft_profile) != "enabled":
            raise ModelConfigurationError(
                "drafting profile must enable thinking for the two-stage deep drafting pilot"
            )
        if (
            draft_profile.max_output_tokens is not None
            and draft_profile.max_output_tokens < budget["recommended_max_output_tokens"]
        ):
            budget["warnings"].append(
                "formal drafting profile output limit is below the chapter long-budget recommendation; the profile limit was preserved"
            )
    except ModelError as exc:
        return persist_preflight_failure(
            paths,
            lot_name=lot_name,
            task=task,
            budget=budget,
            generated_at=generated_at,
            error=exc,
        )

    deep_schema = load_content_schema()
    deep_prompt = build_deep_prompt(prompt, budget)
    try:
        deep_call = draft_client.generate_structured(
            system_prompt=deep_system_prompt(),
            user_prompt=json.dumps(deep_prompt, ensure_ascii=False, separators=(",", ":")),
            response_schema=deep_schema,
            request_metadata=pilot_metadata(lot_name, task, "deep_draft"),
            profile_name=draft_profile.name,
        )
        deep_content = deep_call.parsed_output
        deep_errors = validate_deep_content(deep_content, prompt)
        if deep_errors:
            raise ModelConfigurationError("deep draft contract failed: " + "; ".join(deep_errors))
    except ModelError as exc:
        return persist_deep_failure(
            paths,
            lot_name=lot_name,
            task=task,
            budget=budget,
            generated_at=generated_at,
            profile=draft_profile,
            error=exc,
        )

    deep_result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_title": task.get("chapter_title"),
        "generation_status": "generated",
        "failed_stage": None,
        "output_budget_class": budget["output_budget_class"],
        "recommended_max_output_tokens": budget["recommended_max_output_tokens"],
        "effective_max_output_tokens": draft_profile.max_output_tokens,
        "call_metadata": call_metadata(deep_call, "drafting", draft_profile),
        "content_draft": deep_content,
        "warnings": budget["warnings"],
    }
    write_json(paths["deep_draft_result"], deep_result)

    normalize_profile: LLMProfile | None = None
    try:
        normalize_client, normalize_profile = prepare_normalize_client(
            normalize_client,
            profile_name=normalize_profile_name,
        )
        if thinking_mode(normalize_profile) != "disabled":
            raise ModelConfigurationError(
                "fast_task profile must disable thinking for pilot normalization"
            )
        normalize_prompt = build_normalize_prompt(prompt, deep_content)
        normalize_call = normalize_client.generate_structured(
            system_prompt=normalize_system_prompt(),
            user_prompt=json.dumps(normalize_prompt, ensure_ascii=False, separators=(",", ":")),
            response_schema=load_chapter_draft_schema(),
            request_metadata=pilot_metadata(lot_name, task, "normalize"),
            profile_name=normalize_profile.name,
        )
        normalized = normalize_call.parsed_output
        fidelity = validate_content_fidelity(deep_content, normalized, prompt)
        final_status = fidelity_status(fidelity)
    except ModelError as exc:
        return persist_normalize_failure(
            paths,
            lot_name=lot_name,
            task=task,
            budget=budget,
            generated_at=generated_at,
            deep_result=deep_result,
            draft_profile=draft_profile,
            normalize_profile=normalize_profile,
            error=exc,
        )

    schema_valid = not list(Draft202012Validator(load_chapter_draft_schema()).iter_errors(normalized))
    if not schema_valid:
        fidelity["errors"].append("normalized chapter failed local formal schema validation")
        final_status = "failed"
    report = build_report(
        lot_name=lot_name,
        task=task,
        generated_at=generated_at,
        status=final_status,
        budget=budget,
        deep_result=deep_result,
        normalize_call=normalize_call,
        normalize_profile=normalize_profile,
        normalized=normalized,
        fidelity=fidelity,
        schema_valid=schema_valid,
        paths=paths,
    )
    manifest = build_manifest(
        lot_name,
        task,
        generated_at,
        final_status,
        budget,
        draft_profile,
        normalize_profile,
        paths,
    )
    if final_status == "success":
        write_json(paths["normalized_chapter_draft"], normalized)
        paths["preview"].write_text(render_preview(task, normalized, report), encoding="utf-8")
    write_json(paths["pilot_generation_report"], report)
    write_json(paths["pilot_manifest"], manifest)
    return pilot_result(manifest, report, paths)


def inspect_technical_draft_pilot(lot_name: str, *, output_dir: str | Path = "outputs") -> dict[str, Any]:
    pilot_dir = Path(output_dir) / safe_name(lot_name) / "drafts" / "pilot" / "service_solution"
    paths = pilot_paths(pilot_dir)
    manifest = load_json(paths["pilot_manifest"])
    report = load_json(paths["pilot_generation_report"])
    deep = load_json(paths["deep_draft_result"]) if paths["deep_draft_result"].exists() else {}
    return {
        "lot_name": lot_name,
        "chapter_id": manifest.get("chapter_id"),
        "task_id": manifest.get("task_id"),
        "chapter_title": manifest.get("chapter_title"),
        "pilot_status": manifest.get("pilot_status"),
        "deep_stage": {
            "profile": (manifest.get("profiles") or {}).get("drafting"),
            "call": deep.get("call_metadata") or report.get("deep_call") or {},
            "content_character_count": report.get("deep_content_character_count", 0),
        },
        "normalize_stage": {
            "profile": (manifest.get("profiles") or {}).get("fast_task"),
            "call": report.get("normalize_call") or {},
            "schema_valid": report.get("schema_valid", False),
        },
        "placeholder_preservation": report.get("placeholder_preservation") or {},
        "score_item_coverage": report.get("score_item_coverage") or {},
        "requirement_coverage": report.get("requirement_coverage") or {},
        "unsupported_claim_count": report.get("unsupported_claim_count", 0),
        "content_fidelity": report.get("content_fidelity") or {},
        "files": {key: str(value) for key, value in paths.items() if value.exists()},
    }


def load_pilot_inputs(lot_name: str, lot_dir: Path) -> dict[str, dict[str, Any]]:
    paths = drafting_input_paths(lot_dir)
    inputs = {name: load_json(path) for name, path in paths.items()}
    validate_drafting_inputs(lot_name, inputs)
    return inputs


def select_chapter_prompt(inputs: dict[str, dict[str, Any]], chapter_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in build_chapter_prompts(inputs)
        if str((item.get("chapter_task") or {}).get("chapter_id") or "") == chapter_id
    ]
    if len(matches) != 1:
        raise ValueError(f"chapter_id must identify exactly one task: {chapter_id}")
    return matches[0]


def chapter_budget(task: dict[str, Any]) -> dict[str, Any]:
    title = str(task.get("chapter_title") or "")
    budget_class = "long" if "服务方案" in title else "standard"
    return {
        "output_budget_class": budget_class,
        "recommended_max_output_tokens": OUTPUT_BUDGETS[budget_class],
        "warnings": [],
    }


def prepare_draft_client(
    client: LLMClient | None,
    *,
    profile_name: str | None,
    recommended_max_output_tokens: int,
) -> tuple[LLMClient, LLMProfile]:
    if client is not None:
        profile = require_profile(client, "drafting")
        return client, profile
    profile = resolve_profile(role="drafting", profile_name=profile_name)
    profile_limit = profile.max_output_tokens
    effective = min(profile_limit, recommended_max_output_tokens) if profile_limit is not None else recommended_max_output_tokens
    overrides = {"max_output_tokens": effective, "send_max_output_tokens": True}
    client = get_llm_client(
        role="drafting",
        profile_name=profile_name,
        runtime_profile_overrides=overrides,
        schema_repair_enabled=False,
    )
    return client, require_profile(client, "drafting")


def prepare_normalize_client(
    client: LLMClient | None,
    *,
    profile_name: str | None,
) -> tuple[LLMClient, LLMProfile]:
    if client is not None:
        profile = require_profile(client, "fast_task")
        return client, profile
    profile = resolve_profile(role="fast_task", profile_name=profile_name)
    extra_body = deepcopy(profile.extra_body)
    extra_body["thinking"] = {"type": "disabled"}
    client = get_llm_client(
        role="fast_task",
        profile_name=profile_name,
        runtime_profile_overrides={
            "send_thinking": True,
            "extra_body": extra_body,
        },
    )
    return client, require_profile(client, "fast_task")


def require_profile(client: LLMClient, role: str) -> LLMProfile:
    profile = getattr(client, "profile", None)
    if profile is None:
        raise ModelConfigurationError(f"{role} pilot client must expose its resolved profile")
    if getattr(client, "is_mock", False) or profile.provider == "mock":
        raise ModelConfigurationError("MockLLMClient cannot be used by the real drafting pilot")
    return profile


def load_content_schema() -> dict[str, Any]:
    value = json.loads(CONTENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def build_deep_prompt(prompt: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    task = prompt["chapter_task"]
    return {
        "chapter_identity": {
            "chapter_id": task.get("chapter_id"),
            "task_id": task.get("task_id"),
            "chapter_title": task.get("chapter_title"),
            "task_type": task.get("task_type"),
        },
        "output_budget_class": budget["output_budget_class"],
        "scoring_targets": prompt.get("scoring_targets") or [],
        "must_write_points": prompt.get("must_write_points") or [],
        "mandatory_requirements": prompt.get("mandatory_requirements") or [],
        "technical_requirements": prompt.get("technical_requirements") or [],
        "proposal_content_allowed": prompt.get("proposal_content_allowed") or [],
        "chapter_guardrails": prompt.get("chapter_guardrails") or {},
        "claim_boundaries": prompt.get("claim_boundaries") or {},
        "placeholders": prompt.get("placeholders") or [],
        "confirmed_facts": prompt.get("confirmed_facts") or [],
        "prohibited_evidence_ids": prompt.get("prohibited_evidence_ids") or [],
        "project_conflicts": prompt.get("project_conflicts") or [],
        "neutral_project_terms": NEUTRAL_PROJECT_TERMS,
        "required_service_topics": [
            "服务组织",
            "服务流程",
            "沟通汇报",
            "质量控制",
            "风险管理",
            "安全与保密",
            "交付和验收支持",
        ],
    }


def deep_system_prompt() -> str:
    return (
        "你是技术响应章节的深度撰写模型。专注形成高质量方案内容，不承担最终 claims、block ID 或 manifest 整理。"
        "只使用输入中的评分目标、要求、技术规范、允许方案内容和 guardrails。企业事实只能来自 confirmed_facts；为空时不得"
        "编造企业业绩、人员、专利、证书、成熟产品或既有国网经验。使用‘本项目拟’‘我方将’‘本方案将建立’等方案性表达。"
        "项目名称冲突未解决时只使用‘本项目’‘采购项目’或‘区块链数据管理研究项目’。严格输出轻量章节内容 Schema JSON。"
    )


def build_normalize_prompt(prompt: dict[str, Any], deep_content: dict[str, Any]) -> dict[str, Any]:
    task = prompt["chapter_task"]
    return {
        "operation": "normalize_only",
        "chapter_identity": {
            "chapter_id": task.get("chapter_id"),
            "task_id": task.get("task_id"),
            "chapter_title": task.get("chapter_title"),
        },
        "lightweight_content_draft": deep_content,
        "required_score_item_ids": list((task.get("input_context") or {}).get("score_item_ids") or []),
        "required_requirement_ids": list((task.get("input_context") or {}).get("requirement_ids") or []),
        "required_placeholder_ids": [
            str(item.get("placeholder_id") or "") for item in list(prompt.get("placeholders") or []) if item.get("placeholder_id")
        ],
        "chapter_guardrails": prompt.get("chapter_guardrails") or {},
        "normalization_rules": [
            "保留第一段每个 section 的 heading 和 content，不新增实质性方案段落。",
            "claims 必须是第一段正文中的原文摘录或占位符。",
            "不得增加企业事实、人员、客户、合同、专利、证书、业绩或项目经历。",
            "不得删除 placeholder、score item 或 requirement ID。",
            "不得更换项目名称，不得声称满足满分条件。",
        ],
    }


def normalize_system_prompt() -> str:
    return (
        "你是非深度思考的结构化整理模型。只能整理输入中的轻量章节正文，禁止新增或改写核心方案含义。"
        "将 sections 映射到正式 block_type，抽取原文 claims，并使用 proposal_statement、commitment_statement 或 placeholder。"
        "不得生成 confirmed_enterprise_fact，不得删除稳定 ID。严格输出正式章节 Schema JSON。"
    )


def validate_deep_content(content: dict[str, Any], prompt: dict[str, Any]) -> list[str]:
    task = prompt["chapter_task"]
    required_scores = set((task.get("input_context") or {}).get("score_item_ids") or [])
    required_requirements = set((task.get("input_context") or {}).get("requirement_ids") or [])
    required_placeholders = {
        str(item.get("placeholder_id") or "") for item in list(prompt.get("placeholders") or []) if item.get("placeholder_id")
    }
    errors = []
    if str(content.get("chapter_title") or "") != str(task.get("chapter_title") or ""):
        errors.append("deep draft changed chapter title")
    if not required_scores.issubset(set(content.get("score_items_covered") or [])):
        errors.append("deep draft omitted required score_item_id")
    if not required_requirements.issubset(set(content.get("requirements_covered") or [])):
        errors.append("deep draft omitted required requirement_id")
    if not required_placeholders.issubset(set(content.get("placeholders_used") or [])):
        errors.append("deep draft omitted required placeholder_id")
    text = combined_content_text(content)
    if contains_forbidden_enterprise_fact(text):
        errors.append("deep draft contains unsupported enterprise fact language")
    if contains_conflicting_project_selection(text):
        errors.append("deep draft selected an unresolved project name")
    return errors


def validate_content_fidelity(
    deep: dict[str, Any],
    normalized: dict[str, Any],
    prompt: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    deep_sections = list(deep.get("sections") or [])
    normalized_sections = list(normalized.get("sections") or [])
    normalized_texts = {normalize_text(item.get("content")) for item in normalized_sections}
    missing_sections = [
        str(item.get("heading") or "")
        for item in deep_sections
        if normalize_text(item.get("content")) not in normalized_texts
    ]
    if missing_sections:
        errors.append(f"normalized draft omitted or altered deep sections: {missing_sections}")
    deep_placeholders = set(deep.get("placeholders_used") or [])
    normalized_placeholders = set(normalized.get("placeholders_used") or [])
    missing_placeholders = sorted(deep_placeholders - normalized_placeholders)
    if missing_placeholders:
        errors.append(f"normalized draft removed placeholders: {missing_placeholders}")
    deep_scores = set(deep.get("score_items_covered") or [])
    normalized_scores = set(normalized.get("score_items_covered") or [])
    missing_scores = sorted(deep_scores - normalized_scores)
    if missing_scores:
        errors.append(f"normalized draft removed score items: {missing_scores}")
    deep_requirements = set(deep.get("requirements_covered") or [])
    normalized_requirements = set(normalized.get("requirements_covered") or [])
    missing_requirements = sorted(deep_requirements - normalized_requirements)
    if missing_requirements:
        errors.append(f"normalized draft removed requirements: {missing_requirements}")

    deep_text = combined_content_text(deep)
    deep_claim_source = deep_claim_source_text(deep)
    normalized_text = combined_content_text(normalized)
    added_enterprise_facts = []
    for pattern in ENTERPRISE_FACT_PATTERNS:
        if re.search(pattern, normalized_text) and not re.search(pattern, deep_text):
            added_enterprise_facts.append(pattern)
    if added_enterprise_facts:
        errors.append("normalized draft introduced enterprise fact language")
    if contains_conflicting_project_selection(normalized_text) and not contains_conflicting_project_selection(deep_text):
        errors.append("normalized draft introduced an unresolved project name")

    unsupported_claims = []
    for claim in list(normalized.get("claims") or []):
        claim_type = str(claim.get("claim_type") or "")
        claim_text = normalize_text(claim.get("text"))
        if claim_type == "confirmed_enterprise_fact" or list(claim.get("evidence_ids") or []):
            unsupported_claims.append(claim_text or claim_type)
        elif claim_type != "placeholder" and claim_text and claim_text not in normalize_text(deep_claim_source):
            unsupported_claims.append(claim_text)
    if unsupported_claims:
        errors.append("normalized draft introduced unsupported claims")

    deep_length = len(normalize_text(deep_text))
    normalized_length = len(normalize_text(normalized_text))
    ratio = normalized_length / deep_length if deep_length else 0.0
    if deep_length and not 0.8 <= ratio <= 1.2:
        errors.append(f"normalized content length ratio is abnormal: {ratio:.3f}")
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "deep_section_count": len(deep_sections),
        "normalized_section_count": len(normalized_sections),
        "missing_section_headings": missing_sections,
        "missing_placeholder_ids": missing_placeholders,
        "missing_score_item_ids": missing_scores,
        "missing_requirement_ids": missing_requirements,
        "unsupported_claims": unsupported_claims,
        "deep_character_count": deep_length,
        "normalized_character_count": normalized_length,
        "content_length_ratio": ratio,
    }


def fidelity_status(fidelity: dict[str, Any]) -> str:
    if fidelity.get("passed"):
        return "success"
    severe = any(
        term in str(error)
        for error in list(fidelity.get("errors") or [])
        for term in ["enterprise fact", "unsupported claims", "unresolved project name"]
    )
    return "failed" if severe else "partial"


def call_metadata(call: ModelCallResult, role: str, profile: LLMProfile) -> dict[str, Any]:
    usage = call.usage or {}
    return {
        "role": role,
        "profile_name": profile.name,
        "provider": call.provider,
        "model": call.model,
        "thinking_mode": thinking_mode(profile),
        "timeout_seconds": profile.timeout_seconds,
        "max_output_tokens": profile.max_output_tokens,
        "request_id": call.request_id,
        "duration_ms": call.duration_ms,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "answer_tokens": usage.get("answer_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "finish_status": call.finish_status,
        "incomplete_details": call.incomplete_details,
        "repair_attempted": call.repair_attempted,
        "schema_valid": True,
    }


def error_call_metadata(error: ModelError, role: str, profile: LLMProfile | None) -> dict[str, Any]:
    diagnostics = error.safe_diagnostics()
    usage = diagnostics.get("usage") or {}
    return {
        "role": role,
        "profile_name": profile.name if profile else None,
        "provider": profile.provider if profile else None,
        "model": profile.model if profile else None,
        "thinking_mode": thinking_mode(profile) if profile else None,
        "timeout_seconds": profile.timeout_seconds if profile else None,
        "max_output_tokens": profile.max_output_tokens if profile else None,
        "request_id": diagnostics.get("request_id"),
        "duration_ms": diagnostics.get("provider_request_ms"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "answer_tokens": usage.get("answer_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "finish_status": diagnostics.get("finish_status"),
        "incomplete_details": diagnostics.get("incomplete_details"),
        "repair_attempted": bool(diagnostics.get("repair_attempted")),
        "schema_valid": False,
        "response_received": bool(diagnostics.get("response_received")),
        "output_text_available": bool(diagnostics.get("output_text_available")),
    }


def thinking_mode(profile: LLMProfile | None) -> str:
    if profile is None or not profile.send_thinking:
        return "not_sent"
    value = (profile.extra_body or {}).get("thinking")
    return str(value.get("type") or "configured") if isinstance(value, dict) else str(value or "configured")


def profile_summary(profile: LLMProfile | None, role: str) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "role": role,
        "profile_name": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "thinking_mode": thinking_mode(profile),
        "timeout_seconds": profile.timeout_seconds,
        "max_output_tokens": profile.max_output_tokens,
        "max_retries": profile.max_retries,
    }


def build_report(
    *,
    lot_name: str,
    task: dict[str, Any],
    generated_at: str,
    status: str,
    budget: dict[str, Any],
    deep_result: dict[str, Any],
    normalize_call: ModelCallResult,
    normalize_profile: LLMProfile,
    normalized: dict[str, Any],
    fidelity: dict[str, Any],
    schema_valid: bool,
    paths: dict[str, Path],
) -> dict[str, Any]:
    required_placeholders = {
        str(item.get("placeholder_id") or "") for item in list(task.get("placeholders") or []) if item.get("placeholder_id")
    }
    normalized_placeholders = set(normalized.get("placeholders_used") or [])
    required_scores = set((task.get("input_context") or {}).get("score_item_ids") or [])
    normalized_scores = set(normalized.get("score_items_covered") or [])
    required_requirements = set((task.get("input_context") or {}).get("requirement_ids") or [])
    normalized_requirements = set(normalized.get("requirements_covered") or [])
    deep_call = deep_result.get("call_metadata") or {}
    normalize_meta = call_metadata(normalize_call, "fast_task", normalize_profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_title": task.get("chapter_title"),
        "pilot_status": status,
        "schema_valid": schema_valid,
        "output_budget": budget,
        "deep_call": deep_call,
        "normalize_call": normalize_meta,
        "total_duration_ms": sum(int(item.get("duration_ms") or 0) for item in [deep_call, normalize_meta]),
        "deep_content_character_count": len(normalize_text(combined_content_text(deep_result.get("content_draft") or {}))),
        "placeholder_preservation": {
            "required_ids": sorted(required_placeholders),
            "preserved_ids": sorted(required_placeholders & normalized_placeholders),
            "missing_ids": sorted(required_placeholders - normalized_placeholders),
            "passed": required_placeholders.issubset(normalized_placeholders),
        },
        "score_item_coverage": {
            "required_ids": sorted(required_scores),
            "covered_ids": sorted(required_scores & normalized_scores),
            "missing_ids": sorted(required_scores - normalized_scores),
            "passed": required_scores.issubset(normalized_scores),
        },
        "requirement_coverage": {
            "required_count": len(required_requirements),
            "covered_count": len(required_requirements & normalized_requirements),
            "missing_ids": sorted(required_requirements - normalized_requirements),
            "passed": required_requirements.issubset(normalized_requirements),
        },
        "confirmed_enterprise_fact_count": sum(
            1 for item in list(normalized.get("claims") or []) if item.get("claim_type") == "confirmed_enterprise_fact"
        ),
        "unsupported_claim_count": len(fidelity.get("unsupported_claims") or []),
        "content_fidelity": fidelity,
        "enterprise_fact_fabrication_detected": any("enterprise fact" in str(item) for item in fidelity.get("errors") or []),
        "project_name_conflict_misselection_detected": any(
            "project name" in str(item) for item in fidelity.get("errors") or []
        ),
        "preview_generated": status == "success",
        "files": {key: str(value) for key, value in paths.items()},
        "build_warnings": budget.get("warnings") or [],
    }


def build_manifest(
    lot_name: str,
    task: dict[str, Any],
    generated_at: str,
    status: str,
    budget: dict[str, Any],
    draft_profile: LLMProfile | None,
    normalize_profile: LLMProfile | None,
    paths: dict[str, Path],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_title": task.get("chapter_title"),
        "pilot_status": status,
        "formal_final": False,
        "output_budget": budget,
        "profiles": {
            "drafting": profile_summary(draft_profile, "drafting"),
            "fast_task": profile_summary(normalize_profile, "fast_task"),
        },
        "generated_from": {
            "chapter_task_packets": "writing/chapter_task_packets.json",
            "writing_context_pack": "writing/writing_context_pack.json",
            "evidence_use_map": "writing/evidence_use_map.json",
            "draft_guardrails": "writing/draft_guardrails.json",
        },
        "files": {key: str(value) for key, value in paths.items()},
    }


def persist_preflight_failure(
    paths: dict[str, Path],
    *,
    lot_name: str,
    task: dict[str, Any],
    budget: dict[str, Any],
    generated_at: str,
    error: ModelError,
) -> dict[str, Any]:
    deep_result = failure_result(lot_name, task, generated_at, "profile_preflight", error, None)
    return persist_failure_bundle(paths, lot_name, task, budget, generated_at, deep_result, None, None)


def persist_deep_failure(
    paths: dict[str, Path],
    *,
    lot_name: str,
    task: dict[str, Any],
    budget: dict[str, Any],
    generated_at: str,
    profile: LLMProfile,
    error: ModelError,
) -> dict[str, Any]:
    deep_result = failure_result(lot_name, task, generated_at, "deep_draft_output", error, profile)
    deep_result["model_failed_stage"] = error.safe_diagnostics().get("failed_stage")
    return persist_failure_bundle(paths, lot_name, task, budget, generated_at, deep_result, profile, None)


def persist_normalize_failure(
    paths: dict[str, Path],
    *,
    lot_name: str,
    task: dict[str, Any],
    budget: dict[str, Any],
    generated_at: str,
    deep_result: dict[str, Any],
    draft_profile: LLMProfile,
    normalize_profile: LLMProfile | None,
    error: ModelError,
) -> dict[str, Any]:
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_title": task.get("chapter_title"),
        "pilot_status": "failed",
        "failed_stage": "normalize_output",
        "error_type": error.__class__.__name__,
        "error": str(error),
        "deep_call": deep_result.get("call_metadata") or {},
        "normalize_call": error_call_metadata(error, "fast_task", normalize_profile),
        "schema_valid": False,
        "preview_generated": False,
        "build_warnings": [],
    }
    manifest = build_manifest(
        lot_name, task, generated_at, "failed", budget, draft_profile, normalize_profile, paths
    )
    write_json(paths["pilot_generation_report"], report)
    write_json(paths["pilot_manifest"], manifest)
    return pilot_result(manifest, report, paths)


def persist_failure_bundle(
    paths: dict[str, Path],
    lot_name: str,
    task: dict[str, Any],
    budget: dict[str, Any],
    generated_at: str,
    deep_result: dict[str, Any],
    draft_profile: LLMProfile | None,
    normalize_profile: LLMProfile | None,
) -> dict[str, Any]:
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_title": task.get("chapter_title"),
        "pilot_status": "failed",
        "failed_stage": deep_result.get("failed_stage"),
        "error_type": deep_result.get("error_type"),
        "error": deep_result.get("error"),
        "deep_call": deep_result.get("call_metadata") or {},
        "normalize_call": None,
        "schema_valid": False,
        "preview_generated": False,
        "build_warnings": budget.get("warnings") or [],
    }
    manifest = build_manifest(
        lot_name, task, generated_at, "failed", budget, draft_profile, normalize_profile, paths
    )
    write_json(paths["deep_draft_result"], deep_result)
    write_json(paths["pilot_generation_report"], report)
    write_json(paths["pilot_manifest"], manifest)
    return pilot_result(manifest, report, paths)


def failure_result(
    lot_name: str,
    task: dict[str, Any],
    generated_at: str,
    failed_stage: str,
    error: ModelError,
    profile: LLMProfile | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "lot_name": lot_name,
        "chapter_id": task.get("chapter_id"),
        "task_id": task.get("task_id"),
        "chapter_title": task.get("chapter_title"),
        "generation_status": "failed",
        "failed_stage": failed_stage,
        "error_type": error.__class__.__name__,
        "error": str(error),
        "call_metadata": error_call_metadata(error, "drafting", profile),
        "content_draft": None,
        "warnings": error.safe_diagnostics().get("warnings") or [],
    }


def render_preview(task: dict[str, Any], normalized: dict[str, Any], report: dict[str, Any]) -> str:
    lines = [
        "# 自动生成单章 Pilot 预览",
        "",
        "> 本文件仅用于两段式撰写链路测试，不得直接作为正式投标终稿。",
        "",
        f"## {task.get('chapter_title')}",
        "",
    ]
    for section in list(normalized.get("sections") or []):
        lines.extend([f"### {section.get('heading')}", "", str(section.get("content") or ""), ""])
    placeholders = list(normalized.get("placeholders_used") or [])
    if placeholders:
        lines.extend(["## 内部占位符", "", *[f"- `{value}`" for value in placeholders], ""])
    lines.extend(
        [
            "## Pilot 校验",
            "",
            f"- Schema：{'通过' if report.get('schema_valid') else '未通过'}",
            f"- 内容保真：{'通过' if (report.get('content_fidelity') or {}).get('passed') else '未通过'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def pilot_paths(pilot_dir: Path) -> dict[str, Path]:
    return {
        "pilot_manifest": pilot_dir / "pilot_manifest.json",
        "deep_draft_result": pilot_dir / "deep_draft_result.json",
        "normalized_chapter_draft": pilot_dir / "normalized_chapter_draft.json",
        "preview": pilot_dir / "service_solution_preview.md",
        "pilot_generation_report": pilot_dir / "pilot_generation_report.json",
    }


def remove_stale_success_outputs(paths: dict[str, Path]) -> None:
    for key in ("normalized_chapter_draft", "preview"):
        path = paths[key]
        if path.exists():
            path.unlink()


def pilot_result(manifest: dict[str, Any], report: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "lot_name": manifest.get("lot_name"),
        "chapter_id": manifest.get("chapter_id"),
        "task_id": manifest.get("task_id"),
        "chapter_title": manifest.get("chapter_title"),
        "pilot_status": manifest.get("pilot_status"),
        "report": report,
        "files": {key: str(value) for key, value in paths.items() if value.exists()},
    }


def pilot_metadata(lot_name: str, task: dict[str, Any], stage: str) -> dict[str, Any]:
    return {
        "purpose": "technical_draft_pilot",
        "stage": stage,
        "lot_name": lot_name,
        "chapter_id": str(task.get("chapter_id") or ""),
        "task_id": str(task.get("task_id") or ""),
        "chapter_title": str(task.get("chapter_title") or ""),
    }


def combined_content_text(value: dict[str, Any]) -> str:
    return "\n".join(str(item.get("content") or "") for item in list(value.get("sections") or []))


def deep_claim_source_text(value: dict[str, Any]) -> str:
    parts = [combined_content_text(value)]
    parts.extend(str(item) for item in list(value.get("proposal_commitments") or []))
    parts.extend(str(item) for item in list(value.get("placeholders_used") or []))
    return "\n".join(part for part in parts if part)


def contains_forbidden_enterprise_fact(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in ENTERPRISE_FACT_PATTERNS)


def contains_conflicting_project_selection(text: str) -> bool:
    return any(term in text for term in CONFLICTING_PROJECT_TERMS)


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]", "_", value)

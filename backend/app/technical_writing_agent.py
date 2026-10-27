from __future__ import annotations

import json
import hashlib
import os
import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.candidate_section_canonicalizer import (
    CandidateCanonicalizationError,
    canonicalize_agent_candidate,
)
from app.llm_client import LLMClient, empty_usage, get_llm_client
from app.llm_errors import ModelError, ModelJsonParseError, ModelOutputIncompleteError
from app.metadata_store import MetadataStore
from app.openai_compatible_llm_client import (
    parse_json_object,
    parse_model_tool_arguments,
    suspected_json_truncation,
)
from app.technical_agent_events import TechnicalAgentEventEmitter
from app.technical_placeholders import (
    PLACEHOLDER_ID_PATTERN,
    PLACEHOLDER_TOKEN_PATTERN,
    extract_placeholder_ids,
    extract_placeholder_references,
)
from app.technical_tool_registry import TechnicalToolRegistry, ToolExecutionError
from app.technical_user_instruction import (
    classify_revision_scope,
    detect_instruction_conflicts,
    sanitize_user_instruction,
)
from app.technical_writing_quality import (
    _is_generic_project_role_duty,
    _is_neutral_team_configuration_statement,
    audit_generation_permissions,
    build_rule_application_plan,
    classify_deviation_status,
    confirmation_baselines_from_generation_permissions,
    confirmation_requirement,
    extract_tender_obligation_items,
    generation_permission_audit_errors,
    inspect_deviation_issues,
    inspect_unconfirmed_commitments,
    inspect_unverified_standard_references,
    inspect_section_composition,
    review_chapter_quality,
    section_quality_floor,
    tender_response_policy,
)
from app.technical_writing_rules import (
    WritingRules,
    load_project_constraints,
    load_writing_rules,
    normalize_persistent_constraint,
    persist_confirmed_constraint,
    suggest_instruction_scope,
)
from app.technical_writing_tools import (
    TechnicalWritingToolContext,
    build_technical_tool_registry,
)
from app.web_search_provider import get_web_search_provider


SYSTEM_PROMPT = """你是技术标章节写作 Agent。你必须自主按需调用工具，不得假定工具调用顺序。
优先级依次为：程序安全、输出Schema和硬校验；招标硬性要求、评分办法和技术规格书；
已确认项目事实；用户本轮意见；当前项目持续约束；版本化长期写作规则；Agent默认习惯。
冲突意见不得执行，必须保留招标要求并告警。
事实边界：
1. confirmed_fact 和带 confirmed evidence ID 的 selected knowledge 可以按确认值写入；不得改写或扩大。
1a. Runtime提供的confirmed_business_choices是独立写作约束：true必须执行肯定约束，false必须执行否定约束，
selection只能使用已确认选项，text只能按已确认指令写；pending_confirmations只能使用Runtime登记的稳定占位符，
不得忽略、猜测或改写为项目事实、证明材料或评分已满足结论。
2. 招标明确的商务、服务、工期、交付、培训、验收和履约基准，默认按原值响应并标记无偏离；
产品功能、参数、人员、资质、业绩等客观证明事项不能仅凭无偏离意向写成已确认事实。
3. local knowledge 返回的 candidate 不等于 confirmed，不得自动选择人员、业绩或资质。
4. 网页不能补全本公司人员、业绩、资质、项目团队、服务承诺、实际品牌型号、实际配置或技术偏离。
5. 未确认品牌、型号、人员、实际参数以及招标未提供基准的进度和承诺必须调用 record_pending_confirmation；
每个调用项只能描述一个field，正文使用Runtime返回的{{placeholder_id}}，不得自行创造或组合占位符。
6. 优于招标基准的正偏离必须先确认；劣于招标基准的负偏离必须显著标记且不得伪装成无偏离。
7. 章节级chapter_allowed_*与每节generation_permissions是封闭许可清单：确定事实、
承诺型数字、人员资历、产品能力和占位符只能取自相应allowed_*列表；
未列出的内容必须不写。不得为了正文完整、专业性或行业惯例补写任何未确认事实。
无招标人员要求、confirmed人员事实或confirmed人员材料时，只允许不涉及具体人选、
资历、数量和资源的中性项目角色动作与协作原则；不得生成备份人员、替换机制或公司资源安排。
8. 缺少企业材料或外部来源时也必须登记独立待确认field，不得补造事实。
9. 禁止读取历史 reference_only 文件，禁止虚构来源、页码、证书编号或有效期。
10. 必须保留目标写作大纲全部标题，响应不能只写“满足/符合”。
11. 最终章节对象字段必须为 chapter_title、sections、claims、placeholders_used、
score_items_covered、requirements_covered、warnings；每个 section 必须使用 heading、
block_type、content、covered_score_item_ids、covered_requirement_ids、placeholder_ids。
12. save_section_draft 不能替代最终结构化响应；最终只输出符合章节 JSON Schema 的 JSON 对象，
不得返回 chapter_id、title、summary、draft_status、source_usage 或 saved_path 等运行摘要字段。
13. 通用长期写作规则由 Runtime 从版本化规则文件注入；招标文件不存在对应表格时才可自行设计。
14. 结构性修改和局部事实更新都必须返回完整章节对象并执行完整校验，
不得通过对Markdown做字符串拼接或正则替换冒充重写。
工具由运行时实施权限校验；被拒绝后不得绕过。"""


PENDING_CONFIRMATION_ATOMIC_FIELD_RULES = """[Pending Confirmation Atomic Field Rules]
record_pending_confirmation 每次只能记录一个原子待确认字段。
field 中不得使用“与”“及”“和”“以及”等方式合并多个独立待确认事实。
如果一个问题涉及多个可以独立回答的事实，必须拆成多个独立工具调用。
工具返回“one pending confirmation item must describe exactly one field”后，不得原样重复或换一种复合表述再次提交。

错误示例：field = "产品品牌型号与制造商绿色供应链实践"
正确拆分：
field = "所投产品品牌"
field = "所投产品型号"
field = "所投产品制造商"
field = "制造商绿色供应链管理实践"

错误示例：field = "所投产品节能认证与环境标志认证"
正确拆分：
field = "所投产品节能产品认证"
field = "所投产品环境标志产品认证"
"""


RECOVERABLE_AUXILIARY_TOOL_NAMES = frozenset({
    "get_writing_scope",
    "get_response_requirements",
    "get_task_information",
    "get_resolved_task_context",
    "get_draft_plan",
    "get_confirmed_project_facts",
    "query_local_knowledge",
    "read_local_evidence",
    "web_search",
    "open_web_page",
    "record_pending_confirmation",
    "load_existing_draft",
})
RECOVERABLE_TOOL_ARGUMENT_ERROR_THRESHOLD = 3


CHAPTER_BLOCK_TYPES = {
    "introduction",
    "requirement_response",
    "technical_solution",
    "implementation_plan",
    "organization_plan",
    "quality_assurance",
    "risk_control",
    "service_commitment",
    "evidence_table",
    "required_response_table",
    "personnel_detail",
    "parameter_response",
    "evidence_explanation",
    "placeholder_section",
    "conclusion",
    "other",
}
RESPONSE_POINT_PATTERN = re.compile(r"\bTR-\d{3,}\b")
SCORE_ITEM_PATTERN = re.compile(r"\bSCORE-[0-9A-Z-]+\b")


@dataclass(frozen=True)
class TechnicalWritingAgentInput:
    response_outline_path: str | Path
    writing_outline_path: str | Path
    task_information_path: str | Path
    output_key: str
    target_chapter_id: str | None = None
    target_section_id: str | None = None
    draft_units_path: str | Path | None = None
    confirmed_project_facts_path: str | Path | None = None
    writing_constraint_context: dict[str, Any] | None = None
    tender_metadata: dict[str, Any] | None = None
    stage12_approved_facts: tuple[dict[str, Any], ...] = ()
    stage12_approved_material_refs: tuple[dict[str, Any], ...] = ()
    stage12_prohibited_inferences: tuple[dict[str, Any], ...] = ()
    stage12_acceptance_criteria: tuple[str, ...] = ()
    user_instruction: str = ""
    user_instruction_sources: tuple[dict[str, Any], ...] = ()
    user_instruction_summary: str = ""
    web_search_policy: str = "auto"
    instruction_scope: str = "once"
    writing_rules_path: str | Path | None = None


@dataclass
class TechnicalWritingAgent:
    llm: LLMClient
    tool_context: TechnicalWritingToolContext
    registry: TechnicalToolRegistry
    chapter_schema: dict[str, Any]
    max_agent_steps: int = 12
    max_same_tool_repeats: int = 1
    max_search_calls: int = 4
    max_local_knowledge_calls: int = 3
    max_web_search_calls: int = 1
    max_pending_confirmation_calls: int = 8
    max_runtime_seconds: float = 600.0
    max_parse_failures: int = 3
    max_auto_repair_rounds: int = 1
    profile_name: str | None = None
    user_instruction: str = ""
    user_instruction_sources: tuple[dict[str, Any], ...] = ()
    user_instruction_summary: str = ""
    web_search_policy: str = "auto"
    instruction_scope: str = "once"
    writing_rules: WritingRules | None = None
    project_constraints: dict[str, Any] | None = None
    stage12_approved_facts: tuple[dict[str, Any], ...] = ()
    stage12_approved_material_refs: tuple[dict[str, Any], ...] = ()
    stage12_prohibited_inferences: tuple[dict[str, Any], ...] = ()
    stage12_acceptance_criteria: tuple[str, ...] = ()

    def run(
        self,
        *,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
        event_log_path: str | Path | None = None,
        stream_model: bool = False,
    ) -> dict[str, Any]:
        if not 0 <= self.max_auto_repair_rounds <= 1:
            raise ValueError("max_auto_repair_rounds must be between 0 and 1")
        started = datetime.now(timezone.utc)
        started_clock = time.monotonic()
        emitter = TechnicalAgentEventEmitter(
            max_steps=self.max_agent_steps,
            event_handler=event_handler,
            event_log_path=event_log_path,
        )
        phase = "preparing_context"
        phase_labels = {
            "preparing_context": "准备上下文",
            "collecting_evidence": "补充证据",
            "writing": "正文生成",
            "finalizing": "最终化",
            "validating": "校验",
            "completed": "完成",
            "failed": "失败",
        }

        def emit(event_type: str, *, step: int = 0, data: dict[str, Any] | None = None) -> None:
            emitter.emit(event_type, step=step, phase=phase, data=data)

        def set_phase(value: str, *, step: int = 0) -> None:
            nonlocal phase
            if phase == value:
                return
            phase = value
            emit(
                "agent_phase_changed",
                step=step,
                data={"phase": value, "phase_label": phase_labels.get(value, value)},
            )

        expected_sections = list(self.tool_context._target.get("children") or []) or [
            self.tool_context._target
        ]
        expected_section_count = len(expected_sections)
        target_title = str(self.tool_context._target.get("title") or "")
        user_instruction_present = bool(self.user_instruction.strip())
        existing_draft = self.tool_context.existing_draft_markdown
        existing_draft_present = bool(existing_draft)
        based_on_existing_draft = existing_draft_present and user_instruction_present
        revision_round = (
            self.tool_context.previous_revision_round + 1
            if based_on_existing_draft
            else self.tool_context.previous_revision_round
        )
        revision_scope = (
            classify_revision_scope(self.user_instruction)
            if user_instruction_present
            else "initial_generation"
        )
        instruction_conflicts = detect_instruction_conflicts(self.user_instruction)
        writing_rules = self.writing_rules or load_writing_rules()
        project_constraints = self.project_constraints or {
            "loaded": False,
            "constraints": [],
            "path": "",
        }
        scope_suggestion, scope_suggestion_reason = suggest_instruction_scope(
            self.user_instruction
        )
        rule_application_plan = build_rule_application_plan(
            target=self.tool_context._target,
            rule_ids=[item["rule_id"] for item in writing_rules.rules],
            tender_tables=self.tool_context.tender_tables,
        )
        emit(
            "agent_run_started",
            data={
                "target_id": self.tool_context._target_id,
                "title": target_title,
                "expected_section_count": expected_section_count,
            },
        )
        emit(
            "agent_phase_changed",
            data={"phase": phase, "phase_label": phase_labels[phase]},
        )
        emit(
            "writing_rules_loaded",
            data={
                "version": writing_rules.version,
                "sha256": writing_rules.sha256,
                "rule_count": len(writing_rules.rules),
            },
        )
        emit(
            "project_constraints_loaded",
            data={
                "loaded": bool(project_constraints.get("loaded")),
                "constraint_count": len(project_constraints.get("constraints") or []),
            },
        )
        emit(
            "rule_application_plan_completed",
            data={
                "section_boundary_count": len(rule_application_plan["section_boundaries"]),
                "tender_table_count": len(self.tool_context.tender_tables),
                "tender_table_reuse_count": len(rule_application_plan["tender_tables_to_reuse"]),
                "created_table_candidate_count": len(rule_application_plan["tables_to_create"]),
                "placeholder_fact_count": len(rule_application_plan["facts_requiring_placeholders"]),
                "external_evidence_need_count": len(rule_application_plan["external_evidence_needs"]),
            },
        )
        emit(
            "tender_tables_detected",
            data={"count": len(self.tool_context.tender_tables)},
        )
        if existing_draft_present:
            emit(
                "existing_draft_detected",
                data={
                    "path": str(self.tool_context.draft_path),
                    "sha256": self.tool_context.existing_draft_sha256,
                    "character_count": len(existing_draft),
                },
            )
        if user_instruction_present:
            emit(
                "user_instruction_loaded",
                data={
                    "sources": [item.get("type") for item in self.user_instruction_sources],
                    "character_count": len(self.user_instruction),
                },
            )
            emit(
                "revision_scope_analyzing",
                data={"revision_scope": revision_scope},
            )
            if scope_suggestion != "once" and self.instruction_scope == "once":
                emit(
                    "instruction_scope_suggested",
                    data={
                        "suggested_scope": scope_suggestion,
                        "reason": scope_suggestion_reason,
                    },
                )
        if based_on_existing_draft:
            emit(
                "revision_started",
                data={"revision_round": revision_round, "revision_scope": revision_scope},
            )
            emit(
                "full_chapter_regeneration_started",
                data={"reason": "existing_draft_with_user_instruction"},
            )
        task_message = (
            f"编写目标 {self.tool_context._target_id}。规划文件由工具按需读取；"
            "用户未提供 task_id、draft_unit_id 或 response_point_id。"
            "首次决策应尽量在同一响应中批量调用彼此独立的基础上下文工具："
            "get_writing_scope、get_response_requirements、get_task_information、"
            "get_resolved_task_context、可用时的get_draft_plan与get_confirmed_project_facts。"
            "自主判断是否查询候选材料、联网或记录人工确认。"
            f"本轮Web Search策略为{self.web_search_policy}；"
            "auto不要求搜索，required要求完成有效外部核验或明确记录无法充分核验，"
            "disabled禁止联网。检索有独立预算；工具被拒绝或预算用尽时，应使用已有资料继续写作。"
            "只有required_response_table按招标原表名、表头、字段顺序、合并关系及填写要求复用；"
            "scoring_reference_table只作覆盖依据，不得插入正文或table_fill_data；"
            "informational_source_table默认只提取事实。"
            "写作前划分小节内容边界并在最终化前检查重复内容。"
            "最后返回完整结构化章节，不得删除目录标题。\n\n"
            "【首次章节写作模式】\n"
            + json.dumps(
                {
                    "writing_mode": "formal_technical_prose",
                    "prose_priority": True,
                    "tables_are_supporting": True,
                    "lists_are_supporting": True,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n输出正式投标技术方案正文。每个主要小节必须先形成充分、连续的正文论述，"
            "说明目标、前置条件、实施方法、关键控制点、成果及异常处理或完成判据；"
            "之后仅在确有必要时使用表格或列表。不得以表格或项目符号替代方案说明。"
            "项目符号仅可用于简短并列条件、无需展开的检查项、材料清单或正文后的归纳；"
            "每组项目符号前后必须有完整正文说明列示目的、执行方式、成果或完成判据。"
            "技术原理、实施方法、依赖关系、职责协作、异常闭环、验收判据和风险处置逻辑"
            "必须使用完整段落，不得写成一句话项目符号。"
            "编号步骤必须采用“步骤名称＋完整说明”，不得写成一句话短条目。"
            "不得为了结构整齐创建多个相似表格，同一信息不得在正文、表格和列表中重复。"
            "任何计划外generated_supporting_table必须在generated_supporting_tables中记录"
            "section_id、title、purpose和why_table_is_better_than_prose。"
            "数字、人员和承诺必须先按来源判断：confirmed fact或带confirmed evidence ID的"
            "selected knowledge按确认值写入；招标明确的交货、安装、保修、服务、培训、验收"
            "和履约基准默认按原值响应并写无偏离，不得为这些同值基准另造占位符；"
            "只有candidate时使用已登记占位符并明确待确认；完全unresolved且非必填时省略，"
            "必填时使用已登记占位符。招标未要求且无确认来源的额外时间、人数、会议周期和承诺"
            "不得补写。正偏离必须先登记确认，负偏离必须显著披露；无偏离意向不能替代产品、"
            "人员、资质、证书、业绩或参数证据。claims中涉及招标响应时应记录response_intent、"
            "evidence_status和deviation_status；发生数值比较时同时记录tender_baseline、"
            "offered_value和comparison_direction，确保Validator可以复核偏离方向。"
        )
        technical_rules_message = (
            "[Technical Writing Rules]\n"
            + writing_rules.content[:40_000]
            + "\n\n[Auditable Rule Application Plan]\n"
            + json.dumps(rule_application_plan, ensure_ascii=False, separators=(",", ":"))
            + "\n写作时必须遵守小节职责边界；只有required_response_table可强制复用："
            "simple原表按原字段生成标准Markdown，complex原表不得在Markdown中简化；"
            "如工具可用，仅对required_response_table使用record_table_fill_data记录已知字段"
            "和统一占位符，保留原格式回填接口。推荐表格不是固定数量，模型可按内容减少、"
            "增加或不采用，但普通表格必须有明确用途、正文前后说明和优于正文的理由。"
        )
        project_constraints_message = (
            "[Project Constraints]\n"
            + json.dumps(
                [
                    {
                        "constraint_id": item.get("constraint_id"),
                        "normalized_text": item.get("normalized_text"),
                    }
                    for item in project_constraints.get("constraints") or []
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        resolved_task_context = self.tool_context.get_resolved_task_context({})
        writing_constraints_message = (
            "[Resolved Task Context]\n"
            + json.dumps(
                resolved_task_context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\nconfirmed_business_choices必须作为业务写作约束使用，不得写入Project Fact或证据结论。"
            "negative_constraint表示明确不得作出该承诺；selected_option只能采用已确认选项。"
            "pending_confirmations已由Runtime登记稳定占位符，不得静默忽略或自行选择答案。"
        )
        clean_task_message = task_message
        clean_original_context = {
            "writing_scope": self.tool_context.get_writing_scope({}),
            "tender_requirements": self.tool_context.get_response_requirements({}),
            "task_information": self.tool_context.get_task_information({}),
            "resolved_task_context": resolved_task_context,
            "draft_plan": self.tool_context.get_draft_plan({}),
            "confirmed_project_facts": self.tool_context.get_confirmed_project_facts({}),
            "tender_tables": deepcopy(self.tool_context.tender_tables),
        }
        section_fact_ledger = _build_section_fact_ledger(
            original_context=clean_original_context,
            target=self.tool_context._target,
            pending_confirmations=deepcopy(
                self.tool_context.pending_confirmations
            ),
            local_evidence_refs=deepcopy(
                self.tool_context.local_evidence_refs
            ),
            stage12_approved_facts=list(self.stage12_approved_facts),
        )
        fresh_final_target_contract: dict[str, Any] = {}
        fresh_final_schema = deepcopy(self.chapter_schema)
        if based_on_existing_draft:
            expected_section_specs = _expected_section_specs(
                self.tool_context._target
            )
            active_pending_confirmations, _ = _active_pending_confirmations(
                self.tool_context.pending_confirmations,
                generation_permissions=section_fact_ledger,
            )
            required_placeholders = [
                {
                    "placeholder_id": str(item.get("placeholder_id") or ""),
                    "placeholder_token": str(item.get("placeholder_token") or ""),
                    "field": str(item.get("field") or ""),
                }
                for item in active_pending_confirmations
                if isinstance(item, dict)
                and item.get("placeholder_id")
                and item.get("blocking", True)
            ]
            fresh_final_target_contract = {
                "target_id": self.tool_context._target_id,
                "canonical_target_title": target_title,
                "target_is_leaf": not bool(
                    self.tool_context._target.get("children")
                ),
                "expected_sections": expected_section_specs,
                "expected_section_count": len(expected_section_specs),
                "additional_sections_allowed": False,
                "required_placeholder_ids": sorted(
                    item["placeholder_id"] for item in required_placeholders
                ),
                "required_registered_placeholders": required_placeholders,
                "existing_canonical_section_body": existing_draft,
                "revision_instruction": self.user_instruction,
            }
            schema_properties = fresh_final_schema["properties"]
            schema_properties["chapter_title"] = {
                "type": "string",
                "const": target_title,
            }
            sections_schema = schema_properties["sections"]
            section_item_schema = deepcopy(sections_schema["items"])
            prefix_items: list[dict[str, Any]] = []
            for spec in expected_section_specs:
                expected_item_schema = deepcopy(section_item_schema)
                required_fields = list(expected_item_schema.get("required") or [])
                if "section_id" not in required_fields:
                    required_fields.append("section_id")
                expected_item_schema["required"] = required_fields
                expected_item_schema["properties"]["section_id"] = {
                    "type": "string",
                    "const": spec["section_id"],
                }
                expected_item_schema["properties"]["heading"] = {
                    "type": "string",
                    "const": spec["section_title"],
                }
                prefix_items.append(expected_item_schema)
            sections_schema.pop("items")
            sections_schema.update(
                {
                    "minItems": len(expected_section_specs),
                    "maxItems": len(expected_section_specs),
                    "prefixItems": prefix_items,
                    "items": False,
                }
            )
        if user_instruction_present:
            task_message += (
                "\n\n【本轮用户意见】\n"
                + self.user_instruction
                + "\n【执行边界】用户意见不得覆盖招标硬性要求、确认事实、Schema、事实边界、"
                "响应点或评分项。无论局部或结构性修改，都重新生成完整章节对象。"
            )
        if based_on_existing_draft:
            task_message += (
                "\n\n【current_draft：Runtime已直接读取，不需要调用工具】\n"
                + existing_draft[:120_000]
                + "\n【修订要求】以current_draft为基线重新生成完整章节；保留未要求改变且仍合规的内容，"
                "不得对Markdown做字符串拼接或正则替换。"
            )
        if self.tool_context.pending_confirmations:
            task_message += (
                "\n\n【已登记的稳定占位符】\n"
                + json.dumps(
                    [
                        {
                            "field": item.get("field"),
                            "placeholder_id": item.get("placeholder_id"),
                            "placeholder_token": item.get("placeholder_token"),
                        }
                        for item in self.tool_context.pending_confirmations
                    ],
                    ensure_ascii=False,
                )
                + "\n正文只能引用这些已登记ID；新增待确认字段必须先调用record_pending_confirmation。"
            )
        if instruction_conflicts:
            task_message += (
                "\n\n【Runtime检测到的意见冲突】\n"
                + json.dumps(instruction_conflicts, ensure_ascii=False)
                + "\n这些冲突部分不得执行。"
            )
        clean_generation_messages = (
            {"role": "system", "content": "[Agent System Rules]\n" + SYSTEM_PROMPT},
            {"role": "developer", "content": technical_rules_message},
            {"role": "developer", "content": project_constraints_message},
            {"role": "developer", "content": writing_constraints_message},
            *(
                ({
                    "role": "developer",
                    "content": (
                    "[Stage12 Approved Revision Inputs]\n"
                    + json.dumps(
                        {
                            "approved_facts": list(self.stage12_approved_facts),
                            "approved_material_refs": list(
                                self.stage12_approved_material_refs
                            ),
                            "prohibited_inferences": list(
                                self.stage12_prohibited_inferences
                            ),
                            "acceptance_criteria": list(
                                self.stage12_acceptance_criteria
                            ),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\napproved_facts仅是本次修订已批准事实，保留FACT/FCA来源类型；"
                    "不得写回Project Fact。approved_material_refs仅作为已核验材料引用，"
                    "不得据此扩展材料中未给出的事实。prohibited_inferences不得推断；"
                    "acceptance_criteria必须逐项满足。"
                    ),
                },)
                if any(
                    (
                        self.stage12_approved_facts,
                        self.stage12_approved_material_refs,
                        self.stage12_prohibited_inferences,
                        self.stage12_acceptance_criteria,
                    )
                )
                else ()
            ),
            *(
                ({
                    "role": "developer",
                    "content": (
                        "[Targeted Existing-Draft Revision Contract]\n"
                        + json.dumps(
                            fresh_final_target_contract,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\nThis contract is mandatory. Return exactly the listed sections; "
                        "do not create child or additional sections, and preserve every "
                        "required registered placeholder token in meaningful context."
                    ),
                },)
                if fresh_final_target_contract
                else ()
            ),
            {
                "role": "developer",
                "content": (
                    "Tool参数中的正文如需使用引号，优先使用中文全角引号“”或书名号，"
                    "不要直接输出未转义的ASCII双引号。"
                ),
            },
            {
                "role": "developer",
                "content": PENDING_CONFIRMATION_ATOMIC_FIELD_RULES,
            },
            {
                "role": "developer",
                "content": (
                    "[Output Schema]\n最终响应必须严格遵循以下唯一章节Schema：\n"
                    + json.dumps(fresh_final_schema, ensure_ascii=False, separators=(",", ":"))
                ),
            },
            {"role": "user", "content": clean_task_message},
        )
        messages = [deepcopy(item) for item in clean_generation_messages]
        messages[-1]["content"] = task_message
        messages.insert(
            -1,
            {
                "role": "developer",
                "content": _section_fact_ledger_message(
                    fact_ledger=section_fact_ledger,
                ),
            },
        )

        def refresh_section_fact_ledger() -> None:
            nonlocal section_fact_ledger
            section_fact_ledger = _build_section_fact_ledger(
                original_context=clean_original_context,
                target=self.tool_context._target,
                pending_confirmations=deepcopy(
                    self.tool_context.pending_confirmations
                ),
                local_evidence_refs=deepcopy(
                    self.tool_context.local_evidence_refs
                ),
                stage12_approved_facts=list(self.stage12_approved_facts),
            )
            for message in messages:
                if (
                    message.get("role") == "developer"
                    and "[Section-specific Fact Ledger]"
                    in str(message.get("content") or "")
                ):
                    message["content"] = _section_fact_ledger_message(
                        fact_ledger=section_fact_ledger,
                    )

        reusable_evidence_snapshots: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        model_calls: list[dict[str, Any]] = []
        tool_arguments_json_repairs: list[dict[str, Any]] = []
        signature_counts: dict[str, int] = {}
        query_signatures: list[tuple[str, str]] = []
        tool_counts: dict[str, int] = {}
        parse_failures = 0
        invalid_tool_argument_count = 0
        invalid_tool_argument_count_by_tool: dict[str, int] = {}
        disabled_tools: set[str] = set()
        force_final_after_recoverable_tool_error = False
        stop_reason = "max_agent_steps"
        model_final_output: dict[str, Any] | None = None
        final_draft: dict[str, Any] | None = None
        errors: list[str] = []
        warnings: list[str] = [
            f"user_instruction_conflict:{item['code']}" for item in instruction_conflicts
        ]
        duplicate_tool_calls = 0
        forced_finalization = False
        finalization_announced = False
        writing_announced = False
        streamed_characters = 0
        model_step_count = 0
        draft_file_preserved = False
        json_parse_failure = False
        model_json_repair: dict[str, Any] = {
            "attempted": False,
            "succeeded": False,
            "disabled": True,
            "initial_error": None,
            "repair_error": None,
            "raw_output_saved": False,
            "raw_output_path": None,
            "raw_output_sha256": None,
            "suspected_truncation": False,
            "integrity_check": None,
            "rejected_for_content_change": False,
        }
        fresh_final_generation: dict[str, Any] = {
            "attempted": False,
            "succeeded": False,
            "source_stage": None,
        }
        fresh_quality_regeneration: dict[str, Any] = {
            "attempted": False,
            "succeeded": False,
            "generated": False,
            "validated": False,
            "accepted": False,
            "rejected_reason": None,
            "failure_reason": None,
            "model_call_failed": False,
            "model_response_parse_failed": False,
            "quality_errors": [],
            "first_error_details": [],
            "remaining_error_details": None,
            "first_error_count": 0,
            "remaining_error_count": None,
            "first_raw_error_count": 0,
            "first_grouped_error_count": 0,
            "remaining_raw_error_count": None,
            "remaining_grouped_error_count": None,
            "remaining_validation_status": "not_run",
            "kb_calls_reused": 0,
            "web_results_reused": 0,
            "repair_input_summary": {
                "total_issue_count": 0,
                "auto_repairable_count": 0,
                "review_only_count": 0,
                "unique_issue_count": 0,
                "auto_repairable_unique_issue_count": 0,
                "review_only_unique_issue_count": 0,
                "repair_item_count": 0,
                "review_guard_count": 0,
                "protected_fact_count": 0,
                "protected_placeholder_count": 0,
            },
            "auto_repair_issue_codes": [],
            "review_only_issue_codes": [],
            "preserved_placeholder_ids": [],
            "preserved_tender_fact_ids": [],
            "fresh_edit_scope": {
                "editable_section_ids": [],
                "editable_issue_count": 0,
                "raw_auto_repair_issue_count": 0,
                "unique_auto_repair_issue_count": 0,
                "frozen_section_count": 0,
            },
            "fresh_patch": _fresh_patch_report_template(
                base_candidate_hash="", repair_task_count=0
            ),
            "fresh_repair_mode": "multi_candidate_structured_patch",
            "base_candidate_hash": None,
            "repair_task_count": 0,
            "repair_candidate_count": 0,
            "tasks_with_one_candidate": 0,
            "tasks_with_two_candidates": 0,
            "tasks_with_three_candidates": 0,
            "tasks_missing_conservative_candidate": 0,
            "duplicate_candidate_count": 0,
            "exact_locator_patch_count": 0,
            "local_guarded_delete_candidate_count": 0,
            "local_guarded_delete_applied_count": 0,
            "canonical_title_normalization_count": 0,
            "table_intro_normalization_count": 0,
            "applied_operation_count": 0,
            "rejected_candidate_count": 0,
            "unresolved_repair_task_count": 0,
            "applied_operations": [],
            "unresolved_issue_fingerprints": [],
            "fresh_diff_audit": {
                "changed_section_ids": [],
                "editable_section_ids": [],
                "frozen_section_ids": [],
                "unauthorized_changed_section_ids": [],
                "new_fact_category_codes": [],
                "new_fact_issue_codes": [],
                "new_fact_records": [],
                "new_numeric_spans": [],
                "new_personnel_fact_spans": [],
                "new_product_capability_spans": [],
                "new_resource_commitment_spans": [],
                "new_bidder_commitment_spans": [],
                "passed": True,
                "section_identity_consistent": True,
            },
            "section_identity_map": [],
            "section_identity_issues": [],
            "candidate_selection_audit": {
                "initial_prose_characters": 0,
                "fresh_prose_characters": 0,
                "prose_change_ratio": 0.0,
                "sections_within_tolerance": [],
                "sections_with_length_warning": [],
                "sections_crossing_quality_floor": [],
                "initial_quality_issue_codes": [],
                "fresh_quality_issue_codes": [],
                "new_quality_issue_codes": [],
                "coverage_regressed": False,
                "placeholder_lost": False,
            },
        }
        initial_candidate_validation: dict[str, Any] | None = None
        fresh_candidate_validation: dict[str, Any] | None = None
        selected_candidate = "initial"
        candidate_selection_reason = "fresh_candidate_not_generated"
        fresh_candidate_accepted = False
        initial_candidate_sha256: str | None = None
        fresh_candidate_sha256: str | None = None
        force_final_after_repair_rejection = False
        automatic_repair_rounds: list[dict[str, Any]] = []
        fresh_repair_context: dict[str, Any] = {}
        runtime_complete_working_draft_finalized = False

        def clean_regeneration_messages(instruction: str) -> list[dict[str, Any]]:
            evidence_snapshot = {
                "original_context": deepcopy(clean_original_context),
                "evidence_results": [
                    {
                        "tool_name": item.get("tool_name"),
                        "status": (item.get("result") or {}).get("status"),
                        "candidate_ids": [
                            str(value.get("candidate_id"))
                            for value in (item.get("result") or {}).get("results") or []
                            if isinstance(value, dict) and value.get("candidate_id")
                        ],
                        "policy": "candidate_details_are_not_writing_facts",
                    }
                    for item in reusable_evidence_snapshots
                ],
                "local_evidence_candidates": [
                    {
                        "candidate_id": item.get("candidate_id"),
                        "evidence_type": item.get("evidence_type"),
                        "entity_type": item.get("entity_type"),
                        "selection_status": item.get("selection_status")
                        or "candidate",
                        "policy": (
                            "confirmed_evidence_id_only"
                            if item.get("selection_status") == "confirmed"
                            else "forbidden_as_confirmed_fact"
                        ),
                    }
                    for item in self.tool_context.local_evidence_refs
                ],
                "web_evidence": [
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "source_type": item.get("source_type"),
                    }
                    for item in self.tool_context.web_refs
                ],
                "pending_confirmations": [
                    {
                        "field": item.get("field"),
                        "placeholder_id": item.get("placeholder_id"),
                        "placeholder_token": item.get("placeholder_token"),
                        "affected_sections": item.get("affected_sections") or [],
                        "blocking": item.get("blocking"),
                    }
                    for item in self.tool_context.pending_confirmations
                ],
            }
            return [
                *[deepcopy(item) for item in clean_generation_messages],
                {
                    "role": "developer",
                    "content": (
                        "[Clean Original Context and Evidence Snapshot]\n"
                        + json.dumps(
                            evidence_snapshot,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                },
                {
                    "role": "developer",
                    "content": _section_fact_ledger_message(
                        fact_ledger=section_fact_ledger,
                    ),
                },
                {"role": "user", "content": instruction},
            ]

        def run_targeted_repair_cycle(
            *,
            repair_messages: list[dict[str, Any]],
            step: int,
            purpose: str,
            repair_round: int,
        ) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
            conversation = [deepcopy(item) for item in repair_messages]
            permitted = {
                "get_writing_scope",
                "get_response_requirements",
                "get_task_information",
                "get_resolved_task_context",
                "get_draft_plan",
                "get_confirmed_project_facts",
                "query_local_knowledge",
                "read_local_evidence",
                "web_search",
                "open_web_page",
                "record_pending_confirmation",
            } & set(self.registry.names())
            repair_model_calls: list[dict[str, Any]] = []
            repair_tool_calls: list[dict[str, Any]] = []
            for iteration in range(1, 4):
                result = self.llm.run_with_tools(
                    messages=conversation,
                    tools=self.registry.definitions(permitted),
                    tool_choice="auto" if permitted else "none",
                    options={"stream": False},
                    request_metadata={
                        "target_id": self.tool_context._target_id,
                        "agent_step": step,
                        "purpose": purpose,
                        "repair_round": repair_round,
                        "repair_iteration": iteration,
                        "output_key": self.tool_context.output_key,
                    },
                    profile_name=self.profile_name,
                )
                tool_arguments_json_repairs.extend(
                    _record_tool_argument_json_repairs(
                        tool_calls=result.tool_calls,
                        step=step,
                        output_root=self.tool_context.output_root,
                        output_key=self.tool_context.output_key,
                        target_id=self.tool_context._target_id,
                        secret=str(
                            getattr(getattr(self.llm, "profile", None), "api_key", "")
                            or ""
                        ),
                        emit=emit,
                    )
                )
                summary = _model_call_summary(step, result.request_metadata)
                summary["purpose"] = purpose
                summary["repair_round"] = repair_round
                summary["repair_iteration"] = iteration
                repair_model_calls.append(summary)
                if result.response_type == "final" and not result.tool_calls:
                    return result, repair_model_calls, repair_tool_calls
                if result.response_type != "tool_calls" or not result.tool_calls:
                    raise ValueError(
                        "targeted repair returned neither tool calls nor a final chapter"
                    )
                conversation.append(result.context_message())
                for call in result.tool_calls:
                    name = str(call.get("name") or "")
                    arguments = call.get("arguments") or {}
                    call_id = str(call.get("tool_call_id") or "")
                    tool_record = {
                        "repair_round": repair_round,
                        "repair_iteration": iteration,
                        "tool_call_id": call_id,
                        "tool_name": name,
                        "arguments_summary": _safe_arguments(arguments),
                    }
                    if name not in permitted:
                        tool_result = {
                            "status": "error",
                            "error": "tool is not allowed in targeted repair",
                        }
                        tool_record["status"] = "error"
                    else:
                        skip_reason = _tool_skip_reason(
                            name=name,
                            signature_count=1,
                            allowed_names=permitted,
                            tool_counts=tool_counts,
                            max_search_calls=self.max_search_calls,
                            max_local_knowledge_calls=self.max_local_knowledge_calls,
                            max_web_search_calls=self.max_web_search_calls,
                            max_pending_confirmation_calls=self.max_pending_confirmation_calls,
                        )
                        if skip_reason:
                            tool_result = {
                                "status": "skipped",
                                "reason": skip_reason,
                            }
                            tool_record["status"] = "skipped"
                            tool_record["reason"] = skip_reason
                        else:
                            try:
                                tool_result = self.registry.execute(
                                    name,
                                    arguments,
                                )
                                tool_counts[name] = tool_counts.get(name, 0) + 1
                                tool_record["status"] = str(
                                    tool_result.get("status") or "success"
                                )
                                tool_record["result_count"] = _result_count(
                                    tool_result
                                )
                                if name in {
                                    "query_local_knowledge",
                                    "read_local_evidence",
                                    "web_search",
                                    "open_web_page",
                                }:
                                    reusable_evidence_snapshots.append(
                                        {
                                            "tool_name": name,
                                            "result": deepcopy(tool_result),
                                        }
                                    )
                                if name in {
                                    "query_local_knowledge",
                                    "record_pending_confirmation",
                                }:
                                    refresh_section_fact_ledger()
                            except (
                                ToolExecutionError,
                                PermissionError,
                                ValueError,
                                FileNotFoundError,
                            ) as exc:
                                tool_result = {
                                    "status": "error",
                                    "error": str(exc)[:500],
                                }
                                tool_record["status"] = "error"
                                tool_record["error"] = str(exc)[:300]
                    repair_tool_calls.append(tool_record)
                    trace.append(
                        {
                            "step": step,
                            **deepcopy(tool_record),
                        }
                    )
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": json.dumps(
                                tool_result,
                                ensure_ascii=False,
                            ),
                        }
                    )
            raise ValueError(
                "targeted repair exceeded its bounded tool interaction steps"
            )

        def attempt_fresh_final_recovery(
            *,
            initial_error: ModelJsonParseError | ModelOutputIncompleteError | None,
            raw_content: str = "",
            step: int,
            source_stage: str,
            finish_reason: str | None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            if fresh_final_generation["attempted"]:
                raise ModelJsonParseError(
                    "fresh final recovery has already been attempted",
                    diagnostics={
                        "repair_attempted": False,
                        "fresh_final_attempted": True,
                        "fresh_final_succeeded": False,
                    },
                )
            profile = getattr(self.llm, "profile", None)
            safe_error = (
                _safe_model_error_summary(initial_error, profile=profile)
                if initial_error is not None
                else None
            )
            diagnostics = (
                initial_error.safe_diagnostics()
                if initial_error is not None
                else {}
            )
            if isinstance(initial_error, ModelJsonParseError):
                debug = _save_invalid_model_output(
                    output_root=self.tool_context.output_root,
                    output_key=self.tool_context.output_key,
                    target_id=self.tool_context._target_id,
                    step=step,
                    content=raw_content,
                    secret=str(getattr(profile, "api_key", "") or ""),
                )
                suspected_truncation = bool(
                    diagnostics.get("suspected_truncation")
                    or suspected_json_truncation(
                        raw_content,
                        finish_reason=finish_reason,
                        usage=diagnostics.get("usage"),
                        max_output_tokens=getattr(profile, "max_output_tokens", None),
                        error_summary=str(diagnostics.get("json_error_summary") or ""),
                    )
                )
                model_json_repair.update(
                    {
                        "attempted": False,
                        "succeeded": False,
                        "initial_error": safe_error,
                        "raw_output_saved": debug["saved"],
                        "raw_output_path": debug["path"],
                        "raw_output_sha256": debug["sha256"],
                        "suspected_truncation": suspected_truncation,
                        "source_stage": source_stage,
                        "diagnostics": {
                            "model_step": step,
                            "profile_name": self.profile_name
                            or getattr(profile, "name", None),
                            "api_style": getattr(profile, "api_style", None),
                            "finish_reason": finish_reason,
                            "content_character_count": len(raw_content),
                            "code_block_detected": bool(
                                diagnostics.get("code_block_detected")
                            ),
                            "suspected_truncation": suspected_truncation,
                            "json_error_summary": diagnostics.get(
                                "json_error_summary"
                            ),
                            "model_repair_disabled": True,
                            "repair_attempted": False,
                        },
                    }
                )
            fresh_final_generation.update(
                {
                    "attempted": True,
                    "succeeded": False,
                    "source_stage": source_stage,
                    "initial_error": safe_error,
                }
            )
            emit(
                "fresh_final_generation_started",
                step=step,
                data={
                    "source_stage": source_stage,
                    "tools_available": 0,
                    "damaged_output_included": False,
                },
            )
            fresh_started = time.monotonic()
            purpose = (
                "fresh_final_after_blocked_validation"
                if initial_error is None
                and source_stage == "initial_validation_blocked"
                else "fresh_final_after_blocked_repair"
                if initial_error is None
                else "fresh_final_after_json_failure"
                if isinstance(initial_error, ModelJsonParseError)
                else "fresh_final_after_incomplete_output"
            )
            result = self.llm.run_with_tools(
                messages=clean_regeneration_messages(
                    _fresh_final_generation_instruction(
                        schema=fresh_final_schema,
                        target_contract=fresh_final_target_contract,
                    )
                ),
                tools=[],
                tool_choice="none",
                options={"stream": False, "reasoning_effort": "low"},
                request_metadata={
                    "target_id": self.tool_context._target_id,
                    "agent_step": step,
                    "purpose": purpose,
                    "output_key": self.tool_context.output_key,
                },
                profile_name=self.profile_name,
            )
            summary = _model_call_summary(step, result.request_metadata)
            summary["purpose"] = purpose
            model_calls.append(summary)
            if result.response_type != "final" or result.tool_calls:
                raise ModelJsonParseError(
                    "fresh final response was not a tool-free final response",
                    diagnostics={
                        "repair_attempted": False,
                        "fresh_final_attempted": True,
                        "fresh_final_succeeded": False,
                    },
                )
            output = parse_json_object(str(result.final_content or ""))
            draft = normalize_chapter_draft_output(
                output,
                pending_confirmations=self.tool_context.pending_confirmations,
            )
            try:
                Draft202012Validator(fresh_final_schema).validate(draft)
            except ValidationError as exc:
                path = ".".join(str(item) for item in exc.absolute_path) or "$"
                raise ModelJsonParseError(
                    "fresh final output failed target Schema",
                    diagnostics={
                        "repair_attempted": False,
                        "fresh_final_attempted": True,
                        "fresh_final_succeeded": False,
                        "schema_validation_error_summary": (
                            f"schema validation failed at {path}: {exc.message}"
                        )[:500],
                    },
                ) from None
            fresh_final_generation["succeeded"] = True
            emit(
                "fresh_final_generation_completed",
                step=step,
                data={
                    "succeeded": True,
                    "duration_ms": int(
                        (time.monotonic() - fresh_started) * 1000
                    ),
                },
            )
            return output, draft

        def record_incomplete_output_diagnostics(
            *,
            error: ModelOutputIncompleteError,
            step: int,
        ) -> dict[str, Any]:
            """Persist the existing safe provider diagnostics without raw output."""
            diagnostics = error.safe_diagnostics()
            usage = dict(diagnostics.get("usage") or {})
            safe = {
                "response_status": diagnostics.get("response_status"),
                "finish_reason": diagnostics.get("finish_status"),
                "has_content": bool(diagnostics.get("output_text_available")),
                "content_length": int(diagnostics.get("output_text_character_count") or 0),
                "has_tool_calls": bool(diagnostics.get("has_tool_calls")),
                "tool_call_count": int(diagnostics.get("tool_call_count") or 0),
                "has_reasoning_content": bool(diagnostics.get("has_reasoning_content")),
                "usage": usage,
                "output_tokens": usage.get("output_tokens"),
                "reasoning_tokens": usage.get("reasoning_tokens"),
                "response_type": diagnostics.get("response_type") or "incomplete",
            }
            debug = _save_incomplete_model_diagnostics(
                output_root=self.tool_context.output_root,
                output_key=self.tool_context.output_key,
                target_id=self.tool_context._target_id,
                step=step,
                diagnostics=safe,
            )
            safe.update(
                {
                    "debug_saved": bool(debug.get("saved")),
                    "debug_path": debug.get("path"),
                    "debug_sha256": debug.get("sha256"),
                }
            )
            return safe

        def attempt_model_json_repair(
            *,
            initial_error: ModelJsonParseError,
            raw_content: str,
            step: int,
            source_stage: str,
            finish_reason: str | None,
            usage: dict[str, int | None] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            if model_json_repair["attempted"]:
                raise ModelJsonParseError(
                    "model JSON repair has already been attempted",
                    diagnostics={"repair_attempted": True, "repair_succeeded": False},
                )
            profile = getattr(self.llm, "profile", None)
            safe_error = _safe_model_error_summary(initial_error, profile=profile)
            diagnostics = initial_error.safe_diagnostics()
            suspected_truncation = bool(
                diagnostics.get("suspected_truncation")
                or suspected_json_truncation(
                    raw_content,
                    finish_reason=finish_reason,
                    usage=usage or diagnostics.get("usage"),
                    max_output_tokens=getattr(profile, "max_output_tokens", None),
                    error_summary=str(diagnostics.get("json_error_summary") or ""),
                )
            )
            safe_diagnostics = {
                "model_step": step,
                "profile_name": self.profile_name or getattr(profile, "name", None),
                "api_style": getattr(profile, "api_style", None),
                "finish_reason": finish_reason,
                "content_character_count": len(raw_content),
                "code_block_detected": bool(diagnostics.get("code_block_detected")),
                "suspected_truncation": suspected_truncation,
                "json_error_summary": diagnostics.get("json_error_summary"),
                "schema_validation_error_summary": diagnostics.get(
                    "schema_validation_error_summary"
                ),
                "repair_attempted": True,
            }
            debug = _save_invalid_model_output(
                output_root=self.tool_context.output_root,
                output_key=self.tool_context.output_key,
                target_id=self.tool_context._target_id,
                step=step,
                content=raw_content,
                secret=str(getattr(profile, "api_key", "") or ""),
            )
            model_json_repair.update(
                {
                    "attempted": True,
                    "initial_error": safe_error,
                    "raw_output_saved": debug["saved"],
                    "raw_output_path": debug["path"],
                    "raw_output_sha256": debug["sha256"],
                    "suspected_truncation": suspected_truncation,
                    "source_stage": source_stage,
                    "diagnostics": safe_diagnostics,
                }
            )
            emit(
                "model_json_repair_started",
                step=step,
                data={
                    "source_stage": source_stage,
                    "suspected_truncation": suspected_truncation,
                    "raw_output_saved": debug["saved"],
                },
            )
            repair_prompt = _model_json_repair_prompt(
                raw_content=raw_content,
                schema=self.chapter_schema,
                error_summary=safe_error,
                suspected_truncation=suspected_truncation,
            )
            repair_budget = _model_json_repair_token_budget(profile)
            repair_started = time.monotonic()
            emit(
                "model_call_started",
                step=step,
                data={
                    "profile_name": self.profile_name or getattr(profile, "name", None),
                    "available_tool_count": 0,
                    "purpose": "model_json_repair",
                },
            )
            try:
                repair_result = self.llm.run_with_tools(
                    messages=[
                        *[dict(item) for item in messages],
                        {"role": "user", "content": repair_prompt},
                    ],
                    tools=[],
                    tool_choice="none",
                    options={
                        "stream": False,
                        "max_output_tokens": repair_budget,
                    },
                    request_metadata={
                        "target_id": self.tool_context._target_id,
                        "agent_step": step,
                        "purpose": "model_json_repair",
                        "output_key": self.tool_context.output_key,
                    },
                    profile_name=self.profile_name,
                )
                repair_summary = _model_call_summary(step, repair_result.request_metadata)
                repair_summary["purpose"] = "model_json_repair"
                model_calls.append(repair_summary)
                emit(
                    "model_call_completed",
                    step=step,
                    data={
                        "purpose": "model_json_repair",
                        "response_type": repair_result.response_type,
                        "tool_call_count": len(repair_result.tool_calls),
                        "duration_ms": int((time.monotonic() - repair_started) * 1000),
                    },
                )
                emit("model_usage_updated", step=step, data=_sum_usage(model_calls))
                if repair_result.response_type != "final" or repair_result.tool_calls:
                    raise ModelJsonParseError(
                        "JSON repair response was not a tool-free final response",
                        diagnostics={
                            "repair_attempted": True,
                            "repair_succeeded": False,
                            "finish_reason": repair_result.request_metadata.get("finish_status"),
                        },
                        raw_content=str(repair_result.final_content or ""),
                    )
                repaired_output = parse_json_object(str(repair_result.final_content or ""))
                repaired_draft = normalize_chapter_draft_output(
                    repaired_output,
                    pending_confirmations=self.tool_context.pending_confirmations,
                )
                try:
                    Draft202012Validator(self.chapter_schema).validate(repaired_draft)
                except ValidationError as exc:
                    path = ".".join(str(item) for item in exc.absolute_path) or "$"
                    raise ModelJsonParseError(
                        "JSON repair output failed target Schema",
                        diagnostics={
                            "repair_attempted": True,
                            "repair_succeeded": False,
                            "schema_validation_error_summary": (
                                f"schema validation failed at {path}: {exc.message}"
                            )[:500],
                            "finish_reason": repair_result.request_metadata.get("finish_status"),
                        },
                        raw_content=str(repair_result.final_content or ""),
                    ) from None
                integrity = _assess_json_repair_integrity(
                    raw_content,
                    repaired_draft,
                )
                model_json_repair["integrity_check"] = integrity
                model_json_repair["diagnostics"]["integrity_check"] = integrity
                if not integrity["accepted"]:
                    model_json_repair["rejected_for_content_change"] = True
                    raise ModelJsonParseError(
                        "JSON repair changed chapter content instead of repairing syntax",
                        diagnostics={
                            "repair_attempted": True,
                            "repair_succeeded": False,
                            "repair_rejected_for_content_change": True,
                            "integrity_check": integrity,
                        },
                        raw_content=str(repair_result.final_content or ""),
                    )
            except Exception as exc:
                repair_error = (
                    exc
                    if isinstance(exc, ModelJsonParseError)
                    else ModelJsonParseError(
                        f"model JSON repair request failed: {type(exc).__name__}",
                        diagnostics={"repair_attempted": True, "repair_succeeded": False},
                    )
                )
                repair_summary = _safe_model_error_summary(repair_error, profile=profile)
                model_json_repair["repair_error"] = repair_summary
                model_json_repair["diagnostics"].update(
                    {
                        "repair_succeeded": False,
                        "repair_error_summary": repair_summary,
                        "schema_validation_error_summary": (
                            repair_error.safe_diagnostics().get(
                                "schema_validation_error_summary"
                            )
                            or model_json_repair["diagnostics"].get(
                                "schema_validation_error_summary"
                            )
                        ),
                    }
                )
                emit(
                    "model_json_repair_failed",
                    step=step,
                    data={
                        "error_type": type(exc).__name__,
                        "repair_error": repair_summary,
                    },
                )
                raise ModelJsonParseError(
                    "model output remained invalid after one controlled JSON repair",
                    diagnostics={
                        **initial_error.safe_diagnostics(),
                        **repair_error.safe_diagnostics(),
                        "model_step": step,
                        "profile_name": self.profile_name or getattr(profile, "name", None),
                        "api_style": getattr(profile, "api_style", None),
                        "finish_reason": finish_reason,
                        "repair_attempted": True,
                        "repair_succeeded": False,
                        "initial_error": safe_error,
                        "repair_error": repair_summary,
                        "raw_output_path": debug["path"],
                        "raw_output_sha256": debug["sha256"],
                    },
                ) from None
            model_json_repair["succeeded"] = True
            model_json_repair["diagnostics"]["repair_succeeded"] = True
            emit(
                "model_json_repair_completed",
                step=step,
                data={
                    "succeeded": True,
                    "source_stage": source_stage,
                    "output_character_count": len(str(repair_result.final_content or "")),
                },
            )
            return repaired_output, repaired_draft

        for step in range(1, self.max_agent_steps + 2):
            if step > self.max_agent_steps and not force_final_after_repair_rejection:
                break
            if time.monotonic() - started_clock > self.max_runtime_seconds:
                stop_reason = "max_runtime_exceeded"
                deterministic_draft = _complete_working_draft_candidate(
                    tool_context=self.tool_context,
                    target=self.tool_context._target,
                )
                if deterministic_draft is not None:
                    # The time budget governs model work, not the deterministic
                    # local conversion of a complete staged draft into the
                    # normal candidate/validation path.  In particular, do not
                    # call either normal final generation or Fresh repair here.
                    final_draft = deterministic_draft
                    model_final_output = deepcopy(deterministic_draft)
                    runtime_complete_working_draft_finalized = True
                    forced_finalization = True
                    set_phase("finalizing", step=model_step_count)
                    refresh_section_fact_ledger()
                    if not finalization_announced:
                        emit(
                            "finalization_started",
                            step=model_step_count,
                            data={"reason": "max_runtime_complete_working_draft"},
                        )
                        finalization_announced = True
                    emit(
                        "finalization_forced",
                        step=model_step_count,
                        data={
                            "tools_available": 0,
                            "reason": "max_runtime_complete_working_draft",
                        },
                    )
                    emit(
                        "chapter_draft_assembled",
                        step=model_step_count,
                        data={
                            "section_count": len(final_draft.get("sections") or []),
                            "source": "complete_working_draft",
                        },
                    )
                break
            model_step_count = step
            remaining_steps = self.max_agent_steps - step + 1
            saved_section_count = len(self.tool_context.saved_sections)
            complete_working_sections = bool(
                expected_section_count and saved_section_count >= expected_section_count
            )
            forced_by_recoverable_tool_error = force_final_after_recoverable_tool_error
            force_this_step = (
                remaining_steps <= 1
                or complete_working_sections
                or forced_by_recoverable_tool_error
            )
            if force_final_after_repair_rejection:
                force_this_step = True
            allowed_names = set(self.registry.names()) - disabled_tools
            if remaining_steps <= 3:
                allowed_names -= {"query_local_knowledge", "read_local_evidence", "web_search", "open_web_page"}
                set_phase("writing", step=step)
            if remaining_steps <= 2:
                allowed_names &= {
                    "save_section_draft",
                    "load_existing_draft",
                }
            if force_this_step:
                allowed_names.clear()
                forced_finalization = (
                    remaining_steps <= 1
                    or forced_by_recoverable_tool_error
                )
                set_phase("finalizing", step=step)
                refresh_section_fact_ledger()
                if not finalization_announced:
                    emit("finalization_started", step=step, data={"reason": "reserved_final_step"})
                    finalization_announced = True
                if forced_finalization:
                    emit(
                        "finalization_forced",
                        step=step,
                        data={
                            "remaining_steps": remaining_steps,
                            "tools_available": 0,
                            "reason": (
                                "recoverable_tool_argument_threshold"
                                if forced_by_recoverable_tool_error
                                else "reserved_final_step"
                            ),
                        },
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": _finalization_instruction(
                            schema=self.chapter_schema,
                            target=self.tool_context._target,
                            working_draft=self.tool_context.working_draft_markdown,
                            pending_confirmations=self.tool_context.pending_confirmations,
                            local_refs=self.tool_context.local_evidence_refs,
                            web_refs=self.tool_context.web_refs,
                            fact_ledger=deepcopy(section_fact_ledger),
                        ),
                    }
                )
                force_final_after_repair_rejection = False
                force_final_after_recoverable_tool_error = False

            profile = getattr(self.llm, "profile", None)
            emit(
                "model_call_started",
                step=step,
                data={
                    "provider": getattr(profile, "provider", None),
                    "profile_name": self.profile_name or getattr(profile, "name", None),
                    "model": getattr(profile, "model", None),
                    "api_mode": getattr(profile, "api_mode", None),
                    "available_tool_count": len(allowed_names),
                },
            )
            model_started = time.monotonic()

            def on_provider_stream(event: dict[str, Any]) -> None:
                nonlocal streamed_characters, writing_announced
                if event.get("type") != "text_delta":
                    return
                value = str(event.get("delta") or "")
                if not value:
                    return
                streamed_characters += len(value)
                if not writing_announced:
                    set_phase("writing", step=step)
                    emit("writing_started", step=step, data={"target_id": self.tool_context._target_id})
                    emit(
                        "section_draft_started",
                        step=step,
                        data={"section_id": self.tool_context._target_id, "section_title": target_title},
                    )
                    writing_announced = True
                emit(
                    "section_draft_delta",
                    step=step,
                    data={
                        "delta_character_count": len(value),
                        "characters_generated": streamed_characters,
                    },
                )

            try:
                model_result = self.llm.run_with_tools(
                    messages=messages,
                    tools=self.registry.definitions(allowed_names),
                    tool_choice="auto" if allowed_names else "none",
                    options={
                        "stream": bool(stream_model),
                        "stream_event_handler": on_provider_stream,
                        "retry_event_handler": lambda retry: (
                            emit("model_call_retry_scheduled", step=step, data=retry),
                            emit("model_call_retry_started", step=step, data=retry),
                        ),
                        **(
                            {"finalization_tools": self.registry.definitions(self.registry.names())}
                            if force_this_step
                            else {}
                        ),
                    },
                    request_metadata={
                        "target_id": self.tool_context._target_id,
                        "agent_step": step,
                        "output_key": self.tool_context.output_key,
                    },
                    profile_name=self.profile_name,
                )
                tool_arguments_json_repairs.extend(
                    _record_tool_argument_json_repairs(
                        tool_calls=model_result.tool_calls,
                        step=step,
                        output_root=self.tool_context.output_root,
                        output_key=self.tool_context.output_key,
                        target_id=self.tool_context._target_id,
                        secret=str(
                            getattr(getattr(self.llm, "profile", None), "api_key", "")
                            or ""
                        ),
                        emit=emit,
                    )
                )
            except ModelJsonParseError as exc:
                failed_argument_repair = _record_failed_tool_argument_json_repair(
                    error=exc,
                    step=step,
                    emit=emit,
                )
                if failed_argument_repair:
                    tool_arguments_json_repairs.append(failed_argument_repair)
                emit(
                    "model_call_failed",
                    step=step,
                    data={
                        "error_type": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - model_started) * 1000),
                    },
                )
                try:
                    model_final_output, final_draft = attempt_fresh_final_recovery(
                        initial_error=exc,
                        raw_content=str(exc.raw_content or ""),
                        step=step,
                        source_stage=str(
                            exc.safe_diagnostics().get("json_context")
                            or "tool_or_control_protocol"
                        ),
                        finish_reason=exc.safe_diagnostics().get("finish_reason"),
                    )
                except ModelJsonParseError as repair_exc:
                    json_parse_failure = True
                    errors.append(
                        "model_json_parse_error: "
                        + _safe_model_error_summary(
                            repair_exc,
                            profile=getattr(self.llm, "profile", None),
                        )
                    )
                    stop_reason = "model_json_parse_error"
                    break
                if not finalization_announced:
                    set_phase("finalizing", step=step)
                    emit(
                        "finalization_started",
                        step=step,
                        data={"reason": "fresh_final_after_json_failure"},
                    )
                    finalization_announced = True
                emit(
                    "structured_output_received",
                    step=step,
                    data={
                        "section_count": len(final_draft.get("sections") or []),
                        "character_count": sum(
                            len(str(item.get("content") or ""))
                            for item in final_draft.get("sections") or []
                        ),
                    },
                )
                emit(
                    "chapter_draft_assembled",
                    step=step,
                    data={"section_count": len(final_draft.get("sections") or [])},
                )
                stop_reason = "fresh_final_after_json_failure_succeeded"
                break
            except ModelOutputIncompleteError as exc:
                incomplete_diagnostics = record_incomplete_output_diagnostics(
                    error=exc,
                    step=step,
                )
                emit(
                    "model_output_incomplete",
                    step=step,
                    data=incomplete_diagnostics,
                )
                emit(
                    "model_call_failed",
                    step=step,
                    data={
                        "error_type": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - model_started) * 1000),
                        "response_diagnostics": incomplete_diagnostics,
                    },
                )
                fresh_final_generation["initial_error"] = _safe_model_error_summary(
                    exc,
                    profile=getattr(self.llm, "profile", None),
                )
                try:
                    model_final_output, final_draft = attempt_fresh_final_recovery(
                        initial_error=exc,
                        step=step,
                        source_stage=str(
                            exc.safe_diagnostics().get("failed_stage")
                            or "tool_response_extraction"
                        ),
                        finish_reason=exc.safe_diagnostics().get("finish_status"),
                    )
                except Exception as fallback_exc:
                    fresh_final_generation["fallback_error"] = _safe_model_error_summary(
                        fallback_exc,
                        profile=getattr(self.llm, "profile", None),
                    )
                    errors.append(
                        "model_output_incomplete_error: "
                        + _safe_model_error_summary(
                            exc,
                            profile=getattr(self.llm, "profile", None),
                        )
                        + "; fresh_final_error: "
                        + _safe_model_error_summary(
                            fallback_exc,
                            profile=getattr(self.llm, "profile", None),
                        )
                    )
                    stop_reason = "model_output_incomplete_error"
                    break
                if not finalization_announced:
                    set_phase("finalizing", step=step)
                    emit(
                        "finalization_started",
                        step=step,
                        data={"reason": "fresh_final_after_incomplete_output"},
                    )
                    finalization_announced = True
                emit(
                    "structured_output_received",
                    step=step,
                    data={
                        "section_count": len(final_draft.get("sections") or []),
                        "character_count": sum(
                            len(str(item.get("content") or ""))
                            for item in final_draft.get("sections") or []
                        ),
                    },
                )
                emit(
                    "chapter_draft_assembled",
                    step=step,
                    data={"section_count": len(final_draft.get("sections") or [])},
                )
                stop_reason = "fresh_final_after_incomplete_output_succeeded"
                break
            except Exception as exc:
                emit(
                    "model_call_failed",
                    step=step,
                    data={
                        "error_type": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - model_started) * 1000),
                    },
                )
                errors.append(f"model_error: {type(exc).__name__}: {str(exc)[:500]}")
                stop_reason = "model_error"
                break
            call_summary = _model_call_summary(step, model_result.request_metadata)
            model_calls.append(call_summary)
            emit(
                "model_call_completed",
                step=step,
                data={
                    **{key: call_summary.get(key) for key in (
                        "provider", "profile_name", "model", "api_mode", "request_id",
                        "duration_ms", "finish_status",
                    )},
                    "response_type": model_result.response_type,
                    "tool_call_count": len(model_result.tool_calls),
                },
            )
            emit("model_usage_updated", step=step, data=_sum_usage(model_calls))
            if model_result.response_type == "final":
                try:
                    model_final_output = parse_json_object(str(model_result.final_content or ""))
                    final_draft = normalize_chapter_draft_output(
                        model_final_output,
                        pending_confirmations=self.tool_context.pending_confirmations,
                    )
                    try:
                        Draft202012Validator(self.chapter_schema).validate(
                            final_draft
                        )
                    except ValidationError as schema_exc:
                        schema_path = ".".join(
                            str(item) for item in schema_exc.absolute_path
                        ) or "$"
                        raise ModelJsonParseError(
                            "model final output failed target Schema",
                            diagnostics={
                                "json_context": "final_chapter_content",
                                "schema_validation_error_summary": (
                                    f"schema validation failed at {schema_path}: "
                                    f"{schema_exc.message}"
                                )[:500],
                                "finish_reason": model_result.request_metadata.get(
                                    "finish_status"
                                ),
                            },
                            raw_content=str(model_result.final_content or ""),
                        ) from None
                except ModelJsonParseError as exc:
                    parse_failures += 1
                    trace.append(
                        {
                            "step": step,
                            "event": "invalid_final",
                            "status": "error",
                            "error": str(exc)[:300],
                        }
                    )
                    try:
                        model_final_output, final_draft = attempt_fresh_final_recovery(
                            initial_error=exc,
                            raw_content=str(model_result.final_content or ""),
                            step=step,
                            source_stage="final_chapter_content",
                            finish_reason=str(
                                model_result.request_metadata.get("finish_status") or ""
                            )
                            or None,
                        )
                    except ModelJsonParseError as repair_exc:
                        json_parse_failure = True
                        errors.append(
                            "model_json_parse_error: "
                            + _safe_model_error_summary(
                                repair_exc,
                                profile=getattr(self.llm, "profile", None),
                            )
                        )
                        stop_reason = "model_json_parse_error"
                        break
                if not finalization_announced:
                    set_phase("finalizing", step=step)
                    emit("finalization_started", step=step, data={"reason": "model_returned_final"})
                    finalization_announced = True
                emit(
                    "structured_output_received",
                    step=step,
                    data={
                        "section_count": len(final_draft.get("sections") or []),
                        "character_count": sum(
                            len(str(item.get("content") or ""))
                            for item in final_draft.get("sections") or []
                        ),
                    },
                )
                emit(
                    "chapter_draft_assembled",
                    step=step,
                    data={"section_count": len(final_draft.get("sections") or [])},
                )
                stop_reason = (
                    "fresh_final_after_json_failure_succeeded"
                    if fresh_final_generation["succeeded"]
                    else "model_final"
                )
                break

            messages.append(model_result.context_message())
            invalid_calls = 0
            recoverable_invalid_calls = 0
            tools_disabled_this_step: list[str] = []
            for call in model_result.tool_calls:
                name = str(call.get("name") or "")
                arguments = call.get("arguments") or {}
                call_id = str(call.get("tool_call_id") or "")
                arguments_summary = _safe_arguments(arguments)
                emit(
                    "tool_call_requested",
                    step=step,
                    data={
                        "tool_name": name,
                        "tool_call_id": call_id,
                        "arguments_summary": arguments_summary,
                    },
                )
                event = {
                    "step": step,
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "arguments_summary": arguments_summary,
                }
                signature = _tool_signature(name, arguments)
                signature_counts[signature] = signature_counts.get(signature, 0) + 1
                skip_reason = _tool_skip_reason(
                    name=name,
                    signature_count=signature_counts[signature],
                    allowed_names=allowed_names,
                    tool_counts=tool_counts,
                    max_search_calls=self.max_search_calls,
                    max_local_knowledge_calls=self.max_local_knowledge_calls,
                    max_web_search_calls=self.max_web_search_calls,
                    max_pending_confirmation_calls=self.max_pending_confirmation_calls,
                )
                if signature_counts[signature] > self.max_same_tool_repeats:
                    duplicate_tool_calls += 1
                    emit(
                        "duplicate_tool_call_detected",
                        step=step,
                        data={
                            "tool_name": name,
                            "tool_call_id": call_id,
                            "exact": True,
                            "repeat_count": signature_counts[signature],
                        },
                    )
                elif name in {"query_local_knowledge", "web_search"}:
                    query = _normalized_query(str(arguments.get("query") or ""))
                    similar = _similar_query_seen(name, query, query_signatures)
                    if similar:
                        duplicate_tool_calls += 1
                        warnings.append(f"low-gain similar query detected: {name}")
                        emit(
                            "duplicate_tool_call_detected",
                            step=step,
                            data={
                                "tool_name": name,
                                "tool_call_id": call_id,
                                "exact": False,
                                "similarity": similar,
                            },
                        )
                    query_signatures.append((name, query))
                if skip_reason:
                    tool_result = {
                        "status": "skipped",
                        "reason": skip_reason,
                        "instruction": "使用已有资料继续写作并尽快返回最终章节。",
                    }
                    event.update({"status": "skipped", "reason": skip_reason, "result_count": 0})
                    trace.append(event)
                    emit(
                        "tool_call_skipped",
                        step=step,
                        data={"tool_name": name, "tool_call_id": call_id, "reason": skip_reason},
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )
                    continue
                emit(
                    "tool_call_started",
                    step=step,
                    data={
                        "tool_name": name,
                        "tool_call_id": call_id,
                        "arguments_summary": arguments_summary,
                    },
                )
                tool_started = time.monotonic()
                try:
                    tool_result = self.registry.execute(name, arguments)
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                    if name in {
                        "query_local_knowledge",
                        "read_local_evidence",
                        "web_search",
                        "open_web_page",
                    }:
                        reusable_evidence_snapshots.append(
                            {
                                "tool_name": name,
                                "result": deepcopy(tool_result),
                            }
                        )
                    event.update(
                        {
                            "status": str(tool_result.get("status") or "success"),
                            "result_count": _result_count(tool_result),
                        }
                    )
                except (ToolExecutionError, PermissionError, ValueError, FileNotFoundError) as exc:
                    invalid_calls += 1
                    invalid_tool_argument_count += 1
                    invalid_tool_argument_count_by_tool[name] = (
                        invalid_tool_argument_count_by_tool.get(name, 0) + 1
                    )
                    recoverable_feedback = _recoverable_tool_argument_feedback(
                        tool_name=name,
                        error=exc,
                        error_count_for_tool=invalid_tool_argument_count_by_tool[name],
                    )
                    if recoverable_feedback is not None:
                        recoverable_invalid_calls += 1
                        tool_result = recoverable_feedback
                        event.update({
                            "status": "error",
                            "error": str(exc)[:300],
                            "error_code": recoverable_feedback["error_code"],
                            "recoverable": True,
                            "result_count": 0,
                        })
                        if (
                            invalid_tool_argument_count_by_tool[name]
                            >= RECOVERABLE_TOOL_ARGUMENT_ERROR_THRESHOLD
                            and name not in disabled_tools
                        ):
                            disabled_tools.add(name)
                            force_final_after_recoverable_tool_error = True
                            tools_disabled_this_step.append(name)
                            emit(
                                "recoverable_tool_disabled",
                                step=step,
                                data={
                                    "tool_name": name,
                                    "invalid_tool_argument_count": (
                                        invalid_tool_argument_count_by_tool[name]
                                    ),
                                    "threshold": RECOVERABLE_TOOL_ARGUMENT_ERROR_THRESHOLD,
                                    "next_action": "force_finalization",
                                },
                            )
                    else:
                        tool_result = {"status": "error", "error": str(exc)[:500]}
                        event.update({
                            "status": "error",
                            "error": str(exc)[:300],
                            "recoverable": False,
                            "result_count": 0,
                        })
                    emit(
                        "tool_call_failed",
                        step=step,
                        data={
                            "tool_name": name,
                            "tool_call_id": call_id,
                            "error_type": type(exc).__name__,
                            "recoverable": recoverable_feedback is not None,
                            "error_code": (
                                recoverable_feedback.get("error_code")
                                if recoverable_feedback is not None
                                else None
                            ),
                            "duration_ms": int((time.monotonic() - tool_started) * 1000),
                        },
                    )
                else:
                    emit(
                        "tool_call_completed",
                        step=step,
                        data={
                            "tool_name": name,
                            "tool_call_id": call_id,
                            "status": event["status"],
                            "duration_ms": int((time.monotonic() - tool_started) * 1000),
                            "result_count": event["result_count"],
                            "result_summary": _safe_result_summary(tool_result),
                            "whether_cached": False,
                        },
                    )
                    if name == "save_section_draft":
                        if not writing_announced:
                            set_phase("writing", step=step)
                            emit("writing_started", step=step, data={"target_id": self.tool_context._target_id})
                            writing_announced = True
                        emit(
                            "section_draft_saved",
                            step=step,
                            data={
                                "section_count_completed": len(self.tool_context.saved_sections),
                                "section_count_expected": expected_section_count,
                                "character_count": len(self.tool_context.working_draft_markdown),
                                "storage": "runtime_memory",
                            },
                        )
                    elif name == "record_pending_confirmation":
                        emit(
                            "pending_confirmation_recorded",
                            step=step,
                            data={"pending_count": len(self.tool_context.pending_confirmations)},
                        )
                    if name in {
                        "query_local_knowledge",
                        "record_pending_confirmation",
                    }:
                        refresh_section_fact_ledger()
                trace.append(event)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )
            for disabled_tool in tools_disabled_this_step:
                messages.append({
                    "role": "developer",
                    "content": _tool_error_finalization_instruction(
                        disabled_tool=disabled_tool
                    ),
                })
            if invalid_calls == len(model_result.tool_calls) and invalid_calls:
                if recoverable_invalid_calls == invalid_calls:
                    warnings.append(
                        "recoverable auxiliary tool argument error; continuing toward finalization"
                    )
                else:
                    parse_failures += 1
                    if parse_failures >= self.max_parse_failures:
                        errors.append("model repeatedly issued invalid non-recoverable tool calls")
                        stop_reason = "invalid_tool_arguments"
                        break

        candidate_evidence_ids = {
            str(
                item.get("candidate_id")
                or item.get("evidence_id")
                or item.get("file_id")
                or ""
            )
            for item in self.tool_context.local_evidence_refs
            if item.get("selection_status") != "confirmed"
        }
        confirmed_evidence_ids = {
            str(
                item.get("candidate_id")
                or item.get("evidence_id")
                or item.get("file_id")
                or ""
            )
            for item in self.tool_context.local_evidence_refs
            if item.get("selection_status") == "confirmed"
        }
        web_verification = _web_verification_state(
            policy=self.web_search_policy,
            attempts=self.tool_context.web_search_attempts,
            page_reads=self.tool_context.web_page_reads,
            web_refs=self.tool_context.web_refs,
        )
        self_review = {
            "duplicate_section_pairs": [],
            "revisions_applied": [],
            "repair_attempted": False,
            "repair_count": 0,
        }
        personnel_content_gate = {
            "applied": False,
            "source_only_section_ids": [],
            "removed_character_count": 0,
        }
        deterministic_format_normalization = {
            "canonical_title_normalization_count": 0,
            "table_intro_normalization_count": 0,
        }
        if final_draft is not None:
            final_draft, deterministic_format_normalization = (
                _normalize_candidate_format_before_validation(
                    final_draft,
                    target=self.tool_context._target,
                    tender_tables=self.tool_context.tender_tables,
                )
            )
            fresh_quality_regeneration.update(
                deterministic_format_normalization
            )
            final_draft, personnel_content_gate = (
                _apply_personnel_content_gate(
                    final_draft,
                    fact_ledger=section_fact_ledger,
                )
            )
        quality_review = (
            review_chapter_quality(
                final_draft,
                tender_tables=self.tool_context.tender_tables,
                source_only_personnel_section_ids=(
                    _source_only_personnel_section_references(
                        section_fact_ledger
                    )
                ),
            )
            if final_draft is not None
            else _empty_quality_review()
        )
        initial_candidate_draft = deepcopy(final_draft)
        initial_candidate_output = deepcopy(model_final_output)
        initial_candidate_quality_review = deepcopy(quality_review)
        if final_draft is not None:
            initial_candidate_sha256 = _candidate_sha256(final_draft)
            initial_validation_result = validate_technical_chapter(
                final_draft,
                schema=self.chapter_schema,
                target=self.tool_context._target,
                confirmed_facts=self.tool_context.get_confirmed_project_facts(
                    {}
                ).get("facts")
                or {},
                pending_confirmations=self.tool_context.pending_confirmations,
                web_search_policy=self.web_search_policy,
                web_verification=web_verification,
                tender_tables=self.tool_context.tender_tables,
                quality_review=quality_review,
                candidate_evidence_ids=candidate_evidence_ids,
                confirmed_evidence_ids=confirmed_evidence_ids,
                generation_permissions=section_fact_ledger,
            )
            initial_candidate_validation = _candidate_validation_snapshot(
                validation=initial_validation_result,
                quality_review=quality_review,
                draft=final_draft,
                expected_sections=_expected_section_specs(
                    self.tool_context._target
                ),
            )
        if (
            final_draft is not None
            and self.max_auto_repair_rounds > 0
            and not runtime_complete_working_draft_finalized
        ):
            initial_quality_errors = list(
                (initial_candidate_validation or {}).get("errors") or []
            )
            initial_auto_messages = {
                str(item.get("message") or "")
                for item in classify_chapter_audit(
                    initial_candidate_validation or {},
                    pending_confirmations=self.tool_context.pending_confirmations,
                    prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                    local_evidence_refs=self.tool_context.local_evidence_refs,
                ).get("auto_repairable")
                or []
            }
            regeneration_trigger_errors = _fresh_regeneration_repair_targets(
                [
                    error
                    for error in initial_quality_errors
                    if error in initial_auto_messages
                ]
            )
            if regeneration_trigger_errors:
                first_error_details = _fresh_quality_error_details(
                    initial_quality_errors,
                    draft=final_draft,
                    expected_sections=_expected_section_specs(
                        self.tool_context._target
                    ),
                )
                fresh_repair_context = _build_fresh_repair_context(
                    validation=initial_candidate_validation or {},
                    draft=final_draft,
                    fact_ledger=section_fact_ledger,
                    pending_confirmations=self.tool_context.pending_confirmations,
                    target=self.tool_context._target,
                    prohibited_material_ids=(
                        self.tool_context.prohibited_material_ids_detected
                    ),
                    local_evidence_refs=self.tool_context.local_evidence_refs,
                )
                fresh_quality_regeneration.update(
                    {
                        "attempted": True,
                        "quality_errors": _fresh_quality_error_summary(
                            initial_quality_errors
                        ),
                        "first_error_details": first_error_details,
                        "first_error_count": len(first_error_details),
                        "first_raw_error_count": len(initial_quality_errors),
                        "first_grouped_error_count": len(first_error_details),
                        "kb_calls_reused": tool_counts.get(
                            "query_local_knowledge", 0
                        ),
                        "web_results_reused": len(self.tool_context.web_refs),
                        "repair_input_summary": deepcopy(
                            fresh_repair_context["repair_input_summary"]
                        ),
                        "auto_repair_issue_codes": list(
                            fresh_repair_context["auto_repair_issue_codes"]
                        ),
                        "review_only_issue_codes": list(
                            fresh_repair_context["review_only_issue_codes"]
                        ),
                        "preserved_placeholder_ids": list(
                            fresh_repair_context["preserved_placeholder_ids"]
                        ),
                        "preserved_tender_fact_ids": list(
                            fresh_repair_context["preserved_tender_fact_ids"]
                        ),
                        "fresh_edit_scope": deepcopy(
                            fresh_repair_context["fresh_edit_scope"]
                        ),
                        "fresh_patch": _fresh_patch_report_template(
                            base_candidate_hash=str(
                                fresh_repair_context.get(
                                    "initial_candidate_sha256"
                                )
                                or ""
                            ),
                            repair_task_count=len(
                                fresh_repair_context.get("repair_task_cards")
                                or []
                            ),
                        ),
                        "base_candidate_hash": str(
                            fresh_repair_context.get(
                                "initial_candidate_sha256"
                            )
                            or ""
                        ),
                        "repair_task_count": len(
                            fresh_repair_context.get("repair_task_cards") or []
                        ),
                        "section_identity_map": deepcopy(
                            fresh_repair_context["section_identity_map"]
                        ),
                        "section_identity_issues": deepcopy(
                            fresh_repair_context["section_identity_issues"]
                        ),
                    }
                )
                regen_step = model_step_count + 1
                emit(
                    "fresh_quality_regeneration_started",
                    step=regen_step,
                    data={
                        "quality_errors": fresh_quality_regeneration[
                            "quality_errors"
                        ],
                        "error_details": first_error_details,
                        "tools_available": 1,
                    },
                )
                regen_started = time.monotonic()
                first_repair_model_calls: list[dict[str, Any]] = []
                first_repair_tool_calls: list[dict[str, Any]] = []
                regen_model_call_completed = False
                regen_model_call_event_completed = False
                structured_patch_response_event_emitted = False
                try:
                    fresh_patch_messages = _fresh_structured_patch_messages(
                        fresh_repair_context
                    )
                    emit(
                        "model_call_started",
                        step=regen_step,
                        data={
                            "purpose": "fresh_structured_patch",
                            "profile_name": self.profile_name,
                            "available_tool_count": 1,
                            "forced_tool_name": "submit_structured_patch",
                        },
                    )
                    regen_result = self.llm.run_with_tools(
                        messages=fresh_patch_messages,
                        tools=[_submit_structured_patch_tool_definition()],
                        tool_choice=_forced_structured_patch_tool_choice(),
                        options={"stream": False},
                        request_metadata={
                            "target_id": self.tool_context._target_id,
                            "agent_step": regen_step,
                            "purpose": "fresh_structured_patch",
                            "output_key": self.tool_context.output_key,
                        },
                        profile_name=self.profile_name,
                    )
                    regen_model_call_completed = True
                    regen_summary = _model_call_summary(
                        regen_step, regen_result.request_metadata
                    )
                    regen_summary["purpose"] = "fresh_structured_patch"
                    first_repair_model_calls = [regen_summary]
                    model_calls.append(regen_summary)
                    # A completed model call counts as a model step even when its
                    # payload later fails the structured patch protocol.
                    model_step_count = regen_step
                    emit(
                        "model_call_completed",
                        step=regen_step,
                        data={
                            "purpose": "fresh_structured_patch",
                            "profile_name": self.profile_name,
                            "available_tool_count": 1,
                            "forced_tool_name": "submit_structured_patch",
                            "response_type": regen_result.response_type,
                            "tool_call_count": len(regen_result.tool_calls),
                            "duration_ms": int(
                                (time.monotonic() - regen_started) * 1000
                            ),
                        },
                    )
                    regen_model_call_event_completed = True
                    emit(
                        "model_usage_updated",
                        step=regen_step,
                        data=_sum_usage(model_calls),
                    )
                    emit(
                        "structured_patch_response_received",
                        step=regen_step,
                        data={
                            "purpose": "fresh_structured_patch",
                            "response_type": regen_result.response_type,
                            "tool_call_count": len(regen_result.tool_calls),
                        },
                    )
                    structured_patch_response_event_emitted = True
                    patch_payload = _fresh_patch_payload_from_tool_result(
                        regen_result
                    )
                    regen_argument_repairs = _record_tool_argument_json_repairs(
                        tool_calls=regen_result.tool_calls,
                        step=regen_step,
                        output_root=self.tool_context.output_root,
                        output_key=self.tool_context.output_key,
                        target_id=self.tool_context._target_id,
                        secret=str(
                            getattr(getattr(self.llm, "profile", None), "api_key", "")
                            or ""
                        ),
                        emit=emit,
                    )
                    tool_arguments_json_repairs.extend(regen_argument_repairs)
                    emit(
                        "structured_patch_protocol_validated",
                        step=regen_step,
                        data={
                            "purpose": "fresh_structured_patch",
                            "tool_name": "submit_structured_patch",
                            "repair_result_count": len(
                                patch_payload.get("repair_results") or []
                            ),
                        },
                    )

                    def validate_patch_candidate(
                        candidate: dict[str, Any],
                    ) -> dict[str, Any]:
                        candidate_review = review_chapter_quality(
                            candidate,
                            tender_tables=self.tool_context.tender_tables,
                            source_only_personnel_section_ids=(
                                _source_only_personnel_section_references(
                                    section_fact_ledger
                                )
                            ),
                        )
                        candidate_validation = validate_technical_chapter(
                            candidate,
                            schema=self.chapter_schema,
                            target=self.tool_context._target,
                            confirmed_facts=self.tool_context.get_confirmed_project_facts(
                                {}
                            ).get("facts")
                            or {},
                            pending_confirmations=self.tool_context.pending_confirmations,
                            web_search_policy=self.web_search_policy,
                            web_verification=web_verification,
                            tender_tables=self.tool_context.tender_tables,
                            quality_review=candidate_review,
                            candidate_evidence_ids=candidate_evidence_ids,
                            confirmed_evidence_ids=confirmed_evidence_ids,
                            generation_permissions=section_fact_ledger,
                        )
                        return _candidate_validation_snapshot(
                            validation=candidate_validation,
                            quality_review=candidate_review,
                            draft=candidate,
                            expected_sections=fresh_repair_context.get(
                                "expected_sections"
                            )
                            or [],
                        )

                    regenerated_draft, fresh_patch_report = (
                        _apply_validated_fresh_patches(
                            initial=initial_candidate_draft,
                            patch_payload=patch_payload,
                            repair_context=fresh_repair_context,
                            fact_ledger=section_fact_ledger,
                            initial_validation=initial_candidate_validation
                            or {},
                            validate_candidate=validate_patch_candidate,
                        )
                    )
                    fresh_patch_report.update(
                        {
                            "response_type": "tool_calls",
                            "tool_call_count": 1,
                            "forced_tool_name": "submit_structured_patch",
                            "protocol_validated": True,
                            "json_syntax_repair_applied": bool(
                                regen_argument_repairs
                            ),
                            "json_syntax_repair": deepcopy(
                                regen_argument_repairs[-1]
                                if regen_argument_repairs
                                else {
                                    "attempted": False,
                                    "applied": False,
                                    "repair_type": None,
                                    "repair_count": 0,
                                    "repair_positions": [],
                                    "repaired_response_path": None,
                                }
                            ),
                        }
                    )
                    fresh_quality_regeneration["fresh_patch"] = (
                        fresh_patch_report
                    )
                    for report_field in (
                        "fresh_repair_mode",
                        "base_candidate_hash",
                        "repair_task_count",
                        "repair_candidate_count",
                        "tasks_with_one_candidate",
                        "tasks_with_two_candidates",
                        "tasks_with_three_candidates",
                        "tasks_missing_conservative_candidate",
                        "duplicate_candidate_count",
                        "exact_locator_patch_count",
                        "local_guarded_delete_candidate_count",
                        "local_guarded_delete_applied_count",
                        "applied_operation_count",
                        "rejected_candidate_count",
                        "unresolved_repair_task_count",
                        "applied_operations",
                        "unresolved_issue_fingerprints",
                    ):
                        fresh_quality_regeneration[report_field] = deepcopy(
                            fresh_patch_report.get(report_field)
                        )
                    if fresh_patch_report.get("protocol_issue_code"):
                        raise _FreshPatchProtocolError(
                            str(fresh_patch_report["protocol_issue_code"]),
                            response_type="tool_calls",
                            tool_call_count=1,
                        )
                    fresh_personnel_gate = deepcopy(personnel_content_gate)
                    regenerated_review = review_chapter_quality(
                        regenerated_draft,
                        tender_tables=self.tool_context.tender_tables,
                        source_only_personnel_section_ids=(
                            _source_only_personnel_section_references(
                                section_fact_ledger
                            )
                        ),
                    )
                    fresh_validation_result = validate_technical_chapter(
                        regenerated_draft,
                        schema=self.chapter_schema,
                        target=self.tool_context._target,
                        confirmed_facts=self.tool_context.get_confirmed_project_facts(
                            {}
                        ).get("facts")
                        or {},
                        pending_confirmations=self.tool_context.pending_confirmations,
                        web_search_policy=self.web_search_policy,
                        web_verification=web_verification,
                        tender_tables=self.tool_context.tender_tables,
                        quality_review=regenerated_review,
                        candidate_evidence_ids=candidate_evidence_ids,
                        confirmed_evidence_ids=confirmed_evidence_ids,
                        generation_permissions=section_fact_ledger,
                    )
                    fresh_candidate_sha256 = _candidate_sha256(
                        regenerated_draft
                    )
                    fresh_candidate_validation = _candidate_validation_snapshot(
                        validation=fresh_validation_result,
                        quality_review=regenerated_review,
                        draft=regenerated_draft,
                        expected_sections=fresh_repair_context.get(
                            "expected_sections"
                        )
                        or [],
                    )
                    fresh_diff_audit = _audit_fresh_candidate_diff(
                        initial=initial_candidate_draft,
                        fresh=regenerated_draft,
                        repair_context=fresh_repair_context,
                        fact_ledger=section_fact_ledger,
                    )
                    fresh_quality_regeneration["fresh_diff_audit"] = (
                        fresh_diff_audit
                    )
                    fresh_quality_regeneration["section_identity_issues"] = (
                        deepcopy(fresh_diff_audit.get("section_identity_issues") or [])
                    )
                    fresh_quality_regeneration["generated"] = bool(
                        fresh_candidate_validation.get("schema_valid")
                    )
                    fresh_quality_regeneration["validated"] = True
                    fresh_quality_regeneration["succeeded"] = bool(
                        fresh_quality_regeneration["generated"]
                        and fresh_quality_regeneration["validated"]
                    )
                    remaining_quality_errors = list(
                        fresh_candidate_validation.get("errors") or []
                    )
                    remaining_error_details = _fresh_quality_error_details(
                        remaining_quality_errors,
                        draft=regenerated_draft,
                        expected_sections=fresh_repair_context.get(
                            "expected_sections"
                        )
                        or [],
                    )
                    fresh_quality_regeneration[
                        "remaining_error_details"
                    ] = remaining_error_details
                    fresh_quality_regeneration[
                        "remaining_error_count"
                    ] = len(remaining_error_details)
                    fresh_quality_regeneration[
                        "remaining_raw_error_count"
                    ] = len(remaining_quality_errors)
                    fresh_quality_regeneration[
                        "remaining_grouped_error_count"
                    ] = len(remaining_error_details)
                    fresh_quality_regeneration[
                        "remaining_validation_status"
                    ] = "completed"
                    fresh_quality_regeneration["quality_errors"] = (
                        _fresh_quality_error_summary(remaining_quality_errors)
                    )
                    (
                        fresh_candidate_accepted,
                        candidate_selection_reason,
                    ) = _fresh_candidate_is_monotonic_improvement(
                        initial=initial_candidate_validation or {},
                        fresh=fresh_candidate_validation,
                    )
                    candidate_selection_audit = _candidate_selection_audit(
                        initial=initial_candidate_validation or {},
                        fresh=fresh_candidate_validation,
                    )
                    fresh_quality_regeneration[
                        "candidate_selection_audit"
                    ] = candidate_selection_audit
                    # Keep the audit fields directly discoverable in the existing
                    # Fresh report while retaining the grouped object above.
                    fresh_quality_regeneration.update(candidate_selection_audit)
                    if (
                        fresh_candidate_accepted
                        and not fresh_diff_audit.get("passed")
                    ):
                        fresh_candidate_accepted = False
                        candidate_selection_reason = (
                            "section_identity_resolution_failed"
                            if not fresh_diff_audit.get(
                                "section_identity_consistent", True
                            )
                            else "fresh_unauthorized_section_change"
                            if fresh_diff_audit.get(
                                "unauthorized_changed_section_ids"
                            )
                            else "fresh_diff_audit_new_fact_outside_issue_scope"
                        )
                    fresh_quality_regeneration[
                        "fresh_candidate_accepted"
                    ] = fresh_candidate_accepted
                    fresh_quality_regeneration[
                        "candidate_selection_reason"
                    ] = candidate_selection_reason
                    fresh_quality_regeneration["accepted"] = bool(
                        fresh_candidate_accepted
                    )
                    fresh_quality_regeneration["rejected_reason"] = (
                        None
                        if fresh_candidate_accepted
                        else candidate_selection_reason
                    )
                    fresh_quality_regeneration["elapsed_time_ms"] = int(
                        (time.monotonic() - regen_started) * 1000
                    )
                    if fresh_candidate_accepted:
                        model_final_output = deepcopy(regenerated_draft)
                        final_draft = regenerated_draft
                        quality_review = regenerated_review
                        personnel_content_gate = fresh_personnel_gate
                        selected_candidate = "fresh"
                        stop_reason = (
                            "fresh_quality_regeneration_succeeded"
                            if fresh_validation_result.get("valid")
                            else "fresh_candidate_selected_for_diagnostics"
                        )
                    else:
                        model_final_output = deepcopy(initial_candidate_output)
                        final_draft = deepcopy(initial_candidate_draft)
                        quality_review = deepcopy(
                            initial_candidate_quality_review
                        )
                        selected_candidate = "initial"
                    emit(
                        "fresh_quality_regeneration_completed",
                        step=regen_step,
                        data={
                            "succeeded": fresh_quality_regeneration["succeeded"],
                            "generated": fresh_quality_regeneration["generated"],
                            "validated": fresh_quality_regeneration["validated"],
                            "accepted": fresh_quality_regeneration["accepted"],
                            "rejected_reason": fresh_quality_regeneration[
                                "rejected_reason"
                            ],
                            "fresh_candidate_accepted": fresh_candidate_accepted,
                            "selected_candidate": selected_candidate,
                            "candidate_selection_reason": candidate_selection_reason,
                            "remaining_error_count": len(
                                remaining_error_details
                            ),
                            "duration_ms": int(
                                (time.monotonic() - regen_started) * 1000
                            ),
                        },
                    )
                except Exception as exc:
                    protocol_error: _FreshPatchProtocolError | None = None
                    failed_argument_repair: dict[str, Any] | None = None
                    if isinstance(exc, _FreshPatchProtocolError):
                        protocol_error = exc
                    elif isinstance(exc, ModelJsonParseError):
                        failed_argument_repair = (
                            _record_failed_tool_argument_json_repair(
                                error=exc,
                                step=regen_step,
                                emit=emit,
                            )
                        )
                        if failed_argument_repair:
                            tool_arguments_json_repairs.append(
                                failed_argument_repair
                            )
                        protocol_error = _FreshPatchProtocolError(
                            "fresh_patch_arguments_invalid_json",
                            raw_content=str(exc.raw_content or ""),
                            response_type="tool_calls",
                            tool_call_count=1,
                            parse_error_type="ModelJsonParseError",
                        )
                    if protocol_error is not None:
                        model_step_count = regen_step
                        raw_content = protocol_error.raw_content
                        diagnostics = (
                            exc.safe_diagnostics()
                            if isinstance(exc, ModelJsonParseError)
                            else {}
                        )
                        suspected_truncation = bool(
                            diagnostics.get("suspected_truncation")
                            or suspected_json_truncation(
                                raw_content,
                                finish_reason=diagnostics.get("finish_reason"),
                                usage=diagnostics.get("usage"),
                                error_summary=str(
                                    diagnostics.get("json_error_summary") or ""
                                ),
                            )
                        )
                        debug = _save_invalid_model_output(
                            output_root=self.tool_context.output_root,
                            output_key=self.tool_context.output_key,
                            target_id=self.tool_context._target_id,
                            step=regen_step,
                            content=raw_content,
                            secret=str(
                                getattr(
                                    getattr(self.llm, "profile", None),
                                    "api_key",
                                    "",
                                )
                                or ""
                            ),
                        )
                        safe_error = _safe_model_error_summary(
                            exc,
                            profile=getattr(self.llm, "profile", None),
                        )
                        fresh_patch_report = _fresh_patch_protocol_failure_report(
                            current=fresh_quality_regeneration.get("fresh_patch")
                            or _fresh_patch_report_template(
                                base_candidate_hash=str(
                                    initial_candidate_sha256 or ""
                                ),
                                repair_task_count=len(
                                    fresh_repair_context.get("repair_task_cards")
                                    or []
                                ),
                            ),
                            repair_context=fresh_repair_context,
                            code=protocol_error.code,
                            response_type=protocol_error.response_type,
                            tool_call_count=protocol_error.tool_call_count,
                            parse_error_type=(
                                protocol_error.parse_error_type
                                or type(exc).__name__
                            ),
                            raw_content=raw_content,
                            debug=debug,
                            suspected_truncation=suspected_truncation,
                            safe_error_summary=safe_error,
                        )
                        fresh_quality_regeneration["fresh_patch"] = (
                            fresh_patch_report
                        )
                        if failed_argument_repair:
                            fresh_patch_report["json_syntax_repair"] = deepcopy(
                                failed_argument_repair
                            )
                            fresh_patch_report[
                                "json_syntax_repair_applied"
                            ] = False
                        for report_field in (
                            "fresh_repair_mode",
                            "base_candidate_hash",
                            "repair_task_count",
                            "repair_candidate_count",
                            "tasks_with_one_candidate",
                            "tasks_with_two_candidates",
                            "tasks_with_three_candidates",
                            "tasks_missing_conservative_candidate",
                            "duplicate_candidate_count",
                            "applied_operation_count",
                            "rejected_candidate_count",
                            "unresolved_repair_task_count",
                            "applied_operations",
                            "unresolved_issue_fingerprints",
                        ):
                            fresh_quality_regeneration[report_field] = deepcopy(
                                fresh_patch_report.get(report_field)
                            )
                        fresh_quality_regeneration["model_call_failed"] = False
                        fresh_quality_regeneration[
                            "model_response_parse_failed"
                        ] = bool(
                            fresh_patch_report.get(
                                "model_response_parse_failed"
                            )
                        )
                        fresh_quality_regeneration[
                            "rejected_reason"
                        ] = protocol_error.code
                        fresh_quality_regeneration[
                            "failure_reason"
                        ] = protocol_error.code
                        fresh_quality_regeneration[
                            "remaining_validation_status"
                        ] = "not_run"
                        fresh_quality_regeneration[
                            "remaining_raw_error_count"
                        ] = None
                        fresh_quality_regeneration[
                            "remaining_grouped_error_count"
                        ] = None
                        fresh_quality_regeneration[
                            "remaining_error_count"
                        ] = None
                        fresh_quality_regeneration[
                            "remaining_error_details"
                        ] = None
                        candidate_selection_reason = (
                            "fresh_candidate_not_generated"
                        )
                        if not regen_model_call_event_completed:
                            emit(
                                "model_call_completed",
                                step=regen_step,
                                data={
                                    "purpose": "fresh_structured_patch",
                                    "profile_name": self.profile_name,
                                    "available_tool_count": 1,
                                    "forced_tool_name": "submit_structured_patch",
                                    "response_type": protocol_error.response_type,
                                    "tool_call_count": protocol_error.tool_call_count,
                                    "duration_ms": int(
                                        (time.monotonic() - regen_started) * 1000
                                    ),
                                },
                            )
                            emit(
                                "model_usage_updated",
                                step=regen_step,
                                data=(
                                    diagnostics.get("usage")
                                    or _sum_usage(model_calls)
                                ),
                            )
                        if not structured_patch_response_event_emitted:
                            emit(
                                "structured_patch_response_received",
                                step=regen_step,
                                data={
                                    "purpose": "fresh_structured_patch",
                                    "response_type": protocol_error.response_type,
                                    "tool_call_count": protocol_error.tool_call_count,
                                },
                            )
                    else:
                        fresh_quality_regeneration["model_call_failed"] = not (
                            regen_model_call_completed
                        )
                    fresh_quality_regeneration["elapsed_time_ms"] = int(
                        (time.monotonic() - regen_started) * 1000
                    )
                    warnings.append(
                        "fresh quality regeneration was not applied: "
                        f"{type(exc).__name__}"
                    )
                    emit(
                        "fresh_quality_regeneration_failed",
                        step=regen_step,
                        data={
                            "error_type": type(exc).__name__,
                            "protocol_issue_code": (
                                protocol_error.code
                                if protocol_error is not None
                                else None
                            ),
                            "model_call_failed": fresh_quality_regeneration[
                                "model_call_failed"
                            ],
                            "model_response_parse_failed": (
                                fresh_quality_regeneration[
                                    "model_response_parse_failed"
                                ]
                            ),
                        },
                    )
        structured_patch_still_blocked = (
            based_on_existing_draft
            and fresh_quality_regeneration.get("attempted")
            and fresh_quality_regeneration.get("validated")
            and not fresh_candidate_accepted
            and bool((fresh_candidate_validation or {}).get("errors"))
        )
        initial_candidate_needs_clean_regeneration = (
            based_on_existing_draft
            and self.max_auto_repair_rounds > 0
            and not fresh_quality_regeneration.get("attempted")
            and bool(
                _fresh_regeneration_repair_targets(
                    list((initial_candidate_validation or {}).get("errors") or [])
                )
            )
        )
        if structured_patch_still_blocked or initial_candidate_needs_clean_regeneration:
            recovery_source_validation = (
                fresh_candidate_validation
                if structured_patch_still_blocked
                else initial_candidate_validation
            )
            recovery_source_stage = (
                "structured_patch_validation_blocked"
                if structured_patch_still_blocked
                else "initial_validation_blocked"
            )
            fresh_final_step = model_step_count + 1
            fresh_final_generation["trigger_errors"] = list(
                (recovery_source_validation or {}).get("errors") or []
            )
            try:
                recovered_output, recovered_draft = attempt_fresh_final_recovery(
                    initial_error=None,
                    step=fresh_final_step,
                    source_stage=recovery_source_stage,
                    finish_reason=None,
                )
            except ModelError as exc:
                fresh_final_generation["fallback_error"] = (
                    _safe_model_error_summary(
                        exc,
                        profile=getattr(self.llm, "profile", None),
                    )
                )
                warnings.append(
                    "targeted fresh final recovery was not applied: "
                    f"{type(exc).__name__}"
                )
            else:
                recovered_draft, recovered_format_normalization = (
                    _normalize_candidate_format_before_validation(
                        recovered_draft,
                        target=self.tool_context._target,
                        tender_tables=self.tool_context.tender_tables,
                    )
                )
                recovered_draft, recovered_personnel_gate = (
                    _apply_personnel_content_gate(
                        recovered_draft,
                        fact_ledger=section_fact_ledger,
                    )
                )
                model_final_output = recovered_output
                final_draft = recovered_draft
                quality_review = review_chapter_quality(
                    recovered_draft,
                    tender_tables=self.tool_context.tender_tables,
                    source_only_personnel_section_ids=(
                        _source_only_personnel_section_references(
                            section_fact_ledger
                        )
                    ),
                )
                personnel_content_gate = recovered_personnel_gate
                fresh_quality_regeneration.update(
                    recovered_format_normalization
                )
                model_step_count = fresh_final_step
                selected_candidate = "fresh_final_recovery"
                candidate_selection_reason = recovery_source_stage
                stop_reason = (
                    "fresh_final_after_blocked_repair_succeeded"
                    if structured_patch_still_blocked
                    else "fresh_final_after_blocked_validation_succeeded"
                )
        if fresh_quality_regeneration.get("attempted"):
            before_audit = classify_chapter_audit(
                initial_candidate_validation or {},
                pending_confirmations=self.tool_context.pending_confirmations,
                prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                local_evidence_refs=self.tool_context.local_evidence_refs,
            )
            after_snapshot = (
                fresh_candidate_validation
                if fresh_candidate_accepted
                else initial_candidate_validation
            ) or {}
            after_audit = classify_chapter_audit(
                after_snapshot,
                pending_confirmations=self.tool_context.pending_confirmations,
                prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                local_evidence_refs=self.tool_context.local_evidence_refs,
            )
            before_ids = {
                item.get("finding_id")
                for item in before_audit.get("auto_repairable") or []
            }
            after_ids = {
                item.get("finding_id")
                for item in after_audit.get("auto_repairable") or []
            }
            automatic_repair_rounds.append(
                {
                    "repair_round": 1,
                    "mode": (
                        "multi_candidate_structured_patch"
                    ),
                    "input_draft_hash": initial_candidate_sha256,
                    "audit_findings_before_repair": before_audit,
                    "repair_instruction": (
                        _fresh_quality_regeneration_instruction(
                            schema=_fresh_patch_response_schema(),
                            repair_context=fresh_repair_context,
                        )
                    ),
                    "output_draft_hash": (
                        fresh_candidate_sha256
                        if fresh_candidate_accepted
                        else initial_candidate_sha256
                    ),
                    "audit_findings_after_repair": after_audit,
                    "fixed_finding_ids": sorted(
                        value for value in before_ids - after_ids if value
                    ),
                    "unresolved_finding_ids": sorted(
                        value for value in after_ids if value
                    ),
                    "model_usage": _sum_usage(
                        first_repair_model_calls
                    ),
                    "tool_calls": first_repair_tool_calls,
                    "elapsed_time_ms": int(
                        fresh_quality_regeneration.get("elapsed_time_ms") or 0
                    ),
                    "accepted": bool(fresh_candidate_accepted),
                    "writing_rules_version": writing_rules.version,
                    "writing_rules_hash": writing_rules.sha256,
                    "project_constraints_version": project_constraints.get(
                        "version"
                    )
                    or "project_constraints_v0",
                    "project_constraints_hash": _json_sha256(
                        project_constraints.get("constraints") or []
                    ),
                    "user_instruction_hash": _text_sha256(
                        self.user_instruction
                    ),
                    "model_profile": self.profile_name
                    or getattr(
                        getattr(self.llm, "profile", None),
                        "name",
                        None,
                    ),
                    "output_key": self.tool_context.output_key,
                }
            )

        repair_baseline_validation = (
            fresh_candidate_validation
            if fresh_candidate_accepted
            else initial_candidate_validation
        ) or {}
        remaining_auto_errors: list[str] = []
        if repair_baseline_validation:
            remaining_raw_errors = list(
                repair_baseline_validation.get("errors") or []
            )
            remaining_auto_messages = {
                str(item.get("message") or "")
                for item in classify_chapter_audit(
                    repair_baseline_validation,
                    pending_confirmations=self.tool_context.pending_confirmations,
                    prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                    local_evidence_refs=self.tool_context.local_evidence_refs,
                ).get("auto_repairable")
                or []
            }
            remaining_auto_errors = [
                error
                for error in remaining_raw_errors
                if error in remaining_auto_messages
            ]
        if (
            final_draft is not None
            and not runtime_complete_working_draft_finalized
            and len(automatic_repair_rounds)
            < self.max_auto_repair_rounds
            and _fresh_regeneration_repair_targets(remaining_auto_errors)
        ):
            repair_round = 2
            repair_step = model_step_count + 1
            repair_started = time.monotonic()
            input_draft = deepcopy(final_draft)
            input_hash = _candidate_sha256(input_draft)
            before_validation = deepcopy(repair_baseline_validation)
            before_audit = classify_chapter_audit(
                before_validation,
                pending_confirmations=self.tool_context.pending_confirmations,
                prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                local_evidence_refs=self.tool_context.local_evidence_refs,
            )
            error_details = _fresh_quality_error_details(
                remaining_auto_errors,
                draft=input_draft,
            )
            repair_instruction = _targeted_auto_repair_instruction(
                draft=input_draft,
                audit_findings=before_audit.get("auto_fixable") or [],
                error_details=error_details,
                schema=self.chapter_schema,
                user_instruction=self.user_instruction,
            )
            emit(
                "automatic_repair_started",
                step=repair_step,
                data={
                    "repair_round": repair_round,
                    "finding_count": len(
                        before_audit.get("auto_fixable") or []
                    ),
                    "tools_available": 0,
                },
            )
            accepted = False
            output_hash = input_hash
            after_validation = before_validation
            after_audit = before_audit
            repair_error: str | None = None
            repair_model_call: dict[str, Any] | None = None
            repair_model_call_records: list[dict[str, Any]] = []
            repair_tool_call_records: list[dict[str, Any]] = []
            try:
                (
                    result,
                    repair_model_call_records,
                    repair_tool_call_records,
                ) = run_targeted_repair_cycle(
                    repair_messages=clean_regeneration_messages(
                        repair_instruction
                    ),
                    step=repair_step,
                    purpose="targeted_auto_repair",
                    repair_round=repair_round,
                )
                model_calls.extend(repair_model_call_records)
                repair_model_call = (
                    repair_model_call_records[-1]
                    if repair_model_call_records
                    else None
                )
                if result.response_type != "final" or result.tool_calls:
                    raise ValueError(
                        "targeted auto repair must return a tool-free final response"
                    )
                repaired_output = parse_json_object(
                    str(result.final_content or "")
                )
                repaired_draft = normalize_chapter_draft_output(
                    repaired_output,
                    pending_confirmations=self.tool_context.pending_confirmations,
                )
                repaired_draft, repaired_personnel_gate = (
                    _apply_personnel_content_gate(
                        repaired_draft,
                        fact_ledger=section_fact_ledger,
                    )
                )
                repaired_review = review_chapter_quality(
                    repaired_draft,
                    tender_tables=self.tool_context.tender_tables,
                    source_only_personnel_section_ids=(
                        _source_only_personnel_section_references(
                            section_fact_ledger
                        )
                    ),
                )
                repaired_validation = validate_technical_chapter(
                    repaired_draft,
                    schema=self.chapter_schema,
                    target=self.tool_context._target,
                    confirmed_facts=self.tool_context.get_confirmed_project_facts(
                        {}
                    ).get("facts")
                    or {},
                    pending_confirmations=self.tool_context.pending_confirmations,
                    web_search_policy=self.web_search_policy,
                    web_verification=web_verification,
                    tender_tables=self.tool_context.tender_tables,
                    quality_review=repaired_review,
                    candidate_evidence_ids=candidate_evidence_ids,
                    confirmed_evidence_ids=confirmed_evidence_ids,
                    generation_permissions=section_fact_ledger,
                )
                repaired_snapshot = _candidate_validation_snapshot(
                    validation=repaired_validation,
                    quality_review=repaired_review,
                    draft=repaired_draft,
                    expected_sections=_expected_section_specs(
                        self.tool_context._target
                    ),
                )
                accepted, reason = _fresh_candidate_is_monotonic_improvement(
                    initial=before_validation,
                    fresh=repaired_snapshot,
                )
                after_validation = (
                    repaired_snapshot if accepted else before_validation
                )
                if accepted:
                    final_draft = repaired_draft
                    personnel_content_gate = repaired_personnel_gate
                    model_final_output = repaired_output
                    quality_review = repaired_review
                    output_hash = _candidate_sha256(repaired_draft)
                    selected_candidate = "repair_round_2"
                    candidate_selection_reason = reason
                    model_step_count = repair_step
                    stop_reason = (
                        "targeted_auto_repair_succeeded"
                        if repaired_validation.get("valid")
                        else "targeted_auto_repair_selected_for_diagnostics"
                    )
                after_audit = classify_chapter_audit(
                    after_validation,
                    pending_confirmations=self.tool_context.pending_confirmations,
                    prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                    local_evidence_refs=self.tool_context.local_evidence_refs,
                )
            except Exception as exc:
                repair_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                warnings.append(
                    "targeted auto repair was not applied: "
                    f"{type(exc).__name__}"
                )
            before_ids = {
                item.get("finding_id")
                for item in before_audit.get("auto_fixable") or []
            }
            after_ids = {
                item.get("finding_id")
                for item in after_audit.get("auto_fixable") or []
            }
            automatic_repair_rounds.append(
                {
                    "repair_round": repair_round,
                    "mode": "targeted_auto_repair",
                    "input_draft_hash": input_hash,
                    "audit_findings_before_repair": before_audit,
                    "repair_instruction": repair_instruction,
                    "output_draft_hash": output_hash,
                    "audit_findings_after_repair": after_audit,
                    "fixed_finding_ids": sorted(
                        value for value in before_ids - after_ids if value
                    ),
                    "unresolved_finding_ids": sorted(
                        value for value in after_ids if value
                    ),
                    "model_usage": (
                        _sum_usage(repair_model_call_records)
                    ),
                    "tool_calls": repair_tool_call_records,
                    "elapsed_time_ms": int(
                        (time.monotonic() - repair_started) * 1000
                    ),
                    "accepted": accepted,
                    "error": repair_error,
                    "writing_rules_version": writing_rules.version,
                    "writing_rules_hash": writing_rules.sha256,
                    "project_constraints_version": project_constraints.get(
                        "version"
                    )
                    or "project_constraints_v0",
                    "project_constraints_hash": _json_sha256(
                        project_constraints.get("constraints") or []
                    ),
                    "user_instruction_hash": _text_sha256(
                        self.user_instruction
                    ),
                    "model_profile": self.profile_name
                    or getattr(
                        getattr(self.llm, "profile", None),
                        "name",
                        None,
                    ),
                    "output_key": self.tool_context.output_key,
                }
            )
            emit(
                "automatic_repair_completed",
                step=repair_step,
                data={
                    "repair_round": repair_round,
                    "accepted": accepted,
                    "unresolved_finding_count": len(after_ids),
                    "error": repair_error,
                },
            )
        self_review["duplicate_section_pairs"] = quality_review["duplicate_section_pairs"]
        if final_draft is not None:
            emit(
                "duplicate_review_started",
                step=model_step_count,
                data={"section_count": len(final_draft.get("sections") or [])},
            )
        severe_duplicates = [
            item
            for item in quality_review["duplicate_section_pairs"]
            if item.get("severity") == "severe"
        ]
        if (
            final_draft is not None
            and severe_duplicates
            and len(automatic_repair_rounds) < self.max_auto_repair_rounds
        ):
            self_review["repair_attempted"] = True
            emit(
                "duplicate_repair_started",
                step=model_step_count,
                data={"duplicate_pair_count": len(severe_duplicates), "maximum_repairs": 1},
            )
            repair_step = model_step_count + 1
            repair_started = time.monotonic()
            emit(
                "model_call_started",
                step=repair_step,
                data={
                    "profile_name": self.profile_name,
                    "available_tool_count": 0,
                    "purpose": "controlled_duplicate_repair",
                },
            )
            try:
                duplicate_instruction = _duplicate_repair_instruction(
                    draft=final_draft,
                    duplicate_pairs=severe_duplicates,
                    boundaries=rule_application_plan["section_boundaries"],
                    schema=self.chapter_schema,
                )
                if self.user_instruction:
                    duplicate_instruction += (
                        "\n[Current User Instruction]\n"
                        + self.user_instruction
                    )
                duplicate_input_hash = _candidate_sha256(final_draft)
                repair_result = self.llm.run_with_tools(
                    messages=clean_regeneration_messages(
                        duplicate_instruction
                    ),
                    tools=[],
                    tool_choice="none",
                    options={"stream": False},
                    request_metadata={
                        "target_id": self.tool_context._target_id,
                        "purpose": "controlled_duplicate_repair",
                        "output_key": self.tool_context.output_key,
                    },
                    profile_name=self.profile_name,
                )
                repair_summary = _model_call_summary(
                    repair_step,
                    repair_result.request_metadata,
                )
                model_calls.append(repair_summary)
                emit(
                    "model_call_completed",
                    step=repair_step,
                    data={
                        "response_type": repair_result.response_type,
                        "duration_ms": int((time.monotonic() - repair_started) * 1000),
                        "purpose": "controlled_duplicate_repair",
                        "tool_call_count": len(repair_result.tool_calls),
                    },
                )
                emit("model_usage_updated", step=repair_step, data=_sum_usage(model_calls))
                if repair_result.response_type != "final":
                    raise ValueError("controlled repair did not return a final chapter")
                repaired = normalize_chapter_draft_output(
                    parse_json_object(str(repair_result.final_content or "")),
                    pending_confirmations=self.tool_context.pending_confirmations,
                )
                repaired, repaired_personnel_gate = (
                    _apply_personnel_content_gate(
                        repaired,
                        fact_ledger=section_fact_ledger,
                    )
                )
                if not _repair_preserves_contract(final_draft, repaired):
                    raise ValueError("controlled repair changed headings or coverage contract")
                final_draft = repaired
                personnel_content_gate = repaired_personnel_gate
                model_step_count = repair_step
                self_review["repair_count"] = 1
                self_review["revisions_applied"].append(
                    {
                        "type": "duplicate_boundary_compression",
                        "section_pairs": [
                            [item["left_section"], item["right_section"]]
                            for item in severe_duplicates
                        ],
                    }
                )
                quality_review = review_chapter_quality(
                    final_draft,
                    tender_tables=self.tool_context.tender_tables,
                    source_only_personnel_section_ids=(
                        _source_only_personnel_section_references(
                            section_fact_ledger
                        )
                    ),
                )
                automatic_repair_rounds.append(
                    {
                        "repair_round": len(automatic_repair_rounds) + 1,
                        "mode": "duplicate_boundary_repair",
                        "input_draft_hash": duplicate_input_hash,
                        "audit_findings_before_repair": {
                            "auto_fixable": [
                                {
                                    "finding_id": "AUD-"
                                    + hashlib.sha256(
                                        json.dumps(
                                            item,
                                            ensure_ascii=False,
                                            sort_keys=True,
                                        ).encode("utf-8")
                                    ).hexdigest()[:12].upper(),
                                    "classification": "auto_fixable",
                                    "code": "cross_section_duplicate",
                                    "details": deepcopy(item),
                                }
                                for item in severe_duplicates
                            ]
                        },
                        "repair_instruction": duplicate_instruction,
                        "output_draft_hash": _candidate_sha256(final_draft),
                        "audit_findings_after_repair": {
                            "auto_fixable": [
                                item
                                for item in quality_review.get(
                                    "duplicate_section_pairs"
                                )
                                or []
                                if item.get("severity") == "severe"
                            ]
                        },
                        "fixed_finding_ids": [],
                        "unresolved_finding_ids": [],
                        "model_usage": deepcopy(
                            repair_summary.get("usage") or {}
                        ),
                        "tool_calls": [],
                        "elapsed_time_ms": int(
                            (time.monotonic() - repair_started) * 1000
                        ),
                        "accepted": True,
                        "writing_rules_version": writing_rules.version,
                        "writing_rules_hash": writing_rules.sha256,
                        "project_constraints_version": project_constraints.get(
                            "version"
                        )
                        or "project_constraints_v0",
                        "project_constraints_hash": _json_sha256(
                            project_constraints.get("constraints") or []
                        ),
                        "user_instruction_hash": _text_sha256(
                            self.user_instruction
                        ),
                        "model_profile": self.profile_name
                        or getattr(
                            getattr(self.llm, "profile", None),
                            "name",
                            None,
                        ),
                        "output_key": self.tool_context.output_key,
                    }
                )
                emit(
                    "duplicate_repair_completed",
                    step=repair_step,
                    data={
                        "remaining_duplicate_pair_count": len(
                            quality_review["duplicate_section_pairs"]
                        )
                    },
                )
            except Exception as exc:
                warnings.append(
                    f"controlled duplicate repair was not applied: {type(exc).__name__}"
                )
                emit(
                    "duplicate_repair_failed",
                    step=repair_step,
                    data={"error_type": type(exc).__name__},
                )
        quality_review["table_fill_data"] = _merge_table_fill_data(
            quality_review["table_fill_data"],
            self.tool_context.table_fill_data_records,
            allowed_required_table_ids={
                str(item.get("table_id") or "")
                for item in self.tool_context.tender_tables
                if item.get("table_type") == "required_response_table"
                and item.get("table_id")
            },
        )
        if final_draft is not None:
            emit(
                "duplicate_review_completed",
                step=model_step_count,
                data={
                    "duplicate_pair_count": len(quality_review["duplicate_section_pairs"]),
                    "repair_performed": bool(self_review["repair_count"]),
                },
            )
            emit(
                "tender_tables_reused",
                step=model_step_count,
                data={"count": len(quality_review["tender_tables_reused"])},
            )
            emit(
                "markdown_tables_created",
                step=model_step_count,
                data={"count": len(quality_review["tender_tables_created"])},
            )
            emit(
                "complex_tender_tables_preserved",
                step=model_step_count,
                data={"count": len(quality_review["complex_tender_tables_preserved"])},
            )
        else:
            emit(
                "duplicate_review_skipped",
                step=model_step_count,
                data={
                    "skip_reason": (
                        "model_json_parse_error"
                        if json_parse_failure
                        else "no_valid_chapter_object"
                    )
                },
            )

        set_phase("validating", step=model_step_count)
        emit(
            "validation_started",
            step=model_step_count,
            data={
                "target_id": self.tool_context._target_id,
                "revision": based_on_existing_draft,
            },
        )
        if final_draft is None:
            skip_reason = (
                "model_json_parse_error"
                if json_parse_failure
                else "no_valid_chapter_object"
            )
            skipped_issue = _validation_issue(
                severity="fatal",
                category=(
                    "protocol"
                    if skip_reason == "model_json_parse_error"
                    else "runtime"
                ),
                code=skip_reason,
                message=(
                    "model final output could not be parsed as the chapter Schema"
                    if skip_reason == "model_json_parse_error"
                    else "no structured final chapter was produced"
                ),
            )
            validation = {
                "valid": False,
                "validation_status": "fatal",
                "candidate_generated": False,
                "candidate_saved": False,
                "candidate_available": False,
                "candidate_reviewable": False,
                "submission_ready": False,
                "issues": [skipped_issue],
                "issue_counts": _validation_issue_counts([skipped_issue]),
                "skipped": True,
                "skip_reason": skip_reason,
                "errors": [],
                "warnings": [],
            }
            emit(
                "validation_skipped",
                step=model_step_count,
                data={"skip_reason": skip_reason},
            )
        else:
            canonicalization_error: str | None = None
            try:
                identity_resolution = _resolve_section_identities(
                    final_draft, target=self.tool_context._target
                )
                final_draft = canonicalize_agent_candidate(
                    candidate=final_draft,
                    chapter_id=str(self.tool_context.target_chapter_id or ""),
                    expected_sections=identity_resolution.get("expected_sections") or [],
                    section_identity_map=identity_resolution.get("section_identity_map") or [],
                    section_identity_issues=identity_resolution.get("issues") or [],
                ).candidate
            except CandidateCanonicalizationError as exc:
                canonicalization_error = str(exc)
            validation = validate_technical_chapter(
                final_draft,
                schema=self.chapter_schema,
                target=self.tool_context._target,
                confirmed_facts=self.tool_context.get_confirmed_project_facts({}).get("facts") or {},
                pending_confirmations=self.tool_context.pending_confirmations,
                web_search_policy=self.web_search_policy,
                web_verification=web_verification,
                tender_tables=self.tool_context.tender_tables,
                quality_review=quality_review,
                candidate_evidence_ids=candidate_evidence_ids,
                confirmed_evidence_ids=confirmed_evidence_ids,
                generation_permissions=section_fact_ledger,
            )
            if canonicalization_error:
                canonical_issue = _validation_issue(
                    # A model draft whose section identity cannot be made
                    # canonical is still a reviewable candidate.  The normal
                    # outline-mismatch validator already records the
                    # substantive structure problem as blocking; escalating
                    # this deterministic artifact-normalization failure to
                    # fatal would incorrectly suppress the existing
                    # HUMAN_REVIEW_READY blocked-draft path.
                    severity="blocking",
                    category="structure",
                    code="candidate_section_canonicalization_failed",
                    message=canonicalization_error,
                )
                validation.setdefault("issues", []).append(canonical_issue)
                validation.setdefault("errors", []).append(canonicalization_error)
                validation["issue_counts"] = _validation_issue_counts(validation["issues"])
                validation["validation_status"] = "fatal"
                validation["valid"] = False
            fatal_issues = [
                item
                for item in validation.get("issues") or []
                if item.get("severity") == "fatal"
            ]
            blocking_issues = [
                item
                for item in validation.get("issues") or []
                if item.get("severity") == "blocking"
            ]
            if fatal_issues:
                errors.extend(str(item.get("message") or "") for item in fatal_issues)
                stop_reason = "fatal_validation_failed"
            elif blocking_issues and not runtime_complete_working_draft_finalized:
                stop_reason = "candidate_validation_blocked"
            warnings.extend(validation["warnings"])
            emit(
                "validation_completed",
                step=model_step_count,
                data={
                    "valid": validation["valid"],
                    "validation_status": validation.get("validation_status"),
                    "issue_counts": validation.get("issue_counts") or {},
                    "error_count": len(validation.get("errors") or []),
                    "warning_count": len(validation.get("warnings") or []),
                    "missing_response_point_ids": validation.get("missing_response_point_ids") or [],
                    "missing_score_item_ids": validation.get("missing_score_item_ids") or [],
                    "expected_section_count": len(validation.get("expected_section_titles") or []),
                    "actual_section_count": len(validation.get("actual_section_titles") or []),
                },
            )
        if final_draft is not None:
            report_status, draft_status, submission_ready = chapter_run_status(
                final_draft,
                validation,
                self.tool_context.pending_confirmations,
            )
            if (
                report_status == "completed"
                and not (validation.get("issue_counts") or {}).get("blocking")
                and not (validation.get("issue_counts") or {}).get("fatal")
                and web_verification["status"] in {"insufficient", "unavailable"}
            ):
                draft_status = "initial_draft_pending_web_verification"
                submission_ready = False
        elif self.tool_context.working_draft_markdown.strip() or self.tool_context.saved_sections:
            report_status, draft_status, submission_ready = "incomplete", "partial_draft", False
        else:
            report_status, draft_status, submission_ready = "failed", "invalid_draft", False
        final_audit = classify_chapter_audit(
            validation,
            pending_confirmations=self.tool_context.pending_confirmations,
            prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
            local_evidence_refs=self.tool_context.local_evidence_refs,
        )
        chapter_status = chapter_completion_status(
            draft=final_draft,
            audit=final_audit,
            repair_rounds=automatic_repair_rounds,
            max_auto_repair_rounds=self.max_auto_repair_rounds,
            selected_material_ids=self.tool_context.selected_material_ids,
            local_evidence_refs=self.tool_context.local_evidence_refs,
        )
        if chapter_status != "complete":
            submission_ready = False
        candidate_generated = final_draft is not None
        generation_completed = candidate_generated
        candidate_saved = False
        candidate_available = False
        candidate_reviewable = bool(
            candidate_generated
            and _candidate_schema_is_legal(
                final_draft,
                self.chapter_schema,
            )
            and not any(
                item.get("severity") == "fatal"
                for item in validation.get("issues") or []
                if isinstance(item, dict)
            )
        )
        completed = datetime.now(timezone.utc)
        output_dir = self.tool_context.draft_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        candidate_markdown = (
            render_technical_chapter_markdown(final_draft, self.tool_context._target, validation)
            if final_draft is not None
            else self.tool_context.working_draft_markdown
        )
        candidate_json_path = self.tool_context.draft_path.with_name(
            f"{self.tool_context.draft_path.stem}.candidate.json"
        )
        candidate_markdown_path = self.tool_context.draft_path.with_name(
            f"{self.tool_context.draft_path.stem}.candidate.md"
        )
        candidate_artifacts: dict[str, Any] = {
            "json_path": None,
            "markdown_path": None,
            "json_sha256": None,
            "markdown_sha256": None,
            "json_size_bytes": 0,
            "markdown_size_bytes": 0,
        }
        candidate_artifact_eligible = _candidate_is_artifact_eligible(
            draft=final_draft,
            markdown=candidate_markdown,
            schema_reviewable=candidate_reviewable,
            empty_content_allowed_section_ids=set(
                personnel_content_gate.get("source_only_section_ids") or []
            ) | set(
                personnel_content_gate.get("strict_source_only_section_ids") or []
            ),
        )
        if (
            candidate_generated
            and candidate_artifact_eligible
        ):
            emit(
                "candidate_artifact_write_started",
                step=model_step_count,
                data={
                    "json_path": str(candidate_json_path),
                    "markdown_path": str(candidate_markdown_path),
                },
            )
            try:
                candidate_artifacts = _write_and_verify_candidate_artifacts(
                    draft=final_draft,
                    markdown=candidate_markdown,
                    json_path=candidate_json_path,
                    markdown_path=candidate_markdown_path,
                )
                candidate_saved = True
                candidate_available = True
                validation["candidate_saved"] = True
                validation["candidate_available"] = True
                emit(
                    "candidate_artifact_saved",
                    step=model_step_count,
                    data=deepcopy(candidate_artifacts),
                )
            except Exception as exc:
                failure_message = (
                    "candidate_artifact_write_failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )
                runtime_issue = _validation_issue(
                    severity="fatal",
                    category="runtime",
                    code="candidate_artifact_write_failed",
                    message=failure_message,
                )
                validation.setdefault("issues", []).append(runtime_issue)
                validation.setdefault("errors", []).append(failure_message)
                validation["issue_counts"] = _validation_issue_counts(
                    validation["issues"]
                )
                validation["validation_status"] = "fatal"
                validation["valid"] = False
                validation["candidate_saved"] = False
                validation["candidate_available"] = False
                validation["candidate_reviewable"] = False
                validation["submission_ready"] = False
                candidate_saved = False
                candidate_available = False
                candidate_reviewable = False
                submission_ready = False
                report_status = "failed"
                draft_status = "invalid_draft"
                stop_reason = "candidate_artifact_write_failed"
                errors.append(failure_message)
                emit(
                    "candidate_artifact_write_failed",
                    step=model_step_count,
                    data={"error_type": type(exc).__name__},
                )
        change_summary = build_markdown_change_summary(existing_draft, candidate_markdown)
        previous_draft_path: str | None = None
        formal_markdown_saved = False
        constraint_persistence: dict[str, Any] = {
            "status": "not_requested",
            "scope": self.instruction_scope,
        }
        if final_draft is not None and validation["valid"]:
            try:
                if existing_draft_present:
                    history_path = _history_path(
                        self.tool_context.draft_path,
                        self.tool_context._target_id,
                    )
                    _atomic_write_text(history_path, existing_draft)
                    previous_draft_path = str(history_path)
                    emit(
                        "history_version_saved",
                        step=model_step_count,
                        data={"path": previous_draft_path, "revision_round": revision_round},
                )
                _atomic_write_text(self.tool_context.draft_path, candidate_markdown)
                formal_markdown_saved = True
                emit(
                    "draft_file_committed",
                    step=model_step_count,
                    data={
                        "path": str(self.tool_context.draft_path),
                        "character_count": len(candidate_markdown),
                        "revision": based_on_existing_draft,
                    },
                )
            except OSError as exc:
                if previous_draft_path:
                    try:
                        Path(previous_draft_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                    previous_draft_path = None
                report_status, draft_status, submission_ready = "failed", "invalid_draft", False
                stop_reason = "draft_commit_failed"
                errors.append(f"draft_commit_failed: {type(exc).__name__}: {str(exc)[:300]}")
                draft_file_preserved = self.tool_context.draft_path.exists()
                emit(
                    "draft_file_preserved",
                    step=model_step_count,
                    data={
                        "path": str(self.tool_context.draft_path),
                        "reason": stop_reason,
                        "existing_file": draft_file_preserved,
                    },
                )
        else:
            draft_file_preserved = self.tool_context.draft_path.exists()
            emit(
                "draft_file_preserved",
                step=model_step_count,
                data={
                    "path": str(self.tool_context.draft_path),
                    "reason": stop_reason,
                    "existing_file": draft_file_preserved,
                },
            )
        if (
            report_status == "completed"
            and validation.get("valid")
            and user_instruction_present
            and self.instruction_scope in {"project", "global"}
        ):
            constraint_persistence = persist_confirmed_constraint(
                instruction=self.user_instruction,
                scope=self.instruction_scope,
                output_root=self.tool_context.output_root,
                output_key=self.tool_context.output_key,
                rules_path=writing_rules.path,
            )
            if constraint_persistence.get("status") == "rejected":
                warnings.append(
                    "persistent instruction was rejected: "
                    + ",".join(constraint_persistence.get("blockers") or [])
                )
            emit(
                "instruction_scope_persisted",
                step=model_step_count,
                data={
                    "scope": self.instruction_scope,
                    "status": constraint_persistence.get("status"),
                },
            )
        completed = datetime.now(timezone.utc)
        run_path = output_dir / f"{self.tool_context._target_id}.run.json"
        primary_markdown_path = (
            str(self.tool_context.draft_path)
            if formal_markdown_saved
            else str(candidate_markdown_path)
            if candidate_saved
            else None
        )
        terminal_phase = "completed" if report_status == "completed" else "failed"
        set_phase(terminal_phase, step=model_step_count)
        terminal_event = {
            "completed": "agent_run_completed",
            "incomplete": "agent_run_incomplete",
            "failed": "agent_run_failed",
        }[report_status]
        emit(
            terminal_event,
            step=model_step_count,
            data={
                "status": report_status,
                "generation_completed": generation_completed,
                "candidate_generated": candidate_generated,
                "candidate_saved": candidate_saved,
                "candidate_available": candidate_available,
                "candidate_reviewable": candidate_reviewable,
                "draft_status": draft_status,
                "submission_ready": submission_ready,
                "stop_reason": stop_reason,
            },
        )
        final_event_count = emitter.sequence + 1
        run_payload = {
            "schema_version": "technical_writing_agent_run_v1",
            "status": report_status,
            "chapter_status": chapter_status,
            "validation_status": validation.get("validation_status"),
            "issue_counts": deepcopy(validation.get("issue_counts") or {}),
            "validation": deepcopy(validation),
            "generation_completed": generation_completed,
            "candidate_generated": candidate_generated,
            "candidate_saved": candidate_saved,
            "candidate_available": candidate_available,
            "candidate_reviewable": candidate_reviewable,
            "candidate_artifacts": candidate_artifacts,
            "stop_reason": stop_reason,
            "draft_status": draft_status,
            "submission_ready": submission_ready,
            "initial_audit": (
                classify_chapter_audit(
                    initial_candidate_validation or {},
                    pending_confirmations=self.tool_context.pending_confirmations,
                    prohibited_material_ids=self.tool_context.prohibited_material_ids_detected,
                    local_evidence_refs=self.tool_context.local_evidence_refs,
                )
                if initial_candidate_validation
                else {
                    "auto_fixable": [],
                    "human_confirmation_required": [],
                    "release_blocking": [],
                    "execution_error": [],
                    "counts": {},
                }
            ),
            "automatic_repair": {
                "maximum_rounds": self.max_auto_repair_rounds,
                "rounds_attempted": len(automatic_repair_rounds),
                "stop_condition": (
                    "execution_failed"
                    if chapter_status == "execution_failed"
                    else "maximum_rounds_reached_with_auto_fixable_findings"
                    if chapter_status == "auto_repair_exhausted"
                    else "no_auto_fixable_findings"
                ),
                "rounds": automatic_repair_rounds,
            },
            "final_audit": final_audit,
            "human_confirmation_items": final_audit.get(
                "human_confirmation_required"
            )
            or [],
            "release_blocking_items": final_audit.get("release_blocking")
            or [],
            "unresolved_auto_fixable_issues": final_audit.get(
                "auto_fixable"
            )
            or [],
            "target": {
                "chapter_id": self.tool_context.target_chapter_id,
                "section_id": self.tool_context.target_section_id,
                "title": self.tool_context._target.get("title"),
            },
            "model_profile": (
                getattr(getattr(self.llm, "profile", None), "run_report_summary", lambda: None)()
            ),
            "model_json_repair": model_json_repair,
            "tool_arguments_json_repairs": tool_arguments_json_repairs,
            "fresh_final_generation": fresh_final_generation,
            "fresh_quality_regeneration": fresh_quality_regeneration,
            "fresh_edit_scope": deepcopy(
                fresh_quality_regeneration.get("fresh_edit_scope") or {}
            ),
            "fresh_diff_audit": deepcopy(
                fresh_quality_regeneration.get("fresh_diff_audit") or {}
            ),
            "section_identity_map": deepcopy(
                fresh_quality_regeneration.get("section_identity_map") or []
            ),
            "section_identity_issues": deepcopy(
                fresh_quality_regeneration.get("section_identity_issues") or []
            ),
            "initial_candidate_validation": initial_candidate_validation,
            "fresh_candidate_validation": fresh_candidate_validation,
            "selected_candidate": selected_candidate,
            "candidate_selection_reason": candidate_selection_reason,
            "fresh_candidate_accepted": fresh_candidate_accepted,
            "exact_locator_patch_count": int(
                fresh_quality_regeneration.get("exact_locator_patch_count") or 0
            ),
            "local_guarded_delete_candidate_count": int(
                fresh_quality_regeneration.get(
                    "local_guarded_delete_candidate_count"
                )
                or 0
            ),
            "local_guarded_delete_applied_count": int(
                fresh_quality_regeneration.get(
                    "local_guarded_delete_applied_count"
                )
                or 0
            ),
            "canonical_title_normalization_count": int(
                deterministic_format_normalization.get(
                    "canonical_title_normalization_count"
                )
                or 0
            ),
            "table_intro_normalization_count": int(
                deterministic_format_normalization.get(
                    "table_intro_normalization_count"
                )
                or 0
            ),
            "candidate_selection_audit": deepcopy(
                fresh_quality_regeneration.get("candidate_selection_audit")
                or {}
            ),
            "initial_candidate_sha256": initial_candidate_sha256,
            "fresh_candidate_sha256": fresh_candidate_sha256,
            "generation_permission_audit": deepcopy(
                validation.get("generation_permission_audit")
                or {
                    "passed": False,
                    "unauthorized_numeric_claims": [],
                    "unauthorized_personnel_claims": [],
                    "unauthorized_product_capabilities": [],
                    "candidate_facts_promoted": [],
                    "unregistered_placeholders": [],
                }
            ),
            "personnel_content_gate": personnel_content_gate,
            "chapter_tender_fact_permission_count": int(
                validation.get("chapter_tender_fact_permission_count") or 0
            ),
            "chapter_tender_numeric_permission_count": int(
                validation.get("chapter_tender_numeric_permission_count") or 0
            ),
            "effective_tender_fact_ids_by_section": deepcopy(
                validation.get("effective_tender_fact_ids_by_section") or {}
            ),
            "effective_tender_numeric_fact_ids_by_section": deepcopy(
                validation.get("effective_tender_numeric_fact_ids_by_section")
                or {}
            ),
            "model_final_output": model_final_output,
            "chapter_draft": final_draft,
            "response_point_usage": list((final_draft or {}).get("requirements_covered") or []),
            "score_item_usage": list((final_draft or {}).get("score_items_covered") or []),
            "requirement_ids_consulted": list(
                self.tool_context.consulted_requirement_ids
            ),
            "score_item_ids_consulted": list(
                self.tool_context.consulted_score_item_ids
            ),
            "tender_table_ids_consulted": list(
                self.tool_context.consulted_tender_table_ids
            ),
            "knowledge_candidate_ids_consulted": list(
                self.tool_context.consulted_knowledge_candidate_ids
            ),
            "selected_material_ids": list(
                self.tool_context.selected_material_ids
            ),
            "rejected_or_unusable_material_ids_detected": list(
                self.tool_context.prohibited_material_ids_detected
            ),
            "local_knowledge_references": self.tool_context.local_evidence_refs,
            "web_references": self.tool_context.web_refs,
            "pending_confirmations": self.tool_context.pending_confirmations,
            "placeholders": list((final_draft or {}).get("placeholders_used") or []),
            "revision_round": revision_round,
            "based_on_existing_draft": based_on_existing_draft,
            "existing_draft_path": str(self.tool_context.draft_path) if existing_draft_present else None,
            "existing_draft_sha256": self.tool_context.existing_draft_sha256 or None,
            "user_instruction_present": user_instruction_present,
            "user_instruction_sources": list(self.user_instruction_sources),
            "user_instruction": self.user_instruction if user_instruction_present else "",
            "user_instruction_hash": _text_sha256(self.user_instruction),
            "user_instruction_summary": self.user_instruction_summary,
            "revision_scope": revision_scope,
            "instruction_conflicts": instruction_conflicts,
            "instruction_scope": self.instruction_scope,
            "instruction_scope_suggestion": scope_suggestion,
            "instruction_scope_suggestion_reason": scope_suggestion_reason,
            "instruction_constraint_normalization": (
                normalize_persistent_constraint(
                    self.user_instruction,
                    scope=(
                        self.instruction_scope
                        if self.instruction_scope in {"project", "global"}
                        else scope_suggestion
                    ),
                )
                if user_instruction_present
                and (
                    self.instruction_scope in {"project", "global"}
                    or scope_suggestion in {"project", "global"}
                )
                else None
            ),
            "instruction_constraint_persistence": constraint_persistence,
            "writing_rules": writing_rules.report(),
            "rule_application_plan": rule_application_plan,
            "project_constraints_loaded": {
                "loaded": bool(project_constraints.get("loaded")),
                "path": project_constraints.get("path"),
                "constraint_count": len(project_constraints.get("constraints") or []),
                "version": project_constraints.get("version")
                or "project_constraints_v0",
                "sha256": _json_sha256(
                    project_constraints.get("constraints") or []
                ),
            },
            "self_review": self_review,
            "technical_writing_quality": {
                "chapter": quality_review.get("chapter_metrics") or {},
                "sections": quality_review.get("section_metrics") or [],
                "errors": quality_review.get("quality_errors") or [],
                "warnings": quality_review.get("quality_warnings") or [],
                "sections_with_insufficient_prose": quality_review.get(
                    "sections_with_insufficient_prose"
                )
                or [],
            },
            "tender_tables_detected": self.tool_context.tender_tables,
            "tender_tables_reused": quality_review["tender_tables_reused"],
            "tender_tables_created": quality_review["tender_tables_created"],
            "complex_tender_tables_preserved": quality_review[
                "complex_tender_tables_preserved"
            ],
            "table_fill_data": quality_review["table_fill_data"],
            "duplicate_review_performed": final_draft is not None,
            "duplicate_repair_performed": bool(self_review["repair_count"]),
            "web_search_policy": self.web_search_policy,
            "web_verification": web_verification,
            "previous_draft_path": previous_draft_path,
            "current_draft_path": primary_markdown_path,
            "current_formal_draft_path": (
                str(self.tool_context.draft_path)
                if formal_markdown_saved
                else None
            ),
            "current_candidate_draft_path": (
                str(candidate_markdown_path) if candidate_saved else None
            ),
            "change_summary": change_summary,
            "tool_trace": trace,
            "model_calls": model_calls,
            "working_draft": {
                "markdown": self.tool_context.working_draft_markdown,
                "sections": self.tool_context.saved_sections,
            },
            "run_summary": {
                "output_key": self.tool_context.output_key,
                "model_profile": self.profile_name
                or getattr(getattr(self.llm, "profile", None), "name", None),
                "writing_rules_version": writing_rules.version,
                "writing_rules_hash": writing_rules.sha256,
                "project_constraints_version": project_constraints.get(
                    "version"
                )
                or "project_constraints_v0",
                "project_constraints_hash": _json_sha256(
                    project_constraints.get("constraints") or []
                ),
                "user_instruction_hash": _text_sha256(
                    self.user_instruction
                ),
                "event_count": final_event_count,
                "model_step_count": model_step_count,
                "model_request_count": len(model_calls),
                "tool_call_count": sum(1 for item in trace if item.get("tool_name")),
                "tool_calls_by_name": {
                    name: sum(1 for item in trace if item.get("tool_name") == name)
                    for name in sorted({str(item.get("tool_name") or "") for item in trace})
                    if name
                },
                "tool_names": [item.get("tool_name") for item in trace],
                "duplicate_tool_calls": duplicate_tool_calls,
                "local_knowledge_call_count": tool_counts.get("query_local_knowledge", 0),
                "web_search_call_count": tool_counts.get("web_search", 0),
                "section_count_completed": len(self.tool_context.saved_sections),
                "section_count_expected": expected_section_count,
                "characters_generated": max(
                    streamed_characters,
                    len(self.tool_context.working_draft_markdown),
                    sum(len(str(item.get("content") or "")) for item in (final_draft or {}).get("sections") or []),
                ),
                "forced_finalization": forced_finalization,
                "draft_file_preserved": draft_file_preserved,
                "event_log_path": str(emitter.event_log_path) if emitter.event_log_path else "",
                "total_usage": _sum_usage(model_calls),
                "total_runtime_ms": max(0, int((completed - started).total_seconds() * 1000)),
                "stop_reason": stop_reason,
                "repeat_call_counts": {key: value for key, value in signature_counts.items() if value > 1},
            },
            "validation": validation,
            "failures": errors,
            "warnings": warnings,
            "outputs": {
                "markdown": primary_markdown_path,
                "formal_markdown": (
                    str(self.tool_context.draft_path)
                    if formal_markdown_saved
                    else None
                ),
                "candidate_markdown": (
                    str(candidate_markdown_path) if candidate_saved else None
                ),
                "candidate_json": (
                    str(candidate_json_path) if candidate_saved else None
                ),
                "preserved_formal_markdown": (
                    str(self.tool_context.draft_path)
                    if draft_file_preserved
                    and self.tool_context.draft_path.exists()
                    and not formal_markdown_saved
                    else None
                ),
                "run_json": str(run_path),
                "events_jsonl": str(emitter.event_log_path) if emitter.event_log_path else None,
            },
        }
        _atomic_write_text(run_path, json.dumps(run_payload, ensure_ascii=False, indent=2) + "\n")
        emit("run_report_written", step=model_step_count, data={"path": str(run_path)})
        emitter.close()
        return run_payload


def normalize_chapter_draft_output(
    value: dict[str, Any],
    *,
    pending_confirmations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize only aliases observed in the TechnicalWritingAgent's real output."""
    source = extract_chapter_draft_candidate(value)
    strict_placeholder_ids = any(
        item.get("placeholder_id") for item in (pending_confirmations or []) if isinstance(item, dict)
    )
    sections: list[dict[str, Any]] = []
    for item in list(source.get("sections") or []):
        if not isinstance(item, dict):
            continue
        heading = item.get("heading")
        if heading is None:
            heading = item.get("title")
        content = item.get("content")
        if content is None:
            content = item.get("summary")
        content_text = str(content or "")
        placeholders = (
            extract_placeholder_ids(content_text)
            if strict_placeholder_ids
            else _literal_placeholders(content_text)
        )
        covered_requirements = _string_list(item.get("covered_requirement_ids"))
        covered_requirements.extend(RESPONSE_POINT_PATTERN.findall(content_text))
        covered_scores = _string_list(item.get("covered_score_item_ids"))
        covered_scores.extend(SCORE_ITEM_PATTERN.findall(content_text))
        block_type = str(item.get("block_type") or "other")
        if block_type not in CHAPTER_BLOCK_TYPES:
            block_type = "other"
        normalized_section = {
                "heading": str(heading or ""),
                "block_type": block_type,
                "content": content_text,
                "covered_score_item_ids": _unique_strings(covered_scores),
                "covered_requirement_ids": _unique_strings(covered_requirements),
                "placeholder_ids": _unique_strings(
                    [*_string_list(item.get("placeholder_ids")), *placeholders]
                ),
            }
        if item.get("section_id"):
            normalized_section["section_id"] = str(item.get("section_id"))
        sections.append(normalized_section)

    source_usage = source.get("source_usage") if isinstance(source.get("source_usage"), dict) else {}
    requirements = _string_list(source.get("requirements_covered"))
    # The real final object explicitly recorded these IDs as its tender source usage.
    requirements.extend(_string_list(source_usage.get("tender_requirements_loaded")))
    requirements.extend(
        value
        for section in sections
        for value in section["covered_requirement_ids"]
    )
    scores = _string_list(source.get("score_items_covered"))
    scores.extend(
        value
        for section in sections
        for value in section["covered_score_item_ids"]
    )
    placeholders = _string_list(source.get("placeholders_used"))
    placeholders.extend(value for section in sections for value in section["placeholder_ids"])
    claims = [_normalize_claim(item) for item in list(source.get("claims") or []) if isinstance(item, dict)]
    warnings = _string_list(source.get("warnings"))
    generated_supporting_tables = [
        {
            "section_id": str(item.get("section_id") or item.get("section_title") or ""),
            "title": str(item.get("title") or ""),
            "purpose": str(item.get("purpose") or ""),
            "why_table_is_better_than_prose": str(
                item.get("why_table_is_better_than_prose") or ""
            ),
        }
        for item in source.get("generated_supporting_tables") or []
        if isinstance(item, dict)
    ]
    if source.get("summary") is not None and source.get("chapter_title") is None:
        warnings.append("normalized_from_agent_summary_contract")
    result = {
        "chapter_title": str(source.get("chapter_title") or source.get("title") or ""),
        "sections": sections,
        "claims": claims,
        "placeholders_used": _unique_strings(placeholders),
        "score_items_covered": _unique_strings(scores),
        "requirements_covered": _unique_strings(requirements),
        "warnings": _unique_strings(warnings),
    }
    if "generated_supporting_tables" in source:
        result["generated_supporting_tables"] = generated_supporting_tables
    return result


def extract_chapter_draft_candidate(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("chapter_draft", "structured_chapter_draft", "model_final_output"):
        candidate = value.get(key)
        if isinstance(candidate, dict) and isinstance(candidate.get("sections"), list):
            return candidate
    if isinstance(value.get("sections"), list):
        return value
    return {}


_NEUTRAL_GENERATED_TABLE_INTRO = "为便于说明本节相关内容，现将关键事项整理如下表。"


def _tender_table_header_keys(tender_tables: list[dict[str, Any]]) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for table in tender_tables:
        if not isinstance(table, dict):
            continue
        headers = table.get("headers") or table.get("columns") or []
        if headers and isinstance(headers[0], dict):
            headers = [
                item.get("title") or item.get("name") or item.get("field") or ""
                for item in headers
                if isinstance(item, dict)
            ]
        key = tuple(
            _normalize_diff_text(str(value)) for value in headers if str(value).strip()
        )
        if key:
            keys.add(key)
    return keys


def _markdown_header_key(line: str) -> tuple[str, ...]:
    text = str(line or "").strip().strip("|")
    return tuple(
        _normalize_diff_text(value)
        for value in text.split("|")
        if value.strip()
    )


def _has_deterministic_table_intro(lines: list[str], table_index: int) -> bool:
    for index in range(table_index - 1, max(-1, table_index - 6), -1):
        text = lines[index].strip()
        if not text:
            continue
        if re.fullmatch(r"(?:表\s*[一二三四五六七八九十\d]+\s*)?.{1,40}表", text):
            continue
        if re.search(r"[。！？；;]$", text) and len(re.sub(r"\s+", "", text)) >= 12:
            return True
        return bool(
            re.search(
                r"(?:如下表|见下表|表中列示|具体安排如下|下表展示|下述表格|表\s*[一二三四五六七八九十\d]+(?:列明|展示|说明)|整理如下表)",
                text,
            )
        )
    return False


def _normalize_candidate_format_before_validation(
    draft: dict[str, Any],
    *,
    target: dict[str, Any],
    tender_tables: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Apply source-free title/table formatting before the first audit."""

    result = deepcopy(draft)
    report = {
        "canonical_title_normalization_count": 0,
        "table_intro_normalization_count": 0,
    }
    expected = _expected_section_specs(target)
    expected_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in expected:
        expected_by_id.setdefault(str(item.get("section_id") or ""), []).append(item)
    section_id_counts: dict[str, int] = {}
    for section in result.get("sections") or []:
        if isinstance(section, dict) and section.get("section_id"):
            section_id = str(section["section_id"])
            section_id_counts[section_id] = section_id_counts.get(section_id, 0) + 1
    for section in result.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        matches = expected_by_id.get(section_id) or []
        if section_id_counts.get(section_id) != 1 or len(matches) != 1:
            continue
        expected_title = str(matches[0].get("section_title") or "")
        current_title = str(section.get("heading") or "")
        current_level = re.match(r"^(#{1,6})\s+", current_title)
        expected_level = re.match(r"^(#{1,6})\s+", expected_title)
        if bool(current_level) != bool(expected_level):
            continue
        if current_level and expected_level and current_level.group(1) != expected_level.group(1):
            continue
        if expected_title and current_title != expected_title:
            section["heading"] = expected_title
            report["canonical_title_normalization_count"] += 1

    metadata_by_reference: dict[str, list[dict[str, Any]]] = {}
    for item in result.get("generated_supporting_tables") or []:
        if not isinstance(item, dict):
            continue
        reference = str(item.get("section_id") or item.get("section_title") or "")
        if reference:
            metadata_by_reference.setdefault(reference, []).append(item)
    tender_header_keys = _tender_table_header_keys(tender_tables)
    for section in result.get("sections") or []:
        if not isinstance(section, dict):
            continue
        references = {
            str(section.get("section_id") or ""),
            str(section.get("heading") or ""),
        }
        metadata = [
            item
            for reference in references
            for item in metadata_by_reference.get(reference) or []
        ]
        if not metadata:
            continue
        lines = str(section.get("content") or "").splitlines()
        insertions: list[int] = []
        for index in range(len(lines) - 1):
            if not lines[index].lstrip().startswith("|"):
                continue
            if not re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1]):
                continue
            if _markdown_header_key(lines[index]) in tender_header_keys:
                continue
            if _has_deterministic_table_intro(lines, index):
                continue
            insertion_index = index
            previous = index - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            if previous >= 0 and any(
                _normalize_diff_text(str(item.get("title") or ""))
                and _normalize_diff_text(str(item.get("title") or ""))
                in _normalize_diff_text(lines[previous])
                for item in metadata
            ):
                insertion_index = previous
            insertions.append(insertion_index)
        for insertion_index in reversed(sorted(set(insertions))):
            lines[insertion_index:insertion_index] = [
                _NEUTRAL_GENERATED_TABLE_INTRO,
                "",
            ]
            report["table_intro_normalization_count"] += 1
        if insertions:
            section["content"] = "\n".join(lines)
    return result, report


def _validation_issue(
    *,
    severity: str,
    category: str,
    code: str,
    message: str,
    section_id: str | None = None,
    section_title: str | None = None,
) -> dict[str, Any]:
    issue = {
        "severity": severity,
        "category": category,
        "code": code,
        "section_id": section_id,
        "section_title": section_title,
        "message": message,
    }
    parts = str(message or "").split(":", 3)
    if len(parts) == 4 and parts[0] in {
        "fact_boundary",
        "section_quality",
        "generated_supporting_table",
        "required_response_table",
        "placeholder",
    }:
        issue["offending_span"] = parts[3] or None
    issue["repair_disposition"] = _repair_disposition_for_issue(issue)
    return issue


def _validation_issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    return {
        severity: sum(
            1 for item in issues if item.get("severity") == severity
        )
        for severity in ("fatal", "blocking", "warning")
    }


def _validation_texts_overlap(left: str, right: str) -> bool:
    normalize = lambda value: re.sub(  # noqa: E731
        r"[\W_]+",
        "",
        str(value or ""),
        flags=re.UNICODE,
    ).casefold()
    left_value = normalize(left)
    right_value = normalize(right)
    return bool(
        left_value
        and right_value
        and (
            left_value in right_value
            or right_value in left_value
        )
    )


def _dedupe_personnel_fact_boundary_errors(errors: list[str]) -> list[str]:
    parsed: list[tuple[str, str, str, str]] = []
    for error in errors:
        parts = str(error).split(":", 3)
        if len(parts) == 4 and parts[0] == "fact_boundary":
            parsed.append((str(error), parts[1], parts[2], parts[3]))
    candidate_findings = [
        item for item in parsed if item[2] == "candidate_promoted_to_fact"
    ]
    result: list[str] = []
    for error in errors:
        parts = str(error).split(":", 3)
        if (
            len(parts) == 4
            and parts[0] == "fact_boundary"
            and parts[2] == "unconfirmed_personnel_qualification"
            and any(
                candidate[1] == parts[1]
                and _validation_texts_overlap(candidate[3], parts[3])
                for candidate in candidate_findings
            )
        ):
            continue
        result.append(str(error))
    return list(dict.fromkeys(result))


def _registered_placeholder_claim_is_nondefinite(
    text: str,
    registered_placeholder_ids: set[str],
) -> bool:
    references = extract_placeholder_references(text)
    placeholder_ids = {
        value.removeprefix("{{").removesuffix("}}")
        for value in references
        if value.startswith("{{") and value.endswith("}}")
    }
    if not placeholder_ids or not placeholder_ids <= registered_placeholder_ids:
        return False
    scrubbed = str(text)
    for reference in references:
        scrubbed = scrubbed.replace(reference, "__PH__")
    if re.search(
        r"(?:不少于|至少|具备|具有|持有|取得|已通过|已取得|全职投入|"
        r"全程负责|同类项目|工作年限|项目经验|证书|认证|资质|资格)",
        scrubbed,
    ):
        return False
    return bool(
        re.search(
            r"(?:项目经理|项目负责人|技术负责人|团队成员|人员规模)"
            r"(?:为|包括|由)\s*__PH__",
            scrubbed,
        )
    )


def _normalize_validation_issues(
    *,
    errors: list[str],
    warnings: list[str],
    draft: dict[str, Any],
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    section_ids: dict[str, str] = {
        str(item.get("title") or ""): str(
            item.get("section_id")
            or item.get("chapter_id")
            or item.get("id")
            or ""
        )
        for item in [target, *_all_target_nodes(target)]
        if isinstance(item, dict) and item.get("title")
    }
    for item in draft.get("sections") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("heading") or "")
        identifier = str(item.get("section_id") or "")
        if title and identifier:
            section_ids[title] = identifier
    issues = [
        _issue_from_validation_message(
            str(message),
            severity="blocking",
            section_ids=section_ids,
        )
        for message in errors
    ]
    issues.extend(
        _issue_from_validation_message(
            str(message),
            severity="warning",
            section_ids=section_ids,
        )
        for message in warnings
    )
    return issues


def _attach_generation_permission_issue_details(
    issues: list[dict[str, Any]],
    *,
    permission_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    findings = [
        item
        for key in (
            "unauthorized_numeric_claims",
            "unauthorized_personnel_claims",
            "unauthorized_product_capabilities",
            "candidate_facts_promoted",
            "unregistered_placeholders",
            "unauthorized_bidder_commitments",
        )
        for item in permission_audit.get(key) or []
        if isinstance(item, dict)
    ]
    enriched: list[dict[str, Any]] = []
    for issue in issues:
        value = dict(issue)
        matches = [
            item
            for item in findings
            if str(item.get("code") or "") == str(issue.get("code") or "")
            and (
                not item.get("section_title")
                or str(item.get("section_title"))
                == str(issue.get("section_title") or "")
            )
            and (
                not item.get("offending_span")
                or _validation_texts_overlap(
                    str(item.get("offending_span") or ""),
                    str(issue.get("message") or ""),
                )
                or _validation_texts_overlap(
                    str(item.get("text") or ""),
                    str(issue.get("message") or ""),
                )
            )
        ]
        if matches:
            value["offending_sentence"] = matches[0].get(
                "offending_sentence"
            ) or matches[0].get("text")
            value["offending_span"] = matches[0].get("offending_span")
            value["matched_tender_fact_ids"] = sorted(
                {
                    str(identifier)
                    for item in matches
                    for identifier in item.get("matched_tender_fact_ids") or []
                    if identifier
                }
            )
            value["allowed_spans"] = list(
                dict.fromkeys(
                    str(span)
                    for item in matches
                    for span in item.get("allowed_spans") or []
                    if span
                )
            )
            value["blocked_spans"] = list(
                dict.fromkeys(
                    str(span)
                    for item in matches
                    for span in item.get("blocked_spans") or []
                    if span
                )
            )
        enriched.append(value)
    return enriched


def _sanitize_fact_boundary_issue_spans(
    issues: list[dict[str, Any]],
    *,
    draft: dict[str, Any],
) -> list[dict[str, Any]]:
    sections = {
        str(reference): str(section.get("content") or "")
        for section in draft.get("sections") or []
        if isinstance(section, dict)
        for reference in (section.get("section_id"), section.get("heading"))
        if reference
    }
    sanitized: list[dict[str, Any]] = []
    for raw in issues:
        issue = dict(raw)
        if issue.get("category") != "fact_boundary":
            sanitized.append(issue)
            continue
        message = str(issue.get("message") or "")
        scoped = message.startswith("fact_boundary:")
        sentence = str(issue.get("offending_sentence") or "")
        span = str(issue.get("offending_span") or "")
        payload = message.split(":", 3)[-1] if scoped else ""
        content = sections.get(
            str(issue.get("section_id") or "")
        ) or sections.get(str(issue.get("section_title") or ""), "")
        if not sentence and payload:
            sentence = _locate_exact_offending_sentence(content, payload)
        if not span and payload:
            span = payload
        if sentence and span and _normalized_span_is_within_sentence(
            span, sentence
        ):
            issue["offending_sentence"] = sentence
            issue["offending_span"] = span
            sanitized.append(issue)
            continue
        if sentence:
            relocated = _actual_fact_span_from_sentence(
                str(issue.get("code") or ""), sentence
            )
            if relocated and _normalized_span_is_within_sentence(
                relocated, sentence
            ):
                issue["offending_sentence"] = sentence
                issue["offending_span"] = relocated
                sanitized.append(issue)
                continue
        # Unscoped global fact-boundary diagnostics do not claim a source span.
        if not scoped and not (issue.get("offending_span") or issue.get("offending_sentence")):
            sanitized.append(issue)
    return sanitized


def _normalized_span_is_within_sentence(span: str, sentence: str) -> bool:
    def normalize(value: str) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        text = re.sub(r"(?:\*\*|__|`|#{1,6}|\r|\n)", "", text)
        return re.sub(r"[\s，,。.!！?？：:；;、（）()\[\]{}<>《》\-—]+", "", text)

    normalized_span = normalize(span)
    return bool(normalized_span) and normalized_span in normalize(sentence)


def _actual_fact_span_from_sentence(code: str, sentence: str) -> str:
    exact = _exact_offending_span(code, sentence)
    if exact:
        return exact
    patterns = {
        "schedule_frequency": r"(?:每日|每周|每月|每季度|每年|每个工作日|每\s*(?:\d+|[一二三四五六七八九十]+)\s*(?:日|天|周|月|季度|年))",
        "unconfirmed_resource_commitment": (
            r"(?:调配后备资源|后备资源|备份人员|候补人员|资源池[^，。；;\n]{0,24}|"
            r"(?:获得|提供|确保|保障)[^，。；;\n]{0,20}"
            r"(?:技术与管理资源|技术资源|管理资源)[^，。；;\n]{0,12}(?:保障|支持)?)"
        ),
        "unconfirmed_staffing_assignment": (
            r"(?:双人复核|两人复核|另一名工程师|\d+\s*(?:人|名)|"
            r"(?:项目组织)?采用[^，。；;\n]{0,24}矩阵式(?:组织)?结构|"
            r"(?:项目组织)?(?:将)?设立[^。；;\n]{0,64}(?:组|部门)|"
            r"质量保障组[^。；;\n]{0,32}独立于[^。；;\n]{0,24}(?:组|部门))"
        ),
        "unconfirmed_bidder_commitment": r"(?:包括但不限于|额外[^，。；;\n]{0,24}|确保[^，。；;\n]{0,60}|保证[^，。；;\n]{0,60})",
    }
    pattern = patterns.get(code)
    match = re.search(pattern, sentence) if pattern else None
    return match.group(0) if match else ""


def _issue_from_validation_message(
    message: str,
    *,
    severity: str,
    section_ids: dict[str, str],
) -> dict[str, Any]:
    category = "writing_quality" if severity == "warning" else "structure"
    code = "validation_warning" if severity == "warning" else "validation_error"
    section_title: str | None = None

    scoped_prefixes = {
        "fact_boundary": "fact_boundary",
        "section_quality": "writing_quality",
        "generated_supporting_table": "table_contract",
        "required_response_table": "table_contract",
        "placeholder": "placeholder",
    }
    parts = message.split(":", 3)
    if len(parts) >= 3 and parts[0] in scoped_prefixes:
        category = scoped_prefixes[parts[0]]
        section_title = parts[1] or None
        code = parts[2] or parts[0]
        if code == "unverified_standard_reference":
            category = "source_verification"
    elif message.startswith("chapter_quality:"):
        category = "writing_quality"
        code = parts[1] if len(parts) > 1 and parts[1] else "chapter_quality"
    elif message.startswith("schema validation failed"):
        severity, category, code = "fatal", "schema", "schema_validation_failed"
    elif message.startswith("no structured final chapter was produced"):
        severity, category, code = "fatal", "runtime", "no_structured_candidate"
    elif message.startswith("chapter title changed"):
        category, code = "structure", "chapter_title_mismatch"
    elif message.startswith("section headings or order differ"):
        category, code = "structure", "section_outline_mismatch"
    elif message.startswith("target response points are not covered"):
        category, code = "coverage", "missing_response_points"
    elif message.startswith("target score items are not covered"):
        category, code = "coverage", "missing_score_items"
    elif message.startswith("draft references forbidden historical material"):
        category, code = (
            "source_verification",
            "forbidden_historical_reference",
        )
    elif message.startswith(
        "required project facts are unconfirmed but no pending confirmation"
    ):
        category, code = (
            "placeholder",
            "required_project_fact_pending_confirmation_missing",
        )
    elif message.startswith(
        "tender minimum/delivery requirement was converted"
    ):
        category, code = (
            "fact_boundary",
            "tender_requirement_promoted_to_commitment",
        )
    elif message.startswith(
        "confirmed enterprise claims require confirmed evidence"
    ):
        category, code = (
            "fact_boundary",
            "candidate_evidence_promoted_to_confirmed_fact",
        )
    elif message.startswith("section ") and "placeholder" in message:
        category = "placeholder"
        code = "placeholder_not_declared_in_section"
        section_match = re.match(
            r"section\s+(.+?)\s+references placeholder IDs", message
        )
        if section_match:
            section_title = section_match.group(1)
    elif message.startswith("invalid placeholder IDs"):
        category, code = "placeholder", "invalid_placeholder_id"
    elif message.startswith("unregistered placeholder IDs"):
        category, code = "placeholder", "unregistered_placeholder_id"
    elif message.startswith("section placeholder_ids must also"):
        category, code = "placeholder", "placeholder_missing_from_root"
    elif message.startswith("blocking pending confirmations are missing"):
        category, code = "placeholder", "blocking_placeholder_missing_from_content"
    elif message.startswith("pending confirmations exist"):
        category, code = "placeholder", "pending_confirmation_without_placeholder"
    elif message.startswith("registered placeholders remain"):
        category, code = "placeholder", "placeholder_pending_completion"
    elif message.startswith("required external verification was not attempted"):
        category, code = (
            "source_verification",
            "required_verification_not_attempted",
        )
    elif message.startswith("external verification remains pending"):
        category, code = "source_verification", "external_verification_pending"
    elif message.startswith("invalid Markdown table"):
        category, code = "table_contract", "invalid_markdown_table"
    elif message.startswith("required tender table was not reused"):
        category, code = "table_contract", "required_tender_table_missing"
    elif message.startswith("complex tender table was simplified"):
        category, code = "table_contract", "complex_tender_table_simplified"
    elif message.startswith("cross-section duplicate content"):
        category, code = "writing_quality", "cross_section_duplicate"

    section_id = section_ids.get(section_title or "") or None
    return _validation_issue(
        severity=severity,
        category=category,
        code=code,
        section_id=section_id,
        section_title=section_title,
        message=message,
    )


AUTO_REPAIRABLE_ISSUE_CODES = {
    "numeric_commitment",
    "schedule_frequency",
    "service_level_commitment",
    "unconfirmed_bidder_commitment",
    "unconfirmed_personnel_qualification",
    "personnel_narrative_without_source",
    "unconfirmed_resource_commitment",
    "unconfirmed_staffing_assignment",
    "confirmed_personnel_fact_mismatch",
    "unconfirmed_product_capability",
    "candidate_promoted_to_fact",
    "candidate_evidence_promoted_to_confirmed_fact",
    "unverified_standard_reference",
    "table_without_intro",
    "table_without_followup",
    "title_not_matched",
    "required_registered_placeholder_missing",
    "blocking_placeholder_missing_from_content",
    "placeholder_not_declared_in_section",
    "placeholder_missing_from_root",
    "unregistered_placeholder",
    "unregistered_placeholder_id",
    "missing_response_points",
    "missing_score_items",
    "chapter_title_mismatch",
    "section_outline_mismatch",
    "invalid_markdown_table",
    "required_tender_table_missing",
    "complex_tender_table_simplified",
    "declared_but_missing",
    "missing_purpose_metadata",
    "missing_title",
    "missing_purpose",
    "missing_table_rationale",
    "dangling_table_reference",
    "inline_step_chain",
    "insufficient_prose",
    "no_complete_prose",
    "only_tables_or_lists",
    "ppt_style_short_bullets",
    "bullet_dominant",
    "list_dominant_section",
    "structured_content_exceeds_prose",
    "cross_section_duplicate",
}

REVIEW_ONLY_ISSUE_CODES = {
    "placeholder_pending_completion",
    "pending_confirmation_without_placeholder",
    "missing_pending_confirmation",
    "required_project_fact_pending_confirmation_missing",
    "evidence_pending",
    "positive_deviation_pending_confirmation",
    "negative_deviation",
    "negative_deviation_marked_no_deviation",
    "fixed_requirement_difference_requires_review",
    "required_verification_not_attempted",
    "external_verification_pending",
    "required_proof_missing",
    "critical_product_model_missing",
    "unsupported_major_service_commitment",
    "pending_objective_fact",
    "knowledge_candidate_selection_required",
    "prohibited_material_detected",
    "required_specific_value_missing",
    "invalid_placeholder_id",
}


def _repair_disposition_for_issue(issue: dict[str, Any]) -> str:
    code = str(issue.get("code") or "")
    category = str(issue.get("category") or "")
    if issue.get("severity") == "fatal" or category in {
        "runtime",
        "schema",
        "protocol",
    }:
        return "review_only"
    if code in REVIEW_ONLY_ISSUE_CODES:
        return "review_only"
    if code in AUTO_REPAIRABLE_ISSUE_CODES:
        return "auto_repairable"
    if category in {
        "writing_quality",
        "section_quality",
        "chapter_quality",
        "table_contract",
        "generated_supporting_table",
        "required_response_table",
        "coverage",
        "structure",
    }:
        return "auto_repairable"
    return "review_only"


def classify_chapter_audit(
    validation: dict[str, Any],
    *,
    pending_confirmations: list[dict[str, Any]] | None = None,
    prohibited_material_ids: list[str] | None = None,
    local_evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize deterministic chapter validation into the four production classes."""
    groups: dict[str, list[dict[str, Any]]] = {
        "auto_fixable": [],
        "human_confirmation_required": [],
        "release_blocking": [],
        "execution_error": [],
    }
    objective_codes = {
        "missing_pending_confirmation",
        "placeholder_pending_completion",
        "pending_confirmation_without_placeholder",
        "required_verification_not_attempted",
        "external_verification_pending",
        "positive_deviation_pending_confirmation",
        "fixed_requirement_difference_requires_review",
        "evidence_pending",
    }
    execution_categories = {"runtime", "schema", "protocol"}
    direct_release_codes = {
        "negative_deviation",
        "negative_deviation_marked_no_deviation",
        "prohibited_material_detected",
        "required_specific_value_missing",
        "required_proof_missing",
        "critical_product_model_missing",
        "unsupported_major_service_commitment",
    }
    for raw in validation.get("issues") or []:
        issue = deepcopy(raw)
        if not issue.get("finding_id"):
            identity = "|".join(
                str(issue.get(key) or "")
                for key in ("category", "code", "section_id", "message")
            )
            issue["finding_id"] = "AUD-" + hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()[:12].upper()
        code = str(issue.get("code") or "")
        category = str(issue.get("category") or "")
        issue["repair_disposition"] = _repair_disposition_for_issue(issue)
        if issue.get("severity") == "fatal" or category in execution_categories:
            classification = "execution_error"
        elif code in direct_release_codes:
            classification = "release_blocking"
        elif code in objective_codes:
            classification = "human_confirmation_required"
        else:
            classification = "auto_fixable"
        issue["classification"] = classification
        groups[classification].append(issue)
        if (
            issue.get("severity") == "blocking"
            and classification
            not in {"auto_fixable", "release_blocking", "execution_error"}
        ):
            blocker = deepcopy(issue)
            blocker["classification"] = "release_blocking"
            groups["release_blocking"].append(blocker)

    for item in pending_confirmations or []:
        identity = "|".join(
            (
                str(item.get("placeholder_id") or ""),
                str(item.get("field") or ""),
                str(item.get("reason") or ""),
            )
        )
        finding = {
            "finding_id": "AUD-" + hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()[:12].upper(),
            "classification": "human_confirmation_required",
            "repair_disposition": "review_only",
            "severity": item.get("severity") or (
                "blocking" if item.get("blocking", True) else "warning"
            ),
            "category": item.get("category") or "objective_fact",
            "code": "pending_objective_fact",
            "section_id": None,
            "section_title": None,
            "message": item.get("reason") or item.get("question") or "",
            "placeholder_id": item.get("placeholder_id"),
        }
        if finding["finding_id"] not in {
            value.get("finding_id")
            for value in groups["human_confirmation_required"]
        }:
            groups["human_confirmation_required"].append(finding)
        if item.get("release_blocking", item.get("blocking", True)):
            blocker = deepcopy(finding)
            blocker["classification"] = "release_blocking"
            groups["release_blocking"].append(blocker)

    unresolved_by_type: dict[str, list[dict[str, Any]]] = {}
    for item in local_evidence_refs or []:
        if item.get("selection_status") == "confirmed":
            continue
        if item.get("evidence_status") in {"rejected", "unusable", "missing"}:
            continue
        unresolved_by_type.setdefault(
            str(item.get("evidence_type") or "other"),
            [],
        ).append(item)
    for evidence_type, candidates in unresolved_by_type.items():
        candidate_ids = [
            str(item.get("candidate_id") or "")
            for item in candidates[:3]
            if item.get("candidate_id")
        ]
        identity = f"candidate_selection|{evidence_type}|{'|'.join(candidate_ids)}"
        groups["human_confirmation_required"].append(
            {
                "finding_id": "AUD-" + hashlib.sha256(
                    identity.encode("utf-8")
                ).hexdigest()[:12].upper(),
                "classification": "human_confirmation_required",
                "repair_disposition": "review_only",
                "severity": "warning",
                "category": "evidence_candidate",
                "code": "knowledge_candidate_selection_required",
                "message": (
                    "candidate evidence requires project-specific applicability confirmation"
                ),
                "evidence_type": evidence_type,
                "candidate_ids": candidate_ids,
                "recommended_candidate": candidate_ids[0]
                if candidate_ids
                else None,
            }
        )

    for material_id in prohibited_material_ids or []:
        identity = f"prohibited_material|{material_id}"
        groups["release_blocking"].append(
            {
                "finding_id": "AUD-" + hashlib.sha256(
                    identity.encode("utf-8")
                ).hexdigest()[:12].upper(),
                "classification": "release_blocking",
                "repair_disposition": "review_only",
                "severity": "blocking",
                "category": "evidence",
                "code": "prohibited_material_detected",
                "message": f"rejected or unusable material detected: {material_id}",
                "material_id": material_id,
            }
        )
    auto_repairable = [
        item
        for values in groups.values()
        for item in values
        if item.get("repair_disposition") == "auto_repairable"
    ]
    review_only = [
        item
        for values in groups.values()
        for item in values
        if item.get("repair_disposition") == "review_only"
    ]
    auto_repairable = list(
        {str(item.get("finding_id")): item for item in auto_repairable}.values()
    )
    review_only = list(
        {str(item.get("finding_id")): item for item in review_only}.values()
    )
    unique_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in [*auto_repairable, *review_only]:
        message_parts = str(item.get("message") or "").split(":", 3)
        offending_span = str(
            item.get("offending_span")
            or (message_parts[3] if len(message_parts) == 4 else "")
            or item.get("placeholder_id")
            or item.get("message")
        )
        key = (
            str(item.get("severity") or ""),
            str(item.get("category") or ""),
            str(item.get("code") or ""),
            str(item.get("section_id") or item.get("section_title") or ""),
            re.sub(r"[\W_]+", "", offending_span, flags=re.UNICODE).casefold(),
        )
        existing = unique_by_key.get(key)
        if existing is None or (
            existing.get("repair_disposition") == "auto_repairable"
            and item.get("repair_disposition") == "review_only"
        ):
            unique_by_key[key] = item
    unique_issues = list(unique_by_key.values())
    auto_repairable = [
        item
        for item in unique_issues
        if item.get("repair_disposition") == "auto_repairable"
    ]
    review_only = [
        item
        for item in unique_issues
        if item.get("repair_disposition") == "review_only"
    ]
    return {
        **groups,
        "auto_repairable": auto_repairable,
        "review_only": review_only,
        "unique_issues": unique_issues,
        "unique_issue_count": len(unique_issues),
        "auto_repairable_unique_issue_count": len(auto_repairable),
        "review_only_unique_issue_count": len(review_only),
        "counts": {key: len(value) for key, value in groups.items()},
    }


def chapter_completion_status(
    *,
    draft: dict[str, Any] | None,
    audit: dict[str, Any],
    repair_rounds: list[dict[str, Any]] | None = None,
    max_auto_repair_rounds: int = 1,
    selected_material_ids: list[str] | None = None,
    local_evidence_refs: list[dict[str, Any]] | None = None,
) -> str:
    if not isinstance(draft, dict) or audit.get("execution_error"):
        return "execution_failed"
    if audit.get("auto_fixable"):
        attempted = len(repair_rounds or [])
        return (
            "auto_repair_exhausted"
            if attempted >= max_auto_repair_rounds
            else "auto_repair_exhausted"
        )
    if audit.get("release_blocking"):
        return "release_blocked"
    unresolved_candidates = [
        item
        for item in local_evidence_refs or []
        if item.get("selection_status") != "confirmed"
        and item.get("evidence_status")
        in {"confirmed", "candidate", "needs_review", "unknown"}
        and item.get("candidate_id") not in set(selected_material_ids or [])
    ]
    if unresolved_candidates:
        return "complete_with_candidate_evidence"
    if audit.get("human_confirmation_required"):
        return "complete_with_pending_facts"
    return "complete"


def chapter_run_status(
    draft: dict[str, Any] | None,
    validation: dict[str, Any],
    pending_confirmations: list[dict[str, Any]],
) -> tuple[str, str, bool]:
    if not isinstance(draft, dict):
        return "failed", "invalid_draft", False
    issue_counts = validation.get("issue_counts") or {}
    fatal_count = int(issue_counts.get("fatal") or 0)
    blocking_count = int(issue_counts.get("blocking") or 0)
    if not issue_counts and not validation.get("valid"):
        return "failed", "invalid_draft", False
    if fatal_count:
        return "failed", "invalid_draft", False
    if blocking_count:
        return "completed", "reviewable_draft_with_blockers", False
    placeholders = list(draft.get("placeholders_used") or [])
    if placeholders:
        return "completed", "initial_draft_with_placeholders", False
    active_pending_count = validation.get("active_pending_confirmation_count")
    if (
        int(active_pending_count)
        if active_pending_count is not None
        else len(pending_confirmations)
    ):
        return "completed", "initial_draft_pending_confirmation", False
    return "completed", "initial_draft", True


def build_technical_writing_agent(
    agent_input: TechnicalWritingAgentInput,
    *,
    llm: LLMClient | None = None,
    output_dir: str | Path = "outputs",
    model_profile: str | None = None,
    web_search_provider: str | None = None,
    disable_web: bool = False,
    max_agent_steps: int = 12,
    max_search_calls: int = 4,
    max_local_knowledge_calls: int = 3,
    max_web_search_calls: int = 1,
    max_pending_confirmation_calls: int = 8,
    max_auto_repair_rounds: int = 1,
    metadata_store: Any = None,
    web_provider: Any = None,
) -> TechnicalWritingAgent:
    if agent_input.instruction_scope not in {"once", "project", "global"}:
        raise ValueError("instruction_scope must be once, project or global")
    if disable_web and agent_input.web_search_policy == "required":
        raise ValueError("web_search_policy=required conflicts with disable_web")
    if not 0 <= max_auto_repair_rounds <= 1:
        raise ValueError("max_auto_repair_rounds must be between 0 and 1")
    effective_web_policy = "disabled" if disable_web else agent_input.web_search_policy
    model_client = llm or get_llm_client(role="drafting", profile_name=model_profile)
    context = TechnicalWritingToolContext(
        response_outline_path=Path(agent_input.response_outline_path),
        writing_outline_path=Path(agent_input.writing_outline_path),
        task_information_path=Path(agent_input.task_information_path),
        draft_units_path=Path(agent_input.draft_units_path) if agent_input.draft_units_path else None,
        confirmed_project_facts_path=Path(agent_input.confirmed_project_facts_path) if agent_input.confirmed_project_facts_path else None,
        writing_constraint_context=deepcopy(agent_input.writing_constraint_context or {}),
        tender_metadata=deepcopy(agent_input.tender_metadata or {}),
        output_root=Path(output_dir),
        output_key=agent_input.output_key,
        target_chapter_id=agent_input.target_chapter_id,
        target_section_id=agent_input.target_section_id,
        metadata_store=metadata_store or MetadataStore(),
        web_provider=web_provider or get_web_search_provider(
            web_search_provider,
            llm_client=model_client,
        ),
        web_disabled=disable_web,
        web_search_policy=effective_web_policy,
        local_evidence_refs=[
            {
                "candidate_id": item["material_or_evidence_ref"],
                "evidence_id": item["material_or_evidence_ref"],
                "evidence_type": (
                    (item.get("evidence_types") or [""])[0]
                ),
                "evidence_types": deepcopy(item.get("evidence_types") or []),
                "file_name": item.get("material_name"),
                "source_location": item.get("file_reference")
                or item.get("candidate_reference"),
                "selection_status": "confirmed",
                "evidence_status": "confirmed",
                "source_lineage": "stage12_material_verification",
                "verification_note": item.get("verification_note"),
            }
            for item in agent_input.stage12_approved_material_refs
        ],
        selected_material_ids=[
            item["material_or_evidence_ref"]
            for item in agent_input.stage12_approved_material_refs
        ],
    )
    schema_path = Path(__file__).resolve().parents[1] / "agents" / "technical_writer" / "chapter_draft_output_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    safe_instruction = sanitize_user_instruction(agent_input.user_instruction)[:40_000]
    safe_summary = (
        agent_input.user_instruction_summary
        or re.sub(r"\s+", " ", safe_instruction).strip()[:600]
    )
    writing_rules = load_writing_rules(agent_input.writing_rules_path)
    project_constraints = load_project_constraints(output_dir, agent_input.output_key)
    return TechnicalWritingAgent(
        llm=model_client,
        tool_context=context,
        registry=build_technical_tool_registry(context),
        chapter_schema=schema,
        max_agent_steps=max_agent_steps,
        max_search_calls=max_search_calls,
        max_local_knowledge_calls=max_local_knowledge_calls,
        max_web_search_calls=max_web_search_calls,
        max_pending_confirmation_calls=max_pending_confirmation_calls,
        max_auto_repair_rounds=max_auto_repair_rounds,
        profile_name=model_profile,
        user_instruction=safe_instruction,
        user_instruction_sources=agent_input.user_instruction_sources,
        user_instruction_summary=safe_summary,
        web_search_policy=effective_web_policy,
        instruction_scope=agent_input.instruction_scope,
        writing_rules=writing_rules,
        project_constraints=project_constraints,
        stage12_approved_facts=tuple(
            deepcopy(agent_input.stage12_approved_facts)
        ),
        stage12_approved_material_refs=tuple(
            deepcopy(agent_input.stage12_approved_material_refs)
        ),
        stage12_prohibited_inferences=tuple(
            deepcopy(agent_input.stage12_prohibited_inferences)
        ),
        stage12_acceptance_criteria=tuple(agent_input.stage12_acceptance_criteria),
    )


def _active_pending_confirmations(
    pending_confirmations: list[dict[str, Any]],
    *,
    generation_permissions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    tender_baselines = confirmation_baselines_from_generation_permissions(
        generation_permissions
    )
    active: list[dict[str, Any]] = []
    ignored: list[str] = []
    for item in pending_confirmations:
        if not isinstance(item, dict):
            active.append(item)
            continue
        status = confirmation_requirement(
            item,
            tender_baselines=tender_baselines,
        )
        if status in {"satisfied", "not_required"}:
            placeholder_id = str(item.get("placeholder_id") or "")
            if placeholder_id:
                ignored.append(placeholder_id)
            continue
        active.append(item)
    return active, list(dict.fromkeys(ignored))


def validate_technical_chapter(
    draft: dict[str, Any] | None,
    *,
    schema: dict[str, Any],
    target: dict[str, Any],
    confirmed_facts: dict[str, Any],
    pending_confirmations: list[dict[str, Any]],
    web_search_policy: str = "auto",
    web_verification: dict[str, Any] | None = None,
    tender_tables: list[dict[str, Any]] | None = None,
    quality_review: dict[str, Any] | None = None,
    candidate_evidence_ids: set[str] | None = None,
    confirmed_evidence_ids: set[str] | None = None,
    generation_permissions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(draft, dict):
        issues = [
            _validation_issue(
                severity="fatal",
                category="runtime",
                code="no_structured_candidate",
                message="no structured final chapter was produced",
            )
        ]
        return {
            "valid": False,
            "validation_status": "fatal",
            "candidate_generated": False,
            "candidate_saved": False,
            "candidate_available": False,
            "candidate_reviewable": False,
            "submission_ready": False,
            "issues": issues,
            "issue_counts": _validation_issue_counts(issues),
            "errors": ["no structured final chapter was produced"],
            "warnings": [],
        }
    try:
        Draft202012Validator(schema).validate(draft)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path) or "$"
        errors.append(f"schema validation failed at {path}: {exc.message}")
    expected_title = str(target.get("title") or "")
    if str(draft.get("chapter_title") or "") != expected_title:
        errors.append(f"chapter title changed: expected {expected_title}")
    expected_headings = [str(item.get("title") or "") for item in target.get("children") or []]
    actual_headings = [str(item.get("heading") or "") for item in draft.get("sections") or []]
    if expected_headings and actual_headings != expected_headings:
        errors.append("section headings or order differ from the writing outline")
    expected_response_ids = set(_collect_target_requirement_values(target, "response_point_ids"))
    covered_response_ids = set(_covered_ids(draft, "requirements_covered", "covered_requirement_ids"))
    missing_response_ids = sorted(expected_response_ids - covered_response_ids)
    if missing_response_ids:
        errors.append(f"target response points are not covered: {missing_response_ids}")
    expected_score_ids = set(_collect_target_requirement_values(target, "score_item_refs"))
    covered_score_ids = set(_covered_ids(draft, "score_items_covered", "covered_score_item_ids"))
    missing_score_ids = sorted(expected_score_ids - covered_score_ids)
    if missing_score_ids:
        errors.append(f"target score items are not covered: {missing_score_ids}")
    all_text = "\n".join(str(item.get("content") or "") for item in draft.get("sections") or [])
    active_pending_confirmations, ignored_pending_confirmation_ids = (
        _active_pending_confirmations(
            pending_confirmations,
            generation_permissions=generation_permissions or [],
        )
    )
    registered_placeholder_ids = {
        str(item.get("placeholder_id") or "")
        for item in pending_confirmations
        if isinstance(item, dict) and item.get("placeholder_id")
    }
    if "reference_only" in all_text:
        errors.append("draft references forbidden historical material")
    if not confirmed_facts:
        manual_fact_types = {
            str(fact_type)
            for item in [target, *_all_target_nodes(target)]
            if ((item.get("writing_requirements") or {}).get("manual_project_facts") or {}).get("need") == "required"
            for fact_type in ((item.get("writing_requirements") or {}).get("manual_project_facts") or {}).get("fact_types") or []
        }
        requires_manual = bool(manual_fact_types)
        baseline_semantics = {
            str(item.get("semantic_field") or "")
            for item in confirmation_baselines_from_generation_permissions(
                generation_permissions or []
            )
        }
        tender_baselines = confirmation_baselines_from_generation_permissions(
            generation_permissions or []
        )

        def manual_fact_has_exact_tender_baseline(fact_type: str) -> bool:
            semantic = (
                "delivery" if "delivery" in fact_type.casefold()
                else "installation" if "installation" in fact_type.casefold()
                else ""
            )
            for baseline in tender_baselines:
                if str(baseline.get("semantic_field") or "") != semantic:
                    continue
                if confirmation_requirement(
                    {
                        "fact_semantic_field": semantic,
                        "proposed_value": baseline.get("value"),
                        "unit": baseline.get("unit"),
                        "start_reference": baseline.get("start_reference"),
                        "tender_source_id": baseline.get("source_id"),
                    },
                    tender_baselines=tender_baselines,
                ) == "not_required":
                    return True
            return False
        baseline_only_manual_facts = bool(manual_fact_types) and all(
            (
                "delivery" in fact_type.casefold()
                and "delivery" in baseline_semantics
                and manual_fact_has_exact_tender_baseline(fact_type)
            )
            or (
                "installation" in fact_type.casefold()
                and "installation" in baseline_semantics
                and manual_fact_has_exact_tender_baseline(fact_type)
            )
            for fact_type in manual_fact_types
        )
        if (
            requires_manual
            and not baseline_only_manual_facts
            and not active_pending_confirmations
        ):
            errors.append("required project facts are unconfirmed but no pending confirmation was recorded")
    confirmed_fact_values = {
        str(value).strip()
        for value in confirmed_facts.values()
        if value is not None
        and not isinstance(value, (dict, list))
        and str(value).strip()
    }
    candidate_claims = [
        item for item in draft.get("claims") or []
        if item.get("claim_type") == "confirmed_enterprise_fact"
        and not (
            any(
                value in str(item.get("text") or "")
                for value in confirmed_fact_values
            )
        )
        and not _registered_placeholder_claim_is_nondefinite(
            str(item.get("text") or ""),
            registered_placeholder_ids,
        )
        and (
            not item.get("evidence_ids")
            or bool(
                set(str(value) for value in item.get("evidence_ids") or [])
                & set(candidate_evidence_ids or set())
            )
            or not bool(
                set(str(value) for value in item.get("evidence_ids") or [])
                & set(confirmed_evidence_ids or set())
            )
        )
    ]
    if candidate_claims:
        errors.append("confirmed enterprise claims require confirmed evidence, not candidates")
    boundary_errors = inspect_unconfirmed_commitments(
        draft,
        confirmed_facts=confirmed_facts,
        candidate_evidence_ids=candidate_evidence_ids or set(),
        confirmed_evidence_ids=confirmed_evidence_ids or set(),
        generation_permissions=generation_permissions or [],
    )
    errors.extend(boundary_errors)
    candidate_promoted_payloads = [
        str(item).split(":", 3)[-1]
        for item in boundary_errors
        if ":candidate_promoted_to_fact:" in str(item)
    ]
    errors.extend(
        item
        for item in inspect_deviation_issues(
            draft,
            registered_placeholder_ids=registered_placeholder_ids,
        )
        if not (
            ":evidence_pending:" in item
            and any(
                _validation_texts_overlap(
                    str(item).split(":", 3)[-1],
                    payload,
                )
                for payload in candidate_promoted_payloads
            )
        )
    )
    permission_audit = (
        audit_generation_permissions(
            draft,
            fact_ledger=generation_permissions,
        )
        if generation_permissions is not None
        else {
            "passed": True,
            "unauthorized_numeric_claims": [],
            "unauthorized_personnel_claims": [],
            "unauthorized_product_capabilities": [],
            "candidate_facts_promoted": [],
            "unregistered_placeholders": [],
        }
    )
    for finding in permission_audit.get("unauthorized_personnel_claims") or []:
        errors = [
            item
            for item in errors
            if not (
                ":numeric_commitment:" in item
                and str(finding.get("section_title") or "") in item
                and _validation_texts_overlap(
                    str(item).split(":", 3)[-1],
                    str(finding.get("text") or ""),
                )
            )
        ]
    permission_errors = generation_permission_audit_errors(permission_audit)
    for permission_error in permission_errors:
        parts = str(permission_error).split(":", 3)
        duplicate = bool(
            len(parts) == 4
            and any(
                len(existing_parts := str(existing).split(":", 3)) == 4
                and existing_parts[1] == parts[1]
                and existing_parts[2] == parts[2]
                and _validation_texts_overlap(existing_parts[3], parts[3])
                for existing in errors
            )
        )
        if not duplicate:
            errors.append(permission_error)
    errors = _dedupe_personnel_fact_boundary_errors(errors)
    if active_pending_confirmations and not draft.get("placeholders_used"):
        warnings.append("pending confirmations exist but placeholders_used is empty")
    draft_declares_placeholder_ids = any(
        PLACEHOLDER_ID_PATTERN.fullmatch(value)
        for value in [
            *_string_list(draft.get("placeholders_used")),
            *[
                placeholder_id
                for section in draft.get("sections") or []
                for placeholder_id in _string_list(section.get("placeholder_ids"))
            ],
            *extract_placeholder_ids(all_text),
        ]
    )
    if registered_placeholder_ids or draft_declares_placeholder_ids:
        root_placeholder_ids = set(_string_list(draft.get("placeholders_used")))
        section_placeholder_ids: set[str] = set()
        content_placeholder_ids: set[str] = set()
        for section in draft.get("sections") or []:
            listed = set(_string_list(section.get("placeholder_ids")))
            content_ids = set(extract_placeholder_ids(str(section.get("content") or "")))
            section_placeholder_ids.update(listed)
            content_placeholder_ids.update(content_ids)
            if content_ids - listed:
                errors.append(
                    f"section {section.get('heading')} references placeholder IDs not listed in placeholder_ids: "
                    f"{sorted(content_ids - listed)}"
                )
        all_used_ids = root_placeholder_ids | section_placeholder_ids | content_placeholder_ids
        malformed = sorted(
            value for value in all_used_ids if not PLACEHOLDER_ID_PATTERN.fullmatch(value)
        )
        if malformed:
            errors.append(f"invalid placeholder IDs: {malformed}")
        unknown = sorted(all_used_ids - registered_placeholder_ids)
        if unknown:
            errors.append(f"unregistered placeholder IDs were used: {unknown}")
        if section_placeholder_ids - root_placeholder_ids:
            errors.append(
                "section placeholder_ids must also be declared in placeholders_used: "
                f"{sorted(section_placeholder_ids - root_placeholder_ids)}"
            )
        blocking_ids = {
            str(item.get("placeholder_id"))
            for item in active_pending_confirmations
            if isinstance(item, dict) and item.get("placeholder_id") and item.get("blocking", True)
        }
        for placeholder_id in sorted(blocking_ids - content_placeholder_ids):
            pending_item = next(
                (
                    item
                    for item in active_pending_confirmations
                    if str(item.get("placeholder_id") or "") == placeholder_id
                ),
                {},
            )
            affected = list(pending_item.get("affected_sections") or [])
            section_title = str(affected[0]) if affected else "相关小节"
            errors.append(
                f"placeholder:{section_title}:"
                f"required_registered_placeholder_missing:{placeholder_id}"
            )
    verification = web_verification or {"status": "not_requested"}
    if web_search_policy == "required" and verification.get("status") == "not_requested":
        errors.append("required external verification was not attempted")
    if verification.get("status") in {"insufficient", "unavailable"}:
        warnings.append(f"external verification remains pending: {verification.get('status')}")
    errors.extend(
        inspect_unverified_standard_references(
            draft,
            web_verification=verification,
            confirmed_evidence_ids=confirmed_evidence_ids or set(),
        )
    )
    review = quality_review or review_chapter_quality(
        draft,
        tender_tables=tender_tables or [],
        source_only_personnel_section_ids=(
            _source_only_personnel_section_references(
                generation_permissions or []
            )
        ),
    )
    errors.extend(
        f"invalid Markdown table: {item}"
        for item in review.get("markdown_table_errors") or []
    )
    errors.extend(str(item) for item in review.get("quality_errors") or [])
    warnings.extend(str(item) for item in review.get("quality_warnings") or [])
    reused_table_ids = {
        str(item.get("table_id") or "")
        for item in review.get("tender_tables_reused") or []
    }
    complex_states = {
        str(item.get("table_id") or ""): bool(item.get("preserved"))
        for item in review.get("complex_tender_tables_preserved") or []
    }
    for table in tender_tables or []:
        table_id = str(table.get("table_id") or "")
        if (
            table.get("table_type") == "required_response_table"
            and
            table.get("required")
            and table.get("layout_complexity") == "simple"
            and table_id not in reused_table_ids
        ):
            errors.append(f"required tender table was not reused: {table_id}")
        if (
            table.get("table_type") == "required_response_table"
            and
            table.get("layout_complexity") == "complex"
            and complex_states.get(table_id) is False
        ):
            errors.append(f"complex tender table was simplified in Markdown: {table_id}")
    for item in review.get("duplicate_section_pairs") or []:
        warnings.append(
            "cross-section duplicate content: "
            f"{item.get('left_section')} / {item.get('right_section')} "
            f"({item.get('severity')})"
        )
    warnings.extend(str(item) for item in review.get("soft_warnings") or [])
    if draft.get("placeholders_used"):
        warnings.append("registered placeholders remain pending completion")
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    issues = _normalize_validation_issues(
        errors=errors,
        warnings=warnings,
        draft=draft,
        target=target,
    )
    issues = _attach_generation_permission_issue_details(
        issues,
        permission_audit=permission_audit,
    )
    issues = _sanitize_fact_boundary_issue_spans(issues, draft=draft)
    errors = [
        str(issue.get("message") or "")
        for issue in issues
        if issue.get("severity") in {"fatal", "blocking"}
    ]
    issue_counts = _validation_issue_counts(issues)
    validation_status = (
        "fatal"
        if issue_counts["fatal"]
        else "blocked"
        if issue_counts["blocking"]
        else "passed_with_warnings"
        if issue_counts["warning"]
        else "passed"
    )
    candidate_reviewable = not bool(issue_counts["fatal"])
    submission_ready = (
        candidate_reviewable
        and not bool(issue_counts["blocking"])
        and not bool(draft.get("placeholders_used"))
        and not bool(active_pending_confirmations)
        and verification.get("status") not in {"insufficient", "unavailable"}
    )
    return {
        "valid": not errors,
        "validation_status": validation_status,
        "candidate_generated": True,
        "candidate_saved": False,
        "candidate_available": False,
        "candidate_reviewable": candidate_reviewable,
        "submission_ready": submission_ready,
        "issues": issues,
        "issue_counts": issue_counts,
        "errors": errors,
        "warnings": warnings,
        "expected_section_titles": expected_headings,
        "actual_section_titles": actual_headings,
        "title_count": len(actual_headings),
        "expected_response_point_ids": sorted(expected_response_ids),
        "missing_response_point_ids": missing_response_ids,
        "expected_score_item_ids": sorted(expected_score_ids),
        "missing_score_item_ids": missing_score_ids,
        "markdown_table_count": len(review.get("markdown_tables") or []),
        "technical_writing_quality": {
            "chapter": review.get("chapter_metrics") or {},
            "sections": review.get("section_metrics") or [],
            "sections_with_insufficient_prose": review.get(
                "sections_with_insufficient_prose"
            )
            or [],
        },
        "duplicate_section_pairs": review.get("duplicate_section_pairs") or [],
        "generation_permission_audit": permission_audit,
        "chapter_tender_fact_permission_count": int(
            permission_audit.get("chapter_tender_fact_permission_count") or 0
        ),
        "chapter_tender_numeric_permission_count": int(
            permission_audit.get("chapter_tender_numeric_permission_count") or 0
        ),
        "effective_tender_fact_ids_by_section": deepcopy(
            permission_audit.get("effective_tender_fact_ids_by_section") or {}
        ),
        "effective_tender_numeric_fact_ids_by_section": deepcopy(
            permission_audit.get("effective_tender_numeric_fact_ids_by_section")
            or {}
        ),
        "active_pending_confirmation_count": len(active_pending_confirmations),
        "active_pending_confirmation_ids": [
            str(item.get("placeholder_id"))
            for item in active_pending_confirmations
            if item.get("placeholder_id")
        ],
        "ignored_pending_confirmation_ids": ignored_pending_confirmation_ids,
    }


def render_technical_chapter_markdown(
    draft: dict[str, Any] | None,
    target: dict[str, Any],
    validation: dict[str, Any],
) -> str:
    title = str((draft or {}).get("chapter_title") or target.get("title") or "")
    lines = [f"# {title}", ""]
    if not draft:
        lines.extend(["【生成失败：未获得可校验的结构化章节草稿】", ""])
        return "\n".join(lines)
    for section in draft.get("sections") or []:
        lines.extend([f"## {section.get('heading')}", "", str(section.get("content") or ""), ""])
    if not validation.get("valid"):
        lines.extend(["<!-- 本草稿未通过运行时校验，不得作为可提交版本。 -->", ""])
    issue_markers = [
        {
            "severity": item.get("severity"),
            "category": item.get("category"),
            "code": item.get("code"),
            "section_id": item.get("section_id"),
            "section_title": item.get("section_title"),
        }
        for item in validation.get("issues") or []
        if item.get("severity") in {"blocking", "warning"}
    ]
    if issue_markers:
        marker_json = json.dumps(
            issue_markers,
            ensure_ascii=False,
            separators=(",", ":"),
        ).replace("--", "——")
        lines.extend(
            [
                "<!-- technical-writing-issues",
                marker_json,
                "-->",
                "",
            ]
        )
    return "\n".join(lines)


def repair_existing_technical_writing_run(
    run_path: str | Path,
    markdown_path: str | Path,
) -> dict[str, Any]:
    """Offline-only repair of the report/draft contract; it performs no retrieval or generation."""
    report_path = Path(run_path).expanduser().resolve()
    draft_path = Path(markdown_path).expanduser().resolve()
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    raw_final = extract_chapter_draft_candidate(report)
    normalized = normalize_chapter_draft_output(raw_final)
    old_validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    target_data = report.get("target") if isinstance(report.get("target"), dict) else {}
    target = {
        "chapter_id": target_data.get("chapter_id"),
        "title": target_data.get("title"),
        "writing_requirements": {
            "response_point_ids": list(old_validation.get("expected_response_point_ids") or []),
            "score_item_refs": list(old_validation.get("expected_score_item_ids") or []),
        },
        "children": [
            {"chapter_id": f"offline-{index:02d}", "title": title, "children": []}
            for index, title in enumerate(
                list(old_validation.get("expected_section_titles") or []),
                start=1,
            )
        ],
    }
    schema_path = Path(__file__).resolve().parents[1] / "agents" / "technical_writer" / "chapter_draft_output_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    pending = list(report.get("pending_confirmations") or [])
    validation = validate_technical_chapter(
        normalized,
        schema=schema,
        target=target,
        confirmed_facts={},
        pending_confirmations=pending,
    )
    status, draft_status, submission_ready = chapter_run_status(normalized, validation, pending)
    issue_counts = validation.get("issue_counts") or {}
    if issue_counts.get("fatal"):
        stop_reason = "fatal_validation_failed"
    elif issue_counts.get("blocking"):
        stop_reason = "candidate_validation_blocked"
    else:
        stop_reason = "offline_schema_contract_repaired"
    report.pop("structured_chapter_draft", None)
    report.update(
        {
            "status": status,
            "candidate_generated": True,
            "candidate_saved": False,
            "generation_completed": True,
            "candidate_available": False,
            "candidate_reviewable": bool(
                validation.get("candidate_reviewable")
            ),
            "stop_reason": stop_reason,
            "draft_status": draft_status,
            "submission_ready": submission_ready,
            "model_final_output": raw_final,
            "chapter_draft": normalized,
            "response_point_usage": list(normalized.get("requirements_covered") or []),
            "score_item_usage": list(normalized.get("score_items_covered") or []),
            "placeholders": list(normalized.get("placeholders_used") or []),
            "validation": validation,
            "failures": [
                str(item.get("message") or "")
                for item in validation.get("issues") or []
                if item.get("severity") == "fatal"
            ],
            "warnings": list(validation.get("warnings") or []),
        }
    )
    if isinstance(report.get("run_summary"), dict):
        report["run_summary"]["stop_reason"] = stop_reason
    history = list(report.get("repair_history") or [])
    history.append(
        {
            "type": "schema_contract_repair",
            "model_recalled": False,
            "web_recalled": False,
            "knowledge_requeried": False,
        }
    )
    report["repair_history"] = history
    draft_path.write_text(
        render_technical_chapter_markdown(normalized, target, validation),
        encoding="utf-8",
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _section_fact_ledger_message(
    *,
    fact_ledger: list[dict[str, Any]],
) -> str:
    chapter_permissions = _chapter_fact_permissions_from_ledger(fact_ledger)
    return (
        "[Section-specific Fact Ledger] chapter_fact_permissions与generation_permissions"
        "是首次生成和Fresh共用的封闭许可，不是建议；"
        "正文中的确定事实、"
        "承诺型数字、人员资历、产品能力和占位符只能来自对应allowed_*列表；"
        "未列出的数字、人员门槛、产品实现机制、履约时限和公司资源事实不得生成。"
        "没有来源许可的内容必须不写；不得为了正文完整、专业性或行业惯例"
        "补充未确认事实。无人员来源时只允许不涉及具体人选、资历、数量和资源的"
        "中性项目角色动作与协作原则。"
        "评分覆盖只写入score_items_covered、requirements_covered等metadata，不得写成商务事实正文。"
        "若创建普通辅助表，正文必须使用"
        "metadata.title完全相同的显式表题，并满足intro、followup和column contract。\n"
        + json.dumps(
            {
                "chapter_fact_permissions": chapter_permissions,
                "section_fact_ledger": fact_ledger,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _apply_personnel_content_gate(
    draft: dict[str, Any],
    *,
    fact_ledger: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    gated = deepcopy(draft)
    ledger_by_reference = {
        str(reference): entry
        for entry in fact_ledger
        if isinstance(entry, dict)
        for reference in (entry.get("section_id"), entry.get("section_title"))
        if reference
    }
    source_only_ids: list[str] = []
    removed_characters = 0
    allowed_placeholder_ids: set[str] = set()
    for section in gated.get("sections") or []:
        if not isinstance(section, dict):
            continue
        entry = (
            ledger_by_reference.get(str(section.get("section_id") or ""))
            or ledger_by_reference.get(str(section.get("heading") or ""))
            or {}
        )
        permissions = entry.get("generation_permissions") or {}
        mode = str(
            permissions.get("personnel_content_mode")
            or entry.get("personnel_content_mode")
            or "source_backed"
        )
        if mode != "source_only":
            continue
        section_id = str(
            section.get("section_id") or section.get("heading") or ""
        )
        source_only_ids.append(section_id)
        section_allowed = {
            str(
                item.get("placeholder_id")
                if isinstance(item, dict)
                else item
            )
            for item in permissions.get("allowed_placeholders") or []
            if (
                item.get("placeholder_id")
                if isinstance(item, dict)
                else item
            )
        }
        allowed_placeholder_ids.update(section_allowed)
        original = str(section.get("content") or "")
        strict_personnel_section = bool(
            permissions.get("is_personnel_section")
            or entry.get("is_personnel_section")
        )
        cleaned = (
            _source_only_personnel_content(
                original,
                allowed_placeholder_ids=section_allowed,
                required_table_contracts=list(
                    permissions.get("required_response_table_contracts") or []
                ),
            )
            if strict_personnel_section
            else _filter_source_only_personnel_narrative(
                original,
                allowed_placeholder_ids=section_allowed,
            )
        )
        removed_characters += max(0, len(original) - len(cleaned))
        section["content"] = cleaned
        content_ids = set(extract_placeholder_ids(cleaned))
        section["placeholder_ids"] = [
            value
            for value in _string_list(section.get("placeholder_ids"))
            if value in section_allowed and value in content_ids
        ]
    if source_only_ids:
        retained_root_ids = {
            value
            for section in gated.get("sections") or []
            for value in _string_list(section.get("placeholder_ids"))
        }
        gated["placeholders_used"] = [
            value
            for value in _string_list(gated.get("placeholders_used"))
            if value in retained_root_ids
        ]
        gated["claims"] = [
            claim
            for claim in gated.get("claims") or []
            if not (
                isinstance(claim, dict)
                and _is_source_free_personnel_claim(
                    str(claim.get("text") or ""),
                    claim=claim,
                    allowed_placeholder_ids=allowed_placeholder_ids,
                )
            )
        ]
    return gated, {
        "applied": bool(source_only_ids),
        "source_only_section_ids": source_only_ids,
        "strict_source_only_section_ids": [
            str(entry.get("section_id") or entry.get("section_title") or "")
            for entry in fact_ledger
            if isinstance(entry, dict)
            and str(
                (entry.get("generation_permissions") or {}).get(
                    "personnel_content_mode"
                )
                or entry.get("personnel_content_mode")
                or ""
            )
            == "source_only"
            and bool(
                (entry.get("generation_permissions") or {}).get(
                    "is_personnel_section"
                )
                or entry.get("is_personnel_section")
            )
        ],
        "removed_character_count": removed_characters,
    }


def _source_only_personnel_section_references(
    fact_ledger: list[dict[str, Any]],
) -> set[str]:
    return {
        str(reference)
        for entry in fact_ledger
        if isinstance(entry, dict)
        and str(
            (entry.get("generation_permissions") or {}).get(
                "personnel_content_mode"
            )
            or entry.get("personnel_content_mode")
            or ""
        )
        == "source_only"
        and bool(
            (entry.get("generation_permissions") or {}).get(
                "is_personnel_section"
            )
            or entry.get("is_personnel_section")
        )
        for reference in (entry.get("section_id"), entry.get("section_title"))
        if reference
    }


def _filter_source_only_personnel_narrative(
    content: str,
    *,
    allowed_placeholder_ids: set[str],
) -> str:
    """Remove source-free personnel clauses while preserving unrelated prose/tables."""
    blocks = [
        block.strip()
        for block in re.split(
            r"\n\s*\n+", str(content or "").replace("\r\n", "\n")
        )
        if block.strip()
    ]
    kept_blocks: list[str] = []
    for block in blocks:
        if re.match(r"^\s*\|.*\|", block) or re.match(
            r"^#{1,6}\s+", block
        ):
            kept_blocks.append(block)
            continue
        if re.fullmatch(r"表[一二三四五六七八九十\d]+[^\n]{1,80}", block):
            kept_blocks.append(block)
            continue
        kept_sentences: list[str] = []
        for sentence in re.split(r"(?<=[。！？；;])|\n+", block):
            sentence = sentence.strip()
            if not sentence:
                continue
            if _is_generic_project_role_duty(sentence):
                kept_sentences.append(sentence)
                continue
            if _is_neutral_team_configuration_statement(
                sentence,
                registered_placeholder_ids=allowed_placeholder_ids,
            ):
                kept_sentences.append(sentence)
                continue
            placeholder_ids = set(extract_placeholder_ids(sentence))
            if placeholder_ids and placeholder_ids <= allowed_placeholder_ids:
                scrubbed = sentence
                for reference in extract_placeholder_references(sentence):
                    scrubbed = scrubbed.replace(reference, "")
                if _source_free_personnel_narrative(scrubbed):
                    kept_sentences.extend(
                        f"{{{{{placeholder_id}}}}}"
                        for placeholder_id in sorted(placeholder_ids)
                    )
                else:
                    kept_sentences.append(sentence)
                continue
            filtered = _without_source_free_personnel_clauses(sentence)
            if filtered:
                kept_sentences.append(filtered)
        if kept_sentences:
            kept_blocks.append("".join(kept_sentences))
    return "\n\n".join(kept_blocks)


def _source_free_personnel_narrative(value: str) -> bool:
    text = str(value or "")
    if _is_generic_project_role_duty(text):
        return False
    personnel_subject = bool(
        re.search(
            r"(?:专项团队|项目团队|专业团队|项目组|项目经理|项目负责人|"
            r"技术负责人|实施工程师|技术实施组|质量保障组|人员|岗位职责|"
            r"汇报关系|组织架构|备份人员|候补人员|双人复核|后备资源|资源池)",
            text,
        )
    )
    personnel_assertion = bool(
        re.search(
            r"(?:组建|设置|配备|安排|拟派|负责|明确|具有|具备|熟悉|精通|"
            r"持有|取得|投入|替换|复核|调配|统筹|协调|汇报|经验|年限|"
            r"证书|认证|资质|技能|能力|全职|稳定)",
            text,
        )
    )
    return personnel_subject and personnel_assertion


def _without_source_free_personnel_clauses(sentence: str) -> str:
    parts = re.split(r"([，,；;。！？!?])", str(sentence or ""))
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        clause = parts[index].strip()
        punctuation = parts[index + 1] if index + 1 < len(parts) else ""
        if not clause or _source_free_personnel_narrative(clause):
            continue
        kept.append(clause + punctuation)
    return "".join(kept).strip("，,；;")


def _source_only_personnel_content(
    content: str,
    *,
    allowed_placeholder_ids: set[str],
    required_table_contracts: list[dict[str, Any]],
) -> str:
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n+", str(content or "").replace("\r\n", "\n"))
        if block.strip()
    ]
    permitted_table_indexes = {
        index
        for index, block in enumerate(blocks)
        if re.match(r"^\s*\|.*\|", block)
        and _source_only_table_matches_contract(
            block,
            required_table_contracts=required_table_contracts,
        )
    }
    kept: list[str] = []
    for index, block in enumerate(blocks):
        is_table = index in permitted_table_indexes
        next_is_table = bool(
            index + 1 in permitted_table_indexes
        )
        previous_is_table = bool(
            index - 1 in permitted_table_indexes
        )
        if is_table:
            kept.append(block)
            continue
        if next_is_table and re.fullmatch(
            r"表[一二三四五六七八九十\d]+[^\n]{1,80}", block
        ):
            kept.append(block)
            continue
        if (next_is_table or previous_is_table) and _neutral_table_context(block):
            kept.append(block)
            continue
        if _is_neutral_team_configuration_statement(
            block,
            registered_placeholder_ids=allowed_placeholder_ids,
        ):
            kept.append(block)
            continue
        if _is_generic_project_role_duty(block):
            kept.append(block)
            continue
        references = extract_placeholder_references(block)
        reference_ids = {
            value.removeprefix("{{").removesuffix("}}")
            for value in references
            if value.startswith("{{") and value.endswith("}}")
        }
        permitted_ids = reference_ids & allowed_placeholder_ids
        if not permitted_ids:
            continue
        for sentence in re.split(r"(?<=[。！？；;])|\n+", block):
            sentence_ids = set(extract_placeholder_ids(sentence))
            if not (sentence_ids & permitted_ids):
                continue
            scrubbed = sentence
            for reference in extract_placeholder_references(sentence):
                scrubbed = scrubbed.replace(reference, "")
            if re.search(
                r"(?:具有|具备|熟悉|精通|持有|取得|经验|年限|证书|认证|"
                r"资质|资格|负责|承担|组织|统筹|协调|全职|备份|候补|替换|"
                r"双人|复核|资源调配)",
                scrubbed,
            ):
                kept.extend(
                    f"{{{{{placeholder_id}}}}}"
                    for placeholder_id in sorted(sentence_ids & permitted_ids)
                )
            else:
                kept.append(sentence.strip())
    return "\n\n".join(dict.fromkeys(value for value in kept if value))


def _source_only_table_matches_contract(
    block: str,
    *,
    required_table_contracts: list[dict[str, Any]],
) -> bool:
    first_line = next(
        (
            line.strip()
            for line in str(block or "").splitlines()
            if line.strip().startswith("|") and line.strip().endswith("|")
        ),
        "",
    )
    headers = [
        re.sub(r"\s+", "", value)
        for value in first_line.strip("|").split("|")
    ]
    if not headers:
        return False
    return any(
        headers
        == [
            re.sub(r"\s+", "", str(value))
            for value in (
                (contract.get("column_contract") or {}).get("headers")
                or []
            )
        ]
        for contract in required_table_contracts
        if isinstance(contract, dict)
    )


def _neutral_table_context(value: str) -> bool:
    text = str(value or "")
    if not re.search(
        r"(?:下表|下述表格?|见表[一二三四五六七八九十\d]*|表中|如下表所示|具体安排如下)",
        text,
    ):
        return False
    return not re.search(
        r"(?:具有|具备|熟悉|精通|持有|经验|年限|证书|资质|全职|"
        r"备份人员|双人复核|资源调配)",
        text,
    )


def _is_source_free_personnel_claim(
    text: str,
    *,
    claim: dict[str, Any],
    allowed_placeholder_ids: set[str],
) -> bool:
    if not re.search(
        r"(?:项目经理|项目负责人|技术负责人|实施工程师|拟派人员|团队成员|"
        r"人员|证书|经验|年限|资质|资格)",
        text,
    ):
        return False
    if str(claim.get("claim_type") or "") == "tender_fact" and claim.get(
        "source_ids"
    ):
        return False
    placeholder_ids = set(extract_placeholder_ids(text))
    if (
        placeholder_ids
        and placeholder_ids <= allowed_placeholder_ids
        and str(claim.get("claim_type") or "")
        in {"placeholder", "cautious_reference", "proposal_statement"}
    ):
        return False
    return True


def _chapter_fact_permissions_from_ledger(
    fact_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    for entry in fact_ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("chapter_allowed_tender_facts") is not None:
            return {
                "chapter_allowed_tender_facts": deepcopy(
                    entry.get("chapter_allowed_tender_facts") or []
                ),
                "chapter_allowed_tender_numeric_facts": deepcopy(
                    entry.get("chapter_allowed_tender_numeric_facts") or []
                ),
                "allowed_confirmed_facts": deepcopy(
                    entry.get("generation_permissions", {}).get(
                        "allowed_confirmed_facts"
                    )
                    or []
                ),
                "allowed_placeholders": list(
                    {
                        str(item.get("placeholder_id")): item
                        for ledger_entry in fact_ledger
                        if isinstance(ledger_entry, dict)
                        for item in (
                            ledger_entry.get("generation_permissions", {}).get(
                                "allowed_placeholders"
                            )
                            or []
                        )
                        if isinstance(item, dict) and item.get("placeholder_id")
                    }.values()
                ),
                "forbidden_unresolved_categories": sorted(
                    {
                        str(value)
                        for ledger_entry in fact_ledger
                        if isinstance(ledger_entry, dict)
                        for value in (
                            ledger_entry.get("generation_permissions", {}).get(
                                "forbidden_unresolved_categories"
                            )
                            or []
                        )
                    }
                ),
            }
    return {
        "chapter_allowed_tender_facts": [],
        "chapter_allowed_tender_numeric_facts": [],
        "allowed_confirmed_facts": [],
        "allowed_placeholders": [],
        "forbidden_unresolved_categories": [],
    }


def _build_section_fact_ledger(
    *,
    original_context: dict[str, Any],
    target: dict[str, Any],
    pending_confirmations: list[dict[str, Any]],
    local_evidence_refs: list[dict[str, Any]],
    stage12_approved_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    requirements = list(
        ((original_context.get("tender_requirements") or {}).get("requirements"))
        or []
    )
    confirmed_context = original_context.get("confirmed_project_facts") or {}
    confirmed_facts = dict(confirmed_context.get("facts") or {})
    confirmed_fact_records = dict(confirmed_context.get("fact_records") or {})
    draft_units = list(
        ((original_context.get("draft_plan") or {}).get("draft_units")) or []
    )
    tasks = list(
        ((original_context.get("task_information") or {}).get("tasks")) or []
    )
    candidate_evidence = [
        {
            "candidate_id": item.get("candidate_id")
            or item.get("evidence_id")
            or item.get("file_id"),
            "evidence_type": item.get("evidence_type"),
            "entity_type": item.get("entity_type"),
            "selection_status": item.get("selection_status") or "candidate",
            "policy": "forbidden_as_confirmed_fact",
        }
        for item in local_evidence_refs
        if item.get("selection_status") != "confirmed"
    ]
    candidate_evidence_ids = list(
        dict.fromkeys(
            str(item.get("candidate_id") or "")
            for item in candidate_evidence
            if item.get("candidate_id")
        )
    )
    confirmed_evidence_ids = list(
        dict.fromkeys(
            str(
                item.get("candidate_id")
                or item.get("evidence_id")
                or item.get("file_id")
                or ""
            )
            for item in local_evidence_refs
            if item.get("selection_status") == "confirmed"
            and (
                item.get("candidate_id")
                or item.get("evidence_id")
                or item.get("file_id")
            )
        )
    )
    confirmed_record_evidence_ids = list(
        dict.fromkeys(
            str(evidence_id)
            for record in confirmed_fact_records.values()
            if isinstance(record, dict)
            for evidence_id in (
                list(record.get("evidence_ids") or [])
                or [record.get("source_id") or record.get("evidence_id")]
            )
            if evidence_id
        )
    )
    confirmed_evidence_ids = list(
        dict.fromkeys(
            [*confirmed_evidence_ids, *confirmed_record_evidence_ids]
        )
    )
    confirmed_personnel_facts = [
        {
            "fact_id": key,
            "value": record.get("value"),
            "source_ids": (
                list(record.get("evidence_ids") or [])
                or [
                    record.get("source_id")
                    or record.get("evidence_id")
                ]
            ),
        }
        for key, record in confirmed_fact_records.items()
        if isinstance(record, dict)
        and re.search(
            r"(?:person|project_manager|work_years|management_years|"
            r"similar_project|certificate|人员|项目经理|负责人|简历|"
            r"证书|工作年限|项目经验|同类项目)",
            f"{key} {json.dumps(record, ensure_ascii=False)}",
            re.I,
        )
    ]
    confirmed_personnel_evidence_ids = list(
        dict.fromkeys(
            str(source_id)
            for item in confirmed_personnel_facts
            for source_id in item.get("source_ids") or []
            if source_id
        )
    )
    candidate_personnel_evidence_ids = list(
        dict.fromkeys(
            str(item.get("candidate_id") or "")
            for item in candidate_evidence
            if item.get("candidate_id")
            and str(item.get("entity_type") or "") == "person"
        )
    )
    allowed_confirmed_facts = [
        {
            "fact_id": key,
            "value": (
                record.get("value")
                if isinstance(record, dict)
                else value
            ),
            "source_ids": (
                list(record.get("evidence_ids") or [])
                or [
                    record.get("source_id")
                    or record.get("evidence_id")
                ]
            )
            if isinstance(record, dict)
            else [],
        }
        for key, value in confirmed_facts.items()
        for record in [confirmed_fact_records.get(key, {})]
    ]
    allowed_confirmed_facts.extend(
        {
            "fact_id": str(item.get("fact_key") or item["source_id"]),
            "value": deepcopy(item["confirmed_value"]),
            "source_ids": [str(item["source_id"])],
            "source_type": str(item["source_type"]),
            "permission_kind": "stage12_approved_input",
            "source_lineage": "stage12_human_review",
        }
        for item in stage12_approved_facts or []
    )
    allowed_confirmed_facts.extend(
        {
            "fact_id": str(
                item.get("candidate_id")
                or item.get("evidence_id")
                or item.get("file_id")
                or ""
            ),
            "value": str(
                item.get("text")
                or item.get("excerpt")
                or item.get("entity_name")
                or ""
            )[:1200],
            "source_ids": [
                str(
                    item.get("candidate_id")
                    or item.get("evidence_id")
                    or item.get("file_id")
                )
            ],
        }
        for item in local_evidence_refs
        if item.get("selection_status") == "confirmed"
        and (
            item.get("candidate_id")
            or item.get("evidence_id")
            or item.get("file_id")
        )
    )
    confirmed_personnel_facts.extend(
        item
        for item in allowed_confirmed_facts
        if any(
            str(source_id) in confirmed_evidence_ids
            for source_id in item.get("source_ids") or []
        )
        and re.search(
            r"(?:人员|项目经理|负责人|简历|证书|工作年限|项目经验|同类项目|"
            r"person|resume|certificate)",
            f"{item.get('fact_id')} {json.dumps(item.get('value'), ensure_ascii=False)}",
            re.I,
        )
    )
    confirmed_personnel_evidence_ids = list(
        dict.fromkeys(
            [
                *confirmed_personnel_evidence_ids,
                *[
                    str(source_id)
                    for item in confirmed_personnel_facts
                    for source_id in item.get("source_ids") or []
                    if source_id
                ],
            ]
        )
    )
    allowed_confirmed_numeric_facts = [
        {
            **numeric,
            "fact_id": item.get("fact_id"),
            "fact_type": "confirmed_fact",
            "source_ids": list(item.get("source_ids") or []),
        }
        for item in allowed_confirmed_facts
        for numeric in _extract_generation_numeric_facts(
            json.dumps(item.get("value"), ensure_ascii=False),
            source_id=(
                str((item.get("source_ids") or [""])[0])
                if item.get("source_ids")
                else None
            ),
            fact_type="confirmed_fact",
        )
    ]
    allowed_confirmed_product_capabilities = [
        item
        for item in allowed_confirmed_facts
        if _looks_like_product_capability_fact(
            f"{item.get('fact_id')} {json.dumps(item.get('value'), ensure_ascii=False)}"
        )
        and item.get("source_ids")
    ]
    allowed_confirmed_product_capabilities.extend(
        {
            "fact_id": str(
                item.get("capability_id")
                or item.get("candidate_id")
                or item.get("evidence_id")
                or item.get("file_id")
                or ""
            ),
            "value": str(
                item.get("capability")
                or item.get("text")
                or item.get("excerpt")
                or ""
            )[:1200],
            "source_ids": [
                str(
                    item.get("candidate_id")
                    or item.get("evidence_id")
                    or item.get("file_id")
                )
            ],
        }
        for item in local_evidence_refs
        if item.get("selection_status") == "confirmed"
        and (
            item.get("candidate_id")
            or item.get("evidence_id")
            or item.get("file_id")
        )
        and _looks_like_product_capability_fact(
            json.dumps(item, ensure_ascii=False)
        )
    )
    chapter_allowed_tender_facts: list[dict[str, Any]] = []
    chapter_allowed_tender_numeric_facts: list[dict[str, Any]] = []
    for requirement in requirements:
        source_id = str(requirement.get("point_id") or "")
        requirement_text = str(requirement.get("original_text") or "")
        if not source_id or not requirement_text.strip():
            continue
        response_policy = tender_response_policy(
            requirement_text,
            source_id=source_id,
        )
        semantics = _tender_business_semantics(requirement_text)
        chapter_allowed_tender_facts.append(
            {
                "requirement_id": source_id,
                "source_id": source_id,
                "text": requirement_text[:1000],
                "business_semantics": semantics,
                "response_intent": response_policy.get("response_intent"),
                "evidence_status": response_policy.get("evidence_status"),
                "deviation_status": response_policy.get("deviation_status"),
                "usage": (
                    "no_deviation_response"
                    if response_policy.get("default_no_deviation")
                    else "requirement_description_only"
                ),
                "obligation_items": extract_tender_obligation_items(
                    requirement_text,
                    source_id=source_id,
                    requirement_id=source_id,
                    deviation_status=response_policy.get(
                        "deviation_status"
                    ),
                ),
            }
        )
        chapter_allowed_tender_numeric_facts.extend(
            {
                **numeric,
                "requirement_id": source_id,
                "business_semantics": semantics,
                "time_origin": _tender_time_origin(requirement_text),
                "completion_object": _tender_completion_object(
                    requirement_text
                ),
                "deviation_status": (
                    "no_deviation"
                    if response_policy.get("default_no_deviation")
                    else "evidence_pending"
                ),
                "usage": (
                    "no_deviation_response"
                    if response_policy.get("default_no_deviation")
                    else "requirement_description_only"
                ),
            }
            for numeric in _extract_generation_numeric_facts(
                requirement_text,
                source_id=source_id,
                fact_type="tender_requirement",
            )
        )
    chapter_has_tender_personnel_source = any(
        "personnel_requirement" in (item.get("business_semantics") or [])
        for item in chapter_allowed_tender_facts
    )
    chapter_has_confirmed_personnel_source = bool(
        confirmed_personnel_facts or confirmed_personnel_evidence_ids
    )
    chapter_personnel_content_mode = (
        "source_backed"
        if (
            chapter_has_tender_personnel_source
            or chapter_has_confirmed_personnel_source
        )
        else "source_only"
    )
    nodes = list(target.get("children") or []) or [target]
    ledger: list[dict[str, Any]] = []
    for node in nodes:
        section_id = str(
            node.get("section_id")
            or node.get("chapter_id")
            or node.get("id")
            or ""
        )
        section_title = str(node.get("title") or "")
        writing_requirements = node.get("writing_requirements") or {}
        response_ids = {
            str(item)
            for item in writing_requirements.get("response_point_ids") or []
        }
        section_requirements = [
            {
                "point_id": item.get("point_id"),
                "text": str(item.get("original_text") or "")[:1000],
                "requirement_only": True,
                "not_confirmed_offered_capability": True,
            }
            for item in requirements
            if not response_ids
            or str(item.get("point_id") or "") in response_ids
        ]
        section_response_policies = [
            tender_response_policy(
                str(item.get("original_text") or ""),
                source_id=str(item.get("point_id") or ""),
            )
            for item in requirements
            if not response_ids
            or str(item.get("point_id") or "") in response_ids
        ]
        default_no_deviation_requirements = [
            item
            for item in section_response_policies
            if item.get("default_no_deviation")
        ]
        evidence_pending_requirements = [
            item
            for item in section_response_policies
            if item.get("evidence_status") in {"pending", "missing"}
        ]
        allowed_tender_numeric_facts = [
            {
                **numeric,
                "deviation_status": (
                    "no_deviation"
                    if policy.get("default_no_deviation")
                    else "evidence_pending"
                ),
                "usage": (
                    "no_deviation_response"
                    if policy.get("default_no_deviation")
                    else "requirement_description_only"
                ),
            }
            for requirement, policy in zip(
                [
                    item
                    for item in requirements
                    if not response_ids
                    or str(item.get("point_id") or "") in response_ids
                ],
                section_response_policies,
            )
            for numeric in _extract_generation_numeric_facts(
                str(requirement.get("original_text") or ""),
                source_id=str(requirement.get("point_id") or ""),
                fact_type="tender_requirement",
            )
        ]
        tender_capabilities_for_requirement_description_only = [
            {
                "source_id": item.get("source_id"),
                "text": item.get("requirement_text"),
                "usage": "requirement_description_only",
            }
            for item in evidence_pending_requirements
            if _looks_like_product_capability_fact(
                str(item.get("requirement_text") or "")
            )
        ]
        capabilities_for_test_verification_only = [
            {
                **item,
                "usage": "test_verification_only",
                "required_wording": "验证是否满足，不得预先断言功能正常、业务不中断或能力已具备",
            }
            for item in tender_capabilities_for_requirement_description_only
        ]
        positive_deviation_candidates: list[dict[str, Any]] = []
        negative_deviations: list[dict[str, Any]] = []
        for key, record in confirmed_fact_records.items():
            if not isinstance(record, dict):
                continue
            baseline = str(record.get("tender_baseline") or "")
            offered = str(record.get("offered_value") or record.get("value") or "")
            direction = str(record.get("comparison_direction") or "")
            if not (baseline and offered and direction):
                continue
            deviation_status = classify_deviation_status(
                tender_baseline=baseline,
                offered_value=offered,
                comparison_direction=direction,
                positive_confirmed=bool(
                    record.get("positive_deviation_confirmed")
                    or record.get("deviation_status")
                    == "positive_deviation_confirmed"
                ),
            )
            deviation_item = {
                "fact_id": key,
                "tender_baseline": baseline,
                "offered_value": offered,
                "comparison_direction": direction,
                "deviation_status": deviation_status,
                "source_ids": (
                    list(record.get("evidence_ids") or [])
                    or [
                        record.get("source_id")
                        or record.get("evidence_id")
                    ]
                ),
            }
            if deviation_status.startswith("positive_deviation"):
                positive_deviation_candidates.append(deviation_item)
            elif deviation_status == "negative_deviation":
                negative_deviations.append(deviation_item)
        placeholders = [
            {
                "field": item.get("field"),
                "placeholder_id": item.get("placeholder_id"),
                "placeholder_token": item.get("placeholder_token"),
            }
            for item in pending_confirmations
            if not item.get("affected_sections")
            or section_id in {
                str(value) for value in item.get("affected_sections") or []
            }
            or section_title in {
                str(value) for value in item.get("affected_sections") or []
            }
        ]
        is_personnel_section = bool(
            re.search(
                r"(?:人员|团队|组织|项目经理|项目负责人|技术负责人|简历)",
                section_title
                + " "
                + json.dumps(writing_requirements, ensure_ascii=False),
                re.I,
            )
        )
        has_tender_personnel_source = any(
            "personnel_requirement" in (
                item.get("business_semantics") or []
            )
            for item in chapter_allowed_tender_facts
            if not response_ids
            or str(item.get("source_id") or "") in response_ids
        )
        has_tender_personnel_table = bool(
            is_personnel_section
            and any(
                re.search(
                    r"(?:人员|项目经理|负责人|简历|证书|岗位)",
                    " ".join(
                        [
                            str(table.get("title") or ""),
                            *[
                                str(value)
                                for value in table.get("headers") or []
                            ],
                        ]
                    ),
                )
                for table in original_context.get("tender_tables") or []
                if table.get("table_type") == "required_response_table"
            )
        )
        has_confirmed_personnel_source = chapter_has_confirmed_personnel_source
        personnel_narrative_allowed = bool(
            chapter_personnel_content_mode == "source_backed"
        )
        personnel_content_mode = chapter_personnel_content_mode
        generated_table_contract = {
            "exact_title": (
                "若创建普通辅助表，必须先确定metadata.title，并在正文表格前以"
                "“表X metadata.title”或完全相同的独立表题显式写出；仅写“如下表”不满足。"
            ),
            "intro_requirement": (
                "表格前须说明用途；可使用“如下表所示、见下表、表中列示、具体安排如下”等引导语。"
            ),
            "followup_requirement": "表格后须说明填写边界、使用方式或后续处理。",
            "column_contract": (
                "metadata对应表格的表头和列顺序必须与正文实际Markdown表格一致；"
                "不得声明不存在的表格。"
            ),
        }
        required_table_contracts = [
            {
                "table_id": table.get("table_id"),
                "exact_title": table.get("title"),
                "intro_requirement": "说明该招标原表的响应或填写用途。",
                "followup_requirement": "说明填写边界、回填接口和后续核对方式。",
                "column_contract": {
                    "headers": list(table.get("headers") or []),
                    "layout_complexity": table.get("layout_complexity"),
                    "preserve_field_order": True,
                    "preserve_merge_relationships": True,
                },
            }
            for table in original_context.get("tender_tables") or []
            if table.get("table_type") == "required_response_table"
        ]
        section_units = [
            item
            for item in draft_units
            if _ledger_item_matches_section(
                item,
                section_id=section_id,
                section_title=section_title,
            )
        ]
        section_tasks = [
            item
            for item in tasks
            if _ledger_item_matches_section(
                item,
                section_id=section_id,
                section_title=section_title,
            )
        ]
        cannot_finalize = any(
            str((item.get("draftability") or {}).get("status") or "")
            == "cannot_finalize"
            or item.get("parameter_consistency") is False
            or item.get("parameter_consistency_confirmed") is False
            or (item.get("generation_constraints") or {}).get(
                "parameter_consistency_confirmed"
            )
            is False
            for item in [*section_units, *section_tasks]
        )
        title_and_facts = (
            section_title
            + " "
            + json.dumps(
                (writing_requirements.get("manual_project_facts") or {}).get(
                    "fact_types"
                )
                or [],
                ensure_ascii=False,
            )
        )
        unresolved_staffing = (
            ["staffing_count"]
            if re.search(r"(?:人员|团队|组织|负责人|经理|工程师)", title_and_facts)
            else []
        )
        unresolved_numeric = (
            ["schedule_duration", "handover_or_rectification_deadline"]
            if re.search(r"(?:进度|周期|工期|计划|交接|整改|验收)", title_and_facts)
            else []
        )
        if re.search(r"(?:服务|响应|维修|保修|到场)", title_and_facts):
            unresolved_numeric.append("service_level")
        unresolved_product = (
            ["product_capability"]
            if re.search(r"(?:技术|方案|产品|系统|部署|测试|试运行|验收|参数)", title_and_facts)
            else []
        )
        entry = {
            "section_id": section_id,
            "section_title": section_title,
            "chapter_allowed_tender_facts": deepcopy(
                chapter_allowed_tender_facts
            ),
            "chapter_allowed_tender_numeric_facts": deepcopy(
                chapter_allowed_tender_numeric_facts
            ),
            "confirmed_facts": confirmed_facts,
            "tender_requirements_only": section_requirements,
            "confirmed_evidence_ids": confirmed_evidence_ids,
            "candidate_evidence_ids": candidate_evidence_ids,
            "confirmed_personnel_facts": confirmed_personnel_facts,
            "confirmed_personnel_evidence_ids": confirmed_personnel_evidence_ids,
            "candidate_personnel_evidence_ids": candidate_personnel_evidence_ids,
            "confirmed_tender_commitments": default_no_deviation_requirements,
            "default_no_deviation_requirements": default_no_deviation_requirements,
            "positive_deviation_candidates": positive_deviation_candidates,
            "negative_deviations": negative_deviations,
            "evidence_pending_requirements": evidence_pending_requirements,
            "registered_placeholders": placeholders,
            "personnel_content_mode": personnel_content_mode,
            "is_personnel_section": is_personnel_section,
            "unresolved_staffing": unresolved_staffing,
            "unresolved_numeric_commitments": unresolved_numeric,
            "unresolved_product_capabilities": unresolved_product,
            "tender_capabilities_for_requirement_description_only": (
                tender_capabilities_for_requirement_description_only
            ),
            "capabilities_for_test_verification_only": (
                capabilities_for_test_verification_only
            ),
            "forbidden_as_confirmed_fact": [
                {
                    "type": "candidate_evidence",
                    "candidate_ids": candidate_evidence_ids,
                    "instruction": "候选材料只能形成待确认项，不得写成企业、人员、资质或业绩确认事实。",
                },
                {
                    "type": "tender_requirement_only",
                    "instruction": "招标要求只能描述采购需求，不得自动改写为我方已具备能力或投标承诺。",
                },
                {
                    "type": "unresolved_fact",
                    "instruction": "未确认人员、数字承诺和产品能力只能使用已登记占位符、条件边界或核验目标。",
                },
            ],
            "writing_requirements": {
                "use_only_confirmed_facts_as_definite_fact": True,
                "confirmed_personnel_facts_may_be_written_with_exact_values": True,
                "confirmed_selected_knowledge_requires_confirmed_evidence_id": True,
                "tender_requirement_is_not_offered_capability": True,
                "default_tender_business_commitment_to_original_value_and_no_deviation": True,
                "candidate_evidence_is_not_confirmed_fact": True,
                "do_not_invent_staffing_numeric_or_product_capability": True,
                "positive_deviation_requires_confirmation": True,
                "negative_deviation_must_be_disclosed_and_blocking": True,
                "no_deviation_intent_does_not_confirm_objective_evidence": True,
            },
            "generation_permissions": {
                "chapter_allowed_tender_facts": deepcopy(
                    chapter_allowed_tender_facts
                ),
                "chapter_allowed_tender_numeric_facts": deepcopy(
                    chapter_allowed_tender_numeric_facts
                ),
                "allowed_confirmed_facts": deepcopy(
                    allowed_confirmed_facts
                ),
                "allowed_tender_facts": [
                    {
                        "source_id": item.get("point_id"),
                        "text": item.get("text"),
                        "usage": "tender_requirement",
                    }
                    for item in section_requirements
                ],
                "allowed_confirmed_numeric_facts": deepcopy(
                    allowed_confirmed_numeric_facts
                ),
                "allowed_tender_numeric_facts": allowed_tender_numeric_facts,
                "allowed_confirmed_personnel_facts": deepcopy(
                    confirmed_personnel_facts
                ),
                "allowed_confirmed_product_capabilities": deepcopy(
                    allowed_confirmed_product_capabilities
                ),
                "allowed_placeholders": deepcopy(placeholders),
                "allowed_generic_role_descriptions": [],
                "personnel_narrative_source_status": {
                    "allowed": personnel_narrative_allowed,
                    "tender_personnel_source": has_tender_personnel_source,
                    "confirmed_personnel_source": has_confirmed_personnel_source,
                    "tender_personnel_table_source": has_tender_personnel_table,
                    "policy": (
                        "exact_source_or_confirmed_fact_only"
                        if personnel_narrative_allowed
                        else "tender_table_or_registered_placeholder_only"
                    ),
                },
                "personnel_content_mode": personnel_content_mode,
                "is_personnel_section": is_personnel_section,
                "forbid_unlisted_personnel_narrative": (
                    personnel_content_mode == "source_only"
                ),
                "tender_capabilities_for_requirement_description_only": deepcopy(
                    tender_capabilities_for_requirement_description_only
                ),
                "capabilities_for_test_verification_only": deepcopy(
                    capabilities_for_test_verification_only
                ),
                "forbidden_unresolved_categories": [
                    *unresolved_staffing,
                    *unresolved_numeric,
                    *unresolved_product,
                    "candidate_personnel_qualification",
                    "candidate_enterprise_qualification",
                    "unconfirmed_company_resource",
                ],
                "forbid_unlisted_numeric_commitments": True,
                "forbid_unlisted_personnel_qualifications": True,
                "forbid_unlisted_product_capabilities": True,
                "generated_supporting_table_contract": generated_table_contract,
                "required_response_table_contracts": required_table_contracts,
            },
            "draftability": (
                "cannot_finalize" if cannot_finalize else "boundary_required"
            ),
        }
        if cannot_finalize:
            entry["mandatory_instruction"] = (
                "本节只能生成带占位符或条件边界的初稿，不得自行补齐数字、人员和产品能力。"
            )
        ledger.append(entry)
    return ledger


def _extract_generation_numeric_facts(
    text: str,
    *,
    source_id: str | None,
    fact_type: str,
) -> list[dict[str, Any]]:
    value = str(text or "")
    facts: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in re.finditer(r"7\s*[×xX*]\s*24\s*小时", value, re.I):
        facts.append(
            {
                "text": value[:1000],
                "value": "7×24",
                "unit": "小时",
                "source_id": source_id,
                "fact_type": fact_type,
            }
        )
        occupied.append(match.span())
    for match in re.finditer(
        r"(?P<value>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>分钟|小时|工作日|日|天|周|个月|月|年|人|名|个)",
        value,
    ):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        number = float(match.group("value"))
        facts.append(
            {
                "text": value[:1000],
                "value": int(number) if number.is_integer() else number,
                "unit": match.group("unit"),
                "source_id": source_id,
                "fact_type": fact_type,
            }
        )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in facts:
        unique[
            (
                str(item.get("value")),
                str(item.get("unit")),
                str(item.get("source_id") or ""),
            )
        ] = item
    return list(unique.values())


def _tender_business_semantics(value: str) -> list[str]:
    text = str(value or "")
    semantic_patterns = {
        "delivery_time": r"(?:货到|到货|交货|交付).{0,12}(?:时间|时限|期限|日期|完成|内)",
        "installation_time": r"安装.{0,12}(?:时间|时限|期限|完成|内)",
        "delivery_location": r"(?:交货|送货|到货).{0,8}(?:地点|地址|场所)",
        "random_documents": r"(?:随机资料|随机文件|使用手册|维修手册|说明书)",
        "training": r"培训",
        "acceptance": r"验收",
        "service_response": r"(?:技术响应|故障响应|到场|维修工程师|服务响应)",
        "warranty": r"(?:保修|质保|售后服务)",
        "personnel_requirement": r"(?:项目经理|项目负责人|技术负责人|实施工程师|人员简历|人员配置|人员表)",
        "product_capability": r"(?:产品|系统|平台|设备).{0,20}(?:支持|具备|实现|提供|参数|性能)",
    }
    return [
        semantic
        for semantic, pattern in semantic_patterns.items()
        if re.search(pattern, text, re.I)
    ]


def _tender_time_origin(value: str) -> str | None:
    text = str(value or "")
    patterns = (
        r"(?:自|从)([^，。；;]{1,32}?)(?:之日)?起",
        r"([^，。；;]{1,24}?)(?:后|之日起)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", "", match.group(1))[:40]
    return None


def _tender_completion_object(value: str) -> str | None:
    text = str(value or "")
    for pattern in (
        r"(?:完成|提供|交付|开展)([^，。；;]{1,36})",
        r"([^，。；;]{1,24})(?:完成|交付|到货)",
    ):
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", "", match.group(1))[:40]
    return None


def _looks_like_product_capability_fact(value: str) -> bool:
    return bool(
        re.search(
            r"(?:产品|系统|平台|集群|存储|网络|虚拟机|节点|"
            r"多副本|纠删码|在线扩展|自动加入资源池|不中断业务|"
            r"分布式虚拟交换机|链路聚合|故障切换|自动迁移|自动重启|"
            r"数据重建|热添加|快照|克隆|反亲和|资源预留|MLAG|VPC)",
            str(value or ""),
            re.I,
        )
    )


def _ledger_item_matches_section(
    item: dict[str, Any],
    *,
    section_id: str,
    section_title: str,
) -> bool:
    direct_ids = {
        str(item.get("section_id") or ""),
        str(item.get("target_section_id") or ""),
    }
    if section_id and section_id in direct_ids:
        return True
    if section_title and section_title == str(item.get("title") or ""):
        return True
    paths = [
        str(value)
        for value in [
            *(item.get("target_path") or []),
            *(item.get("section_path") or []),
        ]
    ]
    return bool(
        (section_id and section_id in paths)
        or (section_title and section_title in paths)
    )


def _finalization_instruction(
    *,
    schema: dict[str, Any],
    target: dict[str, Any],
    working_draft: str,
    pending_confirmations: list[dict[str, Any]],
    local_refs: list[dict[str, Any]],
    web_refs: list[dict[str, Any]],
    fact_ledger: list[dict[str, Any]],
) -> str:
    headings = [str(item.get("title") or "") for item in target.get("children") or []]
    pending = [
        {
            "field": item.get("field"),
            "placeholder_id": item.get("placeholder_id"),
            "placeholder_token": item.get("placeholder_token"),
            "affected_sections": item.get("affected_sections") or [],
            "blocking": item.get("blocking"),
        }
        for item in pending_confirmations
    ]
    local = [
        {
            "candidate_id": item.get("candidate_id"),
            "evidence_type": item.get("evidence_type"),
            "selection_status": item.get("selection_status"),
        }
        for item in local_refs
    ]
    web = [
        {"title": item.get("title"), "url": item.get("url"), "source_type": item.get("source_type")}
        for item in web_refs
    ]
    payload = {
        "chapter_title": target.get("title"),
        "required_headings_in_order": headings,
        "working_draft": working_draft[:30000],
        "pending_confirmations": pending,
        "local_evidence_candidates": local,
        "web_sources": web,
        "section_fact_ledger": fact_ledger,
        "chapter_schema": schema,
    }
    return (
        "现在进入最终化。不得再请求任何工具；必须立即返回且只返回符合唯一章节 Schema 的 JSON 对象。"
        "保留全部标题及顺序，未确认事实使用已记录占位符，候选材料不得写成已确认事实。"
        "section_fact_ledger中的generation_permissions是封闭许可：只允许使用列出的确定事实、"
        "承诺数字、人员资历、产品能力和占位符；未列出的内容必须省略或改写为不含具体事实的"
        "过程或核验目标。无人员来源时只可保留中性项目角色动作，不得补写人员事实。"
        "不得使用行业惯例填补数字、产品组网、人员安排或额外承诺。"
        "评分覆盖说明仅保留在coverage metadata，不写入正文或商务claims。"
        "最终化上下文如下：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _duplicate_repair_instruction(
    *,
    draft: dict[str, Any],
    duplicate_pairs: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
    schema: dict[str, Any],
) -> str:
    payload = {
        "chapter_draft": draft,
        "duplicate_section_pairs": duplicate_pairs,
        "section_boundaries": boundaries,
        "chapter_schema": schema,
    }
    return (
        "执行一次且仅一次受控的重复内容修正。不得调用任何工具，不得新增事实、承诺、来源或"
        "占位符；不得改变章节标题、小节标题及顺序、响应点和评分项覆盖。仅压缩跨小节重复的"
        "段落，把完整内容保留在最匹配的小节，其他位置改为必要承接。返回且只返回符合Schema"
        "的完整章节JSON对象。\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _repair_preserves_contract(
    original: dict[str, Any],
    repaired: dict[str, Any],
) -> bool:
    if str(original.get("chapter_title") or "") != str(repaired.get("chapter_title") or ""):
        return False
    original_headings = [
        str(item.get("heading") or "") for item in original.get("sections") or []
    ]
    repaired_headings = [
        str(item.get("heading") or "") for item in repaired.get("sections") or []
    ]
    if original_headings != repaired_headings:
        return False
    if not set(original.get("requirements_covered") or []) <= set(
        repaired.get("requirements_covered") or []
    ):
        return False
    if not set(original.get("score_items_covered") or []) <= set(
        repaired.get("score_items_covered") or []
    ):
        return False
    return True


def _merge_table_fill_data(
    defaults: list[dict[str, Any]],
    recorded: list[dict[str, Any]],
    *,
    allowed_required_table_ids: set[str],
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("table_id") or ""): dict(item)
        for item in defaults
        if str(item.get("table_id") or "") in allowed_required_table_ids
    }
    for item in recorded:
        table_id = str(item.get("table_id") or "")
        if table_id in allowed_required_table_ids:
            by_id[table_id] = dict(item)
    return list(by_id.values())


def _web_verification_state(
    *,
    policy: str,
    attempts: list[dict[str, Any]],
    page_reads: list[dict[str, Any]],
    web_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    if policy == "disabled":
        return {
            "status": "disabled",
            "attempt_count": 0,
            "verified_source_count": 0,
            "reason": "web_search_policy_disabled",
        }
    successful_pages = [item for item in page_reads if item.get("status") == "success"]
    distinct_urls = {str(item.get("url") or "") for item in web_refs if item.get("url")}
    if successful_pages or len(distinct_urls) >= 2:
        return {
            "status": "sufficient",
            "attempt_count": len(attempts),
            "verified_source_count": max(len(successful_pages), len(distinct_urls)),
            "reason": "opened_source_or_multiple_independent_sources",
        }
    if attempts:
        has_results = any(int(item.get("result_count") or 0) > 0 for item in attempts)
        return {
            "status": "insufficient" if has_results else "unavailable",
            "attempt_count": len(attempts),
            "verified_source_count": len(distinct_urls),
            "reason": (
                "single_search_result_is_not_automatically_verified"
                if has_results
                else "search_returned_no_usable_official_source"
            ),
        }
    return {
        "status": "not_requested",
        "attempt_count": 0,
        "verified_source_count": 0,
        "reason": "agent_did_not_need_or_request_external_verification",
    }


def build_markdown_change_summary(previous: str, current: str) -> dict[str, Any]:
    old = str(previous or "")
    new = str(current or "")
    old_sections = _markdown_sections(old)
    new_sections = _markdown_sections(new)
    ordered_titles = list(dict.fromkeys([*old_sections, *new_sections]))
    changed = [
        ("章节标题或前言" if title == "__chapter_preamble__" else title)
        for title in ordered_titles
        if old_sections.get(title) != new_sections.get(title)
    ]
    return {
        "previous_markdown_characters": len(old),
        "current_markdown_characters": len(new),
        "previous_table_count": _markdown_table_count(old),
        "current_table_count": _markdown_table_count(new),
        "previous_placeholder_count": _markdown_placeholder_count(old),
        "current_placeholder_count": _markdown_placeholder_count(new),
        "sections_changed": changed,
    }


def _markdown_sections(markdown: str) -> dict[str, str]:
    result: dict[str, list[str]] = {}
    current = "__chapter_preamble__"
    result[current] = []
    for line in str(markdown or "").splitlines():
        match = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip()
            result.setdefault(current, [])
        else:
            result.setdefault(current, []).append(line)
    return {key: "\n".join(lines).strip() for key, lines in result.items()}


def _markdown_table_count(markdown: str) -> int:
    return sum(
        1
        for line in str(markdown or "").splitlines()
        if re.match(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$", line)
    )


def _markdown_placeholder_count(markdown: str) -> int:
    return len(extract_placeholder_references(str(markdown or "")))


def _history_path(draft_path: Path, target_id: str) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S.%f%z")
    return draft_path.parent / "history" / target_id / f"{timestamp}.md"


def _tool_signature(name: str, arguments: dict[str, Any]) -> str:
    normalized = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{name}:{normalized}"


def _recoverable_tool_argument_feedback(
    *,
    tool_name: str,
    error: Exception,
    error_count_for_tool: int,
) -> dict[str, Any] | None:
    if tool_name not in RECOVERABLE_AUXILIARY_TOOL_NAMES:
        return None
    message = str(error)[:500]
    if (
        tool_name == "record_pending_confirmation"
        and "exactly one field" in message
    ):
        return {
            "status": "error",
            "error": message,
            "error_code": "pending_confirmation_multiple_fields",
            "recoverable": True,
            "rule": "one_call_one_field",
            "action": "split_into_atomic_fields_or_continue_without_recording",
            "instruction": (
                "该工具一次只能提交一个原子 field，请拆分后重试。"
                "不得原样重复或改写为另一个复合 field；也可以停止记录并使用 placeholder、"
                "条件化表述或 blocker 继续生成章节。"
            ),
            "invalid_tool_argument_count_for_tool": error_count_for_tool,
        }
    return {
        "status": "error",
        "error": message,
        "error_code": "auxiliary_tool_invalid_arguments",
        "recoverable": True,
        "rule": "use_only_arguments_allowed_for_the_current_runtime_target",
        "action": "correct_arguments_or_continue_without_this_auxiliary_tool",
        "instruction": (
            "这是可恢复的辅助工具参数错误。请按当前目标修正一次；如果不需要该辅助信息，"
            "直接使用已经取得的上下文继续生成章节，不得猜测未确认事实。"
        ),
        "invalid_tool_argument_count_for_tool": error_count_for_tool,
    }


def _tool_error_finalization_instruction(*, disabled_tool: str) -> str:
    return (
        "[Runtime Recovery Instruction]\n"
        f"工具 {disabled_tool} 已因重复参数错误在本轮禁用。停止继续调用该工具或记录 pending confirmation。\n"
        "使用当前已经取得的招标要求、任务信息、本地候选和已记录 pending confirmations，"
        "直接生成当前目标章节。\n"
        "任何仍未确认的事实不得猜测；使用已经登记的 placeholder、条件化表述或 blocker。\n"
        "下一次响应不得调用工具，必须返回符合章节 Schema 的完整 JSON 对象。"
    )


def _tool_skip_reason(
    *,
    name: str,
    signature_count: int,
    allowed_names: set[str],
    tool_counts: dict[str, int],
    max_search_calls: int,
    max_local_knowledge_calls: int,
    max_web_search_calls: int,
    max_pending_confirmation_calls: int,
) -> str | None:
    if name not in allowed_names:
        return "tool_not_available_in_current_finalization_phase"
    if signature_count > 1:
        return "exact_duplicate_tool_call"
    if name == "query_local_knowledge" and tool_counts.get(name, 0) >= max_local_knowledge_calls:
        return "local_knowledge_budget_exhausted"
    if name == "web_search" and tool_counts.get(name, 0) >= max_web_search_calls:
        return "web_search_budget_exhausted"
    search_count = tool_counts.get("query_local_knowledge", 0) + tool_counts.get("web_search", 0)
    if name in {"query_local_knowledge", "web_search"} and search_count >= max_search_calls:
        return "search_budget_exhausted"
    if name == "record_pending_confirmation" and tool_counts.get(name, 0) >= max_pending_confirmation_calls:
        return "pending_confirmation_budget_exhausted"
    return None


def _normalized_query(value: str) -> str:
    return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", value.lower()))


def _similar_query_seen(name: str, query: str, history: list[tuple[str, str]]) -> float:
    if not query:
        return 0.0
    current = set(query.split())
    best = 0.0
    for old_name, old_query in history:
        if old_name != name or not old_query:
            continue
        previous = set(old_query.split())
        union = current | previous
        similarity = len(current & previous) / len(union) if union else 1.0
        best = max(best, similarity)
    return round(best, 3) if best >= 0.75 else 0.0


def _safe_result_summary(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": value.get("status")}
    for key in ("pending_count", "recorded_count", "section_count", "character_count"):
        if isinstance(value.get(key), (int, float)):
            result[key] = value[key]
    result["result_count"] = _result_count(value)
    return result


def _model_json_repair_prompt(
    *,
    raw_content: str,
    schema: dict[str, Any],
    error_summary: str,
    suspected_truncation: bool,
) -> str:
    instruction = (
        "只返回合法JSON，不要代码块和解释。不得调用或请求任何工具。"
        "保持所有原始字段值和正文原文；不得增加或删除内容，不得改写、扩写或概括正文；"
        "不得增加表格、项目符号、section、事实或承诺；只修复JSON语法、分隔符和转义。"
    )
    if suspected_truncation:
        instruction += (
            "原输出疑似被截断；只能闭合现有结构和字符串，不得借修复补写正文。"
            "若无法在保持原文的前提下修复，返回现有字段的最小合法闭合结构。"
        )
    return (
        "【原始无效输出】\n"
        + str(raw_content)
        + "\n\n【目标Schema】\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        + "\n\n【具体解析错误】\n"
        + str(error_summary)
        + "\n\n【修复指令】\n"
        + instruction
    )


def _expected_section_specs(target: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = list(target.get("children") or []) or [target]
    return [
        {
            "section_id": str(
                node.get("section_id")
                or node.get("chapter_id")
                or node.get("id")
                or ""
            ),
            "section_title": str(node.get("title") or ""),
            "expected_order": index,
        }
        for index, node in enumerate(nodes)
        if isinstance(node, dict)
        and (
            node.get("section_id") or node.get("chapter_id") or node.get("id")
        )
    ]


def _complete_working_draft_candidate(
    *,
    tool_context: TechnicalWritingToolContext,
    target: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a schema-shaped candidate from a fully staged working draft.

    ``save_section_draft`` is runtime-memory staging rather than a Candidate
    artifact.  Once every expected section is staged, converting it through
    the ordinary canonicalization/validation path is deterministic and does
    not spend additional model budget.
    """
    if not tool_context.working_draft_markdown.strip():
        return None
    expected = _expected_section_specs(target)
    saved = list(tool_context.saved_sections or [])
    if not expected or len(saved) != len(expected):
        return None
    by_id: dict[str, dict[str, Any]] = {}
    for item in saved:
        if not isinstance(item, dict):
            return None
        section_id = str(item.get("section_id") or "").strip()
        if not section_id or section_id in by_id:
            return None
        by_id[section_id] = item
    expected_ids = [str(item.get("section_id") or "") for item in expected]
    if set(by_id) != set(expected_ids):
        return None

    nodes_by_id = {
        str(node.get("section_id") or node.get("chapter_id") or node.get("id") or ""): node
        for node in (target.get("children") or [])
        if isinstance(node, dict)
    }
    sections: list[dict[str, Any]] = []
    covered_requirements: list[str] = []
    covered_scores: list[str] = []
    strict_placeholder_ids = any(
        item.get("placeholder_id")
        for item in tool_context.pending_confirmations
        if isinstance(item, dict)
    )
    for spec in expected:
        section_id = str(spec.get("section_id") or "")
        saved_section = by_id[section_id]
        content = str(saved_section.get("content") or "")
        if not content.strip():
            return None
        requirements = dict(
            (nodes_by_id.get(section_id) or {}).get("writing_requirements") or {}
        )
        response_ids = _string_list(requirements.get("response_point_ids"))
        score_ids = _string_list(requirements.get("score_item_refs"))
        covered_requirements.extend(response_ids)
        covered_scores.extend(score_ids)
        placeholder_ids = (
            extract_placeholder_ids(content)
            if strict_placeholder_ids
            else _literal_placeholders(content)
        )
        sections.append(
            {
                "section_id": section_id,
                "heading": str(spec.get("section_title") or ""),
                "block_type": "other",
                "content": content,
                "covered_score_item_ids": score_ids,
                "covered_requirement_ids": response_ids,
                "placeholder_ids": placeholder_ids,
            }
        )
    return normalize_chapter_draft_output(
        {
            "chapter_title": str(target.get("title") or ""),
            "sections": sections,
            "claims": [],
            "placeholders_used": [
                value for section in sections for value in section["placeholder_ids"]
            ],
            "score_items_covered": _unique_strings(covered_scores),
            "requirements_covered": _unique_strings(covered_requirements),
            "warnings": ["deterministically_finalized_from_complete_working_draft"],
        },
        pending_confirmations=tool_context.pending_confirmations,
    )


def _resolve_section_identities(
    draft: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    expected_sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected = deepcopy(expected_sections or _expected_section_specs(target or {}))
    expected_ids = {
        str(item.get("section_id") or "") for item in expected if item.get("section_id")
    }
    expected_by_title: dict[str, list[dict[str, Any]]] = {}
    for item in expected:
        expected_by_title.setdefault(str(item.get("section_title") or ""), []).append(
            item
        )
    identity_map: list[dict[str, Any]] = []
    resolved_by_index: dict[int, str] = {}
    issues: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, section in enumerate(draft.get("sections") or []):
        if not isinstance(section, dict):
            continue
        explicit_id = str(section.get("section_id") or "")
        title = str(section.get("heading") or "")
        section_id = ""
        resolution_method = ""
        if explicit_id and explicit_id in expected_ids and explicit_id not in used_ids:
            section_id = explicit_id
            resolution_method = "explicit_id"
        else:
            all_title_matches = list(expected_by_title.get(title, []))
            title_matches = [
                item
                for item in all_title_matches
                if str(item.get("section_id") or "") not in used_ids
            ]
            if len(all_title_matches) == 1 and len(title_matches) == 1:
                section_id = str(title_matches[0].get("section_id") or "")
                resolution_method = "unique_title"
            elif (
                0 <= index < len(expected)
                and str(expected[index].get("section_title") or "") == title
                and str(expected[index].get("section_id") or "") not in used_ids
            ):
                section_id = str(expected[index].get("section_id") or "")
                resolution_method = "title_and_order"
        if section_id:
            used_ids.add(section_id)
            resolved_by_index[index] = section_id
            identity_map.append(
                {
                    "section_id": section_id,
                    "section_title": title,
                    "resolution_method": resolution_method,
                }
            )
        else:
            issues.append(
                {
                    "severity": "fatal",
                    "category": "structure",
                    "code": "section_identity_resolution_failed",
                    "section_id": None,
                    "section_title": title or None,
                    "section_index": index,
                    "message": "section identity could not be resolved from expected outline",
                }
            )
    return {
        "expected_sections": expected,
        "section_identity_map": identity_map,
        "resolved_by_index": resolved_by_index,
        "issues": issues,
        "consistent": not issues and len(resolved_by_index) == len(
            [item for item in draft.get("sections") or [] if isinstance(item, dict)]
        ),
    }


def _canonical_issue_section_id(
    item: dict[str, Any],
    *,
    draft: dict[str, Any],
    identity_resolution: dict[str, Any],
) -> str:
    expected_ids = {
        str(value.get("section_id") or "")
        for value in identity_resolution.get("expected_sections") or []
    }
    explicit = str(item.get("section_id") or "")
    if explicit in expected_ids:
        return explicit
    identity_map = list(identity_resolution.get("section_identity_map") or [])
    title = str(item.get("section_title") or "")
    title_matches = [
        str(value.get("section_id") or "")
        for value in identity_map
        if str(value.get("section_title") or "") == title
    ]
    if len(title_matches) == 1:
        return title_matches[0]
    offending_sentence = str(item.get("offending_sentence") or "")
    offending_span = str(item.get("offending_span") or "")
    content_matches: list[str] = []
    for index, section in enumerate(draft.get("sections") or []):
        if not isinstance(section, dict):
            continue
        content = str(section.get("content") or "")
        if (
            offending_sentence
            and _normalize_diff_text(offending_sentence) in _normalize_diff_text(content)
        ) or (
            offending_span
            and _normalize_diff_text(offending_span) in _normalize_diff_text(content)
        ):
            section_id = str(
                (identity_resolution.get("resolved_by_index") or {}).get(index)
                or ""
            )
            if section_id:
                content_matches.append(section_id)
    if len(set(content_matches)) == 1:
        return content_matches[0]
    if len(identity_map) == 1:
        return str(identity_map[0].get("section_id") or "")
    return ""


def _deduplicate_fresh_repair_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for raw in items:
        item = deepcopy(raw)
        sentence_key = _normalize_diff_text(
            str(item.get("offending_sentence") or "")
        )
        span_key = _normalize_diff_text(str(item.get("offending_span") or ""))
        if not span_key:
            span_key = sentence_key
        key = (
            str(item.get("section_id") or ""),
            str(item.get("issue_code") or item.get("error_code") or ""),
            sentence_key,
            span_key,
            str(item.get("repair_disposition") or "auto_repairable"),
        )
        if key not in unique:
            unique[key] = item
            continue
        existing = unique[key]
        existing["violations"] = _unique_json_items(
            [
                *list(existing.get("violations") or []),
                *list(item.get("violations") or []),
            ]
        )
        for field in ("must_not_assert", "keywords", "must_preserve_placeholders"):
            existing[field] = list(
                dict.fromkeys(
                    [
                        *list(existing.get(field) or []),
                        *list(item.get(field) or []),
                    ]
                )
            )
    return list(unique.values())


_FRESH_PATCH_OPERATION_TYPES = {
    "replace_span",
    "replace_sentence",
    "delete_sentence",
}

_FRESH_PATCH_EXCLUDED_CODES = {
    # The structured patch protocol deliberately does not invent prose or
    # structures to repair quality/coverage omissions.  Those findings remain
    # visible to reviewers and to the final monotonic comparison.
    "insufficient_prose",
    "no_complete_prose",
    "only_tables_or_lists",
    "prose_ratio_too_low",
    "structured_content_exceeds_prose",
    "table_without_intro",
    "title_not_matched",
}


def _fresh_issue_fingerprint(item: dict[str, Any]) -> str:
    span = _normalize_diff_text(str(item.get("offending_span") or ""))
    sentence = _normalize_diff_text(str(item.get("offending_sentence") or ""))
    payload = {
        "section_id": str(item.get("section_id") or ""),
        "issue_code": str(item.get("issue_code") or item.get("error_code") or ""),
        "offending_sentence": sentence,
        "offending_span": span or sentence,
        "repair_disposition": str(
            item.get("repair_disposition") or "auto_repairable"
        ),
    }
    return _json_sha256(payload)


def _repair_task_context(content: str, sentence: str) -> tuple[str, str]:
    sentences = _diff_sentences(content)
    normalized = _normalize_diff_text(sentence)
    matches = [
        index
        for index, value in enumerate(sentences)
        if normalized
        and (
            normalized in _normalize_diff_text(value)
            or _normalize_diff_text(value) in normalized
        )
    ]
    if len(matches) != 1:
        return "", ""
    index = matches[0]
    before = sentences[index - 1] if index > 0 else ""
    after = sentences[index + 1] if index + 1 < len(sentences) else ""
    return before, after


def _conservative_candidate_requirement(issue_code: str) -> str:
    common = (
        "只保留must_preserve和已许可事实，删除所有无来源义务；优先使用中性过程说明，"
        "不得用其他报告、文件、服务、组织安排或结果承诺替代。"
    )
    if issue_code in {
        "numeric_commitment",
        "service_level_commitment",
        "schedule_frequency",
    }:
        return common + "删除数字或频率时必须同时删除无来源的报告提交、服务或履约义务。"
    if issue_code in {
        "unconfirmed_personnel_qualification",
        "personnel_narrative_without_source",
        "unconfirmed_staffing_assignment",
        "unconfirmed_resource_commitment",
    }:
        return (
            common
            + "删除具体身份、人数、资历、持证、第二人复核、命名岗位配置、独立汇报权限和已落实资源；"
            "只可保留中性职责、协同原则、确认材料边界和注册占位符。"
        )
    if issue_code == "unconfirmed_bidder_commitment":
        return (
            common
            + "删除额外报告、交付文件、培训、知识转移、整改时限、技术支持记录、运维结果保证和额外支持；"
            "只可保留招标要求、合同约定、采购人确认边界及中性检查或核验说明。"
        )
    return common


def _invalid_fresh_fact_target(
    sentence: str, *, section_title: str = ""
) -> bool:
    """Return True when a patch target is structure, not editable prose."""

    text = str(sentence or "").strip()
    if not text:
        return True
    if re.fullmatch(r"#{1,6}\s+.+", text):
        return True
    if section_title and _normalize_diff_text(text) == _normalize_diff_text(
        section_title
    ):
        return True
    if re.fullmatch(r"(?:表|Table)\s*[一二三四五六七八九十0-9]+[^。！？!?；;]*", text, re.I):
        return True
    if re.fullmatch(
        r"(?:[-*+]\s*|\d+[.)、]\s*|[一二三四五六七八九十]+[、.)]\s*)"
        r"[^。！？!?；;]{0,40}",
        text,
    ):
        return True
    return False


def _resolve_fresh_repair_target_sentence(
    item: dict[str, Any], *, section_content: str
) -> str:
    """Resolve a fact issue to a real prose sentence in the initial section."""

    section_title = str(item.get("section_title") or "")
    supplied = str(item.get("offending_sentence") or "").strip()
    if (
        supplied
        and not _invalid_fresh_fact_target(
            supplied, section_title=section_title
        )
        and _normalized_span_is_within_sentence(supplied, section_content)
    ):
        return supplied

    span = str(item.get("offending_span") or "").strip()
    if str(item.get("issue_code") or item.get("error_code") or "") in {
        "required_registered_placeholder_missing",
        "blocking_placeholder_missing_from_content",
    }:
        return next(
            (
                sentence
                for sentence in _diff_sentences(section_content)
                if not _invalid_fresh_fact_target(
                    sentence, section_title=section_title
                )
            ),
            "",
        )
    if not span:
        return ""
    matches = [
        sentence
        for sentence in _diff_sentences(section_content)
        if not _invalid_fresh_fact_target(
            sentence, section_title=section_title
        )
        and _normalized_span_is_within_sentence(span, sentence)
    ]
    return matches[0] if len(matches) == 1 else ""


def _fresh_repair_target_id(section_id: str, sentence: str) -> str:
    return _json_sha256(
        {
            "section_id": str(section_id or ""),
            "target_sentence": _normalize_diff_text(sentence),
        }
    )


def _build_fresh_repair_task_card(
    item: dict[str, Any],
    *,
    section_content: str,
    section_permissions: dict[str, Any],
    section: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    issue_code = str(item.get("issue_code") or item.get("error_code") or "")
    sentence = _resolve_fresh_repair_target_sentence(
        item, section_content=section_content
    )
    span = str(item.get("offending_span") or "").strip()
    if (
        not issue_code
        or issue_code in _FRESH_PATCH_EXCLUDED_CODES
        or not sentence
    ):
        return None
    sentence_locator = _fresh_sentence_locator(section_content, sentence)
    if sentence_locator is None:
        return None
    sentence = str(sentence_locator.get("sentence") or "")
    if (
        span
        and issue_code
        not in {
            "required_registered_placeholder_missing",
            "blocking_placeholder_missing_from_content",
        }
        and not _normalized_span_is_within_sentence(span, sentence)
    ):
        return None
    span_start = sentence.find(span) if span else -1
    span_end = span_start + len(span) if span_start >= 0 else -1
    before, after = _repair_task_context(section_content, sentence)
    confirmed_facts = [
        item
        for item in section_permissions.get("allowed_confirmed_facts") or []
        if isinstance(item, dict)
    ]
    tender_facts = [
        item
        for item in (
            section_permissions.get("effective_tender_facts")
            or section_permissions.get("allowed_tender_facts")
            or []
        )
        if isinstance(item, dict)
    ]
    placeholders = [
        item
        for item in section_permissions.get("allowed_placeholders") or []
        if isinstance(item, dict)
    ]
    preserve = [
        str(value)
        for value in item.get("must_preserve") or []
        if str(value).strip()
    ]
    preserve.extend(
        str(fact.get("text") or "")
        for fact in item.get("must_preserve_facts") or []
        if isinstance(fact, dict)
        and fact.get("text")
        and _normalized_span_is_within_sentence(str(fact["text"]), sentence)
    )
    preserve.extend(
        f"{{{{{placeholder_id}}}}}"
        for placeholder_id in item.get("must_preserve_placeholders") or []
        if placeholder_id
        and str(placeholder_id) in sentence
    )
    preserve = list(dict.fromkeys(preserve))
    explicit_must_preserve = list(preserve)
    if not preserve:
        preserve = ["保留原句中不涉及该违规片段的合法过程含义和上下文衔接"]
    safe_process_meanings = [
        str(value)
        for value in item.get("allowed_generic_role_descriptions") or []
        if str(value).strip()
    ]
    fingerprint = _fresh_issue_fingerprint(item)
    return {
        "repair_target_id": _fresh_repair_target_id(
            str(item.get("section_id") or ""), sentence
        ),
        "issue_fingerprint": fingerprint,
        "member_issue_fingerprints": [fingerprint],
        "member_issue_codes": [issue_code],
        "member_issues": [
            {
                "issue_fingerprint": fingerprint,
                "issue_code": issue_code,
                "offending_span": span,
            }
        ],
        "offending_spans": [span] if span else [],
        "section_id": str(item.get("section_id") or ""),
        "section_title": str(item.get("section_title") or ""),
        "issue_code": issue_code,
        "target": {
            "offending_sentence": sentence,
            "offending_span": span,
            "sentence_index": int(sentence_locator["sentence_index"]),
            "sentence_sha256": str(sentence_locator["sentence_sha256"]),
            "span_start": span_start if span_start >= 0 else None,
            "span_end": span_end if span_end >= 0 else None,
            "span_text": span or None,
            "context_before": before,
            "context_after": after,
        },
        "problem": str(
            item.get("message")
            or item.get("boundary_instruction")
            or item.get("required_action")
            or issue_code
        ),
        "must_preserve": preserve,
        "explicit_must_preserve": explicit_must_preserve,
        "target_carries_coverage": bool(
            item.get("covered_requirement_ids")
            or item.get("covered_score_item_ids")
            or item.get("requirement_ids")
            or item.get("score_item_ids")
        ),
        "target_contains_registered_placeholder": bool(
            set(extract_placeholder_ids(sentence))
            & {
                str(value.get("placeholder_id") or "")
                for value in placeholders
                if isinstance(value, dict) and value.get("placeholder_id")
            }
        ),
        "target_section_metadata": {
            "covered_requirement_ids": _string_list(
                (section or {}).get("covered_requirement_ids")
            ),
            "covered_score_item_ids": _string_list(
                (section or {}).get("covered_score_item_ids")
            ),
        },
        "allowed_basis": {
            "tender_fact_ids": sorted(
                {
                    str(source_id)
                    for fact in tender_facts
                    for source_id in (
                        list(fact.get("source_ids") or [])
                        or [fact.get("source_id")]
                    )
                    if source_id
                }
            ),
            "confirmed_fact_ids": sorted(
                {
                    str(fact.get("fact_id") or fact.get("source_id") or "")
                    for fact in confirmed_facts
                    if fact.get("fact_id") or fact.get("source_id")
                }
            ),
            "allowed_placeholders": sorted(
                {
                    str(value.get("placeholder_id") or "")
                    for value in placeholders
                    if value.get("placeholder_id")
                }
            ),
            "safe_process_meanings": safe_process_meanings,
        },
        "must_not_add": list(
            dict.fromkeys(
                [
                    *[
                        str(value)
                        for value in item.get("must_not_assert") or []
                        if str(value).strip()
                    ],
                    "未列入许可的数字或固定频率",
                    "具体人员事实或具体组织配置",
                    "产品能力或性能结果",
                    "公司资源或人员投入承诺",
                    "招标范围外的投标人承诺或交付成果",
                ]
            )
        ),
        "allowed_operation_types": [
            "replace_span",
            "replace_sentence",
            "delete_sentence",
        ],
        "section_quality_floor": deepcopy(
            item.get("section_quality_floor") or {}
        ),
        "candidate_requirements": {
            "primary": "尽量保留合法含义的正常改写，不得新增事实或义务。",
            "conservative": _conservative_candidate_requirement(issue_code),
            "delete": "仅当整句可安全删除且不会损害覆盖、占位符或正文质量时提供。",
        },
        "related_violations": [],
    }


def _group_fresh_repair_task_cards(
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse issue cards that edit the same canonical section sentence."""

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for source in cards:
        card = deepcopy(source)
        sentence = str((card.get("target") or {}).get("offending_sentence") or "")
        key = (
            str(card.get("section_id") or ""),
            _normalize_diff_text(sentence),
        )
        if not all(key):
            continue
        if key not in grouped:
            grouped[key] = card
            continue
        target = grouped[key]
        for field in (
            "member_issue_fingerprints",
            "member_issue_codes",
            "member_issues",
            "offending_spans",
            "must_preserve",
            "explicit_must_preserve",
            "must_not_add",
            "related_violations",
        ):
            target[field] = _unique_json_items(
                [*list(target.get(field) or []), *list(card.get(field) or [])]
            )
        target["target_carries_coverage"] = bool(
            target.get("target_carries_coverage")
            or card.get("target_carries_coverage")
        )
        target["target_contains_registered_placeholder"] = bool(
            target.get("target_contains_registered_placeholder")
            or card.get("target_contains_registered_placeholder")
        )
        target["problem"] = "；".join(
            dict.fromkeys(
                value
                for value in (
                    str(target.get("problem") or "").strip(),
                    str(card.get("problem") or "").strip(),
                )
                if value
            )
        )

    results = list(grouped.values())
    for card in results:
        members = list(dict.fromkeys(card.get("member_issue_fingerprints") or []))
        codes = list(dict.fromkeys(card.get("member_issue_codes") or []))
        spans = list(
            dict.fromkeys(
                str(value).strip()
                for value in card.get("offending_spans") or []
                if str(value).strip()
            )
        )
        sentence = str((card.get("target") or {}).get("offending_sentence") or "")
        single_span_is_unique = bool(
            len(members) == 1
            and len(spans) == 1
            and len(
                re.findall(
                    re.escape(_normalize_diff_text(spans[0])),
                    _normalize_diff_text(sentence),
                )
            )
            == 1
        )
        card["member_issue_fingerprints"] = members
        card["member_issue_codes"] = codes
        card["offending_spans"] = spans
        card["related_violations"] = _unique_json_items(
            list(card.get("related_violations") or [])
        )
        card["allowed_operation_types"] = (
            ["replace_span", "replace_sentence", "delete_sentence"]
            if single_span_is_unique
            else ["replace_sentence", "delete_sentence"]
        )
        card.setdefault("candidate_requirements", {})["conservative"] = "；".join(
            dict.fromkeys(
                _conservative_candidate_requirement(code) for code in codes
            )
        )
    return results


def _unique_json_items(items: list[Any]) -> list[Any]:
    return list(
        {
            json.dumps(item, ensure_ascii=False, sort_keys=True): item
            for item in items
        }.values()
    )


def _build_fresh_repair_context(
    *,
    validation: dict[str, Any],
    draft: dict[str, Any],
    fact_ledger: list[dict[str, Any]],
    pending_confirmations: list[dict[str, Any]],
    target: dict[str, Any],
    prohibited_material_ids: list[str] | None = None,
    local_evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    identity_resolution = _resolve_section_identities(draft, target=target)
    active_pending_confirmations, _ = _active_pending_confirmations(
        pending_confirmations,
        generation_permissions=fact_ledger,
    )
    audit = classify_chapter_audit(
        validation,
        pending_confirmations=active_pending_confirmations,
        prohibited_material_ids=prohibited_material_ids or [],
        local_evidence_refs=local_evidence_refs or [],
    )
    auto_messages = {
        str(item.get("message") or "")
        for item in audit.get("auto_repairable") or []
    }
    auto_errors = [
        str(error)
        for error in validation.get("errors") or []
        if str(error) in auto_messages
    ]
    details = _fresh_quality_error_details(
        auto_errors,
        draft=draft,
        expected_sections=identity_resolution.get("expected_sections") or [],
    )
    ledger_by_section = {
        str(item.get("section_id") or item.get("section_title") or ""): item
        for item in fact_ledger
        if isinstance(item, dict)
    }
    ledger_by_section.update(
        {
            str(item.get("section_title") or ""): item
            for item in fact_ledger
            if isinstance(item, dict) and item.get("section_title")
        }
    )
    tender_facts: list[dict[str, Any]] = []
    numeric_facts: list[dict[str, Any]] = []
    personnel_facts: list[dict[str, Any]] = []
    product_capabilities: list[dict[str, Any]] = []
    placeholders: list[dict[str, Any]] = []
    section_protection: dict[str, dict[str, Any]] = {}
    draft_sections_by_reference: dict[str, dict[str, Any]] = {}
    for section_index, section in enumerate(draft.get("sections") or []):
        if not isinstance(section, dict):
            continue
        canonical_id = str(
            identity_resolution["resolved_by_index"].get(section_index) or ""
        )
        for reference in (
            canonical_id,
            section.get("section_id"),
            section.get("heading"),
        ):
            if reference:
                draft_sections_by_reference[str(reference)] = section
    target_nodes_by_reference = {
        str(reference): node
        for node in [target, *_all_target_nodes(target)]
        if isinstance(node, dict)
        for reference in (
            node.get("section_id"),
            node.get("chapter_id"),
            node.get("id"),
            node.get("title"),
        )
        if reference
    }
    for entry in fact_ledger:
        if not isinstance(entry, dict):
            continue
        section_id = str(entry.get("section_id") or "")
        permissions = dict(entry.get("generation_permissions") or {})
        allowed_tender = [
            {
                "text": item.get("text"),
                "source_id": item.get("source_id"),
            }
            for item in [
                *list(permissions.get("allowed_tender_facts") or []),
                *list(
                    permissions.get("chapter_allowed_tender_facts")
                    or entry.get("chapter_allowed_tender_facts")
                    or []
                ),
            ]
            if isinstance(item, dict) and item.get("source_id")
        ]
        allowed_numeric = [
            deepcopy(item)
            for item in [
                *list(permissions.get("allowed_confirmed_numeric_facts") or []),
                *[
                    value
                    for value in [
                        *list(
                            permissions.get("allowed_tender_numeric_facts")
                            or []
                        ),
                        *list(
                            permissions.get(
                                "chapter_allowed_tender_numeric_facts"
                            )
                            or entry.get(
                                "chapter_allowed_tender_numeric_facts"
                            )
                            or []
                        ),
                    ]
                    if isinstance(value, dict)
                    and value.get("usage") == "no_deviation_response"
                ],
            ]
            if isinstance(item, dict)
            and (item.get("source_id") or item.get("source_ids"))
        ]
        allowed_personnel = [
            deepcopy(item)
            for item in permissions.get("allowed_confirmed_personnel_facts") or []
            if isinstance(item, dict)
        ]
        allowed_products = [
            deepcopy(item)
            for item in permissions.get("allowed_confirmed_product_capabilities") or []
            if isinstance(item, dict)
        ]
        allowed_placeholders = [
            {
                "placeholder_id": item.get("placeholder_id"),
                "placeholder_token": item.get("placeholder_token"),
                "section_id": section_id or None,
            }
            for item in permissions.get("allowed_placeholders") or []
            if isinstance(item, dict) and item.get("placeholder_id")
        ]
        allowed_generic_role_descriptions = [
            str(value)
            for value in permissions.get("allowed_generic_role_descriptions") or []
            if str(value).strip()
        ]
        draft_section = (
            draft_sections_by_reference.get(section_id)
            or draft_sections_by_reference.get(str(entry.get("section_title") or ""))
            or {}
        )
        quality_floor = section_quality_floor(draft_section)
        target_node = (
            target_nodes_by_reference.get(section_id)
            or target_nodes_by_reference.get(str(entry.get("section_title") or ""))
            or {}
        )
        outline_dimensions = [
            str(value)
            for key in (
                "required_dimensions",
                "required_content_dimensions",
                "section_dimensions",
            )
            for value in (
                (target_node.get("writing_requirements") or {}).get(key)
                or target_node.get(key)
                or []
            )
            if str(value).strip()
        ]
        quality_floor["required_dimensions"] = list(
            dict.fromkeys(
                [
                    *list(quality_floor.get("required_dimensions") or []),
                    *outline_dimensions,
                ]
            )
        )
        personnel_mode = str(
            permissions.get("personnel_content_mode")
            or entry.get("personnel_content_mode")
            or "source_backed"
        )
        is_personnel_section = bool(
            permissions.get("is_personnel_section")
            or entry.get("is_personnel_section")
        )
        if personnel_mode == "source_only" and is_personnel_section:
            quality_floor = {
                "minimum_paragraph_count": 0,
                "minimum_prose_characters": 0,
                "required_dimensions": [],
            }
        section_protection[section_id] = {
            "tender_facts": allowed_tender,
            "placeholders": allowed_placeholders,
            "generic_role_descriptions": allowed_generic_role_descriptions,
            "allowed_product_capabilities": allowed_products,
            "product_capability_generation_allowed": bool(allowed_products),
            "section_quality_floor": quality_floor,
            "personnel_content_mode": personnel_mode,
            "is_personnel_section": is_personnel_section,
        }
        tender_facts.extend(allowed_tender)
        numeric_facts.extend(allowed_numeric)
        personnel_facts.extend(allowed_personnel)
        product_capabilities.extend(allowed_products)
        placeholders.extend(allowed_placeholders)

    def unique_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(
            {
                json.dumps(item, ensure_ascii=False, sort_keys=True): item
                for item in items
            }.values()
        )

    tender_facts = unique_dicts(tender_facts)
    numeric_facts = unique_dicts(numeric_facts)
    personnel_facts = unique_dicts(personnel_facts)
    product_capabilities = unique_dicts(product_capabilities)
    placeholders = unique_dicts(placeholders)
    registered_table_titles = list(
        dict.fromkeys(
            str(item.get("title") or "")
            for item in draft.get("generated_supporting_tables") or []
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        )
    )
    registered_table_introductions = _registered_table_introductions(draft)
    required_section_ids = [
        str(item.get("section_id") or item.get("chapter_id") or item.get("id") or "")
        for item in (list(target.get("children") or []) or [target])
        if item.get("section_id") or item.get("chapter_id") or item.get("id")
    ]
    must_preserve = {
        "confirmed_tender_facts": tender_facts,
        "confirmed_numeric_facts": numeric_facts,
        "confirmed_personnel_facts": personnel_facts,
        "confirmed_product_capabilities": product_capabilities,
        "registered_placeholders": placeholders,
        "required_section_ids": required_section_ids,
        "required_requirement_ids": sorted(
            set(_collect_target_requirement_values(target, "response_point_ids"))
        ),
        "required_score_item_ids": sorted(
            set(_collect_target_requirement_values(target, "score_item_refs"))
        ),
        "registered_table_titles": registered_table_titles,
        "registered_table_introductions": registered_table_introductions,
        "section_generation_guards": {
            section_id: {
                "allowed_product_capabilities": deepcopy(
                    protection["allowed_product_capabilities"]
                ),
                "product_capability_generation_allowed": bool(
                    protection["product_capability_generation_allowed"]
                ),
                "personnel_content_mode": protection[
                    "personnel_content_mode"
                ],
                "is_personnel_section": bool(
                    protection.get("is_personnel_section")
                ),
            }
            for section_id, protection in section_protection.items()
        },
        "section_quality_floors": {
            section_id: deepcopy(protection["section_quality_floor"])
            for section_id, protection in section_protection.items()
        },
        "writing_quality_requirements": {
            "complete_prose_required": True,
            "table_cannot_replace_prose": True,
            "existing_quality_thresholds_must_pass": True,
            "section_specific_structure_must_be_preserved": True,
            "fact_repair_must_not_remove_unrelated_section_structure": True,
            "risk_section_required_dimensions": [
                "风险识别",
                "风险影响",
                "预防措施",
                "应急措施",
                "升级处理",
            ],
            "ordinary_table_requires_matching_intro": True,
            "preserve_existing_table_introductions": True,
            "do_not_create_or_redesign_tables_during_fact_repair": True,
        },
        "must_not_create_fact_categories": [
            "unconfirmed_product_capability",
            "unconfirmed_staffing_assignment",
            "unconfirmed_resource_commitment",
            "unconfirmed_personnel_qualification",
            "unconfirmed_bidder_commitment",
        ],
    }
    raw_auto_repair_items: list[dict[str, Any]] = []
    for detail in details:
        section_id = _canonical_issue_section_id(
            detail,
            draft=draft,
            identity_resolution=identity_resolution,
        )
        protection = section_protection.get(section_id) or {
            "tender_facts": tender_facts,
            "placeholders": placeholders,
            "generic_role_descriptions": [],
            "allowed_product_capabilities": product_capabilities,
            "product_capability_generation_allowed": bool(product_capabilities),
            "section_quality_floor": {
                "minimum_paragraph_count": 0,
                "minimum_prose_characters": 0,
                "required_dimensions": [],
            },
            "personnel_content_mode": "source_backed",
            "is_personnel_section": False,
        }
        violations = list(detail.get("violations") or [{}])
        for violation in violations:
            item = {
                "section_id": section_id or None,
                "section_title": detail.get("section_title"),
                "issue_code": detail.get("issue_code") or detail.get("error_code"),
                "error_code": detail.get("issue_code") or detail.get("error_code"),
                "repair_disposition": "auto_repairable",
                "repair_type": detail.get("repair_type"),
                "offending_sentence": violation.get("offending_sentence")
                or detail.get("offending_sentence"),
                "offending_span": violation.get("offending_span")
                or detail.get("offending_span"),
                "required_action": detail.get("required_action"),
                "must_not_assert": list(
                    dict.fromkeys(
                        [
                            *list(detail.get("must_not_assert") or []),
                            *list(violation.get("must_not_assert") or []),
                        ]
                    )
                ),
                "must_preserve_facts": deepcopy(protection["tender_facts"]),
                "must_preserve_placeholders": [
                    str(value.get("placeholder_id"))
                    for value in protection["placeholders"]
                    if value.get("placeholder_id")
                ],
                "keywords": list(detail.get("keywords") or []),
                "allowed_product_capabilities": deepcopy(
                    protection["allowed_product_capabilities"]
                ),
                "product_capability_generation_allowed": bool(
                    protection["product_capability_generation_allowed"]
                ),
                "section_quality_floor": deepcopy(
                    protection["section_quality_floor"]
                ),
                "violations": [deepcopy(violation)],
            }
            if item["issue_code"] in {
                "numeric_commitment",
                "service_level_commitment",
            }:
                precise_span = _select_unprotected_numeric_span(
                    str(item.get("offending_sentence") or ""),
                    protected_numeric_facts=numeric_facts,
                )
                if precise_span:
                    item["offending_span"] = precise_span
                    item["must_not_assert"] = [precise_span]
                    item["required_action"] = (
                        f"删除未经确认的{precise_span}，改为及时处理或按合同及确认后的"
                        "计划、响应机制执行；不得改动有source ID且数值、单位和语义一致的招标原值。"
                    )
                    item["keywords"] = [precise_span]
            if item["issue_code"] in {
                "unconfirmed_personnel_qualification",
                "personnel_narrative_without_source",
            }:
                item["must_remove_qualification_assertion"] = True
                item["allowed_generic_role_descriptions"] = list(
                    protection.get("generic_role_descriptions") or []
                )
                has_existing_role_duty = bool(
                    re.search(
                        r"(?:负责|组织|审核|审查|归档|检查|复核|记录|开展|执行|"
                        r"跟踪|协调|确认|提交|管理|指导)",
                        str(item.get("offending_sentence") or ""),
                    )
                )
                item["allowed_replacement_type"] = (
                    "preserve_existing_generic_role_duty_only"
                    if has_existing_role_duty
                    else "delete_unsupported_sentence"
                )
                item["must_not_assert"] = list(
                    dict.fromkeys(
                        [
                            *list(item.get("must_not_assert") or []),
                            "具备丰富经验",
                            "具有数据中心部署经验",
                            "熟悉超融合系统",
                            "精通虚拟化技术",
                            "持有相关认证",
                            "能够独立完成",
                            "具备医疗信息化项目经验",
                        ]
                    )
                )
                item["required_action"] = (
                    "删除无来源的资历、年限、证书、项目数量、经验、技能、"
                    "能力评价、具体人员身份和资源安排；不得换用另一种资历断言。"
                    "保留中性项目角色动作、招标人员原表、已注册人员占位符或已确认人员事实。"
                )
            if item["issue_code"] in {
                "unregistered_placeholder",
                "unregistered_placeholder_id",
                "placeholder",
            }:
                token_match = re.search(
                    r"\{\{PH-[A-Z0-9-]+\}\}|PH-[A-Z0-9-]+",
                    str(item.get("offending_sentence") or "")
                    + str(item.get("offending_span") or ""),
                )
                token = token_match.group(0) if token_match else ""
                item["repair_type"] = "delete_unregistered_placeholder"
                item["unregistered_placeholder_token"] = token or None
                item["must_not_assert"] = [token] if token else []
                item["required_action"] = (
                    "删除未注册占位符"
                    + (f"{token}" if token else "token")
                    + "；不得填入推测值、改名或创建替代占位符。"
                )
            if item["issue_code"] in {
                "required_registered_placeholder_missing",
                "blocking_placeholder_missing_from_content",
            }:
                missing_ids = re.findall(
                    r"PH-[A-Z0-9-]+",
                    str(item.get("offending_sentence") or "")
                    + str(item.get("offending_span") or ""),
                )
                tokens = [f"{{{{{value}}}}}" for value in missing_ids]
                item["required_placeholder_ids"] = missing_ids
                item["required_action"] = (
                    "重新插入以下已注册占位符且不得填写真实值："
                    + "、".join(tokens)
                )
            if item["issue_code"] == "title_not_matched":
                entry = ledger_by_section.get(section_id) or ledger_by_section.get(
                    str(detail.get("section_title") or "")
                )
                section_titles = [
                    str(value.get("title") or "")
                    for value in draft.get("generated_supporting_tables") or []
                    if isinstance(value, dict)
                    and str(value.get("section_id") or "")
                    in {
                        section_id,
                        str(detail.get("section_title") or ""),
                    }
                ]
                if not section_titles and entry:
                    section_titles = registered_table_titles
                item["registered_table_titles"] = section_titles
                if section_titles:
                    item["required_action"] = (
                        "仅使用以下generated_supporting_tables.title准确表题："
                        + "、".join(section_titles)
                    )
            raw_auto_repair_items.append(item)

    auto_repair_items = _deduplicate_fresh_repair_items(raw_auto_repair_items)
    for item in auto_repair_items:
        item["issue_fingerprint"] = _fresh_issue_fingerprint(item)

    review_only_guards: list[dict[str, Any]] = []
    for issue in audit.get("review_only") or []:
        placeholder_id = issue.get("placeholder_id")
        guard = {
            "section_id": issue.get("section_id"),
            "section_title": issue.get("section_title"),
            "issue_code": issue.get("code"),
            "repair_disposition": "review_only",
            "message": issue.get("message"),
            "placeholder_id": placeholder_id,
            "repair_type": (
                "preserve_for_human_confirmation"
                if issue.get("code")
                == "required_project_fact_pending_confirmation_missing"
                else "preserve_review_only_state"
            ),
            "required_action": (
                "保留为人工确认事项，不得由Fresh补充或改写为确认事实。"
                if issue.get("code")
                == "required_project_fact_pending_confirmation_missing"
                else "保留已登记占位符或待确认状态，不得由Fresh补充答案。"
            ),
            "guard_instruction": (
                "该事项缺少真实信息，不属于本轮自动修复目标；"
                "必须保留已登记占位符或待确认状态，不得自行补充答案。"
            ),
        }
        if issue.get("candidate_ids"):
            guard["candidate_ids"] = list(issue.get("candidate_ids") or [])
        review_only_guards.append(guard)
    review_only_guards = unique_dicts(review_only_guards)
    preserved_placeholder_ids = list(
        dict.fromkeys(
            str(item.get("placeholder_id"))
            for item in placeholders
            if item.get("placeholder_id")
        )
    )
    preserved_tender_fact_ids = list(
        dict.fromkeys(
            str(item.get("source_id"))
            for item in tender_facts
            if item.get("source_id")
        )
    )
    draft_section_references = [
        str(value)
        for _, value in sorted(identity_resolution["resolved_by_index"].items())
        if value
    ]
    issue_task_cards: list[dict[str, Any]] = []
    unsupported_repair_issues: list[dict[str, Any]] = []
    for item in auto_repair_items:
        section_id = _canonical_issue_section_id(
            item,
            draft=draft,
            identity_resolution=identity_resolution,
        )
        if not section_id and len(draft_section_references) == 1:
            section_id = draft_section_references[0]
        item["section_id"] = section_id or None
        if not section_id:
            continue
        ledger_entry = ledger_by_section.get(section_id) or {}
        permissions = deepcopy(ledger_entry.get("generation_permissions") or {})
        draft_section = draft_sections_by_reference.get(section_id) or {}
        task_card = _build_fresh_repair_task_card(
            item,
            section_content=str(draft_section.get("content") or ""),
            section_permissions=permissions,
            section=draft_section,
        )
        if task_card:
            issue_task_cards.append(task_card)
        elif str(item.get("issue_code") or "") not in _FRESH_PATCH_EXCLUDED_CODES:
            unsupported_repair_issues.append(
                {
                    "issue_fingerprint": _fresh_issue_fingerprint(item),
                    "section_id": section_id,
                    "section_title": item.get("section_title"),
                    "issue_code": item.get("issue_code"),
                    "status": "unsupported",
                    "diagnostic_codes": ["fresh_patch_invalid_fact_target"],
                }
            )

    # Give each task the other validator-confirmed violations attached to the
    # exact same sentence.  This prevents a frequency repair from retaining an
    # unsupported report/submission obligation, without merging unrelated
    # sentences or changing the primary issue fingerprint.
    for card in issue_task_cards:
        card_sentence = _normalize_diff_text(
            str((card.get("target") or {}).get("offending_sentence") or "")
        )
        related: list[dict[str, Any]] = []
        for item in auto_repair_items:
            if str(item.get("section_id") or "") != str(card.get("section_id") or ""):
                continue
            violations = list(item.get("violations") or []) or [item]
            for violation in violations:
                if not isinstance(violation, dict):
                    continue
                item_sentence = _normalize_diff_text(
                    str(
                        violation.get("offending_sentence")
                        or item.get("offending_sentence")
                        or ""
                    )
                )
                if not card_sentence or item_sentence != card_sentence:
                    continue
                related.append(
                    {
                        "issue_code": str(
                            item.get("issue_code") or item.get("error_code") or ""
                        ),
                        "offending_span": str(
                            violation.get("offending_span")
                            or item.get("offending_span")
                            or ""
                        ),
                    }
                )
        card["related_violations"] = _unique_json_items(related)

    repair_task_cards = _group_fresh_repair_task_cards(issue_task_cards)

    editable_issue_spans: list[dict[str, Any]] = []
    for card in repair_task_cards:
        target_payload = dict(card.get("target") or {})
        editable_issue_spans.append(
            {
                "issue_fingerprint": card.get("issue_fingerprint"),
                "repair_target_id": card.get("repair_target_id"),
                "member_issue_fingerprints": deepcopy(
                    card.get("member_issue_fingerprints") or []
                ),
                "section_id": card.get("section_id"),
                "issue_code": card.get("issue_code"),
                "offending_sentence": target_payload.get("offending_sentence"),
                "offending_span": target_payload.get("offending_span"),
            }
        )
    editable_section_ids = list(
        dict.fromkeys(
            str(item.get("section_id") or "")
            for item in editable_issue_spans
            if item.get("section_id")
        )
    )
    initial_section_hashes = {
        str(identity_resolution["resolved_by_index"][index]): _json_sha256(section)
        for index, section in enumerate(draft.get("sections") or [])
        if isinstance(section, dict)
        and index in identity_resolution["resolved_by_index"]
    }
    frozen_section_hashes = {
        section_id: digest
        for section_id, digest in initial_section_hashes.items()
        if section_id not in set(editable_section_ids)
    }
    fresh_edit_scope = {
        "editable_section_ids": editable_section_ids,
        "editable_issue_count": len(editable_issue_spans),
        "raw_auto_repair_issue_count": len(raw_auto_repair_items),
        "unique_auto_repair_issue_count": len(auto_repair_items),
        "editable_issue_spans": editable_issue_spans,
        "frozen_section_count": len(frozen_section_hashes),
        "frozen_section_hashes": frozen_section_hashes,
        "initial_section_hashes": initial_section_hashes,
        "section_identity_consistent": bool(identity_resolution["consistent"]),
    }
    task_section_ids = {
        str(card.get("section_id") or "")
        for card in repair_task_cards
        if card.get("section_id")
    }
    section_contexts: dict[str, dict[str, Any]] = {}
    confirmed_project_facts: list[dict[str, Any]] = []
    candidate_project_facts: list[dict[str, Any]] = []
    for entry in fact_ledger:
        if not isinstance(entry, dict):
            continue
        section_id = str(entry.get("section_id") or "")
        permissions = deepcopy(entry.get("generation_permissions") or {})
        confirmed_project_facts.extend(
            deepcopy(permissions.get("allowed_confirmed_facts") or [])
        )
        candidate_project_facts.extend(
            {
                "evidence_id": str(value),
                "status": "candidate",
                "policy": "forbidden_as_confirmed_fact",
            }
            for value in entry.get("candidate_evidence_ids") or []
            if value
        )
        if section_id not in task_section_ids:
            continue
        target_node = target_nodes_by_reference.get(section_id) or {}
        section_contexts[section_id] = {
            "requirement_ids": sorted(
                set(
                    _collect_target_requirement_values(
                        target_node, "response_point_ids"
                    )
                )
            ),
            "score_item_ids": sorted(
                set(
                    _collect_target_requirement_values(
                        target_node, "score_item_refs"
                    )
                )
            ),
            "effective_tender_facts": deepcopy(
                permissions.get("effective_tender_facts")
                or permissions.get("allowed_tender_facts")
                or []
            ),
            "effective_tender_numeric_facts": deepcopy(
                permissions.get("effective_tender_numeric_facts")
                or permissions.get("allowed_tender_numeric_facts")
                or []
            ),
            "confirmed_facts": deepcopy(
                permissions.get("allowed_confirmed_facts") or []
            ),
            "allowed_placeholders": deepcopy(
                permissions.get("allowed_placeholders") or []
            ),
            "personnel_mode": str(
                permissions.get("personnel_content_mode")
                or entry.get("personnel_content_mode")
                or "source_backed"
            ),
            "quality_requirements": deepcopy(
                section_protection.get(section_id, {}).get(
                    "section_quality_floor"
                )
                or {}
            ),
            # This is the same closed permission object consumed by the initial
            # generator and by all deterministic audits; it is not a Fresh-only
            # whitelist.
            "effective_writing_permissions": permissions,
        }
    compiled_effective_writing_context = {
        "chapter_id": str(
            target.get("chapter_id") or target.get("section_id") or target.get("id") or ""
        ),
        "permission_source": "section_fact_ledger",
        "chapter_rules": {
            "closed_fact_permissions": True,
            "candidate_facts_forbidden_as_confirmed": True,
            "unlisted_commitments_forbidden": True,
        },
        "writing_style_rules": deepcopy(
            must_preserve.get("writing_quality_requirements") or {}
        ),
        "requirement_context": {
            "required_requirement_ids": deepcopy(
                must_preserve.get("required_requirement_ids") or []
            )
        },
        "score_item_context": {
            "required_score_item_ids": deepcopy(
                must_preserve.get("required_score_item_ids") or []
            )
        },
        "confirmed_project_facts": _unique_json_items(confirmed_project_facts),
        "candidate_project_facts": _unique_json_items(candidate_project_facts),
        "registered_placeholders": deepcopy(placeholders),
        "chapter_effective_tender_facts": deepcopy(tender_facts),
        "chapter_effective_tender_numeric_facts": deepcopy(numeric_facts),
        "section_contexts": section_contexts,
    }
    return {
        "edit_mode": "multi_candidate_structured_patch",
        "fresh_repair_mode": "multi_candidate_structured_patch",
        "initial_candidate": deepcopy(draft),
        "initial_candidate_sha256": _candidate_sha256(draft),
        "compiled_effective_writing_context": compiled_effective_writing_context,
        "repair_task_cards": repair_task_cards,
        "unsupported_repair_issues": unsupported_repair_issues,
        "fresh_edit_scope": fresh_edit_scope,
        "expected_sections": deepcopy(identity_resolution["expected_sections"]),
        "section_identity_map": deepcopy(identity_resolution["section_identity_map"]),
        "section_identity_issues": deepcopy(identity_resolution["issues"]),
        "auto_repair_items": auto_repair_items,
        "review_only_guards": review_only_guards,
        "must_preserve": must_preserve,
        "repair_input_summary": {
            "total_issue_count": int(audit.get("unique_issue_count") or 0),
            "auto_repairable_count": int(
                audit.get("auto_repairable_unique_issue_count") or 0
            ),
            "review_only_count": int(
                audit.get("review_only_unique_issue_count") or 0
            ),
            "unique_issue_count": int(audit.get("unique_issue_count") or 0),
            "auto_repairable_unique_issue_count": int(
                audit.get("auto_repairable_unique_issue_count") or 0
            ),
            "review_only_unique_issue_count": int(
                audit.get("review_only_unique_issue_count") or 0
            ),
            "repair_item_count": len(repair_task_cards),
            "raw_auto_repair_issue_count": len(raw_auto_repair_items),
            "unique_auto_repair_issue_count": len(auto_repair_items),
            "review_guard_count": len(review_only_guards),
            "protected_fact_count": len(tender_facts)
            + len(numeric_facts)
            + len(personnel_facts)
            + len(product_capabilities),
            "protected_placeholder_count": len(preserved_placeholder_ids),
        },
        "auto_repair_issue_codes": sorted(
            {
                str(item.get("issue_code") or "")
                for item in auto_repair_items
                if item.get("issue_code")
            }
        ),
        "review_only_issue_codes": sorted(
            {
                str(item.get("issue_code") or "")
                for item in review_only_guards
                if item.get("issue_code")
            }
        ),
        "preserved_placeholder_ids": preserved_placeholder_ids,
        "preserved_tender_fact_ids": preserved_tender_fact_ids,
    }


def _select_unprotected_numeric_span(
    sentence: str,
    *,
    protected_numeric_facts: list[dict[str, Any]],
) -> str:
    value = str(sentence or "")
    pattern = re.compile(
        r"(?:提前|前)?\s*(?:\d+\s*[×xX*]\s*\d+|\d+|"
        r"[一二三四五六七八九十百]+)\s*(?:个)?"
        r"(?:工作日|小时|分钟|人|名|人天|年|个月|日|周|月|天)"
        r"(?:内|前|完成|响应|通知|到场|驻场|试运行|投入)?"
    )
    semantic_groups = (
        ("货到", "交货", "到货", "交付"),
        ("安装", "部署"),
        ("响应", "故障", "服务"),
        ("到场", "维修工程师"),
        ("保修", "质保"),
        ("整改", "纠正"),
        ("报告", "通报"),
        ("项目经理", "人员", "工程师", "团队"),
    )
    for match in pattern.finditer(value):
        span = re.sub(r"\s+", "", match.group(0))
        clause_start = max(
            value.rfind(separator, 0, match.start())
            for separator in ("，", "；", ";", "。", "\n")
        )
        following = [
            position
            for separator in ("，", "；", ";", "。", "\n")
            for position in [value.find(separator, match.end())]
            if position >= 0
        ]
        clause_end = min(following) if following else len(value)
        clause = value[clause_start + 1 : clause_end]
        protected = False
        for fact in protected_numeric_facts:
            if not (fact.get("source_id") or fact.get("source_ids")):
                continue
            fact_text = str(fact.get("text") or "")
            fact_token = f"{fact.get('value')}{fact.get('unit') or ''}"
            comparable_span = re.sub(r"(?:内|前|完成|响应|通知|到场|驻场|试运行|投入)$", "", span)
            if re.sub(r"\s+", "", fact_token) != comparable_span.removeprefix("前").removeprefix("提前"):
                continue
            if any(
                any(term in clause for term in group)
                and any(term in fact_text for term in group)
                for group in semantic_groups
            ):
                protected = True
                break
        if not protected:
            return span
    return ""


def _fresh_quality_regeneration_instruction(
    *,
    schema: dict[str, Any],
    repair_context: dict[str, Any] | None = None,
    error_details: list[dict[str, Any]] | None = None,
) -> str:
    context = deepcopy(repair_context or {})
    if not context:
        context = {
            "repair_task_cards": list(error_details or []),
            "review_only_guards": [],
            "compiled_effective_writing_context": {},
        }
    payload = {
        "fresh_repair_mode": "multi_candidate_structured_patch",
        "base_candidate_hash": context.get("initial_candidate_sha256"),
        "initial_candidate": context.get("initial_candidate"),
        "compiled_effective_writing_context": context.get(
            "compiled_effective_writing_context"
        )
        or {},
        "repair_task_cards": context.get("repair_task_cards") or [],
        "review_only_guards": context.get("review_only_guards") or [],
        "response_schema": schema,
    }
    return (
        "执行一次多问题、局部候选式返修。只返回结构化Patch JSON，不得返回完整章节，"
        "不得调用工具。initial_candidate是唯一编辑基线；compiled_effective_writing_context"
        "与initial生成和Validator使用同一事实许可，不得使用任何先前Fresh结果。"
        "本轮只清理initial中的残余违规，不做内容增强。每张repair_task_card最多给出三个候选，"
        "依次为保留合法含义的改写、只保留安全过程含义的保守改写、删除原句。"
        "只允许replace_span、replace_sentence、delete_sentence；不得扩写章节、新增段落、"
        "新增事实、产品能力、人员或组织配置、资源承诺、报告或交付义务；"
        "不得补充事实、人员内容、产品方案、资源计划，不得重新设计表格，"
        "不得增加MLAG、链路冗余、双人复核、质量保障工程师复核、备份人员、"
        "公司后备资源或人员替换机制。无法安全改写时只能使用delete_sentence，"
        "且表格不得替代完整正文。任何候选都不得造成新的insufficient_prose或prose ratio退化。"
        "review_only_guards不是修复目标，不得为其生成候选或补充答案。"
        "对已登记但未填写的人员或团队事项必须保留占位符，不得自行补充答案。"
        "候选无需自报事实类型、来源ID或是否合规，程序会逐项审计。载荷如下：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _fresh_patch_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["base_candidate_hash", "repair_results"],
        "properties": {
            "base_candidate_hash": {"type": "string", "minLength": 1},
            "repair_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["section_id", "candidates"],
                    "anyOf": [
                        {"required": ["repair_target_id"]},
                        {"required": ["issue_fingerprint"]},
                    ],
                    "properties": {
                        "repair_target_id": {"type": "string", "minLength": 1},
                        "issue_fingerprint": {"type": "string", "minLength": 1},
                        "section_id": {"type": "string", "minLength": 1},
                        "candidates": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "candidate_id",
                                    "operation_type",
                                    "replacement_text",
                                ],
                                "properties": {
                                    "candidate_id": {"type": "string", "minLength": 1},
                                    "candidate_role": {
                                        "enum": ["primary", "conservative", "delete"]
                                    },
                                    "operation_type": {
                                        "enum": sorted(_FRESH_PATCH_OPERATION_TYPES)
                                    },
                                    "replacement_text": {"type": "string"},
                                },
                                "allOf": [
                                    {
                                        "if": {
                                            "properties": {
                                                "operation_type": {
                                                    "const": "delete_sentence"
                                                }
                                            },
                                            "required": ["operation_type"],
                                        },
                                        "then": {
                                            "properties": {
                                                "replacement_text": {
                                                    "const": ""
                                                }
                                            }
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
            },
        },
    }


def _submit_structured_patch_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_structured_patch",
            "description": (
                "提交一次Fresh结构化局部返修的全部候选。参数必须直接符合"
                "Structured Patch Schema，不得返回完整章节或解释文本。"
            ),
            "parameters": _fresh_patch_response_schema(),
            "strict": True,
        },
    }


def _forced_structured_patch_tool_choice() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": "submit_structured_patch"},
    }


def _fresh_structured_patch_messages(
    repair_context: dict[str, Any],
) -> list[dict[str, Any]]:
    payload = {
        "fresh_repair_mode": "multi_candidate_structured_patch",
        "base_candidate_hash": repair_context.get("initial_candidate_sha256"),
        "compiled_effective_writing_context": repair_context.get(
            "compiled_effective_writing_context"
        )
        or {},
        "initial_candidate": repair_context.get("initial_candidate"),
        "repair_task_cards": repair_context.get("repair_task_cards") or [],
        "review_only_guards": repair_context.get("review_only_guards") or [],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是TechnicalWritingAgent的局部返修器。事实许可、写作质量、"
                "占位符和覆盖保护均为硬约束；不得补充未确认事实，不得返回完整章节。"
            ),
        },
        {
            "role": "developer",
            "content": (
                "你必须且只能调用一次submit_structured_patch。不得输出普通final文本，"
                "不得调用其他工具，不得调用save_section_draft。工具参数就是完整Structured "
                "Patch对象；每个任务最多三个候选且只能使用许可的局部操作。"
                "每个repair task默认返回两个实质不同的改写候选：C1为尽量保留合法含义的正常改写，"
                "C2为只保留must_preserve及已许可事实的保守改写。C2不得仅删除数字后保留无来源义务，"
                "也不得用新的报告、文件、服务、人员、组织、资源、产品能力或结果承诺替代。"
                "只有整句可安全删除时才可额外返回C3 delete_sentence；不得机械返回删除候选。"
                "候选只返回candidate_id、candidate_role、operation_type和replacement_text；"
                "不得重复返回original_text、context_before或context_after，锚点由任务卡确定性恢复。"
                "每个repair result优先返回repair_target_id，同一目标句的全部member issues只能返回一组候选；"
                "候选必须同时处理member_issue_codes与offending_spans，不得按issue拆成重叠操作。"
                "replacement_text需要引用术语时使用中文全角引号“”，不要输出未转义的ASCII双引号。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ),
        },
    ]


class _FreshPatchProtocolError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        raw_content: str = "",
        response_type: str | None = None,
        tool_call_count: int | None = None,
        parse_error_type: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.raw_content = raw_content
        self.response_type = response_type
        self.tool_call_count = tool_call_count
        self.parse_error_type = parse_error_type


def _fresh_patch_payload_from_tool_result(
    result: Any,
) -> dict[str, Any]:
    response_type = str(getattr(result, "response_type", "") or "")
    tool_calls = list(getattr(result, "tool_calls", None) or [])
    final_content = str(getattr(result, "final_content", "") or "")
    if response_type != "tool_calls" or not tool_calls:
        code = (
            "fresh_patch_protocol_invalid"
            if '"sections"' in final_content
            or '"chapter_title"' in final_content
            else "fresh_patch_tool_call_missing"
        )
        raise _FreshPatchProtocolError(
            code,
            raw_content=final_content,
            response_type=response_type or "final",
            tool_call_count=len(tool_calls),
        )
    if len(tool_calls) != 1:
        raise _FreshPatchProtocolError(
            "fresh_patch_tool_call_multiple",
            response_type=response_type,
            tool_call_count=len(tool_calls),
        )
    call = tool_calls[0]
    if str(call.get("name") or "") != "submit_structured_patch":
        raise _FreshPatchProtocolError(
            "fresh_patch_tool_name_invalid",
            raw_content=json.dumps(
                call.get("arguments"), ensure_ascii=False, default=str
            ),
            response_type=response_type,
            tool_call_count=1,
        )
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        payload = deepcopy(arguments)
        raw_arguments = json.dumps(
            arguments, ensure_ascii=False, separators=(",", ":")
        )
    elif isinstance(arguments, str):
        raw_arguments = arguments
        try:
            parsed_arguments = parse_model_tool_arguments(
                arguments,
                tool_name="submit_structured_patch",
            )
            payload = parsed_arguments.arguments
            if parsed_arguments.json_syntax_repair.get("applied"):
                call["argument_metadata"] = {
                    **parsed_arguments.json_syntax_repair,
                    "_original_arguments": parsed_arguments.raw_arguments,
                    "_repaired_arguments": parsed_arguments.repaired_arguments,
                }
        except ModelJsonParseError:
            raise _FreshPatchProtocolError(
                "fresh_patch_arguments_invalid_json",
                raw_content=raw_arguments,
                response_type=response_type,
                tool_call_count=1,
                parse_error_type="ModelJsonParseError",
            ) from None
    else:
        raise _FreshPatchProtocolError(
            "fresh_patch_arguments_invalid_json",
            raw_content=str(arguments or ""),
            response_type=response_type,
            tool_call_count=1,
            parse_error_type="ModelJsonParseError",
        )
    if not isinstance(payload, dict):
        raise _FreshPatchProtocolError(
            "fresh_patch_arguments_invalid_json",
            raw_content=raw_arguments,
            response_type=response_type,
            tool_call_count=1,
            parse_error_type="ModelJsonParseError",
        )
    try:
        _fresh_patch_protocol_payload(payload)
    except ValidationError:
        raise _FreshPatchProtocolError(
            "fresh_patch_schema_invalid",
            raw_content=raw_arguments,
            response_type=response_type,
            tool_call_count=1,
            parse_error_type="ValidationError",
        ) from None
    return payload


def _fresh_final_generation_instruction(
    *,
    schema: dict[str, Any],
    target_contract: dict[str, Any] | None = None,
) -> str:
    payload = {"response_schema": schema}
    if target_contract:
        payload["targeted_revision_contract"] = target_contract
    return (
        "上一次返回未能通过本地确定性JSON解析。现在仅使用干净原始上下文、证据快照和"
        "已登记占位符，从零生成一次完整章节；不得调用或请求任何工具，不得引用、修复、"
        "续写或猜测上一次输出，也不得按旧稿修改。逐节generation_permissions是封闭许可；"
        "未列入allowed_*的数字、人员资历、产品能力和占位符不得生成。"
        "返回且只返回符合以下Schema的JSON对象：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _fresh_quality_error_summary(errors: list[str]) -> list[str]:
    categories: list[str] = []
    for error in errors:
        value = str(error)
        parts = value.split(":")
        heading = parts[1] if len(parts) > 2 and parts[0] == "section_quality" else "相关小节"
        if "insufficient_prose" in value or "no_complete_prose" in value:
            categories.append(
                f"{heading}正文有效段落不足，需补充目标、条件、实施方法、控制点、成果和异常处理"
            )
        elif "only_tables_or_lists" in value:
            categories.append(
                f"{heading}只有表格、列表或过渡语，缺少完整技术实施正文"
            )
        elif "ppt_style_short_bullets" in value:
            categories.append(
                f"{heading}存在大量一句话短项目符号，形成PPT式提纲"
            )
        elif (
            "bullet_dominant" in value
            or "list_dominant" in value
            or "bullet_groups_without" in value
            or "consecutive_list" in value
        ):
            categories.append(
                f"{heading}项目符号占比过高或连续堆叠，缺少列表前后的完整实施说明"
            )
        elif "table_" in value or "structured_content" in value:
            categories.append(
                f"{heading}正文密度不足，表格和列表成为主体或缺少用途说明"
            )
        elif "list" in value or "bullet" in value:
            categories.append(f"{heading}项目符号使用不符合正文优先要求")
        elif "numeric_commitment" in value or "service_level" in value:
            categories.append("未确认数字承诺")
        elif "product_capability" in value:
            categories.append("未确认产品能力")
        elif "candidate_promoted" in value:
            categories.append("候选事实被写成确认事实")
        else:
            categories.append(value[:160])
    return list(dict.fromkeys(categories))


def _fresh_quality_error_details(
    errors: list[str],
    *,
    draft: dict[str, Any],
    expected_sections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    section_ids = {
        str(item.get("heading") or ""): str(item.get("section_id") or "")
        for item in draft.get("sections") or []
        if isinstance(item, dict)
    }
    identity_resolution: dict[str, Any] | None = None
    if expected_sections:
        identity_resolution = _resolve_section_identities(
            draft,
            expected_sections=expected_sections,
        )
        for index, section in enumerate(draft.get("sections") or []):
            if not isinstance(section, dict):
                continue
            section_id = str(
                (identity_resolution.get("resolved_by_index") or {}).get(index)
                or ""
            )
            if section_id:
                section_ids[str(section.get("heading") or "")] = section_id
    section_contents = {
        str(item.get("heading") or ""): str(item.get("content") or "")
        for item in draft.get("sections") or []
        if isinstance(item, dict)
    }
    keyword_terms = (
        "高可用", "在线扩展", "多副本", "纠删码", "反亲和", "自动重启",
        "自动恢复", "资源预留", "不中断业务", "业务无感知", "堆叠", "MLAG",
        "VPC", "分布式虚拟交换机", "AD", "备份系统", "容灾系统", "安全设备",
        "资质", "资格", "证书", "经验", "年限", "劳动合同", "社保", "人员投入",
        "整改时限", "响应时限", "工作日", "小时",
    )
    details: list[dict[str, Any]] = []
    for raw_error in errors:
        value = str(raw_error)
        parts = value.split(":", 3)
        section_title = (
            parts[1]
            if len(parts) >= 3
            and parts[0]
            in {
                "fact_boundary",
                "section_quality",
                "generated_supporting_table",
                "required_response_table",
                "placeholder",
            }
            else "相关小节"
        )
        error_code = (
            parts[2]
            if len(parts) >= 3
            and parts[0]
            in {
                "fact_boundary",
                "section_quality",
                "generated_supporting_table",
                "required_response_table",
                "placeholder",
            }
            else _stable_validation_error_code(value)
        )
        payload = parts[3] if len(parts) == 4 else value
        offending_sentence = (
            _table_context_for_fresh_error(
                section_contents.get(section_title, "")
            )
            if error_code == "table_without_intro"
            and re.fullmatch(r"\s*\d+\s*", payload)
            else _locate_exact_offending_sentence(
                section_contents.get(section_title, ""),
                payload,
            )
        )
        offending_span = _exact_offending_span(
            error_code,
            offending_sentence or payload,
        )
        keyword_source = " ".join(
            value
            for value in (offending_span, offending_sentence, payload)
            if value
        )
        keywords = [
            term for term in keyword_terms if term.lower() in keyword_source.lower()
        ]
        keywords.extend(
            re.findall(
                r"\b(?:GB/?T|GB|ISO|IEC)\s*[-:]?\s*\d[\dA-Za-z./:-]*",
                keyword_source,
                flags=re.I,
            )
        )
        keywords.extend(
            re.findall(
                r"(?:\d+\s*[×xX*]\s*\d+|\d+)\s*"
                r"(?:个)?(?:工作日|小时|分钟|人|名|年|个月|日|周|月|天)",
                keyword_source,
            )
        )
        fact_type, instruction = _fresh_fact_boundary(
            error_code,
            keyword_source=payload,
        )
        repair_type, required_action, must_not_assert = (
            _fresh_repair_template(
                error_code,
                offending_sentence=offending_sentence,
                offending_span=offending_span,
            )
        )
        disposition = _repair_disposition_for_issue(
            {
                "severity": "blocking",
                "category": parts[0] if parts else "validation",
                "code": error_code,
            }
        )
        violation = {
            "offending_sentence": offending_sentence or None,
            "offending_span": offending_span or None,
            "must_not_assert": must_not_assert,
        }
        canonical_section_id = section_ids.get(section_title) or None
        if identity_resolution is not None:
            canonical_section_id = (
                _canonical_issue_section_id(
                    {
                        "section_id": None,
                        "section_title": section_title,
                        "offending_sentence": offending_sentence,
                        "offending_span": offending_span,
                    },
                    draft=draft,
                    identity_resolution=identity_resolution,
                )
                or None
            )
        details.append(
            {
                "section_id": canonical_section_id,
                "section_title": section_title,
                "error_code": error_code,
                "issue_code": error_code,
                "offending_fact_type": fact_type,
                "boundary_instruction": instruction,
                "repair_disposition": disposition,
                "repair_type": repair_type,
                "offending_sentence": offending_sentence or None,
                "offending_span": offending_span or None,
                "required_action": required_action,
                "must_not_assert": must_not_assert,
                "keywords": list(dict.fromkeys(keywords))[:16],
                "violations": [violation],
            }
        )
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in details:
        key = (
            str(item.get("section_id") or item.get("section_title") or ""),
            str(item.get("error_code") or ""),
            str(item.get("offending_fact_type") or ""),
        )
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = item
            continue
        existing["keywords"] = list(
            dict.fromkeys(
                [
                    *list(existing.get("keywords") or []),
                    *list(item.get("keywords") or []),
                ]
            )
        )[:16]
        existing["violations"] = [
            *list(existing.get("violations") or []),
            *list(item.get("violations") or []),
        ]
    return list(grouped.values())


def _stable_validation_error_code(message: str) -> str:
    value = str(message or "")
    if value.startswith(
        "required project facts are unconfirmed but no pending confirmation"
    ):
        return "required_project_fact_pending_confirmation_missing"
    mappings = (
        ("invalid placeholder IDs", "invalid_placeholder_id"),
        ("unregistered placeholder IDs", "unregistered_placeholder_id"),
        ("required external verification was not attempted", "required_verification_not_attempted"),
        ("external verification remains pending", "external_verification_pending"),
    )
    for prefix, code in mappings:
        if value.startswith(prefix):
            return code
    fallback = value.split(":", 1)[0]
    normalized = re.sub(r"[^a-z0-9]+", "_", fallback.casefold()).strip("_")
    return normalized or "validation_error"


def _locate_exact_offending_sentence(content: str, payload: str) -> str:
    value = re.sub(r"\s+", " ", str(payload or "")).strip()
    if not value:
        return ""
    normalized_payload = re.sub(r"[\W_]+", "", value, flags=re.UNICODE)
    for sentence in re.split(r"(?<=[。！？!?；;])|\n+", str(content or "")):
        candidate = re.sub(r"\s+", " ", sentence).strip()
        if not candidate:
            continue
        normalized_candidate = re.sub(
            r"[\W_]+", "", candidate, flags=re.UNICODE
        )
        if (
            normalized_payload
            and normalized_payload in normalized_candidate
        ):
            return _strip_diagnostic_sentinels(candidate)
    return _strip_diagnostic_sentinels(value[:220])


def _table_context_for_fresh_error(content: str) -> str:
    lines = str(content or "").replace("\r\n", "\n").splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*\|.*\|\s*$", line):
            continue
        table_lines = [line.strip()]
        for following in lines[index + 1 : index + 3]:
            if re.match(r"^\s*\|.*\|\s*$", following):
                table_lines.append(following.strip())
        return "Markdown表格上下文：" + " ".join(table_lines)[:180]
    return "Markdown表格缺少对应的完整引导句"


def _registered_table_introductions(draft: dict[str, Any]) -> list[dict[str, Any]]:
    introductions: list[dict[str, Any]] = []
    intro_pattern = re.compile(
        r"(?:下表|下述表格?|见下表|见表[一二三四五六七八九十\d]+|"
        r"表[一二三四五六七八九十\d]+[^。！？；;\n]{0,20}(?:说明|列明|展示)|"
        r"如下表所示|具体安排如下)"
    )
    for section in draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        blocks = [
            value.strip()
            for value in re.split(
                r"\n\s*\n+",
                str(section.get("content") or "").replace("\r\n", "\n"),
            )
            if value.strip()
        ]
        for index, block in enumerate(blocks):
            if not re.match(r"^\s*\|.*\|", block):
                continue
            for previous in reversed(blocks[:index]):
                if re.fullmatch(
                    r"表[一二三四五六七八九十\d]+[^\n]{1,60}",
                    previous,
                ) and not intro_pattern.search(previous):
                    continue
                if intro_pattern.search(previous):
                    introductions.append(
                        {
                            "section_id": section.get("section_id"),
                            "section_title": section.get("heading"),
                            "text": previous[:220],
                        }
                    )
                break
    return introductions


def _strip_diagnostic_sentinels(value: str) -> str:
    return re.sub(
        r"\b(?:FAILED|SENTINEL|DRAFT-SENTINEL)-[A-Z0-9-]{4,}\b\s*",
        "",
        str(value or ""),
        flags=re.I,
    ).strip()


def _exact_offending_span(error_code: str, text: str) -> str:
    value = str(text or "")
    if error_code in {
        "required_registered_placeholder_missing",
        "blocking_placeholder_missing_from_content",
    }:
        match = re.search(r"PH-[A-Z0-9-]+", value)
        return match.group(0) if match else ""
    if error_code == "schedule_frequency":
        match = re.search(
            r"(?:每日|每周|每月|每季度|每年|每个工作日|"
            r"每\s*(?:\d+|[一二三四五六七八九十]+)\s*(?:日|天|周|月|季度|年))",
            value,
        )
        return match.group(0) if match else ""
    if error_code in {"numeric_commitment", "service_level_commitment"}:
        match = re.search(
            r"(?:提前|前)?\s*(?:\d+\s*[×xX*]\s*\d+|\d+|"
            r"[一二三四五六七八九十百]+)\s*(?:个)?"
            r"(?:工作日|小时|分钟|人|名|人天|年|个月|日|周|月|天)"
            r"(?:内|前|完成|响应|通知|到场|驻场|试运行|投入)?",
            value,
        )
        return re.sub(r"\s+", "", match.group(0)) if match else ""
    if error_code == "unverified_standard_reference":
        match = re.search(
            r"\b(?:GB/?T|GB|ISO|IEC)\s*[-:]?\s*\d[\dA-Za-z./:-]*",
            value,
            re.I,
        )
        return match.group(0) if match else ""
    if error_code == "unconfirmed_personnel_qualification":
        match = re.search(
            r"(?:不少于|至少|具备|具有|持有|取得|均持有|必须持有)"
            r"[^，。；;]{0,45}(?:年|个同类项目|证书|认证|资质|资格|经验)",
            value,
        )
        return match.group(0) if match else ""
    if error_code == "candidate_promoted_to_fact":
        match = re.search(
            r"(?:我方|我公司|本公司)[^，。；;]{0,50}"
            r"(?:已通过|已取得|已获得|具备|持有)[^，。；;]{0,50}",
            value,
        )
        return match.group(0) if match else ""
    if error_code == "unconfirmed_product_capability":
        match = re.search(
            r"(?:本系统|本方案|所投产品|确保|保证)[^。！？；;]{0,90}",
            value,
        )
        return match.group(0) if match else ""
    return ""


def _fresh_repair_template(
    error_code: str,
    *,
    offending_sentence: str,
    offending_span: str,
) -> tuple[str, str, list[str]]:
    if error_code == "required_project_fact_pending_confirmation_missing":
        return (
            "preserve_for_human_confirmation",
            "保留为人工确认事项，不得由Fresh补充或改写为确认事实。",
            [],
        )
    if error_code in {"numeric_commitment", "service_level_commitment", "schedule_frequency"}:
        return (
            "remove_unconfirmed_numeric_commitment",
            f"删除未经确认的{offending_span or '数字时限'}，改为及时处理或按合同及确认后的计划、响应机制执行；不得改动有source ID的招标原值。",
            [offending_span] if offending_span else [],
        )
    if error_code in {
        "unconfirmed_personnel_qualification",
        "personnel_narrative_without_source",
    }:
        return (
            "remove_unconfirmed_personnel_qualification",
            "删除未确认的资历、年限、证书、数量、具体人员身份和资源安排；"
            "保留不断言具体人员事实的中性项目角色动作、招标原表、注册占位符或confirmed事实。",
            [offending_span] if offending_span else [],
        )
    if error_code in {
        "unconfirmed_staffing_assignment",
        "unconfirmed_resource_commitment",
    }:
        return (
            "delete_unconfirmed_staffing_or_resource_claim",
            "删除无来源的双人复核、备份人员、人员替换、公司后备资源或调配承诺；"
            "不得用另一种人员或资源安排替换。",
            [offending_sentence] if offending_sentence else [],
        )
    if error_code == "unconfirmed_product_capability":
        return (
            "convert_capability_to_requirement_or_verification",
            "改为依据招标要求和确认后的产品资料进行配置，并通过测试验证是否满足相关要求；不得替换成另一种未确认能力。",
            [
                value
                for value in (
                    offending_span,
                    "自动故障切换",
                    "业务不中断",
                )
                if value
            ],
        )
    if error_code == "candidate_promoted_to_fact":
        return (
            "remove_candidate_fact_confirmation",
            "删除候选材料的确定性事实表述，改为不依赖候选证书或候选人员事实的一般流程；候选材料仅保留在人工确认清单。",
            [offending_span or offending_sentence] if (offending_span or offending_sentence) else [],
        )
    if error_code == "unverified_standard_reference":
        return (
            "generalize_unverified_standard_reference",
            "删除未经确认的具体标准编号，改为依据适用制度、合同约定及相关规范执行；招标原文或确认材料中的标准不得删除。",
            [offending_span] if offending_span else [],
        )
    if error_code == "table_without_intro":
        return (
            "add_table_intro",
            "仅在表格前增加说明该表用途和内容的完整正文段落，不改写本节事实。",
            [],
        )
    if error_code == "title_not_matched":
        return (
            "use_registered_table_title",
            "仅将显式表题改为generated_supporting_tables.title登记的准确标题，不改写表格事实内容。",
            [],
        )
    if error_code in {
        "required_registered_placeholder_missing",
        "blocking_placeholder_missing_from_content",
    }:
        return (
            "reinsert_registered_placeholder",
            "在要求的小节重新插入准确的已注册占位符，只能写占位符token，不得填写或推断真实值。",
            [],
        )
    if error_code in {"unregistered_placeholder", "unregistered_placeholder_id", "placeholder"}:
        return (
            "delete_unregistered_placeholder",
            "删除模型自行创造且未在allowed_placeholders注册的token；不得填值、改造或创建新占位符。"
            "如需保留流程说明，仅写按招标要求、合同约定及确认后的实施计划执行。",
            [offending_span or offending_sentence]
            if (offending_span or offending_sentence)
            else [],
        )
    return (
        "rewrite_without_new_facts",
        "仅通过结构、格式或语言改写解决该问题，不得新增数字、人员资历、产品能力、认证、公司资源或履约承诺。",
        [],
    )


def _fresh_regeneration_repair_targets(errors: list[str]) -> list[str]:
    """Return prose/fact/quality failures that a clean regeneration can repair.

    Full candidate validation remains authoritative and is reported separately.
    Workflow-state failures such as missing pending confirmations or malformed
    placeholder registration must not spend the single fresh generation attempt:
    the model cannot safely repair those states from prose alone.
    """
    repairable_prefixes = (
        "fact_boundary:",
        "placeholder:",
        "section_quality:",
        "chapter_quality:",
        "generated_supporting_table:",
        "required_response_table:",
        "invalid Markdown table:",
        "required tender table was not reused:",
        "complex tender table was simplified in Markdown:",
        "chapter title changed",
        "section headings or order differ",
        "target response points are not covered",
        "target score items are not covered",
        "confirmed enterprise claims require confirmed evidence",
        "tender minimum/delivery requirement was converted into an unconfirmed commitment",
    )
    return [
        str(error)
        for error in errors
        if str(error).startswith(repairable_prefixes)
    ]


def _fresh_fact_boundary(
    error_code: str,
    *,
    keyword_source: str,
) -> tuple[str, str]:
    if error_code == "required_project_fact_pending_confirmation_missing":
        return (
            "required_project_fact",
            "该事实缺少真实项目确认，只能保留为人工确认事项；不得由Fresh补充、推断或改写为确认事实。",
        )
    if error_code == "numeric_commitment":
        if re.search(
            r"(?:项目经理|架构师|工程师|人员|团队|投入|驻场)"
            r"[^。！？；;\n]{0,24}(?:\d+|[一二三四五六七八九十]+)"
            r"\s*(?:人|名)",
            keyword_source,
        ) or re.search(r"(?:\d+|[一二三四五六七八九十]+)\s*(?:人|名)", keyword_source):
            return (
                "staffing_count",
                "只描述岗位、职责和协作关系，不得自行填写项目经理、架构师、工程师或其他人员数量；使用已登记人数占位符或等待确认。",
            )
        if re.search(
            r"(?:7\s*[×xX*]\s*24|响应|到场|故障|维修|保修|服务周期)",
            keyword_source,
        ):
            return (
                "service_level",
                "只有数值与单位完全等同于有source_ids的招标供应商服务承诺要求时才可引用；更短响应时间、更高等级或额外结果承诺必须阻断或待确认。",
            )
        if re.search(
            r"(?:整改|交接|验收申请|资料提交|移交)",
            keyword_source,
        ):
            return (
                "handover_or_rectification_deadline",
                "整改、交接、验收申请和资料提交时限必须有确认来源；否则删除数字、使用已登记占位符或按合同及采购人确认要求执行。",
            )
        return (
            "schedule_duration",
            "未确认的周、日、小时、提前量和阶段完成时限应删除数字、使用已登记占位符，或写明按合同及确认计划执行。",
        )
    if error_code in {"unconfirmed_product_capability"}:
        return (
            "product_capability",
            "只能描述招标采购要求、测试核验目标，或明确写明以确认后的技术应答表、产品配置或制造商资料为准；不得改写为所投系统的确定能力。",
        )
    if error_code in {"candidate_promoted_to_fact"}:
        return (
            "candidate_personnel_or_enterprise_fact",
            "候选企业资质、业绩和人员材料不得进入确定正文；姓名占位符不能覆盖资质、证书、经验年限、劳动合同、社保或投入承诺，必须登记独立待确认项或删除。",
        )
    if error_code == "unconfirmed_personnel_qualification":
        return (
            "personnel_qualification",
            "只能使用generation_permissions.allowed_confirmed_personnel_facts中的年限、证书、项目数量和岗位；"
            "删除未确认的人员事实但保留不涉及具体人选、资历、数量和资源的中性项目角色动作。",
        )
    if error_code == "personnel_narrative_without_source":
        return (
            "personnel_narrative_without_source",
            "本节没有招标或确认人员来源；删除具体人选、资历、数量、组织事实和资源安排，"
            "保留中性项目角色动作、招标原表、填报结构和allowed_placeholders。",
        )
    if error_code == "unconfirmed_resource_commitment":
        return (
            "resource_commitment",
            "不得自行承诺全职投入、全程投入、候补人员、替换机制或公司资源池调配；没有确认来源时删除相关句子，不得改写为通用岗位职责。",
        )
    if error_code == "unregistered_placeholder":
        return (
            "placeholder",
            "正文只能使用generation_permissions.allowed_placeholders中的已登记占位符；删除或改用已许可占位符。",
        )
    if error_code == "confirmed_personnel_fact_mismatch":
        return (
            "personnel_fact_mismatch",
            "人员工作年限、同类项目数量、证书名称或有效期必须与confirmed fact或confirmed evidence ID完全一致；不得估算、扩大或混用候选材料。",
        )
    if error_code == "positive_deviation_pending_confirmation":
        return (
            "positive_deviation",
            "优于招标基准的到货、安装、保修或服务承诺会形成额外履约责任；保留招标基准并登记用户确认，确认前不得写成最终承诺。",
        )
    if error_code in {
        "negative_deviation",
        "negative_deviation_marked_no_deviation",
    }:
        return (
            "negative_deviation",
            "确认值劣于招标要求时必须显著标记负偏离、关联招标条款并阻断提交，不得改写或标记为无偏离。",
        )
    if error_code == "fixed_requirement_difference_requires_review":
        return (
            "fixed_requirement_difference",
            "固定型要求只能同值响应；任何不同内容必须保留差异并交由人工判断，不得自动判定正偏离或无偏离。",
        )
    if error_code == "evidence_pending":
        return (
            "objective_evidence_pending",
            "无偏离响应意向不能替代产品、人员、资质、证书、业绩或参数证据；正文只能写待核验响应或使用已登记占位符。",
        )
    if error_code == "service_level_commitment":
        return (
            "service_level",
            "只有数值与单位完全等同于有source_ids的招标供应商服务承诺要求时才可引用；更短响应时间、更高等级或额外结果承诺必须阻断或待确认。",
        )
    if error_code == "unconfirmed_bidder_commitment":
        return (
            "unconfirmed_commitment",
            "不得新增整改、响应、交付或绝对结果承诺；只能按合同约定、招标要求及采购人确认的边界表述。",
        )
    if error_code == "unverified_standard_reference":
        return (
            "unverified_external_standard",
            "删除未核验标准编号、改写为一般性依据，或使用待核验外部来源占位符；只有招标原文或已确认材料中的编号可以保留。",
        )
    return (
        "writing_quality",
        "按当前正式正文规则修正该小节，不得用表格、列表、过渡语或空泛套话替代完整实施说明。",
    )


def _candidate_sha256(draft: dict[str, Any]) -> str:
    canonical = json.dumps(
        draft,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_patch_anchor(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    for index, raw_character in enumerate(str(value or "")):
        for character in unicodedata.normalize("NFKC", raw_character):
            if re.fullmatch(r"[\s，,。.!！?？：:；;、（）()\[\]{}<>《》\-—*_`#]", character):
                continue
            normalized.append(character)
            positions.append(index)
    return "".join(normalized), positions


def _all_substring_positions(value: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while needle and (position := value.find(needle, start)) >= 0:
        positions.append(position)
        start = position + 1
    return positions


def _locate_fresh_patch_anchor(
    content: str,
    original_text: str,
    *,
    context_before: str = "",
    context_after: str = "",
) -> tuple[tuple[int, int] | None, str | None]:
    exact_positions = _all_substring_positions(content, original_text)
    ranges = [(position, position + len(original_text)) for position in exact_positions]
    if not ranges:
        normalized_content, content_positions = _normalize_patch_anchor(content)
        normalized_original, _ = _normalize_patch_anchor(original_text)
        for position in _all_substring_positions(
            normalized_content, normalized_original
        ):
            if not normalized_original or not content_positions:
                continue
            start = content_positions[position]
            end = content_positions[position + len(normalized_original) - 1] + 1
            ranges.append((start, end))
    ranges = list(dict.fromkeys(ranges))
    if not ranges:
        return None, "fresh_patch_anchor_not_found"
    if len(ranges) > 1 and (context_before or context_after):
        matched: list[tuple[int, int]] = []
        before_key = _normalize_diff_text(context_before)
        after_key = _normalize_diff_text(context_after)
        for start, end in ranges:
            before_window = _normalize_diff_text(content[max(0, start - 400) : start])
            after_window = _normalize_diff_text(content[end : end + 400])
            if before_key and not before_window.endswith(before_key):
                continue
            if after_key and not after_window.startswith(after_key):
                continue
            matched.append((start, end))
        if matched:
            ranges = matched
    if len(ranges) != 1:
        return None, "fresh_patch_anchor_ambiguous"
    return ranges[0], None


def _apply_fresh_patch_operations(
    initial: dict[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    result = deepcopy(initial)
    by_section: dict[int, list[dict[str, Any]]] = {}
    for operation in operations:
        by_section.setdefault(int(operation["section_index"]), []).append(operation)
    for section_index, section_operations in by_section.items():
        content = str(result["sections"][section_index].get("content") or "")
        for operation in sorted(
            section_operations, key=lambda item: int(item["start"]), reverse=True
        ):
            start = int(operation["start"])
            end = int(operation["end"])
            content = content[:start] + str(operation.get("replacement_text") or "") + content[end:]
        result["sections"][section_index]["content"] = content
        if any(
            set(operation.get("member_issue_codes") or [])
            & {
                "required_registered_placeholder_missing",
                "blocking_placeholder_missing_from_content",
            }
            for operation in section_operations
        ):
            content_placeholder_ids = extract_placeholder_ids(content)
            result["sections"][section_index]["placeholder_ids"] = _unique_strings(
                [
                    *list(
                        result["sections"][section_index].get("placeholder_ids")
                        or []
                    ),
                    *content_placeholder_ids,
                ]
            )
            result["placeholders_used"] = _unique_strings(
                [
                    *list(result.get("placeholders_used") or []),
                    *content_placeholder_ids,
                ]
            )
    return result


def _fresh_patch_operations_overlap(
    candidate: dict[str, Any], selected: list[dict[str, Any]]
) -> bool:
    return any(
        int(candidate["section_index"]) == int(item["section_index"])
        and int(candidate["start"]) < int(item["end"])
        and int(item["start"]) < int(candidate["end"])
        for item in selected
    )


def _patch_validation_regression_codes(
    before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []
    before_coverage = before.get("coverage") or {}
    after_coverage = after.get("coverage") or {}
    if bool(before.get("schema_valid")) and not bool(after.get("schema_valid")):
        reasons.append("fresh_patch_schema_regressed")
    for key in ("missing_response_point_count", "missing_score_item_count"):
        if int(after_coverage.get(key) or 0) > int(before_coverage.get(key) or 0):
            reasons.append(f"fresh_patch_{key}_increased")
    for key in ("covered_response_point_count", "covered_score_item_count"):
        if int(after_coverage.get(key) or 0) < int(before_coverage.get(key) or 0):
            reasons.append(f"fresh_patch_{key}_decreased")
    before_categories = {
        tuple(value) for value in before.get("blocking_error_categories") or []
    }
    after_categories = {
        tuple(value) for value in after.get("blocking_error_categories") or []
    }
    if after_categories - before_categories:
        reasons.append("fresh_patch_added_blocking_category")
    before_quality = before.get("quality_comparison") or {}
    after_quality = after.get("quality_comparison") or {}
    for key in (
        "insufficient_section_count",
        "list_dominant_section_count",
        "inline_step_chain_count",
        "markdown_table_error_count",
        "quality_error_count",
    ):
        if float(after_quality.get(key) or 0) > float(before_quality.get(key) or 0):
            reasons.append(f"fresh_patch_{key}_regressed")
    return list(dict.fromkeys(reasons))


def _validation_issue_code_counts(validation: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    details = list(validation.get("error_details") or [])
    if details:
        for item in details:
            if not isinstance(item, dict):
                continue
            code = str(item.get("issue_code") or item.get("error_code") or item.get("code") or "")
            if code:
                counts[code] = counts.get(code, 0) + 1
        return counts
    errors = list(validation.get("errors") or [])
    if errors:
        for value in errors:
            parts = str(value).split(":")
            code = parts[2] if len(parts) >= 3 else str(value)
            if code:
                counts[code] = counts.get(code, 0) + 1
        return counts
    for value in validation.get("blocking_error_categories") or []:
        if isinstance(value, (list, tuple)) and value:
            code = str(value[0] or "")
            if code:
                counts[code] = counts.get(code, 0) + 1
    return counts


def _patch_quality_audit(
    *,
    before_draft: dict[str, Any],
    after_draft: dict[str, Any],
    section_index: int,
    section_quality_requirements: dict[str, Any],
    before_validation: dict[str, Any],
    after_validation: dict[str, Any],
    placeholder_lost: bool = False,
) -> dict[str, Any]:
    before_section = (before_draft.get("sections") or [])[section_index]
    after_section = (after_draft.get("sections") or [])[section_index]
    before_metrics = inspect_section_composition(before_section)
    after_metrics = inspect_section_composition(after_section)
    minimum_characters = int(
        section_quality_requirements.get("minimum_prose_characters") or 0
    )
    minimum_paragraphs = int(
        section_quality_requirements.get("minimum_paragraph_count") or 0
    )

    def meets_floor(metrics: dict[str, Any]) -> bool:
        return (
            int(metrics.get("prose_character_count") or 0) >= minimum_characters
            and int(metrics.get("prose_paragraph_count") or 0) >= minimum_paragraphs
        )

    before_codes = set(_validation_issue_code_counts(before_validation))
    after_codes = set(_validation_issue_code_counts(after_validation))
    quality_codes = {
        "insufficient_prose",
        "prose_ratio_too_low",
        "structured_content_exceeds_prose",
        "table_without_intro",
        "table_without_followup",
        "bullet_groups_without_intro",
        "bullet_groups_without_followup",
        "no_complete_prose",
        "only_tables_or_lists",
    }
    def metric_error_codes(metrics: dict[str, Any]) -> set[str]:
        return {
            parts[2]
            for value in metrics.get("errors") or []
            for parts in [str(value).split(":")]
            if len(parts) >= 3
        }

    new_quality_code_set = (
        ((after_codes - before_codes) & quality_codes)
        | (
            metric_error_codes(after_metrics)
            - metric_error_codes(before_metrics)
        )
        & quality_codes
    )
    # Moving from no usable prose to some prose that is still below the same
    # floor is not a newly introduced regression; the section was already
    # quality-blocked and has improved without crossing a floor downward.
    if (
        "no_complete_prose" in metric_error_codes(before_metrics)
        and "insufficient_prose" in new_quality_code_set
        and int(after_metrics.get("prose_character_count") or 0)
        > int(before_metrics.get("prose_character_count") or 0)
    ):
        new_quality_code_set.discard("insufficient_prose")
    new_quality_codes = sorted(new_quality_code_set)
    before_coverage = before_validation.get("coverage") or {}
    after_coverage = after_validation.get("coverage") or {}
    coverage_regressed = any(
        int(after_coverage.get(key) or 0) > int(before_coverage.get(key) or 0)
        for key in ("missing_response_point_count", "missing_score_item_count")
    ) or any(
        int(after_coverage.get(key) or 0) < int(before_coverage.get(key) or 0)
        for key in ("covered_response_point_count", "covered_score_item_count")
    )
    structure_removed = bool(str(before_section.get("content") or "").strip()) and not bool(
        str(after_section.get("content") or "").strip()
    )
    floor_crossed = (
        meets_floor(before_metrics) and not meets_floor(after_metrics)
    ) or structure_removed
    before_characters = int(before_metrics.get("prose_character_count") or 0)
    after_characters = int(after_metrics.get("prose_character_count") or 0)
    absolute_change = after_characters - before_characters
    change_ratio = (
        absolute_change / before_characters if before_characters else 0.0
    )
    length_warning_code = (
        "fresh_patch_prose_reduction_review"
        if -0.30 <= change_ratio < -0.15
        else None
    )
    return {
        "before_prose_characters": before_characters,
        "after_prose_characters": after_characters,
        "change_ratio": round(change_ratio, 6),
        "absolute_change": absolute_change,
        "before_prose_paragraphs": int(before_metrics.get("prose_paragraph_count") or 0),
        "after_prose_paragraphs": int(after_metrics.get("prose_paragraph_count") or 0),
        "quality_floor_crossed": bool(floor_crossed),
        "new_quality_issue_codes": new_quality_codes,
        "coverage_regressed": bool(coverage_regressed),
        "placeholder_lost": bool(placeholder_lost),
        "length_warning_code": length_warning_code,
    }


def _new_blocking_issue_codes(
    before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    before_categories = {
        tuple(value) for value in before.get("blocking_error_categories") or []
    }
    after_categories = {
        tuple(value) for value in after.get("blocking_error_categories") or []
    }
    return sorted(
        {
            str(value[0])
            for value in after_categories - before_categories
            if value and value[0]
        }
    )


def _resolved_member_issue_fingerprints(
    card: dict[str, Any],
    *,
    before_counts: dict[str, int],
    after_counts: dict[str, int],
) -> list[str]:
    members = [
        item for item in card.get("member_issues") or [] if isinstance(item, dict)
    ]
    remaining_resolution_capacity = {
        code: max(before_counts.get(code, 0) - after_counts.get(code, 0), 0)
        for code in {
            str(item.get("issue_code") or "") for item in members
            if item.get("issue_code")
        }
    }
    resolved: list[str] = []
    for member in members:
        fingerprint = str(member.get("issue_fingerprint") or "")
        code = str(member.get("issue_code") or "")
        if not fingerprint or not code:
            continue
        if before_counts.get(code, 0) > 0 and after_counts.get(code, 0) == 0:
            resolved.append(fingerprint)
            continue
        if remaining_resolution_capacity.get(code, 0) > 0:
            resolved.append(fingerprint)
            remaining_resolution_capacity[code] -= 1
    return list(dict.fromkeys(resolved))


def _fresh_patch_protocol_payload(value: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(_fresh_patch_response_schema()).validate(value)
    result_keys = [
        str(item.get("repair_target_id") or item.get("issue_fingerprint") or "")
        for item in value.get("repair_results") or []
        if isinstance(item, dict)
    ]
    if len(result_keys) != len(set(result_keys)):
        raise ValidationError(
            "repair_results repair target values must be unique"
        )
    return value


def _fresh_patch_report_template(
    *, base_candidate_hash: str, repair_task_count: int
) -> dict[str, Any]:
    return {
        "fresh_repair_mode": "multi_candidate_structured_patch",
        "base_candidate_hash": base_candidate_hash,
        "repair_task_count": repair_task_count,
        "repair_target_group_count": repair_task_count,
        "multi_issue_target_group_count": 0,
        "issues_resolved_by_shared_operation_count": 0,
        "operation_overlap_avoided_count": 0,
        "repair_candidate_count": 0,
        "tasks_with_one_candidate": 0,
        "tasks_with_two_candidates": 0,
        "tasks_with_three_candidates": 0,
        "tasks_missing_conservative_candidate": 0,
        "duplicate_candidate_count": 0,
        "exact_locator_patch_count": 0,
        "local_guarded_delete_candidate_count": 0,
        "local_guarded_delete_applied_count": 0,
        "applied_operation_count": 0,
        "rejected_candidate_count": 0,
        "unresolved_repair_task_count": repair_task_count,
        "applied_operations": [],
        "unresolved_issue_fingerprints": [],
        "issue_results": [],
        "repair_target_groups": [],
        "protocol_issue_code": None,
        "model_response_parse_failed": False,
        "parse_error_type": None,
        "response_type": None,
        "tool_call_count": None,
        "forced_tool_name": "submit_structured_patch",
        "protocol_validated": False,
        "json_syntax_repair_applied": False,
        "json_syntax_repair": {
            "attempted": False,
            "applied": False,
            "repair_type": None,
            "repair_count": 0,
            "repair_positions": [],
            "repaired_response_path": None,
        },
        "raw_response_saved": False,
        "raw_response_path": None,
        "raw_response_sha256": None,
        "raw_response_character_count": 0,
        "suspected_truncation": False,
        "safe_error_summary": None,
    }


def _fresh_patch_protocol_failure_report(
    *,
    current: dict[str, Any],
    repair_context: dict[str, Any],
    code: str,
    response_type: str | None,
    tool_call_count: int | None,
    parse_error_type: str | None,
    raw_content: str,
    debug: dict[str, Any],
    suspected_truncation: bool,
    safe_error_summary: str,
) -> dict[str, Any]:
    report = deepcopy(current)
    task_cards = [
        item
        for item in repair_context.get("repair_task_cards") or []
        if isinstance(item, dict) and item.get("issue_fingerprint")
    ]
    fingerprints = [str(item.get("issue_fingerprint") or "") for item in task_cards]
    report.update(
        {
            "protocol_issue_code": code,
            "model_response_parse_failed": code
            in {
                "fresh_patch_arguments_invalid_json",
                "fresh_patch_schema_invalid",
                "fresh_patch_protocol_invalid",
            },
            "parse_error_type": parse_error_type,
            "response_type": response_type,
            "tool_call_count": tool_call_count,
            "forced_tool_name": "submit_structured_patch",
            "protocol_validated": False,
            "raw_response_saved": bool(debug.get("saved")),
            "raw_response_path": debug.get("path"),
            "raw_response_sha256": debug.get("sha256"),
            "raw_response_character_count": len(str(raw_content or "")),
            "suspected_truncation": bool(suspected_truncation),
            "safe_error_summary": str(safe_error_summary or "")[:500] or None,
            "repair_candidate_count": 0,
            "applied_operation_count": 0,
            "rejected_candidate_count": 0,
            "unresolved_repair_task_count": len(fingerprints),
            "unresolved_issue_fingerprints": fingerprints,
            "applied_operations": [],
            "issue_results": [
                {
                    "issue_fingerprint": str(card.get("issue_fingerprint") or ""),
                    "section_id": str(card.get("section_id") or "") or None,
                    "section_title": str(card.get("section_title") or "") or None,
                    "issue_code": str(card.get("issue_code") or "") or None,
                    "status": "not_attempted",
                    "reason_codes": [code],
                    "candidate_count": 0,
                    "candidate_set_complete": False,
                    "missing_candidate_roles": ["primary", "conservative"],
                    "diagnostic_codes": [],
                    "candidate_attempts": [],
                    "selected_candidate_id": None,
                }
                for card in task_cards
            ],
        }
    )
    return report


def _draft_placeholder_ids(draft: dict[str, Any]) -> set[str]:
    values = set(_string_list(draft.get("placeholders_used")))
    for section in draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        values.update(_string_list(section.get("placeholder_ids")))
        values.update(extract_placeholder_ids(str(section.get("content") or "")))
    return {str(value) for value in values if value}


def _draft_content_placeholder_ids(draft: dict[str, Any]) -> set[str]:
    return {
        str(value)
        for section in draft.get("sections") or []
        if isinstance(section, dict)
        for value in extract_placeholder_ids(str(section.get("content") or ""))
        if value
    }


def _patch_replacement_fact_findings(
    *,
    initial: dict[str, Any],
    section_index: int,
    section_id: str,
    replacement_text: str,
    fact_ledger: list[dict[str, Any]],
) -> list[str]:
    if not replacement_text.strip():
        return []
    section = deepcopy((initial.get("sections") or [])[section_index])
    section["section_id"] = section_id
    section["content"] = replacement_text
    audit = audit_generation_permissions(
        {"sections": [section], "claims": []},
        fact_ledger=fact_ledger,
    )
    findings: list[dict[str, Any]] = []
    for group in (
        "unauthorized_numeric_claims",
        "unauthorized_personnel_claims",
        "unauthorized_product_capabilities",
        "candidate_facts_promoted",
        "unauthorized_bidder_commitments",
        "unregistered_placeholders",
    ):
        for finding in audit.get(group) or []:
            if not isinstance(finding, dict):
                continue
            code = str(finding.get("code") or group)
            sentence = str(
                finding.get("offending_sentence")
                or finding.get("full_sentence")
                or finding.get("text")
                or replacement_text
            )
            span = str(finding.get("offending_span") or "")
            if span and not _normalized_span_is_within_sentence(span, sentence):
                span = ""
            if not span and sentence:
                span = sentence
            findings.append(
                {
                    "section_id": section_id,
                    "section_title": str(section.get("heading") or "") or None,
                    "issue_code": code,
                    "offending_sentence": sentence,
                    "offending_span": span,
                }
            )
    return _unique_json_items(findings)


def _patch_replacement_fact_codes(
    *,
    initial: dict[str, Any],
    section_index: int,
    section_id: str,
    replacement_text: str,
    fact_ledger: list[dict[str, Any]],
) -> list[str]:
    return list(
        dict.fromkeys(
            str(item.get("issue_code") or "")
            for item in _patch_replacement_fact_findings(
                initial=initial,
                section_index=section_index,
                section_id=section_id,
                replacement_text=replacement_text,
                fact_ledger=fact_ledger,
            )
            if item.get("issue_code")
        )
    )


def _fresh_patch_candidate_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(candidate.get("operation_type") or ""),
        _normalize_diff_text(str(candidate.get("original_text") or "")),
        _normalize_diff_text(str(candidate.get("replacement_text") or "")),
    )


def _restore_fresh_patch_candidate(
    card: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    restored = deepcopy(candidate)
    target = dict(card.get("target") or {})
    operation_type = str(restored.get("operation_type") or "")
    restored["original_text"] = str(
        target.get("offending_span")
        if operation_type == "replace_span"
        else target.get("offending_sentence")
        or ""
    )
    restored["context_before"] = str(target.get("context_before") or "")
    restored["context_after"] = str(target.get("context_after") or "")
    if operation_type == "delete_sentence":
        restored["replacement_text"] = ""
    return restored


def _fresh_patch_candidates_equivalent(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    left_key = _fresh_patch_candidate_key(left)
    right_key = _fresh_patch_candidate_key(right)
    if left_key[:2] != right_key[:2]:
        return False
    left_text, right_text = left_key[2], right_key[2]
    if left_text == right_text:
        return True
    similarity_left = _normalize_diff_text(
        PLACEHOLDER_TOKEN_PATTERN.sub(
            "", str(left.get("replacement_text") or "")
        )
    )
    similarity_right = _normalize_diff_text(
        PLACEHOLDER_TOKEN_PATTERN.sub(
            "", str(right.get("replacement_text") or "")
        )
    )
    if similarity_left and sorted(similarity_left) == sorted(similarity_right):
        return True
    return bool(
        similarity_left
        and similarity_right
        and SequenceMatcher(
            a=similarity_left,
            b=similarity_right,
            autojunk=False,
        ).ratio()
        >= 0.92
    )


_LOCAL_GUARDED_DELETE_ISSUE_CODES = {
    "unconfirmed_bidder_commitment",
    "unconfirmed_product_capability",
    "personnel_narrative_without_source",
    "numeric_commitment",
    "service_level_commitment",
}


def _local_guarded_delete_candidate(
    card: dict[str, Any],
    *,
    model_candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    member_codes = {
        str(value) for value in card.get("member_issue_codes") or [] if value
    }
    if not member_codes or not member_codes <= _LOCAL_GUARDED_DELETE_ISSUE_CODES:
        return None
    if any(
        str(item.get("operation_type") or "") == "delete_sentence"
        for item in model_candidates
    ):
        return None
    if len(model_candidates) >= 3:
        return None
    if "explicit_must_preserve" not in card:
        return None
    if card.get("explicit_must_preserve"):
        return None
    if card.get("target_carries_coverage"):
        return None
    if card.get("target_contains_registered_placeholder"):
        return None
    target = dict(card.get("target") or {})
    sentence = str(target.get("offending_sentence") or "").strip()
    if _invalid_fresh_fact_target(
        sentence,
        section_title=str(card.get("section_title") or ""),
    ):
        return None
    context_after = str(target.get("context_after") or "")
    if (
        re.search(r"(?:如下表|见下表|如下所示|具体安排如下|整理如下表)", sentence)
        and ("|" in context_after or re.match(r"\s*(?:[-*+]|\d+[.)])", context_after))
    ):
        return None
    return {
        "candidate_id": "LOCAL-DELETE",
        "candidate_role": "delete",
        "candidate_source": "local_guarded_delete",
        "operation_type": "delete_sentence",
        "replacement_text": "",
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _fresh_patch_candidate_count_field(value: int) -> str:
    return {
        1: "tasks_with_one_candidate",
        2: "tasks_with_two_candidates",
        3: "tasks_with_three_candidates",
    }[int(value)]


def _fresh_patch_attempt_record(
    candidate: dict[str, Any],
    *,
    candidate_order: int,
    status: str,
    candidate_role: str | None = None,
    reason_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "candidate_order": int(candidate_order),
        "candidate_role": candidate_role,
        "candidate_source": str(candidate.get("candidate_source") or "model"),
        "operation_type": str(candidate.get("operation_type") or ""),
        "original_text": str(candidate.get("original_text") or ""),
        "replacement_text": str(candidate.get("replacement_text") or ""),
        "context_before": str(candidate.get("context_before") or ""),
        "context_after": str(candidate.get("context_after") or ""),
        "status": status,
        "reason_codes": list(reason_codes or []),
        "audit_findings": [],
        "post_patch_findings": [],
        "before_matching_issue_count": 0,
        "after_matching_issue_count": 0,
        "quality_audit": {},
        "resolved_member_issue_count": 0,
        "remaining_member_issue_count": 0,
        "new_blocking_issue_codes": [],
        "selection_rank": 0,
        "warning_codes": [],
    }


def _matching_patch_issue_findings(
    validation: dict[str, Any],
    *,
    draft: dict[str, Any],
    section_id: str,
    issue_codes: set[str],
    expected_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    details = [
        item
        for item in validation.get("error_details") or []
        if isinstance(item, dict)
    ]
    if not details:
        details = _fresh_quality_error_details(
            [str(value) for value in validation.get("errors") or []],
            draft=draft,
            expected_sections=expected_sections,
        )
    findings: list[dict[str, Any]] = []
    for item in details:
        code = str(item.get("issue_code") or item.get("error_code") or "")
        if code not in issue_codes:
            continue
        item_section_id = str(item.get("section_id") or "")
        if item_section_id and item_section_id != section_id:
            continue
        findings.append(
            {
                "issue_code": code,
                "section_id": section_id,
                "offending_sentence": item.get("offending_sentence"),
                "offending_span": item.get("offending_span"),
            }
        )
    return _unique_json_items(findings)


def _apply_validated_fresh_patches(
    *,
    initial: dict[str, Any],
    patch_payload: dict[str, Any],
    repair_context: dict[str, Any],
    fact_ledger: list[dict[str, Any]],
    initial_validation: dict[str, Any],
    validate_candidate: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    task_cards = [
        deepcopy(item) for item in repair_context.get("repair_task_cards") or []
        if isinstance(item, dict)
    ]
    for card in task_cards:
        fingerprint = str(card.get("issue_fingerprint") or "")
        code = str(card.get("issue_code") or "")
        if fingerprint and fingerprint not in set(card.get("member_issue_fingerprints") or []):
            card.setdefault("member_issue_fingerprints", []).append(fingerprint)
            card.setdefault("member_issues", []).append(
                {
                    "issue_fingerprint": fingerprint,
                    "issue_code": code,
                    "offending_span": str((card.get("target") or {}).get("offending_span") or ""),
                }
            )
        if code and code not in set(card.get("member_issue_codes") or []):
            card.setdefault("member_issue_codes", []).append(code)
    task_cards = _group_fresh_repair_task_cards(task_cards)
    base_hash = _candidate_sha256(initial)
    report = _fresh_patch_report_template(
        base_candidate_hash=base_hash, repair_task_count=len(task_cards)
    )
    report["multi_issue_target_group_count"] = sum(
        len(card.get("member_issue_fingerprints") or []) > 1
        for card in task_cards
    )
    if str(patch_payload.get("base_candidate_hash") or "") != base_hash:
        report["protocol_issue_code"] = "fresh_patch_base_hash_mismatch"
        report["unresolved_issue_fingerprints"] = [
            str(value)
            for card in task_cards
            for value in card.get("member_issue_fingerprints") or [card.get("issue_fingerprint")]
            if value
        ]
        return deepcopy(initial), report

    identity = _resolve_section_identities(
        initial, expected_sections=repair_context.get("expected_sections") or []
    )
    section_index_by_id = {
        str(section_id): int(index)
        for index, section_id in identity.get("resolved_by_index", {}).items()
    }
    cards_by_target = {
        str(card.get("repair_target_id") or ""): card
        for card in task_cards if card.get("repair_target_id")
    }
    target_ids_by_fingerprint: dict[str, set[str]] = {}
    for card in task_cards:
        target_id = str(card.get("repair_target_id") or "")
        for fingerprint in card.get("member_issue_fingerprints") or [card.get("issue_fingerprint")]:
            if fingerprint:
                target_ids_by_fingerprint.setdefault(str(fingerprint), set()).add(target_id)

    results_by_target: dict[str, dict[str, Any]] = {}
    duplicate_target_results: set[str] = set()
    invalid_results: list[dict[str, Any]] = []
    for result in patch_payload.get("repair_results") or []:
        target_id = str(result.get("repair_target_id") or "")
        if not target_id:
            mapped = target_ids_by_fingerprint.get(
                str(result.get("issue_fingerprint") or ""), set()
            )
            if len(mapped) == 1:
                target_id = next(iter(mapped))
        if target_id not in cards_by_target:
            invalid_results.append(result)
            continue
        if target_id in results_by_target:
            duplicate_target_results.add(target_id)
            continue
        results_by_target[target_id] = result

    selected_operations: list[dict[str, Any]] = []
    current_validation = deepcopy(initial_validation)
    preserved_placeholders = {
        str(value) for value in repair_context.get("preserved_placeholder_ids") or [] if value
    }
    preserved_declared_placeholders = (
        preserved_placeholders & _draft_placeholder_ids(initial)
    )
    preserved_content_placeholders = preserved_placeholders & _draft_content_placeholder_ids(initial)
    unresolved_group_ids: list[str] = []
    unresolved_fingerprints: list[str] = []

    for card in task_cards:
        target_id = str(card.get("repair_target_id") or "")
        primary_fingerprint = str(card.get("issue_fingerprint") or "")
        member_fingerprints = [
            str(value) for value in card.get("member_issue_fingerprints") or [primary_fingerprint]
            if value
        ]
        member_codes = [str(value) for value in card.get("member_issue_codes") or [card.get("issue_code")] if value]
        member_issues = [
            item for item in card.get("member_issues") or [] if isinstance(item, dict)
        ] or [
            {
                "issue_fingerprint": fingerprint,
                "issue_code": member_codes[min(index, len(member_codes) - 1)] if member_codes else "",
            }
            for index, fingerprint in enumerate(member_fingerprints)
        ]
        result = results_by_target.get(target_id)
        section_id = str(card.get("section_id") or "")
        group_report = {
            "repair_target_id": target_id,
            "section_id": section_id or None,
            "section_title": str(card.get("section_title") or "") or None,
            "target_sentence": str((card.get("target") or {}).get("offending_sentence") or ""),
            "member_issue_fingerprints": member_fingerprints,
            "member_issue_codes": member_codes,
            "candidate_count": 0,
            "status": "unresolved",
            "selection_reason": None,
            "selected_candidate_id": None,
            "resolved_issue_fingerprints": [],
            "remaining_issue_fingerprints": list(member_fingerprints),
            "diagnostic_codes": [],
            "candidate_attempts": [],
        }
        if target_id in duplicate_target_results:
            group_report["diagnostic_codes"].append(
                "fresh_patch_legacy_duplicate_target_result"
            )
        if not result:
            group_report["selection_reason"] = "candidate_result_missing"
            unresolved_group_ids.append(target_id)
            unresolved_fingerprints.extend(member_fingerprints)
            report["repair_target_groups"].append(group_report)
            for index, fingerprint in enumerate(member_fingerprints):
                report["issue_results"].append({
                    "issue_fingerprint": fingerprint,
                    "repair_target_id": target_id,
                    "section_id": section_id or None,
                    "section_title": group_report["section_title"],
                    "issue_code": member_codes[min(index, len(member_codes) - 1)] if member_codes else None,
                    "status": "unresolved",
                    "candidate_attempts": [],
                    "selected_candidate_id": None,
                    "candidate_count": 0,
                    "candidate_set_complete": False,
                    "missing_candidate_roles": ["primary", "conservative"],
                    "diagnostic_codes": list(group_report["diagnostic_codes"]),
                })
            continue

        raw_candidates: list[dict[str, Any]] = []
        role_names = ("primary", "conservative", "delete")
        for index, value in enumerate(result.get("candidates") or []):
            if not isinstance(value, dict) or index >= 3:
                continue
            supplied_role = str(value.get("candidate_role") or "")
            candidate_id_match = re.match(
                r"^C([123])(?:\D.*)?$", str(value.get("candidate_id") or ""), re.I
            )
            normalized_role = (
                role_names[int(candidate_id_match.group(1)) - 1]
                if candidate_id_match
                else role_names[index]
            )
            candidate = deepcopy(value)
            candidate["candidate_source"] = "model"
            candidate["candidate_role"] = normalized_role
            if supplied_role and supplied_role != normalized_role:
                group_report["diagnostic_codes"].append(
                    "fresh_patch_candidate_role_mismatch"
                )
            raw_candidates.append(_restore_fresh_patch_candidate(card, candidate))

        local_delete = _local_guarded_delete_candidate(
            card,
            model_candidates=raw_candidates,
        )
        if local_delete is not None:
            raw_candidates.append(
                _restore_fresh_patch_candidate(card, local_delete)
            )
            report["local_guarded_delete_candidate_count"] += 1

        candidate_count = len(raw_candidates)
        group_report["candidate_count"] = candidate_count
        report["repair_candidate_count"] += candidate_count
        if candidate_count in {1, 2, 3}:
            report[_fresh_patch_candidate_count_field(candidate_count)] += 1

        unique_candidates: list[tuple[int, dict[str, Any]]] = []
        duplicate_attempts: list[dict[str, Any]] = []
        seen_candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(raw_candidates):
            if any(_fresh_patch_candidates_equivalent(candidate, existing) for existing in seen_candidates):
                report["duplicate_candidate_count"] += 1
                report["rejected_candidate_count"] += 1
                duplicate_attempts.append(_fresh_patch_attempt_record(
                    candidate, candidate_order=index + 1,
                    candidate_role=str(candidate.get("candidate_role") or ""),
                    status="rejected", reason_codes=["fresh_patch_duplicate_candidate"]
                ))
                continue
            seen_candidates.append(candidate)
            unique_candidates.append((index, candidate))

        normalized_roles = {
            str(candidate.get("candidate_role") or "")
            for _, candidate in unique_candidates
        }
        missing_roles = [
            role for role in ("primary", "conservative")
            if role not in normalized_roles
        ]
        candidate_set_complete = not missing_roles
        if "conservative" in missing_roles:
            group_report["diagnostic_codes"].append(
                "fresh_patch_conservative_candidate_missing"
            )
            report["tasks_missing_conservative_candidate"] += 1

        candidates = sorted(
            unique_candidates,
            key=lambda pair: (pair[1].get("operation_type") == "delete_sentence", pair[0]),
        )
        attempted_orders: set[int] = set()
        selected_operation: dict[str, Any] | None = None
        resolved_fingerprints: list[str] = []
        multi_issue_group = len(member_fingerprints) > 1
        eligible_candidates: list[dict[str, Any]] = []
        for candidate_index, candidate in candidates:
            operation_type = str(candidate.get("operation_type") or "")
            candidate_role = str(candidate.get("candidate_role") or "")
            candidate_id = str(candidate.get("candidate_id") or "")
            attempt = _fresh_patch_attempt_record(
                candidate, candidate_order=candidate_index + 1,
                candidate_role=candidate_role, status="rejected"
            )
            attempted_orders.add(candidate_index + 1)
            reasons: list[str] = []
            target_payload = dict(card.get("target") or {})
            expected_original = str(
                target_payload.get("offending_span")
                if operation_type == "replace_span"
                else target_payload.get("offending_sentence") or ""
            )
            replacement_text = str(candidate.get("replacement_text") or "")
            if operation_type == "replace_span" and operation_type not in set(card.get("allowed_operation_types") or []):
                reasons.append("fresh_patch_operation_scope_too_narrow")
            role_operation_valid = (
                candidate_role in {"primary", "conservative"} and operation_type != "delete_sentence"
            ) or (candidate_role == "delete" and operation_type == "delete_sentence")
            if (
                str(result.get("section_id") or "") != section_id
                or section_id not in section_index_by_id
                or operation_type not in _FRESH_PATCH_OPERATION_TYPES
                or not role_operation_valid
                or not expected_original
                or (operation_type == "delete_sentence" and replacement_text)
                or (operation_type != "delete_sentence" and not replacement_text)
            ):
                reasons.append(
                    "fresh_patch_operation_target_invalid"
                    if operation_type == "replace_span" and not expected_original
                    else "fresh_patch_invalid_issue_binding"
                )

            operation: dict[str, Any] | None = None
            if not reasons:
                section_index = section_index_by_id[section_id]
                content = str((initial.get("sections") or [])[section_index].get("content") or "")
                if operation_type == "replace_span":
                    anchor, anchor_error = _locate_fresh_patch_span_by_coordinates(
                        content,
                        target_payload,
                    )
                else:
                    anchor, anchor_error = _locate_fresh_patch_anchor(
                        content, str(candidate.get("original_text") or ""),
                        context_before=str(candidate.get("context_before") or ""),
                        context_after=str(candidate.get("context_after") or ""),
                    )
                if anchor_error:
                    reasons.append(anchor_error)
                elif anchor:
                    operation_id = _json_sha256({
                        "repair_target_id": target_id,
                        "candidate_id": candidate_id,
                        "candidate_source": str(
                            candidate.get("candidate_source") or "model"
                        ),
                        "section_id": section_id,
                        "start": anchor[0], "end": anchor[1],
                        "replacement_text": replacement_text,
                    })
                    operation = {
                        "operation_id": operation_id,
                        "repair_target_id": target_id,
                        "issue_fingerprint": primary_fingerprint,
                        "member_issue_fingerprints": member_fingerprints,
                        "member_issue_codes": member_codes,
                        "candidate_id": candidate_id,
                        "candidate_source": str(
                            candidate.get("candidate_source") or "model"
                        ),
                        "section_id": section_id,
                        "section_title": group_report["section_title"],
                        "issue_code": str(card.get("issue_code") or "") or None,
                        "section_index": section_index,
                        "operation_type": operation_type,
                        "start": anchor[0], "end": anchor[1],
                        "original_text": content[anchor[0]:anchor[1]],
                        "replacement_text": replacement_text,
                        "context_before": str(candidate.get("context_before") or ""),
                        "context_after": str(candidate.get("context_after") or ""),
                        "exact_locator": operation_type == "replace_span",
                    }
                    if _fresh_patch_operations_overlap(operation, selected_operations):
                        reasons.append("fresh_patch_operation_overlap")

            if not reasons and operation:
                fact_findings = _patch_replacement_fact_findings(
                    initial=initial,
                    section_index=int(operation["section_index"]),
                    section_id=section_id,
                    replacement_text=replacement_text,
                    fact_ledger=fact_ledger,
                )
                attempt["audit_findings"] = fact_findings
                finding_codes = [
                    str(item.get("issue_code") or "")
                    for item in fact_findings if item.get("issue_code")
                ]
                attempt["new_blocking_issue_codes"] = list(
                    dict.fromkeys(finding_codes)
                )
                reasons.extend(finding_codes)

            if not reasons and operation:
                before_draft = _apply_fresh_patch_operations(initial, selected_operations)
                temporary = _apply_fresh_patch_operations(initial, [*selected_operations, operation])
                placeholder_lost = bool(
                    not preserved_declared_placeholders
                    <= _draft_placeholder_ids(temporary)
                    or not preserved_content_placeholders <= _draft_content_placeholder_ids(temporary)
                )
                if placeholder_lost:
                    reasons.append("fresh_patch_registered_placeholder_removed")
                temporary_validation = validate_candidate(temporary)
                reasons.extend(_patch_validation_regression_codes(current_validation, temporary_validation))
                new_blocking_codes = _new_blocking_issue_codes(
                    current_validation, temporary_validation
                )
                attempt["new_blocking_issue_codes"] = new_blocking_codes
                quality_audit = _patch_quality_audit(
                    before_draft=before_draft,
                    after_draft=temporary,
                    section_index=int(operation["section_index"]),
                    section_quality_requirements=dict(card.get("section_quality_floor") or {}),
                    before_validation=current_validation,
                    after_validation=temporary_validation,
                    placeholder_lost=placeholder_lost,
                )
                attempt["quality_audit"] = quality_audit
                if quality_audit.get("length_warning_code"):
                    attempt["warning_codes"].append(
                        str(quality_audit["length_warning_code"])
                    )
                if quality_audit["quality_floor_crossed"] or quality_audit["new_quality_issue_codes"]:
                    reasons.append("fresh_patch_prose_density_regressed")

                before_counts = _validation_issue_code_counts(current_validation)
                after_counts = _validation_issue_code_counts(temporary_validation)
                matching_codes = set(member_codes)
                before_matching_findings = _matching_patch_issue_findings(
                    current_validation,
                    draft=before_draft,
                    section_id=section_id,
                    issue_codes=matching_codes,
                    expected_sections=list(
                        repair_context.get("expected_sections") or []
                    ),
                )
                after_matching_findings = _matching_patch_issue_findings(
                    temporary_validation,
                    draft=temporary,
                    section_id=section_id,
                    issue_codes=matching_codes,
                    expected_sections=list(
                        repair_context.get("expected_sections") or []
                    ),
                )
                attempt["before_matching_issue_count"] = len(
                    before_matching_findings
                )
                attempt["after_matching_issue_count"] = len(
                    after_matching_findings
                )
                resolved_codes = {
                    code for code in set(member_codes)
                    if after_counts.get(code, 0) < before_counts.get(code, 0)
                }
                if (
                    not resolved_codes
                    or int(temporary_validation.get("raw_error_count") or 0)
                    >= int(current_validation.get("raw_error_count") or 0)
                ):
                    reasons.append("fresh_patch_issue_not_improved")
                    attempt["post_patch_findings"] = after_matching_findings
                    if not attempt["audit_findings"]:
                        attempt["audit_findings"] = deepcopy(
                            after_matching_findings
                        )
                candidate_resolved_fingerprints = _resolved_member_issue_fingerprints(
                    card,
                    before_counts=before_counts,
                    after_counts=after_counts,
                )
                attempt["resolved_member_issue_count"] = len(
                    candidate_resolved_fingerprints
                )
                attempt["remaining_member_issue_count"] = max(
                    len(member_fingerprints) - len(candidate_resolved_fingerprints), 0
                )

            if reasons:
                attempt["reason_codes"] = list(dict.fromkeys(reasons))
                report["rejected_candidate_count"] += 1
                group_report["candidate_attempts"].append(attempt)
                continue

            assert operation is not None
            if multi_issue_group:
                attempt["status"] = "eligible"
                group_report["candidate_attempts"].append(attempt)
                eligible_candidates.append(
                    {
                        "candidate_index": candidate_index,
                        "candidate_id": candidate_id,
                        "attempt": attempt,
                        "operation": operation,
                        "temporary_validation": temporary_validation,
                        "resolved_fingerprints": list(
                            candidate_resolved_fingerprints
                        ),
                    }
                )
                continue

            selected_operations.append(operation)
            current_validation = temporary_validation
            selected_operation = operation
            resolved_fingerprints = list(candidate_resolved_fingerprints)
            attempt["status"] = "applied"
            attempt["selection_rank"] = 1
            group_report["candidate_attempts"].append(attempt)
            group_report["selected_candidate_id"] = candidate_id
            group_report["selection_reason"] = "first_qualified_candidate"
            group_report["resolved_issue_fingerprints"] = list(resolved_fingerprints)
            group_report["remaining_issue_fingerprints"] = [
                value for value in member_fingerprints if value not in set(resolved_fingerprints)
            ]
            group_report["status"] = (
                "resolved" if not group_report["remaining_issue_fingerprints"]
                else "partially_resolved"
            )
            break

        if multi_issue_group and eligible_candidates:
            ranked = sorted(
                eligible_candidates,
                key=lambda item: (
                    -int(item["attempt"].get("resolved_member_issue_count") or 0),
                    int(item["attempt"].get("remaining_member_issue_count") or 0),
                    int(item["candidate_index"]),
                ),
            )
            for rank, item in enumerate(ranked, start=1):
                item["attempt"]["selection_rank"] = rank
                item["attempt"]["status"] = (
                    "applied" if rank == 1 else "not_selected_better_candidate_available"
                )
            best = ranked[0]
            selected_operation = best["operation"]
            selected_operations.append(selected_operation)
            current_validation = best["temporary_validation"]
            resolved_fingerprints = list(best["resolved_fingerprints"])
            group_report["selected_candidate_id"] = best["candidate_id"]
            group_report["selection_reason"] = "max_member_issue_resolution"
            group_report["resolved_issue_fingerprints"] = resolved_fingerprints
            group_report["remaining_issue_fingerprints"] = [
                value for value in member_fingerprints
                if value not in set(resolved_fingerprints)
            ]
            group_report["status"] = (
                "resolved" if not group_report["remaining_issue_fingerprints"]
                else "partially_resolved"
            )
        elif not selected_operation:
            group_report["selection_reason"] = "no_qualified_candidate"

        if selected_operation and not multi_issue_group:
            for candidate_index, candidate in candidates:
                if candidate_index + 1 in attempted_orders:
                    continue
                group_report["candidate_attempts"].append(_fresh_patch_attempt_record(
                    candidate, candidate_order=candidate_index + 1,
                    candidate_role=str(candidate.get("candidate_role") or ""),
                    status="not_evaluated_after_prior_candidate_applied"
                ))
        group_report["candidate_attempts"].extend(duplicate_attempts)
        group_report["candidate_attempts"] = sorted(
            group_report["candidate_attempts"],
            key=lambda item: int(item.get("candidate_order") or 0),
        )
        report["repair_target_groups"].append(group_report)

        resolved_set = set(group_report["resolved_issue_fingerprints"])
        remaining_set = set(group_report["remaining_issue_fingerprints"])
        if remaining_set:
            unresolved_group_ids.append(target_id)
            unresolved_fingerprints.extend(group_report["remaining_issue_fingerprints"])
        for index, fingerprint in enumerate(member_fingerprints):
            member = next(
                (
                    value for value in member_issues
                    if str(value.get("issue_fingerprint") or "") == fingerprint
                ),
                {},
            )
            is_primary = index == 0
            if fingerprint in resolved_set:
                status = (
                    "resolved_by_shared_operation"
                    if multi_issue_group
                    else "applied"
                )
            elif selected_operation:
                status = "remaining_after_shared_operation"
            else:
                status = "unresolved"
            if status == "resolved_by_shared_operation":
                report["issues_resolved_by_shared_operation_count"] += 1
                if index > 0:
                    report["operation_overlap_avoided_count"] += 1
            report["issue_results"].append({
                "issue_fingerprint": fingerprint,
                "repair_target_id": target_id,
                "applied_operation_id": selected_operation.get("operation_id") if selected_operation else None,
                "section_id": section_id or None,
                "section_title": group_report["section_title"],
                "issue_code": str(member.get("issue_code") or "") or None,
                "status": status,
                "candidate_attempts": deepcopy(group_report["candidate_attempts"]) if is_primary else [],
                "selected_candidate_id": group_report["selected_candidate_id"],
                "candidate_count": candidate_count,
                "candidate_set_complete": candidate_set_complete,
                "missing_candidate_roles": missing_roles,
                "diagnostic_codes": list(dict.fromkeys(group_report["diagnostic_codes"])),
            })

    for unsupported in repair_context.get("unsupported_repair_issues") or []:
        if isinstance(unsupported, dict):
            report["issue_results"].append(deepcopy(unsupported))
    for result in invalid_results:
        candidates = [item for item in result.get("candidates") or [] if isinstance(item, dict)]
        report["repair_candidate_count"] += len(candidates)
        report["rejected_candidate_count"] += len(candidates)
        report["issue_results"].append({
            "issue_fingerprint": result.get("issue_fingerprint"),
            "repair_target_id": result.get("repair_target_id"),
            "section_id": str(result.get("section_id") or "") or None,
            "section_title": None, "issue_code": None, "status": "unresolved",
            "candidate_count": len(candidates), "candidate_set_complete": False,
            "missing_candidate_roles": ["conservative"],
            "diagnostic_codes": ["fresh_patch_invalid_issue_binding"],
            "candidate_attempts": [
                _fresh_patch_attempt_record(
                    _restore_fresh_patch_candidate({}, item),
                    candidate_order=index + 1, status="rejected",
                    reason_codes=["fresh_patch_invalid_issue_binding"]
                ) for index, item in enumerate(candidates)
            ],
            "selected_candidate_id": None,
        })

    merged = _apply_fresh_patch_operations(initial, selected_operations)
    report["applied_operations"] = [
        {
            key: operation.get(key)
            for key in (
                "operation_id", "repair_target_id", "issue_fingerprint",
                "member_issue_fingerprints", "candidate_id", "section_id",
                "section_title", "issue_code", "operation_type", "candidate_source", "original_text",
                "replacement_text", "context_before", "context_after",
            )
        }
        | {
            "initial_text_sha256": _text_sha256(str(operation.get("original_text") or "")),
            "result_text_sha256": _text_sha256(str(operation.get("replacement_text") or "")),
        }
        for operation in selected_operations
    ]
    report["applied_operation_count"] = len(selected_operations)
    report["exact_locator_patch_count"] = sum(
        bool(operation.get("exact_locator"))
        for operation in selected_operations
    )
    report["local_guarded_delete_applied_count"] = sum(
        str(operation.get("candidate_source") or "")
        == "local_guarded_delete"
        for operation in selected_operations
    )
    report["unresolved_issue_fingerprints"] = list(dict.fromkeys(unresolved_fingerprints))
    report["unresolved_repair_task_count"] = len(set(unresolved_group_ids))
    return merged, report


def _audit_fresh_candidate_diff(
    *,
    initial: dict[str, Any] | None,
    fresh: dict[str, Any] | None,
    repair_context: dict[str, Any],
    fact_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    initial_draft = initial or {}
    fresh_draft = fresh or {}
    scope = dict(repair_context.get("fresh_edit_scope") or {})
    editable_ids = {
        str(value)
        for value in scope.get("editable_section_ids") or []
        if str(value)
    }
    issue_spans_by_section: dict[str, list[dict[str, Any]]] = {}
    for item in scope.get("editable_issue_spans") or []:
        if not isinstance(item, dict) or not item.get("section_id"):
            continue
        issue_spans_by_section.setdefault(str(item["section_id"]), []).append(item)
    expected_sections = list(repair_context.get("expected_sections") or [])
    if not expected_sections:
        expected_sections = [
            {
                "section_id": str(item.get("section_id") or ""),
                "section_title": str(item.get("section_title") or ""),
                "expected_order": index,
            }
            for index, item in enumerate(fact_ledger)
            if isinstance(item, dict) and item.get("section_id")
        ]
    initial_identity = _resolve_section_identities(
        initial_draft, expected_sections=expected_sections
    )
    fresh_identity = _resolve_section_identities(
        fresh_draft, expected_sections=expected_sections
    )

    def sections_by_id(
        draft: dict[str, Any], identity: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        return {
            str(identity["resolved_by_index"][index]): section
            for index, section in enumerate(draft.get("sections") or [])
            if isinstance(section, dict) and index in identity["resolved_by_index"]
        }

    initial_sections = sections_by_id(initial_draft, initial_identity)
    fresh_sections = sections_by_id(fresh_draft, fresh_identity)

    def resolved_section_hash(section: dict[str, Any] | None) -> str:
        payload = dict(section or {})
        payload.pop("section_id", None)
        return _json_sha256(payload)

    changed_section_ids = sorted(
        section_id
        for section_id in set(initial_sections) | set(fresh_sections)
        if resolved_section_hash(initial_sections.get(section_id))
        != resolved_section_hash(fresh_sections.get(section_id))
    )
    unauthorized_changed_section_ids: list[str] = []
    for section_id in changed_section_ids:
        if section_id not in editable_ids:
            unauthorized_changed_section_ids.append(section_id)
            continue
        before = initial_sections.get(section_id) or {}
        after = fresh_sections.get(section_id) or {}
        section_issue_spans = issue_spans_by_section.get(section_id) or []
        # section_id is identity metadata; absence/presence does not count as a
        # content edit once both candidates resolve to the same canonical ID.
        editable_metadata_keys = {"content", "section_id"}
        if any(
            str(item.get("issue_code") or "")
            in {
                "required_registered_placeholder_missing",
                "blocking_placeholder_missing_from_content",
            }
            for item in section_issue_spans
        ):
            editable_metadata_keys.add("placeholder_ids")
        before_metadata = {
            key: value
            for key, value in before.items()
            if key not in editable_metadata_keys
        }
        after_metadata = {
            key: value
            for key, value in after.items()
            if key not in editable_metadata_keys
        }
        if _json_sha256(before_metadata) != _json_sha256(after_metadata):
            unauthorized_changed_section_ids.append(section_id)
            continue
        if not _fresh_content_change_is_scoped(
            str(before.get("content") or ""),
            str(after.get("content") or ""),
            issue_spans=section_issue_spans,
        ):
            unauthorized_changed_section_ids.append(section_id)

    changed_clauses_by_section = {
        section_id: _new_or_changed_clauses(
            str((initial_sections.get(section_id) or {}).get("content") or ""),
            str((fresh_sections.get(section_id) or {}).get("content") or ""),
        )
        for section_id in changed_section_ids
        if section_id in fresh_sections
    }
    clause_sections = [
        {
            "section_id": section_id,
            "heading": str(fresh_sections[section_id].get("heading") or ""),
            "content": "\n".join(clauses),
        }
        for section_id, clauses in changed_clauses_by_section.items()
        if clauses
    ]
    clause_audit = audit_generation_permissions(
        {
            "sections": clause_sections,
            "claims": deepcopy(fresh_draft.get("claims") or []),
        },
        fact_ledger=fact_ledger,
    )
    finding_groups = (
        "unauthorized_numeric_claims",
        "unauthorized_personnel_claims",
        "unauthorized_product_capabilities",
        "candidate_facts_promoted",
        "unauthorized_bidder_commitments",
    )

    new_findings = [
        item
        for group in finding_groups
        for item in clause_audit.get(group) or []
        if isinstance(item, dict)
    ]
    new_fact_records: list[dict[str, Any]] = []
    for item in new_findings:
        section_id = str(item.get("section_id") or "")
        offending_span = str(item.get("offending_span") or item.get("text") or "")
        finding_sentence = str(item.get("offending_sentence") or "")
        candidates = changed_clauses_by_section.get(section_id) or []
        new_clause = next(
            (
                clause
                for clause in candidates
                if (
                    finding_sentence
                    and _normalize_diff_text(finding_sentence)
                    in _normalize_diff_text(clause)
                )
                or (
                    offending_span
                    and _normalize_diff_text(offending_span)
                    in _normalize_diff_text(clause)
                )
            ),
            finding_sentence,
        )
        if not new_clause or not offending_span:
            continue
        if not _normalized_span_is_within_sentence(offending_span, new_clause):
            continue
        new_fact_records.append(
            {
                "section_id": section_id or None,
                "issue_code": str(
                    item.get("code") or "generation_permission_violation"
                ),
                "new_clause": new_clause,
                "offending_span": offending_span,
                "matched_tender_fact_ids": sorted(
                    {
                        str(value)
                        for value in item.get("matched_tender_fact_ids") or []
                        if value
                    }
                ),
                "source_audit_result": "blocked",
            }
        )
    new_fact_records = _unique_json_items(new_fact_records)
    new_numeric_spans: list[str] = []
    new_personnel_fact_spans: list[str] = []
    new_product_capability_spans: list[str] = []
    new_resource_commitment_spans: list[str] = []
    new_bidder_commitment_spans: list[str] = []
    for item in new_fact_records:
        clause = str(item.get("new_clause") or "")
        code = str(item.get("issue_code") or "")
        if code in {"numeric_commitment", "schedule_frequency"}:
            new_numeric_spans.append(clause)
        elif code == "unconfirmed_product_capability":
            new_product_capability_spans.append(clause)
        elif code == "unconfirmed_resource_commitment":
            new_resource_commitment_spans.append(clause)
        elif code == "unconfirmed_bidder_commitment":
            new_bidder_commitment_spans.append(clause)
        else:
            new_personnel_fact_spans.append(clause)
    new_fact_issue_codes = sorted(
        {
            str(item.get("issue_code") or "generation_permission_violation")
            for item in new_fact_records
        }
    )
    section_identity_consistent = bool(
        initial_identity["consistent"] and fresh_identity["consistent"]
    )
    return {
        "changed_section_ids": changed_section_ids,
        "editable_section_ids": sorted(editable_ids),
        "frozen_section_ids": sorted(
            set(initial_sections) - set(editable_ids)
        ),
        "unauthorized_changed_section_ids": sorted(
            set(unauthorized_changed_section_ids)
        ),
        "section_identity_consistent": section_identity_consistent,
        "section_identity_issues": [
            *deepcopy(initial_identity["issues"]),
            *deepcopy(fresh_identity["issues"]),
        ],
        "new_fact_category_codes": new_fact_issue_codes,
        "new_fact_issue_codes": new_fact_issue_codes,
        "new_fact_records": new_fact_records,
        "new_numeric_spans": list(dict.fromkeys(new_numeric_spans)),
        "new_personnel_fact_spans": list(
            dict.fromkeys(new_personnel_fact_spans)
        ),
        "new_product_capability_spans": list(
            dict.fromkeys(new_product_capability_spans)
        ),
        "new_resource_commitment_spans": list(
            dict.fromkeys(new_resource_commitment_spans)
        ),
        "new_bidder_commitment_spans": list(
            dict.fromkeys(new_bidder_commitment_spans)
        ),
        "passed": section_identity_consistent
        and not unauthorized_changed_section_ids
        and not new_fact_records,
    }


def _new_or_changed_clauses(before: str, after: str) -> list[str]:
    before_clauses = _diff_clauses(before)
    after_clauses = _diff_clauses(after)
    before_keys = [_normalize_diff_text(value) for value in before_clauses]
    after_keys = [_normalize_diff_text(value) for value in after_clauses]
    matcher = SequenceMatcher(a=before_keys, b=after_keys, autojunk=False)
    additions: list[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "insert"}:
            additions.extend(after_clauses[j1:j2])
    return list(dict.fromkeys(value for value in additions if value.strip()))


def _diff_clauses(value: str) -> list[str]:
    clauses: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", line):
            continue
        if "|" in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            clauses.extend(cell for cell in cells if cell)
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)、]\s*|[一二三四五六七八九十]+[、.)]\s*)", "", line)
        clauses.extend(
            item.strip()
            for item in re.split(r"(?<=[。！？!?；;])", line)
            if item.strip()
        )
    return clauses


def _fresh_content_change_is_scoped(
    before: str,
    after: str,
    *,
    issue_spans: list[dict[str, Any]],
) -> bool:
    if before == after:
        return True
    if any(
        str(item.get("issue_code") or "")
        in {
            "insufficient_prose",
            "no_complete_prose",
            "only_tables_or_lists",
            "prose_ratio_too_low",
            "structured_content_exceeds_prose",
            "required_registered_placeholder_missing",
            "blocking_placeholder_missing_from_content",
            "table_without_intro",
            "title_not_matched",
        }
        for item in issue_spans
    ):
        return True
    before_sentences = _diff_sentences(before)
    after_sentences = _diff_sentences(after)
    authorized_indexes: set[int] = set()
    for index, sentence in enumerate(before_sentences):
        for issue in issue_spans:
            offending_sentence = str(issue.get("offending_sentence") or "")
            offending_span = str(issue.get("offending_span") or "")
            if (
                offending_sentence
                and (
                    _normalize_diff_text(offending_sentence)
                    in _normalize_diff_text(sentence)
                    or _normalize_diff_text(sentence)
                    in _normalize_diff_text(offending_sentence)
                )
            ) or (
                offending_span
                and _normalize_diff_text(offending_span)
                in _normalize_diff_text(sentence)
            ):
                authorized_indexes.update(
                    value
                    for value in (index - 1, index, index + 1)
                    if 0 <= value < len(before_sentences)
                )
    if not authorized_indexes:
        return False
    matcher = SequenceMatcher(a=before_sentences, b=after_sentences, autojunk=False)
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:
            adjacent = {i1 - 1, i1}
            if not (adjacent & authorized_indexes):
                return False
            continue
        if any(index not in authorized_indexes for index in range(i1, i2)):
            return False
    return True


def _diff_sentences(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;])|\n+", str(value or ""))
        if item.strip()
    ]


def _fresh_sentence_locations(value: str) -> list[dict[str, Any]]:
    """Return the exact source coordinates for the shared sentence splitter."""

    content = str(value or "")
    locations: list[dict[str, Any]] = []
    cursor = 0
    for sentence_index, sentence in enumerate(_diff_sentences(content)):
        start = content.find(sentence, cursor)
        if start < 0:
            return []
        end = start + len(sentence)
        locations.append(
            {
                "sentence_index": sentence_index,
                "sentence": sentence,
                "start": start,
                "end": end,
                "sentence_sha256": _text_sha256(sentence),
            }
        )
        cursor = end
    return locations


def _fresh_sentence_locator(
    content: str, sentence: str
) -> dict[str, Any] | None:
    normalized = _normalize_diff_text(sentence)
    matches = [
        item
        for item in _fresh_sentence_locations(content)
        if normalized
        and normalized
        in _normalize_diff_text(str(item.get("sentence") or ""))
    ]
    return matches[0] if len(matches) == 1 else None


def _locate_fresh_patch_span_by_coordinates(
    content: str,
    target: dict[str, Any],
) -> tuple[tuple[int, int] | None, str | None]:
    try:
        sentence_index = int(target.get("sentence_index"))
        span_start = int(target.get("span_start"))
        span_end = int(target.get("span_end"))
    except (TypeError, ValueError):
        return None, "fresh_patch_target_locator_mismatch"
    locations = _fresh_sentence_locations(content)
    if sentence_index < 0 or sentence_index >= len(locations):
        return None, "fresh_patch_target_locator_mismatch"
    location = locations[sentence_index]
    sentence = str(location.get("sentence") or "")
    if str(target.get("sentence_sha256") or "") != _text_sha256(sentence):
        return None, "fresh_patch_target_locator_mismatch"
    if not (0 <= span_start < span_end <= len(sentence)):
        return None, "fresh_patch_target_locator_mismatch"
    span_text = str(target.get("span_text") or "")
    if not span_text or sentence[span_start:span_end] != span_text:
        return None, "fresh_patch_target_locator_mismatch"
    absolute_start = int(location["start"]) + span_start
    absolute_end = int(location["start"]) + span_end
    if content[absolute_start:absolute_end] != span_text:
        return None, "fresh_patch_target_locator_mismatch"
    return (absolute_start, absolute_end), None


def _normalize_diff_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"(?:\*\*|__|`|#{1,6})", "", text)
    return re.sub(r"[\s，,。.!！?？：:；;、（）()\[\]{}<>《》\-—]+", "", text)


def _json_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _targeted_auto_repair_instruction(
    *,
    draft: dict[str, Any],
    audit_findings: list[dict[str, Any]],
    error_details: list[dict[str, Any]],
    schema: dict[str, Any],
    user_instruction: str,
) -> str:
    payload = {
        "repair_mode": "targeted_auto_repair",
        "current_chapter_draft": draft,
        "auto_fixable_findings": audit_findings,
        "section_specific_repair_details": error_details,
        "user_instruction": user_instruction,
        "chapter_schema": schema,
    }
    return (
        "Repair the complete current chapter in place. Return only one complete "
        "chapter JSON object conforming to the supplied Schema. Preserve the target "
        "title, all required headings in order, valid tender facts, confirmed facts, "
        "registered placeholders, requirement coverage and score coverage. Fix every "
        "auto_fixable finding with complete formal technical prose. Each substantive "
        "solution section must cover objective, prerequisites, execution method, "
        "control points, deliverables or completion criteria, and exception handling. "
        "Tables and bullets may only support prose. Do not invent objective facts, "
        "promote candidate/unknown evidence, add promises, or turn a release risk into "
        "a writing claim. Global writing rules, project constraints, the current user "
        "instruction, tender requirements and section fact permissions in the other "
        "messages remain binding during this repair.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _candidate_validation_snapshot(
    *,
    validation: dict[str, Any],
    quality_review: dict[str, Any],
    draft: dict[str, Any],
    expected_sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors = [str(item) for item in validation.get("errors") or []]
    details = _fresh_quality_error_details(
        errors,
        draft=draft,
        expected_sections=expected_sections,
    )
    chapter_metrics = dict(quality_review.get("chapter_metrics") or {})
    section_metrics = list(quality_review.get("section_metrics") or [])
    identity_resolution = _resolve_section_identities(
        draft,
        expected_sections=expected_sections,
    )
    section_quality: dict[str, dict[str, Any]] = {}
    for index, section in enumerate(draft.get("sections") or []):
        if not isinstance(section, dict):
            continue
        section_id = str(
            (identity_resolution.get("resolved_by_index") or {}).get(index)
            or section.get("section_id")
            or ""
        )
        if not section_id:
            continue
        metrics = (
            dict(section_metrics[index])
            if index < len(section_metrics)
            and isinstance(section_metrics[index], dict)
            else inspect_section_composition(section)
        )
        floor = section_quality_floor(section)
        floor_met = bool(metrics.get("source_only_personnel_exempt")) or (
            int(metrics.get("prose_paragraph_count") or 0)
            >= int(floor.get("minimum_paragraph_count") or 0)
            and int(metrics.get("prose_character_count") or 0)
            >= int(floor.get("minimum_prose_characters") or 0)
        )
        section_quality[section_id] = {
            "section_id": section_id,
            "section_title": str(section.get("heading") or ""),
            "prose_character_count": int(
                metrics.get("prose_character_count") or 0
            ),
            "prose_paragraph_count": int(
                metrics.get("prose_paragraph_count") or 0
            ),
            "quality_floor": deepcopy(floor),
            "quality_floor_met": floor_met,
            "quality_issue_codes": _quality_issue_codes(
                metrics.get("errors") or []
            ),
        }
    quality_issue_code_counts = _quality_issue_code_counts(
        quality_review.get("quality_errors") or []
    )
    total_prose_characters = sum(
        int(item.get("prose_character_count") or 0)
        for item in section_quality.values()
    )
    return {
        **deepcopy(validation),
        "raw_error_count": len(errors),
        "grouped_error_count": len(details),
        "error_details": details,
        "blocking_error_categories": sorted(
            {
                (
                    str(item.get("error_code") or ""),
                    str(item.get("offending_fact_type") or ""),
                )
                for item in details
            }
        ),
        "schema_valid": not any(
            item.startswith("schema validation failed") for item in errors
        ),
        "fatal_issue_count": int(
            (validation.get("issue_counts") or {}).get("fatal") or 0
        ),
        "placeholder_ids": sorted(_draft_placeholder_ids(draft)),
        "content_placeholder_ids": sorted(
            _draft_content_placeholder_ids(draft)
        ),
        "section_quality": section_quality,
        "coverage": {
            "missing_response_point_count": len(
                validation.get("missing_response_point_ids") or []
            ),
            "missing_score_item_count": len(
                validation.get("missing_score_item_ids") or []
            ),
            "covered_response_point_count": len(
                draft.get("requirements_covered") or []
            ),
            "covered_score_item_count": len(
                draft.get("score_items_covered") or []
            ),
            "headings_match": (
                validation.get("expected_section_titles")
                == validation.get("actual_section_titles")
            ),
        },
        "quality_comparison": {
            "prose_character_count": total_prose_characters,
            "prose_ratio": float(chapter_metrics.get("prose_ratio") or 0),
            "bullet_ratio": float(chapter_metrics.get("bullet_ratio") or 0),
            "table_ratio": float(chapter_metrics.get("table_ratio") or 0),
            "insufficient_section_count": len(
                quality_review.get("sections_with_insufficient_prose") or []
            ),
            "list_dominant_section_count": len(
                chapter_metrics.get("list_dominant_sections") or []
            ),
            "inline_step_chain_count": int(
                chapter_metrics.get("inline_step_chain_count") or 0
            ),
            "markdown_table_error_count": len(
                quality_review.get("markdown_table_errors") or []
            ),
            "quality_error_count": len(
                quality_review.get("quality_errors") or []
            ),
            "quality_issue_codes": sorted(quality_issue_code_counts),
            "quality_issue_code_counts": quality_issue_code_counts,
        },
    }


_FINAL_SELECTION_QUALITY_BLOCKING_CODES = {
    "insufficient_prose",
    "prose_ratio_too_low",
    "structured_content_exceeds_prose",
    "bullet_dominant",
    "only_tables_or_lists",
    "no_complete_prose",
}


def _quality_issue_codes(errors: list[Any]) -> list[str]:
    codes: list[str] = []
    for error in errors:
        parts = str(error).split(":", 3)
        if len(parts) >= 3 and parts[0] == "section_quality":
            codes.append(parts[2])
        elif len(parts) >= 2 and parts[0] == "chapter_quality":
            codes.append(parts[1])
    return sorted(set(codes))


def _quality_issue_code_counts(errors: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for error in errors:
        parts = str(error).split(":", 3)
        code = (
            parts[2]
            if len(parts) >= 3 and parts[0] == "section_quality"
            else parts[1]
            if len(parts) >= 2 and parts[0] == "chapter_quality"
            else ""
        )
        if code:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _candidate_selection_audit(
    *,
    initial: dict[str, Any],
    fresh: dict[str, Any],
) -> dict[str, Any]:
    initial_quality = initial.get("quality_comparison") or {}
    fresh_quality = fresh.get("quality_comparison") or {}
    initial_characters = int(
        initial_quality.get("prose_character_count") or 0
    )
    fresh_characters = int(fresh_quality.get("prose_character_count") or 0)
    prose_change_ratio = (
        (fresh_characters - initial_characters) / initial_characters
        if initial_characters
        else 0.0
    )
    initial_counts = {
        str(code): int(count or 0)
        for code, count in (
            initial_quality.get("quality_issue_code_counts") or {}
        ).items()
    }
    fresh_counts = {
        str(code): int(count or 0)
        for code, count in (
            fresh_quality.get("quality_issue_code_counts") or {}
        ).items()
    }
    initial_codes = sorted(
        set(initial_quality.get("quality_issue_codes") or initial_counts)
    )
    fresh_codes = sorted(
        set(fresh_quality.get("quality_issue_codes") or fresh_counts)
    )
    new_quality_codes = sorted(
        code
        for code, count in fresh_counts.items()
        if count > int(initial_counts.get(code) or 0)
    )

    initial_sections = initial.get("section_quality") or {}
    fresh_sections = fresh.get("section_quality") or {}
    within_tolerance: list[str] = []
    length_warnings: list[str] = []
    crossing_floor: list[str] = []
    for section_id in sorted(set(initial_sections) & set(fresh_sections)):
        before = int(
            (initial_sections.get(section_id) or {}).get(
                "prose_character_count"
            )
            or 0
        )
        after = int(
            (fresh_sections.get(section_id) or {}).get(
                "prose_character_count"
            )
            or 0
        )
        change = after - before
        ratio = change / before if before else 0.0
        initial_floor_met = bool(
            (initial_sections.get(section_id) or {}).get(
                "quality_floor_met", True
            )
        )
        fresh_floor_met = bool(
            (fresh_sections.get(section_id) or {}).get(
                "quality_floor_met", True
            )
        )
        if initial_floor_met and not fresh_floor_met:
            crossing_floor.append(section_id)
        if -0.15 <= ratio <= 0.20 or abs(change) < 300:
            within_tolerance.append(section_id)
        elif ratio < -0.15:
            # A length reduction is review data only. It becomes blocking only
            # through a real floor crossing or a newly introduced quality issue.
            length_warnings.append(section_id)

    initial_coverage = initial.get("coverage") or {}
    fresh_coverage = fresh.get("coverage") or {}
    coverage_regressed = bool(
        bool(initial_coverage.get("headings_match"))
        and not bool(fresh_coverage.get("headings_match"))
    )
    coverage_regressed = coverage_regressed or any(
        int(fresh_coverage.get(key) or 0)
        > int(initial_coverage.get(key) or 0)
        for key in ("missing_response_point_count", "missing_score_item_count")
    )
    coverage_regressed = coverage_regressed or any(
        int(fresh_coverage.get(key) or 0)
        < int(initial_coverage.get(key) or 0)
        for key in ("covered_response_point_count", "covered_score_item_count")
    )
    initial_placeholders = set(initial.get("placeholder_ids") or [])
    fresh_placeholders = set(fresh.get("placeholder_ids") or [])
    initial_content_placeholders = set(
        initial.get("content_placeholder_ids") or []
    )
    fresh_content_placeholders = set(
        fresh.get("content_placeholder_ids") or []
    )
    placeholder_lost = bool(
        not initial_placeholders <= fresh_placeholders
        or not initial_content_placeholders <= fresh_content_placeholders
    )
    return {
        "initial_prose_characters": initial_characters,
        "fresh_prose_characters": fresh_characters,
        "prose_change_ratio": round(prose_change_ratio, 6),
        "sections_within_tolerance": within_tolerance,
        "sections_with_length_warning": length_warnings,
        "sections_crossing_quality_floor": crossing_floor,
        "initial_quality_issue_codes": initial_codes,
        "fresh_quality_issue_codes": fresh_codes,
        "new_quality_issue_codes": new_quality_codes,
        "coverage_regressed": coverage_regressed,
        "placeholder_lost": placeholder_lost,
    }


def _fresh_candidate_is_monotonic_improvement(
    *,
    initial: dict[str, Any],
    fresh: dict[str, Any],
) -> tuple[bool, str]:
    if not initial or not fresh:
        return False, "candidate_validation_unavailable"
    selection_audit = _candidate_selection_audit(
        initial=initial,
        fresh=fresh,
    )
    if int(fresh.get("fatal_issue_count") or 0) > int(
        initial.get("fatal_issue_count") or 0
    ):
        return False, "fresh_fatal_issue_count_increased"
    initial_coverage = initial.get("coverage") or {}
    fresh_coverage = fresh.get("coverage") or {}
    if bool(initial.get("schema_valid")) and not bool(fresh.get("schema_valid")):
        return False, "fresh_schema_regressed"
    if bool(initial_coverage.get("headings_match")) and not bool(
        fresh_coverage.get("headings_match")
    ):
        return False, "fresh_coverage_regressed"
    for key in ("missing_response_point_count", "missing_score_item_count"):
        if int(fresh_coverage.get(key) or 0) > int(
            initial_coverage.get(key) or 0
        ):
            return False, "fresh_coverage_regressed"
    for key in ("covered_response_point_count", "covered_score_item_count"):
        if int(fresh_coverage.get(key) or 0) < int(
            initial_coverage.get(key) or 0
        ):
            return False, "fresh_coverage_regressed"
    if selection_audit["placeholder_lost"]:
        return False, "fresh_placeholder_lost"
    if selection_audit["sections_crossing_quality_floor"]:
        return False, "fresh_quality_floor_crossed"
    if any(
        code in _FINAL_SELECTION_QUALITY_BLOCKING_CODES
        for code in selection_audit["new_quality_issue_codes"]
    ):
        return False, "fresh_new_quality_blocking_issue"
    initial_categories = {
        tuple(item) for item in initial.get("blocking_error_categories") or []
    }
    fresh_categories = {
        tuple(item) for item in fresh.get("blocking_error_categories") or []
    }
    quality_codes = set(selection_audit["initial_quality_issue_codes"]) | set(
        selection_audit["fresh_quality_issue_codes"]
    )
    new_non_quality_categories = {
        item for item in fresh_categories - initial_categories if item[0] not in quality_codes
    }
    if new_non_quality_categories:
        return False, "fresh_added_new_blocking_categories"
    raw_improved = int(fresh.get("raw_error_count") or 0) < int(
        initial.get("raw_error_count") or 0
    )
    grouped_improved = int(fresh.get("grouped_error_count") or 0) < int(
        initial.get("grouped_error_count") or 0
    )
    if not (raw_improved or grouped_improved):
        return False, "fresh_blocking_error_count_not_strictly_lower"
    return True, "fresh_is_strict_monotonic_improvement"


def _empty_quality_review() -> dict[str, Any]:
    return {
        "duplicate_section_pairs": [],
        "tender_tables_reused": [],
        "tender_tables_created": [],
        "scoring_reference_tables_inserted": [],
        "complex_tender_tables_preserved": [],
        "table_fill_data": [],
        "markdown_table_errors": [],
        "section_metrics": [],
        "chapter_metrics": {},
        "quality_errors": [],
        "quality_warnings": [],
        "sections_with_insufficient_prose": [],
        "soft_warnings": [],
    }


def _assess_json_repair_integrity(
    raw_content: str,
    repaired_draft: dict[str, Any],
) -> dict[str, Any]:
    before = _repair_content_snapshot(raw_content)
    repaired_text = json.dumps(repaired_draft, ensure_ascii=False, separators=(",", ":"))
    after = _repair_content_snapshot(repaired_text)
    reasons: list[str] = []
    if after["table_count"] > before["table_count"]:
        reasons.append("table_count_increased")
    if after["bullet_item_count"] > before["bullet_item_count"]:
        reasons.append("bullet_item_count_increased")
    if before["section_count"] and after["section_count"] > before["section_count"]:
        reasons.append("section_count_increased")
    if before["content_sha256"] and not set(before["content_sha256"]) <= set(
        after["content_sha256"]
    ):
        reasons.append("section_content_substantively_changed")
    growth_limit = max(160, int(before["content_character_count"] * 1.08))
    if after["content_character_count"] > growth_limit:
        reasons.append("content_character_count_increased")
    if set(after["commitment_markers"]) - set(before["commitment_markers"]):
        reasons.append("new_fact_or_commitment_marker")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "before": before,
        "after": after,
    }


def _repair_content_snapshot(value: str) -> dict[str, Any]:
    text = str(value or "")
    normalized = text.replace(r"\n", "\n")
    tables = len(
        re.findall(
            r"(?m)^\s*\|(?:\s*:?-{3,}:?\s*\|)+",
            normalized,
        )
    )
    bullets = len(re.findall(r"(?m)^\s*[-*+]\s+\S", normalized))
    section_count = len(re.findall(r'"heading"\s*:', text))
    content_values = re.findall(
        r'"content"\s*:\s*"((?:\\.|[^"\\])*)',
        text,
        flags=re.S,
    )
    content_chars = sum(len(re.sub(r"\\[nrt\"\\/]", "", item)) for item in content_values)
    if not content_values:
        content_chars = len(re.sub(r"\s+", "", text))
    content_hashes = [
        hashlib.sha256(
            item.replace(r"\n", "\n").replace(r"\"", '"').encode("utf-8")
        ).hexdigest()
        for item in content_values
    ]
    commitment_pattern = re.compile(
        r"(?:\d+\s*(?:个)?(?:工作日|小时|分钟|人|名|天)|"
        r"高可用|业务不中断|国密|双因子认证|RBAC|多副本|在线扩展|"
        r"故障自动恢复|无缝加入集群|我公司已通过|我方项目经理为)",
        re.I,
    )
    return {
        "content_character_count": content_chars,
        "table_count": tables,
        "bullet_item_count": bullets,
        "section_count": section_count,
        "content_sha256": content_hashes,
        "commitment_markers": sorted(set(commitment_pattern.findall(normalized))),
    }


def _model_json_repair_token_budget(profile: Any) -> int:
    configured = int(getattr(profile, "max_output_tokens", 0) or 0)
    baseline = configured or 8000
    return min(24000, max(baseline, int(baseline * 1.25)))


def _safe_model_error_summary(error: Exception, *, profile: Any = None) -> str:
    value = f"{type(error).__name__}: {str(error)}"
    secret = str(getattr(profile, "api_key", "") or "")
    return _redact_model_debug_text(value, secret=secret)[:1000]


def _save_invalid_model_output(
    *,
    output_root: Path,
    output_key: str,
    target_id: str,
    step: int,
    content: str,
    secret: str = "",
) -> dict[str, Any]:
    safe_target = re.sub(r"[^A-Za-z0-9._-]+", "_", str(target_id)) or "target"
    path = Path(output_root) / str(output_key) / "debug" / (
        f"{safe_target}.model-step-{int(step)}.invalid-json.txt"
    )
    redacted = _redact_model_debug_text(
        str(content) if content else "[raw model content unavailable]",
        secret=secret,
    )
    digest = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    try:
        _atomic_write_text(path, redacted + ("\n" if not redacted.endswith("\n") else ""))
    except OSError:
        return {"saved": False, "path": None, "sha256": digest}
    return {"saved": True, "path": str(path), "sha256": digest}


def _save_incomplete_model_diagnostics(
    *,
    output_root: Path,
    output_key: str,
    target_id: str,
    step: int,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Save only the safe diagnostic projection, never raw provider payloads."""
    safe_target = re.sub(r"[^A-Za-z0-9._-]+", "_", str(target_id)) or "target"
    path = Path(output_root) / str(output_key) / "debug" / (
        f"{safe_target}.model-step-{int(step)}.incomplete-response.json"
    )
    content = json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        _atomic_write_text(path, content + "\n")
    except OSError:
        return {"saved": False, "path": None, "sha256": digest}
    return {"saved": True, "path": str(path), "sha256": digest}


def _save_repaired_model_output(
    *,
    output_root: Path,
    output_key: str,
    target_id: str,
    step: int,
    content: str,
    secret: str = "",
) -> dict[str, Any]:
    safe_target = re.sub(r"[^A-Za-z0-9._-]+", "_", str(target_id)) or "target"
    path = Path(output_root) / str(output_key) / "debug" / (
        f"{safe_target}.model-step-{int(step)}.repaired-json.txt"
    )
    redacted = _redact_model_debug_text(str(content), secret=secret)
    digest = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    try:
        _atomic_write_text(path, redacted + ("\n" if not redacted.endswith("\n") else ""))
    except OSError:
        return {"saved": False, "path": None, "sha256": digest}
    return {"saved": True, "path": str(path), "sha256": digest}


def _record_tool_argument_json_repairs(
    *,
    tool_calls: list[dict[str, Any]],
    step: int,
    output_root: Path,
    output_key: str,
    target_id: str,
    secret: str,
    emit: Callable[..., Any],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for call in tool_calls:
        metadata = dict(call.get("argument_metadata") or {})
        if not metadata.get("applied"):
            continue
        tool_name = str(call.get("name") or "")
        original = str(metadata.pop("_original_arguments", ""))
        repaired = str(metadata.pop("_repaired_arguments", ""))
        safe_event = {
            "tool_name": tool_name,
            "repair_type": metadata.get("repair_type"),
            "repair_count": int(metadata.get("repair_count") or 0),
            "original_character_count": int(
                metadata.get("original_character_count") or 0
            ),
            "repaired_character_count": int(
                metadata.get("repaired_character_count") or 0
            ),
        }
        emit("tool_arguments_json_repair_started", step=step, data=safe_event)
        original_debug = _save_invalid_model_output(
            output_root=output_root,
            output_key=output_key,
            target_id=target_id,
            step=step,
            content=original,
            secret=secret,
        )
        repaired_debug = _save_repaired_model_output(
            output_root=output_root,
            output_key=output_key,
            target_id=target_id,
            step=step,
            content=repaired,
            secret=secret,
        )
        report = {
            **metadata,
            "tool_name": tool_name,
            "original_response_path": original_debug.get("path"),
            "original_response_saved": bool(original_debug.get("saved")),
            "repaired_response_path": repaired_debug.get("path"),
            "repaired_response_saved": bool(repaired_debug.get("saved")),
        }
        reports.append(report)
        emit(
            "tool_arguments_json_repair_completed",
            step=step,
            data={**safe_event, "schema_validation_started": True},
        )
        call["argument_metadata"] = metadata
    return reports


def _record_failed_tool_argument_json_repair(
    *,
    error: ModelJsonParseError,
    step: int,
    emit: Callable[..., Any],
) -> dict[str, Any] | None:
    diagnostics = error.safe_diagnostics()
    metadata = dict(diagnostics.get("json_syntax_repair") or {})
    if not metadata.get("attempted"):
        return None
    safe_event = {
        "tool_name": str(diagnostics.get("tool_name") or ""),
        "repair_type": metadata.get("repair_type"),
        "repair_count": int(metadata.get("repair_count") or 0),
        "original_character_count": int(
            metadata.get("original_character_count") or 0
        ),
        "repaired_character_count": int(
            metadata.get("repaired_character_count") or 0
        ),
    }
    emit("tool_arguments_json_repair_started", step=step, data=safe_event)
    emit(
        "tool_arguments_json_repair_failed",
        step=step,
        data={**safe_event, "schema_validation_started": False},
    )
    return {**metadata, "tool_name": safe_event["tool_name"]}


def _redact_model_debug_text(value: str, *, secret: str = "") -> str:
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED_API_KEY]")
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|authorization|token)\s*[=:]\s*)"
        r"[\"']?[^\s,\"']+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,})?\b",
        "[REDACTED_TOKEN]",
        text,
    )
    text = re.sub(
        r"(?i)(\"reasoning_content\"\s*:\s*)\"(?:\\.|[^\"\\])*\"",
        r'\1"[REDACTED]"',
        text,
    )
    text = re.sub(
        r"(?i)(\"(?:parsed_text|raw_text|source_text|evidence_text|knowledge_content)\""
        r"\s*:\s*)\"(?:\\.|[^\"\\])*\"",
        r'\1"[REDACTED_KNOWLEDGE_TEXT]"',
        text,
    )
    text = re.sub(
        r"(?im)^(\s*reasoning_content\s*[:=]).*$",
        r"\1 [REDACTED]",
        text,
    )
    text = re.sub(
        r"(?<![\dXx])\d{17}[\dXx](?![\dXx])",
        "[REDACTED_ID]",
        text,
    )
    text = re.sub(
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "[REDACTED_PHONE]",
        text,
    )
    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED_EMAIL]",
        text,
    )
    return text


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _candidate_is_artifact_eligible(
    *,
    draft: dict[str, Any] | None,
    markdown: str,
    schema_reviewable: bool,
    empty_content_allowed_section_ids: set[str] | None = None,
) -> bool:
    if not schema_reviewable or not isinstance(draft, dict):
        return False
    sections = draft.get("sections")
    if not isinstance(sections, list) or not sections:
        return False
    if any(
        not isinstance(section, dict)
        or not str(section.get("heading") or "").strip()
        or (
            not str(section.get("content") or "").strip()
            and str(section.get("section_id") or section.get("heading") or "")
            not in set(empty_content_allowed_section_ids or set())
        )
        for section in sections
    ):
        return False
    if not str(markdown or "").strip():
        return False
    try:
        json.dumps(draft, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return True


def _candidate_schema_is_legal(
    draft: dict[str, Any] | None,
    schema: dict[str, Any],
) -> bool:
    if not isinstance(draft, dict):
        return False
    try:
        Draft202012Validator(schema).validate(draft)
    except ValidationError:
        return False
    return True


def _write_and_verify_candidate_artifacts(
    *,
    draft: dict[str, Any],
    markdown: str,
    json_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    serialized = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(json_path, serialized)
    _atomic_write_text(markdown_path, markdown)
    for path in (json_path, markdown_path):
        if not path.is_file():
            raise OSError(f"candidate artifact does not exist after write: {path.name}")
        if path.stat().st_size <= 0:
            raise OSError(f"candidate artifact is empty after write: {path.name}")
    reparsed = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(reparsed, dict):
        raise ValueError("candidate JSON did not parse as an object")
    if reparsed != draft:
        raise ValueError("candidate JSON changed during artifact persistence")
    json_bytes = json_path.read_bytes()
    markdown_bytes = markdown_path.read_bytes()
    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "json_sha256": hashlib.sha256(json_bytes).hexdigest(),
        "markdown_sha256": hashlib.sha256(markdown_bytes).hexdigest(),
        "json_size_bytes": len(json_bytes),
        "markdown_size_bytes": len(markdown_bytes),
    }


def _model_call_summary(step: int, metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "provider", "protocol", "profile_name", "model", "api_mode", "request_id",
        "duration_ms", "finish_status", "retry_count", "usage",
    }
    return {"step": step, **{key: value for key, value in metadata.items() if key in allowed}}


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        normalized_key = str(key).lower()
        if normalized_key in {"api_key", "authorization", "headers"} or normalized_key.endswith("_api_key"):
            continue
        if key in {"markdown", "content"}:
            result[key] = {"character_count": len(str(value))}
        elif key == "sections" and isinstance(value, list):
            result[key] = {
                "section_count": len(value),
                "titles": [str(item.get("title") or "")[:100] for item in value[:20] if isinstance(item, dict)],
                "character_count": sum(
                    len(str(item.get("content") or "")) for item in value if isinstance(item, dict)
                ),
            }
        elif key in {"candidates", "entity_names"} and isinstance(value, list):
            result[key] = {"item_count": len(value), "values_redacted": True}
        elif key == "items" and isinstance(value, list):
            result[key] = {
                "item_count": len(value),
                "fields": [str(item.get("field") or "")[:100] for item in value[:20] if isinstance(item, dict)],
            }
        elif isinstance(value, str):
            result[key] = value[:300]
        elif isinstance(value, list):
            result[key] = value[:20]
        elif isinstance(value, (int, float, bool)) or value is None:
            result[key] = value
    return result


def _result_count(value: dict[str, Any]) -> int:
    for key in ("results", "requirements", "tasks", "draft_units"):
        if isinstance(value.get(key), list):
            return len(value[key])
    return int(value.get("pending_count") or 0)


def _sum_usage(calls: list[dict[str, Any]]) -> dict[str, int | None]:
    result = empty_usage()
    for key in result:
        values = [item.get("usage", {}).get(key) for item in calls if item.get("usage", {}).get(key) is not None]
        result[key] = sum(values) if values else None
    return result


def _collect_target_requirement_values(node: dict[str, Any], field: str) -> list[str]:
    values = [str(item) for item in (node.get("writing_requirements") or {}).get(field) or []]
    values.extend(str(item) for item in node.get(field) or [])
    for child in node.get("children") or []:
        values.extend(_collect_target_requirement_values(child, field))
    return list(dict.fromkeys(value for value in values if value))


def _all_target_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        values.append(child)
        values.extend(_all_target_nodes(child))
    return values


def _normalize_claim(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "text": str(item.get("text") or ""),
        "claim_type": str(item.get("claim_type") or "proposal_statement"),
        "source_ids": _unique_strings(_string_list(item.get("source_ids"))),
        "evidence_ids": _unique_strings(_string_list(item.get("evidence_ids"))),
        "human_review_required": bool(item.get("human_review_required")),
    }
    for key in (
        "response_intent",
        "evidence_status",
        "deviation_status",
        "tender_baseline",
        "offered_value",
        "comparison_direction",
        "additional_obligation",
        "confirmation_question",
    ):
        if item.get(key) is not None:
            normalized[key] = str(item.get(key) or "")
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _literal_placeholders(content: str) -> list[str]:
    return _unique_strings(extract_placeholder_references(str(content or "")))


def _covered_ids(draft: dict[str, Any], root_field: str, section_field: str) -> list[str]:
    values = _string_list(draft.get(root_field))
    for section in list(draft.get("sections") or []):
        if isinstance(section, dict):
            values.extend(_string_list(section.get(section_field)))
    return _unique_strings(values)

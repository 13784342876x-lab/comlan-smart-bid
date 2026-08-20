import json
from pathlib import Path
from types import SimpleNamespace

from app.chapter_drafting_agent import (
    build_technical_draft,
    build_chapter_prompts,
    build_deterministic_chapter,
    drafting_input_paths,
    inspect_technical_draft,
    validate_chapter_draft,
)
from app.chapter_drafting_pilot import build_technical_draft_pilot, inspect_technical_draft_pilot
from app.llm_client import ModelCallResult, empty_usage
from app.llm_errors import ModelOutputIncompleteError, ModelSchemaValidationError


LOT = "lot-stage5"
TITLES = [
    "技术部分总体评价",
    "项目负责人情况",
    "项目团队人员结构",
    "业绩情况",
    "服务方案",
    "工作进度及保证措施",
    "工作基础",
    "专利数量",
    "绩效评价",
]
TASK_TYPES = [
    "proposal_drafting",
    "mixed_drafting",
    "mixed_drafting",
    "evidence_based_drafting",
    "proposal_drafting",
    "proposal_drafting",
    "evidence_based_drafting",
    "evidence_based_drafting",
    "evidence_based_drafting",
]
BLOCKING = [
    "ready_with_placeholders",
    "blocked_by_manual_review",
    "blocked_by_manual_review",
    "blocked_by_evidence",
    "ready_with_placeholders",
    "ready_with_placeholders",
    "blocked_by_evidence",
    "blocked_by_evidence",
    "blocked_by_evidence",
]
PLACEHOLDER_IDS = ["PH-PKG", "PH-PERSON", "PH-TEAM", "PH-PERF", "PH-WORK", "PH-PATENT", "PH-EVAL"]


def dump(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def placeholder(value: str, text: str) -> dict:
    return {
        "placeholder_id": value,
        "placeholder_type": "missing_or_blocked_fact",
        "display_text": f"[待确认：{text}]",
        "required_fact": text,
        "related_evidence_requirement_ids": [f"er-{value}"],
        "related_resolution_ids": [f"resolution-{value}"],
        "related_intake_package_ids": [f"intake-{value}"],
        "blocking_level": "blocks_full_score_assessment",
        "replacement_rule": "确认后由正式证据替换。",
    }


def write_stage4_inputs(tmp_path: Path) -> Path:
    output_dir = tmp_path / "outputs"
    lot_dir = output_dir / LOT
    chapter_ids = [f"task_{15 + index:03d}_technical_{index + 1:02d}_{TITLES[index]}" for index in range(9)]
    score_ids = [f"technical_{index + 1:02d}_score" for index in range(9)]
    ph_by_chapter = [
        [placeholder("PH-PKG", "包名称冲突")],
        [placeholder("PH-PERSON", "项目负责人姓名、部门、角色及可投入性")],
        [placeholder("PH-TEAM", "团队人员及投入状态")],
        [placeholder("PH-PERF", "5项符合评分规则的企业业绩证据包")],
        [placeholder("PH-PKG", "包名称冲突")],
        [placeholder("PH-PKG", "包名称冲突")],
        [placeholder("PH-WORK", "工作基础证明")],
        [placeholder("PH-PATENT", "2项有效专利证明")],
        [placeholder("PH-EVAL", "绩效评价结果")],
    ]
    tasks = []
    pack_chapters = []
    outline_chapters = []
    context_chapters = []
    for index, chapter_id in enumerate(chapter_ids):
        requirement = {
            "requirement_id": f"REQ-{index + 1}",
            "title": f"{TITLES[index]}响应要求",
            "raw_text": "应在技术响应中明确说明。",
            "compliance_level": "fatal_if_missing" if index == 0 else "must_respond",
        }
        task = {
            "task_id": f"writing-{index + 1}",
            "chapter_id": chapter_id,
            "chapter_title": TITLES[index],
            "task_type": TASK_TYPES[index],
            "blocking_status": BLOCKING[index],
            "input_context": {
                "project": {"package_name": "未确认名称"},
                "project_conflicts": [
                    {
                        "field": "package_name",
                        "status": "needs_human_confirmation",
                        "candidate_values": ["工业数据管理技术研究", "电力系统数据管理国际标准研究"],
                    }
                ],
                "score_item_ids": [score_ids[index]],
                "requirement_ids": [f"REQ-{index + 1}"],
                "action_group_ids": [f"AG-{index + 1}"],
                "evidence_requirement_ids": [f"ER-{index + 1}"],
            },
            "scoring_targets": [
                {
                    "score_item_id": score_ids[index],
                    "score_item_name": TITLES[index],
                    "scoring_goal": f"覆盖{TITLES[index]}评分目标",
                }
            ],
            "mandatory_requirements": [requirement],
            "compliance_constraints": [],
            "technical_requirements": [{"ref_id": f"TS-{index + 1}", "title": "技术规范要求"}],
            "content_plan": ["响应要求", "形成可检查内容"],
            "proposal_content_allowed": ["岗位职责", "质量控制", "实施计划"],
            "confirmed_facts": [],
            "cautious_references": [
                {"evidence_id": "E-CANDIDATE", "title": "张三人员简历", "usability_status": "candidate"}
            ],
            "forbidden_facts": [
                {"evidence_id": "E-MOCK", "title": "mock业绩", "usability_status": "unusable"}
            ],
            "missing_materials": [],
            "placeholders": ph_by_chapter[index],
            "drafting_instructions": [],
            "output_contract": {},
            "validation_checklist": [],
        }
        tasks.append(task)
        pack_chapters.append(
            {
                "chapter_id": chapter_id,
                "chapter_order": index + 1,
                "must_write_points": [f"{TITLES[index]}核心响应点"],
            }
        )
        outline_chapters.append(
            {
                "chapter_id": chapter_id,
                "claim_boundaries": {"forbidden": ["不得编造企业事实"]},
            }
        )
        context_chapters.append({"chapter_id": chapter_id, "chapter_title": TITLES[index]})

    common = {"schema_version": "1.0", "lot_name": LOT}
    context = {
        **common,
        "project": {"package_name": "未确认名称"},
        "project_conflicts": tasks[0]["input_context"]["project_conflicts"],
        "chapters": context_chapters,
    }
    pack = {**common, "chapters": pack_chapters}
    outline = {**common, "chapters": outline_chapters}
    evidence_map = {
        **common,
        "evidence_usage_items": [
            {
                "evidence_id": "E-CANDIDATE",
                "evidence_type": "personnel_resume",
                "title": "张三人员简历",
                "usability_status": "candidate",
                "allowed_usage": "cautious_reference",
            },
            {
                "evidence_id": "E-MOCK",
                "evidence_type": "project_performance",
                "title": "mock业绩",
                "usability_status": "unusable",
                "allowed_usage": "prohibited",
            },
        ],
    }
    guardrails = {
        **common,
        "forbidden_evidence_ids": ["E-MOCK"],
        "chapter_specific_guardrails": [
            {"chapter_id": chapter_id, "rules": ["不得编造企业事实"]} for chapter_id in chapter_ids
        ],
    }
    packets = {**common, "chapter_tasks": tasks}
    paths = drafting_input_paths(lot_dir)
    for key, value in {
        "technical_response_context": context,
        "writing_context_pack": pack,
        "technical_response_outline_for_agent": outline,
        "evidence_use_map": evidence_map,
        "draft_guardrails": guardrails,
        "chapter_task_packets": packets,
    }.items():
        dump(paths[key], value)
    return output_dir


def load_drafts(output_dir: Path) -> dict:
    return json.loads((output_dir / LOT / "drafts" / "chapter_drafts.json").read_text(encoding="utf-8"))


def build_preview(tmp_path: Path) -> tuple[Path, dict]:
    output_dir = write_stage4_inputs(tmp_path)
    build_technical_draft(LOT, output_dir=output_dir, preview=True)
    return output_dir, load_drafts(output_dir)


def chapter(drafts: dict, number: int) -> dict:
    return drafts["chapters"][number - 1]


def body(item: dict) -> str:
    return "\n".join(block["content"] for block in item["content_blocks"])


def test_confirmed_zero_still_generates_three_proposal_chapters(tmp_path):
    _, drafts = build_preview(tmp_path)
    proposal = [item for item in drafts["chapters"] if item["task_type"] == "proposal_drafting"]
    assert len(proposal) == 3
    assert all(item["generation_status"] == "generated_with_placeholders" for item in proposal)


def test_performance_chapter_does_not_invent_project_customer_or_contract(tmp_path):
    _, drafts = build_preview(tmp_path)
    text = body(chapter(drafts, 4))
    assert "合同金额为" not in text and "客户为" not in text and "已通过验收" not in text


def test_unconfirmed_project_manager_name_is_not_generated(tmp_path):
    _, drafts = build_preview(tmp_path)
    assert "张三" not in body(chapter(drafts, 2))


def test_unconfirmed_team_is_not_claimed_as_thirty_people(tmp_path):
    _, drafts = build_preview(tmp_path)
    text = body(chapter(drafts, 3))
    assert "已有30人" not in text and "现有30人" not in text


def test_missing_patents_do_not_create_specific_patent_facts(tmp_path):
    _, drafts = build_preview(tmp_path)
    text = body(chapter(drafts, 8))
    assert "专利名称：" not in text and "专利编号：" not in text


def test_missing_evaluation_does_not_create_existing_result(tmp_path):
    _, drafts = build_preview(tmp_path)
    assert "绩效评价结果为" not in body(chapter(drafts, 9))


def test_candidate_never_enters_confirmed_enterprise_claims(tmp_path):
    _, drafts = build_preview(tmp_path)
    claims = [claim for item in drafts["chapters"] for claim in item["claims"]]
    assert not [item for item in claims if item["claim_type"] == "confirmed_enterprise_fact"]


def test_mock_and_unusable_evidence_never_enter_body(tmp_path):
    _, drafts = build_preview(tmp_path)
    text = "\n".join(body(item) for item in drafts["chapters"])
    assert "mock业绩" not in text
    assert all(not item["evidence_ids_used"] for item in drafts["chapters"])


def test_blocked_chapters_generate_skeletons_and_placeholders(tmp_path):
    _, drafts = build_preview(tmp_path)
    blocked = [item for item in drafts["chapters"] if item["blocking_status"].startswith("blocked_by")]
    assert len(blocked) == 6
    assert all(item["generation_status"] == "partial" and item["placeholders"] for item in blocked)


def test_placeholder_ids_are_reused_from_stage_four(tmp_path):
    _, drafts = build_preview(tmp_path)
    actual = {item["placeholder_id"] for item in drafts["global_placeholders"]}
    assert actual == set(PLACEHOLDER_IDS)
    assert all(value in (tmp_path / "outputs" / LOT / "drafts" / "technical_response_draft.md").read_text(encoding="utf-8") for value in actual)


def test_package_name_conflict_uses_neutral_wording(tmp_path):
    _, drafts = build_preview(tmp_path)
    text = "\n".join(body(item) for item in drafts["chapters"])
    assert "工业数据管理技术研究" not in text
    assert "电力系统数据管理国际标准研究" not in text
    assert "本项目" in text


def test_proposal_statements_use_future_or_plan_language(tmp_path):
    _, drafts = build_preview(tmp_path)
    text = body(chapter(drafts, 5))
    assert any(term in text for term in ["拟", "将", "方案"])
    assert "我司已" not in text


def test_enterprise_fact_claim_requires_confirmed_evidence(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    inputs = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in drafting_input_paths(output_dir / LOT).items()}
    prompt = build_chapter_prompts(inputs)[0]
    draft = build_deterministic_chapter(prompt)
    draft["claims"].append(
        {
            "claim_id": "bad-claim",
            "claim_type": "confirmed_enterprise_fact",
            "evidence_ids": ["E-CANDIDATE"],
            "source_ids": [],
        }
    )
    result = validate_chapter_draft(draft, prompt)
    assert "bad-claim" in result["unsupported_claim_ids"]


def test_must_write_points_map_to_content_blocks(tmp_path):
    _, drafts = build_preview(tmp_path)
    assert all(all(point["covered"] for point in item["must_write_point_coverage"]) for item in drafts["chapters"])


def test_fatal_requirement_has_explicit_coverage(tmp_path):
    _, drafts = build_preview(tmp_path)
    coverage = chapter(drafts, 1)["requirement_coverage"][0]
    assert coverage["compliance_level"] == "fatal_if_missing"
    assert coverage["responded"] is True
    assert coverage["response_method"] == "explicit_response"


def test_invalid_model_json_is_not_saved_as_unchecked_text(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)

    class InvalidStructuredClient:
        is_mock = False

        def generate_structured(self, **_kwargs):
            raise ModelSchemaValidationError("invalid structured JSON")

    build_technical_draft(LOT, output_dir=output_dir, llm_client=InvalidStructuredClient())
    drafts = load_drafts(output_dir)
    assert drafts["draft_mode"] == "failed"
    assert all(item["generation_status"] == "failed" for item in drafts["chapters"])
    assert all(not item["content_blocks"] for item in drafts["chapters"])


def test_dry_run_does_not_call_model_or_write_drafts(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    class RecordingClient:
        is_mock = False

        def __init__(self):
            self.calls = 0

        def generate_structured(self, **_kwargs):
            self.calls += 1

    client = RecordingClient()
    result = build_technical_draft(LOT, output_dir=output_dir, dry_run=True, llm_client=client)
    assert result["draft_mode"] == "dry_run" and result["model_called"] is False
    assert client.calls == 0
    assert not (output_dir / LOT / "drafts").exists()


def test_markdown_is_declared_as_derived_not_data_source(tmp_path):
    output_dir, _ = build_preview(tmp_path)
    manifest = json.loads((output_dir / LOT / "drafts" / "draft_manifest.json").read_text(encoding="utf-8"))
    assert manifest["formal_source"] == "chapter_drafts.json"
    assert manifest["markdown_is_data_source"] is False


def test_context_only_receives_draft_paths_and_lightweight_chapter_indexes(tmp_path):
    output_dir, _ = build_preview(tmp_path)
    context = json.loads((output_dir / LOT / "outline" / "technical_response_context.json").read_text(encoding="utf-8"))
    assert context["chapter_drafts_path"].endswith("chapter_drafts.json")
    assert "draft_chapters" not in context and "content_blocks" not in context
    assert all("chapter_generation_status" in item and "chapter_draft_ids" in item for item in context["chapters"])


def test_output_has_no_bid_decision_conclusion(tmp_path):
    output_dir, _ = build_preview(tmp_path)
    text = (output_dir / LOT / "drafts" / "technical_response_draft.md").read_text(encoding="utf-8")
    forbidden = ["能投", "不能投", "建议投标", "不建议投标"]
    assert not any(term in text for term in forbidden)


def test_inspect_reports_status_words_and_coverage(tmp_path):
    output_dir, _ = build_preview(tmp_path)
    result = inspect_technical_draft(LOT, output_dir=output_dir)
    assert len(result["chapters"]) == 9
    assert result["report"]["score_items_covered"] == 9
    assert result["report"]["confirmed_enterprise_fact_claim_count"] == 0


def test_real_structured_client_success_produces_ai_generated(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)

    class SuccessfulStructuredClient:
        is_mock = False
        profile = SimpleNamespace(
            provider="volcengine_ark",
            protocol="openai_compatible",
            name="ark_draft",
            model="configured-model",
            api_mode="responses",
            send_thinking=True,
            extra_body={"thinking": {"type": "enabled"}},
            timeout_seconds=240,
            max_retries=2,
            max_output_tokens=8000,
        )

        def generate_structured(self, **kwargs):
            metadata = kwargs["request_metadata"]
            prompt = kwargs["user_prompt"]
            payload = json.loads(prompt.split("\n", 1)[1])
            task = payload["chapter_task"]
            score_ids = task["input_context"]["score_item_ids"]
            requirement_ids = task["input_context"]["requirement_ids"]
            placeholder_ids = [item["placeholder_id"] for item in task["placeholders"]]
            parsed = {
                "chapter_title": task["chapter_title"],
                "sections": [
                    {
                        "heading": "方案响应",
                        "block_type": "requirement_response",
                        "content": "本项目拟按要求组织实施，并保留所有待确认占位符。",
                        "covered_score_item_ids": score_ids,
                        "covered_requirement_ids": requirement_ids,
                        "placeholder_ids": placeholder_ids,
                    }
                ],
                "claims": [
                    {
                        "text": "本项目拟按要求组织实施。",
                        "claim_type": "proposal_statement",
                        "source_ids": score_ids + requirement_ids,
                        "evidence_ids": [],
                        "human_review_required": False,
                    }
                ],
                "placeholders_used": placeholder_ids,
                "score_items_covered": score_ids,
                "requirements_covered": requirement_ids,
                "warnings": [],
            }
            return ModelCallResult(
                provider="volcengine_ark",
                protocol="openai_compatible",
                profile_name="ark_draft",
                model="configured-model",
                api_mode="responses",
                request_id=f"request-{metadata['task_id']}",
                started_at="2026-01-01T00:00:00+00:00",
                completed_at="2026-01-01T00:00:01+00:00",
                duration_ms=1000,
                parsed_output=parsed,
                raw_output_available=True,
                finish_status="completed",
                retry_count=0,
                repair_attempted=False,
                usage={**empty_usage(), "reasoning_tokens": 10, "output_tokens": 20},
                warnings=[],
            )

    build_technical_draft(LOT, output_dir=output_dir, llm_client=SuccessfulStructuredClient())
    drafts = load_drafts(output_dir)
    assert drafts["draft_mode"] == "ai_generated"
    assert all(item["generation_status"] == "generated_with_placeholders" for item in drafts["chapters"])
    assert drafts["model_info"]["profile_name"] == "ark_draft"
    assert drafts["model_info"]["thinking_config"] == {"sent": True, "mode": "enabled"}
    assert drafts["model_info"]["timeout_seconds"] == 240
    assert drafts["model_info"]["max_retries"] == 2
    assert drafts["model_info"]["max_output_tokens"] == 8000
    assert drafts["model_info"]["requested_thinking_mode"] == "enabled"
    assert drafts["model_info"]["requested_max_output_tokens"] == 8000
    assert drafts["model_info"]["requested_timeout_seconds"] == 240
    assert drafts["model_info"]["actual_usage"] == {"reasoning_tokens": 90, "output_tokens": 180}


def test_explicit_preview_does_not_call_injected_real_client(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)

    class ExplodingClient:
        is_mock = False

        def generate_structured(self, **_kwargs):
            raise AssertionError("preview called the real client")

    result = build_technical_draft(LOT, output_dir=output_dir, preview=True, llm_client=ExplodingClient())
    assert result["draft_mode"] == "deterministic_preview"


def pilot_profile(name: str, *, thinking: str, max_output_tokens: int, timeout_seconds: int):
    return SimpleNamespace(
        name=name,
        provider="volcengine_ark",
        protocol="openai_compatible",
        model="configured-model",
        api_mode="responses",
        structured_output_mode="prompt_json",
        send_thinking=True,
        extra_body={"thinking": {"type": thinking}},
        timeout_seconds=timeout_seconds,
        max_retries=2,
        max_output_tokens=max_output_tokens,
    )


class PilotClient:
    is_mock = False

    def __init__(self, profile, output=None, error=None, *, reasoning_marker=None):
        self.profile = profile
        self.output = output
        self.error = error
        self.reasoning_marker = reasoning_marker
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        usage = {
            **empty_usage(),
            "input_tokens": 500,
            "output_tokens": 900,
            "reasoning_tokens": 300 if self.profile.extra_body["thinking"]["type"] == "enabled" else 0,
            "answer_tokens": 600,
            "total_tokens": 1400,
        }
        return ModelCallResult(
            provider=self.profile.provider,
            protocol=self.profile.protocol,
            profile_name=self.profile.name,
            model=self.profile.model,
            api_mode=self.profile.api_mode,
            request_id=f"request-{self.profile.name}",
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            duration_ms=1000,
            parsed_output=self.output,
            raw_output_available=True,
            finish_status="completed",
            retry_count=0,
            repair_attempted=False,
            usage=usage,
            warnings=[],
            response_received=True,
            response_status="completed",
            output_text_available=True,
            output_text_character_count=len(json.dumps(self.output, ensure_ascii=False)),
            processing_stages=[
                "request_started",
                "response_received",
                "output_items_inspected",
                "output_extracted",
                "json_parsed",
                "schema_validated",
            ],
        )


def pilot_outputs(*, add_enterprise_fact=False, remove_placeholder=False, remove_score=False, remove_requirement=False):
    content = "本项目拟建立分级服务组织、闭环服务流程、沟通汇报、质量控制、风险管理、安全保密及交付验收支持机制。"
    deep = {
        "chapter_title": "服务方案",
        "drafting_summary": "形成可执行的总体服务方案。",
        "sections": [
            {
                "heading": "服务组织与全过程保障",
                "content": content,
                "covered_score_item_ids": ["technical_05_score"],
                "covered_requirement_ids": ["REQ-5"],
                "placeholder_ids": ["PH-PKG"],
                "content_intent": "service_organization",
            }
        ],
        "proposal_commitments": ["我方将按要求组织实施。"],
        "placeholders_used": ["PH-PKG"],
        "score_items_covered": ["technical_05_score"],
        "requirements_covered": ["REQ-5"],
        "unresolved_items": ["包名称待确认"],
        "warnings": [],
    }
    normalized_content = content + ("我司已完成多个同类国网项目。" if add_enterprise_fact else "")
    normalized = {
        "chapter_title": "服务方案",
        "sections": [
            {
                "heading": "服务组织与全过程保障",
                "block_type": "service_commitment",
                "content": normalized_content,
                "covered_score_item_ids": [] if remove_score else ["technical_05_score"],
                "covered_requirement_ids": [] if remove_requirement else ["REQ-5"],
                "placeholder_ids": [] if remove_placeholder else ["PH-PKG"],
            }
        ],
        "claims": [
            {
                "text": normalized_content,
                "claim_type": "proposal_statement",
                "source_ids": ["technical_05_score", "REQ-5"],
                "evidence_ids": [],
                "human_review_required": False,
            }
        ],
        "placeholders_used": [] if remove_placeholder else ["PH-PKG"],
        "score_items_covered": [] if remove_score else ["technical_05_score"],
        "requirements_covered": [] if remove_requirement else ["REQ-5"],
        "warnings": [],
    }
    return deep, normalized


def test_two_stage_pilot_uses_independent_roles_and_schemas(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    chapter_id = "task_019_technical_05_服务方案"
    deep_output, normalized_output = pilot_outputs()
    draft_client = PilotClient(
        pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
        deep_output,
        reasoning_marker="hidden-reasoning-never-save",
    )
    normalize_client = PilotClient(
        pilot_profile("ark_fast", thinking="disabled", max_output_tokens=5000, timeout_seconds=180),
        normalized_output,
    )
    result = build_technical_draft_pilot(
        LOT,
        chapter_id,
        output_dir=output_dir,
        draft_client=draft_client,
        normalize_client=normalize_client,
    )
    assert result["pilot_status"] == "success"
    assert len(draft_client.calls) == 1 and len(normalize_client.calls) == 1
    assert draft_client.calls[0]["response_schema"]["title"] == "Technical Chapter Content Draft"
    assert normalize_client.calls[0]["response_schema"]["title"] == "Technical Chapter Draft Output"
    report = result["report"]
    assert report["deep_call"]["role"] == "drafting"
    assert report["deep_call"]["thinking_mode"] == "enabled"
    assert report["deep_call"]["reasoning_tokens"] == 300
    assert report["normalize_call"]["role"] == "fast_task"
    assert report["normalize_call"]["thinking_mode"] == "disabled"
    assert report["schema_valid"] is True
    assert report["placeholder_preservation"]["passed"] is True
    assert report["score_item_coverage"]["passed"] is True
    assert report["requirement_coverage"]["passed"] is True
    assert report["confirmed_enterprise_fact_count"] == 0
    assert report["unsupported_claim_count"] == 0
    assert not (output_dir / LOT / "drafts" / "chapter_drafts.json").exists()
    assert not (output_dir / LOT / "drafts" / "technical_response_draft.md").exists()
    pilot_dir = output_dir / LOT / "drafts" / "pilot" / "service_solution"
    assert (pilot_dir / "normalized_chapter_draft.json").exists()
    assert (pilot_dir / "service_solution_preview.md").exists()
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in pilot_dir.iterdir() if path.is_file())
    assert "hidden-reasoning-never-save" not in persisted
    assert "张三" not in persisted and "mock业绩" not in persisted
    assert not any(term in persisted for term in ["能投", "不能投", "建议投标", "不建议投标"])
    inspected = inspect_technical_draft_pilot(LOT, output_dir=output_dir)
    assert inspected["pilot_status"] == "success"


def test_deep_incomplete_output_stops_before_normalization(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    chapter_id = "task_019_technical_05_服务方案"
    error = ModelOutputIncompleteError(
        "provider returned no final structured text output",
        diagnostics={
            "failed_stage": "output_extraction",
            "request_id": "deep-incomplete",
            "response_received": True,
            "finish_status": "incomplete",
            "incomplete_details": {"reason": "length"},
            "usage": {**empty_usage(), "output_tokens": 16000, "reasoning_tokens": 16000, "answer_tokens": 0},
            "provider_request_ms": 3210,
            "repair_attempted": False,
            "output_text_available": False,
        },
    )
    draft_client = PilotClient(
        pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
        error=error,
    )
    normalize_client = PilotClient(
        pilot_profile("ark_fast", thinking="disabled", max_output_tokens=5000, timeout_seconds=180),
        output={},
    )
    result = build_technical_draft_pilot(
        LOT,
        chapter_id,
        output_dir=output_dir,
        draft_client=draft_client,
        normalize_client=normalize_client,
    )
    assert result["pilot_status"] == "failed"
    assert result["report"]["failed_stage"] == "deep_draft_output"
    assert result["report"]["error_type"] == "ModelOutputIncompleteError"
    assert result["report"]["deep_call"]["reasoning_tokens"] == 16000
    assert result["report"]["deep_call"]["duration_ms"] == 3210
    assert len(normalize_client.calls) == 0
    pilot_dir = output_dir / LOT / "drafts" / "pilot" / "service_solution"
    assert not (pilot_dir / "normalized_chapter_draft.json").exists()
    assert not (pilot_dir / "service_solution_preview.md").exists()


def test_normalization_fidelity_blocks_enterprise_fact_and_missing_ids(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    chapter_id = "task_019_technical_05_服务方案"
    deep_output, normalized_output = pilot_outputs(
        add_enterprise_fact=True,
        remove_placeholder=True,
        remove_score=True,
        remove_requirement=True,
    )
    draft_client = PilotClient(
        pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
        deep_output,
    )
    normalize_client = PilotClient(
        pilot_profile("ark_fast", thinking="disabled", max_output_tokens=5000, timeout_seconds=180),
        normalized_output,
    )
    result = build_technical_draft_pilot(
        LOT,
        chapter_id,
        output_dir=output_dir,
        draft_client=draft_client,
        normalize_client=normalize_client,
    )
    assert result["pilot_status"] == "failed"
    fidelity = result["report"]["content_fidelity"]
    assert fidelity["passed"] is False
    assert fidelity["missing_placeholder_ids"] == ["PH-PKG"]
    assert fidelity["missing_score_item_ids"] == ["technical_05_score"]
    assert fidelity["missing_requirement_ids"] == ["REQ-5"]
    assert result["report"]["enterprise_fact_fabrication_detected"] is True
    pilot_dir = output_dir / LOT / "drafts" / "pilot" / "service_solution"
    assert not (pilot_dir / "normalized_chapter_draft.json").exists()
    assert not (pilot_dir / "service_solution_preview.md").exists()


def test_pilot_requires_disabled_thinking_for_normalization_and_keeps_deep_result(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    chapter_id = "task_019_technical_05_服务方案"
    deep_output, normalized_output = pilot_outputs()
    draft_client = PilotClient(
        pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
        deep_output,
    )
    normalize_client = PilotClient(
        pilot_profile("ark_fast", thinking="enabled", max_output_tokens=5000, timeout_seconds=180),
        normalized_output,
    )
    result = build_technical_draft_pilot(
        LOT,
        chapter_id,
        output_dir=output_dir,
        draft_client=draft_client,
        normalize_client=normalize_client,
    )
    assert result["pilot_status"] == "failed"
    assert result["report"]["failed_stage"] == "normalize_output"
    assert len(draft_client.calls) == 1
    assert len(normalize_client.calls) == 0
    pilot_dir = output_dir / LOT / "drafts" / "pilot" / "service_solution"
    deep_result = json.loads((pilot_dir / "deep_draft_result.json").read_text(encoding="utf-8"))
    assert deep_result["generation_status"] == "generated"
    assert not (pilot_dir / "normalized_chapter_draft.json").exists()
    assert not (pilot_dir / "service_solution_preview.md").exists()


def test_failed_pilot_rerun_removes_stale_success_outputs(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    chapter_id = "task_019_technical_05_服务方案"
    deep_output, normalized_output = pilot_outputs()
    first = build_technical_draft_pilot(
        LOT,
        chapter_id,
        output_dir=output_dir,
        draft_client=PilotClient(
            pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
            deep_output,
        ),
        normalize_client=PilotClient(
            pilot_profile("ark_fast", thinking="disabled", max_output_tokens=5000, timeout_seconds=180),
            normalized_output,
        ),
    )
    assert first["pilot_status"] == "success"
    error = ModelOutputIncompleteError(
        "provider returned no final structured text output",
        diagnostics={
            "failed_stage": "output_extraction",
            "request_id": "deep-rerun-incomplete",
            "response_received": True,
            "finish_status": "incomplete",
            "incomplete_details": {"reason": "length"},
            "usage": {**empty_usage(), "output_tokens": 16000, "reasoning_tokens": 16000, "answer_tokens": 0},
            "repair_attempted": False,
            "output_text_available": False,
        },
    )
    second = build_technical_draft_pilot(
        LOT,
        chapter_id,
        output_dir=output_dir,
        draft_client=PilotClient(
            pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
            error=error,
        ),
        normalize_client=PilotClient(
            pilot_profile("ark_fast", thinking="disabled", max_output_tokens=5000, timeout_seconds=180),
            normalized_output,
        ),
    )
    assert second["pilot_status"] == "failed"
    pilot_dir = output_dir / LOT / "drafts" / "pilot" / "service_solution"
    assert not (pilot_dir / "normalized_chapter_draft.json").exists()
    assert not (pilot_dir / "service_solution_preview.md").exists()


def test_normalized_claim_can_come_from_deep_proposal_commitment(tmp_path):
    output_dir = write_stage4_inputs(tmp_path)
    deep_output, normalized_output = pilot_outputs()
    normalized_output["claims"][0]["text"] = deep_output["proposal_commitments"][0]
    result = build_technical_draft_pilot(
        LOT,
        "task_019_technical_05_服务方案",
        output_dir=output_dir,
        draft_client=PilotClient(
            pilot_profile("ark_draft", thinking="enabled", max_output_tokens=16000, timeout_seconds=600),
            deep_output,
        ),
        normalize_client=PilotClient(
            pilot_profile("ark_fast", thinking="disabled", max_output_tokens=5000, timeout_seconds=180),
            normalized_output,
        ),
    )
    assert result["pilot_status"] == "success"
    assert result["report"]["unsupported_claim_count"] == 0

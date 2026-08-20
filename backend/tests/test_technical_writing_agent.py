import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm_client import ToolModelResult, empty_usage
from app.llm_profiles import LLMProfile
from app.openai_compatible_llm_client import OpenAICompatibleLLMClient
from app.technical_agent_console import TechnicalAgentConsoleRenderer
from app.technical_agent_events import TechnicalAgentEventEmitter
from app.technical_placeholders import placeholder_id_for_field, placeholder_token
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
    chapter_run_status,
    normalize_chapter_draft_output,
    render_technical_chapter_markdown,
    repair_existing_technical_writing_run,
    validate_technical_chapter,
)
from app.technical_writing_tools import (
    TechnicalWritingToolContext,
    build_technical_tool_registry,
)
from app.web_research_tools import open_web_page, search_web, validate_public_url
from app.web_search_provider import (
    NotConfiguredSearchProvider,
    classify_unavailable_reason,
    get_web_search_provider,
)


BACKEND = Path(__file__).resolve().parents[1]
RESPONSE = BACKEND / "outputs/hospital_hci_001/outline/technical_response_outline.json"
WRITING = BACKEND / "outputs/hospital_hci_001/writing/technical_writing_outline.json"
TASKS = BACKEND / "outputs/hospital_hci_001/writing/tasks/task_information_requirements.json"
DRAFT_UNITS = BACKEND / "outputs/hospital_hci_001/writing/tasks/draft_units.json"
FAILED_OUTPUT_FIXTURE = BACKEND / "tests/fixtures/technical_writing_agent_failed_output.json"
HEADINGS = [
    "实施总体规划",
    "项目建设技术方案",
    "项目设计实施周期",
    "项目组织架构",
    "项目负责人及主要人员",
    "安装部署",
    "测试与试运行",
    "质量控制",
    "项目验收",
    "交付成果",
    "风险控制与应急保障",
]


class Endpoint:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeSDK:
    def __init__(self, responses):
        self.endpoint = Endpoint(responses)
        self.responses = self.endpoint
        self.chat = SimpleNamespace(completions=self.endpoint)


def profile(mode):
    return LLMProfile(
        name="tools",
        provider="fake",
        protocol="openai_compatible",
        base_url="https://example.test/v1",
        api_key_env="KEY",
        model_env="MODEL",
        api_key="secret",
        model="fake-model",
        api_mode=mode,
        structured_output_mode="json_schema",
        timeout_seconds=5,
        max_retries=0,
        max_output_tokens=1000,
        temperature=0,
        enabled=True,
        use_instructions=True,
        send_temperature=True,
        send_max_output_tokens=True,
        send_thinking=False,
        send_metadata=True,
        extra_body={},
        extra_headers={},
    )


def provider_client(mode, responses):
    sdk = FakeSDK(responses)
    client = OpenAICompatibleLLMClient(profile(mode), client_factory=lambda **_: sdk)
    return client, sdk


def tool_defs():
    return [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "lookup",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            },
        }
    ]


def test_chat_completions_tool_call_and_tool_result_message():
    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="lookup", arguments='{"q":"验收"}'),
    )
    response = SimpleNamespace(
        id="chat-1",
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]), finish_reason="tool_calls")],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
    )
    client, sdk = provider_client("chat_completions", [response])
    result = client.run_with_tools(
        messages=[
            {"role": "user", "content": "work"},
            {"role": "tool", "tool_call_id": "old", "name": "lookup", "content": '{"status":"ok"}'},
        ],
        tools=tool_defs(),
    )
    assert result.response_type == "tool_calls"
    assert result.tool_calls[0] == {"tool_call_id": "call-1", "name": "lookup", "arguments": {"q": "验收"}}
    assert sdk.endpoint.calls[0]["messages"][1]["role"] == "tool"


def test_responses_api_tool_call_and_function_output_message():
    response = SimpleNamespace(
        id="resp-1",
        output=[SimpleNamespace(type="function_call", call_id="call-2", name="lookup", arguments='{"q":"标准"}')],
        output_text="",
        status="completed",
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    client, sdk = provider_client("responses", [response])
    result = client.run_with_tools(
        messages=[
            {"role": "user", "content": "work"},
            {"role": "tool", "tool_call_id": "old", "name": "lookup", "content": '{"status":"ok"}'},
        ],
        tools=tool_defs(),
    )
    assert result.tool_calls[0]["name"] == "lookup"
    assert sdk.endpoint.calls[0]["input"][1]["type"] == "function_call_output"
    assert sdk.endpoint.calls[0]["tools"][0]["name"] == "lookup"


def test_provider_final_response_is_distinguished():
    response = SimpleNamespace(
        id="resp-final",
        output=[],
        output_text='{"status":"done"}',
        status="completed",
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    client, _ = provider_client("responses", [response])
    result = client.run_with_tools(messages=[{"role": "user", "content": "work"}], tools=tool_defs())
    assert result.response_type == "final"
    assert result.final_content == '{"status":"done"}'


class FakeMetadataStore:
    def load(self):
        return {
            "files": [
                {
                    "id": "person-1",
                    "original_name": "张三简历.docx",
                    "storage_key": "original/person-1.docx",
                    "parsed_text": "张三 项目实施 Python",
                    "resume_parse": {"person_name": "张三"},
                    "material_profile": {"material_group": "personnel", "material_types": ["resume"]},
                },
                {
                    "id": "mock-1",
                    "original_name": "mock_业绩文件_测试.docx",
                    "storage_key": "original/mock.docx",
                    "company_performance": {"project_name": "测试"},
                    "material_profile": {"material_group": "company_performance", "material_types": ["performance"]},
                },
            ]
        }

    def get_file(self, file_id):
        for item in self.load()["files"]:
            if item["id"] == file_id:
                return item
        raise KeyError(file_id)


def make_context(tmp_path, *, web_disabled=True, provider=None):
    return TechnicalWritingToolContext(
        response_outline_path=RESPONSE,
        writing_outline_path=WRITING,
        task_information_path=TASKS,
        draft_units_path=DRAFT_UNITS,
        confirmed_project_facts_path=None,
        output_root=tmp_path,
        output_key="hospital_hci_001",
        target_chapter_id="WC-003",
        target_section_id=None,
        metadata_store=FakeMetadataStore(),
        web_provider=provider or NotConfiguredSearchProvider(),
        web_disabled=web_disabled,
    )


def test_planning_tools_are_scoped_and_do_not_load_parameter_table(tmp_path):
    context = make_context(tmp_path)
    registry = build_technical_tool_registry(context)
    scope = registry.execute("get_writing_scope", {"chapter_id": "WC-003", "section_id": None})
    assert [item["title"] for item in scope["target_outline"]["children"]] == HEADINGS
    requirements = registry.execute("get_response_requirements", {"chapter_id": "WC-003", "response_point_ids": []})
    ids = {item["point_id"] for item in requirements["requirements"]}
    assert set(f"TR-{value:03d}" for value in range(5, 11)) <= ids
    assert "TR-018" not in ids
    assert "超融合系统\t" not in json.dumps(requirements, ensure_ascii=False)


def test_local_knowledge_returns_candidates_excludes_mock_and_never_confirms(tmp_path):
    registry = build_technical_tool_registry(make_context(tmp_path))
    result = registry.execute(
        "query_local_knowledge",
        {"query": "项目实施 人员", "evidence_types": ["person_resume", "implementation_experience"], "top_k": 10},
    )
    assert result["query_summary"]["semantic_vector_search_used"] is False
    assert all(item["selection_status"] == "candidate" for item in result["results"])
    assert all("mock_业绩" not in item["file_name"] for item in result["results"])
    assert any(item["file_id"] == "person-1" for item in result["results"])


def test_permissions_block_web_reference_only_and_arbitrary_paths(tmp_path):
    context = make_context(tmp_path, web_disabled=True)
    registry = build_technical_tool_registry(context)
    assert "web_search" not in registry.names()
    with pytest.raises(Exception):
        registry.execute("read_local_evidence", {"file_id": "mock-1"})
    with pytest.raises(Exception):
        registry.execute("load_existing_draft", {"path": "C:/Windows/win.ini"})
    assert context.draft_path.parent == (tmp_path.resolve() / "hospital_hci_001" / "drafts")


def test_web_policy_blocks_enterprise_people_and_performance_even_when_web_enabled(tmp_path):
    registry = build_technical_tool_registry(make_context(tmp_path, web_disabled=False))
    with pytest.raises(PermissionError):
        registry.execute(
            "web_search",
            {
                "query": "查询本公司人员和公司业绩",
                "purpose": "补全项目团队",
                "allowed_source_types": ["official_standard"],
                "top_k": 5,
            },
        )


def test_confirmed_project_facts_omits_values_without_confirmed_status(tmp_path):
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "facts": {
                    "project_manager": {"status": "candidate", "value": "张三"},
                    "project_name": {"status": "confirmed", "value": "医院项目"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    context.confirmed_project_facts_path = facts.resolve()
    result = context.get_confirmed_project_facts({})
    assert result["facts"] == {"project_name": "医院项目"}


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost/a", "http://127.0.0.1/a", "http://10.0.0.1/a", "http://[::1]/a"])
def test_web_page_ssrf_protection(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_web_provider_without_ark_responses_client_is_not_configured():
    provider = get_web_search_provider("ark_builtin", llm_client=None)
    assert provider.search("国家标准", {}) == {
        "status": "not_configured",
        "provider": "not_configured",
        "reason": "llm_client_not_available",
        "results": [],
    }


def test_ark_responses_builtin_web_search_request_and_citation_parsing():
    annotation = SimpleNamespace(
        type="url_citation",
        url="https://openstd.samr.gov.cn/example",
        title="国家标准信息",
        start_index=0,
        end_index=6,
    )
    source = SimpleNamespace(
        url="https://openstd.samr.gov.cn/example",
        title="国家标准信息",
        publisher="国家市场监督管理总局",
        published_at="2026-01-02",
    )
    response = SimpleNamespace(
        id="resp-web-1",
        output=[
            SimpleNamespace(
                type="web_search_call",
                id="ws-1",
                status="completed",
                action=SimpleNamespace(type="search", query="验收国家标准", sources=[source]),
            ),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="检索结果", annotations=[annotation])],
            ),
        ],
        output_text="检索结果",
        status="completed",
        usage=SimpleNamespace(input_tokens=10, output_tokens=8, total_tokens=18),
    )
    client, sdk = provider_client("responses", [response])
    client.profile = profile("responses")
    object.__setattr__(client.profile, "provider", "volcengine_ark")
    result = client.run_builtin_web_search(query="验收国家标准")
    assert sdk.endpoint.calls[0]["tools"] == [{"type": "web_search"}]
    assert sdk.endpoint.calls[0]["tool_choice"] == "required"
    assert result["status"] == "success"
    assert result["results"][0]["title"] == "国家标准信息"
    assert result["results"][0]["publisher"] == "国家市场监督管理总局"
    assert result["results"][0]["published_at"] == "2026-01-02"
    assert any(item.get("type") == "url_citation" for item in result["results"][0]["citations"])


def test_ark_tool_not_open_is_classified_as_account_activation_gap():
    class Error(Exception):
        def safe_diagnostics(self):
            return {
                "provider_error_code": "ToolNotOpen",
                "provider_error_message": "Your account has not activated web search.",
                "status_code": 404,
            }

    assert classify_unavailable_reason(Error("configured model or endpoint was not found")) == (
        "account_web_search_not_activated"
    )


def test_web_search_filters_to_allowed_official_source_types():
    class Provider:
        def search(self, query, options):
            return {
                "status": "success",
                "results": [
                    {"title": "标准", "url": "https://openstd.samr.gov.cn/a", "snippet": "x"},
                    {"title": "博客", "url": "https://example.com/a", "snippet": "x"},
                ],
            }

    result = search_web(
        Provider(),
        query="验收标准",
        purpose="定位国家标准",
        allowed_source_types=["standards_authority_official"],
        allowed_domains=[],
        recency_days=None,
        top_k=5,
    )
    assert [item["title"] for item in result["results"]] == ["标准"]
    assert result["results"][0]["source_type"] == "standards_authority_official"


class ScriptedModel:
    def __init__(self):
        self.turn = 0
        self.seen_messages = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.seen_messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        actions = [
            ("get_writing_scope", {"chapter_id": "WC-003", "section_id": None}),
            ("get_response_requirements", {"chapter_id": "WC-003", "response_point_ids": []}),
            ("get_task_information", {"chapter_id": "WC-003", "section_id": None}),
            (
                "query_local_knowledge",
                {"query": "实施经验 项目人员 质量管理", "evidence_types": ["implementation_experience", "person_resume", "person_certificate", "quality_management_material"], "top_k": 10},
            ),
            (
                "record_pending_confirmation",
                {
                    "items": [
                        {
                            "field": field,
                            "question": f"请确认{field}。",
                            "reason": "候选材料和招标要求均不是本项目确认事实。",
                            "candidates": [],
                            "affected_sections": ["项目设计实施周期", "项目负责人及主要人员", "安装部署"],
                            "blocking": True,
                        }
                        for field in [
                            "实际项目进度计划",
                            "最终交货承诺",
                            "最终安装周期",
                            "项目负责人",
                            "项目团队成员",
                        ]
                    ]
                },
            ),
        ]
        self.turn += 1
        metadata = {
            "provider": "scripted_fake",
            "model": "scripted",
            "api_mode": "fake",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        if self.turn <= len(actions):
            name, arguments = actions[self.turn - 1]
            return ToolModelResult(
                response_type="tool_calls",
                tool_calls=[{"tool_call_id": f"call-{self.turn}", "name": name, "arguments": arguments}],
                final_content=None,
                request_metadata=metadata,
            )
        return ToolModelResult(
            response_type="final",
            tool_calls=[],
            final_content=json.dumps(valid_wc003_draft(), ensure_ascii=False),
            request_metadata=metadata,
        )


def valid_wc003_draft():
    placeholder_fields = [
        "实际项目进度计划",
        "最终交货承诺",
        "最终安装周期",
        "项目负责人",
        "项目团队成员",
    ]
    placeholder_ids = {
        field: placeholder_id_for_field(field) for field in placeholder_fields
    }
    sections = []
    for heading in HEADINGS:
        content = (
            f"{heading}以形成可执行、可检查的实施安排为目标。在招标要求和现场条件完成核对后，"
            "各项工作按照明确的责任边界组织实施，说明输入条件、实施方法及关键控制点，并同步"
            "形成过程记录，确保方案内容能够追踪到具体动作和成果。\n\n"
            "实施过程中按阶段检查输入、操作结果和接口状态；遇到条件变化或检查不通过时，先登记"
            "问题并分析影响，完成处置和复核后再进入下一环节。最终以记录完整、问题关闭且成果"
            f"满足既定检查要求作为{heading}的完成判据。"
        )
        section_placeholders = []
        if heading == "项目设计实施周期":
            selected = placeholder_fields[:3]
            content += " 招标文件规定的期限仅作为需求基线，投标安排为" + "、".join(
                placeholder_token(placeholder_ids[field]) for field in selected
            ) + "。"
            section_placeholders.extend(placeholder_ids[field] for field in selected)
        if heading == "项目负责人及主要人员":
            selected = placeholder_fields[3:]
            content += " 人员安排为" + "和".join(
                placeholder_token(placeholder_ids[field]) for field in selected
            ) + "，候选简历不得自动采用。"
            section_placeholders.extend(placeholder_ids[field] for field in selected)
        sections.append(
            {
                "heading": heading,
                "block_type": "implementation_plan",
                "content": content,
                "covered_score_item_ids": ["SCORE-TR-022-R017"],
                "covered_requirement_ids": ["TR-022", *[f"TR-{value:03d}" for value in range(5, 11)]],
                "placeholder_ids": section_placeholders,
            }
        )
    return {
        "chapter_title": "实施部署方案",
        "sections": sections,
        "claims": [
            {
                "text": "招标文件对交货、安装和验收提出要求。",
                "claim_type": "tender_fact",
                "source_ids": [f"TR-{value:03d}" for value in range(5, 11)],
                "evidence_ids": [],
                "human_review_required": False,
            }
        ],
        "placeholders_used": list(placeholder_ids.values()),
        "score_items_covered": ["SCORE-TR-022-R017"],
        "requirements_covered": ["TR-022", *[f"TR-{value:03d}" for value in range(5, 11)]],
        "warnings": ["scripted fake model pilot; requires human review"],
    }


def chapter_schema():
    return json.loads(
        (BACKEND / "agents/technical_writer/chapter_draft_output_schema.json").read_text(encoding="utf-8")
    )


def wc003_validation_target():
    return {
        "chapter_id": "WC-003",
        "title": "实施部署方案",
        "writing_requirements": {
            "response_point_ids": ["TR-005", "TR-006", "TR-007", "TR-008", "TR-009", "TR-010", "TR-022"],
            "score_item_refs": ["SCORE-TR-022-R017"],
        },
        "children": [{"chapter_id": f"WC-003-{index:02d}", "title": title} for index, title in enumerate(HEADINGS, 1)],
    }


def test_failed_run_report_extracts_only_chapter_candidate_and_strips_report_fields():
    report = json.loads(FAILED_OUTPUT_FIXTURE.read_text(encoding="utf-8"))
    normalized = normalize_chapter_draft_output(report)
    assert set(normalized) == {
        "chapter_title",
        "sections",
        "claims",
        "placeholders_used",
        "score_items_covered",
        "requirements_covered",
        "warnings",
    }
    assert normalized["chapter_title"] == "实施部署方案"
    assert [item["heading"] for item in normalized["sections"]] == HEADINGS
    assert all(set(item) == {
        "heading",
        "block_type",
        "content",
        "covered_score_item_ids",
        "covered_requirement_ids",
        "placeholder_ids",
    } for item in normalized["sections"])
    assert not ({"chapter_id", "draft_status", "source_usage", "saved_path", "summary", "title"} & set(normalized))


def test_real_aliases_and_literal_placeholders_are_normalized_without_expected_id_injection():
    report = json.loads(FAILED_OUTPUT_FIXTURE.read_text(encoding="utf-8"))
    normalized = normalize_chapter_draft_output(report)
    assert normalized["sections"][0]["content"].startswith("响应TR-005")
    assert normalized["sections"][0]["placeholder_ids"] == ["【待人工确认：最终交货承诺】"]
    assert normalized["placeholders_used"] == ["【待人工确认：最终交货承诺】"]
    assert set(normalized["requirements_covered"]) == {
        "TR-005", "TR-006", "TR-007", "TR-008", "TR-009", "TR-010", "TR-022"
    }
    assert normalized["score_items_covered"] == []
    assert "SCORE-TR-022-R017" not in json.dumps(normalized, ensure_ascii=False)


def test_coverage_aggregates_standard_root_and_section_fields_but_missing_score_still_fails():
    report = json.loads(FAILED_OUTPUT_FIXTURE.read_text(encoding="utf-8"))
    normalized = normalize_chapter_draft_output(report)
    validation = validate_technical_chapter(
        normalized,
        schema=chapter_schema(),
        target=wc003_validation_target(),
        confirmed_facts={},
        pending_confirmations=report["pending_confirmations"],
    )
    assert validation["actual_section_titles"] == HEADINGS
    assert validation["missing_response_point_ids"] == []
    assert validation["missing_score_item_ids"] == ["SCORE-TR-022-R017"]
    assert validation["valid"] is False


def test_pending_confirmation_does_not_fabricate_placeholder_and_only_affects_readiness_when_valid():
    draft = valid_wc003_draft()
    draft["placeholders_used"] = []
    for section in draft["sections"]:
        section["content"] = re.sub(r"\{\{PH-[A-Z0-9-]+\}\}", "事实尚未确认", section["content"])
        section["placeholder_ids"] = []
    normalized = normalize_chapter_draft_output(draft)
    assert normalized["placeholders_used"] == []
    validation = validate_technical_chapter(
        normalized,
        schema=chapter_schema(),
        target=wc003_validation_target(),
        confirmed_facts={},
        pending_confirmations=[{"field": "project_manager"}],
    )
    assert validation["valid"] is True
    assert chapter_run_status(normalized, validation, [{"field": "project_manager"}]) == (
        "completed",
        "initial_draft_pending_confirmation",
        False,
    )


def test_markdown_renderer_uses_schema_heading_and_content():
    draft = valid_wc003_draft()
    validation = {"valid": True}
    markdown = render_technical_chapter_markdown(draft, wc003_validation_target(), validation)
    assert markdown.startswith("# 实施部署方案")
    assert "## 实施总体规划" in markdown
    assert "实施总体规划以形成可执行、可检查的实施安排为目标" in markdown
    assert "## None" not in markdown


def test_offline_repair_separates_model_output_and_chapter_draft_without_external_calls(tmp_path):
    run_path = tmp_path / "WC-003.run.json"
    md_path = tmp_path / "WC-003.md"
    run_path.write_text(FAILED_OUTPUT_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    md_path.write_text("# broken\n\n## None\n", encoding="utf-8")
    repaired = repair_existing_technical_writing_run(run_path, md_path)
    assert "structured_chapter_draft" not in repaired
    assert repaired["model_final_output"]["title"] == "实施部署方案"
    assert repaired["chapter_draft"]["chapter_title"] == "实施部署方案"
    assert repaired["status"] == "completed"
    assert repaired["draft_status"] == "reviewable_draft_with_blockers"
    assert repaired["candidate_generated"] is True
    assert repaired["candidate_saved"] is False
    assert repaired["candidate_available"] is False
    assert repaired["candidate_reviewable"] is True
    assert repaired["submission_ready"] is False
    assert repaired["repair_history"][-1] == {
        "type": "schema_contract_repair",
        "model_recalled": False,
        "web_recalled": False,
        "knowledge_requeried": False,
    }
    assert "## 实施总体规划" in md_path.read_text(encoding="utf-8")
    assert repaired["validation"]["missing_score_item_ids"] == ["SCORE-TR-022-R017"]


def test_wc003_agent_autonomous_loop_saves_candidate_and_formal_outputs(tmp_path):
    model = ScriptedModel()
    agent = build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=RESPONSE,
            writing_outline_path=WRITING,
            task_information_path=TASKS,
            draft_units_path=DRAFT_UNITS,
            confirmed_project_facts_path=None,
            target_chapter_id="WC-003",
            output_key="hospital_hci_001",
        ),
        llm=model,
        output_dir=tmp_path,
        metadata_store=FakeMetadataStore(),
        disable_web=True,
    )
    result = agent.run()
    assert result["status"] == "completed"
    assert result["draft_status"] == "initial_draft_with_placeholders"
    assert result["submission_ready"] is False
    assert [item["heading"] for item in result["chapter_draft"]["sections"]] == HEADINGS
    assert set(f"TR-{value:03d}" for value in range(5, 11)) <= set(result["response_point_usage"])
    assert "TR-018" not in json.dumps(result, ensure_ascii=False)
    assert result["run_summary"]["tool_names"][:4] == [
        "get_writing_scope",
        "get_response_requirements",
        "get_task_information",
        "query_local_knowledge",
    ]
    assert any(item.startswith("PH-") for item in result["placeholders"])
    assert result["candidate_generated"] is True
    assert result["candidate_saved"] is True
    assert result["candidate_available"] is True
    assert result["candidate_reviewable"] is True
    files = sorted(path.name for path in (tmp_path / "hospital_hci_001/drafts").iterdir())
    assert files == [
        "WC-003.candidate.json",
        "WC-003.candidate.md",
        "WC-003.md",
        "WC-003.run.json",
    ]
    assert "task_id" not in TechnicalWritingAgentInput.__dataclass_fields__
    assert "draft_unit_id" not in TechnicalWritingAgentInput.__dataclass_fields__
    schema_message = next(
        item for item in model.seen_messages[0]
        if item["role"] == "developer" and item["content"].startswith("[Output Schema]")
    )
    assert '"chapter_title"' in schema_message["content"]
    assert '"heading"' in schema_message["content"]


class BatchThenFinalModel:
    def __init__(self, *, final=True):
        self.turn = 0
        self.final = final
        self.tools_by_turn = []

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.turn += 1
        self.tools_by_turn.append([item["function"]["name"] for item in tools])
        metadata = {
            "provider": "scripted_fake",
            "profile_name": "fake",
            "model": "fake-model",
            "api_mode": "fake",
            "duration_ms": 2,
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        if self.turn == 1:
            return ToolModelResult(
                response_type="tool_calls",
                tool_calls=[
                    {
                        "tool_call_id": "batch-1",
                        "name": "get_writing_scope",
                        "arguments": {"chapter_id": "WC-003", "section_id": None},
                    },
                    {
                        "tool_call_id": "batch-2",
                        "name": "get_response_requirements",
                        "arguments": {"chapter_id": "WC-003", "response_point_ids": []},
                    },
                    {
                        "tool_call_id": "batch-3",
                        "name": "get_confirmed_project_facts",
                        "arguments": {},
                    },
                ],
                final_content=None,
                request_metadata=metadata,
            )
        if self.final:
            return ToolModelResult(
                response_type="final",
                tool_calls=[],
                final_content=json.dumps(valid_wc003_draft(), ensure_ascii=False),
                request_metadata=metadata,
            )
        return ToolModelResult(
            response_type="tool_calls",
            tool_calls=[
                {
                    "tool_call_id": f"late-{self.turn}",
                    "name": "get_writing_scope",
                    "arguments": {"chapter_id": "WC-003", "section_id": None},
                }
            ],
            final_content=None,
            request_metadata=metadata,
        )


def build_fake_agent(tmp_path, model, **kwargs):
    return build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=RESPONSE,
            writing_outline_path=WRITING,
            task_information_path=TASKS,
            draft_units_path=DRAFT_UNITS,
            target_chapter_id="WC-003",
            output_key="hospital_hci_001",
        ),
        llm=model,
        output_dir=tmp_path,
        metadata_store=FakeMetadataStore(),
        disable_web=True,
        **kwargs,
    )


def test_same_model_step_executes_multiple_tool_calls_with_matching_ids_and_events(tmp_path):
    events = []
    agent = build_fake_agent(tmp_path, BatchThenFinalModel())
    result = agent.run(event_handler=events.append)
    first_step = [item for item in result["tool_trace"] if item["step"] == 1]
    assert [item["tool_call_id"] for item in first_step] == ["batch-1", "batch-2", "batch-3"]
    assert result["run_summary"]["model_step_count"] == 3
    assert result["run_summary"]["tool_call_count"] == 3
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert all(item["run_id"] and item["timestamp"] and item["event_type"] for item in events)
    event_types = [item["event_type"] for item in events]
    assert event_types.index("model_call_started") < event_types.index("model_call_completed")
    assert event_types.index("tool_call_requested") < event_types.index("tool_call_started")
    assert event_types.index("tool_call_started") < event_types.index("tool_call_completed")


class OneInvalidOneValidModel:
    def __init__(self):
        self.turn = 0

    def run_with_tools(self, **kwargs):
        self.turn += 1
        metadata = {"provider": "fake", "usage": empty_usage()}
        if self.turn == 1:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": "bad",
                        "name": "get_response_requirements",
                        "arguments": {"chapter_id": "OTHER", "response_point_ids": []},
                    },
                    {
                        "tool_call_id": "good",
                        "name": "get_writing_scope",
                        "arguments": {"chapter_id": "WC-003", "section_id": None},
                    },
                ],
                None,
                metadata,
            )
        return ToolModelResult(
            "final",
            [],
            json.dumps(valid_wc003_draft(), ensure_ascii=False),
            metadata,
        )


def test_one_tool_failure_does_not_drop_other_batch_results(tmp_path):
    events = []
    result = build_fake_agent(tmp_path, OneInvalidOneValidModel()).run(event_handler=events.append)
    assert [item["status"] for item in result["tool_trace"]] == ["error", "success"]
    assert "tool_call_failed" in [item["event_type"] for item in events]
    assert result["chapter_draft"] is not None


class BudgetAndFinalizeModel:
    def __init__(self):
        self.turn = 0
        self.empty_tool_turns = []

    def run_with_tools(self, *, tools, **kwargs):
        self.turn += 1
        names = [item["function"]["name"] for item in tools]
        metadata = {"provider": "fake", "usage": empty_usage()}
        if not names:
            self.empty_tool_turns.append(self.turn)
            return ToolModelResult(
                "final",
                [],
                json.dumps(valid_wc003_draft(), ensure_ascii=False),
                metadata,
            )
        if self.turn <= 2:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": f"kb-{self.turn}",
                        "name": "query_local_knowledge",
                        "arguments": {
                            "query": f"实施质量材料 第{self.turn}轮",
                            "evidence_types": ["quality_management_material"],
                            "top_k": 3,
                        },
                    }
                ],
                None,
                metadata,
            )
        return ToolModelResult(
            "tool_calls",
            [
                {
                    "tool_call_id": f"pending-{self.turn}",
                    "name": "record_pending_confirmation",
                    "arguments": {
                        "items": [
                            {
                                "field": field,
                                "question": f"请确认{field}",
                                "reason": "未确认",
                                "blocking": False,
                            }
                            for field in [
                                "实际项目进度计划",
                                "最终交货承诺",
                                "最终安装周期",
                                "项目负责人",
                                "项目团队成员",
                            ]
                        ],
                    },
                }
            ],
            None,
            metadata,
        )


def test_budgets_skip_search_and_last_step_forces_final_without_tools(tmp_path):
    events = []
    model = BudgetAndFinalizeModel()
    agent = build_fake_agent(
        tmp_path,
        model,
        max_agent_steps=5,
        max_local_knowledge_calls=1,
        max_search_calls=1,
    )
    result = agent.run(event_handler=events.append)
    assert result["status"] == "completed"
    assert any(item.get("reason") == "local_knowledge_budget_exhausted" for item in result["tool_trace"])
    assert result["run_summary"]["local_knowledge_call_count"] == 1
    assert result["run_summary"]["forced_finalization"] is True
    # Turn 5 is the tool-free initial finalization.  A later Fresh repair turn
    # now uses the forced submit_structured_patch tool contract.
    assert model.empty_tool_turns == [5]
    assert "finalization_forced" in [item["event_type"] for item in events]
    assert model.turn == result["run_summary"]["model_step_count"]


class DuplicateModel:
    def __init__(self):
        self.turn = 0

    def run_with_tools(self, *, tools, **kwargs):
        self.turn += 1
        metadata = {"provider": "fake", "usage": empty_usage()}
        if not tools:
            return ToolModelResult("final", [], json.dumps(valid_wc003_draft(), ensure_ascii=False), metadata)
        if self.turn <= 2:
            return ToolModelResult(
                "tool_calls",
                [{"tool_call_id": f"same-{self.turn}", "name": "get_confirmed_project_facts", "arguments": {}}],
                None,
                metadata,
            )
        return ToolModelResult("final", [], json.dumps(valid_wc003_draft(), ensure_ascii=False), metadata)


def test_exact_duplicate_tool_call_is_detected_and_skipped(tmp_path):
    events = []
    result = build_fake_agent(tmp_path, DuplicateModel(), max_agent_steps=5).run(event_handler=events.append)
    assert result["tool_trace"][1]["status"] == "skipped"
    assert result["tool_trace"][1]["reason"] == "exact_duplicate_tool_call"
    assert result["run_summary"]["duplicate_tool_calls"] == 1
    assert "duplicate_tool_call_detected" in [item["event_type"] for item in events]


def test_max_steps_and_validation_failure_preserve_existing_markdown(tmp_path):
    draft_path = tmp_path / "hospital_hci_001/drafts/WC-003.md"
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text("# 已有正式草稿\n\n不得覆盖\n", encoding="utf-8")
    result = build_fake_agent(
        tmp_path,
        BatchThenFinalModel(final=False),
        max_agent_steps=4,
    ).run()
    assert result["status"] == "failed"
    assert result["stop_reason"] == "max_agent_steps"
    assert result["generation_completed"] is False
    assert result["candidate_available"] is False
    assert result["candidate_reviewable"] is False
    assert result["validation"]["validation_status"] == "fatal"
    assert result["validation"]["issues"][0]["category"] == "runtime"
    assert result["run_summary"]["draft_file_preserved"] is True
    assert draft_path.read_text(encoding="utf-8") == "# 已有正式草稿\n\n不得覆盖\n"

    invalid = valid_wc003_draft()
    invalid["chapter_title"] = "错误标题"

    class InvalidFinal:
        def run_with_tools(self, **kwargs):
            return ToolModelResult("final", [], json.dumps(invalid, ensure_ascii=False), {"provider": "fake", "usage": empty_usage()})

    second = build_fake_agent(tmp_path, InvalidFinal()).run()
    assert second["status"] == "completed"
    assert second["stop_reason"] == "candidate_validation_blocked"
    assert second["draft_status"] == "reviewable_draft_with_blockers"
    assert second["generation_completed"] is True
    assert second["candidate_available"] is True
    assert second["candidate_reviewable"] is True
    assert second["submission_ready"] is False
    assert second["failures"] == []
    assert second["candidate_generated"] is True
    assert second["candidate_saved"] is True
    assert second["candidate_available"] is True
    assert draft_path.read_text(encoding="utf-8") == "# 已有正式草稿\n\n不得覆盖\n"


def test_partial_working_draft_is_incomplete_and_no_failure_markdown_is_created(tmp_path):
    class PartialModel:
        def __init__(self):
            self.turn = 0

        def run_with_tools(self, *, tools, **kwargs):
            self.turn += 1
            metadata = {"provider": "fake", "usage": empty_usage()}
            if self.turn == 1:
                return ToolModelResult(
                    "tool_calls",
                    [
                        {
                            "tool_call_id": "save-1",
                            "name": "save_section_draft",
                            "arguments": {
                                "sections": [
                                    {
                                        "section_id": "WC-003-01",
                                        "title": HEADINGS[0],
                                        "content": "部分有效正文",
                                    }
                                ]
                            },
                        }
                    ],
                    None,
                    metadata,
                )
            return ToolModelResult(
                "tool_calls",
                [{"tool_call_id": f"late-{self.turn}", "name": "get_writing_scope", "arguments": {}}],
                None,
                metadata,
            )

    result = build_fake_agent(tmp_path, PartialModel(), max_agent_steps=4).run()
    assert result["status"] == "incomplete"
    assert result["draft_status"] == "partial_draft"
    assert result["working_draft"]["sections"][0]["title"] == HEADINGS[0]
    assert not (tmp_path / "hospital_hci_001/drafts/WC-003.md").exists()


def test_event_jsonl_is_valid_flushed_and_contains_no_prompt_or_credentials(tmp_path):
    event_path = tmp_path / "events.jsonl"
    observed_during_run = []

    def handler(event):
        if event_path.exists():
            observed_during_run.append(event_path.read_text(encoding="utf-8").count("\n"))

    result = build_fake_agent(tmp_path, BatchThenFinalModel()).run(
        event_handler=handler,
        event_log_path=event_path,
    )
    lines = event_path.read_text(encoding="utf-8").splitlines()
    values = [json.loads(line) for line in lines]
    assert observed_during_run and max(observed_during_run) >= 1
    assert len(values) == result["run_summary"]["event_count"]
    assert [item["sequence"] for item in values] == list(range(1, len(values) + 1))
    raw = event_path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert "Authorization" not in raw
    assert SYSTEM_PROMPT_SNIPPET not in raw


SYSTEM_PROMPT_SNIPPET = "你是技术标章节写作 Agent"


def test_plain_and_jsonl_console_renderers_emit_immediately(capsys):
    event = {
        "timestamp": "2026-07-23T15:20:01+08:00",
        "event_type": "tool_call_completed",
        "step": 1,
        "max_steps": 12,
        "phase": "preparing_context",
        "data": {"tool_name": "get_writing_scope", "duration_ms": 18, "result_count": 1},
    }
    TechnicalAgentConsoleRenderer(stream_format="plain", show_result_summary=True)(event)
    assert "get_writing_scope" in capsys.readouterr().out
    TechnicalAgentConsoleRenderer(stream_format="jsonl")(event)
    assert json.loads(capsys.readouterr().out)["event_type"] == "tool_call_completed"


def test_event_emitter_redacts_credentials_prompts_and_sensitive_candidate_values(tmp_path):
    event_path = tmp_path / "redacted.jsonl"
    emitter = TechnicalAgentEventEmitter(max_steps=4, event_log_path=event_path)
    event = emitter.emit(
        "tool_call_requested",
        phase="preparing_context",
        data={
            "api_key": "secret-key",
            "authorization": "Bearer secret-token",
            "system_prompt": "full prompt",
            "arguments_summary": {
                "entity_names": {"item_count": 2, "values_redacted": True},
            },
        },
    )
    emitter.close()
    raw = event_path.read_text(encoding="utf-8")
    assert "secret-key" not in raw
    assert "secret-token" not in raw
    assert "full prompt" not in raw
    assert event["data"]["arguments_summary"]["entity_names"]["values_redacted"] is True


def test_responses_stream_assembles_multiple_function_calls_and_text_delta():
    function_events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="function_call", id="item-1", call_id="call-1", name="lookup", arguments='{"q":"实施"}'),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="function_call", id="item-2", call_id="call-2", name="lookup", arguments='{"q":"验收"}'),
        ),
    ]
    client, sdk = provider_client("responses", [iter(function_events)])
    streamed = []
    result = client.run_with_tools(
        messages=[{"role": "user", "content": "work"}],
        tools=tool_defs(),
        options={"stream": True, "stream_event_handler": streamed.append},
    )
    assert [item["tool_call_id"] for item in result.tool_calls] == ["call-1", "call-2"]
    assert sdk.endpoint.calls[0]["stream"] is True

    text_events = [
        SimpleNamespace(type="response.output_text.delta", delta='{"chapter_'),
        SimpleNamespace(type="response.output_text.delta", delta='title":"x"}'),
    ]
    text_client, _ = provider_client("responses", [iter(text_events)])
    deltas = []
    final = text_client.run_with_tools(
        messages=[{"role": "user", "content": "work"}],
        tools=[],
        tool_choice="none",
        options={"stream": True, "stream_event_handler": deltas.append},
    )
    assert final.response_type == "final"
    assert final.final_content == '{"chapter_title":"x"}'
    assert [item["type"] for item in deltas] == [
        "text_delta",
        "text_delta",
        "response_completed",
    ]


def test_chat_stream_assembles_incremental_function_arguments():
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="chat-call",
                                function=SimpleNamespace(name="lookup", arguments='{"q":"'),
                            )
                        ],
                    )
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name=None, arguments='验收"}'),
                            )
                        ],
                    )
                )
            ]
        ),
    ]
    client, sdk = provider_client("chat_completions", [iter(chunks)])
    result = client.run_with_tools(
        messages=[{"role": "user", "content": "work"}],
        tools=tool_defs(),
        options={"stream": True},
    )
    assert result.tool_calls == [
        {"tool_call_id": "chat-call", "name": "lookup", "arguments": {"q": "验收"}}
    ]
    assert sdk.endpoint.calls[0]["stream"] is True


def test_all_sections_staged_triggers_early_finalization(tmp_path):
    class SaveAllThenFinal:
        def __init__(self):
            self.turn = 0
            self.saw_empty_tools = False

        def run_with_tools(self, *, tools, **kwargs):
            self.turn += 1
            metadata = {"provider": "fake", "usage": empty_usage()}
            if self.turn == 1:
                return ToolModelResult(
                    "tool_calls",
                    [
                        {
                            "tool_call_id": "save-all",
                            "name": "save_section_draft",
                            "arguments": {
                                "sections": [
                                    {
                                        "section_id": f"WC-003-{index:02d}",
                                        "title": title,
                                        "content": f"{title}工作草稿",
                                    }
                                    for index, title in enumerate(HEADINGS, 1)
                                ]
                            },
                        }
                    ],
                    None,
                    metadata,
                )
            # Keep the observation from the initial finalization; a later
            # Fresh repair call legitimately carries submit_structured_patch.
            self.saw_empty_tools = self.saw_empty_tools or not tools
            return ToolModelResult(
                "final",
                [],
                json.dumps(valid_wc003_draft(), ensure_ascii=False),
                metadata,
            )

    events = []
    model = SaveAllThenFinal()
    result = build_fake_agent(tmp_path, model, max_agent_steps=12).run(event_handler=events.append)
    assert model.turn == 3
    assert model.saw_empty_tools is True
    assert result["run_summary"]["section_count_completed"] == 11
    assert result["run_summary"]["model_step_count"] == 3
    assert "finalization_started" in [item["event_type"] for item in events]


def test_remaining_three_steps_blocks_new_search_even_if_fake_model_requests_it(tmp_path):
    class LateSearchModel:
        def __init__(self):
            self.turn = 0

        def run_with_tools(self, *, tools, **kwargs):
            self.turn += 1
            metadata = {"provider": "fake", "usage": empty_usage()}
            if self.turn < 3:
                return ToolModelResult(
                    "tool_calls",
                    [
                        {
                            "tool_call_id": f"scope-{self.turn}",
                            "name": "get_writing_scope",
                            "arguments": {"chapter_id": "WC-003", "section_id": None},
                        }
                    ],
                    None,
                    metadata,
                )
            if self.turn == 3:
                return ToolModelResult(
                    "tool_calls",
                    [
                        {
                            "tool_call_id": "late-search",
                            "name": "query_local_knowledge",
                            "arguments": {
                                "query": "后期不应继续检索",
                                "evidence_types": ["quality_management_material"],
                            },
                        }
                    ],
                    None,
                    metadata,
                )
            return ToolModelResult(
                "final",
                [],
                json.dumps(valid_wc003_draft(), ensure_ascii=False),
                metadata,
            )

    result = build_fake_agent(tmp_path, LateSearchModel(), max_agent_steps=5).run()
    late = next(item for item in result["tool_trace"] if item["tool_call_id"] == "late-search")
    assert late["status"] == "skipped"
    assert late["reason"] == "tool_not_available_in_current_finalization_phase"
    assert result["run_summary"]["local_knowledge_call_count"] == 0


def test_successful_commit_is_atomic_and_leaves_no_tmp_file(tmp_path):
    result = build_fake_agent(tmp_path, ScriptedModel()).run()
    draft = Path(result["outputs"]["markdown"])
    assert draft.is_file()
    assert not draft.with_name(draft.name + ".tmp").exists()
    assert not Path(result["outputs"]["run_json"] + ".tmp").exists()


def test_agent_forwards_genuine_model_deltas_without_storing_text_in_events(tmp_path):
    class DeltaModel:
        def run_with_tools(self, *, options, **kwargs):
            handler = options["stream_event_handler"]
            handler({"type": "text_delta", "delta": '{"chapter_title":'})
            handler({"type": "text_delta", "delta": '"实施部署方案"}'})
            return ToolModelResult(
                "final",
                [],
                json.dumps(valid_wc003_draft(), ensure_ascii=False),
                {"provider": "fake", "usage": empty_usage()},
            )

    events = []
    build_fake_agent(tmp_path, DeltaModel()).run(event_handler=events.append, stream_model=True)
    deltas = [item for item in events if item["event_type"] == "section_draft_delta"]
    assert len(deltas) == 2
    assert deltas[-1]["data"]["characters_generated"] > deltas[0]["data"]["characters_generated"]
    assert all("delta" not in item["data"] for item in deltas)


def test_rich_renderer_falls_back_to_plain_when_rich_console_is_unavailable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "rich.console", None)
    renderer = TechnicalAgentConsoleRenderer(stream_format="rich")
    assert renderer.stream_format == "plain"
    renderer(
        {
            "timestamp": "2026-07-23T15:20:01+08:00",
            "event_type": "agent_run_started",
            "step": 0,
            "max_steps": 12,
            "phase": "preparing_context",
            "data": {"target_id": "WC-003", "title": "实施部署方案"},
        }
    )
    assert "Agent启动" in capsys.readouterr().out

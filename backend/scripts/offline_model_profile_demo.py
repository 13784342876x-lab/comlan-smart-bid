from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm_client import ToolModelResult
from app.llm_profiles import (
    LLMProfile,
    ModelCapabilities,
    RequestMapping,
    resolve_profile,
)
from app.openai_compatible_llm_client import OpenAICompatibleLLMClient
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
)
from app.web_search_provider import get_web_search_provider


class FakeEndpoint:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeSDK:
    def __init__(self, responses):
        self.endpoint = FakeEndpoint(responses)
        self.responses = self.endpoint
        self.chat = SimpleNamespace(completions=self.endpoint)


def response(*, content=None, reasoning=None, call_id=None):
    tool_calls = []
    finish_reason = "stop"
    if call_id:
        finish_reason = "tool_calls"
        tool_calls = [
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name="lookup",
                    arguments='{"query":"offline evidence"}',
                ),
            )
        ]
    return SimpleNamespace(
        id="offline-response",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "offline lookup",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


def run_same_tool_loop(client: OpenAICompatibleLLMClient) -> tuple[ToolModelResult, list[dict]]:
    messages = [
        {"role": "system", "content": "[Agent System Rules]\nFollow the contract."},
        {"role": "developer", "content": "[Technical Writing Rules]\nUse evidence."},
        {"role": "developer", "content": "[Project Constraints]\nOffline only."},
        {"role": "developer", "content": "[Output Schema]\nReturn plain demo text."},
        {"role": "user", "content": "Run the offline demonstration."},
    ]
    first = client.run_with_tools(messages=messages, tools=TOOLS, tool_choice="auto")
    messages.extend(
        [
            first.context_message(),
            {
                "role": "tool",
                "tool_call_id": first.tool_calls[0]["tool_call_id"],
                "content": '{"status":"ok","source":"fake"}',
            },
        ]
    )
    final = client.run_with_tools(messages=messages, tools=TOOLS, tool_choice="none")
    return final, messages


def generic_profile() -> LLMProfile:
    return LLMProfile(
        name="generic_openai_compatible",
        provider="openai_compatible",
        protocol="openai_compatible",
        base_url="https://generic.example/v1",
        api_key_env="GENERIC_API_KEY",
        model_env="",
        api_key="offline-fake-key",
        model="generic-model",
        api_mode="chat_completions",
        structured_output_mode="prompt_json",
        timeout_seconds=5,
        max_retries=0,
        max_output_tokens=1000,
        temperature=0,
        enabled=True,
        use_instructions=False,
        send_temperature=True,
        send_max_output_tokens=True,
        send_thinking=False,
        send_metadata=False,
        extra_body={},
        extra_headers={},
        stream=False,
        capabilities=ModelCapabilities(
            supports_tools=True,
            supports_parallel_tool_calls=True,
            supports_streaming=True,
            supports_structured_output=True,
            supports_reasoning_content=False,
            requires_reasoning_content_replay=False,
            supports_developer_role=True,
            supports_tool_choice=True,
            supports_tool_choice_none=True,
            supports_usage_in_stream=True,
        ),
        request_mapping=RequestMapping(
            system_role="system",
            token_limit_parameter="max_completion_tokens",
            finalization_strategy="tool_choice_none",
            thinking_enabled=False,
        ),
    )


class EmptyMetadataStore:
    def load(self):
        return {"files": []}

    def get_file(self, file_id):
        raise KeyError(file_id)


class OfflineJsonRepairModel:
    def __init__(self):
        self.turn = 0
        self.tools_per_turn = []
        self.profile = generic_profile()

    def run_with_tools(self, *, messages, tools, **kwargs):
        self.turn += 1
        self.tools_per_turn.append([item["function"]["name"] for item in tools])
        metadata = {
            "provider": "offline_fake",
            "profile_name": self.profile.name,
            "model": self.profile.model,
            "api_mode": self.profile.api_mode,
            "finish_status": "stop",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            },
        }
        if self.turn == 1:
            return ToolModelResult(
                "tool_calls",
                [
                    {
                        "tool_call_id": "offline-scope",
                        "name": "get_writing_scope",
                        "arguments": {
                            "chapter_id": "OFFLINE-CH",
                            "section_id": None,
                        },
                    }
                ],
                None,
                {**metadata, "finish_status": "tool_calls"},
            )
        if self.turn == 2:
            return ToolModelResult(
                "final",
                [],
                '{"chapter_title":"离线章节","sections":[{"content":"未转义\n换行"}]}',
                metadata,
            )
        repaired = {
            "chapter_title": "离线章节",
            "sections": [
                {
                    "heading": "准备工作",
                    "block_type": "implementation_plan",
                    "content": "根据已取得的工具结果组织准备条件、职责边界和过程记录。",
                    "covered_score_item_ids": ["OFFLINE-SCORE"],
                    "covered_requirement_ids": ["OFFLINE-RP"],
                    "placeholder_ids": [],
                }
            ],
            "claims": [],
            "placeholders_used": [],
            "score_items_covered": ["OFFLINE-SCORE"],
            "requirements_covered": ["OFFLINE-RP"],
            "warnings": [],
        }
        return ToolModelResult(
            "final",
            [],
            json.dumps(repaired, ensure_ascii=False),
            metadata,
        )


def run_agent_json_repair_demo() -> dict:
    with tempfile.TemporaryDirectory(prefix="comlan-offline-json-repair-") as directory:
        root = Path(directory)
        response_path = root / "response.json"
        writing_path = root / "writing.json"
        tasks_path = root / "tasks.json"
        response_path.write_text(
            json.dumps(
                {
                    "chapters": [
                        {
                            "chapter_id": "OFFLINE-SRC",
                            "response_points": [
                                {
                                    "point_id": "OFFLINE-RP",
                                    "original_text": "说明准备工作。",
                                    "mandatory_marker": "required",
                                    "content_type": "narrative",
                                    "source_refs": [],
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        writing_path.write_text(
            json.dumps(
                {
                    "score_items": [
                        {
                            "score_item_id": "OFFLINE-SCORE",
                            "response_point_id": "OFFLINE-RP",
                        }
                    ],
                    "chapters": [
                        {
                            "chapter_id": "OFFLINE-CH",
                            "title": "离线章节",
                            "writing_requirements": {
                                "response_point_ids": ["OFFLINE-RP"],
                                "score_item_refs": ["OFFLINE-SCORE"],
                                "manual_project_facts": {"need": "none", "fact_types": []},
                                "web": {"need": "none", "allowed_source_types": [], "purposes": []},
                            },
                            "children": [
                                {
                                    "chapter_id": "OFFLINE-CH-01",
                                    "title": "准备工作",
                                    "writing_requirements": {
                                        "response_point_ids": ["OFFLINE-RP"],
                                        "score_item_refs": ["OFFLINE-SCORE"],
                                        "manual_project_facts": {
                                            "need": "none",
                                            "fact_types": [],
                                        },
                                        "web": {
                                            "need": "none",
                                            "allowed_source_types": [],
                                            "purposes": [],
                                        },
                                    },
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tasks_path.write_text('{"tasks":[]}', encoding="utf-8")
        model = OfflineJsonRepairModel()
        agent = build_technical_writing_agent(
            TechnicalWritingAgentInput(
                response_outline_path=response_path,
                writing_outline_path=writing_path,
                task_information_path=tasks_path,
                target_chapter_id="OFFLINE-CH",
                output_key="offline_case",
                web_search_policy="disabled",
            ),
            llm=model,
            output_dir=root / "outputs",
            metadata_store=EmptyMetadataStore(),
            disable_web=True,
        )
        result = agent.run()
        return {
            "status": result["status"],
            "repair_attempted": result["model_json_repair"]["attempted"],
            "repair_succeeded": result["model_json_repair"]["succeeded"],
            "tool_call_count": sum(
                1 for item in result["tool_trace"] if item.get("tool_name")
            ),
            "repair_turn_tools": model.tools_per_turn[-1],
            "section_count": len(result["chapter_draft"]["sections"]),
        }


def main() -> int:
    deepseek = resolve_profile(
        profile_name="deepseek_draft",
        environ={"DEEPSEEK_API_KEY": "offline-fake-key"},
        load_dotenv_file=False,
    )
    deepseek = replace(deepseek, stream=False)
    deepseek_sdk = FakeSDK(
        [
            response(reasoning="private reasoning never printed", call_id="deep-call"),
            response(content="场景一最终答案：fake 工具结果已回传。"),
        ]
    )
    deepseek_client = OpenAICompatibleLLMClient(
        deepseek,
        client_factory=lambda **_: deepseek_sdk,
    )
    deepseek_final, deepseek_messages = run_same_tool_loop(deepseek_client)
    replayed = deepseek_sdk.endpoint.calls[1]["messages"]
    replay_ok = any(
        item.get("role") == "assistant" and item.get("reasoning_content")
        for item in replayed
    )
    print("场景一")
    print(f"  Profile: {deepseek.name}")
    print(f"  最终答案: {deepseek_final.final_content}")
    print(f"  reasoning_content 已内部保存并回传: {replay_ok}")
    assert "private reasoning" not in str(deepseek_final.final_content)
    assert any("reasoning_content" in item for item in deepseek_messages)

    generic_sdk = FakeSDK(
        [
            response(call_id="generic-call"),
            response(content="Scenario 2 final answer from a generic compatible profile."),
        ]
    )
    generic_client = OpenAICompatibleLLMClient(
        generic_profile(),
        client_factory=lambda **_: generic_sdk,
    )
    generic_final, _ = run_same_tool_loop(generic_client)
    sent_roles = [item["role"] for item in generic_sdk.endpoint.calls[0]["messages"]]
    print("场景二")
    print(f"  Profile: {generic_client.profile.name}")
    print(f"  最终答案: {generic_final.final_content}")
    print(f"  developer role 保留: {'developer' in sent_roles}")
    print(f"  tool_choice 已发送: {'tool_choice' in generic_sdk.endpoint.calls[0]}")

    ark_profile = replace(
        generic_profile(),
        name="ark_web_search",
        provider="volcengine_ark",
        api_mode="responses",
        api_key_env="ARK_API_KEY",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="offline-ark-endpoint",
        request_mapping=RequestMapping(
            system_role="developer",
            token_limit_parameter="max_output_tokens",
            finalization_strategy="tool_choice_none",
        ),
    )
    ark_search_client = SimpleNamespace(
        profile=ark_profile,
        run_builtin_web_search=lambda **_: {
            "status": "success",
            "provider": "ark_builtin_web_search",
            "results": [],
        },
    )
    search_provider = get_web_search_provider(
        "ark_builtin",
        llm_client=deepseek_client,
        independent_llm_client=ark_search_client,
    )
    print("场景三")
    print(f"  主模型: {deepseek.name} ({deepseek.api_key_env})")
    print(f"  Web Search: ark_builtin ({search_provider.llm_client.profile.api_key_env})")
    print("  两套客户端与环境变量彼此独立: True")
    repair_demo = run_agent_json_repair_demo()
    print("场景四")
    print("  工具结果返回后收到轻微损坏JSON，通用受控修复成功")
    print(f"  repair轮工具数: {len(repair_demo['repair_turn_tools'])}")
    print(f"  最终章节数: {repair_demo['section_count']}")
    assert repair_demo == {
        "status": "completed",
        "repair_attempted": True,
        "repair_succeeded": True,
        "tool_call_count": 1,
        "repair_turn_tools": [],
        "section_count": 1,
    }
    print(json.dumps({"status": "offline_demo_passed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

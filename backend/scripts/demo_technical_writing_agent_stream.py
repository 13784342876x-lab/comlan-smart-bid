"""Offline WC-003-style event-stream demo. It never calls a real model, Web or KB."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.llm_client import ToolModelResult, empty_usage
from app.technical_agent_console import TechnicalAgentConsoleRenderer
from app.technical_placeholders import placeholder_id_for_field, placeholder_token
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
)


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


class DemoMetadataStore:
    def load(self) -> dict[str, Any]:
        return {
            "files": [
                {
                    "id": "demo-person-1",
                    "original_name": "演示候选人员简历.docx",
                    "storage_key": "demo/person-1.docx",
                    "parsed_text": "实施经验 质量管理 项目人员（仅演示候选）",
                    "resume_parse": {"person_name": "演示候选人"},
                    "material_profile": {"material_group": "personnel", "material_types": ["resume"]},
                }
            ]
        }

    def get_file(self, file_id: str) -> dict[str, Any]:
        return self.load()["files"][0]


class DemoWebProvider:
    name = "offline_demo"

    def search(self, query: str, options: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "provider": self.name,
            "results": [
                {
                    "title": "演示：官方验收标准来源",
                    "url": "https://www.samr.gov.cn/standards/demo",
                    "snippet": "离线脚本化结果，不是实际联网内容。",
                    "publisher": "演示主管机构",
                    "published_at": None,
                    "source_type": "standards_authority_official",
                    "retrieved_at": "2026-07-23T00:00:00+08:00",
                }
            ],
        }


class DemoModel:
    def __init__(self) -> None:
        self.turn = 0

    def run_with_tools(self, *, tools, **kwargs) -> ToolModelResult:
        self.turn += 1
        metadata = {
            "provider": "scripted_fake",
            "profile_name": "offline_demo",
            "model": "fake-streaming-model",
            "api_mode": "fake",
            "duration_ms": 12,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            },
        }
        if not tools:
            return ToolModelResult(
                "final",
                [],
                json.dumps(_valid_draft(), ensure_ascii=False),
                metadata,
            )
        actions: list[list[dict[str, Any]]] = [
            [
                _call("scope", "get_writing_scope", {"chapter_id": "WC-003", "section_id": None}),
                _call("requirements", "get_response_requirements", {"chapter_id": "WC-003", "response_point_ids": []}),
                _call("tasks", "get_task_information", {"chapter_id": "WC-003", "section_id": None}),
                _call("plan", "get_draft_plan", {"chapter_id": "WC-003", "section_id": None}),
                _call("facts", "get_confirmed_project_facts", {}),
            ],
            [
                _call(
                    "kb",
                    "query_local_knowledge",
                    {
                        "query": "实施经验 项目人员 质量管理",
                        "evidence_types": [
                            "implementation_experience",
                            "person_resume",
                            "person_certificate",
                            "quality_management_material",
                        ],
                        "top_k": 5,
                    },
                )
            ],
            [
                _call(
                    "web",
                    "web_search",
                    {
                        "query": "信息系统项目实施验收 国家标准",
                        "purpose": "验收标准",
                        "allowed_source_types": ["standards_authority_official"],
                        "allowed_domains": [],
                        "top_k": 3,
                    },
                )
            ],
            [
                _call(
                    "pending",
                    "record_pending_confirmation",
                    {
                        "items": [
                            {
                                "field": field,
                                "question": f"请确认{field}。",
                                "reason": "候选材料或招标期限不等于本项目确认事实。",
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
                _call(
                    "save",
                    "save_section_draft",
                    {
                        "sections": [
                            {
                                "section_id": f"WC-003-{index:02d}",
                                "title": title,
                                "content": f"{title}脚本化工作草稿。",
                            }
                            for index, title in enumerate(HEADINGS, 1)
                        ]
                    },
                ),
            ],
        ]
        return ToolModelResult("tool_calls", actions[self.turn - 1], None, metadata)


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"tool_call_id": call_id, "name": name, "arguments": arguments}


def _valid_draft() -> dict[str, Any]:
    requirements = ["TR-005", "TR-006", "TR-007", "TR-008", "TR-009", "TR-010", "TR-022"]
    fields = [
        "实际项目进度计划",
        "最终交货承诺",
        "最终安装周期",
        "项目负责人",
        "项目团队成员",
    ]
    placeholder_ids = [placeholder_id_for_field(field) for field in fields]
    sections = []
    for title in HEADINGS:
        content = f"本节为 {title} 的离线脚本化演示正文。"
        section_placeholders = []
        if title == "项目设计实施周期":
            section_placeholders = placeholder_ids[:3]
            content += "".join(placeholder_token(value) for value in section_placeholders)
        if title == "项目负责人及主要人员":
            section_placeholders = placeholder_ids[3:]
            content += "".join(placeholder_token(value) for value in section_placeholders)
        sections.append(
            {
                "heading": title,
                "block_type": "implementation_plan",
                "content": content,
                "covered_score_item_ids": ["SCORE-TR-022-R017"],
                "covered_requirement_ids": requirements,
                "placeholder_ids": section_placeholders,
            }
        )
    return {
        "chapter_title": "实施部署方案",
        "sections": sections,
        "claims": [],
        "placeholders_used": placeholder_ids,
        "score_items_covered": ["SCORE-TR-022-R017"],
        "requirements_covered": requirements,
        "warnings": ["offline scripted demonstration"],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="comlan-agent-demo-") as directory:
        output_root = Path(directory)
        agent = build_technical_writing_agent(
            TechnicalWritingAgentInput(
                response_outline_path=BACKEND / "outputs/hospital_hci_001/outline/technical_response_outline.json",
                writing_outline_path=BACKEND / "outputs/hospital_hci_001/writing/technical_writing_outline.json",
                task_information_path=BACKEND / "outputs/hospital_hci_001/writing/tasks/task_information_requirements.json",
                draft_units_path=BACKEND / "outputs/hospital_hci_001/writing/tasks/draft_units.json",
                target_chapter_id="WC-003",
                output_key="hospital_hci_001",
            ),
            llm=DemoModel(),
            output_dir=output_root,
            metadata_store=DemoMetadataStore(),
            web_provider=DemoWebProvider(),
            disable_web=False,
            max_agent_steps=12,
        )
        event_path = output_root / "hospital_hci_001/drafts/WC-003.events.jsonl"
        result = agent.run(
            event_handler=TechnicalAgentConsoleRenderer(
                stream_format="rich",
                show_model_usage=True,
                show_result_summary=True,
            ),
            event_log_path=event_path,
        )
        print(
            f"\n离线演示完成：status={result['status']}，"
            f"steps={result['run_summary']['model_step_count']}，"
            f"tools={result['run_summary']['tool_call_count']}"
        )


if __name__ == "__main__":
    main()

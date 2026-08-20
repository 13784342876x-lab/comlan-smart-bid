"""Offline, project-neutral two-round TechnicalWritingAgent revision demo."""

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
from app.technical_writing_agent import TechnicalWritingAgentInput, build_technical_writing_agent


class EmptyMetadataStore:
    def load(self) -> dict[str, Any]:
        return {"files": []}

    def get_file(self, file_id: str) -> dict[str, Any]:
        raise KeyError(file_id)


class FinalFakeModel:
    def __init__(self, *drafts: dict[str, Any]) -> None:
        self.drafts = list(drafts)
        self.index = 0

    def run_with_tools(self, **kwargs: Any) -> ToolModelResult:
        draft = self.drafts[min(self.index, len(self.drafts) - 1)]
        self.index += 1
        return ToolModelResult(
            response_type="final",
            tool_calls=[],
            final_content=json.dumps(draft, ensure_ascii=False),
            request_metadata={
                "provider": "scripted_fake",
                "model": "offline-revision-demo",
                "api_mode": "fake",
                "duration_ms": 5,
                "usage": {
                    **empty_usage(),
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )


def draft(revised: bool = False, duplicated: bool = False) -> dict[str, Any]:
    prepare = "说明启动条件、输入资料和责任边界。"
    execute = (
        "说明执行步骤、过程检查和成果交付。\n\n"
        "| 阶段 | 主要职责 | 交付成果 |\n"
        "| --- | --- | --- |\n"
        "| 执行 | 落实过程控制 | 过程记录 |"
    )
    if duplicated:
        repeated = (
            "项目按照统一流程完成准备、执行、检查和交付，明确责任边界、输入资料、完成判据"
            "与过程记录，并对发现的问题闭环整改，确保阶段成果满足进入下一阶段的条件。"
        ) * 2
        prepare = repeated
        execute = (
            repeated
            + "\n\n| 阶段 | 主要职责 | 交付成果 |\n"
            "| --- | --- | --- |\n"
            "| 执行 | 落实过程控制 | 过程记录 |"
        )
    if revised:
        prepare = "精简说明启动条件、输入资料、责任边界及检查条件。"
        execute = (
            "按阶段重新组织执行步骤，并用表格归纳职责和成果。\n\n"
            "| 阶段 | 主要职责 | 交付成果 |\n"
            "| --- | --- | --- |\n"
            "| 执行 | 落实过程控制 | 过程记录 |"
        )
    sections = [
        ("准备阶段", prepare),
        ("执行阶段", execute),
    ]
    return {
        "chapter_title": "项目交付计划",
        "sections": [
            {
                "heading": title,
                "block_type": "implementation_plan",
                "content": content,
                "covered_score_item_ids": ["SCORE-GENERIC-001"],
                "covered_requirement_ids": ["REQ-GENERIC-001"],
                "placeholder_ids": [],
            }
            for title, content in sections
        ],
        "claims": [],
        "placeholders_used": [],
        "score_items_covered": ["SCORE-GENERIC-001"],
        "requirements_covered": ["REQ-GENERIC-001"],
        "warnings": ["offline fake demonstration"],
    }


def write_inputs(root: Path) -> dict[str, Path]:
    values = {
        "response": {
            "chapters": [
                {
                    "chapter_id": "SOURCE-GENERIC-001",
                    "response_points": [
                        {
                            "point_id": "REQ-GENERIC-001",
                            "original_text": "应说明准备、执行和交付安排。",
                            "mandatory_marker": "required",
                            "content_type": "narrative",
                            "source_refs": [{"file": "generic-requirements.pdf", "page": 1}],
                            "table": {
                                "title": "阶段职责与交付成果表",
                                "headers": ["阶段", "主要职责", "交付成果"],
                                "rows": [],
                                "filling_requirements": ["按阶段顺序填写"],
                            },
                        }
                    ],
                }
            ]
        },
        "writing": {
            "score_items": [
                {
                    "score_item_id": "SCORE-GENERIC-001",
                    "response_point_id": "REQ-GENERIC-001",
                }
            ],
            "chapters": [
                {
                    "chapter_id": "CH-GENERIC-001",
                    "title": "项目交付计划",
                    "writing_requirements": {
                        "response_point_ids": ["REQ-GENERIC-001"],
                        "score_item_refs": ["SCORE-GENERIC-001"],
                        "manual_project_facts": {"need": "none", "fact_types": []},
                        "web": {"need": "forbidden", "allowed_source_types": [], "purposes": []},
                    },
                    "children": [
                        {
                            "chapter_id": f"CH-GENERIC-001-{index:02d}",
                            "title": title,
                            "writing_requirements": {
                                "response_point_ids": ["REQ-GENERIC-001"],
                                "score_item_refs": ["SCORE-GENERIC-001"],
                                "manual_project_facts": {"need": "none", "fact_types": []},
                                "web": {"need": "forbidden", "allowed_source_types": [], "purposes": []},
                            },
                            "children": [],
                        }
                        for index, title in enumerate(["准备阶段", "执行阶段"], 1)
                    ],
                }
            ],
        },
        "tasks": {"tasks": []},
    }
    paths = {}
    for name, value in values.items():
        path = root / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    return paths


def build(root: Path, paths: dict[str, Path], model: FinalFakeModel, instruction: str = ""):
    return build_technical_writing_agent(
        TechnicalWritingAgentInput(
            response_outline_path=paths["response"],
            writing_outline_path=paths["writing"],
            task_information_path=paths["tasks"],
            target_chapter_id="CH-GENERIC-001",
            output_key="generic_revision_demo",
            user_instruction=instruction,
            user_instruction_sources=({"type": "demo_cli"},) if instruction else (),
            user_instruction_summary=instruction,
            web_search_policy="disabled",
        ),
        llm=model,
        output_dir=root / "outputs",
        metadata_store=EmptyMetadataStore(),
        disable_web=True,
    )


def main() -> None:
    renderer = TechnicalAgentConsoleRenderer(stream_format="rich", show_model_usage=True)
    with tempfile.TemporaryDirectory(prefix="comlan-revision-demo-") as directory:
        root = Path(directory)
        paths = write_inputs(root)
        print("\n=== 首次生成 ===")
        first = build(
            root,
            paths,
            FinalFakeModel(draft(duplicated=True), draft()),
        ).run(event_handler=renderer)
        print("\n=== 第二轮：同一Agent + 用户意见 ===")
        instruction = "以后所有技术标都应先划分小节职责边界，再压缩重复内容。"
        second = build(root, paths, FinalFakeModel(draft(revised=True)), instruction).run(
            event_handler=renderer
        )
        print("\n首次生成规则与表格结果：")
        print(
            json.dumps(
                {
                    "writing_rules": first["writing_rules"],
                    "section_boundaries": first["rule_application_plan"]["section_boundaries"],
                    "tender_tables_reused": first["tender_tables_reused"],
                    "duplicate_repair_performed": first["duplicate_repair_performed"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print("\nchange_summary:")
        print(json.dumps(second["change_summary"], ensure_ascii=False, indent=2))
        print(
            "scope:",
            second["instruction_scope"],
            "suggestion:",
            second["instruction_scope_suggestion"],
            "persistence:",
            second["instruction_constraint_persistence"]["status"],
        )
        print(f"history: {second['previous_draft_path']}")
        print(f"current: {second['current_draft_path']}")
        assert first["status"] == second["status"] == "completed"
        assert first["duplicate_repair_performed"] is True
        assert len(first["tender_tables_reused"]) == 1
        assert second["instruction_scope"] == "once"
        assert second["instruction_scope_suggestion"] == "global"
        assert second["instruction_constraint_persistence"]["status"] == "not_requested"


if __name__ == "__main__":
    main()

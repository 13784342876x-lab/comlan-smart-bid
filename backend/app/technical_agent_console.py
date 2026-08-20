from __future__ import annotations

import json
from typing import Any


class TechnicalAgentConsoleRenderer:
    """Render events immediately without exposing prompts or tool result bodies."""

    def __init__(
        self,
        *,
        stream_format: str = "rich",
        show_model_usage: bool = False,
        show_result_summary: bool = False,
        verbose_events: bool = False,
    ) -> None:
        if stream_format not in {"rich", "plain", "jsonl"}:
            raise ValueError("stream_format must be rich, plain or jsonl")
        self.stream_format = stream_format
        self.show_model_usage = show_model_usage
        self.show_result_summary = show_result_summary
        self.verbose_events = verbose_events
        self._console = None
        if stream_format == "rich":
            try:
                from rich.console import Console

                self._console = Console()
            except Exception:
                self.stream_format = "plain"

    def __call__(self, event: dict[str, Any]) -> None:
        if self.stream_format == "jsonl":
            print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)
            return
        line = format_agent_event(
            event,
            show_model_usage=self.show_model_usage,
            show_result_summary=self.show_result_summary,
            verbose=self.verbose_events,
        )
        if not line:
            return
        if self._console is not None:
            self._console.print(line)
        else:
            print(_strip_rich_markup(line), flush=True)


def format_agent_event(
    event: dict[str, Any],
    *,
    show_model_usage: bool = False,
    show_result_summary: bool = False,
    verbose: bool = False,
) -> str:
    event_type = str(event.get("event_type") or "")
    data = event.get("data") or {}
    stamp = str(event.get("timestamp") or "")[11:19]
    step = int(event.get("step") or 0)
    max_steps = int(event.get("max_steps") or 0)
    prefix = f"[dim][{stamp}][/dim] "
    if event_type == "agent_run_started":
        return prefix + f"[bold cyan]Agent启动[/bold cyan]：{data.get('target_id')} {data.get('title', '')}"
    if event_type == "agent_phase_changed":
        return prefix + f"[bold]阶段[/bold]：{data.get('phase_label') or event.get('phase')}"
    if event_type == "writing_rules_loaded":
        return prefix + f"✓ 已加载长期写作规则 {data.get('version')}（{data.get('rule_count', 0)} 条）"
    if event_type == "project_constraints_loaded":
        return prefix + f"✓ 已加载项目持续约束 {data.get('constraint_count', 0)} 条"
    if event_type == "rule_application_plan_completed":
        return prefix + (
            f"✓ 已完成章节职责划分：{data.get('section_boundary_count', 0)} 个小节；"
            f"计划复用 {data.get('tender_table_reuse_count', 0)} 张招标表格，"
            f"可选自拟 {data.get('created_table_candidate_count', 0)} 张表"
        )
    if event_type == "tender_tables_detected":
        return prefix + f"检测到 {data.get('count', 0)} 张适用招标表格"
    if event_type == "instruction_scope_suggested":
        return prefix + f"本轮意见建议保存为 {data.get('suggested_scope')} 规则（未经确认不会持久化）"
    if event_type == "instruction_scope_persisted":
        return prefix + f"规则范围处理：{data.get('scope')} / {data.get('status')}"
    if event_type == "existing_draft_detected":
        return prefix + f"[cyan]检测到已有草稿[/cyan]：{data.get('path')}"
    if event_type == "user_instruction_loaded":
        return prefix + f"✓ 已读取用户意见，{data.get('character_count', 0)} 字符"
    if event_type == "revision_scope_analyzing":
        return prefix + "正在分析修改范围"
    if event_type == "revision_started":
        return prefix + f"[bold cyan]本轮为第 {data.get('revision_round')} 轮修改[/bold cyan]"
    if event_type == "full_chapter_regeneration_started":
        return prefix + "[bold magenta]正在重新生成完整章节[/bold magenta]"
    if event_type == "model_call_started":
        return prefix + f"[cyan]模型正在分析并选择下一步操作[/cyan]（步骤 {step}/{max_steps}）"
    if event_type == "model_call_completed":
        return prefix + f"✓ 模型响应完成，耗时 {data.get('duration_ms', 0)} ms，类型={data.get('response_type')}"
    if event_type == "model_call_failed":
        return prefix + f"[red]✗ 模型调用失败[/red]：{data.get('error_type')}"
    if event_type == "model_usage_updated":
        if not show_model_usage:
            return ""
        return prefix + (
            f"Token累计：输入 {data.get('input_tokens') or 0}，输出 {data.get('output_tokens') or 0}，"
            f"缓存 {data.get('cached_tokens') or 0}"
        )
    if event_type == "tool_call_requested":
        return prefix + f"→ [bold]{data.get('tool_name')}[/bold]"
    if event_type == "tool_call_started":
        if verbose:
            return prefix + f"  执行参数：{json.dumps(data.get('arguments_summary') or {}, ensure_ascii=False)}"
        return ""
    if event_type == "tool_call_completed":
        suffix = f"，结果 {data.get('result_count', 0)} 项" if show_result_summary else ""
        return prefix + f"✓ {data.get('tool_name')} 完成，耗时 {data.get('duration_ms', 0)} ms{suffix}"
    if event_type == "tool_call_failed":
        return prefix + f"[red]✗ {data.get('tool_name')} 失败[/red]：{data.get('error_type')}"
    if event_type == "tool_call_skipped":
        return prefix + f"[yellow]↷ 跳过 {data.get('tool_name')}[/yellow]：{data.get('reason')}"
    if event_type == "duplicate_tool_call_detected":
        return prefix + f"[yellow]⚠ 检测到重复调用 {data.get('tool_name')}[/yellow]"
    if event_type == "writing_started":
        return prefix + "[bold magenta]开始生成正文[/bold magenta]"
    if event_type == "section_draft_started":
        return prefix + f"正在生成：{data.get('section_title') or data.get('section_id')}"
    if event_type == "section_draft_delta":
        return prefix + f"正文累计：{data.get('characters_generated', 0)} 字符"
    if event_type == "section_draft_saved":
        return prefix + (
            f"✓ 工作草稿已暂存：{data.get('section_count_completed', 0)}/"
            f"{data.get('section_count_expected', 0)} 小节，{data.get('character_count', 0)} 字符"
        )
    if event_type == "pending_confirmation_recorded":
        return prefix + f"[yellow]⚠ 待人工确认累计 {data.get('pending_count', 0)} 项[/yellow]"
    if event_type == "duplicate_review_started":
        return prefix + "正在执行重复内容自检"
    if event_type == "duplicate_repair_started":
        return prefix + f"检测到 {data.get('duplicate_pair_count', 0)} 组严重重复，开始受控修正"
    if event_type == "duplicate_repair_completed":
        return prefix + "✓ 已自动压缩重复内容"
    if event_type == "duplicate_repair_failed":
        return prefix + "[yellow]⚠ 重复内容受控修正未应用[/yellow]"
    if event_type == "duplicate_review_completed":
        return prefix + f"✓ 重复内容自检完成，剩余 {data.get('duplicate_pair_count', 0)} 组"
    if event_type == "duplicate_review_skipped":
        return prefix + "跳过重复内容自检：未取得有效章节对象"
    if event_type == "tender_tables_reused":
        return prefix + f"复用 {data.get('count', 0)} 张招标表格"
    if event_type == "markdown_tables_created":
        return prefix + f"自拟 {data.get('count', 0)} 张普通表格"
    if event_type == "complex_tender_tables_preserved":
        return prefix + f"保留 {data.get('count', 0)} 张复杂原格式表格"
    if event_type in {"finalization_started", "finalization_forced"}:
        label = "强制最终化" if event_type.endswith("forced") else "开始组装最终结构化章节"
        return prefix + f"[bold blue]{label}[/bold blue]"
    if event_type == "structured_output_received":
        return prefix + "✓ 已接收结构化章节"
    if event_type == "model_json_repair_started":
        suffix = "（疑似输出截断）" if data.get("suspected_truncation") else ""
        return prefix + f"[yellow]检测到模型JSON无效，开始一次受控修复[/yellow]{suffix}"
    if event_type == "model_json_repair_completed":
        return prefix + "✓ 模型JSON受控修复成功"
    if event_type == "model_json_repair_failed":
        return prefix + "[red]✗ 模型JSON受控修复失败[/red]"
    if event_type == "validation_started":
        return prefix + (
            "[bold]正在重新校验[/bold]" if data.get("revision") else "[bold]开始校验[/bold]"
        )
    if event_type == "validation_completed":
        validation_status = str(data.get("validation_status") or "")
        issue_counts = data.get("issue_counts") or {}
        if validation_status == "fatal":
            marker, color = "✗", "red"
        elif validation_status == "blocked":
            marker, color = "⚠", "yellow"
        else:
            marker, color = "✓", "green"
        return prefix + (
            f"[{color}]{marker} 校验完成[/{color}]：小节 "
            f"{data.get('actual_section_count', 0)}/{data.get('expected_section_count', 0)}，"
            f"缺少响应点 {len(data.get('missing_response_point_ids') or [])}，"
            f"缺少评分项 {len(data.get('missing_score_item_ids') or [])}；"
            f"fatal {int(issue_counts.get('fatal') or 0)}，"
            f"blocking {int(issue_counts.get('blocking') or 0)}，"
            f"warning {int(issue_counts.get('warning') or 0)}"
        )
    if event_type == "validation_skipped":
        return prefix + "[yellow]未取得可校验章节对象，跳过章节业务校验[/yellow]"
    if event_type == "draft_file_committed":
        label = "已原子提交新版" if data.get("revision") else "正式草稿已原子提交"
        return prefix + f"✓ {label}：{data.get('path')}"
    if event_type == "candidate_artifact_saved":
        return prefix + (
            f"[green]✓ 候选稿已保存[/green]："
            f"Markdown {data.get('markdown_path')}；JSON {data.get('json_path')}"
        )
    if event_type == "candidate_artifact_write_failed":
        return prefix + "[red]✗ 候选稿保存失败[/red]"
    if event_type == "history_version_saved":
        return prefix + f"✓ 已保存上一版：{data.get('path')}"
    if event_type == "draft_file_preserved":
        return prefix + f"[yellow]⚠ 已保留原正式草稿[/yellow]：{data.get('reason')}"
    if event_type == "run_report_written":
        return prefix + f"运行报告：{data.get('path')}"
    if event_type in {"agent_run_completed", "agent_run_incomplete", "agent_run_failed"}:
        return prefix + (
            f"[bold]运行结束[/bold]：status={data.get('status')}，"
            f"draft_status={data.get('draft_status')}，stop_reason={data.get('stop_reason')}"
        )
    return prefix + event_type if verbose else ""


def _strip_rich_markup(value: str) -> str:
    import re

    return re.sub(r"\[/?(?:bold|dim|cyan|yellow|red|green|blue|magenta)(?:\s+[^\]]+)?\]", "", value)

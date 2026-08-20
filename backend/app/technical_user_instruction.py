from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MAX_INSTRUCTION_CHARACTERS = 40_000
MAX_INSTRUCTION_SUMMARY_CHARACTERS = 600


@dataclass(frozen=True)
class UserInstructionBundle:
    text: str
    sources: list[dict[str, Any]]
    summary: str

    @property
    def present(self) -> bool:
        return bool(self.text.strip())


def collect_user_instructions(
    *,
    instruction: str | None = None,
    instruction_file: str | Path | None = None,
    interactive: bool = False,
    input_fn: Callable[[], str] = input,
    prompt_fn: Callable[[str], Any] | None = None,
) -> UserInstructionBundle:
    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    if instruction and str(instruction).strip():
        value = sanitize_user_instruction(str(instruction))
        parts.append(value)
        sources.append({"type": "cli", "characters": len(value)})
    if instruction_file:
        path = Path(instruction_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"instruction file not found: {path}")
        value = sanitize_user_instruction(path.read_text(encoding="utf-8-sig"))
        if value:
            parts.append(value)
            sources.append(
                {
                    "type": "file",
                    "path": str(path),
                    "characters": len(value),
                }
            )
    if interactive:
        if prompt_fn:
            prompt_fn("请输入本轮修改意见；单独一行输入 END 结束：")
        lines: list[str] = []
        while True:
            try:
                line = input_fn()
            except EOFError:
                break
            if line.strip() == "END":
                break
            lines.append(line)
        value = sanitize_user_instruction("\n".join(lines))
        if value:
            parts.append(value)
            sources.append({"type": "interactive", "characters": len(value), "terminator": "END"})
    merged = "\n\n".join(part for part in parts if part).strip()
    if len(merged) > MAX_INSTRUCTION_CHARACTERS:
        merged = merged[:MAX_INSTRUCTION_CHARACTERS]
        sources.append(
            {
                "type": "runtime_limit",
                "truncated": True,
                "maximum_characters": MAX_INSTRUCTION_CHARACTERS,
            }
        )
    summary = re.sub(r"\s+", " ", merged).strip()[:MAX_INSTRUCTION_SUMMARY_CHARACTERS]
    return UserInstructionBundle(text=merged, sources=sources, summary=summary)


def sanitize_user_instruction(value: str) -> str:
    text = str(value or "").replace("\x00", "")
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[REDACTED-ID]", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[REDACTED-PHONE]", text)
    return text.strip()


def classify_revision_scope(instruction: str) -> str:
    value = str(instruction or "")
    structural_terms = (
        "结构", "章节边界", "重新组织", "重写", "压缩重复", "增加表格", "删除表格",
        "调整表格", "多个小节", "实施计划", "流程重组", "职责重组", "交付物重组",
    )
    local_terms = (
        "修改姓名", "替换姓名", "修改日期", "替换日期", "替换占位符", "确认占位符",
        "统一术语", "术语替换", "更正名称", "单个字段",
    )
    if any(term in value for term in structural_terms):
        return "structural_revision"
    if any(term in value for term in local_terms):
        return "local_fact_update"
    return "full_chapter_revision"


def detect_instruction_conflicts(instruction: str) -> list[dict[str, str]]:
    value = str(instruction or "")
    rules = (
        (r"(忽略|绕过|不遵守).{0,12}(招标|采购|硬性要求)", "instruction_conflicts_with_tender_requirements"),
        (r"(删除|取消).{0,12}(必选|强制|评分项|响应点)", "instruction_attempts_to_remove_mandatory_coverage"),
        (r"(编造|虚构|猜测).{0,12}(人员|业绩|资质|参数|承诺)", "instruction_requests_unconfirmed_fact"),
        (r"(关闭|跳过|不要).{0,12}(校验|Schema|响应点检查|评分项检查)", "instruction_conflicts_with_validation"),
    )
    conflicts = []
    for pattern, code in rules:
        if re.search(pattern, value, flags=re.IGNORECASE):
            conflicts.append(
                {
                    "code": code,
                    "action": "not_applied",
                    "reason": "用户意见低于招标硬性要求、事实边界和输出校验约束。",
                }
            )
    return conflicts


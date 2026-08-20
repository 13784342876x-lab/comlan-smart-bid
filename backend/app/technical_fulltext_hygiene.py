from __future__ import annotations

import hashlib
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.technical_fulltext_auditor import (
    TechnicalFulltextAuditError,
    TechnicalFulltextAuditor,
)
from app.technical_fulltext_composer import (
    atomic_write_json,
    atomic_write_markdown,
    load_json_object,
    sha256_file,
    stable_json_sha256,
    utc_now,
    inspect_technical_fulltext,
)


HYGIENE_SCHEMA_VERSION = "technical_fulltext_hygiene_v1"
CLEANED_SCHEMA_VERSION = "technical_fulltext_cleaned_v1"
HYGIENE_NOTICE = "Stage 11-E 全文机械质量清理报告"
CLEANED_PREVIEW_NOTICE = "Stage 11-E 清理后全文工作稿（非 Stage 10 committed artifact）"

JINJA_TAG = re.compile(r"\{%\s*(?P<body>.*?)\s*%\}", re.DOTALL)
SAFE_JINJA_TAG = re.compile(r"\{%\s*(?:if\s+[^%]+|endif)\s*%\}", re.IGNORECASE)
MARKDOWN_HEADING_REPEAT = re.compile(
    r"(?m)^(?P<heading>#{1,6}[ \t]+[^\n]+)\n[ \t]*(?P=heading)(?=\n|$)"
)
SAFE_MALFORMED_RESIDUE = re.compile(r"以[A-Za-z]\）[，,]?[ \t]*(?=实现)")
GENERIC_MALFORMED_RESIDUE = re.compile(r"(?<=[\u3400-\u9fff])[A-Za-z]\）")
MOJIBAKE_RESIDUE = re.compile(r"�|锟斤拷")
PLACEHOLDER_TOKEN = re.compile(r"\{\{\s*[A-Za-z0-9_.:-]+\s*\}\}")
REQUIREMENT_TOKEN = re.compile(r"\bTR-[A-Za-z0-9._:-]+\b", re.IGNORECASE)
SCORE_TOKEN = re.compile(r"\bSCORE-[A-Za-z0-9._:-]+\b", re.IGNORECASE)
PROTECTED_VALUE_TOKEN = re.compile(
    r"(?:\bGB/T\s*\d+(?:[.\-]\d+)*\b|"
    r"\bISO\s*\d+(?:[.\-]\d+)*\b|"
    r"\d+(?:\.\d+)?\s*[×xX]\s*\d+(?:\.\d+)?\s*(?:小时|时|天|日|年|月)?|"
    r"\d+(?:\.\d+)?\s*(?:%|％|小时|时|天|日|年|月|GB|TB|MB|KB|Gbps|Mbps|Kbps|MHz|GHz|版本))",
    re.IGNORECASE,
)


class TechnicalFulltextHygieneError(RuntimeError):
    """Raised when Stage 11-E cannot trust, validate, or persist its result."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: Any) -> str:
    return prefix + stable_json_sha256(value)[:16].upper()


def _counter(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(match.group(0) for match in pattern.finditer(text))


def _protected_signature(text: str) -> dict[str, Counter[str]]:
    return {
        "placeholders": _counter(PLACEHOLDER_TOKEN, text),
        "requirements": _counter(REQUIREMENT_TOKEN, text),
        "score_items": _counter(SCORE_TOKEN, text),
        "protected_values": _counter(PROTECTED_VALUE_TOKEN, text),
    }


def _is_subsequence(candidate: str, source: str) -> bool:
    iterator = iter(source)
    return all(character in iterator for character in candidate)


def _paragraph_spans(content: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    paragraph_index = 0
    for separator in list(re.finditer(r"\n[ \t]*\n+", content)) + [None]:
        end = separator.start() if separator is not None else len(content)
        raw = content[cursor:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = cursor + leading
        stop = end - trailing
        if start < stop:
            paragraph_index += 1
            spans.append((start, stop, content[start:stop]))
        cursor = separator.end() if separator is not None else len(content)
    return spans


def _paragraph_id(chapter_id: str, section_id: str, paragraph_index: int) -> str:
    return "PAR-" + stable_json_sha256({
        "chapter_id": chapter_id,
        "section_id": section_id,
        "paragraph_index": paragraph_index,
    })[:16].upper()


def _excerpt(text: str, start: int, end: int, radius: int = 100) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)].strip()


def _contains_protected_token(text: str) -> bool:
    signature = _protected_signature(text)
    return any(signature[name] for name in signature)


def _find_adjacent_repeat(
    text: str,
    *,
    minimum: int,
    maximum: int,
    phrase_only: bool,
) -> tuple[int, int, str] | None:
    for start in range(len(text)):
        upper = min(maximum, (len(text) - start) // 2)
        for length in range(upper, minimum - 1, -1):
            unit = text[start:start + length]
            if unit != text[start + length:start + 2 * length]:
                continue
            if _contains_protected_token(unit):
                continue
            cjk_count = len(re.findall(r"[\u3400-\u9fff]", unit))
            if phrase_only:
                if cjk_count != len(unit) or cjk_count < minimum:
                    continue
            elif cjk_count < 8 or len(set(unit)) < 5:
                continue
            return start, length, unit
    return None


def _content_text(fulltext: dict[str, Any]) -> str:
    return "\n".join(
        str(section.get("content") or "")
        for chapter in fulltext.get("chapters") or []
        for section in chapter.get("sections") or []
        if isinstance(section, dict)
    )


def _metadata_projection(fulltext: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(fulltext)
    for chapter in projected.get("chapters") or []:
        for section in chapter.get("sections") or []:
            if isinstance(section, dict):
                section["content"] = "<CONTENT>"
    return projected


class TechnicalFulltextHygiene:
    """Apply only high-confidence, deletion-only mechanical repairs to Stage 10 text."""

    def __init__(self, *, schema_path: str | Path | None = None):
        self.schema_path = (
            Path(schema_path).expanduser().resolve()
            if schema_path
            else Path(__file__).resolve().parent.parent
            / "schemas"
            / "technical_fulltext_hygiene_v1.json"
        )

    def run(
        self,
        *,
        fulltext_json_path: str | Path,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        requested_path = Path(fulltext_json_path).expanduser().resolve()
        try:
            fulltext, commit, commit_path, source_sha = (
                TechnicalFulltextAuditor._load_committed_snapshot(requested_path)
            )
        except (TechnicalFulltextAuditError, OSError, ValueError) as exc:
            raise TechnicalFulltextHygieneError(
                f"committed fulltext snapshot is unavailable: {exc}"
            ) from exc

        try:
            inspect_technical_fulltext(requested_path)
        except Exception as exc:
            raise TechnicalFulltextHygieneError(
                f"Stage 10 committed source integrity validation failed: {exc}"
            ) from exc
        cleaned, findings, repairs, unresolved, validation = self.clean_document(fulltext)
        cleaned_content_sha = stable_json_sha256(cleaned)
        cleaned_document_id = (
            f"technical-fulltext-cleaned:{fulltext['output_key']}:"
            f"{cleaned_content_sha[:16]}"
        )
        cleaned_artifact = {
            "schema_version": CLEANED_SCHEMA_VERSION,
            "cleaned_document_id": cleaned_document_id,
            "source_document_id": fulltext["document_id"],
            "output_key": fulltext["output_key"],
            "source_fulltext_sha256": source_sha,
            "cleaned_content_sha256": cleaned_content_sha,
            "release_ready": False,
            "fulltext": cleaned,
            "generated_at": utc_now(),
        }
        status = "completed_with_unresolved" if unresolved else "completed"
        ready = not any(bool(item.get("gate_blocking")) for item in unresolved)
        rule_counts = Counter(item["repair_type"] for item in repairs)
        report = {
            "schema_version": HYGIENE_SCHEMA_VERSION,
            "hygiene_id": _stable_id("HYG-", {
                "source_sha256": source_sha,
                "cleaned_content_sha256": cleaned_content_sha,
                "repair_ids": [item["repair_id"] for item in repairs],
                "unresolved_ids": [item["finding_id"] for item in unresolved],
            }),
            "document_id": fulltext["document_id"],
            "output_key": fulltext["output_key"],
            "source_fulltext": {
                "path": str(requested_path),
                "sha256": source_sha,
                "schema_version": fulltext["schema_version"],
            },
            "source_commit": {
                "path": str(commit_path),
                "sha256": sha256_file(commit_path),
                "schema_version": commit.get("schema_version"),
            },
            "status": "completed",
            "hygiene_status": status,
            "human_review_hygiene_ready": ready,
            "release_ready": False,
            "detector_version": "fulltext_hygiene_rules_v1",
            "summary": {
                "finding_count": len(findings),
                "repair_count": len(repairs),
                "unresolved_count": len(unresolved),
                "repair_counts_by_type": dict(sorted(rule_counts.items())),
                "model_call_count": 0,
            },
            "hygiene_findings": findings,
            "hygiene_repairs": repairs,
            "unresolved_hygiene_findings": unresolved,
            "protection_validation": validation,
            "cleaned_fulltext": {
                "cleaned_document_id": cleaned_document_id,
                "cleaned_content_sha256": cleaned_content_sha,
            },
            "model_usage": {
                "model_call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "generated_at": utc_now(),
        }
        self._validate_report(report)

        if sha256_file(requested_path) != source_sha:
            raise TechnicalFulltextHygieneError(
                "technical fulltext changed while hygiene was running"
            )
        if dry_run:
            return {
                "status": report["status"],
                "hygiene_status": status,
                "human_review_hygiene_ready": ready,
                "release_ready": False,
                "summary": report["summary"],
                "hygiene": report,
                "cleaned_artifact": cleaned_artifact,
                "outputs": {},
            }

        output_dir = requested_path.parent / "hygiene"
        report_json = output_dir / "technical_fulltext.hygiene.json"
        report_md = output_dir / "technical_fulltext.hygiene.md"
        cleaned_json = output_dir / "technical_fulltext.cleaned.json"
        cleaned_md = output_dir / "technical_fulltext.cleaned.preview.md"
        report_markdown = self._render_report(report)
        cleaned_markdown = self._render_cleaned_preview(cleaned, report)
        try:
            report_sha = atomic_write_json(
                report_json,
                report,
                required_fields=(
                    "schema_version", "hygiene_id", "hygiene_status",
                    "human_review_hygiene_ready", "hygiene_repairs",
                ),
            )
            report_md_sha = atomic_write_markdown(
                report_md, report_markdown, required_text=HYGIENE_NOTICE
            )
            cleaned_sha = atomic_write_json(
                cleaned_json,
                cleaned_artifact,
                required_fields=(
                    "schema_version", "cleaned_document_id", "source_document_id",
                    "cleaned_content_sha256", "fulltext",
                ),
            )
            cleaned_md_sha = atomic_write_markdown(
                cleaned_md, cleaned_markdown, required_text=CLEANED_PREVIEW_NOTICE
            )
        except (OSError, ValueError) as exc:
            raise TechnicalFulltextHygieneError(
                f"hygiene artifact persistence failed: {type(exc).__name__}: {exc}"
            ) from exc
        return {
            "status": report["status"],
            "hygiene_status": status,
            "human_review_hygiene_ready": ready,
            "release_ready": False,
            "summary": report["summary"],
            "hygiene": report,
            "cleaned_artifact": cleaned_artifact,
            "outputs": {
                "technical_fulltext_hygiene_json": str(report_json.resolve()),
                "technical_fulltext_hygiene_json_sha256": report_sha,
                "technical_fulltext_hygiene_markdown": str(report_md.resolve()),
                "technical_fulltext_hygiene_markdown_sha256": report_md_sha,
                "technical_fulltext_cleaned_json": str(cleaned_json.resolve()),
                "technical_fulltext_cleaned_json_sha256": cleaned_sha,
                "technical_fulltext_cleaned_preview": str(cleaned_md.resolve()),
                "technical_fulltext_cleaned_preview_sha256": cleaned_md_sha,
                "cleaned_content_sha256": cleaned_content_sha,
            },
        }

    def clean_document(
        self, fulltext: dict[str, Any]
    ) -> tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]],
        list[dict[str, Any]], dict[str, Any],
    ]:
        source = deepcopy(fulltext)
        cleaned = deepcopy(fulltext)
        findings: list[dict[str, Any]] = []
        repairs: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []

        for chapter in cleaned.get("chapters") or []:
            chapter_id = str(chapter.get("chapter_id") or "")
            for section in chapter.get("sections") or []:
                section_id = str(section.get("section_id") or "")
                content = str(section.get("content") or "")
                replacements: list[tuple[int, int, str]] = []
                for paragraph_index, (start, stop, paragraph) in enumerate(
                    _paragraph_spans(content), start=1
                ):
                    paragraph_id = _paragraph_id(
                        chapter_id, section_id, paragraph_index
                    )
                    repaired, paragraph_findings, paragraph_repairs, paragraph_unresolved = (
                        self._clean_paragraph(
                            paragraph,
                            chapter_id=chapter_id,
                            section_id=section_id,
                            paragraph_id=paragraph_id,
                            paragraph_index=paragraph_index,
                        )
                    )
                    findings.extend(paragraph_findings)
                    repairs.extend(paragraph_repairs)
                    unresolved.extend(paragraph_unresolved)
                    if repaired != paragraph:
                        replacements.append((start, stop, repaired))
                for start, stop, replacement in reversed(replacements):
                    content = content[:start] + replacement + content[stop:]
                content, duplicate_findings, duplicate_repairs = self._remove_duplicate_paragraphs(
                    content, chapter_id=chapter_id, section_id=section_id
                )
                findings.extend(duplicate_findings)
                repairs.extend(duplicate_repairs)
                section["content"] = content

        validation = self._validate_protection(source, cleaned, repairs)
        return cleaned, findings, repairs, unresolved, validation

    def _clean_paragraph(
        self,
        paragraph: str,
        *,
        chapter_id: str,
        section_id: str,
        paragraph_id: str,
        paragraph_index: int,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        current = paragraph
        findings: list[dict[str, Any]] = []
        repairs: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []

        def apply(
            repair_type: str,
            start: int,
            end: int,
            replacement: str,
            confidence: str = "high",
        ) -> None:
            nonlocal current
            before = current
            removed = before[start:end]
            after = before[:start] + replacement + before[end:]
            if _protected_signature(before) != _protected_signature(after):
                raise TechnicalFulltextHygieneError(
                    f"protected token change rejected for {chapter_id}/{section_id}/{paragraph_id}"
                )
            if not _is_subsequence(after, before):
                raise TechnicalFulltextHygieneError(
                    "deterministic hygiene repair must be deletion-only"
                )
            repair_id = _stable_id("HGR-", {
                "repair_type": repair_type,
                "chapter_id": chapter_id,
                "section_id": section_id,
                "paragraph_id": paragraph_id,
                "source_text_sha256": _sha256_text(before),
                "start": start,
                "removed": removed,
            })
            finding_id = _stable_id("HGF-", {
                "repair_type": repair_type,
                "chapter_id": chapter_id,
                "section_id": section_id,
                "paragraph_id": paragraph_id,
                "source_text_sha256": _sha256_text(before),
                "start": start,
                "removed": removed,
            })
            location = {
                "chapter_id": chapter_id,
                "section_id": section_id,
                "paragraph_id": paragraph_id,
                "paragraph_index": paragraph_index,
            }
            finding = {
                "finding_id": finding_id,
                "finding_type": repair_type,
                "status": "repaired",
                "gate_blocking": False,
                "location": location,
                "excerpt": _excerpt(before, start, end),
                "detection": "deterministic_exact_match",
            }
            repair = {
                "repair_id": repair_id,
                "finding_id": finding_id,
                "repair_type": repair_type,
                **location,
                "source_text_sha256": _sha256_text(before),
                "result_text_sha256": _sha256_text(after),
                "original_excerpt": _excerpt(before, start, end),
                "repaired_excerpt": _excerpt(after, start, start + len(replacement)),
                "changed_span": {
                    "start": start,
                    "end": end,
                    "removed_text": removed,
                    "replacement_text": replacement,
                },
                "method": "deterministic",
                "confidence": confidence,
                "model_used": None,
                "validation_status": "accepted",
            }
            findings.append(finding)
            repairs.append(repair)
            current = after

        tags = list(JINJA_TAG.finditer(current))
        if tags:
            unsafe_tags = [tag for tag in tags if not SAFE_JINJA_TAG.fullmatch(tag.group(0))]
            branch_tags = [
                tag for tag in tags
                if re.match(r"\s*(?:else|elif)\b", tag.group("body"), re.IGNORECASE)
            ]
            if not unsafe_tags and not branch_tags:
                for tag in reversed(tags):
                    apply("template_control_residue", tag.start(), tag.end(), "")

        while True:
            match = SAFE_MALFORMED_RESIDUE.search(current)
            if not match:
                break
            apply("malformed_generation_residue", match.start(), match.end(), "以")

        while True:
            match = MARKDOWN_HEADING_REPEAT.search(current)
            if not match:
                break
            second_start = match.start() + len(match.group("heading")) + 1
            apply("duplicate_markdown_heading", second_start, match.end(), "")

        while True:
            duplicate = _find_adjacent_repeat(
                current, minimum=4, maximum=20, phrase_only=True
            )
            if duplicate is None:
                break
            start, length, _ = duplicate
            apply(
                "adjacent_exact_phrase_duplication",
                start + length,
                start + 2 * length,
                "",
            )

        while True:
            duplicate = _find_adjacent_repeat(
                current, minimum=12, maximum=240, phrase_only=False
            )
            if duplicate is None:
                break
            start, length, _ = duplicate
            apply(
                "adjacent_exact_segment_duplication",
                start + length,
                start + 2 * length,
                "",
            )

        residues: list[tuple[str, re.Match[str]]] = []
        residues.extend(("template_control_residue", item) for item in JINJA_TAG.finditer(current))
        residues.extend(("malformed_generation_residue", item) for item in GENERIC_MALFORMED_RESIDUE.finditer(current))
        residues.extend(("mojibake_residue", item) for item in MOJIBAKE_RESIDUE.finditer(current))
        for residue_type, match in residues:
            finding_id = _stable_id("HGF-", {
                "finding_type": residue_type,
                "chapter_id": chapter_id,
                "section_id": section_id,
                "paragraph_id": paragraph_id,
                "source_text_sha256": _sha256_text(current),
                "start": match.start(),
                "text": match.group(0),
            })
            finding = {
                "finding_id": finding_id,
                "finding_type": residue_type,
                "status": "unresolved",
                "gate_blocking": True,
                "location": {
                    "chapter_id": chapter_id,
                    "section_id": section_id,
                    "paragraph_id": paragraph_id,
                    "paragraph_index": paragraph_index,
                },
                "excerpt": _excerpt(current, match.start(), match.end()),
                "detection": "deterministic_residue_pattern",
                "reason": "no deletion-only repair is provably safe",
            }
            findings.append(finding)
            unresolved.append(finding)
        return current, findings, repairs, unresolved

    def _remove_duplicate_paragraphs(
        self, content: str, *, chapter_id: str, section_id: str
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        parts = re.split(r"(\n[ \t]*\n+)", content)
        findings: list[dict[str, Any]] = []
        repairs: list[dict[str, Any]] = []
        index = 2
        paragraph_index = 2
        while index < len(parts):
            previous = parts[index - 2].strip()
            current = parts[index].strip()
            if (
                len(current) >= 20
                and current == previous
                and not _contains_protected_token(current)
            ):
                before = content
                paragraph_id = _paragraph_id(chapter_id, section_id, paragraph_index)
                source_hash = _sha256_text(current)
                finding_id = _stable_id("HGF-", {
                    "finding_type": "adjacent_duplicate_paragraph",
                    "chapter_id": chapter_id,
                    "section_id": section_id,
                    "paragraph_id": paragraph_id,
                    "source_text_sha256": source_hash,
                })
                repair_id = _stable_id("HGR-", {
                    "finding_id": finding_id,
                    "method": "deterministic",
                })
                parts[index - 1] = ""
                parts[index] = ""
                content = "".join(parts)
                location = {
                    "chapter_id": chapter_id,
                    "section_id": section_id,
                    "paragraph_id": paragraph_id,
                    "paragraph_index": paragraph_index,
                }
                findings.append({
                    "finding_id": finding_id,
                    "finding_type": "adjacent_duplicate_paragraph",
                    "status": "repaired",
                    "gate_blocking": False,
                    "location": location,
                    "excerpt": current[:240],
                    "detection": "deterministic_exact_match",
                })
                first_start = before.find(current)
                second_start = before.find(current, first_start + len(current))
                repairs.append({
                    "repair_id": repair_id,
                    "finding_id": finding_id,
                    "repair_type": "adjacent_duplicate_paragraph",
                    **location,
                    "source_text_sha256": source_hash,
                    "result_text_sha256": _sha256_text(""),
                    "original_excerpt": current[:240],
                    "repaired_excerpt": "",
                    "changed_span": {
                        "start": second_start,
                        "end": second_start + len(current),
                        "removed_text": current,
                        "replacement_text": "",
                    },
                    "method": "deterministic",
                    "confidence": "high",
                    "model_used": None,
                    "validation_status": "accepted",
                })
                continue
            index += 2
            paragraph_index += 1
        return content, findings, repairs

    @staticmethod
    def _validate_protection(
        source: dict[str, Any], cleaned: dict[str, Any], repairs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if _metadata_projection(source) != _metadata_projection(cleaned):
            raise TechnicalFulltextHygieneError(
                "hygiene changed fulltext structure or non-content metadata"
            )
        before = _protected_signature(_content_text(source))
        after = _protected_signature(_content_text(cleaned))
        checks = {
            "placeholder_tokens_unchanged": before["placeholders"] == after["placeholders"],
            "requirement_tokens_unchanged": before["requirements"] == after["requirements"],
            "score_item_tokens_unchanged": before["score_items"] == after["score_items"],
            "protected_value_tokens_unchanged": before["protected_values"] == after["protected_values"],
            "non_content_metadata_unchanged": True,
            "deterministic_repairs_deletion_only": all(
                item.get("method") == "deterministic" for item in repairs
            ),
        }
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise TechnicalFulltextHygieneError(
                f"post-repair protection validation failed: {failed}"
            )
        return {
            **checks,
            "validation_status": "passed",
            "source_content_sha256": _sha256_text(_content_text(source)),
            "result_content_sha256": _sha256_text(_content_text(cleaned)),
        }

    def _validate_report(self, report: dict[str, Any]) -> None:
        try:
            schema = load_json_object(self.schema_path, label="hygiene schema")
        except (FileNotFoundError, ValueError) as exc:
            raise TechnicalFulltextHygieneError(
                f"hygiene schema is unavailable: {exc}"
            ) from exc
        errors = sorted(
            Draft202012Validator(schema).iter_errors(report),
            key=lambda error: list(error.path),
        )
        if errors:
            rendered = "; ".join(
                f"{'/'.join(str(item) for item in error.path) or '<root>'}: {error.message}"
                for error in errors[:8]
            )
            raise TechnicalFulltextHygieneError(
                f"hygiene report schema validation failed: {rendered}"
            )

    @staticmethod
    def _render_report(report: dict[str, Any]) -> str:
        summary = report["summary"]
        lines = [
            f"# {HYGIENE_NOTICE}", "",
            f"- Hygiene status：{report['hygiene_status']}",
            f"- Human review hygiene ready：{'是' if report['human_review_hygiene_ready'] else '否'}",
            "- Release ready：否", "",
            "## 汇总", "",
            f"- Findings：{summary['finding_count']}",
            f"- Repairs：{summary['repair_count']}",
            f"- Unresolved：{summary['unresolved_count']}",
            f"- Model calls：{summary['model_call_count']}", "",
            "## 自动修复", "",
        ]
        if not report["hygiene_repairs"]:
            lines.append("无。")
        for repair in report["hygiene_repairs"]:
            lines.extend([
                f"### {repair['repair_id']} · {repair['repair_type']}", "",
                f"- 位置：{repair['chapter_id']} / {repair['section_id']} / {repair['paragraph_id']}",
                f"- 原文：{repair['original_excerpt']}",
                f"- 修复后：{repair['repaired_excerpt']}",
                f"- 方法：{repair['method']}", "",
            ])
        lines.extend(["## 未解决机械问题", ""])
        if not report["unresolved_hygiene_findings"]:
            lines.append("无。")
        for finding in report["unresolved_hygiene_findings"]:
            location = finding["location"]
            lines.extend([
                f"### {finding['finding_id']} · {finding['finding_type']}", "",
                f"- 位置：{location['chapter_id']} / {location['section_id']} / {location['paragraph_id']}",
                f"- 片段：{finding['excerpt']}",
                f"- 原因：{finding['reason']}", "",
            ])
        lines.extend([
            "## 边界声明", "",
            "本产物仅处理高置信度机械缺陷，不修改事实、承诺、评分语义、FAI、FCA或Stage 10 committed artifact。",
        ])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_cleaned_preview(
        fulltext: dict[str, Any], report: dict[str, Any]
    ) -> str:
        lines = [
            f"# {CLEANED_PREVIEW_NOTICE}", "",
            f"> Source document：{fulltext.get('document_id')}",
            f"> Hygiene ID：{report.get('hygiene_id')}",
            "> Release ready：否", "",
        ]
        for chapter in fulltext.get("chapters") or []:
            lines.extend([f"# {chapter.get('chapter_title')}", ""])
            depth_by_id = {
                str(item.get("section_id") or ""): int(item.get("depth") or 1)
                for item in chapter.get("section_outline") or []
                if isinstance(item, dict)
            }
            for section in chapter.get("sections") or []:
                section_id = str(section.get("section_id") or "")
                depth = max(1, depth_by_id.get(section_id, 1))
                lines.extend([
                    f"{'#' * (depth + 1)} {section.get('heading')}", "",
                    str(section.get("content") or ""), "",
                ])
        return "\n".join(lines).rstrip() + "\n"


def inspect_technical_fulltext_hygiene(path: str | Path) -> dict[str, Any]:
    payload = load_json_object(Path(path).expanduser().resolve(), label="fulltext hygiene")
    return {
        "schema_version": payload.get("schema_version"),
        "hygiene_id": payload.get("hygiene_id"),
        "document_id": payload.get("document_id"),
        "output_key": payload.get("output_key"),
        "hygiene_status": payload.get("hygiene_status"),
        "human_review_hygiene_ready": payload.get("human_review_hygiene_ready"),
        "release_ready": payload.get("release_ready"),
        "summary": payload.get("summary"),
        "cleaned_fulltext": payload.get("cleaned_fulltext"),
    }

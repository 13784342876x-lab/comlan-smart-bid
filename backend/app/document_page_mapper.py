from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.document_parser import find_soffice


def map_docx_blocks_to_pages(
    path: Path,
    blocks: list[dict[str, Any]],
    *,
    timeout_seconds: int = 120,
) -> tuple[list[dict[str, Any]], list[str]]:
    soffice = find_soffice()
    if soffice is None:
        return [dict(block) for block in blocks], ["未找到 LibreOffice，页码未可靠定位"]
    try:
        import fitz
    except ImportError:
        return [dict(block) for block in blocks], ["缺少 PyMuPDF，页码未可靠定位"]

    with tempfile.TemporaryDirectory(prefix="comlan_outline_pages_") as temp_name:
        temp_dir = Path(temp_name)
        profile_dir = temp_dir / "lo_profile"
        output_dir = temp_dir / "pdf"
        output_dir.mkdir(parents=True, exist_ok=True)
        conversion_source = temp_dir / "source.docx"
        shutil.copy2(path, conversion_source)
        executable = soffice.with_name("soffice.com") if soffice.with_name("soffice.com").exists() else soffice
        command = [
            str(executable),
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--headless",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(output_dir),
            str(conversion_source),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return [dict(block) for block in blocks], [f"LibreOffice 转 PDF 超时（{timeout_seconds}秒），页码未可靠定位"]
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "LibreOffice 转 PDF 失败").strip()
            return [dict(block) for block in blocks], [f"{message[:240]}；页码未可靠定位"]
        pdf_path = output_dir / "source.pdf"
        if not pdf_path.exists():
            candidates = sorted(output_dir.glob("*.pdf"))
            pdf_path = candidates[0] if len(candidates) == 1 else pdf_path
        if not pdf_path.exists():
            return [dict(block) for block in blocks], ["LibreOffice 未生成 PDF，页码未可靠定位"]

        with fitz.open(pdf_path) as document:
            page_texts = [_normalize_page_text(page.get_text("text")) for page in document]

    mapped: list[dict[str, Any]] = []
    mapped_count = 0
    for block in blocks:
        item = dict(block)
        pages = _match_block_pages(block, page_texts)
        if pages:
            item.update(
                {
                    "page_start": min(pages),
                    "page_end": max(pages),
                    "page_source": "libreoffice_rendered_pdf",
                    "rendered_page": min(pages),
                }
            )
            mapped_count += 1
        mapped.append(item)
    warnings = []
    if mapped_count < len([block for block in blocks if _block_text(block)]):
        warnings.append("部分 DOCX 内容未能与渲染 PDF 可靠匹配，相关页码标记为未可靠定位")
    return mapped, warnings


def _match_block_pages(block: dict[str, Any], page_texts: list[str]) -> list[int]:
    if block.get("block_type") == "table":
        markers = _table_markers(block)
        if not markers:
            return []
        pages: set[int] = set()
        for marker in markers:
            matches = [page_number for page_number, page_text in enumerate(page_texts, start=1) if marker in page_text]
            if len(matches) == 1:
                pages.add(matches[0])
        return sorted(pages) if len(pages) >= 1 else []

    text = _normalize_page_text(str(block.get("text") or ""))
    if len(text) < 4:
        return []
    exact = [index for index, page in enumerate(page_texts, start=1) if text in page]
    if len(exact) == 1:
        return exact
    if exact and max(exact) - min(exact) <= 2:
        return list(range(min(exact), max(exact) + 1))
    if len(text) < 80:
        return []
    start = text[:80]
    end = text[-80:]
    start_pages = [index for index, page in enumerate(page_texts, start=1) if start in page]
    end_pages = [index for index, page in enumerate(page_texts, start=1) if end in page]
    if start_pages and end_pages and start_pages[0] <= end_pages[-1] <= start_pages[0] + 2:
        return list(range(start_pages[0], end_pages[-1] + 1))
    return []


def _table_markers(block: dict[str, Any]) -> list[str]:
    structured_rows = list(block.get("structured_rows") or [])
    texts = [
        _normalize_page_text(str(cell.get("text") or ""))
        for row in structured_rows
        for cell in list(row.get("cells") or [])
        if len(_normalize_page_text(str(cell.get("text") or ""))) >= 8
    ]
    if not texts:
        texts = [
            _normalize_page_text(str(value or ""))
            for row in list(block.get("rows") or [])
            for value in list(row or [])
            if len(_normalize_page_text(str(value or ""))) >= 8
        ]
    if len(texts) <= 24:
        return list(dict.fromkeys(texts))
    indexes = {0, 1, 2, len(texts) // 4, len(texts) // 2, (len(texts) * 3) // 4, len(texts) - 3, len(texts) - 2, len(texts) - 1}
    return list(dict.fromkeys(texts[index] for index in sorted(indexes) if 0 <= index < len(texts)))


def _block_text(block: dict[str, Any]) -> str:
    if block.get("block_type") == "paragraph":
        return str(block.get("text") or "")
    return "".join(
        str(cell.get("text") or "")
        for row in list(block.get("structured_rows") or [])
        for cell in list(row.get("cells") or [])
    )


def _normalize_page_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")

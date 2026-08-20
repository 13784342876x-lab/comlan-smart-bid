from __future__ import annotations

import re
import io
import struct
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

from app.kb_service import KnowledgeBaseService
from app.metadata_store import MetadataStore
from app.source_cleanup import resolve_record_paths
from config import DATA_DIR, METADATA_PATH


def inspect_embedded_attachments(
    file_id: str,
    metadata: dict[str, Any] | None = None,
    metadata_path: Path = METADATA_PATH,
    save: bool = True,
) -> dict[str, Any]:
    store = MetadataStore(metadata_path)
    metadata = metadata or store.load()
    record = find_file(metadata, file_id)
    result = inspect_record(record)
    if save:
        write_inspection(metadata, file_id, result, metadata_path)
    return result


def inspect_record(record: dict[str, Any]) -> dict[str, Any]:
    ext = file_ext(record)
    base = empty_result()
    base["file_id"] = record.get("id") or record.get("file_id")
    base["source_file_name"] = record.get("original_name") or record.get("name")
    base["file_ext"] = ext
    if ext == ".docx":
        path = first_existing_path(record)
        if not path:
            base["requires_manual_review"] = True
            base["note"] = "未找到本地原始文件，无法检查 docx zip 结构。"
            return base
        return inspect_docx(path, base)
    if ext == ".doc":
        return inspect_legacy_doc(record, base)
    base["note"] = "当前仅检查 Word doc/docx 文件。"
    return base


def inspect_docx(path: Path, base: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            content_types = read_archive_text(archive, "[Content_Types].xml")
            for name in names:
                if name.startswith("word/embeddings/"):
                    kind = "embeddedPackage" if "package" in name.lower() else "oleObject"
                    items.append(
                        {
                            "kind": kind,
                            "path": name,
                            "relationship_id": None,
                            "target": name,
                            "target_mode": None,
                            "content_type": content_type_for_part(content_types, name),
                            "can_extract": True,
                            "requires_manual_upload": False,
                            "note": "docx zip 中发现 word/embeddings 内嵌对象。",
                        }
                    )
            for rel_path in [name for name in names if name.endswith(".rels") and (name.startswith("word/_rels/") or name.startswith("_rels/"))]:
                rel_text = read_archive_text(archive, rel_path)
                items.extend(parse_relationship_items(rel_path, rel_text))
    except zipfile.BadZipFile:
        base["requires_manual_review"] = True
        base["note"] = "docx 不是有效 zip 结构，需人工检查源文件。"
        return base

    return summarize_items(base, items, inspection_method="docx_zip_relationship_scan")


def inspect_legacy_doc(record: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    name = str(record.get("original_name") or "")
    text = str(record.get("parsed_text") or "")[:3000]
    triggers = ["附件", "详见附件", "技术规范书", "内嵌", "对象", "双击", "图标", "embedded", "package"]
    possible = True if any(term.lower() in f"{name}\n{text}".lower() for term in triggers) else True
    suspected_names = suspected_embedded_file_names(record)
    items = [
        {
            "kind": "iconHint",
            "path": asset.get("source_location") or asset.get("name"),
            "relationship_id": None,
            "target": filename,
            "target_mode": None,
            "content_type": "",
            "can_extract": False,
            "requires_manual_upload": False,
            "note": f"检测到疑似内嵌文件图标：{filename}。",
        }
        for filename, asset in suspected_names
    ]
    base.update(
        {
            "possible_ole_embedded_objects": possible,
            "inspection_method": "legacy_doc_heuristic",
            "requires_manual_review": True,
            "items": items,
            "suspected_embedded_file_names": [name for name, _asset in suspected_names],
            "note": legacy_doc_note(suspected_names),
        }
    )
    return base


def extract_embedded_attachments(
    file_id: str,
    metadata_path: Path = METADATA_PATH,
    output_root: Path | None = None,
    ingest: bool = False,
) -> dict[str, Any]:
    store = MetadataStore(metadata_path)
    metadata = store.load()
    record = find_file(metadata, file_id)
    source_name = record.get("original_name") or record.get("name")
    output_dir = output_root or DATA_DIR / "extracted_embedded_attachments" / file_id
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_files: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    path = first_existing_path(record)
    if not path:
        raise FileNotFoundError(f"source file not found for file_id={file_id}")

    if file_ext(record) == ".doc":
        extracted_files.extend(extract_legacy_doc_payloads(record, path, output_dir))
    elif file_ext(record) == ".docx":
        extracted_files.extend(extract_docx_embeddings(record, path, output_dir))

    if ingest and extracted_files:
        service = KnowledgeBaseService()
        for item in extracted_files:
            child = service.ingest_file(
                Path(item["extracted_path"]),
                category=item.get("category") or "招标需求一览表",
                tags=["tender_demand_list", "extracted_from_tender_notice_attachment"],
            )
            item["child_file_id"] = child.get("id")

    suspected = [name for name, _asset in suspected_embedded_file_names(record)]
    if not extracted_files:
        for filename in suspected:
            failed_items.append(
                {
                    "filename": filename,
                    "reason": "未能自动抽取旧 .doc OLE 子文件。",
                    "manual_action": manual_action_message(filename, source_name),
                }
            )

    update_extracted_children(metadata, file_id, extracted_files, metadata_path)
    return {
        "parent_file_id": file_id,
        "source_file_name": source_name,
        "extracted_count": len(extracted_files),
        "extracted_files": extracted_files,
        "failed_items": failed_items,
        "manual_action_required": bool(failed_items) or not bool(extracted_files),
        "manual_action": manual_action_message(suspected[0], source_name) if suspected and not extracted_files else "",
        "output_dir": str(output_dir),
        "ingest": ingest,
    }


def parse_relationship_items(rel_path: str, rel_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in re.finditer(r"<Relationship\b[^>]*>", rel_text):
        tag = match.group(0)
        rel_id = attr(tag, "Id")
        rel_type = attr(tag, "Type")
        target = attr(tag, "Target")
        target_mode = attr(tag, "TargetMode")
        lowered = " ".join([rel_type, target, tag]).lower()
        is_embedded_target = "embeddings/" in lowered or "/embeddings/" in lowered
        is_ole_or_package = "oleobject" in lowered or "embeddedpackage" in lowered or is_embedded_target
        if not is_ole_or_package and target_mode != "External":
            continue
        kind = "externalLink" if target_mode == "External" else ("embeddedPackage" if "embeddedpackage" in lowered else "oleObject")
        items.append(
            {
                "kind": kind,
                "path": rel_path,
                "relationship_id": rel_id,
                "target": target,
                "target_mode": target_mode or None,
                "content_type": rel_type,
                "can_extract": bool(target and target_mode != "External" and "embeddings" in target.lower()),
                "requires_manual_upload": target_mode == "External",
                "note": "检测到疑似链接附件或图标占位，当前未找到可直接抽取的内嵌文件；请人工上传真实附件文件。" if target_mode == "External" else "rels 中发现 OLE/嵌入包关系。",
            }
        )
    return items


def summarize_items(base: dict[str, Any], items: list[dict[str, Any]], inspection_method: str) -> dict[str, Any]:
    embedded_packages = [item for item in items if item.get("kind") == "embeddedPackage"]
    ole_objects = [item for item in items if item.get("kind") == "oleObject"]
    external_links = [item for item in items if item.get("target_mode") == "External" or item.get("kind") == "externalLink"]
    base.update(
        {
            "inspection_method": inspection_method,
            "has_embedded_attachments": bool(embedded_packages or ole_objects),
            "embedded_objects_count": len(embedded_packages) + len(ole_objects),
            "embedded_packages_count": len(embedded_packages),
            "ole_objects_count": len(ole_objects),
            "linked_objects_count": len(external_links),
            "external_links_count": len(external_links),
            "requires_manual_upload": any(bool(item.get("requires_manual_upload")) for item in items),
            "requires_manual_review": any(bool(item.get("requires_manual_upload")) for item in items),
            "items": items,
        }
    )
    if base["requires_manual_upload"]:
        base["note"] = "检测到疑似链接附件或图标占位，当前未找到可直接抽取的内嵌文件；请人工上传真实附件文件。"
    return base


def empty_result() -> dict[str, Any]:
    return {
        "version": "v0",
        "inspected_at": datetime.now().isoformat(timespec="seconds"),
        "has_embedded_attachments": False,
        "possible_ole_embedded_objects": False,
        "embedded_objects_count": 0,
        "embedded_packages_count": 0,
        "ole_objects_count": 0,
        "linked_objects_count": 0,
        "external_links_count": 0,
        "requires_manual_upload": False,
        "requires_manual_review": False,
        "items": [],
        "extracted_children": [],
    }


def write_inspection(metadata: dict[str, Any], file_id: str, result: dict[str, Any], metadata_path: Path) -> None:
    files = metadata.setdefault("files", [])
    for index, record in enumerate(files):
        if str(record.get("id") or record.get("file_id") or "") == file_id:
            existing = record.get("embedded_attachments") if isinstance(record.get("embedded_attachments"), dict) else {}
            if existing.get("extracted_children") and not result.get("extracted_children"):
                result["extracted_children"] = existing.get("extracted_children")
            files[index] = {**record, "embedded_attachments": result}
            metadata_path.write_text(to_json(metadata), encoding="utf-8")
            return
    raise KeyError(f"file_id not found: {file_id}")


def find_file(metadata: dict[str, Any], file_id: str) -> dict[str, Any]:
    for record in list(metadata.get("files") or []):
        if str(record.get("id") or record.get("file_id") or "") == file_id:
            return record
    raise KeyError(f"file_id not found: {file_id}")


def first_existing_path(record: dict[str, Any]) -> Path | None:
    for path in resolve_record_paths(record):
        if path.exists() and path.is_file():
            return path
    return None


def file_ext(record: dict[str, Any]) -> str:
    ext = str(record.get("file_ext") or "").strip().lower()
    if ext and not ext.startswith("."):
        return f".{ext}"
    name = str(record.get("original_name") or "")
    return ext or Path(name).suffix.lower()


def read_archive_text(archive: zipfile.ZipFile, name: str) -> str:
    try:
        return archive.read(name).decode("utf-8", errors="ignore")
    except KeyError:
        return ""


def attr(tag: str, name: str) -> str:
    match = re.search(rf'{name}\s*=\s*"([^"]*)"', tag)
    return match.group(1) if match else ""


def content_type_for_part(content_types_xml: str, part_name: str) -> str:
    if not content_types_xml:
        return ""
    normalized = "/" + part_name.lstrip("/")
    for match in re.finditer(r"<Override\b[^>]*>", content_types_xml):
        tag = match.group(0)
        if attr(tag, "PartName") == normalized:
            return attr(tag, "ContentType")
    return ""


def extract_legacy_doc_payloads(record: dict[str, Any], path: Path, output_dir: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    suspected = [name for name, _asset in suspected_embedded_file_names(record)]
    carved = carve_zip_payloads(data)
    extracted: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, payload in enumerate(carved, start=1):
        ext = payload_extension(payload["names"])
        if ext not in {".xlsx", ".docx"}:
            continue
        filename = choose_extracted_filename(suspected, ext, index, used_names)
        used_names.add(filename)
        target_path = unique_output_path(output_dir / filename)
        target_path.write_bytes(payload["data"])
        extracted.append(
            {
                "parent_file_id": record.get("id") or record.get("file_id"),
                "child_file_id": None,
                "embedded_attachment_index": index,
                "extracted_file_name": target_path.name,
                "original_name": target_path.name,
                "extracted_path": str(target_path),
                "category": "招标需求一览表" if ext == ".xlsx" and ("需求" in target_path.name or "一览表" in target_path.name) else "招标公告内嵌附件",
                "tags": ["tender_demand_list", "extracted_from_tender_notice_attachment"] if ext == ".xlsx" else ["extracted_from_tender_notice_attachment"],
                "can_parse": True,
                "extraction_method": "legacy_doc_zip_carving",
                "zip_entries_preview": payload["names"][:20],
            }
        )
    return extracted


def extract_docx_embeddings(record: dict[str, Any], path: Path, output_dir: Path) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        for index, name in enumerate([item for item in archive.namelist() if item.startswith("word/embeddings/")], start=1):
            data = archive.read(name)
            ext = ".bin"
            if data.startswith(b"PK\x03\x04"):
                ext = ".xlsx" if zip_payload_kind(data) == "xlsx" else ".docx"
            target_path = unique_output_path(output_dir / f"embedded_{index}{ext}")
            target_path.write_bytes(data)
            extracted.append(
                {
                    "parent_file_id": record.get("id") or record.get("file_id"),
                    "child_file_id": None,
                    "embedded_attachment_index": index,
                    "extracted_file_name": target_path.name,
                    "original_name": target_path.name,
                    "extracted_path": str(target_path),
                    "category": "招标公告内嵌附件",
                    "tags": ["extracted_from_tender_notice_attachment"],
                    "can_parse": True,
                    "extraction_method": "docx_word_embeddings",
                    "source_part": name,
                }
            )
    return extracted


def carve_zip_payloads(data: bytes) -> list[dict[str, Any]]:
    starts = find_all(data, b"PK\x03\x04")
    ends = [pos + 22 for pos in find_all(data, b"PK\x05\x06")]
    payloads: list[dict[str, Any]] = []
    seen_names: set[tuple[str, ...]] = set()
    for end in ends:
        normalized_prefix = repack_zip_bytes(data[:end])
        if normalized_prefix and add_payload_if_office(payloads, seen_names, normalized_prefix, 0, end):
            continue
        for start in reversed([item for item in starts if item < end]):
            blob = data[start:end]
            normalized = normalize_zip_payload(blob, start)
            if not normalized:
                continue
            if add_payload_if_office(payloads, seen_names, normalized, start, end):
                break
    return payloads


def add_payload_if_office(
    payloads: list[dict[str, Any]],
    seen_names: set[tuple[str, ...]],
    normalized: tuple[bytes, list[str]],
    start: int,
    end: int,
) -> bool:
    fixed_blob, names = normalized
    if zip_payload_kind(names) not in {"docx", "xlsx"}:
        return False
    key = tuple(names)
    if key in seen_names:
        return True
    seen_names.add(key)
    payloads.append({"data": fixed_blob, "names": names, "start": start, "end": end})
    return True


def normalize_zip_payload(blob: bytes, base_offset: int = 0) -> tuple[bytes, list[str]] | None:
    """Return a standalone ZIP payload.

    Embedded Office packages inside legacy .doc files may keep central-directory
    offsets relative to the parent compound file. Python can list those entries
    but cannot read them once the blob is saved separately, so rebuild from local
    file headers when needed.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = archive.namelist()
            if names and archive.testzip() is None:
                return blob, names
    except Exception:
        pass
    if base_offset > 0:
        try:
            padded = (b"\x00" * base_offset) + blob
            repacked = repack_zip_bytes(padded)
            if repacked:
                return repacked
        except Exception:
            pass
    return rebuild_zip_from_local_headers(blob)


def repack_zip_bytes(data: bytes) -> tuple[bytes, list[str]] | None:
    source = io.BytesIO(data)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if not names:
            return None
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name in names:
                target.writestr(name, archive.read(name))
    fixed = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(fixed)) as archive:
        if archive.testzip() is not None:
            return None
        return fixed, archive.namelist()


def rebuild_zip_from_local_headers(blob: bytes) -> tuple[bytes, list[str]] | None:
    entries: list[tuple[str, bytes]] = []
    pos = 0
    while pos < len(blob):
        sig = blob.find(b"PK\x03\x04", pos)
        if sig < 0 or sig + 30 > len(blob):
            break
        try:
            (
                _signature,
                _version,
                flags,
                compression,
                _mod_time,
                _mod_date,
                _crc32,
                compressed_size,
                _uncompressed_size,
                name_len,
                extra_len,
            ) = struct.unpack_from("<IHHHHHIIIHH", blob, sig)
        except struct.error:
            break
        name_start = sig + 30
        data_start = name_start + name_len + extra_len
        data_end = data_start + compressed_size
        if name_len <= 0 or data_start > len(blob) or data_end > len(blob):
            pos = sig + 4
            continue
        raw_name = blob[name_start:name_start + name_len]
        name_encoding = "utf-8" if flags & 0x800 else "cp437"
        name = raw_name.decode(name_encoding, errors="replace").lstrip("/")
        compressed = blob[data_start:data_end]
        if flags & 0x08:
            pos = sig + 4
            continue
        try:
            if name.endswith("/"):
                content = b""
            elif compression == 0:
                content = compressed
            elif compression == 8:
                content = zlib.decompress(compressed, -15)
            else:
                pos = data_end
                continue
        except Exception:
            pos = data_end
            continue
        entries.append((name, content))
        pos = data_end

    names = [name for name, _content in entries]
    if not any(name == "[Content_Types].xml" for name in names):
        return None
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    data = output.getvalue()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip() is not None:
                return None
            return data, archive.namelist()
    except Exception:
        return None


def zip_payload_kind(names: list[str] | bytes) -> str:
    if isinstance(names, bytes):
        try:
            import io

            with zipfile.ZipFile(io.BytesIO(names)) as archive:
                names = archive.namelist()
        except Exception:
            return "zip"
    joined = "\n".join(str(name) for name in names)
    if "xl/workbook.xml" in joined or "/xl/" in joined or joined.startswith("xl/"):
        return "xlsx"
    if "word/document.xml" in joined or "/word/" in joined or joined.startswith("word/"):
        return "docx"
    return "zip"


def payload_extension(names: list[str]) -> str:
    kind = zip_payload_kind(names)
    if kind == "xlsx":
        return ".xlsx"
    if kind == "docx":
        return ".docx"
    return ".zip"


def suspected_embedded_file_names(record: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    results: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for asset in list(record.get("assets") or []):
        text = normalize_ocr_filename_text(str(asset.get("ocr_text") or ""))
        for filename in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_.-]{2,}\.(?:xlsx|xls|docx|doc|pdf)", text, flags=re.IGNORECASE):
            cleaned = normalize_detected_filename(filename)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                results.append((cleaned, asset))
    text = normalize_ocr_filename_text(str(record.get("parsed_text") or ""))
    for filename in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_.-]{2,}\.(?:xlsx|xls|docx|doc|pdf)", text, flags=re.IGNORECASE):
        cleaned = normalize_detected_filename(filename)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            results.append((cleaned, {}))
    return results


def normalize_ocr_filename_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    compact = compact.replace("需求-览表", "需求一览表").replace("需求一览表", "需求一览表")
    compact = compact.replace("。", ".").replace("．", ".")
    return compact


def normalize_detected_filename(filename: str) -> str:
    value = filename.strip(" .。；;，,")
    value = value.replace("需求-览表", "需求一览表")
    return safe_file_name(value)


def choose_extracted_filename(suspected: list[str], ext: str, index: int, used: set[str]) -> str:
    for filename in suspected:
        if filename.lower().endswith(ext) and filename not in used:
            return safe_file_name(filename)
    default = "需求一览表042513.xlsx" if ext == ".xlsx" and index == 1 else f"embedded_attachment_{index}{ext}"
    return safe_file_name(default)


def unique_output_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot create unique output path for {path}")


def safe_file_name(value: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip(" ._")
    return name[:120] or "embedded_attachment.bin"


def find_all(data: bytes, needle: bytes) -> list[int]:
    positions: list[int] = []
    start = -1
    while True:
        start = data.find(needle, start + 1)
        if start < 0:
            return positions
        positions.append(start)


def update_extracted_children(metadata: dict[str, Any], file_id: str, extracted_files: list[dict[str, Any]], metadata_path: Path) -> None:
    files = metadata.setdefault("files", [])
    for index, record in enumerate(files):
        if str(record.get("id") or record.get("file_id") or "") != file_id:
            continue
        embedded = record.get("embedded_attachments") if isinstance(record.get("embedded_attachments"), dict) else empty_result()
        embedded["extracted_children"] = extracted_files
        embedded["extracted_count"] = len(extracted_files)
        embedded["last_extract_at"] = datetime.now().isoformat(timespec="seconds")
        files[index] = {**record, "embedded_attachments": embedded}
        metadata_path.write_text(to_json(metadata), encoding="utf-8")
        return
    raise KeyError(f"file_id not found: {file_id}")


def legacy_doc_note(suspected_names: list[tuple[str, dict[str, Any]]]) -> str:
    if suspected_names:
        names = "、".join(name for name, _asset in suspected_names)
        return f"检测到疑似内嵌文件图标：{names}。可运行 extract-embedded-attachments 尝试自动抽取；若失败，请用 Word/WPS/LibreOffice 打开源 .doc，双击或另存该内嵌文件后重新入库。"
    return "旧版 .doc 无法通过 zip 结构直接检查，本轮仅做保守提示；如存在图标或链接附件，请人工上传真实附件文件。"


def manual_action_message(filename: str | None, source_name: Any) -> str:
    target = filename or "疑似内嵌附件"
    return f"当前未能自动抽取旧 .doc OLE 子文件，请用 Word/WPS/LibreOffice 打开{source_name or '源文件'}，双击或另存该内嵌文件（{target}），并将其作为“招标需求一览表”重新入库。"


def to_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)

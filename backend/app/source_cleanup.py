from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.file_id_utils import existing_file_ids
from app.metadata_store import MetadataStore
from app.tender_source_guard import is_full_tender_document, tender_source_type
from config import ASSETS_DIR, DATA_DIR, METADATA_PATH, ORIGINAL_DIR, PARSED_DIR, STORAGE_DIR


MOCK_FILE_ID_DENYLIST = {
    "file_a5eee523007a",
    "file_a0a571b442b0",
}

PROTECTED_FILE_IDS = {
    "file_2308aa547dd9",
    "file_acb8d90cde1f",
    "file_829b97fcf9b4",
    "file_bd310be2d205",
    "file_3fa83f22cdb4",
    "file_ae4329911662",
}

PATH_KEYS = {
    "path",
    "file_path",
    "stored_path",
    "local_path",
    "absolute_path",
    "source_path",
    "parsed_output_path",
    "storage_key",
    "asset_key",
}

MOCK_MARKERS = ["mock_source", "synthetic", "fake", "demo", "泛化摘要", "占位", "测试摘要", "Mock Word解析文本"]
GENERIC_SUMMARY_MARKERS = [
    "包含营业执照、ISO9001质量管理体系认证、ISO27001信息安全管理体系认证、AAA信用等级、专利、软件著作权、检测报告、合同等资料",
]
EVIDENCE_MARKERS = ["证书编号", "有效期", "权利人", "合同编号", "盖章", "统一社会信用代码", "授权公告号", "专利号"]

FULL_TENDER_POSITIVE_TERMS = [
    "招标文件",
    "采购文件",
    "招标公告",
    "采购公告",
    "投标人须知",
    "投标人须知前附表",
    "响应文件组成",
    "投标文件组成",
    "签字盖章",
    "否决",
    "无效响应",
    "资格条件",
    "服务期限",
    "采购需求一览表",
    "分标分包表",
]
FULL_TENDER_STRONG_TERMS = [
    "投标人须知前附表",
    "响应文件组成",
    "投标文件组成",
    "签字盖章",
    "无效响应",
    "否决",
    "资格条件",
    "服务期限",
    "采购需求一览表",
    "分标分包表",
]
FULL_TENDER_NEGATIVE_NAME_TERMS = ["招标文件附件", "评标办法", "评分表", "技术规范书", "技术标准详见技术规范书"]
TEXT_SUFFIXES = {".json", ".md", ".txt", ".csv", ".log", ".yaml", ".yml"}
SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache", "chroma"}


def audit_case_sources(lot_name: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or MetadataStore().load()
    rows: list[dict[str, Any]] = []
    for record in list(metadata.get("files") or []):
        if not isinstance(record, dict):
            continue
        resolved_paths = resolve_record_paths(record)
        physical_path = str(resolved_paths[0]) if resolved_paths else ""
        rows.append(
            {
                "file_id": record.get("id") or record.get("file_id"),
                "file_name": record.get("original_name") or record.get("name"),
                "file_role": infer_file_role(record),
                "parsed_text_len": parsed_text_len(record),
                "tables_count": len(list(record.get("tables") or [])),
                "is_known_mock": is_known_mock(record),
                "is_suspected_mock": is_suspected_mock(record),
                "physical_path": physical_path,
                "physical_exists": any(path.exists() for path in resolved_paths),
                "is_full_tender_document_candidate": is_full_tender_document_candidate(record),
                "risk_notes": risk_notes(record),
            }
        )
    readiness = inspect_case_readiness(lot_name, metadata=metadata)
    return {
        "lot_name": lot_name,
        "files": rows,
        "warnings": readiness["warnings"],
        "readiness": readiness,
    }


def purge_mock_materials(
    lot_name: str,
    denylist: set[str] | None = None,
    metadata_path: Path = METADATA_PATH,
    data_dir: Path = DATA_DIR,
    outputs_dir: Path = Path("outputs"),
    storage_dir: Path = STORAGE_DIR,
) -> dict[str, Any]:
    denylist = set(denylist or MOCK_FILE_ID_DENYLIST)
    denied = denylist - PROTECTED_FILE_IDS
    started_at = datetime.now().isoformat(timespec="seconds")
    store = MetadataStore(metadata_path)
    metadata = store.load()
    before_ids = existing_file_ids(metadata)
    records_to_delete = [
        record for record in list(metadata.get("files") or [])
        if isinstance(record, dict) and str(record.get("id") or record.get("file_id") or "") in denied
    ]

    physical_deletions = delete_physical_artifacts(records_to_delete, denied, storage_dir)
    cleaned_metadata, metadata_stats = purge_object_references(metadata, denied)
    if cleaned_metadata is None or not isinstance(cleaned_metadata, dict):
        cleaned_metadata = {"files": []}
    cleaned_metadata.setdefault("files", [])
    store.save(cleaned_metadata)

    json_cleanup = clean_runtime_json_files(data_dir, denied, exclude_paths={metadata_path.resolve()})
    output_cleanup = delete_case_output_files(lot_name, denied, outputs_dir)
    residual = scan_residual_references(
        denied,
        roots=[metadata_path, data_dir, outputs_dir],
        report_dir=purge_report_dir(data_dir),
    )
    report = {
        "kind": "source_cleanup_purge_report",
        "started_at": started_at,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "lot_name": lot_name,
        "denylist": sorted(denied),
        "protected_file_ids": sorted(PROTECTED_FILE_IDS),
        "metadata_file_ids_before": sorted(before_ids),
        "metadata_file_ids_after": sorted(existing_file_ids(cleaned_metadata)),
        "metadata_removed_file_ids": sorted(before_ids - existing_file_ids(cleaned_metadata)),
        "metadata_cleanup": metadata_stats,
        "physical_deletions": physical_deletions,
        "runtime_json_cleanup": json_cleanup,
        "case_output_cleanup": output_cleanup,
        "residual_references_found": residual["residual_references_found"],
        "residual_reference_locations": residual["residual_reference_locations"],
        "business_residual_references_found": residual["business_residual_references_found"],
        "business_residual_reference_locations": residual["business_residual_reference_locations"],
        "readiness": inspect_case_readiness(lot_name, metadata=cleaned_metadata),
    }
    report_path = write_purge_report(report, data_dir)
    report["report_path"] = str(report_path)
    return report


def inspect_case_readiness(lot_name: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or MetadataStore().load()
    files = [item for item in list(metadata.get("files") or []) if isinstance(item, dict)]
    full_tender = [record for record in files if is_full_tender_document(record)]
    tender_notice = [record for record in files if tender_source_type(record) == "tender_notice"]
    tender_notice_attachments = [record for record in files if tender_source_type(record) == "tender_notice_attachment"]
    procurement_package_tables = [
        record for record in files
        if tender_source_type(record) == "procurement_package_table" and record_matches_lot(record, lot_name)
    ]
    tender_attachments = [record for record in files if is_tender_attachment(record)]
    technical_specs = [record for record in files if has_any(record_name(record), ["技术规范书", "技术标准"])]
    score_files = [record for record in files if has_any(record_name(record), ["评标办法", "评分表"])]
    old_mock = [record for record in files if is_known_mock(record)]
    warnings: list[str] = []
    if not full_tender:
        warnings.append("未检测到完整招标文件正文")
    if tender_attachments and not full_tender:
        warnings.append("检测到招标文件附件，但未检测到完整招标文件正文；该附件不能替代完整招标文件。")
    return {
        "lot_name": lot_name,
        "full_tender_document": {
            "status": "detected" if full_tender else "missing",
            "file_ids": [record.get("id") for record in full_tender],
        },
        "tender_notice": {
            "status": "detected" if tender_notice else "missing",
            "file_ids": [record.get("id") for record in tender_notice],
        },
        "tender_notice_attachment": {
            "status": "detected" if tender_notice_attachments else "missing",
            "file_ids": [record.get("id") for record in tender_notice_attachments],
        },
        "procurement_package_table": {
            "status": "detected" if procurement_package_tables else "missing",
            "file_ids": [record.get("id") for record in procurement_package_tables],
        },
        "tender_attachment": {
            "status": "detected" if tender_attachments else "missing",
            "file_ids": [record.get("id") for record in tender_attachments],
        },
        "technical_spec": {
            "status": "detected" if technical_specs else "missing",
            "file_ids": [record.get("id") for record in technical_specs],
        },
        "score_table": {
            "status": "detected" if score_files else "missing",
            "file_ids": [record.get("id") for record in score_files],
        },
        "old_mock_files": {
            "status": "remaining" if old_mock else "cleaned",
            "file_ids": [record.get("id") for record in old_mock],
        },
        "allow_metadata_extraction": bool(full_tender),
        "allow_formal_outline": bool(full_tender),
        "blocked": not bool(full_tender),
        "blocking_reasons": [] if full_tender else ["未检测到完整招标文件正文"],
        "warnings": warnings,
        "next_steps": readiness_next_steps(bool(full_tender)),
    }


def readiness_next_steps(has_full_tender: bool) -> list[str]:
    if has_full_tender:
        return [
            "重新运行 audit-case-sources",
            "readiness 通过后再执行 Tender Metadata Extractor V0 和 Embedded Attachment Inspector V0",
        ]
    return [
        '请先使用现有 ingest-file / ingest-auto 入库完整招标文件正文或采购公告',
        '入库后运行 parse-file 和 classify-file',
        '重新运行 audit-case-sources',
        'readiness 通过后再执行 Tender Metadata Extractor V0 和 Embedded Attachment Inspector V0',
    ]


def resolve_record_paths(record: dict[str, Any], backend_root: Path | None = None) -> list[Path]:
    backend_root = backend_root or DATA_DIR.parent
    paths: list[Path] = []
    for key in PATH_KEYS:
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key in {"storage_key", "asset_key"} and not Path(value).is_absolute():
            path = STORAGE_DIR / value
        else:
            path = Path(value)
            if not path.is_absolute():
                path = backend_root / path
        paths.append(path)
    for asset in list(record.get("assets") or []):
        if isinstance(asset, dict):
            paths.extend(resolve_record_paths(asset, backend_root=backend_root))
    return unique_paths(paths)


def delete_physical_artifacts(records: list[dict[str, Any]], denied: set[str], storage_dir: Path) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    for record in records:
        candidates.extend(resolve_record_paths(record, backend_root=DATA_DIR.parent))
    for root in [storage_dir / "original", storage_dir / "assets", storage_dir / "parsed"]:
        if root.exists():
            for file_id in denied:
                candidates.extend(root.glob(f"*{file_id}*"))
                candidates.extend(root.glob(f"**/*{file_id}*"))
    result: list[dict[str, Any]] = []
    for path in unique_paths(candidates):
        item = {"path": str(path), "existed": path.exists(), "deleted": False, "error": ""}
        try:
            if not belongs_to_file_id(path, denied):
                item["error"] = "skipped: path does not include denied file_id"
            elif not is_safe_artifact_path(path, storage_dir):
                item["error"] = "skipped: path outside allowed storage roots"
            elif path.is_file():
                path.unlink()
                item["deleted"] = True
            elif path.is_dir():
                if any(path.iterdir()):
                    item["error"] = "skipped: non-empty directory"
                else:
                    path.rmdir()
                    item["deleted"] = True
        except Exception as exc:
            item["error"] = str(exc)
        result.append(item)
    return result


def clean_runtime_json_files(data_dir: Path, denied: set[str], exclude_paths: set[Path] | None = None) -> list[dict[str, Any]]:
    exclude_paths = exclude_paths or set()
    result: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("*.json")):
        if path.resolve() in exclude_paths:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            cleaned, stats = purge_object_references(data, denied)
            if stats["removed_items"] > 0 or stats["removed_dicts"] > 0 or stats["removed_strings"] > 0:
                path.write_text(json.dumps(cleaned if cleaned is not None else {}, ensure_ascii=False, indent=2), encoding="utf-8")
                changed = True
            else:
                changed = False
            result.append({"path": str(path), "changed": changed, **stats})
        except Exception as exc:
            result.append({"path": str(path), "changed": False, "error": str(exc)})
    return result


def delete_case_output_files(lot_name: str, denied: set[str], outputs_dir: Path) -> list[dict[str, Any]]:
    case_dir = outputs_dir / safe_path_name(lot_name)
    result: list[dict[str, Any]] = []
    if not case_dir.exists():
        return result
    for path in sorted(case_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            matched = sorted(file_id for file_id in denied if file_id in text)
            if not matched:
                continue
            path.unlink()
            result.append({"path": str(path), "matched_file_ids": matched, "deleted": True, "error": ""})
        except Exception as exc:
            result.append({"path": str(path), "matched_file_ids": [], "deleted": False, "error": str(exc)})
    return result


def scan_residual_references(
    file_ids: set[str],
    roots: list[Path],
    report_dir: Path | None = None,
) -> dict[str, Any]:
    locations: list[dict[str, Any]] = []
    report_root = report_dir.resolve() if report_dir else None
    seen_paths: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen_paths or should_skip_path(path):
                continue
            seen_paths.add(resolved)
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                    for file_id in sorted(file_ids):
                        if file_id not in line:
                            continue
                        report_only = bool(report_root and report_root in resolved.parents)
                        locations.append(
                            {
                                "path": str(path),
                                "matched_file_id": file_id,
                                "line_or_context": f"{line_no}: {line.strip()[:240]}",
                                "report_only": report_only,
                                "note": "仅清理报告中保留历史记录，不视为业务残留。" if report_only else "",
                            }
                        )
            except Exception:
                continue
    business = [item for item in locations if not item.get("report_only")]
    return {
        "residual_references_found": bool(locations),
        "residual_reference_locations": locations,
        "business_residual_references_found": bool(business),
        "business_residual_reference_locations": business,
    }


def purge_object_references(value: Any, denied: set[str]) -> tuple[Any, dict[str, int]]:
    stats = {"removed_items": 0, "removed_dicts": 0, "removed_strings": 0}

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            for key in ["id", "file_id", "source_file_id"]:
                if str(item.get(key) or "") in denied:
                    stats["removed_dicts"] += 1
                    return None
            result: dict[str, Any] = {}
            for key, subvalue in item.items():
                if str(key) in denied:
                    stats["removed_items"] += 1
                    continue
                cleaned = clean(subvalue)
                if cleaned is None and subvalue is not None:
                    stats["removed_items"] += 1
                    continue
                result[key] = cleaned
            return result
        if isinstance(item, list):
            result = []
            for subvalue in item:
                if isinstance(subvalue, str) and subvalue in denied:
                    stats["removed_strings"] += 1
                    continue
                cleaned = clean(subvalue)
                if cleaned is None and subvalue is not None:
                    stats["removed_items"] += 1
                    continue
                result.append(cleaned)
            return result
        if isinstance(item, str) and item in denied:
            stats["removed_strings"] += 1
            return None
        return item

    return clean(value), stats


def is_known_mock(record: dict[str, Any]) -> bool:
    return str(record.get("id") or record.get("file_id") or "") in MOCK_FILE_ID_DENYLIST


def is_suspected_mock(record: dict[str, Any]) -> bool:
    if is_known_mock(record):
        return True
    text = searchable_record_text(record)
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in MOCK_MARKERS):
        return True
    if any(marker in text for marker in GENERIC_SUMMARY_MARKERS) and not any(marker in text for marker in EVIDENCE_MARKERS):
        return True
    return False


def is_full_tender_document_candidate(record: dict[str, Any]) -> bool:
    return is_full_tender_document(record)


def record_matches_lot(record: dict[str, Any], lot_name: str) -> bool:
    if not lot_name:
        return True
    if lot_name in searchable_record_text(record):
        return True
    normalized_lot = normalize_lot_key(lot_name)
    if not normalized_lot:
        return False
    return normalized_lot in normalize_lot_key(json.dumps(record.get("tables") or [], ensure_ascii=False))


def normalize_lot_key(value: Any) -> str:
    return "".join(re.findall(r"[A-Za-z0-9]+", str(value or ""))).lower()


def is_tender_attachment(record: dict[str, Any]) -> bool:
    return "招标文件附件" in record_name(record)


def infer_file_role(record: dict[str, Any]) -> str:
    profile = record.get("material_profile")
    if isinstance(profile, dict) and profile.get("material_group"):
        return str(profile.get("material_group"))
    return str(record.get("category") or record.get("file_role") or "")


def parsed_text_len(record: dict[str, Any]) -> int:
    text = record.get("parsed_text")
    if isinstance(text, str):
        return len(text)
    parse_result = record.get("parse_result")
    if isinstance(parse_result, dict):
        return len(str(parse_result.get("parsed_text") or parse_result.get("text") or ""))
    return 0


def risk_notes(record: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if is_known_mock(record):
        notes.append("known_mock_denylist")
    elif is_suspected_mock(record):
        notes.append("suspected_mock_candidate_only")
    if is_tender_attachment(record):
        notes.append("招标文件附件不能替代完整招标文件正文")
    if is_full_tender_document_candidate(record):
        notes.append("full_tender_document_candidate")
    return notes


def record_name(record: dict[str, Any]) -> str:
    return str(record.get("original_name") or record.get("name") or record.get("file_name") or "")


def searchable_record_text(record: dict[str, Any]) -> str:
    parts = [record_name(record), str(record.get("category") or ""), str(record.get("source_folder") or ""), str(record.get("parsed_text") or "")]
    for asset in list(record.get("assets") or []):
        if isinstance(asset, dict):
            parts.append(str(asset.get("ocr_text") or ""))
    return "\n".join(parts)


def has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def belongs_to_file_id(path: Path, file_ids: set[str]) -> bool:
    text = str(path)
    return any(file_id in text for file_id in file_ids)


def is_safe_artifact_path(path: Path, storage_dir: Path) -> bool:
    try:
        resolved = path.resolve()
        allowed_roots = [storage_dir.resolve(), (DATA_DIR.parent / "outputs").resolve()]
        return any(root == resolved or root in resolved.parents for root in allowed_roots)
    except Exception:
        return False


def unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def safe_path_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value) or "unknown_lot"


def should_skip_path(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def purge_report_dir(data_dir: Path) -> Path:
    return data_dir / "source_cleanup_reports"


def write_purge_report(report: dict[str, Any], data_dir: Path) -> Path:
    report_dir = purge_report_dir(data_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"purge_mock_materials_{safe_path_name(str(report.get('lot_name') or 'unknown'))}_{timestamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

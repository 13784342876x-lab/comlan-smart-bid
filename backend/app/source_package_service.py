from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from config import (
    INGEST_ALLOWED_EXTENSIONS,
    SOURCE_PACKAGE_ARCHIVE_EXTENSIONS,
    SOURCE_PACKAGE_MAX_ARCHIVE_DEPTH,
    SOURCE_PACKAGE_MAX_FILE_COUNT,
    SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES,
    SOURCE_PACKAGE_MAX_TOTAL_UNCOMPRESSED_BYTES,
)
from app.file_store import resolve_storage_key


PACKAGE_MANIFEST_SCHEMA_VERSION = "source_package_manifest_v1"
PROJECT_PROFILE_SCHEMA_VERSION = "project_package_profile_v1"
REFERENCE_RESOLUTION_SCHEMA_VERSION = "source_reference_resolution_v1"
PACKAGE_LEAF_EXTENSIONS = {"doc", "docx", "pdf", "xls", "xlsx", "ppt", "pptx", "txt", "md"}


class SourcePackageError(RuntimeError):
    pass


class _IngestParseService(Protocol):
    def ingest_file(self, path: Path, category: str, tags: list[str]) -> dict[str, Any]: ...
    def parse_file(self, file_id: str) -> dict[str, Any]: ...
    @property
    def metadata(self): ...


class SourcePackageService:
    """Safely expands a file, directory, or recursive ZIP package.

    It intentionally stops at ingest/register + parse.  Indexing remains an
    explicit KnowledgeBaseService action, so procurement packages never enter
    the enterprise Chroma collection merely by being expanded.
    """

    def __init__(
        self,
        *,
        output_root: str | Path,
        max_archive_depth: int = SOURCE_PACKAGE_MAX_ARCHIVE_DEPTH,
        max_file_count: int = SOURCE_PACKAGE_MAX_FILE_COUNT,
        max_single_file_bytes: int = SOURCE_PACKAGE_MAX_SINGLE_FILE_BYTES,
        max_total_uncompressed_bytes: int = SOURCE_PACKAGE_MAX_TOTAL_UNCOMPRESSED_BYTES,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.max_archive_depth = max_archive_depth
        self.max_file_count = max_file_count
        self.max_single_file_bytes = max_single_file_bytes
        self.max_total_uncompressed_bytes = max_total_uncompressed_bytes

    def discover(self, source: str | Path, *, package_id: str | None = None) -> dict[str, Any]:
        root = Path(source).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"source package path not found: {root}")
        if not root.is_file() and not root.is_dir():
            raise SourcePackageError(f"source package must be a file or folder: {root}")
        package_id = package_id or f"pkg_{uuid.uuid4().hex[:16]}"
        package_dir = (self.output_root / "source_packages" / package_id).resolve()
        extraction_root = package_dir / "expanded"
        extraction_root.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "schema_version": PACKAGE_MANIFEST_SCHEMA_VERSION,
            "package_id": package_id,
            "source_path": str(root),
            "package_root": str(package_dir),
            "manifest_path": str(package_dir / "package_manifest.json"),
            "created_at": _utc_now(),
            "archive_depth_max": 0,
            "files": [],
            "archives": [],
            "warnings": [],
            "failures": [],
            "summary": {},
        }
        state = {"file_count": 0, "total_uncompressed": 0}
        if root.is_file():
            self._visit_path(root, manifest, state, extraction_root, archive_depth=0, parent_archive_id=None, internal_path=None)
        else:
            for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
                if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts):
                    self._visit_path(path, manifest, state, extraction_root, archive_depth=0, parent_archive_id=None, internal_path=str(path.relative_to(root)))
        self._fold_duplicates(manifest)
        manifest["summary"] = self._summary(manifest)
        self._write_manifest(manifest)
        return manifest

    def ingest_and_parse(
        self,
        manifest: dict[str, Any],
        service: _IngestParseService,
        *,
        category: str = "待分类",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        tags = list(tags or ["自动识别", "source_package"])
        canonical_by_sha: dict[str, dict[str, Any]] = {}
        for entry in manifest.get("files") or []:
            if entry.get("file_type") not in PACKAGE_LEAF_EXTENSIONS:
                continue
            if entry.get("duplicate_of"):
                canonical = canonical_by_sha.get(str(entry.get("source_sha256") or ""))
                if canonical:
                    entry["file_id"] = canonical.get("file_id")
                    entry["logical_source_id"] = canonical.get("logical_source_id")
                    entry["parsed_status"] = canonical.get("parsed_status")
                continue
            path = Path(str(entry["extracted_path"])).resolve()
            try:
                record = service.ingest_file(path, category, tags)
                file_id = str(record["id"])
                lineage = self._lineage_patch(entry, manifest["package_id"])
                service.metadata.update_file(file_id, lineage)
                parsed = service.parse_file(file_id)
                entry.update(
                    {
                        "file_id": file_id,
                        "stored_path": str(resolve_storage_key(str(record.get("storage_key") or "")).resolve()),
                        "logical_source_id": file_id,
                        "parsed_status": "success" if parsed.get("ok") else "failed",
                        "extraction_status": "ingested_parsed" if parsed.get("ok") else "parse_failed",
                    }
                )
                canonical_by_sha[str(entry.get("source_sha256") or "")] = entry
            except Exception as exc:
                entry["parsed_status"] = "failed"
                entry["extraction_status"] = "ingest_or_parse_failed"
                entry["failure"] = str(exc)
                manifest.setdefault("failures", []).append({"path": entry.get("archive_internal_path"), "reason": str(exc)})
        manifest["summary"] = self._summary(manifest)
        self._write_manifest(manifest)
        return manifest

    def _visit_path(self, path: Path, manifest: dict[str, Any], state: dict[str, int], extraction_root: Path, *, archive_depth: int, parent_archive_id: str | None, internal_path: str | None) -> None:
        ext = path.suffix.lower().lstrip(".")
        # OOXML documents (docx/xlsx/pptx) are ZIP containers too, but are
        # leaf documents for this service and must go through document_parser.
        if ext in SOURCE_PACKAGE_ARCHIVE_EXTENSIONS:
            self._expand_archive(path, manifest, state, extraction_root, archive_depth=archive_depth + 1, parent_archive_id=parent_archive_id, internal_path=internal_path)
            return
        self._add_leaf(path, manifest, state, archive_depth=archive_depth, parent_archive_id=parent_archive_id, internal_path=internal_path)

    def _expand_archive(self, path: Path, manifest: dict[str, Any], state: dict[str, int], extraction_root: Path, *, archive_depth: int, parent_archive_id: str | None, internal_path: str | None) -> None:
        archive_id = f"archive_{uuid.uuid4().hex[:12]}"
        archive_item = {
            "archive_id": archive_id,
            "original_name": path.name,
            "parent_archive_id": parent_archive_id,
            "archive_internal_path": internal_path or path.name,
            "archive_depth": archive_depth,
            "source_sha256": _sha256(path),
            "status": "pending",
        }
        manifest["archives"].append(archive_item)
        manifest["archive_depth_max"] = max(int(manifest.get("archive_depth_max") or 0), archive_depth)
        if archive_depth > self.max_archive_depth:
            archive_item.update({"status": "failed", "reason": "max_archive_depth_exceeded"})
            manifest["failures"].append({"archive_id": archive_id, "reason": "max_archive_depth_exceeded"})
            return
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if any(info.flag_bits & 0x1 for info in infos):
                    raise SourcePackageError("encrypted_archive")
                if archive.testzip() is not None:
                    raise SourcePackageError("corrupted_archive")
                archive_item["status"] = "expanded"
                for info in infos:
                    if info.is_dir():
                        continue
                    self._validate_member(info, state)
                    member_name = _decoded_archive_member_name(info.filename)
                    relative = _safe_archive_path(member_name)
                    target = (extraction_root / archive_id / relative).resolve()
                    base = (extraction_root / archive_id).resolve()
                    if base not in target.parents and target != base:
                        raise SourcePackageError("archive_path_escape")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    member_internal_path = _join_internal_path(internal_path or path.name, relative.as_posix())
                    self._visit_path(target, manifest, state, extraction_root, archive_depth=archive_depth, parent_archive_id=archive_id, internal_path=member_internal_path)
        except (OSError, zipfile.BadZipFile, SourcePackageError) as exc:
            archive_item.update({"status": "failed", "reason": str(exc)})
            manifest["failures"].append({"archive_id": archive_id, "reason": str(exc)})

    def _validate_member(self, info: zipfile.ZipInfo, state: dict[str, int]) -> None:
        if info.file_size > self.max_single_file_bytes:
            raise SourcePackageError("max_single_file_size_exceeded")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SourcePackageError("archive_symlink_member")
        state["file_count"] += 1
        state["total_uncompressed"] += info.file_size
        if state["file_count"] > self.max_file_count:
            raise SourcePackageError("max_file_count_exceeded")
        if state["total_uncompressed"] > self.max_total_uncompressed_bytes:
            raise SourcePackageError("max_total_uncompressed_size_exceeded")

    def _add_leaf(self, path: Path, manifest: dict[str, Any], state: dict[str, int], *, archive_depth: int, parent_archive_id: str | None, internal_path: str | None) -> None:
        ext = path.suffix.lower().lstrip(".")
        if path.stat().st_size > self.max_single_file_bytes:
            manifest["failures"].append({"path": str(path), "reason": "max_single_file_size_exceeded"})
            return
        state["file_count"] += 1
        if state["file_count"] > self.max_file_count:
            raise SourcePackageError("max_file_count_exceeded")
        supported = ext in PACKAGE_LEAF_EXTENSIONS
        manifest["files"].append(
            {
                "source_entry_id": f"source_{uuid.uuid4().hex[:12]}",
                "file_id": None,
                "original_name": path.name,
                "extracted_path": str(path.resolve()),
                "stored_path": None,
                "source_package_id": manifest["package_id"],
                "parent_archive_id": parent_archive_id,
                "archive_internal_path": internal_path or path.name,
                "archive_depth": archive_depth,
                "source_sha256": _sha256(path),
                "file_type": ext or "unknown",
                "parsed_status": "pending" if supported else "not_supported",
                "logical_source_id": None,
                "duplicate_of": None,
                "extraction_status": "discovered" if supported else "ignored_unsupported_file_type",
            }
        )

    @staticmethod
    def _fold_duplicates(manifest: dict[str, Any]) -> None:
        seen: dict[str, dict[str, Any]] = {}
        for entry in manifest.get("files") or []:
            digest = str(entry.get("source_sha256") or "")
            canonical = seen.get(digest)
            if canonical:
                entry["duplicate_of"] = canonical["source_entry_id"]
                entry["logical_source_id"] = canonical["source_entry_id"]
                entry["extraction_status"] = "duplicate_content_folded"
            else:
                entry["logical_source_id"] = entry["source_entry_id"]
                seen[digest] = entry

    @staticmethod
    def _lineage_patch(entry: dict[str, Any], package_id: str) -> dict[str, Any]:
        return {
            "source_package_id": package_id,
            "parent_archive_id": entry.get("parent_archive_id"),
            "archive_internal_path": entry.get("archive_internal_path"),
            "archive_depth": entry.get("archive_depth"),
            "source_sha256": entry.get("source_sha256"),
            "source_package_entry_id": entry.get("source_entry_id"),
            "logical_source_id": entry.get("logical_source_id"),
            "extraction_status": entry.get("extraction_status"),
        }

    @staticmethod
    def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
        files = list(manifest.get("files") or [])
        return {
            "package_id": manifest.get("package_id"),
            "archive_depth": manifest.get("archive_depth_max", 0),
            "discovered_files": len(files),
            "parsed_files": len(
                [item for item in files if item.get("parsed_status") == "success" and not item.get("duplicate_of")]
            ),
            "nested_archives": len(manifest.get("archives") or []),
            "ignored_files": len([item for item in files if item.get("parsed_status") == "not_supported"]),
            "failed_files": len([item for item in files if item.get("parsed_status") == "failed"]) + len(manifest.get("failures") or []),
            "warnings": list(manifest.get("warnings") or []),
            "manifest_path": manifest.get("manifest_path"),
        }

    @staticmethod
    def _write_manifest(manifest: dict[str, Any]) -> None:
        path = Path(manifest["manifest_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


class SourceReferenceResolver:
    """Resolve procurement-document attachment references inside one package scope."""

    def resolve(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        by_id = {str(item.get("id") or item.get("file_id") or ""): item for item in records}
        resolutions: list[dict[str, Any]] = []
        for record in records:
            source_id = str(record.get("id") or record.get("file_id") or "")
            text = str(record.get("parsed_text") or "")
            for match in _find_attachment_references(text):
                expected = _canonical_expected_name(match["expected_name"])
                candidates = self._find_candidates(expected, records, exclude_id=source_id)
                equivalent = len(candidates) > 1 and len({_normalize(str(item.get("original_name") or "")) for item in candidates}) == 1
                resolved = candidates[0] if len(candidates) == 1 or equivalent else None
                resolutions.append(
                    {
                        "referring_source_id": source_id,
                        "source_location": f"text_offset:{match['offset']}",
                        "reference_text": match["reference_text"],
                        "expected_name": expected,
                        "resolved_source_id": str(resolved.get("id") or resolved.get("file_id")) if resolved else None,
                        "match_reason": ("normalized_attachment_name" if len(candidates) == 1 else "equivalent_duplicate_attachment_name") if resolved else None,
                        "confidence": 0.96 if len(candidates) == 1 else (0.86 if resolved else 0.0),
                        "status": "resolved" if resolved else "referenced_source_missing",
                        "candidate_source_ids": [str(item.get("id") or item.get("file_id")) for item in candidates],
                    }
                )
        return {
            "schema_version": REFERENCE_RESOLUTION_SCHEMA_VERSION,
            "references": resolutions,
            "missing_references": [item for item in resolutions if item["status"] == "referenced_source_missing"],
            "status": "needs_review" if any(item["status"] != "resolved" for item in resolutions) else "complete",
        }

    @staticmethod
    def _find_candidates(expected: str, records: list[dict[str, Any]], *, exclude_id: str) -> list[dict[str, Any]]:
        expected_norm = _name_tokens(expected)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            record_id = str(record.get("id") or record.get("file_id") or "")
            if not record_id or record_id == exclude_id:
                continue
            name = str(record.get("original_name") or record.get("name") or "")
            tokens = _name_tokens(name)
            overlap = len(expected_norm & tokens)
            normalized_name = _normalize(name)
            normalized_expected = _normalize(expected)
            if normalized_expected and (
                normalized_expected in normalized_name or normalized_name in normalized_expected
            ):
                overlap = max(overlap, 100)
            if overlap >= max(1, min(2, len(expected_norm))):
                ranked.append((overlap, record))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("original_name") or "")))
        return [item[1] for item in ranked]


class ProjectPackageProfileBuilder:
    """Extract package-scoped project identity and score applicability facts."""

    def build(self, records: list[dict[str, Any]], reference_resolution: dict[str, Any]) -> dict[str, Any]:
        corpus = "\n".join(
            f"=== {item.get('original_name') or item.get('id')} ===\n{item.get('parsed_text') or ''}"
            for item in records
        )
        refs = list(reference_resolution.get("references") or [])
        demand_row = _select_project_demand_row(records)
        profile = {
            "schema_version": PROJECT_PROFILE_SCHEMA_VERSION,
            "procurement_number": demand_row.get("procurement_number") or _first_regex(corpus, r"(?:采购编号|采购文件编号)\s*[：:]?\s*([A-Za-z]{1,8}-\d{4,}[A-Za-z0-9-]*)"),
            "project_number": demand_row.get("project_number") or _first_regex(corpus, r"(?:项目编号)\s*[：:]?\s*([A-Za-z]{1,8}-\d{4,}[A-Za-z0-9-]*)"),
            "project_name": demand_row.get("project_name") or _first_regex(corpus, r"(?:项目名称|采购项目名称)\s*[：:]?\s*([^\n\t]{4,120})"),
            "package_number": demand_row.get("package_number") or _first_regex(corpus, r"(?:包号|包件号|采购包|分包编号)\s*[：:]?\s*(\d{1,4})"),
            "package_name": demand_row.get("package_name") or _first_regex(corpus, r"(?:包名称|包件名称|采购包名称|标段名称)\s*[：:]?\s*([^\n\t]{4,120})"),
            "technical_score_template": demand_row.get("technical_score_template") or _find_template(corpus),
            "price_scoring_method": demand_row.get("price_scoring_method") or _find_price_method(corpus),
            "service_delivery_period": demand_row.get("service_delivery_period") or _first_regex(corpus, r"(?:服务期限|服务期|交付期|履约期限)\s*[：:]?\s*([^\n]{2,100})"),
            "relevant_source_refs": refs,
            "evidence": [],
        }
        weights = demand_row.get("weights") or _find_component_weights(corpus)
        profile.update({
            "business_weight": weights.get("business"),
            "technical_weight": weights.get("technical"),
            "price_weight": weights.get("price"),
        })
        for field in ("procurement_number", "project_number", "project_name", "package_number", "technical_score_template", "price_scoring_method"):
            if profile.get(field):
                profile["evidence"].append({"field": field, "value": profile[field], "source_scope": "package_parsed_sources"})
        return profile


def apply_project_profile_to_score_parse(score_parse: dict[str, Any], project_profile: dict[str, Any] | None) -> dict[str, Any]:
    """Apply deterministic package facts to v2 extraction without changing its extractor."""
    result = deepcopy(score_parse)
    profile = project_profile or {}
    template = str(profile.get("technical_score_template") or "").strip()
    candidates = result.get("technical_score_table_candidates") or []
    if template:
        matches = [item for item in candidates if _normalize(template) in _normalize(str(item.get("heading") or "")) or _normalize(str(item.get("heading") or "")) in _normalize(template)]
        if len(matches) == 1:
            selected = matches[0]
            result["selected_technical_candidate_id"] = selected.get("candidate_id")
            result["matched_technical_template"] = selected.get("heading")
            result["technical_score_items"] = deepcopy(selected.get("score_items") or [])
            result["technical_template_unresolved"] = False
            result["technical_template_resolution_status"] = "project_profile_resolved"
            result["technical_template_selection_reason"] = "project package profile exact template match"
            result["technical_template_evidence"] = deepcopy(profile.get("evidence") or [])
    weights = {"business": profile.get("business_weight"), "technical": profile.get("technical_weight"), "price": profile.get("price_weight")}
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in weights.values()) and abs(sum(weights.values()) - 100) < 1e-9:
        result["weights"] = weights
        result["component_weights_unresolved"] = False
        price = result.get("price_score_rule") or {}
        if price:
            price["score"] = weights["price"]
            price["score_range"] = {"min": 0, "max": weights["price"]}
            result["price_score_rule"] = price
    price_method = str(profile.get("price_scoring_method") or "").strip()
    extracted_method = str((result.get("price_scoring_method") or {}).get("method_name") or "")
    if price_method and extracted_method and _normalize(price_method) not in _normalize(extracted_method):
        result["project_profile_price_method_conflict"] = {"profile": price_method, "extracted": extracted_method}
    # A project-profile template choice replaces one score component.  Reuse
    # the score locator's canonical materializer so score_items and its
    # existing derived lists cannot retain the previously selected candidate.
    from app.score_table_locator import materialize_score_item_aggregates

    result = materialize_score_item_aggregates(result)
    result["project_package_profile"] = deepcopy(profile)
    unresolved = bool(result.get("technical_template_unresolved") or result.get("component_weights_unresolved"))
    if not result.get("unsupported_score_format"):
        result["review_gate"] = "technical_template_unresolved" if result.get("technical_template_unresolved") else ("component_weights_unresolved" if result.get("component_weights_unresolved") else None)
        result["status"] = "needs_review" if unresolved else "success"
        result["parsed"] = True
    return result


def _select_project_demand_row(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Select one demand-list row using an independently supplied technical-spec name.

    A demand list can contain many packages; extracting all rows into a single
    corpus would mix their score weights.  A distinct technical specification
    filename/body is a package-scoped selector, not a hard-coded project rule.
    """
    hints = [
        _normalize(str(record.get("original_name") or ""))
        for record in records
        if str(record.get("file_ext") or "").lower() not in {"xls", "xlsx"}
        and ("技术规范" in str(record.get("original_name") or "") or record.get("document_role") == "technical_spec")
    ]
    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in records:
        if str(record.get("file_ext") or "").lower() not in {"xls", "xlsx"}:
            continue
        for table in record.get("tables") or []:
            rows = table.get("rows") or []
            if not rows:
                continue
            normalized_rows = [_table_row_values(row) for row in rows]
            headers = normalized_rows[0]
            columns = _demand_columns(headers)
            if not {"project_name", "project_number", "package_number", "weights", "technical_score_template"}.issubset(columns):
                continue
            for row in normalized_rows[1:]:
                project_name = _at(row, columns["project_name"])
                if not project_name:
                    continue
                normalized_name = _normalize(project_name)
                score = 0
                for hint in hints:
                    if _project_name_matches(normalized_name, hint):
                        score = max(score, 100 + len(normalized_name))
                    elif normalized_name and hint:
                        score = max(score, len(set(_name_tokens(project_name)) & _name_tokens(hint)))
                if score <= 0:
                    continue
                weights = _find_component_weights(_at(row, columns["weights"]))
                candidates.append(
                    (
                        score,
                        {
                            "procurement_number": _at(row, columns.get("procurement_number")),
                            "project_number": _at(row, columns.get("project_number")),
                            "project_name": project_name,
                            "package_number": _at(row, columns.get("package_number")),
                            "package_name": project_name,
                            "technical_score_template": _at(row, columns.get("technical_score_template")),
                            "price_scoring_method": _at(row, columns.get("price_scoring_method")),
                            "service_delivery_period": _at(row, columns.get("service_delivery_period")),
                            "weights": weights,
                        },
                    )
                )
    if not candidates:
        return {}
    candidates.sort(key=lambda item: -item[0])
    return candidates[0][1]


def _table_row_values(row: Any) -> list[str]:
    if isinstance(row, list):
        return [str(value or "") for value in row]
    if isinstance(row, dict):
        return [str(cell.get("value") or "") for cell in row.get("cells") or [] if isinstance(cell, dict)]
    return []


def _demand_columns(headers: list[str]) -> dict[str, int]:
    wanted = {
        "procurement_number": ("采购编号",),
        "project_name": ("采购项目名称", "包名称", "项目名称"),
        "project_number": ("项目编号",),
        "package_number": ("包号", "分包编号"),
        "weights": ("打分权重", "技术商务价格"),
        "technical_score_template": ("技术评分细则模板",),
        "price_scoring_method": ("价格评分办法",),
        "service_delivery_period": ("服务期", "交货期"),
    }
    result: dict[str, int] = {}
    for name, aliases in wanted.items():
        for alias in aliases:
            matched = next(
                (index for index, header in enumerate(headers) if _normalize(alias) in _normalize(header)),
                None,
            )
            if matched is not None:
                result[name] = matched
                break
    return result


def _at(row: list[str], index: int | None) -> str | None:
    if index is None or index >= len(row):
        return None
    value = str(row[index] or "").strip()
    return value or None


def _find_attachment_references(text: str) -> list[dict[str, Any]]:
    patterns = [
        r"(附件\s*\d+(?:[：:]\s*[^\n，。；;]{2,60})?)",
        r"(技术规范书(?:单独成册)?)",
        r"(合同条款(?:单独成册)?)",
        r"(附表[一二三四五六七八九十0-9])",
    ]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            ref = re.sub(r"\s+", "", match.group(1))
            if ref in seen:
                continue
            seen.add(ref)
            result.append({"reference_text": ref, "expected_name": ref.replace("单独成册", ""), "offset": match.start()})
    return result


def _canonical_expected_name(value: str) -> str:
    for anchor in ("项目采购需求清单", "技术规范书", "合同条款", "报价单"):
        index = value.find(anchor)
        if index >= 0:
            prefix = value[:index]
            attachment = re.search(r"附件\s*\d+", prefix)
            return (attachment.group(0) + "：" if attachment else "") + anchor
    return value


def _project_name_matches(normalized_name: str, hint: str) -> bool:
    if not normalized_name or not hint:
        return False
    if normalized_name in hint or hint in normalized_name:
        return True
    strip_year = lambda value: re.sub(r"(?:19|20)\d{2}年?", "", value)
    name_without_year = strip_year(normalized_name)
    hint_without_year = strip_year(hint)
    return bool(name_without_year and (name_without_year in hint_without_year or hint_without_year in name_without_year))


def _find_component_weights(text: str) -> dict[str, int]:
    patterns = [
        r"^\s*(\d{1,3})\s*[:：/]\s*(\d{1,3})\s*[:：/]\s*(\d{1,3})\s*$",
        r"技术\s*[：:]\s*商务\s*[：:]\s*价格\s*[=＝：:]\s*(\d{1,3})\s*[:：/]\s*(\d{1,3})\s*[:：/]\s*(\d{1,3})",
        r"(?:技术|商务|价格)\s*[：:]?\s*(\d{1,3})\s*[:：/]\s*(\d{1,3})\s*[:：/]\s*(\d{1,3})",
        r"技术\s*(\d{1,3})\s*分?\s*[，,、]\s*商务\s*(\d{1,3})\s*分?\s*[，,、]\s*价格\s*(\d{1,3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        technical, business, price = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if technical + business + price == 100:
            return {"technical": technical, "business": business, "price": price}
    return {}


def _find_template(text: str) -> str | None:
    for name in ("服务类技术评分", "产品加工服务技术评分", "科技技术服务技术评分"):
        if name in text:
            return name
    match = re.search(r"(?:技术评分标准选用|技术评分模板)\s*[：:]?\s*([^\n，。；;]{3,80})", text)
    return match.group(1).strip() if match else None


def _find_price_method(text: str) -> str | None:
    for name in ("区间平均价浮动法", "基准价法", "低价优先法"):
        if name in text:
            return name
    return None


def _first_regex(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.I)
    return match.group(1).strip(" ：:。；;") if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SourcePackageError("zip_slip_path")
    return path


def _decoded_archive_member_name(value: str) -> str:
    """Recover common GBK/CP936 ZIP names that lack the UTF-8 flag."""
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        return value
    try:
        recovered = value.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return recovered if any("\u4e00" <= char <= "\u9fff" for char in recovered) else value


def _join_internal_path(parent: str, child: str) -> str:
    return f"{parent}!/{child}"


def _name_tokens(value: str) -> set[str]:
    cleaned = _normalize(value)
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", cleaned))
    return tokens or {cleaned} if cleaned else set()


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

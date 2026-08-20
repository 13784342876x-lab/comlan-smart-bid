from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.enterprise_knowledge_query import query_enterprise_evidence_candidates
from app.metadata_store import MetadataStore
from app.technical_agent_context_loader import load_technical_agent_context
from app.technical_placeholders import attach_placeholder_identity
from app.technical_tool_registry import RegisteredTool, TechnicalToolRegistry, object_schema
from app.technical_writing_quality import (
    collect_applicable_tender_tables,
    tender_response_policy,
)
from app.web_research_tools import open_web_page, search_web
from app.web_search_provider import NotConfiguredSearchProvider


EVIDENCE_SUBJECT_MAP = {
    "company_qualification": "enterprise_qualification",
    "manufacturer_qualification": "other_enterprise_evidence",
    "product_certificate": "other_enterprise_evidence",
    "product_test_report": "other_enterprise_evidence",
    "product_parameter_proof": "other_enterprise_evidence",
    "manufacturer_official_document": "other_enterprise_evidence",
    "company_performance": "project_performance",
    "person_resume": "personnel_resume",
    "person_certificate": "personnel_certificate",
    "service_network": "service_capability",
    "service_commitment": "service_capability",
    "implementation_experience": "personnel_project_experience",
    "quality_management_material": "management_system_certificate",
    "training_material": "other_enterprise_evidence",
    "green_supply_chain_evidence": "other_enterprise_evidence",
}
FORBIDDEN_EVIDENCE_NAME_PARTS = ("reference_only", "mock_业绩", "mock业绩", "测试业绩")


@dataclass
class TechnicalWritingToolContext:
    response_outline_path: Path
    writing_outline_path: Path
    task_information_path: Path
    output_root: Path
    output_key: str
    target_chapter_id: str | None
    target_section_id: str | None
    draft_units_path: Path | None = None
    confirmed_project_facts_path: Path | None = None
    metadata_store: MetadataStore = field(default_factory=MetadataStore)
    web_provider: Any = field(default_factory=NotConfiguredSearchProvider)
    web_disabled: bool = False
    web_search_policy: str = "auto"
    web_page_opener: Any = None
    web_resolver: Any = None
    pending_confirmations: list[dict[str, Any]] = field(default_factory=list)
    local_evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    web_refs: list[dict[str, Any]] = field(default_factory=list)
    web_search_attempts: list[dict[str, Any]] = field(default_factory=list)
    web_page_reads: list[dict[str, Any]] = field(default_factory=list)
    working_draft_markdown: str = ""
    saved_sections: list[dict[str, Any]] = field(default_factory=list)
    table_fill_data_records: list[dict[str, Any]] = field(default_factory=list)
    consulted_requirement_ids: list[str] = field(default_factory=list)
    consulted_score_item_ids: list[str] = field(default_factory=list)
    consulted_tender_table_ids: list[str] = field(default_factory=list)
    consulted_knowledge_candidate_ids: list[str] = field(default_factory=list)
    selected_material_ids: list[str] = field(default_factory=list)
    prohibited_material_ids_detected: list[str] = field(default_factory=list)
    existing_draft_markdown: str = ""
    existing_draft_sha256: str = ""
    previous_revision_round: int = 0

    def __post_init__(self) -> None:
        for name in ("response_outline_path", "writing_outline_path", "task_information_path"):
            value = Path(getattr(self, name)).expanduser().resolve()
            if not value.is_file():
                raise FileNotFoundError(f"required planning file not found: {value}")
            setattr(self, name, value)
        for name in ("draft_units_path", "confirmed_project_facts_path"):
            value = getattr(self, name)
            setattr(self, name, Path(value).expanduser().resolve() if value else None)
        if bool(self.target_chapter_id) == bool(self.target_section_id):
            raise ValueError("provide exactly one target_chapter_id or target_section_id")
        if not re.fullmatch(r"[0-9A-Za-z._-]+", self.output_key):
            raise ValueError("output_key contains unsafe characters")
        if self.web_search_policy not in {"auto", "required", "disabled"}:
            raise ValueError("web_search_policy must be auto, required or disabled")
        if self.web_search_policy == "disabled":
            self.web_disabled = True
        self.output_root = Path(self.output_root).expanduser().resolve()
        self._response = _load_json(self.response_outline_path)
        self._writing = _load_json(self.writing_outline_path)
        self._tasks = _load_json(self.task_information_path)
        self._target_id = str(self.target_section_id or self.target_chapter_id)
        self._target, self._root = _find_node(self._writing.get("chapters") or [], self._target_id)
        if self._target is None or self._root is None:
            raise ValueError(f"target not found in writing outline: {self._target_id}")
        self._allowed_response_ids = set(_collect_values(self._target, "response_point_ids"))
        self._allowed_web = _collect_web_policy(self._target)
        self.tender_tables = collect_applicable_tender_tables(
            self._response,
            self._allowed_response_ids,
        )
        self._tender_tables_by_point = {
            str(item.get("response_point_id") or ""): item for item in self.tender_tables
        }
        if self.draft_path.is_file():
            candidate = self.draft_path.read_text(encoding="utf-8-sig")
            if _is_valid_existing_markdown(candidate):
                self.existing_draft_markdown = candidate
                self.existing_draft_sha256 = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
                previous_report = self.draft_path.with_name(f"{self._target_id}.run.json")
                if previous_report.is_file():
                    try:
                        report = _load_json(previous_report)
                    except (ValueError, OSError, json.JSONDecodeError):
                        report = {}
                    self.previous_revision_round = max(int(report.get("revision_round") or 0), 0)
                    for raw in report.get("pending_confirmations") or []:
                        if not isinstance(raw, dict):
                            continue
                        try:
                            item = attach_placeholder_identity(raw)
                        except ValueError:
                            continue
                        item.setdefault(
                            "target",
                            {
                                "chapter_id": self.target_chapter_id,
                                "section_id": self.target_section_id,
                                "affected_sections": list(
                                    item.get("affected_sections") or []
                                ),
                            },
                        )
                        item["candidate_ids"] = list(
                            item.get("candidate_ids")
                            or [
                                str(
                                    candidate.get("candidate_id")
                                    or candidate.get("evidence_id")
                                    or ""
                                )
                                if isinstance(candidate, dict)
                                else str(candidate)
                                for candidate in item.get("candidates") or []
                                if candidate
                            ][:3]
                        )
                        if item not in self.pending_confirmations:
                            self.pending_confirmations.append(item)

    @property
    def draft_path(self) -> Path:
        return self.output_root / self.output_key / "drafts" / f"{self._target_id}.md"

    @property
    def has_existing_draft(self) -> bool:
        return bool(self.existing_draft_markdown)

    def get_writing_scope(self, args: dict[str, Any]) -> dict[str, Any]:
        self._assert_target(args)
        context = load_technical_agent_context(
            self._response,
            self._writing,
            target_chapter_id=self.target_chapter_id,
            target_section_id=self.target_section_id,
        )
        return {
            "status": "success",
            "target_outline": context["target_outline"],
            "score_items": context["score_items"],
            "information_requirements": context["information_requirements"],
            "generation_constraints": context["generation_constraints"],
        }

    def get_response_requirements(self, args: dict[str, Any]) -> dict[str, Any]:
        self._assert_target(args)
        requested = set(str(item) for item in args.get("response_point_ids") or [])
        if requested and not requested <= self._allowed_response_ids:
            raise PermissionError("requested response point is outside the target writing scope")
        selected = requested or self._allowed_response_ids
        points = []
        for chapter in self._response.get("chapters") or []:
            for point in chapter.get("response_points") or []:
                point_id = str(point.get("point_id") or "")
                if point_id not in selected:
                    continue
                item = {
                    "point_id": point_id,
                    "requirement_id": point_id,
                    "original_text": str(point.get("original_text") or ""),
                    "mandatory_marker": point.get("mandatory_marker"),
                    "content_type": point.get("content_type"),
                    "source_refs": copy.deepcopy(point.get("source_refs") or []),
                    "score_item_refs": _score_refs_for_point(self._writing, point_id),
                }
                item["source_location"] = _requirement_source_location(
                    item["source_refs"]
                )
                item["substantive"] = _is_substantive_requirement(point)
                item["response_form"] = _requirement_response_form(point)
                item["requires_specific_value"] = _requires_specific_value(point)
                item["requires_proof"] = _requires_proof(point)
                item["score_items"] = _score_items_for_point(
                    self._writing,
                    point_id,
                )
                table = point.get("table")
                if isinstance(table, dict):
                    item.pop("original_text", None)
                    item["table_access"] = {
                        "row_count": len(table.get("rows") or []),
                        "included": False,
                        "reason": "large table is not part of the target scope",
                    }
                    item["table_writing_specification"] = copy.deepcopy(
                        self._tender_tables_by_point.get(point_id)
                        or _table_writing_specification(table)
                    )
                points.append(item)
        requirement_ids = [str(item["point_id"]) for item in points]
        score_item_ids = [
            str(score.get("score_item_id") or "")
            for item in points
            for score in item.get("score_items") or []
            if score.get("score_item_id")
        ]
        table_ids = [
            str(
                (item.get("table_writing_specification") or {}).get("table_id")
                or ""
            )
            for item in points
            if item.get("table_writing_specification")
        ]
        self.consulted_requirement_ids.extend(
            value
            for value in requirement_ids
            if value not in self.consulted_requirement_ids
        )
        self.consulted_score_item_ids.extend(
            value
            for value in score_item_ids
            if value and value not in self.consulted_score_item_ids
        )
        self.consulted_tender_table_ids.extend(
            value
            for value in table_ids
            if value and value not in self.consulted_tender_table_ids
        )
        return {
            "status": "success",
            "requirements": points,
            "response_point_ids": sorted(selected),
            "score_item_ids": sorted(set(score_item_ids)),
            "tender_table_ids": sorted(set(value for value in table_ids if value)),
            "target_scoped": True,
        }

    def get_task_information(self, args: dict[str, Any]) -> dict[str, Any]:
        self._assert_target(args)
        tasks = []
        for task in self._tasks.get("tasks") or []:
            if str(task.get("chapter_id") or "") != str(self._root.get("chapter_id") or ""):
                continue
            if self.target_section_id and str(task.get("section_id") or "") != self.target_section_id:
                continue
            tasks.append(
                {
                    "task_id": task.get("task_id"),
                    "section_id": task.get("section_id"),
                    "target_path": task.get("target_path") or [],
                    "response_point_ids": task.get("response_point_ids") or [],
                    "score_item_refs": task.get("score_item_refs") or [],
                    "information_requirements": task.get("information_requirements") or {},
                    "draftability": task.get("draftability") or {},
                }
            )
        self.consulted_score_item_ids.extend(
            value
            for value in (
                str(score_id)
                for task in tasks
                for score_id in task.get("score_item_refs") or []
            )
            if value and value not in self.consulted_score_item_ids
        )
        return {"status": "success", "task_count": len(tasks), "tasks": tasks}

    def get_draft_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        self._assert_target(args)
        if not self.draft_units_path or not self.draft_units_path.is_file():
            return {"status": "not_available", "draft_units": []}
        payload = _load_json(self.draft_units_path)
        allowed_task_ids = {
            str(task.get("task_id") or "")
            for task in self._tasks.get("tasks") or []
            if str(task.get("chapter_id") or "")
            == str(self._root.get("chapter_id") or "")
            and (
                not self.target_section_id
                or str(task.get("section_id") or "") == self.target_section_id
            )
        }
        units = [
            copy.deepcopy(item)
            for item in payload.get("draft_units") or []
            if str(item.get("chapter_id") or "") == str(self._root.get("chapter_id") or "")
            and (
                not self.target_section_id
                or bool(
                    allowed_task_ids
                    & {
                        str(task_id)
                        for task_id in item.get("task_ids") or []
                    }
                )
                or str(item.get("section_id") or "") == self.target_section_id
            )
        ]
        return {
            "status": "success",
            "advisory_only": True,
            "agent_retains_final_planning_authority": True,
            "target_scoped": True,
            "draft_units": units,
        }

    def get_confirmed_project_facts(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.confirmed_project_facts_path or not self.confirmed_project_facts_path.is_file():
            return {"status": "not_available", "facts": {}}
        payload = _load_json(self.confirmed_project_facts_path)
        facts: dict[str, Any] = {}
        fact_records: dict[str, Any] = {}
        for key, value in dict(payload.get("facts") or {}).items():
            if isinstance(value, dict) and str(value.get("status") or value.get("confirmation_status") or "") == "confirmed":
                facts[str(key)] = value.get("value")
                fact_records[str(key)] = copy.deepcopy(value)
        return {
            "status": "success",
            "facts": facts,
            "fact_records": fact_records,
            "unconfirmed_values_omitted": True,
        }

    def query_local_knowledge(self, args: dict[str, Any]) -> dict[str, Any]:
        evidence_types = [str(item) for item in args.get("evidence_types") or []]
        subjects = list(dict.fromkeys(EVIDENCE_SUBJECT_MAP[item] for item in evidence_types if item in EVIDENCE_SUBJECT_MAP))
        payload = query_enterprise_evidence_candidates(
            str(args.get("query") or ""),
            self.metadata_store.load(),
            evidence_subject_types=subjects,
            expected_evidence_types=evidence_types,
        )
        results = []
        for candidate in payload.get("candidates") or []:
            record = candidate.get("record") or {}
            name = str(candidate.get("original_name") or "")
            storage_key = str(candidate.get("storage_key") or "")
            candidate_id = f"KB-{candidate.get('file_id')}"
            evidence_status = _evidence_status(record)
            if (
                _forbidden_evidence(name, storage_key)
                or evidence_status in {"rejected", "unusable"}
            ):
                if candidate_id not in self.prohibited_material_ids_detected:
                    self.prohibited_material_ids_detected.append(candidate_id)
                continue
            entity_name = _entity_name(record)
            wanted_names = {str(item) for item in args.get("entity_names") or [] if str(item)}
            if wanted_names and entity_name not in wanted_names:
                continue
            result = {
                "candidate_id": candidate_id,
                "evidence_id": str(
                    record.get("evidence_id")
                    or record.get("id")
                    or candidate.get("file_id")
                    or ""
                ),
                "evidence_type": _best_evidence_type(evidence_types, candidate),
                "entity_type": "person" if candidate.get("material_group") == "personnel" else "enterprise_material",
                "entity_name": entity_name,
                "file_id": candidate.get("file_id"),
                "file_name": name,
                "source_location": _source_location(record),
                "excerpt": _excerpt(record, 1200),
                "metadata": {
                    "material_group": candidate.get("material_group"),
                    "matched_terms": candidate.get("matched_terms") or [],
                    "physical_file_exists": candidate.get("physical_file_exists"),
                    "department": _first_present(
                        record,
                        "department",
                        "owner_department",
                        "belonging_department",
                    ),
                    "owner": _first_present(
                        record,
                        "owner",
                        "person_name",
                        "product_name",
                        "project_name",
                    ),
                    "valid_from": _first_present(
                        record,
                        "valid_from",
                        "effective_date",
                        "issued_at",
                    ),
                    "valid_until": _first_present(
                        record,
                        "valid_until",
                        "expiry_date",
                        "expires_at",
                    ),
                },
                "evidence_status": evidence_status,
                "selection_status": "candidate",
                "auto_selected": False,
                "warnings": [],
            }
            conflicts: list[str] = []
            requested_department = str(args.get("department") or "").strip()
            actual_department = str(
                result["metadata"].get("department") or ""
            ).strip()
            if (
                requested_department
                and actual_department
                and requested_department.casefold()
                != actual_department.casefold()
            ):
                conflicts.append("department_mismatch")
            requested_project_type = str(
                args.get("project_type") or ""
            ).strip()
            if (
                requested_project_type
                and requested_project_type.casefold()
                not in json.dumps(record, ensure_ascii=False).casefold()
            ):
                conflicts.append("project_type_not_verified")
            if (
                args.get("validity_requirement")
                and not result["metadata"].get("valid_until")
            ):
                conflicts.append("validity_not_verified")
            valid_until = str(
                result["metadata"].get("valid_until") or ""
            )
            iso_expiry = re.search(r"\d{4}-\d{2}-\d{2}", valid_until)
            if (
                iso_expiry
                and iso_expiry.group(0) < date.today().isoformat()
            ):
                conflicts.append("material_expired")
            requested_entity_types = {
                str(item)
                for item in args.get("entity_types") or []
                if str(item)
            }
            if (
                requested_entity_types
                and result["entity_type"] not in requested_entity_types
            ):
                conflicts.append("entity_type_mismatch")
            result["match_conflicts"] = conflicts
            result["match_score"] = (
                len(result["metadata"].get("matched_terms") or [])
                + (4 if evidence_status == "confirmed" else 0)
                - 5 * len(conflicts)
            )
            if evidence_status in {"candidate", "needs_review", "unknown"}:
                result["warnings"].append(
                    "material is not confirmed and must not be promoted to a fact"
                )
            results.append(result)
        top_k = min(max(int(args.get("top_k") or 10), 1), 20)
        results.sort(
            key=lambda item: (
                bool(item.get("match_conflicts")),
                -int(item.get("match_score") or 0),
                str(item.get("candidate_id") or ""),
            )
        )
        results = results[:top_k]
        confirmed = [
            item
            for item in results
            if item.get("evidence_status") == "confirmed"
            and not item.get("match_conflicts")
            and bool(
                item.get("source_location")
                and (
                    item.get("excerpt")
                    or item.get("metadata", {}).get("physical_file_exists")
                    is not False
                )
            )
        ]
        if len(confirmed) == 1:
            selected_item = confirmed[0]
            selected_item["selection_status"] = "confirmed"
            selected_item["auto_selected"] = True
            selected_item["selection_reason"] = (
                "unique confirmed target-scoped candidate with no detected conflict"
            )
            selected_id = str(selected_item.get("candidate_id") or "")
            if selected_id and selected_id not in self.selected_material_ids:
                self.selected_material_ids.append(selected_id)
        elif len(confirmed) > 1:
            for item in confirmed[:3]:
                item["selection_reason"] = (
                    "multiple confirmed candidates require project-specific selection"
                )
        self.local_evidence_refs.extend(_unique_refs(results, self.local_evidence_refs, "candidate_id"))
        self.consulted_knowledge_candidate_ids.extend(
            value
            for value in (
                str(item.get("candidate_id") or "") for item in results
            )
            if value and value not in self.consulted_knowledge_candidate_ids
        )
        return {
            "status": "success" if results else "no_results",
            "query_summary": {
                "query": args.get("query"),
                "evidence_types": evidence_types,
                "retrieval_mode": "structured_metadata_and_keyword_candidate_query",
                "semantic_vector_search_used": False,
            },
            "results": results,
            "recommended_candidates": [
                item.get("candidate_id") for item in results[:3]
            ],
            "auto_selected_material_ids": [
                item.get("candidate_id")
                for item in results
                if item.get("auto_selected")
            ],
            "human_confirmation_recommendation": (
                {
                    "required": True,
                    "reason": (
                        "multiple candidates are similarly applicable"
                        if len(results) > 1
                        else "the only candidate is not confirmed"
                    ),
                    "candidate_ids": [
                        item.get("candidate_id") for item in results[:3]
                    ],
                    "recommended_candidate": (
                        results[0].get("candidate_id") if results else None
                    ),
                    "recommendation_reason": (
                        "highest target-scoped metadata match; objective applicability remains unconfirmed"
                    ),
                    "record_with": "record_pending_confirmation",
                }
                if results
                and not any(item.get("auto_selected") for item in results)
                else {
                    "required": False,
                    "reason": "unique confirmed candidate auto-selected",
                    "candidate_ids": [],
                    "recommended_candidate": None,
                    "recommendation_reason": "",
                    "record_with": None,
                }
            ),
            "prohibited_material_ids_detected": list(
                self.prohibited_material_ids_detected
            ),
        }

    def read_local_evidence(self, args: dict[str, Any]) -> dict[str, Any]:
        file_id = str(args.get("file_id") or "")
        allowed_file_ids = {str(item.get("file_id") or "") for item in self.local_evidence_refs}
        if file_id not in allowed_file_ids:
            raise PermissionError("file_id must come from a prior query_local_knowledge candidate result")
        try:
            record = self.metadata_store.get_file(file_id)
        except KeyError:
            return {"status": "not_found", "file_id": file_id}
        evidence_status = _evidence_status(record)
        if (
            _forbidden_evidence(
                str(record.get("original_name") or ""),
                str(record.get("storage_key") or ""),
            )
            or evidence_status in {"rejected", "unusable"}
        ):
            raise PermissionError("reference-only, mock and test evidence cannot be read")
        max_chars = min(max(int(args.get("max_chars") or 8000), 1), 12000)
        actual_location = _source_location(record)
        requested_location = str(args.get("source_location") or "")
        if requested_location and requested_location != actual_location:
            raise PermissionError("source_location does not match metadata")
        return {
            "status": "success",
            "file_id": file_id,
            "file_name": record.get("original_name"),
            "source_location": actual_location,
            "content": _excerpt(record, max_chars),
            "evidence_status": evidence_status,
            "selection_status": next(
                (
                    item.get("selection_status")
                    for item in self.local_evidence_refs
                    if str(item.get("file_id") or "") == file_id
                ),
                "candidate",
            ),
            "warning": "原文片段仍是候选材料，不等于本项目已确认事实。",
        }

    def web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        self._ensure_web_allowed(args)
        result = search_web(
            self.web_provider,
            query=str(args.get("query") or ""),
            purpose=str(args.get("purpose") or ""),
            allowed_source_types=[str(item) for item in args.get("allowed_source_types") or []],
            allowed_domains=[str(item) for item in args.get("allowed_domains") or []],
            recency_days=args.get("recency_days"),
            top_k=int(args.get("top_k") or 5),
        )
        self.web_refs.extend(_unique_refs(result.get("results") or [], self.web_refs, "url"))
        self.web_search_attempts.append(
            {
                "query": str(args.get("query") or ""),
                "purpose": str(args.get("purpose") or ""),
                "status": result.get("status"),
                "result_count": len(result.get("results") or []),
                "provider": result.get("provider") or getattr(self.web_provider, "name", None),
            }
        )
        return result

    def open_web_page(self, args: dict[str, Any]) -> dict[str, Any]:
        self._ensure_web_allowed(args)
        url = str(args.get("url") or "")
        if url not in {str(item.get("url") or "") for item in self.web_refs}:
            raise PermissionError("URL must come from a prior web_search result")
        kwargs: dict[str, Any] = {
            "purpose": str(args.get("purpose") or ""),
            "max_chars": int(args.get("max_chars") or 12000),
        }
        if self.web_page_opener is not None:
            kwargs["opener"] = self.web_page_opener
        if self.web_resolver is not None:
            kwargs["resolver"] = self.web_resolver
        result = open_web_page(url, **kwargs)
        if result.get("status") == "success":
            self.web_refs.extend(_unique_refs([result], self.web_refs, "url"))
            self.web_page_reads.append(
                {
                    "url": url,
                    "purpose": str(args.get("purpose") or ""),
                    "status": "success",
                    "content_hash": result.get("content_hash"),
                }
            )
        return result

    def record_pending_confirmation(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_items = list(args.get("items") or [])
        if not raw_items:
            raw_items = [args]
        recorded = 0
        mappings = []
        for value in raw_items:
            if self._pending_confirmation_is_redundant(value):
                continue
            item = attach_placeholder_identity({
                "field": value["field"],
                "question": value["question"],
                "reason": value["reason"],
                "candidates": copy.deepcopy(value.get("candidates") or []),
                "affected_sections": list(value.get("affected_sections") or []),
                "blocking": bool(value.get("blocking", True)),
                "status": "pending",
                "placeholder_id": value.get("placeholder_id"),
                "category": value.get("category"),
                "required_confirmation": value.get("required_confirmation")
                or value.get("question"),
                "recommended_candidate": value.get("recommended_candidate"),
                "recommendation_reason": value.get("recommendation_reason"),
                "source_references": copy.deepcopy(
                    value.get("source_references") or []
                ),
                "severity": value.get("severity")
                or ("blocking" if value.get("blocking", True) else "warning"),
                "release_blocking": bool(
                    value.get(
                        "release_blocking",
                        value.get("blocking", True),
                    )
                ),
            })
            item["field_name"] = item["field"]
            item["target"] = {
                "chapter_id": self.target_chapter_id,
                "section_id": self.target_section_id,
                "affected_sections": list(item.get("affected_sections") or []),
            }
            item["candidate_ids"] = [
                str(
                    candidate.get("candidate_id")
                    or candidate.get("evidence_id")
                    or ""
                )
                if isinstance(candidate, dict)
                else str(candidate)
                for candidate in item.get("candidates") or []
                if candidate
            ][:3]
            matched_evidence = [
                evidence
                for evidence in self.local_evidence_refs
                if str(evidence.get("candidate_id") or "")
                in set(item["candidate_ids"])
            ]
            if not item.get("source_references"):
                item["source_references"] = [
                    {
                        "candidate_id": evidence.get("candidate_id"),
                        "evidence_id": evidence.get("evidence_id"),
                        "file_id": evidence.get("file_id"),
                        "file_name": evidence.get("file_name"),
                        "source_location": evidence.get("source_location"),
                        "evidence_status": evidence.get("evidence_status"),
                    }
                    for evidence in matched_evidence
                ]
            if (
                not item.get("recommended_candidate")
                and item["candidate_ids"]
            ):
                item["recommended_candidate"] = item["candidate_ids"][0]
            if (
                item.get("recommended_candidate")
                and not item.get("recommendation_reason")
            ):
                item["recommendation_reason"] = (
                    "highest-ranked target-scoped candidate; final applicability requires confirmation"
                )
            existing = next(
                (
                    candidate
                    for candidate in self.pending_confirmations
                    if candidate.get("placeholder_id") == item["placeholder_id"]
                ),
                None,
            )
            if existing is None:
                self.pending_confirmations.append(item)
                recorded += 1
            else:
                existing["affected_sections"] = list(
                    dict.fromkeys(
                        [
                            *list(existing.get("affected_sections") or []),
                            *list(item.get("affected_sections") or []),
                        ]
                    )
                )
                existing["candidates"] = [
                    *list(existing.get("candidates") or []),
                    *[
                        candidate
                        for candidate in item.get("candidates") or []
                        if candidate not in list(existing.get("candidates") or [])
                    ],
                ]
                existing["blocking"] = bool(existing.get("blocking") or item.get("blocking"))
            mappings.append(
                {
                    "field": item["field"],
                    "placeholder_id": item["placeholder_id"],
                    "placeholder_token": item["placeholder_token"],
                }
            )
        return {
            "status": "recorded" if mappings else "not_required",
            "recorded_count": recorded,
            "pending_count": len(self.pending_confirmations),
            "placeholders": mappings,
        }

    def _pending_confirmation_is_redundant(
        self,
        value: dict[str, Any],
    ) -> bool:
        combined = " ".join(
            str(value.get(key) or "")
            for key in ("field", "question", "reason")
        )
        if re.search(
            r"(?:正偏离|优于|提前|延长|缩短|候选|尚未选定|"
            r"冲突|实际值|投标人自身|自行填写)",
            combined,
        ):
            return False
        confirmed = self.get_confirmed_project_facts({}).get("fact_records") or {}
        normalized_field = re.sub(r"\W+", "", str(value.get("field") or "")).casefold()
        if normalized_field and any(
            normalized_field in re.sub(
                r"\W+", "", f"{key} {json.dumps(record, ensure_ascii=False)}"
            ).casefold()
            for key, record in confirmed.items()
        ):
            return True
        confirmed_blob = json.dumps(confirmed, ensure_ascii=False)
        semantic_confirmed_fields = (
            (
                ("工作年限", "项目管理年限", "从业年限"),
                ("work_years", "management_years", "工作年限", "项目管理年限"),
            ),
            (
                ("同类项目", "项目数量"),
                ("similar_project_count", "同类项目数量"),
            ),
            (
                ("证书", "资质", "资格"),
                ("certificate", "证书", "资质", "资格"),
            ),
            (
                ("人员姓名", "项目经理姓名", "负责人姓名"),
                ("person_name", "人员姓名", "项目经理", "负责人"),
            ),
        )
        if any(
            any(term in combined for term in field_terms)
            and any(term in confirmed_blob for term in fact_terms)
            for field_terms, fact_terms in semantic_confirmed_fields
        ):
            return True
        confirmed_selected_blob = json.dumps(
            [
                item
                for item in self.local_evidence_refs
                if item.get("selection_status") == "confirmed"
            ],
            ensure_ascii=False,
        )
        selected_semantics = (
            (("人员", "项目经理", "负责人"), ("person", "人员", "项目经理", "负责人")),
            (("证书", "资格"), ("certificate", "证书", "资格")),
            (("资质",), ("qualification", "资质")),
            (("工作年限", "项目管理年限"), ("work_years", "management_years", "工作年限")),
            (("同类项目", "项目经验"), ("similar_project", "project_experience", "同类项目")),
            (("业绩",), ("performance", "业绩")),
        )
        if confirmed_selected_blob and any(
            any(term in combined for term in field_terms)
            and any(term in confirmed_selected_blob for term in evidence_terms)
            for field_terms, evidence_terms in selected_semantics
        ):
            return True
        category_groups = (
            ("交货", "货到", "交付"),
            ("安装", "工期", "完工"),
            ("保修", "质保"),
            ("服务", "响应", "到场", "维修"),
            ("培训",),
            ("交货地点", "地点"),
            ("随机资料", "随附资料"),
            ("验收",),
            ("履约",),
        )
        matched_groups = [
            group for group in category_groups if any(term in combined for term in group)
        ]
        if not matched_groups:
            return False
        for chapter in self._response.get("chapters") or []:
            for point in chapter.get("response_points") or []:
                text = str(point.get("original_text") or "")
                policy = tender_response_policy(
                    text,
                    source_id=str(point.get("point_id") or ""),
                )
                if policy.get("default_no_deviation") and any(
                    any(term in text for term in group)
                    for group in matched_groups
                ):
                    return True
        return False

    def save_section_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        markdown = str(args.get("markdown") or "")
        sections = list(args.get("sections") or [])
        if not markdown and not sections:
            raise ValueError("save_section_draft requires markdown or sections")
        if markdown:
            self.working_draft_markdown = markdown
        for value in sections:
            section_id = str(value.get("section_id") or "")
            title = str(value.get("title") or "")
            if section_id and not _contains_id(self._target, section_id):
                raise PermissionError("section is outside the runtime target")
            normalized = {
                "section_id": section_id,
                "title": title,
                "content": str(value.get("content") or ""),
            }
            existing = next(
                (
                    index
                    for index, item in enumerate(self.saved_sections)
                    if section_id and item.get("section_id") == section_id
                ),
                None,
            )
            if existing is None:
                self.saved_sections.append(normalized)
            else:
                self.saved_sections[existing] = normalized
        if sections and not markdown:
            lines: list[str] = []
            for item in self.saved_sections:
                lines.extend([f"## {item.get('title')}", "", str(item.get("content") or ""), ""])
            self.working_draft_markdown = "\n".join(lines)
        return {
            "status": "staged",
            "storage": "runtime_memory",
            "formal_draft_unchanged": True,
            "section_count": len(self.saved_sections),
            "character_count": len(self.working_draft_markdown),
        }

    def record_table_fill_data(self, args: dict[str, Any]) -> dict[str, Any]:
        table_id = str(args.get("table_id") or "")
        table = next(
            (
                item
                for item in self.tender_tables
                if str(item.get("table_id") or "") == table_id
                and item.get("layout_complexity") == "complex"
                and item.get("table_type") == "required_response_table"
            ),
            None,
        )
        if table is None:
            raise PermissionError("table_id is not an applicable complex tender table")
        cell_values = dict(args.get("cell_values") or {})
        allowed_fields = set(str(item) for item in table.get("field_order") or [])
        unknown = sorted(set(cell_values) - allowed_fields)
        if unknown:
            raise ValueError(f"unknown tender table fields: {unknown}")
        record = {
            "table_id": table_id,
            "source_file_id": table.get("source_file_id"),
            "source_table_index": table.get("source_table_index"),
            "format_preservation": "exact",
            "cell_values": {
                field: (
                    None
                    if cell_values.get(field) is None
                    else str(cell_values.get(field))
                )
                for field in table.get("field_order") or []
            },
        }
        self.table_fill_data_records = [
            item
            for item in self.table_fill_data_records
            if item.get("table_id") != table_id
        ]
        self.table_fill_data_records.append(record)
        return {
            "status": "recorded",
            "table_id": table_id,
            "format_preservation": "exact",
            "field_count": len(record["cell_values"]),
        }

    def load_existing_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.existing_draft_markdown:
            return {"status": "not_available", "markdown": ""}
        return {
            "status": "success",
            "markdown": self.existing_draft_markdown,
            "sha256": self.existing_draft_sha256,
        }

    def _assert_target(self, args: dict[str, Any]) -> None:
        chapter = args.get("chapter_id")
        section = args.get("section_id")
        if chapter and str(chapter) != str(self._root.get("chapter_id") or ""):
            raise PermissionError("chapter is outside the runtime target")
        if section and str(section) != self._target_id and not _contains_id(self._target, str(section)):
            raise PermissionError("section is outside the runtime target")

    def _ensure_web_allowed(self, args: dict[str, Any]) -> None:
        if self.web_disabled or not self._allowed_web["allowed"]:
            raise PermissionError("web access is not allowed for this target")
        requested = {str(item) for item in args.get("allowed_source_types") or []}
        allowed = self._allowed_web["source_types"]
        if not requested:
            raise PermissionError("web access requires explicit allowed_source_types")
        if allowed and not requested <= allowed:
            raise PermissionError("requested web source type exceeds the target policy")
        purpose = str(args.get("purpose") or "")
        if not _web_purpose_matches(purpose, self._allowed_web["purposes"]):
            raise PermissionError("requested web purpose exceeds the target writing policy")
        if any("manufacturer" in item or "product_official" in item for item in requested):
            confirmed = self.get_confirmed_project_facts({}).get("facts") or {}
            if not any(key in confirmed for key in ("manufacturer", "product_manufacturer", "product_model")):
                raise PermissionError("manufacturer/product official web search requires confirmed project facts")


def build_technical_tool_registry(context: TechnicalWritingToolContext) -> TechnicalToolRegistry:
    registry = TechnicalToolRegistry()
    target_props = {
        "chapter_id": {"type": ["string", "null"]},
        "section_id": {"type": ["string", "null"]},
    }
    registry.register(RegisteredTool("get_writing_scope", "读取当前目标目录、写作要求和标题层级。", object_schema(target_props), context.get_writing_scope))
    registry.register(
        RegisteredTool(
            "get_response_requirements",
            "按当前章节或响应点读取招标原文；大型参数表不会无关加载。",
            object_schema({**target_props, "response_point_ids": {"type": "array", "items": {"type": "string"}}}),
            context.get_response_requirements,
        )
    )
    registry.register(RegisteredTool("get_task_information", "读取当前章节的信息需求提示，不预设查询顺序。", object_schema(target_props), context.get_task_information))
    if context.draft_units_path:
        registry.register(RegisteredTool("get_draft_plan", "读取可选的内部生成分批建议。", object_schema(target_props), context.get_draft_plan))
    registry.register(RegisteredTool("get_confirmed_project_facts", "只读取明确标记 confirmed 的项目事实。", object_schema({}), context.get_confirmed_project_facts))
    registry.register(
        RegisteredTool(
            "query_local_knowledge",
            "用结构化 metadata 与关键词查询企业材料候选；结果永远是 candidate。",
            object_schema(
                {
                    "query": {"type": "string", "minLength": 1},
                    "evidence_types": {"type": "array", "items": {"type": "string"}},
                    "entity_types": {"type": "array", "items": {"type": "string"}},
                    "entity_names": {"type": "array", "items": {"type": "string"}},
                    "department": {"type": ["string", "null"]},
                    "project_type": {"type": ["string", "null"]},
                    "validity_requirement": {"type": ["string", "null"]},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                required=["query", "evidence_types"],
            ),
            context.query_local_knowledge,
        )
    )
    registry.register(
        RegisteredTool(
            "read_local_evidence",
            "按 file_id 读取候选材料必要片段，禁止任意路径与 reference_only。",
            object_schema(
                {
                    "file_id": {"type": "string", "minLength": 1},
                    "source_location": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000},
                },
                required=["file_id"],
            ),
            context.read_local_evidence,
        )
    )
    if not context.web_disabled:
        registry.register(
            RegisteredTool(
                "web_search",
                "按章节联网权限检索政府、标准、行业背景或已确认厂商官方资料。",
                object_schema(
                    {
                        "query": {"type": "string", "minLength": 1},
                        "purpose": {"type": "string", "minLength": 1},
                        "allowed_source_types": {"type": "array", "items": {"type": "string"}},
                        "allowed_domains": {"type": "array", "items": {"type": "string"}},
                        "recency_days": {"type": ["integer", "null"], "minimum": 1},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    required=["query", "purpose", "allowed_source_types"],
                ),
                context.web_search,
            )
        )
        registry.register(
            RegisteredTool(
                "open_web_page",
                "打开模型选择的公开网页，带 SSRF 防护和正文长度限制。",
                object_schema(
                    {
                        "url": {"type": "string", "minLength": 1},
                        "purpose": {"type": "string", "minLength": 1},
                        "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000},
                    },
                    required=["url", "purpose"],
                ),
                context.open_web_page,
            )
        )
    pending_item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "field": {"type": "string", "minLength": 1},
            "question": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1},
            "candidates": {"type": "array", "items": {}},
            "affected_sections": {"type": "array", "items": {"type": "string"}},
            "blocking": {"type": "boolean"},
            "category": {
                "type": "string",
                "enum": [
                    "personnel",
                    "performance",
                    "qualification",
                    "product",
                    "service",
                    "other",
                ],
            },
            "required_confirmation": {"type": "string"},
            "recommended_candidate": {},
            "recommendation_reason": {"type": "string"},
            "source_references": {"type": "array", "items": {}},
            "severity": {
                "type": "string",
                "enum": ["warning", "blocking", "critical"],
            },
            "release_blocking": {"type": "boolean"},
        },
        "required": ["field", "question", "reason"],
    }
    registry.register(
        RegisteredTool(
            "record_pending_confirmation",
            "一次记录一个或多个待确认项；每项只能描述一个field，Runtime返回稳定placeholder_id。",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    **pending_item_schema["properties"],
                    "items": {"type": "array", "items": pending_item_schema},
                },
                "anyOf": [
                    {"required": ["field", "question", "reason"]},
                    {"required": ["items"]},
                ],
            },
            context.record_pending_confirmation,
        )
    )
    registry.register(
        RegisteredTool(
            "save_section_draft",
            "将单个、多个小节或整章工作草稿暂存到本次运行内存；不会覆盖正式 Markdown。",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "markdown": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "section_id": {"type": "string"},
                                "title": {"type": "string", "minLength": 1},
                                "content": {"type": "string"},
                            },
                            "required": ["title", "content"],
                        },
                    },
                },
                "anyOf": [{"required": ["markdown"]}, {"required": ["sections"]}],
            },
            context.save_section_draft,
        )
    )
    if any(
        item.get("layout_complexity") == "complex"
        and item.get("table_type") == "required_response_table"
        for item in context.tender_tables
    ):
        registry.register(
            RegisteredTool(
                "record_table_fill_data",
                "为适用的复杂招标表记录原格式回填数据；不得把复杂原表简化为Markdown。",
                object_schema(
                    {
                        "table_id": {"type": "string", "minLength": 1},
                        "cell_values": {
                            "type": "object",
                            "additionalProperties": {"type": ["string", "null"]},
                        },
                    },
                    required=["table_id", "cell_values"],
                ),
                context.record_table_fill_data,
            )
        )
    return registry


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return value


def _find_node(nodes: list[dict[str, Any]], target_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for root in nodes:
        found = _find_in(root, target_id)
        if found:
            return found, root
    return None, None


def _find_in(node: dict[str, Any], target_id: str) -> dict[str, Any] | None:
    if str(node.get("chapter_id") or "") == target_id:
        return node
    for child in node.get("children") or []:
        found = _find_in(child, target_id)
        if found:
            return found
    return None


def _contains_id(node: dict[str, Any], target_id: str) -> bool:
    return _find_in(node, target_id) is not None


def _collect_values(node: dict[str, Any], field: str) -> list[str]:
    result = [str(item) for item in (node.get("writing_requirements") or {}).get(field) or []]
    for child in node.get("children") or []:
        result.extend(_collect_values(child, field))
    return list(dict.fromkeys(result))


def _collect_web_policy(node: dict[str, Any]) -> dict[str, Any]:
    needs: list[str] = []
    sources: list[str] = []
    purposes: list[str] = []
    for item in [node, *_all_children(node)]:
        web = (item.get("writing_requirements") or {}).get("web") or {}
        needs.append(str(web.get("need") or "forbidden"))
        sources.extend(str(value) for value in web.get("allowed_source_types") or [])
        purposes.extend(str(value) for value in web.get("purposes") or [])
    return {
        "allowed": any(item in {"optional", "required"} for item in needs),
        "source_types": set(sources),
        "purposes": set(purposes),
    }


def _all_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        result.append(child)
        result.extend(_all_children(child))
    return result


def _score_refs_for_point(writing: dict[str, Any], point_id: str) -> list[str]:
    return [
        str(item.get("score_item_id"))
        for item in writing.get("score_items") or []
        if str(item.get("response_point_id") or "") == point_id
    ]


def _forbidden_evidence(name: str, storage_key: str) -> bool:
    value = f"{name}\n{storage_key}".lower()
    return any(term.lower() in value for term in FORBIDDEN_EVIDENCE_NAME_PARTS)


def _entity_name(record: dict[str, Any]) -> str:
    resume = record.get("resume_parse") or {}
    return str(resume.get("person_name") or resume.get("name") or record.get("entity_name") or "")


def _source_location(record: dict[str, Any]) -> str:
    if record.get("sections"):
        return "parsed.sections"
    if record.get("parsed_text"):
        return "parsed_text"
    if record.get("resume_parse"):
        return "resume_parse"
    return "metadata"


def _excerpt(record: dict[str, Any], limit: int) -> str:
    resume = record.get("resume_parse") or {}
    values = [
        str(record.get("parsed_text") or ""),
        json.dumps(resume, ensure_ascii=False) if resume else "",
        json.dumps(record.get("company_qualification") or {}, ensure_ascii=False),
    ]
    text = "\n".join(value for value in values if value)
    return text[:limit]


def _best_evidence_type(requested: list[str], candidate: dict[str, Any]) -> str:
    group = str(candidate.get("material_group") or "")
    preferred = {
        "personnel": ("person_resume", "person_certificate", "implementation_experience"),
        "company_qualification": ("quality_management_material", "company_qualification"),
        "company_performance": ("company_performance", "implementation_experience"),
    }.get(group, ())
    for value in preferred:
        if value in requested:
            return value
    return requested[0] if requested else (group or "other_enterprise_evidence")


def _evidence_status(record: dict[str, Any]) -> str:
    allowed = {
        "confirmed",
        "candidate",
        "needs_review",
        "rejected",
        "unusable",
        "unknown",
        "missing",
    }
    containers = [
        record,
        record.get("evidence") if isinstance(record.get("evidence"), dict) else {},
        record.get("material_profile")
        if isinstance(record.get("material_profile"), dict)
        else {},
    ]
    for container in containers:
        for key in (
            "evidence_status",
            "confirmation_status",
            "selection_status",
            "review_status",
            "status",
        ):
            value = str(container.get(key) or "").strip().lower()
            if value in allowed:
                return value
    return "unknown"


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    containers = [
        record,
        record.get("resume_parse")
        if isinstance(record.get("resume_parse"), dict)
        else {},
        record.get("company_performance")
        if isinstance(record.get("company_performance"), dict)
        else {},
        record.get("material_profile")
        if isinstance(record.get("material_profile"), dict)
        else {},
    ]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _requirement_source_location(source_refs: list[dict[str, Any]]) -> str:
    if not source_refs:
        return ""
    source = source_refs[0]
    parts = [
        str(source.get("file_name") or source.get("file_id") or ""),
        "/".join(str(item) for item in source.get("clause_path") or []),
        str(source.get("row_or_cell") or ""),
    ]
    page_start = source.get("page_start")
    page_end = source.get("page_end")
    if page_start is not None:
        parts.append(
            f"pages:{page_start}-{page_end if page_end is not None else page_start}"
        )
    return " | ".join(part for part in parts if part)


def _point_text(point: dict[str, Any]) -> str:
    return " ".join(
        str(point.get(key) or "")
        for key in (
            "original_text",
            "original_heading",
            "mandatory_marker",
            "content_type",
            "criteria_summary",
            "full_score_condition",
        )
    )


def _is_substantive_requirement(point: dict[str, Any]) -> bool:
    marker = str(point.get("mandatory_marker") or "").strip().lower()
    text = _point_text(point).lower()
    return bool(
        marker
        or any(
            term in text
            for term in (
                "must",
                "shall",
                "mandatory",
                "substantive",
                "必须",
                "应当",
                "实质性",
                "否决",
            )
        )
    )


def _requires_specific_value(point: dict[str, Any]) -> bool:
    text = _point_text(point)
    return bool(
        re.search(
            r"\d+(?:\.\d+)?\s*(?:%|天|日|小时|分钟|年|月|人|台|套|项)",
            text,
        )
        or any(
            term in text.lower()
            for term in ("specific value", "参数值", "数值")
        )
    )


def _requires_proof(point: dict[str, Any]) -> bool:
    text = _point_text(point).lower()
    return any(
        term in text
        for term in (
            "proof",
            "certificate",
            "report",
            "证明",
            "证书",
            "检测报告",
            "佐证",
        )
    )


def _requirement_response_form(point: dict[str, Any]) -> str:
    if isinstance(point.get("table"), dict):
        return "tender_table"
    content_type = str(point.get("content_type") or "").lower()
    if "attachment" in content_type or _requires_proof(point):
        return "narrative_with_evidence"
    return "narrative"


def _score_items_for_point(
    writing: dict[str, Any],
    point_id: str,
) -> list[dict[str, Any]]:
    result = []
    for item in writing.get("score_items") or []:
        refs = {
            str(item.get("response_point_id") or ""),
            *(str(value) for value in item.get("response_point_ids") or []),
        }
        if point_id not in refs:
            continue
        result.append(
            {
                "score_item_id": item.get("score_item_id"),
                "title": item.get("title"),
                "criteria_summary": item.get("criteria_summary"),
                "full_score_condition": (
                    item.get("full_score_condition")
                    or item.get("maximum_score_condition")
                    or item.get("scoring_criteria")
                    or item.get("criteria_summary")
                ),
                "score": item.get("score"),
                "requires_proof": bool(
                    item.get("requires_proof")
                    or _requires_proof(item)
                ),
            }
        )
    return result


def _unique_refs(items: list[dict[str, Any]], existing: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen = {str(item.get(key) or "") for item in existing}
    return [copy.deepcopy(item) for item in items if str(item.get(key) or "") and str(item.get(key)) not in seen]


def _web_purpose_matches(purpose: str, allowed_purposes: set[str]) -> bool:
    if not allowed_purposes:
        return False
    categories = {
        "policy": ("政策", "主管部门", "政府"),
        "standard": ("标准", "规范"),
        "industry_background": ("行业背景", "公开背景", "应用背景"),
        "technical_principle": ("技术原理", "通用原理"),
        "manufacturer": ("制造商", "厂商官网", "产品官方"),
    }
    requested_categories = {
        name for name, terms in categories.items() if any(term in purpose for term in terms)
    }
    allowed_text = "\n".join(allowed_purposes)
    allowed_categories = {
        name for name, terms in categories.items() if any(term in allowed_text for term in terms)
    }
    return bool(requested_categories & allowed_categories)


def _is_valid_existing_markdown(value: str) -> bool:
    text = str(value or "").strip()
    if not text or "【生成失败：" in text:
        return False
    body_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "<!--"))
    ]
    return len("\n".join(body_lines)) >= 10


def _table_writing_specification(table: dict[str, Any]) -> dict[str, Any]:
    title = str(
        table.get("title")
        or table.get("table_name")
        or table.get("name")
        or table.get("caption")
        or ""
    )
    headers = table.get("headers") or table.get("columns") or []
    if headers and isinstance(headers[0], dict):
        headers = [
            str(item.get("title") or item.get("name") or item.get("field") or "")
            for item in headers
        ]
    elif not headers:
        rows = list(table.get("rows") or [])
        first = rows[0] if rows else None
        if isinstance(first, dict):
            headers = list(first)
        elif isinstance(first, list):
            headers = [str(item) for item in first]
    return {
        "reuse_required": True,
        "table_name": title,
        "headers": [str(item) for item in headers],
        "field_order": [str(item) for item in headers],
        "filling_requirements": copy.deepcopy(
            table.get("filling_requirements")
            or table.get("instructions")
            or table.get("notes")
            or []
        ),
        "policy": "招标文件存在对应表格时，优先复用原表名、表头、字段顺序和填写要求。",
    }

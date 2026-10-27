from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.human_confirmations import (
    HumanConfirmationApplicationService,
    HumanConfirmationSource,
)
from app.project_facts import ProjectFactApplicationService, ProjectFactSource
from app.technical_fulltext_composer import stable_json_sha256
from app.web.analysis_query_service import TenderAnalysisReadService
from app.web.dtos import (
    FactMaterialHeadlineDTO,
    FactMaterialItemDTO,
    FactMaterialSummaryDTO,
    HumanConfirmationListDTO,
    HumanConfirmationRequestDTO,
    PreparationDependencyDTO,
    SourceReferenceDTO,
    WritingContextItemDTO,
    WritingPreparationDTO,
    WritingPreparationItemDTO,
    WritingPreparationSummaryDTO,
    VersionRefDTO,
)
from app.web.project_lifecycle_service import LifecycleProjectNotFoundError, ProjectLifecycleService
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import WebProjectionError


STATUS_LABELS = {
    "confirmed": "已确认",
    "missing": "待确认",
    "confirmation_required": "待人工确认",
    "query_not_executed": "尚未查询",
    "candidate": "已发现，待核验",
    "verified": "已准备",
    "invalid": "无效",
    "expired": "已过期",
    "not_provided": "未提供",
    "not_started": "待准备",
}


class PreparationItemNotFoundError(LookupError):
    pass


class WritingPreparationReadService:
    """Read-only Stage4/writing-input projection.

    The source artifacts describe needs and query plans.  They are never
    treated as a writable fact store, and Stage12 confirmations are not read.
    """

    def __init__(
        self,
        resolver: ReadOnlyProjectResolver,
        lifecycle: ProjectLifecycleService,
        analysis: TenderAnalysisReadService,
    ) -> None:
        self.resolver = resolver
        self.lifecycle = lifecycle
        self.analysis = analysis
        self.outputs_root = resolver.outputs_root.resolve()
        self.fact_service = ProjectFactApplicationService(self.outputs_root, self.fact_source)
        self.confirmation_service = HumanConfirmationApplicationService(
            self.outputs_root, self.confirmation_source
        )
        self.mutation_lock = threading.RLock()

    def get(self, project_id: str) -> WritingPreparationDTO:
        bundle = self.analysis._bundle(project_id)
        fact_request, query_plan, tasks, confirmed_facts = self._writing_inputs(project_id)
        task_index = {
            str(item.get("task_id") or ""): item
            for item in tasks.get("tasks") or []
            if isinstance(item, dict) and item.get("task_id")
        }
        requirement_titles = self._requirement_titles(bundle)
        score_titles = {item.score_id: item.title for item in self.analysis.scoring(project_id).rules}

        fact_queries: dict[str, list[dict[str, Any]]] = {}
        evidence_queries: list[dict[str, Any]] = []
        for query in query_plan.get("queries") or []:
            if not isinstance(query, dict):
                continue
            fact_slot_id = str(query.get("fact_slot_id") or "")
            if fact_slot_id:
                fact_queries.setdefault(fact_slot_id, []).append(query)
            else:
                evidence_queries.append(query)

        items: list[WritingPreparationItemDTO] = []
        contexts: list[WritingContextItemDTO] = []
        explicit_facts = dict(confirmed_facts.get("facts") or {})
        for raw in fact_request.get("slots") or []:
            if not isinstance(raw, dict):
                continue
            fact_id = str(raw.get("fact_slot_id") or raw.get("fact_id") or "")
            if not fact_id:
                continue
            value = raw.get("value")
            status = str(raw.get("status") or "missing")
            explicit = explicit_facts.get(str(raw.get("fact_key") or ""))
            if isinstance(explicit, dict) and str(explicit.get("status") or explicit.get("confirmation_status") or "") == "confirmed":
                value, status = explicit.get("value"), "confirmed"
            confirmed = status == "confirmed" and value not in (None, "", [], {})
            status = "confirmed" if confirmed else "missing"
            context = WritingContextItemDTO(
                context_id=fact_id,
                title=str(raw.get("name") or raw.get("label") or raw.get("fact_key") or "项目事实"),
                value=_display_value(value) if confirmed else None,
                availability="available" if confirmed else "placeholder_required",
                availability_label="可用于写作" if confirmed else "待确认；写作时只能使用待确认字段",
                source_policy=str(raw.get("source_policy") or "confirmed_project_fact"),
                source_description="已确认项目事实" if confirmed else "不得从招标最低要求反向推断投标事实",
                technical_ref=fact_id,
            )
            contexts.append(context)
            target_ids = [str(value) for value in raw.get("target_task_ids") or []]
            query_ids = [str(value.get("query_id") or "") for value in fact_queries.get(fact_id, [])]
            items.append(
                WritingPreparationItemDTO(
                    item_id=fact_id,
                    kind="project_fact",
                    fact_key=str(raw.get("fact_key") or ""),
                    title=context.title,
                    description=str(raw.get("description") or raw.get("prohibited_inference") or "仅能使用经确认的项目事实。"),
                    status=status,
                    status_label=STATUS_LABELS[status],
                    required=bool(raw.get("required", True)),
                    current_value=context.value,
                    current_raw_value=value if confirmed else None,
                    value_type="structured" if str(raw.get("fact_key") or "") == "manufacturer_service_network" else "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "text",
                    value_fields=_value_fields(str(raw.get("fact_key") or "")),
                    dependencies=self._dependencies(project_id, target_ids, task_index, requirement_titles, score_titles),
                    source_references=self._task_sources(target_ids, task_index),
                    writing_context=context,
                    technical_refs=[value for value in [fact_id, *query_ids] if value],
                    can_confirm=True,
                )
            )

        confirmation_values = self.confirmations(project_id).confirmations
        for confirmation in confirmation_values:
            if confirmation.request_kind != "business_choice":
                continue
            item_id = confirmation.confirmation_id
            confirmed = confirmation.status == "confirmed"
            context = WritingContextItemDTO(
                context_id=item_id,
                title=confirmation.question,
                value=_display_value(confirmation.answer) if confirmed else None,
                availability="available" if confirmed else "pending",
                availability_label=(
                    "已确认业务约束；Stage9 将按当前选择写作"
                    if confirmed
                    else "待人工确认；Stage9 将使用稳定占位符"
                ),
                source_policy="human_confirmation_required",
                source_description=confirmation.reason,
                technical_ref=item_id,
                context_kind="human_confirmation",
                stage9_consumable=True,
                stage9_policy="apply_constraint" if confirmed else "placeholder_required",
            )
            contexts.append(context)
            dependencies = self._confirmation_dependencies(
                project_id,
                confirmation,
                task_index,
                requirement_titles,
                score_titles,
            )
            items.append(
                WritingPreparationItemDTO(
                    item_id=item_id,
                    kind="human_confirmation",
                    title=context.title,
                    description=confirmation.reason,
                    status="confirmed" if confirmed else "confirmation_required",
                    status_label="已确认业务选择" if confirmed else STATUS_LABELS["confirmation_required"],
                    required=True,
                    priority=confirmation.priority,
                    current_value=_display_value(confirmation.answer) if confirmed else _display_value(confirmation.current_value),
                    current_raw_value=confirmation.answer if confirmed else None,
                    value_type="text",
                    candidate_values=confirmation.candidate_values,
                    dependencies=dependencies,
                    source_references=confirmation.source_references,
                    writing_context=context,
                    technical_refs=[item_id],
                    can_confirm=confirmation.can_decide,
                    confirmation_response_kind=confirmation.response_kind,
                    stage9_consumable=confirmation.stage9_consumable,
                )
            )

        for raw in evidence_queries:
            item = self._evidence_query_item(project_id, raw, task_index, requirement_titles, score_titles)
            items.append(item)

        existing_material_ids = {item.item_id for item in items}
        for raw in bundle.context.get("global_missing_materials") or []:
            item_id = str(raw.get("missing_id") or "")
            if not item_id or item_id in existing_material_ids:
                continue
            status = _material_status(raw.get("status") or "not_started")
            requirements = [str(value) for value in raw.get("related_requirement_ref_ids") or []]
            scores = [str(value) for value in raw.get("related_score_item_ids") or []]
            dependencies = [self._dependency(project_id, "requirement", value, requirement_titles.get(value, value)) for value in requirements]
            dependencies.extend(self._dependency(project_id, "score", value, score_titles.get(value, value)) for value in scores)
            items.append(
                WritingPreparationItemDTO(
                    item_id=item_id,
                    kind="material",
                    title=str(raw.get("title") or raw.get("requested_material_type") or "证明材料"),
                    description=str(raw.get("reason") or raw.get("description") or ""),
                    status=status,
                    status_label=STATUS_LABELS.get(status, status),
                    required=True,
                    priority=_optional_text(raw.get("priority")),
                    dependencies=_dedupe_dependencies(dependencies),
                    technical_refs=[item_id],
                )
            )

        items = _dedupe_items(items)
        contexts = _dedupe_contexts(contexts)
        confirmed_count = sum(item.kind == "project_fact" and item.status == "confirmed" for item in items)
        pending_count = sum(item.kind == "project_fact" and item.status != "confirmed" for item in items)
        query_count = sum(item.status == "query_not_executed" for item in items)
        candidate_count = sum(item.status == "candidate" for item in items)
        prepared_count = sum(item.status == "verified" for item in items)
        placeholder_count = sum(item.availability == "placeholder_required" for item in contexts)
        return WritingPreparationDTO(
            project_id=project_id,
            project_name=bundle.project_name,
            version_token=self.current_token(project_id),
            lineage=bundle.lineage,
            summary=WritingPreparationSummaryDTO(
                confirmed_fact_count=confirmed_count,
                pending_fact_count=pending_count,
                confirmation_count=sum(
                    item.kind == "human_confirmation" and item.status != "confirmed"
                    for item in items
                ),
                query_not_executed_count=query_count,
                candidate_material_count=candidate_count,
                prepared_material_count=prepared_count,
                placeholder_count=placeholder_count,
                analysis_ready=bundle.lineage.analysis_ready,
                planning_accessible=bundle.lineage.analysis_ready,
                gate_source="analysis_ready_without_canonical_preparation_blocker",
                guidance="招标分析已完成。可以继续补充准备事项，也可以先进入写作规划；待确认内容不会被当作确定事实使用。",
            ),
            items=items,
            writing_context=contexts,
            read_only=False,
            mutation_available=True,
            mutation_unavailable_reason=None,
        )

    def item(self, project_id: str, item_id: str) -> WritingPreparationItemDTO:
        match = next((item for item in self.get(project_id).items if item.item_id == item_id), None)
        if match is None:
            raise PreparationItemNotFoundError(f"writing preparation item is not available: {item_id}")
        return match

    def context(self, project_id: str) -> list[WritingContextItemDTO]:
        return self.get(project_id).writing_context

    def confirmations(self, project_id: str) -> HumanConfirmationListDTO:
        bundle = self.analysis._bundle(project_id)
        snapshot = self.confirmation_service.snapshot(project_id)
        values = [self._confirmation_dto(item, snapshot.decisions) for item in snapshot.requests]
        return HumanConfirmationListDTO(
            project_id=project_id,
            project_name=bundle.project_name,
            version_token=self.current_token(project_id),
            lineage=bundle.lineage,
            pending_count=sum(
                item.request_kind == "business_choice" and item.status == "pending"
                for item in values
            ),
            confirmed_count=sum(
                item.request_kind == "business_choice" and item.status == "confirmed"
                for item in values
            ),
            project_fact_routed_count=sum(item.request_kind == "project_fact" for item in values),
            material_routed_count=sum(
                item.request_kind == "material_availability" for item in values
            ),
            confirmations=values,
        )

    def confirmation(self, project_id: str, confirmation_id: str) -> HumanConfirmationRequestDTO:
        match = next(
            (
                item
                for item in self.confirmations(project_id).confirmations
                if item.confirmation_id == confirmation_id
            ),
            None,
        )
        if match is None:
            raise PreparationItemNotFoundError(
                f"human confirmation is not available: {confirmation_id}"
            )
        return match

    def facts_materials(self, project_id: str) -> FactMaterialSummaryDTO:
        preparation = self.get(project_id)
        visible = [item for item in preparation.items if item.kind in {"project_fact", "human_confirmation", "material", "evidence_query"}]
        base = f"/projects/{quote(project_id, safe='')}"
        items = [
            FactMaterialItemDTO(
                category="missing_project_fact" if item.kind == "project_fact" else "confirmation_required" if item.kind == "human_confirmation" else "material_preparation",
                title=item.title,
                status=item.status,
                status_label=item.status_label,
                description=item.description,
                required=item.required,
                related_task_count=len(item.dependencies),
                trace_id=item.item_id,
                current_value=item.current_value,
                related_locations=_business_dependency_titles(item.dependencies, "writing"),
                requirement_context=_business_dependency_titles(item.dependencies, "requirement"),
                action_label=(
                    "填写信息"
                    if item.kind == "project_fact"
                    else "确认"
                    if item.kind == "human_confirmation"
                    else "查看 / 补充材料"
                ),
                action_url=f"{base}/writing-preparation?item={quote(item.item_id, safe='')}",
            )
            for item in visible
        ]
        counts: dict[str, int] = {}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return FactMaterialSummaryDTO(
            project_id=project_id,
            reviewed_version=VersionRefDTO(
                version_id=preparation.lineage.run_id or "legacy-analysis",
                label="写作前项目事实与材料准备",
                content_sha256=None,
                artifact_sha256=None,
                status="analysis_ready",
            ),
            headline=FactMaterialHeadlineDTO(
                missing_project_facts=preparation.summary.pending_fact_count,
                confirmations_required=preparation.summary.confirmation_count,
                queries_not_executed=preparation.summary.query_not_executed_count,
                candidate_materials=preparation.summary.candidate_material_count,
                verified_materials=preparation.summary.prepared_material_count,
            ),
            status_legend={
                "missing": "待确认",
                "confirmed": "已确认",
                "confirmation_required": "待人工确认",
                "query_not_executed": "尚未查询",
                "candidate": "已发现，待核验",
                "verified": "已准备",
            },
            status_counts=counts,
            items=items,
        )

    def _writing_inputs(self, project_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        source = self.fact_source(project_id)
        snapshot = self.fact_service.snapshot(project_id)
        query_path = source.fact_request_path.parent / "local_evidence_query_plan.json"
        return snapshot.fact_request, _load_optional(query_path), snapshot.writing_tasks, {"facts": snapshot.facts}

    def fact_source(self, project_id: str) -> ProjectFactSource:
        try:
            manifest = self.lifecycle._load_manifest(project_id)
            state = self.lifecycle._latest_analysis_state(manifest)
            fact = self._stage_artifact_path(state, "build_writing_tasks", "project_fact_request")
            tasks = self._stage_artifact_path(state, "build_writing_tasks", "section_writing_tasks_json")
            return ProjectFactSource(
                project_id=project_id,
                source_mode="pipeline_analysis",
                analysis_run_id=str(state.get("run_id") or "") or None,
                fact_request_path=fact,
                writing_tasks_path=tasks,
            )
        except LifecycleProjectNotFoundError:
            artifacts = self.resolver.resolve(project_id)
            run = _load_json(artifacts.root / "fulltext" / "technical_fulltext.run.json")
            inputs = run.get("input_paths") if isinstance(run.get("input_paths"), dict) else {}
            task_reference = inputs.get("task_information_path")
            task_path = self._safe_reference(task_reference) if task_reference else None
            task_dir = task_path.parent if task_path else None
            if task_dir is None:
                raise WebProjectionError("writing preparation task artifacts are unavailable")
            confirmed_reference = inputs.get("confirmed_project_facts_path")
            return ProjectFactSource(
                project_id=project_id,
                source_mode="legacy_response_outline",
                analysis_run_id=None,
                fact_request_path=task_dir / "project_fact_request.json",
                writing_tasks_path=task_dir / "section_writing_tasks.json",
                existing_confirmed_facts_path=self._safe_reference(confirmed_reference) if confirmed_reference else None,
            )

    def current_token(self, project_id: str) -> str:
        return stable_json_sha256(
            {
                "project_fact_token": self.fact_service.snapshot(project_id).token,
                "human_confirmation_token": self.confirmation_service.snapshot(project_id).token,
            }
        )

    def assert_current_token(self, project_id: str, token: str) -> None:
        normalized = str(token or "").strip()
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
            normalized = normalized[1:-1]
        if normalized != self.current_token(project_id):
            from app.project_facts import ProjectFactLineageConflict

            raise ProjectFactLineageConflict(
                "写作准备内容已发生变化或所属分析 Run 已更新，请刷新后重试"
            )

    def confirmation_source(self, project_id: str) -> HumanConfirmationSource:
        fact_source = self.fact_source(project_id)
        context_path: Path | None = None
        if fact_source.source_mode == "pipeline_analysis":
            manifest = self.lifecycle._load_manifest(project_id)
            state = self.lifecycle._latest_analysis_state(manifest)
            context_path = self._stage_artifact_path(
                state, "build_project_response_context", "technical_response_context"
            )
        return HumanConfirmationSource(
            project_id=project_id,
            source_mode=fact_source.source_mode,
            analysis_run_id=fact_source.analysis_run_id,
            response_context_path=context_path,
            fact_request_path=fact_source.fact_request_path,
            writing_tasks_path=fact_source.writing_tasks_path,
        )

    def _confirmation_dto(
        self, raw: dict[str, Any], decisions: dict[str, dict[str, Any]]
    ) -> HumanConfirmationRequestDTO:
        confirmation_id = str(raw.get("confirmation_id") or "")
        decision = decisions.get(confirmation_id) or {}
        confirmed = decision.get("status") == "confirmed"
        return HumanConfirmationRequestDTO(
            confirmation_id=confirmation_id,
            request_kind=str(raw.get("request_kind") or "business_choice"),  # type: ignore[arg-type]
            managed_by=str(raw.get("managed_by") or "human_confirmation_service"),  # type: ignore[arg-type]
            question=str(raw.get("question") or "需要人工确认"),
            reason=str(raw.get("reason") or ""),
            category=str(raw.get("category") or "business_choice"),
            priority=str(raw.get("priority") or "medium"),
            response_kind=str(raw.get("response_kind") or "text"),  # type: ignore[arg-type]
            candidate_values=[str(value) for value in raw.get("candidate_values") or []],
            current_value=raw.get("current_value"),
            status="confirmed" if confirmed else "pending",
            status_label="已确认业务选择" if confirmed else "待人工确认",
            answer=decision.get("answer") if confirmed else None,
            source_description=_optional_text(decision.get("source_description")),
            confirmation_basis=_optional_text(decision.get("confirmation_basis")),
            note=_optional_text(decision.get("note")),
            writing_effect=str(raw.get("writing_effect") or "none"),
            linked_fact_id=_optional_text(raw.get("linked_fact_id")),
            requirement_refs=[str(value) for value in raw.get("requirement_refs") or []],
            score_refs=[str(value) for value in raw.get("score_refs") or []],
            writing_task_refs=[str(value) for value in raw.get("writing_task_refs") or []],
            writing_section_refs=[str(value) for value in raw.get("writing_section_refs") or []],
            source_references=[
                _source_ref(value)
                for value in raw.get("source_refs") or []
                if isinstance(value, dict)
            ],
            can_decide=raw.get("request_kind") == "business_choice",
            stage9_consumable=True,
        )

    def _confirmation_dependencies(
        self,
        project_id: str,
        confirmation: HumanConfirmationRequestDTO,
        task_index: dict[str, dict[str, Any]],
        requirement_titles: dict[str, str],
        score_titles: dict[str, str],
    ) -> list[PreparationDependencyDTO]:
        values = [
            self._dependency(
                project_id, "requirement", trace_id, requirement_titles.get(trace_id, trace_id)
            )
            for trace_id in confirmation.requirement_refs
        ]
        values.extend(
            self._dependency(project_id, "score", trace_id, score_titles.get(trace_id, trace_id))
            for trace_id in confirmation.score_refs
        )
        for task_id in confirmation.writing_task_refs:
            task = task_index.get(task_id, {})
            section_id = str(task.get("section_id") or task.get("chapter_id") or task_id)
            title = " / ".join(str(value) for value in task.get("target_path") or []) or section_id
            values.append(
                PreparationDependencyDTO(
                    kind="writing",
                    trace_id=section_id,
                    title=title,
                    description=f"Writing Task {task_id}",
                    target_url=f"/projects/{quote(project_id, safe='')}/writing-plan?task={quote(task_id, safe='')}",
                )
            )
        return _dedupe_dependencies(values)

    def _stage_artifact(self, state: dict[str, Any], stage_id: str, key: str) -> dict[str, Any]:
        stage = next((value for value in state.get("stages") or [] if value.get("stage_id") == stage_id), {})
        reference = (stage.get("artifacts") or {}).get(key)
        return _load_json(self._safe_reference(reference)) if reference else {}

    def _stage_artifact_path(self, state: dict[str, Any], stage_id: str, key: str) -> Path:
        stage = next((value for value in state.get("stages") or [] if value.get("stage_id") == stage_id), {})
        reference = (stage.get("artifacts") or {}).get(key)
        if not reference:
            raise WebProjectionError(f"writing preparation artifact is unavailable: {key}")
        return self._safe_reference(reference)

    def _safe_reference(self, reference: Any) -> Path:
        try:
            path = Path(str(reference or "")).expanduser().resolve()
            path.relative_to(self.outputs_root)
        except (OSError, ValueError) as exc:
            raise WebProjectionError("writing preparation artifact is outside the configured output root") from exc
        if not path.is_file():
            raise WebProjectionError("writing preparation artifact is unavailable")
        return path

    def _requirement_titles(self, bundle: Any) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw in bundle.requirement_matrix.get("requirements") or []:
            item_id = str(raw.get("requirement_id") or "")
            if item_id:
                values[item_id] = str(raw.get("title") or raw.get("raw_text") or item_id)
        for chapter in bundle.response_outline.get("chapters") or []:
            for raw in chapter.get("response_points") or []:
                item_id = str(raw.get("point_id") or "")
                if item_id:
                    values[item_id] = str(raw.get("original_text") or raw.get("original_heading") or item_id)
        return values

    def _dependencies(
        self,
        project_id: str,
        task_ids: list[str],
        task_index: dict[str, dict[str, Any]],
        requirement_titles: dict[str, str],
        score_titles: dict[str, str],
    ) -> list[PreparationDependencyDTO]:
        values: list[PreparationDependencyDTO] = []
        for task_id in task_ids:
            task = task_index.get(task_id, {})
            for mapping in task.get("response_point_mappings") or []:
                requirement_id = str(mapping.get("response_point_id") or "")
                if requirement_id:
                    values.append(self._dependency(project_id, "requirement", requirement_id, requirement_titles.get(requirement_id, requirement_id)))
            for score_id in task.get("score_item_refs") or []:
                score_id = str(score_id)
                values.append(self._dependency(project_id, "score", score_id, score_titles.get(score_id, score_id)))
            section_id = str(task.get("section_id") or task.get("chapter_id") or "")
            if section_id:
                title = " / ".join(str(value) for value in task.get("target_path") or []) or section_id
                values.append(self._dependency(project_id, "writing", section_id, title))
        return _dedupe_dependencies(values)

    def _dependency(self, project_id: str, kind: str, trace_id: str, title: str) -> PreparationDependencyDTO:
        base = f"/projects/{quote(project_id, safe='')}"
        target = f"{base}/analysis/requirements/{quote(trace_id, safe='')}" if kind == "requirement" else f"{base}/analysis/scoring?scoreItem={quote(trace_id, safe='')}" if kind == "score" else f"{base}/writing-plan?section={quote(trace_id, safe='')}"
        return PreparationDependencyDTO(kind=kind, trace_id=trace_id, title=title, target_url=target)

    def _task_sources(self, task_ids: list[str], task_index: dict[str, dict[str, Any]]) -> list[SourceReferenceDTO]:
        values: list[SourceReferenceDTO] = []
        for task_id in task_ids:
            for raw in task_index.get(task_id, {}).get("tender_source_refs") or []:
                if isinstance(raw, dict):
                    values.append(_source_ref(raw))
        return values[:10]

    def _evidence_query_item(
        self,
        project_id: str,
        raw: dict[str, Any],
        task_index: dict[str, dict[str, Any]],
        requirement_titles: dict[str, str],
        score_titles: dict[str, str],
    ) -> WritingPreparationItemDTO:
        item_id = str(raw.get("query_id") or "")
        status = _material_status(raw.get("execution_status"))
        task_ids = [str(value) for value in raw.get("target_task_ids") or []]
        # The query is the canonical human-readable search target.  evidence_type
        # is an internal classification code and is only a fallback for older data.
        title = str(raw.get("query") or raw.get("evidence_type") or "证明材料")
        return WritingPreparationItemDTO(
            item_id=item_id,
            kind="evidence_query",
            title=title,
            description=str(raw.get("purpose") or raw.get("query") or ""),
            status=status,
            status_label=STATUS_LABELS.get(status, status),
            required=bool(raw.get("required", True)),
            dependencies=self._dependencies(project_id, task_ids, task_index, requirement_titles, score_titles),
            source_references=self._task_sources(task_ids, task_index),
            technical_refs=[item_id],
        )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebProjectionError(f"writing preparation artifact is unavailable or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise WebProjectionError(f"writing preparation artifact must contain an object: {path.name}")
    return value


def _load_optional(path: Path | None) -> dict[str, Any]:
    return _load_json(path) if path and path.is_file() else {}


def _source_ref(raw: dict[str, Any]) -> SourceReferenceDTO:
    clause_value = raw.get("clause_path") or []
    clause_path = (
        [str(value) for value in clause_value]
        if isinstance(clause_value, list)
        else [str(clause_value)]
        if str(clause_value).strip()
        else []
    )
    return SourceReferenceDTO(
        file_name=_optional_text(raw.get("source_file_name") or raw.get("file_name")),
        clause_path=clause_path,
        page_start=_optional_int(raw.get("page_start")),
        page_end=_optional_int(raw.get("page_end")),
        table_name=_optional_text(raw.get("table_name")),
        table_range=_optional_text(raw.get("table_range")),
        row_or_cell=_optional_text(raw.get("row_or_cell")),
        source_kind="table" if raw.get("table_name") or raw.get("table_range") else "paragraph",
    )


def _material_status(value: Any) -> str:
    status = str(value or "").strip()
    if status in {"planned_not_executed", "query_not_executed", "not_executed", ""}:
        return "query_not_executed"
    if status in {"found", "candidate", "completed_with_candidates"}:
        return "candidate"
    if status in STATUS_LABELS:
        return status
    return "not_started"


def _display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _value_fields(fact_key: str) -> list[dict[str, Any]]:
    if fact_key == "manufacturer_service_network":
        return [
            {"key": "address", "label": "地址", "field_type": "text", "required": True},
            {"key": "contact", "label": "联系方式", "field_type": "text", "required": True},
            {"key": "technician_count", "label": "技术人员数量", "field_type": "number", "required": True, "minimum": 0},
        ]
    return [{"key": "value", "label": "确认值", "field_type": "text", "required": True}]


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _business_dependency_titles(values: list[PreparationDependencyDTO], kind: str) -> list[str]:
    titles: list[str] = []
    for value in values:
        title = value.title.strip()
        if value.kind != kind or not title or title == value.trace_id:
            continue
        if re.fullmatch(r"(?:TR|WC|TASK|FACT|FAI|FCA|FSF|PH)-[A-Z0-9-]+", title, flags=re.IGNORECASE):
            continue
        titles.append(title)
    return list(dict.fromkeys(titles))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dedupe_dependencies(values: list[PreparationDependencyDTO]) -> list[PreparationDependencyDTO]:
    result: list[PreparationDependencyDTO] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (item.kind, item.trace_id)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dedupe_items(values: list[WritingPreparationItemDTO]) -> list[WritingPreparationItemDTO]:
    result: list[WritingPreparationItemDTO] = []
    seen: set[str] = set()
    for item in values:
        if item.item_id and item.item_id not in seen:
            seen.add(item.item_id)
            result.append(item)
    return result


def _dedupe_contexts(values: list[WritingContextItemDTO]) -> list[WritingContextItemDTO]:
    result: list[WritingContextItemDTO] = []
    seen: set[str] = set()
    for item in values:
        if item.context_id not in seen:
            seen.add(item.context_id)
            result.append(item)
    return result

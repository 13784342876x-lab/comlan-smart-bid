from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.bid_pipeline_runner import ANALYSIS_READY_STATUS
from app.stage9_input_handoff import (
    Stage9InputResolutionError,
    Stage9InputResolver,
    Stage9InputSource,
)
from app.technical_review_projection import is_technical_writing_requirement
from app.web.analysis_query_service import TenderAnalysisReadService
from app.web.dtos import (
    AIWritingReadinessDTO,
    WritingChapterDTO,
    WritingContextItemDTO,
    WritingPlanCoverageDTO,
    WritingPlanDTO,
    WritingPlanFactDependencyDTO,
    WritingPlanLineageDTO,
    WritingPlanMaterialDependencyDTO,
    WritingPlanReferenceDTO,
    WritingPlanSummaryDTO,
    WritingSectionDTO,
    WritingTaskDTO,
)
from app.web.project_lifecycle_service import LifecycleProjectNotFoundError, ProjectLifecycleService
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import WebProjectionError
from app.web.writing_preparation_query_service import WritingPreparationReadService

# Backward-compatible module alias for callers that already imported the
# established scope predicate from this projection module.
_is_technical_writing_requirement = is_technical_writing_requirement


@dataclass(frozen=True)
class _PlanningSource:
    source_mode: str
    analysis_run_id: str | None
    current_analysis_run_id: str | None
    planning_key: str | None
    response_path: Path
    writing_path: Path
    tasks_path: Path
    information_path: Path | None
    draft_units_path: Path | None


class WritingPlanReadService:
    """Project Stage5-8 projection over immutable planning artifacts.

    Planning structure is read from the response/writing/task artifacts.  The
    current Project Fact state is only overlaid at query time; no planning
    artifact is rewritten when a fact changes.
    """

    def __init__(
        self,
        resolver: ReadOnlyProjectResolver,
        lifecycle: ProjectLifecycleService,
        analysis: TenderAnalysisReadService,
        preparation: WritingPreparationReadService,
    ) -> None:
        self.resolver = resolver
        self.lifecycle = lifecycle
        self.analysis = analysis
        self.preparation = preparation
        self.outputs_root = resolver.outputs_root.resolve()
        self.stage9_resolver = Stage9InputResolver()

    def get(self, project_id: str) -> WritingPlanDTO:
        bundle = self.analysis._bundle(project_id)
        source = self._source(project_id)
        if source is None:
            runs = self.lifecycle.list_runs(project_id).runs
            current_run = runs[0] if runs else None
            can_generate = bool(
                current_run and current_run.status == ANALYSIS_READY_STATUS
            )
            return WritingPlanDTO(
                project_id=project_id,
                project_name=bundle.project_name,
                available=False,
                status="not_generated",
                status_label="尚未生成写作规划",
                message="当前招标分析已完成，但 Stage5–8 的确定性规划产物尚未生成。页面不会用旧目录或前端推导代替正式规划。",
                lineage=WritingPlanLineageDTO(
                    source_mode="not_generated",
                    analysis_run_id=bundle.lineage.run_id,
                    current_analysis_run_id=bundle.lineage.run_id,
                    is_current=True,
                    lineage_valid=True,
                ),
                summary=WritingPlanSummaryDTO(),
                generation_available=can_generate,
                generation_reason=(
                    "招标分析已完成，可以在同一 Pipeline Run 中继续生成写作规划。"
                    if can_generate
                    else "写作规划只能从已完成的招标分析继续生成。"
                ),
            )

        response = _load_json(source.response_path)
        writing = _load_json(source.writing_path)
        task_plan = _load_json(source.tasks_path)
        task_information = _load_optional(source.information_path)
        self._validate_schemas(response, writing, task_plan, task_information)

        preparation = self.preparation.get(project_id)
        preparation_by_id = {item.item_id: item for item in preparation.items}
        pending_confirmations = [
            item
            for item in preparation.items
            if item.kind == "human_confirmation" and item.status != "confirmed"
        ]
        stage9_unconsumable_confirmations = [
            item
            for item in preparation.items
            if item.kind == "human_confirmation" and not item.stage9_consumable
        ]
        scoring = self.analysis.scoring(project_id)
        score_index = {item.score_id: item for item in scoring.rules}
        raw_tasks = [item for item in task_plan.get("tasks") or [] if isinstance(item, dict)]
        requirement_titles = self._requirement_titles(bundle, response, raw_tasks, scoring.rules)
        info_index = {
            str(item.get("task_id") or ""): item
            for item in task_information.get("tasks") or []
            if isinstance(item, dict) and item.get("task_id")
        }
        tasks = [
            self._task(project_id, item, info_index.get(str(item.get("task_id") or ""), {}), preparation_by_id, requirement_titles, score_index)
            for item in raw_tasks
        ]
        task_index = {item.task_id: item for item in tasks}
        chapters = self._chapters(writing, raw_tasks, task_index)
        requirement_coverage = self._requirement_coverage(project_id, requirement_titles, raw_tasks)
        score_coverage = self._score_coverage(project_id, score_index, raw_tasks)
        lineage = self._lineage(source, response, writing, task_plan, task_information)
        status = "ready" if lineage.is_current and lineage.lineage_valid else "stale" if not lineage.is_current else "invalid"
        message = (
            "写作规划与当前招标分析一致；事实状态来自当前 Writing Context，不会回写或篡改历史规划。"
            if status == "ready"
            else lineage.stale_reason or "规划 artifact lineage 校验未通过。"
        )
        all_fact_dependencies = [value for task in tasks for value in task.fact_dependencies]
        all_material_dependencies = [value for task in tasks for value in task.material_dependencies]
        return WritingPlanDTO(
            project_id=project_id,
            project_name=bundle.project_name,
            available=True,
            status=status,
            status_label={"ready": "写作规划已完成", "stale": "规划基于旧版招标分析", "invalid": "规划 lineage 异常"}[status],
            message=message,
            lineage=lineage,
            summary=WritingPlanSummaryDTO(
                chapter_count=len(chapters),
                section_count=sum(len(item.sections) for item in chapters),
                task_count=len(tasks),
                requirement_count=len(requirement_coverage),
                score_count=len(score_coverage),
                confirmed_fact_count=len({item.fact_id for item in all_fact_dependencies if item.availability == "available"}),
                placeholder_fact_count=len({item.fact_id for item in all_fact_dependencies if item.availability != "available"}),
                query_not_executed_count=len({item.dependency_id for item in all_material_dependencies if item.status == "query_not_executed"}),
                pending_human_confirmation_count=len(pending_confirmations),
                stage9_unconsumable_confirmation_count=len(
                    stage9_unconsumable_confirmations
                ),
                unassigned_requirement_count=sum(item.status == "unassigned" for item in requirement_coverage),
            ),
            chapters=chapters,
            tasks=tasks,
            requirement_coverage=requirement_coverage,
            score_coverage=score_coverage,
            writing_context=preparation.writing_context,
            generation_available=False,
            generation_reason=None,
        )

    def stage9_input_context(self, project_id: str) -> dict[str, Any]:
        try:
            return self.stage9_resolver.resolve(self.stage9_input_source(project_id))
        except Stage9InputResolutionError as exc:
            raise WebProjectionError(str(exc)) from exc

    def stage9_input_source(self, project_id: str) -> Stage9InputSource:
        source = self._source(project_id)
        if source is None or source.information_path is None:
            raise WebProjectionError("Writing Plan 尚未形成完整的 Stage9 输入")
        fact_snapshot = self.preparation.fact_service.snapshot(project_id)
        confirmation_snapshot = self.preparation.confirmation_service.snapshot(project_id)
        return Stage9InputSource(
            project_id=project_id,
            analysis_run_id=source.analysis_run_id,
            response_outline_path=source.response_path,
            writing_outline_path=source.writing_path,
            writing_tasks_path=source.tasks_path,
            task_information_path=source.information_path,
            confirmed_project_facts=fact_snapshot.state,
            human_confirmation_state=confirmation_snapshot.state,
        )

    def revision_inputs(self, project_id: str) -> tuple[Stage9InputSource, Path | None]:
        source = self._source(project_id)
        if source is None or source.information_path is None:
            raise WebProjectionError("Writing Plan is not ready for revision")
        return self.stage9_input_source(project_id), source.draft_units_path

    def readiness(self, project_id: str) -> AIWritingReadinessDTO:
        plan = self.get(project_id)
        if not plan.available or plan.status != "ready":
            return AIWritingReadinessDTO(
                project_id=project_id,
                project_name=plan.project_name,
                writing_plan_ready=False,
                project_facts_connected=False,
                human_confirmations_connected=False,
                stage9_contract_ready=False,
                pending_policy="placeholder_required",
                message=plan.message,
            )
        try:
            handoff = self.stage9_input_context(project_id)
        except WebProjectionError as exc:
            return AIWritingReadinessDTO(
                project_id=project_id,
                project_name=plan.project_name,
                writing_plan_ready=True,
                project_facts_connected=False,
                human_confirmations_connected=False,
                task_count=plan.summary.task_count,
                stage9_contract_ready=False,
                pending_policy="placeholder_required",
                message=str(exc),
            )
        summary = handoff["summary"]
        blocked = int(summary["blocked_task_count"])
        task_count = int(summary["task_count"])
        return AIWritingReadinessDTO(
            project_id=project_id,
            project_name=plan.project_name,
            writing_plan_ready=True,
            project_facts_connected=True,
            human_confirmations_connected=True,
            pending_confirmation_count=int(summary["pending_confirmation_count"]),
            confirmed_business_choice_count=int(
                summary["confirmed_business_choice_count"]
            ),
            task_count=task_count,
            task_ready_count=task_count - blocked,
            task_blocked_count=blocked,
            placeholder_task_count=int(summary["placeholder_task_count"]),
            stage9_contract_ready=True,
            pending_policy="placeholder_required",
            input_fingerprint=str(handoff["fingerprint"]),
            message=(
                "Stage9 输入契约已连接；待确认业务选择将在受影响任务中使用稳定占位符。"
                if summary["pending_confirmation_count"]
                else "Stage9 输入契约已连接，当前业务选择均已按任务范围解析。"
            ),
        )

    def generation_inputs(self, project_id: str, task_id: str) -> dict[str, Any]:
        """Resolve controlled canonical inputs for exactly one Writing Task."""
        source = self._source(project_id)
        if source is None or source.information_path is None:
            raise WebProjectionError("Writing Plan is not ready for Stage9")
        handoff = self.stage9_input_context(project_id)
        contexts = [
            item for item in handoff.get("tasks") or []
            if str(item.get("task_id") or "") == task_id
        ]
        if not contexts:
            raise WebProjectionError(f"Writing Task is not available: {task_id}")
        task_context = contexts[0]
        section_id = str(task_context.get("section_id") or "")
        same_section = [
            item for item in handoff.get("tasks") or []
            if str(item.get("section_id") or "") == section_id
        ]
        if len(same_section) != 1:
            raise WebProjectionError(
                "V1 task generation requires a one-to-one Writing Task and Section mapping"
            )
        fact_snapshot = self.preparation.fact_service.snapshot(project_id)
        return {
            "source": source,
            "handoff": handoff,
            "task_context": task_context,
            "confirmed_project_facts_path": (
                fact_snapshot.state_path if fact_snapshot.state_path.is_file() else None
            ),
        }

    def _source(self, project_id: str) -> _PlanningSource | None:
        try:
            manifest = self.lifecycle._load_manifest(project_id)
            state = self.lifecycle._latest_analysis_state(manifest)
            response = self._stage_path(state, "build_technical_response_outline", "technical_response_outline_json", required=False)
            writing = self._stage_path(state, "build_writing_tasks", "technical_writing_outline_json", required=False)
            tasks = self._stage_path(state, "build_writing_tasks", "section_writing_tasks_json", required=False)
            information = self._stage_path(state, "build_writing_tasks", "task_information_requirements_json", required=False)
            draft_units = self._stage_path(state, "build_writing_tasks", "draft_units_json", required=False)
            if not all((response, writing, tasks)):
                return None
            return _PlanningSource(
                source_mode="pipeline_planning",
                analysis_run_id=str(state.get("run_id") or "") or None,
                current_analysis_run_id=str(state.get("run_id") or "") or None,
                planning_key=str((state.get("routing") or {}).get("planning_output_key") or "") or None,
                response_path=response,
                writing_path=writing,
                tasks_path=tasks,
                information_path=information,
                draft_units_path=draft_units,
            )
        except LifecycleProjectNotFoundError:
            artifacts = self.resolver.resolve(project_id)
            run = _load_json(artifacts.root / "fulltext" / "technical_fulltext.run.json")
            inputs = run.get("input_paths") if isinstance(run.get("input_paths"), dict) else {}
            response = self._safe_reference(inputs.get("response_outline_path"))
            writing = self._safe_reference(inputs.get("writing_outline_path"))
            information = self._safe_reference(inputs.get("task_information_path"), required=False)
            draft_units = self._safe_reference(inputs.get("draft_units_path"), required=False)
            task_dir = information.parent if information else writing.parent / "tasks"
            tasks = self._safe_reference(task_dir / "section_writing_tasks.json")
            return _PlanningSource(
                source_mode="legacy_planning",
                analysis_run_id=None,
                current_analysis_run_id=None,
                planning_key=writing.parent.parent.name,
                response_path=response,
                writing_path=writing,
                tasks_path=tasks,
                information_path=information,
                draft_units_path=draft_units,
            )

    def _stage_path(self, state: dict[str, Any], stage_id: str, key: str, *, required: bool) -> Path | None:
        stage = next((item for item in state.get("stages") or [] if item.get("stage_id") == stage_id), {})
        reference = (stage.get("artifacts") or {}).get(key)
        if not reference:
            if required:
                raise WebProjectionError(f"writing plan artifact is unavailable: {key}")
            return None
        return self._safe_reference(reference, required=required)

    def _safe_reference(self, reference: Any, *, required: bool = True) -> Path | None:
        if isinstance(reference, Path):
            path = reference.expanduser().resolve()
        else:
            text = str(reference or "").strip()
            if not text:
                if required:
                    raise WebProjectionError("writing plan artifact reference is unavailable")
                return None
            path = Path(text).expanduser().resolve()
        try:
            path.relative_to(self.outputs_root)
        except ValueError as exc:
            raise WebProjectionError("writing plan artifact is outside the configured output root") from exc
        if not path.is_file():
            if required:
                raise WebProjectionError("writing plan artifact is unavailable")
            return None
        return path

    def _lineage(
        self,
        source: _PlanningSource,
        response: dict[str, Any],
        writing: dict[str, Any],
        task_plan: dict[str, Any],
        task_information: dict[str, Any],
    ) -> WritingPlanLineageDTO:
        response_sha = _sha256(source.response_path)
        writing_sha = _sha256(source.writing_path)
        tasks_sha = _sha256(source.tasks_path)
        information_sha = _sha256(source.information_path) if source.information_path else None
        expected_response = str((writing.get("source_response_outline") or {}).get("sha256") or "")
        integrity = task_information.get("source_integrity") if isinstance(task_information.get("source_integrity"), dict) else {}
        expected_information_response = str(
            ((integrity.get("response") or {}).get("sha256_after")) or ""
        )
        expected_information_writing = str(
            ((integrity.get("writing") or {}).get("sha256_after")) or ""
        )
        expected_tasks = str(((integrity.get("tasks") or {}).get("sha256_after")) or "")
        lineage_valid = (
            (not expected_response or expected_response == response_sha)
            and (
                not expected_information_response
                or expected_information_response == response_sha
            )
            and (
                not expected_information_writing
                or expected_information_writing == writing_sha
            )
            and (not expected_tasks or expected_tasks == tasks_sha)
        )
        is_current = source.analysis_run_id == source.current_analysis_run_id
        reasons: list[str] = []
        if not is_current:
            reasons.append("当前写作规划属于另一 Analysis Run。")
        if expected_response and expected_response != response_sha:
            reasons.append("technical writing outline 引用的 response outline SHA 与当前规划 bundle 不一致。")
        if expected_information_response and expected_information_response != response_sha:
            reasons.append("task information 引用的 response outline SHA 与当前规划 bundle 不一致。")
        if expected_information_writing and expected_information_writing != writing_sha:
            reasons.append("task information 引用的 writing outline SHA 与当前规划 bundle 不一致。")
        if expected_tasks and expected_tasks != tasks_sha:
            reasons.append("task information 引用的 section task SHA 与当前规划 bundle 不一致。")
        return WritingPlanLineageDTO(
            source_mode=source.source_mode,  # type: ignore[arg-type]
            analysis_run_id=source.analysis_run_id,
            current_analysis_run_id=source.current_analysis_run_id,
            planning_key=source.planning_key,
            generated_at=_optional_text(response.get("generated_at")),
            response_outline_sha256=response_sha,
            writing_outline_sha256=writing_sha,
            writing_tasks_sha256=tasks_sha,
            task_information_sha256=information_sha,
            is_current=is_current,
            lineage_valid=lineage_valid,
            stale_reason=" ".join(reasons) or None,
        )

    def _task(
        self,
        project_id: str,
        raw: dict[str, Any],
        information: dict[str, Any],
        preparation_by_id: dict[str, Any],
        requirement_titles: dict[str, str],
        score_index: dict[str, Any],
    ) -> WritingTaskDTO:
        task_id = str(raw.get("task_id") or "")
        chapter_id = str(raw.get("chapter_id") or "")
        section_id = str(raw.get("section_id") or chapter_id)
        mappings = [item for item in raw.get("response_point_mappings") or [] if isinstance(item, dict)]
        requirement_refs = [
            WritingPlanReferenceDTO(
                trace_id=str(item.get("response_point_id") or ""),
                title=requirement_titles.get(str(item.get("response_point_id") or ""), str(item.get("response_point_id") or "")),
                mapping_role=_optional_text(item.get("mapping_role")),
                target_url=f"/projects/{quote(project_id, safe='')}/analysis/requirements/{quote(str(item.get('response_point_id') or ''), safe='')}",
            )
            for item in mappings
            if item.get("response_point_id")
            and str(item.get("response_point_id") or "") in requirement_titles
        ]
        score_refs: list[WritingPlanReferenceDTO] = []
        for value in raw.get("score_item_refs") or []:
            score_id = str(value)
            score = score_index.get(score_id)
            score_refs.append(
                WritingPlanReferenceDTO(
                    trace_id=score_id,
                    title=str(getattr(score, "title", None) or score_id),
                    description=str(getattr(score, "criteria_text", None) or "") or None,
                    max_score=getattr(score, "available_score", None),
                    target_url=f"/projects/{quote(project_id, safe='')}/analysis/scoring?scoreItem={quote(score_id, safe='')}",
                )
            )
        facts: list[WritingPlanFactDependencyDTO] = []
        placeholders: list[str] = []
        prohibited: list[str] = []
        for slot in raw.get("project_fact_slots") or []:
            if not isinstance(slot, dict):
                continue
            fact_id = str(slot.get("fact_slot_id") or "")
            current = preparation_by_id.get(fact_id)
            available = bool(current and current.status == "confirmed" and current.current_value)
            if not available:
                placeholders.append(fact_id)
                prohibited.append(f"不得自行推断：{slot.get('name') or slot.get('fact_key') or fact_id}")
            facts.append(
                WritingPlanFactDependencyDTO(
                    fact_id=fact_id,
                    fact_key=str(slot.get("fact_key") or ""),
                    title=str(slot.get("name") or slot.get("fact_key") or fact_id),
                    required=True,
                    status="confirmed" if available else "missing",
                    status_label="已确认" if available else "待确认；写作时使用待确认字段",
                    availability="available" if available else "placeholder_required",
                    value=current.current_value if available else None,
                    source_policy=str(slot.get("source_policy") or "confirmed_project_fact"),
                    target_url=f"/projects/{quote(project_id, safe='')}/writing-preparation?item={quote(fact_id, safe='')}",
                )
            )
        materials: list[WritingPlanMaterialDependencyDTO] = []
        task_queries = [item for item in raw.get("kb_query_plan") or [] if isinstance(item, dict)]
        for index, slot in enumerate(raw.get("evidence_slots") or [], start=1):
            if not isinstance(slot, dict):
                continue
            evidence_type = str(slot.get("evidence_type") or "")
            query = next((item for item in task_queries if str(item.get("evidence_type") or "") == evidence_type), None)
            query_id = str((query or {}).get("query_id") or f"{task_id}-EVIDENCE-{index}")
            execution = str((query or {}).get("execution_status") or "")
            status = "query_not_executed" if execution in {"", "planned_not_executed", "query_not_executed", "not_executed"} else "candidate" if execution in {"found", "candidate", "completed_with_candidates"} else execution
            status_label = "尚未查询" if status == "query_not_executed" else "已发现，待核验" if status == "candidate" else status or "待准备"
            materials.append(
                WritingPlanMaterialDependencyDTO(
                    dependency_id=query_id,
                    evidence_type=evidence_type,
                    title=evidence_type or "证明材料",
                    required=bool(slot.get("required", True)),
                    status=status or "not_started",
                    status_label=status_label,
                    source_policy=str(slot.get("source_policy") or "local_kb_evidence"),
                    target_url=f"/projects/{quote(project_id, safe='')}/writing-preparation?item={quote(query_id, safe='')}",
                )
            )
        readiness = str((information.get("draftability") or {}).get("status") or raw.get("readiness") or "planned")
        human_confirmations = [
            current.writing_context
            for current in preparation_by_id.values()
            if current.kind == "human_confirmation"
            and current.writing_context is not None
            and any(
                dependency.kind == "writing"
                and (
                    dependency.trace_id == section_id
                    or dependency.description == f"Writing Task {task_id}"
                )
                for dependency in current.dependencies
            )
        ]
        pending_business_choices = [
            item for item in human_confirmations if item.availability != "available"
        ]
        confirmed_business_choices = [
            item for item in human_confirmations if item.availability == "available"
        ]
        if placeholders or pending_business_choices:
            count = len(placeholders) + len(pending_business_choices)
            readiness_label = f"可进入 AI 写作，但包含 {count} 个稳定待确认字段"
        elif confirmed_business_choices:
            readiness_label = "当前业务选择已作为写作约束接入 Stage9"
        elif any(item.status == "query_not_executed" for item in materials):
            readiness_label = "可进入 AI 写作，证明材料尚未查询"
        else:
            readiness_label = "当前上下文可用"
        missing_inputs = [str(value) for value in (information.get("draftability") or {}).get("missing_inputs") or []]
        prohibited.extend(f"不得将未核验材料当作已有证据：{value}" for value in missing_inputs if value)
        return WritingTaskDTO(
            task_id=task_id,
            chapter_id=chapter_id,
            section_id=section_id,
            title=" / ".join(str(value) for value in raw.get("target_path") or []) or section_id,
            objective=str(raw.get("writing_objective") or "按当前规划完成可审查的技术响应内容。"),
            instruction_points=[str(value) for value in raw.get("required_content_points") or []],
            requirement_refs=requirement_refs,
            score_refs=score_refs,
            fact_dependencies=facts,
            material_dependencies=materials,
            human_confirmation_refs=human_confirmations,
            placeholder_refs=placeholders,
            prohibited_inference=_dedupe(prohibited),
            readiness=readiness,
            readiness_label=readiness_label,
            body_generated=bool(raw.get("body_generated", False)),
        )

    def _chapters(self, writing: dict[str, Any], raw_tasks: list[dict[str, Any]], task_index: dict[str, WritingTaskDTO]) -> list[WritingChapterDTO]:
        result: list[WritingChapterDTO] = []
        for chapter_order, raw in enumerate(writing.get("chapters") or [], start=1):
            if not isinstance(raw, dict):
                continue
            chapter_id = str(raw.get("chapter_id") or "")
            chapter_tasks = [task for task in task_index.values() if task.chapter_id == chapter_id]
            sections: list[WritingSectionDTO] = []
            if any(task.section_id == chapter_id for task in chapter_tasks):
                sections.append(self._section(raw, chapter_id, [str(raw.get("title") or chapter_id)], 0, 1, task_index))
            order = len(sections)
            for child in raw.get("children") or []:
                order = self._append_sections(sections, child, chapter_id, [str(raw.get("title") or chapter_id)], 1, order, task_index)
            requirements = {item.trace_id for task in chapter_tasks for item in task.requirement_refs}
            scores = {item.trace_id for task in chapter_tasks for item in task.score_refs}
            pending_facts = {item.fact_id for task in chapter_tasks for item in task.fact_dependencies if item.availability != "available"}
            pending_materials = {item.dependency_id for task in chapter_tasks for item in task.material_dependencies if item.status != "verified"}
            result.append(
                WritingChapterDTO(
                    chapter_id=chapter_id,
                    title=str(raw.get("title") or chapter_id),
                    order=chapter_order,
                    purpose=str(raw.get("adaptation_reason") or _first_content_point(raw) or "按技术写作规划组织本章响应内容。"),
                    sections=sections,
                    task_ids=[item.task_id for item in chapter_tasks],
                    requirement_count=len(requirements),
                    score_count=len(scores),
                    pending_fact_count=len(pending_facts),
                    pending_material_count=len(pending_materials),
                )
            )
        return result

    def _append_sections(self, result: list[WritingSectionDTO], raw: Any, chapter_id: str, parent_path: list[str], depth: int, order: int, task_index: dict[str, WritingTaskDTO]) -> int:
        if not isinstance(raw, dict):
            return order
        section_id = str(raw.get("chapter_id") or "")
        title = str(raw.get("title") or section_id)
        order += 1
        result.append(self._section(raw, chapter_id, [*parent_path, title], depth, order, task_index))
        for child in raw.get("children") or []:
            order = self._append_sections(result, child, chapter_id, [*parent_path, title], depth + 1, order, task_index)
        return order

    def _section(self, raw: dict[str, Any], chapter_id: str, path: list[str], depth: int, order: int, task_index: dict[str, WritingTaskDTO]) -> WritingSectionDTO:
        section_id = str(raw.get("chapter_id") or chapter_id)
        tasks = [item for item in task_index.values() if item.section_id == section_id]
        requirements = {item.trace_id for task in tasks for item in task.requirement_refs}
        scores = {item.trace_id for task in tasks for item in task.score_refs}
        pending_facts = {item.fact_id for task in tasks for item in task.fact_dependencies if item.availability != "available"}
        pending_materials = {item.dependency_id for task in tasks for item in task.material_dependencies if item.status != "verified"}
        if not tasks:
            state = "尚未建立 Writing Task"
        elif pending_facts:
            state = f"可写框架，{len(pending_facts)} 项事实待确认"
        elif pending_materials:
            state = f"可进入写作，{len(pending_materials)} 项材料待准备"
        else:
            state = "当前上下文可用"
        purpose = tasks[0].objective if tasks else str(raw.get("adaptation_reason") or _first_content_point(raw) or "按规划完成本节响应。")
        return WritingSectionDTO(
            section_id=section_id,
            chapter_id=chapter_id,
            title=str(raw.get("title") or section_id),
            path=path,
            order=order,
            depth=depth,
            purpose=purpose,
            task_ids=[item.task_id for item in tasks],
            requirement_count=len(requirements),
            score_count=len(scores),
            pending_fact_count=len(pending_facts),
            pending_material_count=len(pending_materials),
            preparation_label=state,
        )

    def _requirement_coverage(self, project_id: str, titles: dict[str, str], raw_tasks: list[dict[str, Any]]) -> list[WritingPlanCoverageDTO]:
        sections: dict[str, set[str]] = {value: set() for value in titles}
        for task in raw_tasks:
            section_id = str(task.get("section_id") or task.get("chapter_id") or "")
            for mapping in task.get("response_point_mappings") or []:
                if isinstance(mapping, dict):
                    requirement_id = str(mapping.get("response_point_id") or "")
                    if requirement_id:
                        sections.setdefault(requirement_id, set()).add(section_id)
        result: list[WritingPlanCoverageDTO] = []
        for trace_id, title in titles.items():
            mapped = sections.get(trace_id, set())
            result.append(
                WritingPlanCoverageDTO(
                    trace_id=trace_id,
                    title=title,
                    section_ids=sorted(mapped),
                    status="unassigned" if not mapped else "multiple_sections" if len(mapped) > 1 else "planned",
                    target_url=f"/projects/{quote(project_id, safe='')}/analysis/requirements/{quote(trace_id, safe='')}",
                )
            )
        return result

    def _score_coverage(self, project_id: str, score_index: dict[str, Any], raw_tasks: list[dict[str, Any]]) -> list[WritingPlanCoverageDTO]:
        sections: dict[str, set[str]] = {value: set() for value in score_index}
        for task in raw_tasks:
            section_id = str(task.get("section_id") or task.get("chapter_id") or "")
            for value in task.get("score_item_refs") or []:
                sections.setdefault(str(value), set()).add(section_id)
        return [
            WritingPlanCoverageDTO(
                trace_id=trace_id,
                title=str(getattr(score, "title", None) or trace_id),
                section_ids=sorted(sections.get(trace_id, set())),
                status="unassigned" if not sections.get(trace_id) else "multiple_sections" if len(sections[trace_id]) > 1 else "planned",
                max_score=getattr(score, "available_score", None),
                target_url=f"/projects/{quote(project_id, safe='')}/analysis/scoring?scoreItem={quote(trace_id, safe='')}",
            )
            for trace_id, score in score_index.items()
        ]

    def _requirement_titles(
        self,
        bundle: Any,
        response: dict[str, Any],
        raw_tasks: list[dict[str, Any]],
        scoring_rules: list[Any],
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        matrix_requirements = [
            raw
            for raw in bundle.requirement_matrix.get("requirements") or []
            if isinstance(raw, dict)
        ]
        matrix_ids = {
            str(raw.get("requirement_id") or "")
            for raw in matrix_requirements
            if raw.get("requirement_id")
        }
        mapped_requirement_ids = {
            str(mapping.get("response_point_id") or "")
            for task in raw_tasks
            for mapping in task.get("response_point_mappings") or []
            if isinstance(mapping, dict) and mapping.get("response_point_id")
        }
        technical_score_ids = {
            str(getattr(rule, "score_id", "") or "")
            for rule in scoring_rules
            if getattr(rule, "score_id", None)
        }
        technical_score_requirement_ids = {
            str(requirement_id)
            for rule in scoring_rules
            for requirement_id in getattr(rule, "requirement_ids", [])
            if requirement_id
        }
        for raw in matrix_requirements:
            if not is_technical_writing_requirement(
                raw,
                mapped_requirement_ids=mapped_requirement_ids,
                technical_score_ids=technical_score_ids,
                technical_score_requirement_ids=technical_score_requirement_ids,
            ):
                continue
            if not isinstance(raw, dict):
                continue
            trace_id = str(raw.get("requirement_id") or "")
            text = str((raw.get("normalized_requirement") or {}).get("text") or raw.get("raw_text") or raw.get("title") or trace_id)
            if trace_id:
                values[trace_id] = text
        for chapter in response.get("chapters") or []:
            for raw in chapter.get("response_points") or []:
                trace_id = str(raw.get("point_id") or "")
                if not trace_id or trace_id in matrix_ids:
                    continue
                source_category = str(raw.get("source_category") or "")
                if source_category in {"technical_specification", "technical_score"} or trace_id in mapped_requirement_ids:
                    values[trace_id] = str(raw.get("original_text") or raw.get("original_heading") or trace_id)
        return values

    @staticmethod
    def _validate_schemas(response: dict[str, Any], writing: dict[str, Any], tasks: dict[str, Any], information: dict[str, Any]) -> None:
        expected = (
            (response, "technical_response_outline_v2"),
            (writing, "technical_writing_outline_v0"),
            (tasks, "technical_section_writing_tasks_v0"),
        )
        for payload, schema in expected:
            if payload.get("schema_version") != schema:
                raise WebProjectionError(f"unsupported writing plan artifact schema: {payload.get('schema_version')}")
        if information and information.get("schema_version") != "technical_task_information_requirements_v0":
            raise WebProjectionError("unsupported task information artifact schema")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebProjectionError(f"writing plan artifact is unavailable or invalid: {path.name}") from exc
    if not isinstance(payload, dict):
        raise WebProjectionError(f"writing plan artifact must contain an object: {path.name}")
    return payload


def _load_optional(path: Path | None) -> dict[str, Any]:
    return _load_json(path) if path and path.is_file() else {}


def _sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_content_point(raw: dict[str, Any]) -> str | None:
    values = (raw.get("writing_requirements") or {}).get("required_content_points") or []
    return str(values[0]) if values else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))

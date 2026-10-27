from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.web.dtos import (
    AnalysisLineageDTO,
    AnalysisPriorityItemDTO,
    AnalysisSourceFileDTO,
    FactMaterialHeadlineDTO,
    FactMaterialItemDTO,
    FactMaterialSummaryDTO,
    PreparationNeedDTO,
    RequirementCategoryCountDTO,
    RequirementDependencyDTO,
    RequiredSubmissionItemDTO,
    SourceReferenceDTO,
    TenderAnalysisOverviewDTO,
    TenderParameterTableDTO,
    TenderParametersDTO,
    TenderProjectInfoItemDTO,
    TenderRiskDTO,
    TenderRisksPreparationDTO,
    TenderScoringRuleDTO,
    TenderScoringRulesDTO,
    TenderTableCellDTO,
    VersionRefDTO,
)
from app.web.project_lifecycle_service import (
    LifecycleProjectNotFoundError,
    ProjectLifecycleError,
    ProjectLifecycleService,
)
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import ProjectReadService, WebProjectionError


class AnalysisItemNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class _AnalysisBundle:
    project_id: str
    project_name: str
    lineage: AnalysisLineageDTO
    sources: list[dict[str, Any]]
    context: dict[str, Any]
    requirement_matrix: dict[str, Any]
    actionable_index: dict[str, Any]
    scoring_plan: dict[str, Any]
    score_matrix: dict[str, Any]
    missing_materials: dict[str, Any]
    response_outline: dict[str, Any]


PROJECT_FIELDS = (
    ("tenderee", "采购人"),
    ("tender_number", "项目编号"),
    ("project_name", "项目名称"),
    ("technical_topic_name", "采购内容"),
    ("budget", "预算"),
    ("max_price", "最高限价"),
    ("deadline", "投标截止时间"),
    ("delivery_period", "交付期"),
    ("service_period", "服务期"),
    ("warranty_period", "质保期"),
    ("project_location", "项目地点"),
)

REQUIREMENT_LABELS = {
    "technical_mandatory_requirement": "技术强制要求",
    "mandatory_substantive_response": "实质响应要求",
    "qualification_requirement": "资格要求",
    "format_submission_requirement": "格式与提交要求",
    "score_related_requirement": "评分相关要求",
    "delivery_acceptance_commitment": "交付与验收要求",
    "potential_invalidity_risk": "否决风险要求",
    "reference_only": "参考信息",
    "procurement_document": "商务与履约要求",
    "technical_specification": "技术要求",
    "project_requirement": "项目与采购清单",
    "technical_score": "评分规则",
    "technical_format": "投标文件格式要求",
}

ACTION_LABELS = {
    "manual_confirm": "需要人工确认",
    "provide_evidence": "需要证明材料",
    "write_response": "需要重点响应",
    "fill_form": "需要填写表格",
    "external_reference_resolve": "需要补齐引用文件",
    "comply_format": "需要遵守格式",
    "no_direct_action": "无需直接操作",
}

SOURCE_ROLE_LABELS = {
    "main_tender": "主招标文件",
    "technical_spec": "技术参数 / 规范",
    "scoring_document": "评分文件",
    "attachment": "附件",
    "supplement": "补充 / 澄清文件",
    "unknown": "待确认",
    "procurement_document": "主招标文件",
    "technical_specification": "技术参数 / 规范",
    "technical_score": "评分文件",
    "tender_source": "招标资料",
}


class TenderAnalysisReadService:
    """Project Stage1-4 projections. Stage11/12 artifacts are never scoring or risk inputs."""

    def __init__(
        self,
        resolver: ReadOnlyProjectResolver,
        lifecycle: ProjectLifecycleService,
        project_read: ProjectReadService,
    ) -> None:
        self.resolver = resolver
        self.lifecycle = lifecycle
        self.project_read = project_read
        self.outputs_root = resolver.outputs_root.resolve()

    def overview(self, project_id: str) -> TenderAnalysisOverviewDTO:
        bundle = self._bundle(project_id)
        scoring = self.scoring(project_id)
        parameters = self.parameters(project_id)
        preparation = self.risks_preparation(project_id)
        categories = self._requirement_categories(bundle)
        requirement_count = sum(item.count for item in categories)
        if bundle.requirement_matrix:
            requirement_count = len(bundle.requirement_matrix.get("requirements") or [])
        elif bundle.response_outline:
            requirement_count = len(_outline_points(bundle.response_outline))
        priority = self._priority_items(bundle)[:6]
        return TenderAnalysisOverviewDTO(
            project_id=project_id,
            project_name=bundle.project_name,
            status="招标分析已完成",
            lineage=bundle.lineage,
            source_file_count=len(bundle.sources),
            project_information=self._project_information(bundle),
            requirement_count=requirement_count,
            requirement_categories=categories,
            score_rule_count=scoring.rule_count,
            available_score_total=scoring.available_score_total,
            parameter_table_count=parameters.table_count,
            parameter_row_count=parameters.row_count,
            fact_need_count=len(preparation.facts),
            material_need_count=len(preparation.materials),
            confirmation_count=len(preparation.confirmations),
            priority_items=priority,
        )

    def files(self, project_id: str) -> list[AnalysisSourceFileDTO]:
        bundle = self._bundle(project_id)
        values: list[AnalysisSourceFileDTO] = []
        for item in bundle.sources:
            role = str(item.get("role") or item.get("source_category") or "tender_source")
            values.append(
                AnalysisSourceFileDTO(
                    file_id=str(item.get("file_id") or ""),
                    display_name=str(item.get("display_name") or item.get("file_name") or item.get("original_name") or "招标资料"),
                    role=role,
                    role_label=SOURCE_ROLE_LABELS.get(role, "招标资料"),
                    parse_status=str(item.get("status") or item.get("parse_status") or "parsed"),
                    size=_optional_int(item.get("size")),
                    source_reference=None,
                )
            )
        return values

    def scoring(self, project_id: str) -> TenderScoringRulesDTO:
        bundle = self._bundle(project_id)
        rules = self._pipeline_scoring(bundle) if bundle.context else self._outline_scoring(bundle)
        values = [item.available_score for item in rules if item.available_score is not None]
        return TenderScoringRulesDTO(
            project_id=project_id,
            lineage=bundle.lineage,
            rule_count=len(rules),
            available_score_total=round(sum(values), 6) if values else None,
            rules=rules,
            machine_assessment_included=False,
        )

    def scoring_detail(self, project_id: str, score_id: str) -> TenderScoringRuleDTO:
        result = self.scoring(project_id)
        match = next((item for item in result.rules if item.score_id == score_id), None)
        if match is None:
            raise AnalysisItemNotFoundError(f"analysis scoring rule is not available: {score_id}")
        return match

    def parameters(self, project_id: str) -> TenderParametersDTO:
        bundle = self._bundle(project_id)
        tables = self._outline_parameter_tables(bundle) if bundle.response_outline else self._matrix_parameter_tables(bundle)
        return TenderParametersDTO(
            project_id=project_id,
            lineage=bundle.lineage,
            table_count=len(tables),
            row_count=sum(item.row_count for item in tables),
            tables=tables,
        )

    def risks_preparation(self, project_id: str) -> TenderRisksPreparationDTO:
        bundle = self._bundle(project_id)
        if bundle.actionable_index or bundle.context:
            return self._pipeline_preparation(bundle)
        return self._outline_preparation(bundle)

    def facts_materials(self, project_id: str) -> FactMaterialSummaryDTO:
        value = self.risks_preparation(project_id)
        items: list[FactMaterialItemDTO] = []
        base = f"/projects/{quote(project_id, safe='')}"
        for need in value.facts + value.confirmations + value.materials:
            category = "missing_project_fact" if need.kind == "project_fact" else "confirmation_required" if need.kind == "fact_confirmation" else "material_preparation"
            items.append(
                FactMaterialItemDTO(
                    category=category,
                    title=need.title,
                    status=need.status,
                    status_label=need.status_label,
                    description=need.description,
                    required=need.required,
                    related_task_count=len(need.requirement_ids) + len(need.score_item_ids),
                    trace_id=need.trace_id,
                    action_label="填写信息" if need.kind == "project_fact" else "确认" if need.kind == "fact_confirmation" else "查看 / 补充材料",
                    action_url=f"{base}/writing-preparation?item={quote(need.trace_id, safe='')}",
                )
            )
        counts = Counter(item.status for item in items)
        return FactMaterialSummaryDTO(
            project_id=project_id,
            reviewed_version=VersionRefDTO(
                version_id=value.lineage.run_id or "legacy-analysis",
                label="写作前准备状态",
                content_sha256=None,
                artifact_sha256=None,
                status="analysis_ready",
            ),
            headline=FactMaterialHeadlineDTO(
                missing_project_facts=len(value.facts),
                confirmations_required=len(value.confirmations),
                queries_not_executed=sum(item.status == "query_not_executed" for item in value.materials),
                candidate_materials=sum(item.status == "candidate" for item in value.materials),
                verified_materials=sum(item.status == "verified" for item in value.materials),
            ),
            status_legend={
                "not_started": "待准备",
                "query_not_executed": "尚未查询",
                "confirmation_required": "待人工确认",
                "candidate": "候选材料",
                "verified": "已准备",
            },
            status_counts=dict(counts),
            items=items,
        )

    def required_submission_items(self, project_id: str) -> list[RequiredSubmissionItemDTO]:
        """Project applicable form/upload reminders; never participates in the release gate."""
        try:
            bundle = self._bundle(project_id)
        except (ProjectLifecycleError, WebProjectionError):
            return []
        return project_required_submission_items(
            bundle.requirement_matrix,
            bundle.actionable_index,
        )

    def _bundle(self, project_id: str) -> _AnalysisBundle:
        try:
            manifest = self.lifecycle._load_manifest(project_id)
        except LifecycleProjectNotFoundError:
            return self._legacy_bundle(project_id)
        state = self.lifecycle._latest_analysis_state(manifest)
        context = self._stage_json(state, "build_project_response_context", "technical_response_context")
        matrix = self._stage_json(state, "build_requirement_analysis", "tender_requirement_matrix_json")
        actionable = self._stage_json(state, "build_requirement_analysis", "actionable_requirement_index_json")
        scoring_plan = self._stage_json(state, "build_project_response_context", "scoring_action_plan_json")
        score_matrix = self._stage_json(state, "build_scoring_analysis", "score_maximization_matrix_json")
        missing = self._stage_json(state, "build_scoring_analysis", "missing_material_request_json")
        project_name = str(manifest.get("project_name") or "投标项目")
        return _AnalysisBundle(
            project_id=project_id,
            project_name=project_name,
            lineage=AnalysisLineageDTO(
                source_mode="pipeline_analysis",
                run_id=str(state.get("run_id") or "") or None,
                completed_at=_optional_text(state.get("completed_at") or state.get("updated_at")),
                analysis_ready=True,
                post_writing_available=False,
            ),
            sources=list(manifest.get("source_files") or []),
            context=context,
            requirement_matrix=matrix,
            actionable_index=actionable,
            scoring_plan=scoring_plan,
            score_matrix=score_matrix,
            missing_materials=missing,
            response_outline={},
        )

    def _legacy_bundle(self, project_id: str) -> _AnalysisBundle:
        artifacts = self.resolver.resolve(project_id)
        run = _load_json(artifacts.root / "fulltext" / "technical_fulltext.run.json")
        reference = (run.get("input_paths") or {}).get("response_outline_path")
        outline_path = self._safe_reference(reference)
        outline = _load_json(outline_path)
        project = outline.get("project") if isinstance(outline.get("project"), dict) else {}
        summary = self.project_read.overview(project_id).project
        project_name = _clean_value(project.get("project_name")) or summary.project_name
        requirements_dir = artifacts.root / "requirements"
        matrix_path = requirements_dir / "tender_requirement_matrix.json"
        actionable_path = requirements_dir / "actionable_requirement_index.json"
        return _AnalysisBundle(
            project_id=project_id,
            project_name=project_name,
            lineage=AnalysisLineageDTO(
                source_mode="legacy_response_outline",
                run_id=None,
                completed_at=_optional_text(outline.get("generated_at") or run.get("completed_at")),
                analysis_ready=True,
                post_writing_available=True,
            ),
            sources=list(outline.get("source_files") or []),
            context={},
            requirement_matrix=_load_json(matrix_path) if matrix_path.is_file() else {},
            actionable_index=_load_json(actionable_path) if actionable_path.is_file() else {},
            scoring_plan={},
            score_matrix={},
            missing_materials={},
            response_outline=outline,
        )

    def _stage_json(self, state: dict[str, Any], stage_id: str, key: str) -> dict[str, Any]:
        stage = next((item for item in state.get("stages") or [] if item.get("stage_id") == stage_id), {})
        reference = (stage.get("artifacts") or {}).get(key)
        if not reference:
            return {}
        return _load_json(self._safe_reference(reference))

    def _safe_reference(self, reference: Any) -> Path:
        try:
            path = Path(str(reference or "")).expanduser().resolve()
            path.relative_to(self.outputs_root)
        except (OSError, ValueError) as exc:
            raise WebProjectionError("analysis artifact is outside the configured output root") from exc
        if not path.is_file():
            raise WebProjectionError("analysis artifact is unavailable")
        return path

    def _project_information(self, bundle: _AnalysisBundle) -> list[TenderProjectInfoItemDTO]:
        values: list[TenderProjectInfoItemDTO] = []
        project = bundle.context.get("project") if bundle.context else bundle.response_outline.get("project")
        project = project if isinstance(project, dict) else {}
        for key, label in PROJECT_FIELDS:
            value = _clean_value(project.get(key))
            if value:
                values.append(TenderProjectInfoItemDTO(key=key, label=label, value=value, source_reference=None))
        if bundle.response_outline:
            background = next((item for item in _outline_points(bundle.response_outline) if item.get("source_category") == "project_requirement" and item.get("content_type") == "paragraph"), None)
            if background:
                values.append(TenderProjectInfoItemDTO(key="project_background", label="项目背景", value=str(background.get("original_text") or ""), source_reference=_outline_source(background)))
        return values

    def _requirement_categories(self, bundle: _AnalysisBundle) -> list[RequirementCategoryCountDTO]:
        if bundle.requirement_matrix:
            counter = Counter(str(item.get("requirement_type") or "unknown") for item in bundle.requirement_matrix.get("requirements") or [])
        else:
            counter = Counter(str(item.get("source_category") or "unknown") for item in _outline_points(bundle.response_outline))
        return [RequirementCategoryCountDTO(key=key, label=REQUIREMENT_LABELS.get(key, key), count=count) for key, count in counter.most_common()]

    def _priority_items(self, bundle: _AnalysisBundle) -> list[AnalysisPriorityItemDTO]:
        values: list[AnalysisPriorityItemDTO] = []
        for item in bundle.actionable_index.get("action_groups") or []:
            priority = str(item.get("action_priority") or "")
            action = str(item.get("primary_action_kind") or item.get("action_kind") or "")
            if priority not in {"critical", "high"} or action == "no_direct_action":
                continue
            values.append(AnalysisPriorityItemDTO(
                trace_id=str(item.get("action_group_id") or ""),
                title=str(item.get("canonical_title") or "优先准备事项"),
                description=str((item.get("canonical_requirement") or {}).get("text") or ""),
                action_kind=action,
                action_label=ACTION_LABELS.get(action, "建议优先准备"),
                priority=priority,
                requirement_ids=[str(value) for value in item.get("member_requirement_ids") or []],
                source_references=[_matrix_source_ref(value) for value in item.get("member_source_refs") or []],
            ))
        if values:
            return values
        for point in _outline_points(bundle.response_outline):
            marker = str(point.get("mandatory_marker") or "")
            if not marker:
                continue
            values.append(AnalysisPriorityItemDTO(
                trace_id=str(point.get("point_id") or ""),
                title=str(point.get("original_heading") or "带标记的招标要求"),
                description=_short(str(point.get("original_text") or ""), 240),
                action_kind="write_response",
                action_label=f"包含 {marker} 标记，建议优先核对",
                priority=None,
                requirement_ids=[str(point.get("point_id") or "")],
                source_references=[_outline_source(point)],
            ))
        return values

    def _pipeline_scoring(self, bundle: _AnalysisBundle) -> list[TenderScoringRuleDTO]:
        source_names = {str(item.get("file_id") or ""): str(item.get("display_name") or item.get("original_name") or item.get("file_name") or "") for item in bundle.sources}
        matrix_sections = list(bundle.score_matrix.get("sections") or [])
        missing = list(bundle.missing_materials.get("requests") or [])
        rules: list[TenderScoringRuleDTO] = []
        seen: set[str] = set()
        for chapter in bundle.context.get("chapters") or []:
            for raw in chapter.get("score_refs") or []:
                score_id = str(raw.get("score_item_id") or "")
                if not score_id or score_id in seen:
                    continue
                seen.add(score_id)
                score_range = raw.get("score_range") if isinstance(raw.get("score_range"), dict) else {}
                maximum = _number(score_range.get("max")) or _number(raw.get("score"))
                section = next((item for item in matrix_sections if str(item.get("score_item") or "") == str(raw.get("score_item_name") or "")), {})
                material_values: list[RequirementDependencyDTO] = []
                for request in missing:
                    if str(request.get("related_score_item") or "") != score_id:
                        continue
                    material_values.append(RequirementDependencyDTO(kind="evidence_requirement", title=str(request.get("target_material") or "待准备材料"), status=str(request.get("status") or "not_started"), status_label=_preparation_status(str(request.get("status") or "not_started")), description=_optional_text(request.get("why_needed")), required=True, trace_id=str(request.get("request_id") or "")))
                source = raw.get("source_location") if isinstance(raw.get("source_location"), dict) else {}
                file_id = str(source.get("source_file_id") or "")
                rules.append(TenderScoringRuleDTO(
                    score_id=score_id,
                    title=str(raw.get("score_item_name") or "评分项"),
                    criteria_text=str(raw.get("score_rule") or raw.get("raw_score_text") or ""),
                    available_score=maximum or None,
                    score_text=str(raw.get("raw_score_text") or (f"{maximum:g} 分" if maximum else "")),
                    score_group=_optional_text(raw.get("source_template_name")),
                    preparation_text=_optional_text(section.get("max_score_preparation_target")),
                    response_suggestions=[str(value) for value in section.get("writing_response_points") or []],
                    requirement_ids=[str(value) for value in chapter.get("requirement_ids") or []],
                    fact_needs=[],
                    material_needs=material_values,
                    source_reference=SourceReferenceDTO(file_name=source_names.get(file_id) or None, clause_path=_clause_parts(source.get("clause_path")), page_start=None, page_end=None, table_name=None, table_range=None, row_or_cell=None, source_kind="paragraph"),
                ))
        return rules

    def _outline_scoring(self, bundle: _AnalysisBundle) -> list[TenderScoringRuleDTO]:
        point = next((item for item in _outline_points(bundle.response_outline) if item.get("source_category") == "technical_score" and isinstance(item.get("table"), dict)), None)
        if not point:
            return []
        source = _outline_source(point)
        rules: list[TenderScoringRuleDTO] = []
        group: str | None = None
        title: str | None = None
        for row in (point.get("table") or {}).get("rows") or []:
            cells = {int(cell.get("column") or 0): str(cell.get("text") or "").strip() for cell in row.get("cells") or []}
            first, second, criteria, score_text = cells.get(1, ""), cells.get(2, ""), cells.get(3, ""), cells.get(4, "")
            if first and "分" in first and not criteria:
                group = first
                continue
            if second:
                title = second
            if not criteria or not _score_number(score_text):
                continue
            row_number = int(row.get("row") or len(rules) + 1)
            item_source = source.model_copy(update={"row_or_cell": f"row {row_number}"})
            rules.append(TenderScoringRuleDTO(
                score_id=f"SCORE-{point.get('point_id')}-R{row_number:03d}",
                title=title or f"评分子项 {row_number}",
                criteria_text=criteria,
                available_score=_score_number(score_text),
                score_text=score_text,
                score_group=group,
                preparation_text=None,
                response_suggestions=[],
                requirement_ids=[],
                fact_needs=[],
                material_needs=[],
                source_reference=item_source,
            ))
        return rules

    def _outline_parameter_tables(self, bundle: _AnalysisBundle) -> list[TenderParameterTableDTO]:
        values: list[TenderParameterTableDTO] = []
        for point in _outline_points(bundle.response_outline):
            if point.get("source_category") in {"technical_score", "technical_format"} or not isinstance(point.get("table"), dict):
                continue
            values.append(_parameter_table(str(point.get("point_id") or "TABLE"), str(point.get("original_heading") or "技术参数表"), point, _outline_source(point)))
        return values

    def _matrix_parameter_tables(self, bundle: _AnalysisBundle) -> list[TenderParameterTableDTO]:
        values: list[TenderParameterTableDTO] = []
        for item in bundle.requirement_matrix.get("requirements") or []:
            source = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            location = source.get("source_location") if isinstance(source.get("source_location"), dict) else {}
            raw = str(item.get("raw_text") or "")
            if location.get("table_index") is None and not location.get("sheet_name") and "\t" not in raw:
                continue
            pseudo = {"original_text": raw, "mandatory_marker": "", "table": item.get("table")}
            values.append(_parameter_table(str(item.get("requirement_id") or "TABLE"), str(item.get("title") or "技术参数表"), pseudo, _matrix_source_ref(source)))
        return values

    def _pipeline_preparation(self, bundle: _AnalysisBundle) -> TenderRisksPreparationDTO:
        facts: list[PreparationNeedDTO] = []
        confirmations: list[PreparationNeedDTO] = []
        materials: list[PreparationNeedDTO] = []
        responses: list[PreparationNeedDTO] = []
        risks: list[TenderRiskDTO] = []
        for item in bundle.context.get("global_manual_confirm_items") or []:
            need = PreparationNeedDTO(trace_id=str(item.get("confirm_id") or ""), kind="fact_confirmation", title=str(item.get("question") or "待确认信息"), description=str(item.get("reason") or item.get("question") or ""), status="confirmation_required", status_label="待人工确认", required=True, priority=_optional_text(item.get("priority")), requirement_ids=[], score_item_ids=[], source_references=[_matrix_source_ref(value) for value in item.get("source_refs") or []])
            confirmations.append(need)
        for item in bundle.context.get("global_missing_materials") or []:
            status = str(item.get("status") or "not_started")
            materials.append(PreparationNeedDTO(trace_id=str(item.get("missing_id") or ""), kind="material", title=str(item.get("title") or item.get("requested_material_type") or "待准备材料"), description=str(item.get("reason") or item.get("description") or ""), status=status, status_label=_preparation_status(status), required=True, priority=_optional_text(item.get("priority")), requirement_ids=[str(value) for value in item.get("related_requirement_ref_ids") or []], score_item_ids=[str(value) for value in item.get("related_score_item_ids") or []], source_references=[]))
        for item in bundle.actionable_index.get("action_groups") or []:
            action = str(item.get("primary_action_kind") or item.get("action_kind") or "")
            if action in {"no_direct_action", "comply_format"}:
                continue
            refs = [_matrix_source_ref(value) for value in item.get("member_source_refs") or []]
            if action in {"manual_confirm", "external_reference_resolve"}:
                risks.append(TenderRiskDTO(trace_id=str(item.get("action_group_id") or ""), category=action, category_label=ACTION_LABELS.get(action, "需要关注"), title=str(item.get("canonical_title") or "待确认要求"), description=str((item.get("canonical_requirement") or {}).get("text") or ""), action_priority=_optional_text(item.get("action_priority")), requirement_ids=[str(value) for value in item.get("member_requirement_ids") or []], source_references=refs))
            if action == "write_response":
                responses.append(PreparationNeedDTO(trace_id=str(item.get("action_group_id") or ""), kind="writing_response", title=str(item.get("canonical_title") or "待响应要求"), description=str((item.get("canonical_requirement") or {}).get("text") or ""), status="not_started", status_label="待进入写作准备", required=True, priority=_optional_text(item.get("action_priority")), requirement_ids=[str(value) for value in item.get("member_requirement_ids") or []], score_item_ids=[str(value) for value in item.get("related_score_item_ids") or []], source_references=refs))
        return TenderRisksPreparationDTO(project_id=bundle.project_id, lineage=bundle.lineage, risks=risks, facts=facts, materials=_dedupe_needs(materials), confirmations=_dedupe_needs(confirmations), writing_responses=_dedupe_needs(responses), blocking_semantics_available=False)

    def _outline_preparation(self, bundle: _AnalysisBundle) -> TenderRisksPreparationDTO:
        risks: list[TenderRiskDTO] = []
        responses: list[PreparationNeedDTO] = []
        for point in _outline_points(bundle.response_outline):
            if point.get("source_category") in {"technical_score", "technical_format"} or point.get("content_type") == "table":
                continue
            source = _outline_source(point)
            responses.append(PreparationNeedDTO(trace_id=str(point.get("point_id") or ""), kind="writing_response", title=str(point.get("original_heading") or "招标响应要求"), description=_short(str(point.get("original_text") or ""), 800), status="not_started", status_label="原始分析要求", required=bool(point.get("mandatory_marker")), priority=None, requirement_ids=[str(point.get("point_id") or "")], score_item_ids=[], source_references=[source]))
            marker = str(point.get("mandatory_marker") or "")
            if marker:
                risks.append(TenderRiskDTO(trace_id=str(point.get("point_id") or ""), category="marked_requirement", category_label="带标记要求", title=str(point.get("original_heading") or "带标记要求"), description=_short(str(point.get("original_text") or ""), 600), action_priority=None, requirement_ids=[str(point.get("point_id") or "")], source_references=[source]))
        return TenderRisksPreparationDTO(project_id=bundle.project_id, lineage=bundle.lineage, risks=risks, facts=[], materials=[], confirmations=[], writing_responses=responses, blocking_semantics_available=False)


def project_required_submission_items(
    requirement_matrix: dict[str, Any],
    actionable_index: dict[str, Any],
) -> list[RequiredSubmissionItemDTO]:
    """Project release reminders derived only from structured, applicable actions."""
    requirements = {
        str(item.get("requirement_id") or ""): item
        for item in requirement_matrix.get("requirements") or []
        if isinstance(item, dict) and item.get("requirement_id")
    }
    applicable_ids = {
        requirement_id
        for requirement_id, item in requirements.items()
        if (item.get("applicable_scope") or {}).get("applicable") is True
    }
    result: list[RequiredSubmissionItemDTO] = []
    seen: set[str] = set()

    for group in actionable_index.get("action_groups") or []:
        if not isinstance(group, dict):
            continue
        action = str(group.get("primary_action_kind") or group.get("action_kind") or "")
        member_ids = [str(value) for value in group.get("member_requirement_ids") or []]
        eligible = [value for value in member_ids if value in applicable_ids]
        if action != "fill_form" or not eligible:
            continue

        refs = [value for value in group.get("form_or_table_refs") or [] if isinstance(value, dict)]
        if not refs:
            refs = [{"table_id": "", "title": ""}]
        for ref in refs:
            table_id = str(ref.get("table_id") or "").strip()
            item_key = table_id or str(group.get("action_group_id") or "").strip()
            if not item_key or item_key in seen:
                continue
            title = _submission_title(ref, group, requirements, eligible)
            if not title:
                continue
            seen.add(item_key)
            external_upload = table_id == "special_tender_document"
            if external_upload:
                description = "请按招标文件要求在投标平台另行处理，当前技术标正文不会自动合并该材料。"
            elif table_id == "technical_deviation_table":
                description = "正式技术偏差表尚未自动生成，请在提交前完成。"
            else:
                description = "当前系统尚未自动形成该正式提交材料，请在提交前完成。"
            result.append(
                RequiredSubmissionItemDTO(
                    item_key=item_key,
                    display_title=title,
                    status="pending",
                    description=description,
                    submission_kind="external_upload" if external_upload else "form",
                )
            )
    return result


def _submission_title(
    ref: dict[str, Any],
    group: dict[str, Any],
    requirements: dict[str, dict[str, Any]],
    eligible_ids: list[str],
) -> str | None:
    table_id = str(ref.get("table_id") or "").strip()
    ref_title = str(ref.get("title") or "").strip()
    if ref_title and ref_title != table_id and not re.fullmatch(r"[a-z][a-z0-9_]*", ref_title):
        return ref_title
    for requirement_id in eligible_ids:
        title = str((requirements.get(requirement_id) or {}).get("title") or "").strip()
        if title and not re.fullmatch(r"(?:req|TR)-[A-Za-z0-9_-]+", title):
            return title
    title = str(group.get("canonical_title") or "").strip()
    if title and not re.fullmatch(r"[a-z][a-z0-9_]*", title):
        return title
    return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebProjectionError(f"analysis artifact is unavailable or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise WebProjectionError(f"analysis artifact must contain an object: {path.name}")
    return value


def _outline_points(outline: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for chapter in outline.get("chapters") or [] for item in chapter.get("response_points") or [] if isinstance(item, dict)]


def _outline_source(item: dict[str, Any]) -> SourceReferenceDTO:
    raw = next((value for value in item.get("source_refs") or [] if isinstance(value, dict)), {})
    return SourceReferenceDTO(file_name=_optional_text(raw.get("file_name")), clause_path=[str(value) for value in raw.get("clause_path") or []], page_start=_optional_int(raw.get("page_start")), page_end=_optional_int(raw.get("page_end")), table_name=_optional_text(raw.get("table_name")), table_range=_optional_text(raw.get("table_range")), row_or_cell=_optional_text(raw.get("row_or_cell")), source_kind="table" if raw.get("table_name") or raw.get("table_range") else "paragraph")


def _matrix_source_ref(raw: dict[str, Any]) -> SourceReferenceDTO:
    location = raw.get("source_location") if isinstance(raw.get("source_location"), dict) else {}
    row = location.get("row_index")
    return SourceReferenceDTO(file_name=_optional_text(raw.get("source_file_name")), clause_path=_clause_parts(raw.get("clause_path")), page_start=_optional_int(raw.get("page_start")), page_end=_optional_int(raw.get("page_end")), table_name=_optional_text(location.get("sheet_name") or raw.get("table_name")), table_range=_optional_text(raw.get("table_range")), row_or_cell=f"row {row}" if row is not None else _optional_text(raw.get("row_or_cell")), source_kind="table" if location.get("table_index") is not None or location.get("sheet_name") or raw.get("table_name") else "paragraph")


def _parameter_table(table_id: str, title: str, point: dict[str, Any], source: SourceReferenceDTO) -> TenderParameterTableDTO:
    table = point.get("table") if isinstance(point.get("table"), dict) else {}
    rows: list[list[TenderTableCellDTO]] = []
    for index, row in enumerate(table.get("rows") or []):
        cells = [TenderTableCellDTO(text=str(cell.get("text") or ""), row=int(cell.get("row") or row.get("row") or index + 1), column=int(cell.get("column") or position + 1), rowspan=max(1, int(cell.get("rowspan") or 1)), colspan=max(1, int(cell.get("colspan") or 1))) for position, cell in enumerate(row.get("cells") or []) if isinstance(cell, dict)]
        if cells:
            rows.append(cells)
    raw_text = str(point.get("original_text") or "")
    if not rows and raw_text:
        for row_index, line in enumerate(raw_text.splitlines(), start=1):
            values = line.split("\t")
            if len(values) < 2:
                continue
            rows.append([TenderTableCellDTO(text=value, row=row_index, column=index + 1) for index, value in enumerate(values)])
    column_count = int(table.get("column_count") or max((max((cell.column for cell in row), default=0) for row in rows), default=0))
    return TenderParameterTableDTO(table_id=table_id, title=title, requirement_id=table_id, column_count=column_count, row_count=len(rows), rows=rows, raw_text=None if rows else raw_text, mandatory_marker=_optional_text(point.get("mandatory_marker")), source_reference=source)


def _score_number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_value(value: Any) -> str | None:
    text = _optional_text(value)
    if not text or any(marker in text for marker in ("未定位", "需人工确认")):
        return None
    return text


def _clause_parts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"\s*>\s*", str(value or "")) if item.strip()]


def _short(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _preparation_status(status: str) -> str:
    return {"query_not_executed": "尚未查询", "candidate": "已有候选材料", "verified": "已准备", "confirmation_required": "待人工确认", "not_started": "待准备"}.get(status, "待准备")


def _dedupe_needs(values: list[PreparationNeedDTO]) -> list[PreparationNeedDTO]:
    result: list[PreparationNeedDTO] = []
    seen: set[str] = set()
    for item in values:
        key = item.trace_id or f"{item.kind}:{item.title}"
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

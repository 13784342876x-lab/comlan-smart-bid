from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.writing_context_compiler import (
    build_writing_context,
    compile_writing_artifacts,
    inspect_writing_context,
    render_agent_outline_markdown,
    update_context_writing_index,
)


LOT_NAME = "0425139003001-6582"
TECHNICAL_SCORES = [
    ("technical_01_应答文件技术部分总体评价", "应答文件技术部分总体评价"),
    ("technical_02_项目负责人情况", "项目负责人情况"),
    ("technical_03_项目团队人员结构", "项目团队人员结构"),
    ("technical_04_业绩情况", "业绩情况"),
    ("technical_05_服务方案", "服务方案"),
    ("technical_06_工作进度及保证措施", "工作进度及保证措施"),
    ("technical_07_工作基础", "工作基础"),
    ("technical_08_专利数量", "专利数量"),
    ("technical_09_绩效评价", "绩效评价"),
]


def requirement(index: int, score_id: str, *, fatal: bool = False, negative: bool = False) -> dict:
    raw = "本条并不否决投标。" if negative else ("未按要求签字盖章的，否决其投标。" if fatal else f"{score_id}评分响应要求。")
    return {
        "requirement_id": f"req-{index}",
        "title": f"要求{index}",
        "requirement_type": "potential_invalidity_risk" if fatal else "score_related_requirement",
        "raw_text": raw,
        "normalized_requirement": {"text": raw, "source_type": "system_judgement"},
        "required_action": {"text": "逐项响应", "source_type": "suggested_writing"},
        "source_ref": {"source_file_id": "tender-file", "source_file_name": "招标文件.docx", "excerpt": raw},
        "applicable_scope": {"scope_type": "technical_response", "applicable": True},
        "compliance_level": "fatal_if_missing" if fatal else "score_related",
        "risk_type": "signature_seal_risk" if fatal else "scoring_loss_risk",
        "related_score_item_ids": [score_id],
        "related_context_chapter_ids": [chapter_id(index if index <= 9 else 1)],
        "source_type": "tender_original_fact",
        "polarity": "negative_exception" if negative else ("conditional_requirement" if fatal else "positive_requirement"),
    }


def chapter_id(index: int) -> str:
    score_id, title = TECHNICAL_SCORES[index - 1]
    return f"chapter-{index}-{score_id}-{title}"


def evidence(evidence_id: str, evidence_type: str, title: str, *, source_name: str = "材料.docx", excerpt: str = "材料摘录") -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "title": title,
        "source_file_id": f"file-{evidence_id}",
        "source_file_name": source_name,
        "source_location": {"clause_path": "测试材料"},
        "location_precision": "clause_approximate",
        "excerpt": excerpt,
        "visual_source_ref": None,
        "verification_status": "unverified",
        "decision_ref_ids": [],
        "source_type": "enterprise_existing_fact",
    }


def relation(support_id: str, evidence_id: str, req_id: str, score_id: str, status: str) -> dict:
    return {
        "support_id": support_id,
        "evidence_requirement_id": req_id,
        "evidence_id": evidence_id,
        "matched_score_item_ids": [score_id],
        "matched_action_group_ids": [f"action-{score_id}"],
        "matched_requirement_ids": [f"source-{req_id}"],
        "support_strength": "strong" if status != "unusable" else "none",
        "usability_status": status,
        "quantity_eligible": False,
        "permitted_claims": [],
        "restricted_claims": ["待确认前不得作为企业事实。"],
        "rejection_reasons": ["材料类型错误。"] if status == "unusable" else [],
        "decision_ref_ids": [],
        "verification_status": "unverified",
    }


def fixture_inputs() -> dict:
    chapters = []
    project_scores = []
    requirements = []
    actions = []
    evidence_requirements = []
    for index, (score_id, title) in enumerate(TECHNICAL_SCORES, start=1):
        chapters.append(
            {
                "chapter_id": chapter_id(index),
                "chapter_order": index,
                "chapter_title": title,
                "score_refs": [{"score_item_id": score_id, "score_item_name": title, "score": 10, "score_range": {"max": 10}, "score_rule": f"{title}评分规则", "source_location": {"source_file_id": "score-file"}}],
                "technical_spec_refs": [{"ref_id": f"spec-{index}", "excerpt": f"{title}技术要求", "source_type": "tender_original_fact"}],
            }
        )
        project_scores.append({"id": score_id, "item_name": title, "score": 10, "score_range": {"max": 10}, "score_rule": f"{title}评分规则"})
        requirements.append(requirement(index, score_id))
        actions.append(
            {
                "action_group_id": f"action-{score_id}",
                "member_requirement_ids": [f"req-{index}"],
                "canonical_title": title,
                "action_kind": "write_response",
                "secondary_action_kinds": [],
                "action_priority": "high",
                "response_content_needed": [f"响应{title}"],
                "related_score_item_ids": [score_id],
                "related_context_chapter_ids": [chapter_id(index)],
                "applicable_scope": {"scope_type": "technical_response"},
            }
        )
    requirements.extend(
        [
            requirement(10, TECHNICAL_SCORES[0][0], fatal=True),
            requirement(11, TECHNICAL_SCORES[0][0], fatal=True, negative=True),
        ]
    )
    evidence_score_map = {
        2: "person-req",
        3: "team-req",
        4: "performance-req",
        7: "foundation-req",
        8: "patent-req",
        9: "evaluation-req",
    }
    for index, req_id in evidence_score_map.items():
        score_id = TECHNICAL_SCORES[index - 1][0]
        evidence_requirements.append(
            {
                "evidence_requirement_id": req_id,
                "canonical_title": TECHNICAL_SCORES[index - 1][1],
                "source_score_item_ids": [score_id],
                "source_action_group_ids": [f"action-{score_id}"],
                "source_requirement_ids": [f"req-{index}"],
                "related_context_chapter_ids": [chapter_id(index)],
            }
        )
    catalog_items = [
        evidence("person", "personnel_resume", "张三人员简历"),
        evidence("management", "management_system_certificate", "ISO 9001质量管理体系认证证书"),
        evidence("mock-performance", "company_performance_summary", "mock业绩", source_name="mock_业绩文件.docx"),
        evidence("bad-patent", "personnel_certificate", "错误类型材料"),
    ]
    support_relations = [
        relation("support-person", "person", "person-req", TECHNICAL_SCORES[1][0], "needs_review"),
        relation("support-team", "person", "team-req", TECHNICAL_SCORES[2][0], "candidate"),
        relation("support-management", "management", "team-req", "business_05_绿色管理体系认证", "candidate"),
        relation("support-mock", "mock-performance", "performance-req", TECHNICAL_SCORES[3][0], "unusable"),
        relation("support-patent", "bad-patent", "patent-req", TECHNICAL_SCORES[7][0], "unusable"),
    ]
    score_coverage = [
        {"score_item_id": score_id, "required_count": (5 if index == 4 else 2 if index == 8 else None), "confirmed_count": 0, "candidate_count": 0, "remaining_confirmed_gap": (5 if index == 4 else 2 if index == 8 else None)}
        for index, (score_id, _) in enumerate(TECHNICAL_SCORES, start=1)
    ]
    score_coverage.append({"score_item_id": "business_10_研发团队规模", "required_count": 30, "confirmed_count": 0, "candidate_count": 2, "remaining_confirmed_gap": 30})
    queue = {
        "schema_version": "1.0",
        "lot_name": LOT_NAME,
        "resolution_cases": [
            {
                "resolution_id": "resolution-person",
                "resolution_subject_type": "person_department",
                "canonical_question": "确认张三部门、角色和投入状态。",
                "current_status": "pending",
                "blocking_level": "blocks_confirmed_evidence",
                "related_evidence_ids": ["person"],
                "related_evidence_requirement_ids": ["person-req", "team-req"],
                "related_context_chapter_ids": [chapter_id(2), chapter_id(3)],
            }
        ],
    }
    intake = {
        "schema_version": "1.0",
        "lot_name": LOT_NAME,
        "material_intake_packages": [
            {"intake_package_id": "intake-performance", "title": "企业业绩完整证据包", "related_evidence_requirement_ids": ["performance-req"], "related_score_item_ids": [TECHNICAL_SCORES[3][0]], "requested_count": 5, "confirmed_count": 0, "remaining_count": 5, "requested_material_components": ["合同关键页", "验收证明"], "intake_status": "not_requested"},
            {"intake_package_id": "intake-patent", "title": "专利证据包", "related_evidence_requirement_ids": ["patent-req"], "related_score_item_ids": [TECHNICAL_SCORES[7][0]], "requested_count": 2, "confirmed_count": 0, "remaining_count": 2, "requested_material_components": ["专利证书"], "intake_status": "not_requested"},
        ],
    }
    return {
        "context": {
            "schema_version": "1.0",
            "lot_name": LOT_NAME,
            "project": {"lot_name": LOT_NAME, "package_name": "当前选中包名", "source_type": "tender_original_fact"},
            "project_conflicts": [{"field": "package_name", "selected_value": "当前选中包名", "candidate_values": ["当前选中包名", "技术规范候选名"], "status": "needs_human_confirmation"}],
            "chapters": chapters,
        },
        "requirement_matrix": {"schema_version": "1.0", "lot_name": LOT_NAME, "requirements": requirements},
        "action_index": {"schema_version": "1.0", "lot_name": LOT_NAME, "action_groups": actions},
        "catalog": {"schema_version": "1.0", "lot_name": LOT_NAME, "evidence_items": catalog_items},
        "coverage": {"schema_version": "1.0", "lot_name": LOT_NAME, "evidence_requirements": evidence_requirements, "support_relations": support_relations, "score_item_coverage": score_coverage},
        "resolution_queue": queue,
        "intake_plan": intake,
        "project": {"lot_name": LOT_NAME, "score_items": project_scores},
    }


def compiled() -> dict:
    values = fixture_inputs()
    return compile_writing_artifacts(LOT_NAME, **values)


def task_for(result: dict, score_id: str) -> dict:
    return next(item for item in result["chapter_task_packets"]["chapter_tasks"] if score_id in item["input_context"]["score_item_ids"])


def test_confirmed_zero_still_creates_proposal_tasks() -> None:
    result = compiled()
    tasks = [task_for(result, score_id) for score_id in [TECHNICAL_SCORES[0][0], TECHNICAL_SCORES[4][0], TECHNICAL_SCORES[5][0]]]
    assert all(item["task_type"] == "proposal_drafting" for item in tasks)
    assert all(item["blocking_status"] in {"ready", "ready_with_placeholders"} for item in tasks)


def test_performance_chapter_has_no_enterprise_fact_without_confirmed_evidence() -> None:
    task = task_for(compiled(), TECHNICAL_SCORES[3][0])
    assert task["confirmed_facts"] == []
    assert task["blocking_status"] == "blocked_by_evidence"


def test_candidate_and_needs_review_never_enter_confirmed_facts() -> None:
    result = compiled()
    assert task_for(result, TECHNICAL_SCORES[1][0])["confirmed_facts"] == []
    assert task_for(result, TECHNICAL_SCORES[2][0])["confirmed_facts"] == []


def test_rejected_unusable_and_mock_evidence_are_prohibited() -> None:
    usage = {item["evidence_id"]: item for item in compiled()["evidence_use_map"]["evidence_usage_items"]}
    assert usage["mock-performance"]["allowed_usage"] == "prohibited"
    assert usage["bad-patent"]["allowed_usage"] == "prohibited"


def test_unconfirmed_person_is_not_an_enterprise_fact_but_role_plan_is_allowed() -> None:
    task = task_for(compiled(), TECHNICAL_SCORES[1][0])
    assert task["confirmed_facts"] == []
    assert "岗位职责" in task["proposal_content_allowed"]
    assert "能力要求" in task["proposal_content_allowed"]


def test_service_plan_is_complete_proposal_task_without_enterprise_evidence() -> None:
    task = task_for(compiled(), TECHNICAL_SCORES[4][0])
    assert task["task_type"] == "proposal_drafting"
    assert {"服务组织", "响应机制", "质量控制", "风险处置"}.issubset(task["proposal_content_allowed"])


def test_performance_gap_and_patent_gap_create_explicit_placeholders() -> None:
    result = compiled()
    performance = json.dumps(task_for(result, TECHNICAL_SCORES[3][0])["placeholders"], ensure_ascii=False)
    patent = json.dumps(task_for(result, TECHNICAL_SCORES[7][0])["placeholders"], ensure_ascii=False)
    assert "5项符合评分规则的企业业绩证据包" in performance
    assert "2项与采购主题相关的有效专利证明" in patent


def test_management_certificate_is_only_cautious_reference() -> None:
    usage = next(item for item in compiled()["evidence_use_map"]["evidence_usage_items"] if item["evidence_id"] == "management")
    assert usage["allowed_usage"] == "cautious_reference"
    assert usage["allowed_claims"] == []


def test_research_team_requirement_is_not_claimed_satisfied_by_candidates() -> None:
    text = json.dumps(compiled(), ensure_ascii=False)
    assert "研发团队已满足30人" not in text
    assert "confirmed_count\": 2" not in text


def test_every_chapter_preserves_score_and_requirement_ids() -> None:
    tasks = compiled()["chapter_task_packets"]["chapter_tasks"]
    assert all(item["input_context"]["score_item_ids"] for item in tasks)
    assert all(item["input_context"]["requirement_ids"] for item in tasks)


def test_fatal_guardrail_includes_explicit_rule_and_excludes_negative_exception() -> None:
    fatal = compiled()["draft_guardrails"]["fatal_risk_items"]
    assert any("未按要求签字盖章" in item["raw_text"] for item in fatal)
    assert all("并不否决" not in item["raw_text"] for item in fatal)


def test_context_update_only_adds_paths_and_chapter_indexes(tmp_path: Path) -> None:
    context = fixture_inputs()["context"]
    before = deepcopy(context)
    tasks = compiled()["chapter_task_packets"]
    paths = {
        "writing_context_pack": tmp_path / "writing_context_pack.json",
        "technical_response_outline_for_agent": tmp_path / "outline.json",
        "evidence_use_map": tmp_path / "evidence.json",
        "draft_guardrails": tmp_path / "guardrails.json",
        "chapter_task_packets": tmp_path / "tasks.json",
    }
    update_context_writing_index(context, tasks, paths)
    assert context["project"] == before["project"]
    assert context["project_conflicts"] == before["project_conflicts"]
    assert set(context) - set(before) == {"writing_context_pack_path", "agent_outline_path", "evidence_use_map_path", "draft_guardrails_path", "chapter_task_packets_path"}
    assert all(set(item) - set(before["chapters"][index]) == {"chapter_task_ids", "chapter_blocking_status"} for index, item in enumerate(context["chapters"]))


def test_markdown_is_display_only_and_no_formal_body_is_generated() -> None:
    result = compiled()
    markdown = render_agent_outline_markdown(result["technical_response_outline_for_agent"], result["chapter_task_packets"])
    assert "不参与下游计算" in markdown
    assert all(item["output_contract"]["formal_body_generation_allowed"] is False for item in result["chapter_task_packets"]["chapter_tasks"])
    generated_from = json.dumps({key: value.get("generated_from") for key, value in result.items()}, ensure_ascii=False)
    assert ".md" not in generated_from


def test_no_decision_conclusion_or_invented_enterprise_fact() -> None:
    result = compiled()
    text = json.dumps(result, ensure_ascii=False)
    assert "建议投标" not in text
    assert "不建议投标" not in text
    assert all(not item["confirmed_facts"] for item in result["chapter_task_packets"]["chapter_tasks"])


def test_build_writes_six_artifacts_from_json_and_inspect_does_not_need_markdown(tmp_path: Path, monkeypatch) -> None:
    values = fixture_inputs()
    lot_dir = tmp_path / LOT_NAME
    files = {
        lot_dir / "outline" / "technical_response_context.json": values["context"],
        lot_dir / "requirements" / "tender_requirement_matrix.json": values["requirement_matrix"],
        lot_dir / "requirements" / "actionable_requirement_index.json": values["action_index"],
        lot_dir / "evidence" / "enterprise_evidence_catalog.json": values["catalog"],
        lot_dir / "evidence" / "evidence_coverage_matrix.json": values["coverage"],
        lot_dir / "evidence" / "evidence_resolution_queue.json": values["resolution_queue"],
        lot_dir / "evidence" / "material_intake_plan.json": values["intake_plan"],
    }
    for path, payload in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("app.writing_context_compiler.load_tender_projects", lambda: {"projects": [values["project"]]})

    built = build_writing_context(LOT_NAME, output_dir=tmp_path)

    assert len(built["files"]) == 6
    assert all(Path(path).exists() for path in built["files"].values())
    markdown = Path(built["files"]["technical_response_outline_for_agent_markdown"])
    markdown.unlink()
    inspected = inspect_writing_context(LOT_NAME, output_dir=tmp_path)
    assert inspected["statistics"]["chapter_count"] == 9

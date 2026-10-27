from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.stage9_input_handoff import Stage9InputLineageError, Stage9InputResolver, Stage9InputSource
from app.technical_writing_agent import (
    TechnicalWritingAgentInput,
    build_technical_writing_agent,
    validate_technical_chapter,
)
from app.web.api import create_app
from app.web.query_service import WebProjectionError
from app.web_search_provider import NotConfiguredSearchProvider
from test_human_confirmation_application_service import _attach_confirmation_requests, _command
from test_web_tender_analysis import _analysis_project


class _NoModelClient:
    def run_with_tools(self, **_: object) -> object:  # pragma: no cover - safety tripwire
        raise AssertionError("Stage9 handoff tests must not call a model")


def _client_with_plan(tmp_path: Path, *, extended: bool = False) -> tuple[TestClient, str]:
    project_id, _ = _analysis_project(tmp_path)
    _attach_confirmation_requests(tmp_path, project_id, with_plan=True)
    if extended:
        context_path = tmp_path / "technical_response_context.json"
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context["global_manual_confirm_items"].extend(
            [
                {
                    "confirm_id": "HC-DEPLOY",
                    "category": "deployment_strategy",
                    "question": "本项目采用哪种部署方案？",
                    "reason": "该选择限定部署方案章节。",
                    "answer_type": "selection",
                    "candidate_values": ["方案A", "方案B", "方案C"],
                    "writing_effect": "writing_constraint",
                    "target_task_ids": ["TASK-2"],
                    "priority": "high",
                    "source_refs": [],
                },
                {
                    "confirm_id": "HC-STRATEGY",
                    "category": "implementation_strategy",
                    "question": "请确认本项目实施策略。",
                    "reason": "该指令限定技术方案写法。",
                    "answer_type": "text",
                    "writing_effect": "writing_constraint",
                    "target_task_ids": ["TASK-3"],
                    "priority": "medium",
                    "source_refs": [],
                },
            ]
        )
        context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
    return TestClient(create_app(tmp_path)), project_id


def _decide(client: TestClient, project_id: str, confirmation_id: str, answer: bool | str) -> None:
    current = client.get(f"/api/projects/{project_id}/confirmations")
    result = client.patch(
        f"/api/projects/{project_id}/confirmations/{confirmation_id}",
        headers={"If-Match": current.headers["etag"]},
        json=_command(answer),
    )
    assert result.status_code == 200, result.text


def _handoff(client: TestClient, project_id: str) -> dict:
    return client.app.state.writing_plan_service.stage9_input_context(project_id)


def _task(handoff: dict, task_id: str) -> dict:
    return next(item for item in handoff["tasks"] if item["task_id"] == task_id)


def test_pending_confirmation_is_task_scoped_and_registered_as_stable_placeholder(tmp_path: Path) -> None:
    client, project_id = _client_with_plan(tmp_path)
    first = _handoff(client, project_id)
    second = _handoff(client, project_id)
    assert first == second
    assert first["fingerprint"] == second["fingerprint"]
    task_1 = _task(first, "TASK-1")
    task_2 = _task(first, "TASK-2")
    assert task_1["generation_policy"] == "allow_with_placeholders"
    assert task_1["pending_confirmations"][0]["confirmation_id"] == "HC-ONSITE"
    assert task_1["placeholder_requirements"][0]["source_kind"] == "human_confirmation"
    assert task_2["pending_confirmations"] == []

    source = client.app.state.writing_plan_service._source(project_id)
    agent_input = TechnicalWritingAgentInput(
        response_outline_path=source.response_path,
        writing_outline_path=source.writing_path,
        task_information_path=source.information_path,
        writing_constraint_context=first,
        output_key="stage9-handoff-test",
        target_section_id="WC-005-04",
    )
    agent = build_technical_writing_agent(
        agent_input,
        llm=_NoModelClient(),
        output_dir=tmp_path,
        disable_web=True,
        web_provider=NotConfiguredSearchProvider(),
    )
    resolved = agent.tool_context.get_resolved_task_context({})
    assert resolved["status"] == "success"
    assert resolved["input_fingerprint"] == first["fingerprint"]
    task_information = agent.tool_context.get_task_information({})
    assert task_information["tasks"][0]["resolved_context"]["task_id"] == "TASK-1"
    assert any(
        item.get("source_id") == "HC-ONSITE" and item.get("blocking") is True
        for item in agent.tool_context.pending_confirmations
    )
    validation = validate_technical_chapter(
        {
            "chapter_title": "服务机构与人员",
            "sections": [
                {
                    "section_id": "WC-005-04",
                    "heading": "服务机构与人员",
                    "block_type": "other",
                    "content": "业务选择尚待确认。",
                    "covered_score_item_ids": [],
                    "covered_requirement_ids": ["TR-003"],
                    "placeholder_ids": [],
                }
            ],
            "claims": [],
            "placeholders_used": [],
            "score_items_covered": [],
            "requirements_covered": ["TR-003"],
            "warnings": [],
        },
        schema=agent.chapter_schema,
        target=agent.tool_context._target,
        confirmed_facts={},
        pending_confirmations=agent.tool_context.pending_confirmations,
    )
    placeholder_id = task_1["placeholder_requirements"][0]["placeholder_id"]
    assert any(placeholder_id in error for error in validation["errors"])

    readiness = client.get(f"/api/projects/{project_id}/writing-readiness")
    assert readiness.status_code == 200, readiness.text
    ready = readiness.json()
    assert ready["stage9_contract_ready"] is True
    assert ready["human_confirmations_connected"] is True
    assert ready["pending_policy"] == "placeholder_required"
    assert ready["placeholder_task_count"] == 1
    assert str(tmp_path) not in readiness.text


def test_unresolved_pending_scope_is_conservatively_applied_but_confirmed_scope_is_rejected(
    tmp_path: Path,
) -> None:
    client, project_id = _client_with_plan(tmp_path)
    context_path = tmp_path / "technical_response_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["global_manual_confirm_items"][0]["target_task_ids"] = []
    context["global_manual_confirm_items"][0]["related_requirement_ref_ids"] = [
        "ANALYSIS-ONLY-REF"
    ]
    context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")

    pending = _handoff(client, project_id)
    assert all(
        any(
            item["confirmation_id"] == "HC-ONSITE"
            for item in task["pending_confirmations"]
        )
        for task in pending["tasks"]
    )
    assert all(task["generation_policy"] == "allow_with_placeholders" for task in pending["tasks"])

    _decide(client, project_id, "HC-ONSITE", True)
    with pytest.raises(
        WebProjectionError,
        match="Human Confirmation cannot be resolved to the current Writing Plan",
    ):
        _handoff(client, project_id)


def test_confirmed_true_and_false_are_explicit_non_fact_constraints(tmp_path: Path) -> None:
    client, project_id = _client_with_plan(tmp_path)
    _decide(client, project_id, "HC-ONSITE", True)
    positive = _task(_handoff(client, project_id), "TASK-1")["writing_constraints"][0]
    assert positive["answer"] is True
    assert positive["constraint_semantic"] == "positive_constraint"
    facts = client.app.state.writing_preparation_service.fact_service.snapshot(project_id).state
    assert "HC-ONSITE" not in json.dumps(facts, ensure_ascii=False)

    current = client.get(f"/api/projects/{project_id}/confirmations")
    changed = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": current.headers["etag"]},
        json=_command(False, change_reason="本项目仅提供远程支持"),
    )
    assert changed.status_code == 200, changed.text
    negative = _task(_handoff(client, project_id), "TASK-1")["writing_constraints"][0]
    assert negative["answer"] is False
    assert negative["constraint_semantic"] == "negative_constraint"
    assert _task(_handoff(client, project_id), "TASK-2")["writing_constraints"] == []


def test_selection_and_text_pass_only_confirmed_answers_to_their_tasks(tmp_path: Path) -> None:
    client, project_id = _client_with_plan(tmp_path, extended=True)
    _decide(client, project_id, "HC-DEPLOY", "方案B")
    _decide(client, project_id, "HC-STRATEGY", "先核心系统迁移，再分批切换业务节点")
    handoff = _handoff(client, project_id)
    selected = _task(handoff, "TASK-2")["writing_constraints"][0]
    instruction = _task(handoff, "TASK-3")["writing_constraints"][0]
    assert selected["constraint_semantic"] == "selected_option"
    assert selected["answer"] == "方案B"
    assert "candidate_values" not in selected
    assert instruction["constraint_semantic"] == "free_text_business_instruction"
    assert instruction["answer"] == "先核心系统迁移，再分批切换业务节点"
    assert _task(handoff, "TASK-4")["writing_constraints"] == []


def test_project_facts_and_unqueried_material_keep_their_existing_channels(tmp_path: Path) -> None:
    client, project_id = _client_with_plan(tmp_path)
    preparation = client.get(f"/api/projects/{project_id}/writing-preparation")
    saved = client.patch(
        f"/api/projects/{project_id}/writing-preparation/facts/FACT-013",
        headers={"If-Match": preparation.headers["etag"]},
        json={
            "status": "confirmed",
            "value": {
                "address": "天津市滨海新区",
                "contact": "022-12345678",
                "technician_count": 12,
            },
            "source_description": "制造商书面确认",
            "confirmation_basis": "项目负责人核验",
            "note": None,
            "change_reason": None,
        },
    )
    assert saved.status_code == 200, saved.text
    handoff = _handoff(client, project_id)
    service_task = _task(handoff, "TASK-1")
    fact = service_task["confirmed_project_facts"][0]
    assert fact["fact_id"] == "FACT-013"
    assert fact["fact_key"] == "manufacturer_service_network"
    assert fact["value"]["technician_count"] == 12
    assert "HC-ONSITE" not in json.dumps(service_task["confirmed_project_facts"], ensure_ascii=False)

    material = _task(handoff, "TASK-2")["evidence_dependencies"][0]
    assert material["status"] == "query_not_executed"
    assert material["available_as_verified_evidence"] is False
    assert _task(handoff, "TASK-2")["available_evidence"] == []


def test_changed_plan_rejects_old_fact_and_confirmation_lineage(tmp_path: Path) -> None:
    client, project_id = _client_with_plan(tmp_path)
    service = client.app.state.writing_plan_service
    source = service._source(project_id)
    facts = client.app.state.writing_preparation_service.fact_service.snapshot(project_id).state
    confirmations = client.app.state.writing_preparation_service.confirmation_service.snapshot(project_id).state
    task_plan = json.loads(source.tasks_path.read_text(encoding="utf-8"))
    task_plan["tasks"][0]["writing_objective"] = "Plan B"
    source.tasks_path.write_text(json.dumps(task_plan, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(Stage9InputLineageError):
        Stage9InputResolver().resolve(
            Stage9InputSource(
                project_id=project_id,
                analysis_run_id=source.analysis_run_id,
                response_outline_path=source.response_path,
                writing_outline_path=source.writing_path,
                writing_tasks_path=source.tasks_path,
                task_information_path=source.information_path,
                confirmed_project_facts=facts,
                human_confirmation_state=confirmations,
            )
        )

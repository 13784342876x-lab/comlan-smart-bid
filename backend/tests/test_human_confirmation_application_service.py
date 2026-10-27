from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.technical_writing_tools import TechnicalWritingToolContext
from app.web.api import create_app
from test_web_tender_analysis import (
    _analysis_project,
    _attach_writing_preparation_artifacts,
)
from test_web_writing_plan import _attach_plan


def _attach_confirmation_requests(
    tmp_path: Path, project_id: str, *, with_plan: bool = False
) -> None:
    if with_plan:
        _attach_plan(tmp_path, project_id)
    else:
        _attach_writing_preparation_artifacts(tmp_path, project_id)
    context_path = tmp_path / "technical_response_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["global_manual_confirm_items"] = [
        {
            "confirm_id": "HC-ONSITE",
            "category": "service_strategy",
            "question": "本项目是否采用驻场服务方案？",
            "reason": "该业务选择影响售后服务章节写作约束。",
            "answer_type": "boolean",
            "writing_effect": "writing_constraint",
            "target_task_ids": ["TASK-1"],
            "related_requirement_ref_ids": ["TR-003"],
            "priority": "high",
            "source_refs": [],
        },
        {
            "confirm_id": "HC-FACT-013",
            "category": "project_fact_value",
            "question": "请确认制造商服务机构信息。",
            "reason": "本质上是项目事实补充。",
            "related_field": "manufacturer_service_network",
            "priority": "high",
            "source_refs": [],
        },
        {
            "confirm_id": "HC-MATERIAL",
            "category": "chapter_review",
            "question": "是否已经取得厂家服务证明？",
            "reason": "存在材料缺口，需补充正式材料后再进入正文撰写。",
            "priority": "medium",
            "source_refs": [],
        },
    ]
    context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")


def _command(answer: bool | str = True, *, change_reason: str | None = None) -> dict:
    return {
        "answer": answer,
        "source_description": "项目服务方案会签",
        "confirmation_basis": "项目负责人书面确认",
        "note": "用于写作约束",
        "change_reason": change_reason,
    }


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    project_id, _ = _analysis_project(tmp_path)
    _attach_confirmation_requests(tmp_path, project_id)
    return TestClient(create_app(tmp_path)), project_id


def test_confirmation_requests_keep_fact_business_and_material_boundaries(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    response = client.get(f"/api/projects/{project_id}/confirmations")
    assert response.status_code == 200, response.text
    assert response.headers["etag"]
    payload = response.json()
    values = {item["confirmation_id"]: item for item in payload["confirmations"]}
    assert values["HC-ONSITE"]["request_kind"] == "business_choice"
    assert values["HC-ONSITE"]["can_decide"] is True
    assert values["HC-FACT-013"]["request_kind"] == "project_fact"
    assert values["HC-FACT-013"]["linked_fact_id"] == "FACT-013"
    assert values["HC-FACT-013"]["can_decide"] is False
    assert values["HC-MATERIAL"]["request_kind"] == "material_availability"
    preparation = client.get(f"/api/projects/{project_id}/writing-preparation").json()
    assert sum(item["item_id"] == "FACT-013" for item in preparation["items"]) == 1
    assert not any(item["item_id"] == "HC-FACT-013" for item in preparation["items"])


def test_business_choice_persists_history_and_updates_context_without_becoming_fact(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    initial = client.get(f"/api/projects/{project_id}/writing-preparation")
    token_a = initial.headers["etag"]
    missing = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE", json=_command()
    )
    assert missing.status_code == 428
    saved = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": token_a},
        json=_command(),
    )
    assert saved.status_code == 200, saved.text
    assert saved.headers["etag"] != token_a
    result = saved.json()
    assert result["confirmation"]["status"] == "confirmed"
    assert result["confirmation"]["answer"] is True
    assert result["confirmation"]["stage9_consumable"] is True
    item = next(
        value
        for value in result["writing_preparation"]["items"]
        if value["item_id"] == "HC-ONSITE"
    )
    assert item["status"] == "confirmed"
    assert item["writing_context"]["stage9_policy"] == "apply_constraint"
    assert item["writing_context"]["stage9_consumable"] is True
    assert any(dep["trace_id"] == "WC-005-04" for dep in item["dependencies"])

    service = client.app.state.writing_preparation_service.confirmation_service
    snapshot = service.snapshot(project_id)
    persisted = json.loads(snapshot.state_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "prewriting_human_confirmations_v1"
    assert len(persisted["change_history"]) == 1
    assert persisted["decisions"]["HC-ONSITE"]["writing_effect"] == "writing_constraint"
    assert "confirmed_project_facts" not in persisted
    assert str(tmp_path) not in saved.text

    updated = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": saved.headers["etag"]},
        json=_command(False, change_reason="调整为远程服务"),
    )
    assert updated.status_code == 200, updated.text
    history = service.snapshot(project_id).state["change_history"]
    assert len(history) == 2
    assert history[-1]["previous_answer"] is True
    assert history[-1]["new_answer"] is False


def test_writing_plan_overlays_task_specific_confirmation_without_mutating_plan(tmp_path: Path) -> None:
    project_id, _ = _analysis_project(tmp_path)
    _attach_confirmation_requests(tmp_path, project_id, with_plan=True)
    client = TestClient(create_app(tmp_path))
    initial = client.get(f"/api/projects/{project_id}/writing-plan")
    assert initial.status_code == 200, initial.text
    plan = initial.json()
    task = next(item for item in plan["tasks"] if item["task_id"] == "TASK-1")
    assert task["human_confirmation_refs"][0]["context_id"] == "HC-ONSITE"
    assert task["human_confirmation_refs"][0]["stage9_consumable"] is True
    plan_sha = plan["lineage"]["writing_tasks_sha256"]
    token = client.get(f"/api/projects/{project_id}/confirmations").headers["etag"]
    assert client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": token}, json=_command(True),
    ).status_code == 200
    current = client.get(f"/api/projects/{project_id}/writing-plan").json()
    assert current["lineage"]["writing_tasks_sha256"] == plan_sha
    assert current["summary"]["pending_human_confirmation_count"] == 0
    assert current["summary"]["stage9_unconsumable_confirmation_count"] == 0


def test_confirmation_stale_and_reanalysis_tokens_do_not_overwrite(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    token_a = client.get(f"/api/projects/{project_id}/confirmations").headers["etag"]
    first = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": token_a},
        json=_command(True),
    )
    assert first.status_code == 200
    stale = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": token_a},
        json=_command(False, change_reason="改为非驻场服务"),
    )
    assert stale.status_code == 409
    assert client.get(f"/api/projects/{project_id}/confirmations/HC-ONSITE").json()["answer"] is True

    context_path = tmp_path / "technical_response_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["build_warnings"] = ["new analysis lineage"]
    context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
    reanalysis_stale = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": first.headers["etag"]},
        json=_command(False, change_reason="旧页面修改"),
    )
    assert reanalysis_stale.status_code == 409


def test_invalid_kind_answer_unknown_and_project_scope_are_rejected(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    token = client.get(f"/api/projects/{project_id}/confirmations").headers["etag"]
    assert client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": token}, json=_command("yes"),
    ).status_code == 422
    assert client.patch(
        f"/api/projects/{project_id}/confirmations/HC-FACT-013",
        headers={"If-Match": token}, json=_command("value"),
    ).status_code == 422
    assert client.patch(
        f"/api/projects/{project_id}/confirmations/HC-MATERIAL",
        headers={"If-Match": token}, json=_command("available"),
    ).status_code == 422
    assert client.patch(
        f"/api/projects/{project_id}/confirmations/HC-UNKNOWN",
        headers={"If-Match": token}, json=_command(),
    ).status_code == 404
    assert client.get("/api/projects/project-unknown/confirmations").status_code == 404


def test_stage9_loader_sees_only_confirmed_project_fact_not_human_decision(tmp_path: Path) -> None:
    client, project_id = _client(tmp_path)
    token_a = client.get(f"/api/projects/{project_id}/writing-preparation").headers["etag"]
    decision = client.patch(
        f"/api/projects/{project_id}/confirmations/HC-ONSITE",
        headers={"If-Match": token_a}, json=_command(True),
    )
    token_b = decision.headers["etag"]
    fact = client.patch(
        f"/api/projects/{project_id}/writing-preparation/facts/FACT-013",
        headers={"If-Match": token_b},
        json={
            "status": "confirmed",
            "value": {"address": "天津市滨海新区", "contact": "022-12345678", "technician_count": 12},
            "source_description": "制造商书面确认",
            "confirmation_basis": "项目负责人核验",
            "note": None,
            "change_reason": None,
        },
    )
    assert fact.status_code == 200, fact.text
    facts_path = client.app.state.writing_preparation_service.fact_service.snapshot(project_id).state_path
    context = _stage9_context(tmp_path, facts_path)
    loaded = context.get_confirmed_project_facts({})
    assert "manufacturer_service_network" in loaded["facts"]
    assert "HC-ONSITE" not in json.dumps(loaded, ensure_ascii=False)
    confirmation_state = client.app.state.writing_preparation_service.confirmation_service.snapshot(project_id).state
    pending = next(
        item
        for item in confirmation_state["writing_context"]["items"]
        if item["context_id"] == "HC-ONSITE"
    )
    assert pending["stage9_consumable"] is True
    assert pending["stage9_policy"] == "apply_constraint"


def _stage9_context(tmp_path: Path, facts_path: Path) -> TechnicalWritingToolContext:
    response = tmp_path / "response.json"
    writing = tmp_path / "writing.json"
    tasks = tmp_path / "tasks.json"
    response.write_text(json.dumps({"chapters": []}), encoding="utf-8")
    writing.write_text(
        json.dumps({"chapters": [{"chapter_id": "WC-001", "children": [], "response_point_ids": []}]}),
        encoding="utf-8",
    )
    tasks.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    return TechnicalWritingToolContext(
        response_outline_path=response,
        writing_outline_path=writing,
        task_information_path=tasks,
        output_root=tmp_path,
        output_key="human_confirmation_test",
        target_chapter_id="WC-001",
        target_section_id=None,
        confirmed_project_facts_path=facts_path,
    )

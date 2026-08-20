import json

import app.agent_draft_generator as generator
from app.agent_draft_generator import generate_agent_drafts


def evidence(material_type="person_resume"):
    return {
        "file_id": "file_resume_001" if material_type != "company_qualification" else "file_qual_001",
        "source": "writing_plan.fixture",
        "title": "张三简历.docx" if material_type != "company_qualification" else "CMMI证书.pdf",
        "snippet": "ACP 个人证书材料" if material_type == "personal_certificate" else "结构化证据片段",
        "material_type": material_type,
        "confidence": 0.9,
    }


def draft_plan_item(task_id, name, writer_agent, section_type, evidence_refs=None, missing=None):
    return {
        "task_id": task_id,
        "task_name": name,
        "task_type": "technical" if writer_agent == "technical_writer" else "business",
        "match_status": "matched" if section_type == "evidence_backed" else section_type,
        "section_type": section_type,
        "writer_agent": writer_agent,
        "writing_goal": f"生成{name}草稿",
        "evidence_refs": evidence_refs or [],
        "missing_materials": missing or [],
        "outline": ["材料引用", "响应骨架"],
        "notes": [],
    }


def draft_section_from_plan(plan):
    status = "blocked_by_missing_material" if plan["section_type"] == "missing_blocked" else (
        "not_applicable" if plan["section_type"] == "not_applicable" else "draft_skeleton"
    )
    return {
        "task_id": plan["task_id"],
        "task_name": plan["task_name"],
        "section_title": plan["task_name"],
        "section_type": plan["section_type"],
        "writer_agent": plan["writer_agent"],
        "draft_text": f"V0 draft for {plan['task_name']}",
        "evidence_refs": list(plan.get("evidence_refs") or []),
        "missing_materials": list(plan.get("missing_materials") or []),
        "status": status,
    }


def project_with_sections(plans):
    return {
        "lot_name": "test-lot",
        "writing_plan": {
            "summary": {},
            "draft_plan": plans,
            "draft_sections": [draft_section_from_plan(plan) for plan in plans],
        },
    }


def make_24_plan():
    plans = []
    for index in range(1, 25):
        task_id = f"task_{index:03d}"
        if index in {5, 10}:
            plans.append(draft_plan_item(task_id, "材料缺口", "none", "missing_blocked", missing=[{"material_type": "company_qualification", "reason": "缺少资质"}]))
        elif index in {6, 12}:
            plans.append(draft_plan_item(task_id, "价格评分", "none", "not_applicable"))
        elif index % 2:
            plans.append(draft_plan_item(task_id, "项目负责人情况", "technical_writer", "evidence_backed", [evidence("person_resume")]))
        else:
            plans.append(draft_plan_item(task_id, "服务方案", "technical_writer", "writing_required"))
    return plans


def test_generates_24_agent_draft_sections(monkeypatch):
    calls = []

    def fake_run_agent(agent_name, input_data, provider="mock", agents_dir="agents"):
        calls.append((agent_name, input_data))
        return {"status": "success", "result": {"content": f"generated {input_data['task_name']}"}, "errors": []}

    monkeypatch.setattr(generator, "run_agent", fake_run_agent)
    result = generate_agent_drafts(project_with_sections(make_24_plan()))

    assert result["summary"]["total_sections"] == 24
    assert len(result["agent_draft_sections"]) == 24
    assert result["summary"]["agent_generated_sections"] == len(calls)


def test_evidence_backed_section_calls_writer_agent(monkeypatch):
    calls = []

    def fake_run_agent(agent_name, input_data, provider="mock", agents_dir="agents"):
        calls.append(agent_name)
        return {"status": "success", "result": {"content": "agent draft"}, "errors": []}

    monkeypatch.setattr(generator, "run_agent", fake_run_agent)
    plan = draft_plan_item("task_001", "项目负责人情况", "technical_writer", "evidence_backed", [evidence()])
    result = generate_agent_drafts(project_with_sections([plan]))

    assert calls == ["technical_writer"]
    assert result["agent_draft_sections"][0]["status"] == "generated_mock"


def test_writing_required_section_calls_writer_agent(monkeypatch):
    calls = []

    def fake_run_agent(agent_name, input_data, provider="mock", agents_dir="agents"):
        calls.append(agent_name)
        return {"status": "success", "result": {"content": "writing required draft"}, "errors": []}

    monkeypatch.setattr(generator, "run_agent", fake_run_agent)
    plan = draft_plan_item("task_002", "服务方案", "technical_writer", "writing_required")
    result = generate_agent_drafts(project_with_sections([plan]))

    assert calls == ["technical_writer"]
    assert result["agent_draft_sections"][0]["draft_text"] == "writing required draft"


def test_missing_blocked_section_does_not_call_agent(monkeypatch):
    def fail_run_agent(*args, **kwargs):
        raise AssertionError("agent should not be called")

    monkeypatch.setattr(generator, "run_agent", fail_run_agent)
    missing = [{"material_type": "company_qualification", "reason": "缺少企业资质"}]
    plan = draft_plan_item("task_003", "企业资质", "none", "missing_blocked", missing=missing)
    result = generate_agent_drafts(project_with_sections([plan]))

    section = result["agent_draft_sections"][0]
    assert section["status"] == "blocked_by_missing_material"
    assert section["missing_materials"] == missing


def test_not_applicable_section_does_not_call_agent(monkeypatch):
    def fail_run_agent(*args, **kwargs):
        raise AssertionError("agent should not be called")

    monkeypatch.setattr(generator, "run_agent", fail_run_agent)
    plan = draft_plan_item("task_004", "价格评分", "none", "not_applicable")
    result = generate_agent_drafts(project_with_sections([plan]))

    assert result["agent_draft_sections"][0]["status"] == "not_applicable"


def test_evidence_refs_are_preserved_from_writing_plan(monkeypatch):
    ref = evidence("personal_certificate")

    def fake_run_agent(agent_name, input_data, provider="mock", agents_dir="agents"):
        assert input_data["evidence_refs"] == [ref]
        return {"status": "success", "result": {"content": "agent draft"}, "errors": []}

    monkeypatch.setattr(generator, "run_agent", fake_run_agent)
    plan = draft_plan_item("task_005", "项目团队人员结构", "technical_writer", "evidence_backed", [ref])
    result = generate_agent_drafts(project_with_sections([plan]))

    assert result["agent_draft_sections"][0]["evidence_refs"] == [ref]


def test_personal_certificate_not_confused_with_company_qualification(monkeypatch):
    ref = evidence("personal_certificate")

    def fake_run_agent(agent_name, input_data, provider="mock", agents_dir="agents"):
        return {"status": "success", "result": {"content": "个人证书不得写成企业资质"}, "errors": []}

    monkeypatch.setattr(generator, "run_agent", fake_run_agent)
    plan = draft_plan_item("task_006", "项目负责人情况", "technical_writer", "evidence_backed", [ref])
    result = generate_agent_drafts(project_with_sections([plan]))

    section = result["agent_draft_sections"][0]
    assert section["evidence_refs"][0]["material_type"] == "personal_certificate"
    assert "个人证书不得写成企业资质" in section["draft_text"]


def test_output_has_no_bid_decision_language(monkeypatch):
    def fake_run_agent(agent_name, input_data, provider="mock", agents_dir="agents"):
        return {"status": "success", "result": {"content": "mock draft"}, "errors": []}

    monkeypatch.setattr(generator, "run_agent", fake_run_agent)
    plan = draft_plan_item("task_007", "商务响应", "business_writer", "evidence_backed", [evidence("company_qualification")])
    text = json.dumps(generate_agent_drafts(project_with_sections([plan])), ensure_ascii=False).lower()

    forbidden = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go"]
    assert not any(term.lower() in text for term in forbidden)

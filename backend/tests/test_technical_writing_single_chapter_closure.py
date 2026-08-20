import json
from pathlib import Path

from app.technical_placeholders import attach_placeholder_identity
from app.technical_writing_agent import (
    _targeted_auto_repair_instruction,
    chapter_completion_status,
    classify_chapter_audit,
)
from app.technical_writing_tools import TechnicalWritingToolContext


BACKEND = Path(__file__).resolve().parents[1]
RESPONSE = BACKEND / "outputs/hospital_hci_001/outline/technical_response_outline.json"
WRITING = BACKEND / "outputs/hospital_hci_001/writing/technical_writing_outline.json"
TASKS = BACKEND / "outputs/hospital_hci_001/writing/tasks/task_information_requirements.json"
DRAFT_UNITS = BACKEND / "outputs/hospital_hci_001/writing/tasks/draft_units.json"


class EvidenceStore:
    def __init__(self, records):
        self.records = records

    def load(self):
        return {"files": self.records}

    def get_file(self, file_id):
        return next(item for item in self.records if item["id"] == file_id)


def evidence(file_id, status, *, name="项目经理简历.docx"):
    return {
        "id": file_id,
        "evidence_id": f"EVD-{file_id}",
        "evidence_status": status,
        "original_name": name,
        "storage_key": f"original/{file_id}.docx",
        "parsed_text": "项目实施 人员 项目经理 交付管理",
        "resume_parse": {
            "person_name": f"人员-{file_id}",
            "department": "交付部",
        },
        "material_profile": {
            "material_group": "personnel",
            "material_types": ["resume"],
        },
        "valid_until": "2027-12-31",
    }


def context(tmp_path, records, *, section_id=None):
    return TechnicalWritingToolContext(
        response_outline_path=RESPONSE,
        writing_outline_path=WRITING,
        task_information_path=TASKS,
        draft_units_path=DRAFT_UNITS,
        output_root=tmp_path,
        output_key="single_chapter_closure_test",
        target_chapter_id=None if section_id else "WC-003",
        target_section_id=section_id,
        metadata_store=EvidenceStore(records),
        web_disabled=True,
    )


def test_task_information_and_draft_plan_remain_target_scoped_advice(tmp_path):
    tool_context = context(tmp_path, [], section_id="WC-003-01")
    task_info = tool_context.get_task_information({"section_id": "WC-003-01"})
    draft_plan = tool_context.get_draft_plan({"section_id": "WC-003-01"})
    assert task_info["task_count"] == 1
    assert {item["section_id"] for item in task_info["tasks"]} == {"WC-003-01"}
    assert draft_plan["advisory_only"] is True
    assert draft_plan["agent_retains_final_planning_authority"] is True
    assert all(
        set(item.get("task_ids") or [])
        & {
            task.get("task_id")
            for task in tool_context._tasks.get("tasks") or []
            if task.get("section_id") == "WC-003-01"
        }
        for item in draft_plan["draft_units"]
    )


def test_requirement_query_exposes_score_conditions_and_tender_table_index(tmp_path):
    tool_context = context(tmp_path, [])
    result = tool_context.get_response_requirements({"response_point_ids": []})
    assert result["target_scoped"] is True
    assert result["requirements"]
    assert all(
        {
            "requirement_id",
            "source_location",
            "substantive",
            "response_form",
            "requires_specific_value",
            "requires_proof",
            "score_items",
        }
        <= set(item)
        for item in result["requirements"]
    )
    assert all(
        "full_score_condition" in score
        for item in result["requirements"]
        for score in item["score_items"]
    )
    assert result["score_item_ids"]
    assert result["tender_table_ids"]
    assert any(
        item.get("table_writing_specification")
        for item in result["requirements"]
    )
    assert any(
        score["full_score_condition"]
        for item in result["requirements"]
        for score in item["score_items"]
    )
    assert set(result["response_point_ids"]) == set(
        tool_context.consulted_requirement_ids
    )


def test_unique_confirmed_knowledge_candidate_is_auto_selected(tmp_path):
    tool_context = context(tmp_path, [evidence("one", "confirmed")])
    result = tool_context.query_local_knowledge(
        {
            "query": "项目实施 人员",
            "evidence_types": ["person_resume"],
            "top_k": 3,
        }
    )
    selected = result["results"][0]
    assert selected["evidence_status"] == "confirmed"
    assert selected["selection_status"] == "confirmed"
    assert selected["auto_selected"] is True
    assert result["auto_selected_material_ids"] == ["KB-one"]
    assert selected["source_location"]
    assert selected["evidence_id"] == "EVD-one"
    assert selected["metadata"]["valid_until"] == "2027-12-31"


def test_multiple_confirmed_candidates_remain_review_candidates(tmp_path):
    tool_context = context(
        tmp_path,
        [evidence("one", "confirmed"), evidence("two", "confirmed")],
    )
    result = tool_context.query_local_knowledge(
        {
            "query": "项目实施 人员",
            "evidence_types": ["person_resume"],
            "top_k": 3,
        }
    )
    assert len(result["recommended_candidates"]) == 2
    assert not result["auto_selected_material_ids"]
    assert all(
        item["selection_status"] == "candidate"
        for item in result["results"]
    )
    assert result["human_confirmation_recommendation"]["required"] is True
    assert len(
        result["human_confirmation_recommendation"]["candidate_ids"]
    ) == 2
    assert (
        result["human_confirmation_recommendation"]["record_with"]
        == "record_pending_confirmation"
    )


def test_rejected_and_unusable_evidence_is_not_returned(tmp_path):
    tool_context = context(
        tmp_path,
        [
            evidence("bad-1", "rejected"),
            evidence("bad-2", "unusable"),
            evidence("unknown", "unknown"),
        ],
    )
    result = tool_context.query_local_knowledge(
        {
            "query": "项目实施 人员",
            "evidence_types": ["person_resume"],
            "top_k": 3,
        }
    )
    assert [item["candidate_id"] for item in result["results"]] == [
        "KB-unknown"
    ]
    assert result["results"][0]["evidence_status"] == "unknown"
    assert set(result["prohibited_material_ids_detected"]) == {
        "KB-bad-1",
        "KB-bad-2",
    }


def test_placeholder_has_one_traceable_schema_for_objective_facts():
    item = attach_placeholder_identity(
        {
            "field": "project_manager_name",
            "question": "确认拟派项目经理姓名",
            "reason": "人员是否实际参加本项目尚未确认",
            "candidates": [{"candidate_id": "KB-one"}],
            "affected_sections": ["WC-003-05"],
            "blocking": True,
        }
    )
    assert {
        "placeholder_id",
        "placeholder_token",
        "category",
        "field_name",
        "reason",
        "required_confirmation",
        "candidate_ids",
        "recommended_candidate",
        "recommendation_reason",
        "source_references",
        "severity",
        "release_blocking",
    } <= set(item)
    assert item["category"] == "personnel"
    assert item["placeholder_token"].startswith("{{PH-")


def test_audit_classification_separates_writing_fact_release_and_execution():
    validation = {
        "issues": [
            {
                "severity": "blocking",
                "category": "coverage",
                "code": "missing_score_items",
                "message": "score coverage incomplete",
            },
            {
                "severity": "blocking",
                "category": "placeholder",
                "code": "missing_pending_confirmation",
                "message": "product model is not confirmed",
            },
            {
                "severity": "fatal",
                "category": "schema",
                "code": "schema_validation_failed",
                "message": "invalid output",
            },
        ]
    }
    audit = classify_chapter_audit(
        validation,
        pending_confirmations=[
            {
                "placeholder_id": "PH-PRODUCT-MODEL",
                "field": "product_model",
                "reason": "model unknown",
                "release_blocking": True,
            }
        ],
        prohibited_material_ids=["KB-rejected"],
    )
    assert {item["code"] for item in audit["auto_fixable"]} == {
        "missing_score_items"
    }
    assert audit["human_confirmation_required"]
    assert audit["release_blocking"]
    assert audit["execution_error"]
    assert all(item["finding_id"].startswith("AUD-") for values in audit.values() if isinstance(values, list) for item in values)


def test_targeted_repair_input_reloads_current_draft_and_user_instruction():
    draft = {
        "chapter_title": "实施方案",
        "sections": [{"heading": "质量控制", "content": "当前草稿正文"}],
    }
    prompt = _targeted_auto_repair_instruction(
        draft=draft,
        audit_findings=[{"finding_id": "AUD-1", "code": "insufficient_prose"}],
        error_details=[],
        schema={"type": "object"},
        user_instruction="本轮意见：强化异常闭环",
    )
    assert "current_chapter_draft" in prompt
    assert "当前草稿正文" in prompt
    assert "本轮意见：强化异常闭环" in prompt
    assert "Global writing rules" in prompt


def test_final_status_distinguishes_pending_candidates_release_and_exhaustion():
    draft = {"chapter_title": "实施方案", "sections": []}
    assert chapter_completion_status(
        draft=draft,
        audit={
            "auto_fixable": [],
            "human_confirmation_required": [{"code": "pending_objective_fact"}],
            "release_blocking": [],
            "execution_error": [],
        },
    ) == "complete_with_pending_facts"
    assert chapter_completion_status(
        draft=draft,
        audit={
            "auto_fixable": [],
            "human_confirmation_required": [],
            "release_blocking": [],
            "execution_error": [],
        },
        local_evidence_refs=[
            {
                "candidate_id": "KB-one",
                "evidence_status": "candidate",
                "selection_status": "candidate",
            }
        ],
    ) == "complete_with_candidate_evidence"
    assert chapter_completion_status(
        draft=draft,
        audit={
            "auto_fixable": [],
            "human_confirmation_required": [],
            "release_blocking": [{"code": "missing_required_proof"}],
            "execution_error": [],
        },
    ) == "release_blocked"
    assert chapter_completion_status(
        draft=draft,
        audit={
            "auto_fixable": [{"code": "insufficient_prose"}],
            "human_confirmation_required": [],
            "release_blocking": [],
            "execution_error": [],
        },
        repair_rounds=[{"repair_round": 1}, {"repair_round": 2}],
    ) == "auto_repair_exhausted"
    assert chapter_completion_status(
        draft=None,
        audit={
            "auto_fixable": [],
            "human_confirmation_required": [],
            "release_blocking": [],
            "execution_error": [{"code": "model_failed"}],
        },
    ) == "execution_failed"

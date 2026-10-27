from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.stage9_input_handoff import Stage9InputResolver, Stage9InputSource
from app.technical_fulltext_auditor import TechnicalFulltextAuditor
from app.technical_fulltext_composer import sha256_file
from app.technical_fulltext_targeted_revision import (
    TechnicalFulltextTargetedRevisionError,
    execute_targeted_section_revision,
)
from app.technical_placeholders import placeholder_id_for_field, placeholder_token
from test_technical_fulltext_composer import (
    build_case,
    compose,
    default_candidates,
)
from test_technical_fulltext_revision_materializer import write_json
from test_technical_writing_agent_revision import EmptyMetadataStore, FinalModel


TARGET_SECTION_ID = "WC-010-01"
CANDIDATE_SOURCE_TYPE = "FAI"
CANDIDATE_SOURCE_ID = "FAI-T34-EXACT"


def _committed_source(tmp_path: Path) -> dict[str, Any]:
    candidates = default_candidates()
    candidates["WC-010"]["sections"][0]["content"] = (
        "当前 committed Stage10 正文是审核修改的唯一 existing draft 来源；"
        "该正文包含足够内容以满足现有 Agent 对可修订 Markdown 的有效性判断。"
    )
    case = build_case(
        tmp_path,
        candidates=candidates,
        output_key="revision_source",
    )
    composed = compose(case)
    planning = {
        "response_outline": {"schema_version": "technical_response_outline_v1", "chapters": []},
        "task_information": {"schema_version": "task_information_requirements_v1", "items": []},
        "draft_units": {
            "schema_version": "draft_units_v1",
            "content_location_plan": {},
            "draft_units": [],
        },
    }
    planning_paths = {
        name: write_json(tmp_path / "stage11" / f"{name}.json", payload)
        for name, payload in planning.items()
    }
    fulltext_path = Path(composed["outputs"]["technical_fulltext_json"])
    input_paths = {
        "writing_outline_path": str(case["outline_path"].resolve()),
        **{
            f"{name}_path": str(path.resolve())
            for name, path in planning_paths.items()
        },
        "confirmed_project_facts_path": None,
    }
    input_hashes = {
        key.replace("_path", "_sha256"): sha256_file(path)
        for key, path in input_paths.items()
        if path is not None
    }
    input_hashes["confirmed_project_facts_sha256"] = None
    write_json(
        fulltext_path.parent / "technical_fulltext.run.json",
        {
            "schema_version": "technical_fulltext_run_v1",
            "status": "completed",
            "production_status": "complete",
            "composition_status": "complete",
            "output_key": "revision_source",
            "input_paths": input_paths,
            "input_hashes": input_hashes,
            "chapter_count_expected": 2,
            "chapter_count_completed": 2,
            "chapter_count_submission_ready": 2,
            "chapter_count_blocked": 0,
            "chapter_count_failed": 0,
            "chapters": [],
            "outputs": composed["outputs"],
        },
    )
    handoff_path = write_json(
        fulltext_path.parent / "review" / "technical_fulltext.stage13_handoff.json",
        {
            "schema_version": "technical_fulltext_stage13_handoff_v1",
            "artifact_identity": {
                "handoff_id": "S13H-T34-01234567",
                "document_id": "DOC-STRICT",
                "output_key": "revision_source",
                "status": "ready",
            },
            "revision_candidates": [],
        },
    )
    return {
        "case": case,
        "composed": composed,
        "fulltext_path": fulltext_path,
        "handoff_path": handoff_path,
    }


def _stage9_source(tmp_path: Path, writing_outline_path: Path) -> tuple[Stage9InputSource, Path]:
    response_path = write_json(
        tmp_path / "stage9" / "response.json",
        {
            "schema_version": "technical_response_outline_v1",
            "chapters": [
                {
                    "chapter_id": "SRC-001",
                    "response_points": [
                        {
                            "point_id": "TR-1",
                            "original_text": "应说明第十章准备工作的执行安排。",
                            "mandatory_marker": "required",
                            "content_type": "narrative",
                            "source_refs": [{"file": "requirements.pdf", "page": 1}],
                        }
                    ],
                }
            ],
        },
    )
    task = {
        "task_id": "TASK-T34-1",
        "chapter_id": "WC-010",
        "section_id": TARGET_SECTION_ID,
        "response_point_ids": ["TR-1"],
        "response_point_mappings": [{"response_point_id": "TR-1"}],
        "score_item_refs": ["S-1"],
        "project_fact_slots": [
            {
                "fact_slot_id": "FACT-SLOT-CANONICAL",
                "fact_key": "canonical_plan_name",
                "name": "已确认方案名称",
            }
        ],
        "evidence_slots": [],
        "kb_query_plan": [],
    }
    writing_tasks_path = write_json(
        tmp_path / "stage9" / "section_writing_tasks.json",
        {"schema_version": "section_writing_tasks_v1", "tasks": [task]},
    )
    task_information_path = write_json(
        tmp_path / "stage9" / "task_information.json",
        {"schema_version": "task_information_requirements_v1", "tasks": [task]},
    )
    fact_state = {
        "project_id": "PROJECT-T34",
        "revision": 3,
        "facts": {
            "canonical_plan_name": {
                "fact_id": "FACT-CANONICAL",
                "status": "confirmed",
                "value": "既有正式方案",
                "label": "已确认方案名称",
            }
        },
    }
    fact_store_path = write_json(tmp_path / "canonical" / "project_facts.json", fact_state)
    confirmation_state = {
        "project_id": "PROJECT-T34",
        "revision": 7,
        "confirmation_requests": [
            {
                "confirmation_id": "HC-FALSE",
                "request_kind": "business_choice",
                "response_kind": "boolean",
                "question": "是否允许省略复核步骤？",
                "writing_task_refs": ["TASK-T34-1"],
            },
            {
                "confirmation_id": "HC-PENDING",
                "request_kind": "business_choice",
                "response_kind": "text",
                "question": "请确认最终责任人",
                "writing_task_refs": ["TASK-T34-1"],
            },
        ],
        "decisions": {
            "HC-FALSE": {
                "status": "confirmed",
                "response_kind": "boolean",
                "answer": False,
            }
        },
    }
    return (
        Stage9InputSource(
            project_id="PROJECT-T34",
            analysis_run_id=None,
            response_outline_path=response_path,
            writing_outline_path=writing_outline_path,
            writing_tasks_path=writing_tasks_path,
            task_information_path=task_information_path,
            confirmed_project_facts=fact_state,
            human_confirmation_state=confirmation_state,
        ),
        fact_store_path,
    )


def _candidate(*, locations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "source_type": CANDIDATE_SOURCE_TYPE,
        "source_id": CANDIDATE_SOURCE_ID,
        "human_decision": "modified",
        "effective_revision_instruction": "保留完整章节结构，并补充已批准的执行复核说明。",
        "target_locations": locations
        if locations is not None
        else [{"chapter_id": "WC-010", "section_id": TARGET_SECTION_ID}],
        "required_fact_refs": ["FACT-STAGE12", "FCA-STAGE12"],
        "required_confirmation_refs": [],
        "required_material_refs": ["MAT-STAGE12"],
        "approved_fact_refs": ["FACT-STAGE12", "FCA-STAGE12"],
        "approved_material_ref_ids": ["MAT-STAGE12"],
        "approved_facts": [
            {
                "source_type": "FACT",
                "source_id": "FACT-STAGE12",
                "confirmed_value": "经审核确认的本地服务安排",
                "source_description": "Stage12 fact confirmation",
                "confirmation_basis": "human_provided",
            },
            {
                "source_type": "FCA",
                "source_id": "FCA-STAGE12",
                "confirmed_value": "关键能力一致性已人工确认",
                "source_description": "Stage12 FCA confirmation",
                "confirmation_basis": "human_verified",
            },
        ],
        "approved_material_refs": [
            {
                "source_type": "MATERIAL",
                "material_or_evidence_ref": "MAT-STAGE12",
                "material_name": "经核验证明材料",
                "file_reference": "verified/material.pdf",
                "evidence_types": ["service_certificate"],
                "verification_note": "Stage12 verified",
            }
        ],
        "prohibited_inferences": [
            {
                "source_type": "FACT",
                "source_id": "FACT-PENDING",
                "reason": "must_not_be_inferred",
            }
        ],
        "acceptance_criteria": ["修改后必须明确执行、复核和关闭判据。"],
        "unresolved_dependencies": {
            "pending_fact_refs": [],
            "pending_material_refs": [],
            "pending_confirmation_refs": [],
        },
    }


def _replace_handoff_candidate(path: Path, candidate: dict[str, Any]) -> None:
    handoff = json.loads(path.read_text(encoding="utf-8"))
    handoff["revision_candidates"] = [candidate]
    write_json(path, handoff)


def _replacement_draft(source_fulltext: dict[str, Any]) -> dict[str, Any]:
    original = source_fulltext["chapters"][0]["sections"][0]
    pending_id = placeholder_id_for_field("human_confirmation_HC-PENDING")
    content = (
        "准备工作以既有正式方案为基线，先核对输入资料、责任接口和执行边界，形成可追踪的启动记录。"
        "项目团队按照经审核确认的本地服务安排组织执行，并以经核验证明材料作为可引用的支撑来源。\n\n"
        "执行过程中设置操作、复核和问题关闭三个检查点。发现缺项时登记责任、整改动作与完成判据，"
        "复核通过后方可进入下一阶段；关键能力一致性采用已人工确认的结论，不推断任何待确认事实。\n\n"
        f"最终责任人保持待确认状态：{placeholder_token(pending_id)}。"
    )
    replacement = deepcopy(original)
    replacement["content"] = content
    replacement["placeholder_ids"] = [pending_id]
    return {
        "chapter_title": original["heading"],
        "sections": [replacement],
        "claims": [],
        "placeholders_used": [pending_id],
        "score_items_covered": list(replacement["covered_score_item_ids"]),
        "requirements_covered": list(replacement["covered_requirement_ids"]),
        "warnings": [],
    }


def _source_snapshot(fulltext_dir: Path) -> dict[str, tuple[bytes, str]]:
    return {
        path.name: (path.read_bytes(), sha256_file(path))
        for path in fulltext_dir.iterdir()
        if path.is_file()
    }


def test_ready_candidate_rewrites_current_stage10_section_and_materializes_revision(
    tmp_path: Path,
) -> None:
    fixture = _committed_source(tmp_path)
    source_fulltext = json.loads(fixture["fulltext_path"].read_text(encoding="utf-8"))
    _replace_handoff_candidate(fixture["handoff_path"], _candidate())
    stage9_source, fact_store_path = _stage9_source(
        tmp_path, fixture["case"]["outline_path"]
    )
    material_store_path = write_json(
        tmp_path / "canonical" / "materials.json",
        {"materials": [{"material_id": "UNCHANGED"}]},
    )
    resolved = Stage9InputResolver().resolve(stage9_source)
    expected_task = resolved["tasks"][0]
    model = FinalModel(_replacement_draft(source_fulltext))
    source_before = _source_snapshot(fixture["fulltext_path"].parent)
    fact_before = fact_store_path.read_bytes()
    material_before = material_store_path.read_bytes()

    result = execute_targeted_section_revision(
        source_fulltext_path=fixture["fulltext_path"],
        stage12_handoff_path=fixture["handoff_path"],
        candidate_source_type=CANDIDATE_SOURCE_TYPE,
        candidate_source_id=CANDIDATE_SOURCE_ID,
        stage9_source=stage9_source,
        draft_units_path=None,
        output_dir=fixture["case"]["output_dir"],
        output_key="revision_t34",
        llm=model,
        metadata_store=EmptyMetadataStore(),
    )

    assert result["candidate_identity"] == {
        "source_type": CANDIDATE_SOURCE_TYPE,
        "source_id": CANDIDATE_SOURCE_ID,
    }
    assert result["target_section_id"] == TARGET_SECTION_ID
    assert result["task_id"] == expected_task["task_id"]
    assert result["stage9_input_fingerprint"] == resolved["fingerprint"]
    assert result["task_fingerprint"] == expected_task["task_fingerprint"]
    assert expected_task["confirmed_project_facts"][0]["value"] == "既有正式方案"
    assert expected_task["confirmed_business_choices"][0]["answer"] is False
    assert expected_task["pending_confirmations"][0]["confirmation_id"] == "HC-PENDING"
    assert expected_task["placeholder_requirements"][0]["placeholder_token"] in json.dumps(
        model.messages, ensure_ascii=False
    )

    messages = json.dumps(model.messages, ensure_ascii=False)
    original_body = source_fulltext["chapters"][0]["sections"][0]["content"]
    assert original_body in messages
    assert _candidate()["effective_revision_instruction"] in messages
    assert "经审核确认的本地服务安排" in messages
    assert "FCA-STAGE12" in messages
    assert "stage12_approved_input" in messages
    assert "MAT-STAGE12" in messages
    assert "FACT-PENDING" in messages
    assert _candidate()["acceptance_criteria"][0] in messages
    assert result["agent_result"]["based_on_existing_draft"] is True
    assert result["agent_result"]["run_summary"]["section_count_expected"] == 1
    assert result["agent_result"]["local_knowledge_references"][0][
        "selection_status"
    ] == "confirmed"

    materialized = result["materialization"]
    revised = materialized["fulltext"]
    assert revised["output_key"] == "revision_t34"
    assert revised["chapters"][0]["sections"][0] == result["replacement_section"]
    assert [
        {key: value for key, value in chapter.items() if key != "source"}
        for chapter in revised["chapters"][1:]
    ] == [
        {key: value for key, value in chapter.items() if key != "source"}
        for chapter in source_fulltext["chapters"][1:]
    ]
    assert _source_snapshot(fixture["fulltext_path"].parent) == source_before
    assert fact_store_path.read_bytes() == fact_before
    assert material_store_path.read_bytes() == material_before

    workspace = Path(result["agent_workspace"])
    assert workspace == (
        fixture["case"]["output_dir"]
        / "_targeted_revision_workspaces"
        / hashlib.sha256(b"revision_t34").hexdigest()[:16]
    ).resolve()
    assert result["agent_output_key"] == "agent"
    assert workspace != Path(materialized["outputs"]["technical_fulltext_json"]).parent
    audit = TechnicalFulltextAuditor().audit(
        fulltext_json_path=materialized["outputs"]["technical_fulltext_json"]
    )
    assert audit["status"] == "completed"


@pytest.mark.parametrize(
    "locations",
    [
        [],
        [
            {"chapter_id": "WC-010", "section_id": "WC-010-01"},
            {"chapter_id": "WC-002", "section_id": "WC-002-01"},
        ],
        [{"chapter_id": "WC-010", "section_id": "DOES-NOT-EXIST"}],
    ],
)
def test_stage12_location_must_resolve_exactly_without_requirement_fallback(
    tmp_path: Path, locations: list[dict[str, Any]]
) -> None:
    fixture = _committed_source(tmp_path)
    _replace_handoff_candidate(fixture["handoff_path"], _candidate(locations=locations))
    stage9_source, _ = _stage9_source(tmp_path, fixture["case"]["outline_path"])

    with pytest.raises(
        TechnicalFulltextTargetedRevisionError,
        match=(
            "exactly one section|conflicting chapters|"
            "match exactly one source fulltext section"
        ),
    ):
        execute_targeted_section_revision(
            source_fulltext_path=fixture["fulltext_path"],
            stage12_handoff_path=fixture["handoff_path"],
            candidate_source_type=CANDIDATE_SOURCE_TYPE,
            candidate_source_id=CANDIDATE_SOURCE_ID,
            stage9_source=stage9_source,
            draft_units_path=None,
            output_dir=fixture["case"]["output_dir"],
            output_key="revision_t34_invalid",
            llm=FinalModel({}),
            metadata_store=EmptyMetadataStore(),
        )
    assert not (fixture["case"]["output_dir"] / "revision_t34_invalid").exists()


def test_requested_candidate_must_match_existing_identity_exactly(tmp_path: Path) -> None:
    fixture = _committed_source(tmp_path)
    _replace_handoff_candidate(fixture["handoff_path"], _candidate())
    stage9_source, _ = _stage9_source(tmp_path, fixture["case"]["outline_path"])

    with pytest.raises(
        TechnicalFulltextTargetedRevisionError,
        match="candidate must match exactly once; found 0",
    ):
        execute_targeted_section_revision(
            source_fulltext_path=fixture["fulltext_path"],
            stage12_handoff_path=fixture["handoff_path"],
            candidate_source_type=CANDIDATE_SOURCE_TYPE,
            candidate_source_id="FAI-NOT-REQUESTED",
            stage9_source=stage9_source,
            draft_units_path=None,
            output_dir=fixture["case"]["output_dir"],
            output_key="revision_t34_missing_candidate",
            llm=FinalModel({}),
            metadata_store=EmptyMetadataStore(),
        )


def test_ready_handoff_candidate_with_unresolved_dependency_fails_before_agent(
    tmp_path: Path,
) -> None:
    fixture = _committed_source(tmp_path)
    candidate = _candidate()
    candidate["unresolved_dependencies"]["pending_material_refs"] = ["MAT-PENDING"]
    _replace_handoff_candidate(fixture["handoff_path"], candidate)
    stage9_source, _ = _stage9_source(tmp_path, fixture["case"]["outline_path"])
    model = FinalModel({})

    with pytest.raises(
        TechnicalFulltextTargetedRevisionError,
        match="unresolved dependencies: MAT-PENDING",
    ):
        execute_targeted_section_revision(
            source_fulltext_path=fixture["fulltext_path"],
            stage12_handoff_path=fixture["handoff_path"],
            candidate_source_type=CANDIDATE_SOURCE_TYPE,
            candidate_source_id=CANDIDATE_SOURCE_ID,
            stage9_source=stage9_source,
            draft_units_path=None,
            output_dir=fixture["case"]["output_dir"],
            output_key="revision_t34_blocked",
            llm=model,
            metadata_store=EmptyMetadataStore(),
        )
    assert model.messages == []

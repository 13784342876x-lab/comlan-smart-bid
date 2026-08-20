from __future__ import annotations

import json
from pathlib import Path

from app.llm_client import FORBIDDEN_DECISION_TERMS
from app.tender_requirement_matrix import (
    DERIVED_OUTPUTS,
    build_tender_requirement_matrix,
    classify_requirement,
    inspect_tender_requirement_matrix,
)


LOT_NAME = "0425139003001-6582"


def clause(
    text: str,
    *,
    clause_type: str = "response_file_requirement",
    region: str = "bidder_instruction",
) -> dict:
    return {
        "clause_id": "file_tender::c0001",
        "file_id": "file_tender",
        "file_name": "招标文件-服务.docx",
        "source_type": "full_tender_document",
        "document_region": region,
        "section_path": ["第二章 投标人须知", "响应和偏差"],
        "clause_title": "响应和偏差",
        "clause_text": text,
        "clause_type": clause_type,
        "source_location": {"page": None, "paragraph_index": 12, "table_index": None, "row_index": None, "sheet_name": None},
    }


def context() -> dict:
    return {
        "schema_version": "1.0",
        "lot_name": LOT_NAME,
        "project": {
            "tender_number": "042513",
            "project_name": "国网河北电力2025年第三次服务类公开招标采购",
            "lot_number": LOT_NAME,
            "lot_name": "综合服务-科技检修技术标准",
            "package_number": "包01",
            "package_name": "基于区块链的电力系统数据管理国际标准研究",
        },
        "project_conflicts": [
            {
                "field": "package_name",
                "selected_value": "基于区块链的电力系统数据管理国际标准研究",
                "candidate_values": ["基于区块链的电力系统数据管理国际标准研究", "基于区块链的工业数据管理技术研究"],
                "status": "needs_human_confirmation",
                "source_type": "human_confirmation_required",
            }
        ],
        "source_files": [{"source_file_id": "file_tender", "source_file_name": "招标文件-服务.docx", "source_type": "tender_original_fact"}],
        "chapters": [
            {
                "chapter_id": "chapter_overall",
                "chapter_title": "应答文件技术部分总体评价",
                "score_refs": [{"score_item_id": "score_technical_overall", "score_item_name": "应答文件技术部分总体评价"}],
                "tender_requirement_refs": [
                    {
                        "ref_id": "file_tender::c0001",
                        "source_file_id": "file_tender",
                        "clause_path": "第二章 投标人须知 > 响应和偏差",
                        "excerpt": "投标文件应当对招标文件的实质性要求作出响应。",
                        "source_type": "tender_original_fact",
                    }
                ],
                "technical_spec_refs": [],
                "response_format_refs": [],
            }
        ],
        "global_manual_confirm_items": [
            {
                "confirm_id": "confirm-project-package_name",
                "category": "project_field_conflict",
                "question": "请确认包名称。",
                "related_field": "package_name",
                "source_type": "human_confirmation_required",
            }
        ],
    }


def source_files() -> list[dict]:
    return [
        {
            "id": "file_tender",
            "original_name": "招标文件-服务.docx",
            "category": "招标文件",
            "tags": ["full_tender_document"],
            "parsed_text": "",
        }
    ]


def classify(text: str, **kwargs) -> dict:
    return classify_requirement(text, clause(text, **kwargs), context(), source_files())


def test_negative_exception_is_not_fatal() -> None:
    item = classify("剔除只为计算平均值使用，并不否决投标人的报价。")

    assert item["polarity"] == "negative_exception"
    assert item["compliance_level"] != "fatal_if_missing"
    assert item["risk_type"] != "bid_invalidity_risk"


def test_not_a_rejection_condition_is_not_invalidity_risk() -> None:
    item = classify("该项不作为否决条件，也不得作为否决依据。")

    assert item["requirement_type"] == "reference_only"
    assert item["risk_type"] == "none"


def test_explicit_unsigned_clause_can_be_fatal_signature_risk() -> None:
    item = classify(
        "未按要求签字盖章的，否决其投标。",
        clause_type="signature_seal_requirement",
        region="response_format",
    )

    assert item["requirement_type"] == "signature_seal_requirement"
    assert item["compliance_level"] == "fatal_if_missing"
    assert item["risk_type"] == "signature_seal_risk"


def test_must_or_should_without_explicit_consequence_is_not_fatal() -> None:
    for text in ["投标文件必须按要求签字盖章。", "投标人应当提供资格证明材料。", "技术方案须逐条响应技术规范书。"]:
        assert classify(text)["compliance_level"] != "fatal_if_missing"


def test_unresolved_external_reference_requires_review() -> None:
    item = classify("投标人应按国家电网相关标准执行。")

    assert item["external_reference"]["resolved"] is False
    assert item["human_review_required"] is True


def matrix_fixture(tmp_path: Path) -> tuple[dict, Path]:
    parsed_text = "\n".join(
        [
            "第二章 投标人须知",
            "1.1 响应和偏差",
            "投标文件应当对招标文件的实质性要求作出响应，否则投标将被否决。",
            "2.1 引用标准",
            "投标人应按国家电网相关标准执行。",
            "第六章 投标文件格式",
            "1.1 技术偏差表",
            "投标人应填写技术偏差表并签字盖章。",
        ]
    )
    metadata = {
        "files": [
            {
                "id": "file_tender",
                "original_name": "招标文件-服务.docx",
                "category": "招标文件",
                "tags": ["full_tender_document"],
                "parsed_text": parsed_text,
            }
        ]
    }
    project = {
        "lot_name": LOT_NAME,
        "tender_file_id": "file_tender",
        "score_items": [],
        "disqualification_rules": [
            {"requirement": "剔除只为计算平均值使用，并不否决投标人的报价。"}
        ],
    }
    result = build_tender_requirement_matrix(
        LOT_NAME,
        output_dir=tmp_path,
        metadata=metadata,
        project=project,
        context=context(),
        update_context_index=False,
    )
    matrix_path = Path(result["matrix_path"])
    return json.loads(matrix_path.read_text(encoding="utf-8")), matrix_path


def test_matrix_requirements_have_sources_locations_and_chapter_links(tmp_path: Path) -> None:
    matrix, _ = matrix_fixture(tmp_path)

    assert matrix["requirements"]
    assert all(item["source_ref"] and item["source_type"] == "tender_original_fact" for item in matrix["requirements"])
    assert all(item["source_ref"]["location_precision"] in {"paragraph_exact", "clause_approximate", "snippet_only", "file_only", "unknown"} for item in matrix["requirements"])
    assert any("chapter_overall" in item["related_context_chapter_ids"] for item in matrix["requirements"])
    payload = json.dumps(matrix, ensure_ascii=False)
    assert '"page_number"' not in payload
    assert '"cell_coordinate"' not in payload


def test_derived_json_is_from_matrix_and_does_not_reparse_sources(tmp_path: Path) -> None:
    matrix, matrix_path = matrix_fixture(tmp_path)

    for name, predicate in DERIVED_OUTPUTS.items():
        payload = json.loads((matrix_path.parent / f"{name}.json").read_text(encoding="utf-8"))
        expected = [item["requirement_id"] for item in matrix["requirements"] if predicate(item)]
        assert payload["generated_from"] == str(matrix_path)
        assert payload["requirement_ids"] == expected


def test_markdown_is_not_needed_for_inspection(tmp_path: Path) -> None:
    _, matrix_path = matrix_fixture(tmp_path)
    (matrix_path.parent / "tender_requirement_matrix.md").unlink()

    inspected = inspect_tender_requirement_matrix(LOT_NAME, output_dir=tmp_path)

    assert inspected["statistics"]["requirement_count"] > 0


def test_unresolved_reference_and_package_conflict_are_preserved(tmp_path: Path) -> None:
    matrix, _ = matrix_fixture(tmp_path)

    assert matrix["unresolved_external_references"]
    assert matrix["project_ref"]["project_conflicts"][0]["status"] == "needs_human_confirmation"
    assert any(item.get("related_field") == "package_name" for item in matrix["global_manual_confirm_items"])
    assert any("旧 disqualification_rules" in warning for warning in matrix["build_warnings"])


def test_outputs_have_no_bid_decision_language(tmp_path: Path) -> None:
    _, matrix_path = matrix_fixture(tmp_path)
    text = "\n".join(path.read_text(encoding="utf-8") for path in matrix_path.parent.glob("*"))

    for term in FORBIDDEN_DECISION_TERMS:
        assert term.lower() not in text.lower()

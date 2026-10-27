from __future__ import annotations

import json
from pathlib import Path

from app.technical_review_projection import (
    active_technical_review_items,
    confirmation_groups,
    semantic_confirmation_source_ids,
    user_facing_text,
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path):
    package_path = tmp_path / "artifact" / "fulltext" / "review" / "technical_fulltext.review_package.json"
    package = {
        "review_items": [
            {"source_type": "FAI", "source_id": "FAI-TECH", "requirement_ids": ["TR-001"]},
            {"source_type": "FAI", "source_id": "FAI-PRICE", "requirement_ids": ["TR-002"]},
            {"source_type": "FAI", "source_id": "FAI-COMM", "requirement_ids": ["TR-003"]},
            {"source_type": "FSF", "source_id": "FSF-SCORE", "requirement_ids": ["TR-004"]},
            {
                "source_type": "FCA",
                "source_id": "FCA-A",
                "requirement_ids": [],
                "required_confirmation_refs": ["FCA-A"],
                "current_response_locations": [
                    {"chapter_id": "WC-001", "section_id": f"WC-001-0{i}"}
                    for i in range(1, 9)
                ],
            },
        ],
        "fact_material_queue": [
            {"source_type": "FCA", "source_id": "FCA-A", "display_title": "项目经理姓名"},
            {"source_type": "FCA", "source_id": "FCA-A2", "display_title": "项目经理姓名"},
        ],
    }
    human = {
        "fact_confirmations": [
            {"source_type": "FACT", "source_id": "FACT-1", "fact_key": "manager_name", "confirmation_status": "pending"},
            {"source_type": "FACT", "source_id": "FACT-2", "fact_key": "manager_title", "confirmation_status": "pending"},
            {"source_type": "FCA", "source_id": "FCA-A", "fact_key": None, "confirmation_status": "pending"},
            {"source_type": "FCA", "source_id": "FCA-A2", "fact_key": None, "confirmation_status": "pending"},
            {"source_type": "FCA", "source_id": "FCA-UNKNOWN-1", "fact_key": None, "confirmation_status": "pending"},
            {"source_type": "FCA", "source_id": "FCA-UNKNOWN-2", "fact_key": None, "confirmation_status": "pending"},
        ]
    }
    outline = {
        "chapters": [{"response_points": [
            {"point_id": "TR-001", "original_text": "性能要求", "source_refs": [{"file_name": "tender.docx"}]},
            {"point_id": "TR-002", "original_text": "报价评分方法", "source_refs": [{"file_name": "tender.docx"}]},
            {"point_id": "TR-003", "original_text": "商务评分标准", "source_refs": [{"file_name": "tender.docx"}]},
            {"point_id": "TR-004", "original_text": "技术评分标准", "source_refs": [{"file_name": "tender.docx"}]},
        ]}],
        "score_items": [{"score_item_id": "technical-score-1"}],
    }
    matrix = {"requirements": [
        {"requirement_id": "REQ-TECH", "requirement_type": "technical_mandatory_requirement", "raw_text": "性能要求", "source_ref": {"source_file_name": "tender.docx"}},
        {"requirement_id": "REQ-PRICE", "requirement_type": "score_related_requirement", "raw_text": "报价评分方法", "related_score_item_ids": ["price-score-1"], "source_ref": {"source_file_name": "tender.docx"}},
        {"requirement_id": "REQ-COMM", "requirement_type": "score_related_requirement", "raw_text": "商务评分标准", "related_score_item_ids": ["business-score-1"], "source_ref": {"source_file_name": "tender.docx"}},
        {"requirement_id": "REQ-SCORE", "requirement_type": "score_related_requirement", "raw_text": "技术评分标准", "related_score_item_ids": ["technical-score-1"], "source_ref": {"source_file_name": "tender.docx"}},
    ]}
    tasks = {"tasks": [{"response_point_mappings": [{"response_point_id": "TR-001"}]}]}
    audit = {"confirmation_register": [
        {"confirmation_id": "FCA-A", "canonicalization": "exact_placeholder_id", "member_placeholder_ids": ["{{PH-FIELD-MANAGER}}"], "occurrence_count": 8, "affected_locations": [{"chapter_id": "WC-001", "section_id": f"WC-001-0{i}"} for i in range(1, 9)]},
        {"confirmation_id": "FCA-A2", "canonicalization": "exact_placeholder_id", "member_placeholder_ids": ["PH-FIELD-MANAGER"], "occurrence_count": 2, "affected_locations": []},
        {"confirmation_id": "FCA-UNKNOWN-1", "canonicalization": "unregistered", "member_placeholder_ids": ["PH-FIELD-X"], "occurrence_count": 1, "affected_locations": []},
        {"confirmation_id": "FCA-UNKNOWN-2", "canonicalization": "unregistered", "member_placeholder_ids": ["PH-FIELD-X"], "occurrence_count": 1, "affected_locations": []},
    ]}
    _write(package_path, package)
    _write(tmp_path / "artifact" / "planning" / "tender_requirement_matrix.json", matrix)
    _write(tmp_path / "artifact" / "planning" / "technical_response_outline.json", outline)
    _write(tmp_path / "artifact" / "planning" / "section_writing_tasks.json", tasks)
    _write(tmp_path / "artifact" / "fulltext" / "audit" / "technical_fulltext.audit.json", audit)
    return package_path, package, human


def test_review_scope_uses_structured_technical_writing_applicability(tmp_path: Path) -> None:
    package_path, package, _ = _fixture(tmp_path)
    ids = {item["source_id"] for item in active_technical_review_items(package, package_path)}
    assert "FAI-TECH" in ids
    assert "FSF-SCORE" in ids
    assert "FAI-PRICE" not in ids
    assert "FAI-COMM" not in ids


def test_confirmation_projection_groups_only_reliable_semantic_identity(tmp_path: Path) -> None:
    package_path, package, human = _fixture(tmp_path)
    groups = confirmation_groups(package, human, package_path)
    assert len(human["fact_confirmations"]) == 6
    assert len(groups) == 5
    manager = next(group for group in groups if group["semantic_key"] == ("PLACEHOLDER", "PH-FIELD-MANAGER"))
    assert len(manager["records"]) == 2
    assert manager["occurrence_count"] == 10
    assert len(manager["locations"]) == 8
    assert semantic_confirmation_source_ids(package, human, package_path, "FCA-A") == ["FCA-A", "FCA-A2"]
    assert {group["semantic_key"] for group in groups} >= {
        ("FACT", "manager_name"),
        ("FACT", "manager_title"),
        ("FCA", "FCA-UNKNOWN-1"),
        ("FCA", "FCA-UNKNOWN-2"),
    }


def test_user_facing_projection_removes_embedded_internal_identity_without_losing_title() -> None:
    titles = {"TR-008": "报价评分方法", "WC-002-01": "总体技术方案 > 总体设计原则"}
    rendered = user_facing_text(
        "TR-008：报价评分方法；WC-002-01 \u201c总体设计原则\u201d；FAI-ABC；PH-FIELD-X",
        titles,
    )
    assert rendered is not None
    assert "报价评分方法" in rendered
    assert "总体设计原则" in rendered
    assert all(token not in rendered for token in ("TR-", "WC-", "FAI-", "PH-"))


def test_current_v1_package_projects_to_technical_scope_and_unique_confirmations() -> None:
    root = Path(__file__).resolve().parents[1] / "outputs" / "bid_pipeline_outputs" / "bid-20260910T052035Z-342c5914" / "fulltext-bid-20260910T052035Z-342c5914" / "fulltext"
    package_path = root / "review" / "technical_fulltext.review_package.json"
    human_path = root / "review" / "technical_fulltext.human_review.json"
    if not package_path.is_file() or not human_path.is_file():
        return
    package = json.loads(package_path.read_text(encoding="utf-8"))
    human = json.loads(human_path.read_text(encoding="utf-8"))
    active = active_technical_review_items(package, package_path)
    active_titles = {str(item.get("display_title") or "") for item in active}
    assert "报价评分办法" not in active_titles, [
        (item.get("source_id"), item.get("display_title"), item.get("requirement_ids"))
        for item in active
        if item.get("source_type") in {"FAI", "FSF"}
    ]
    assert "商务评分标准" not in active_titles
    assert len([item for item in active if item.get("source_type") in {"FAI", "FSF"}]) == 5
    assert len(human["fact_confirmations"]) == 53
    assert len(confirmation_groups(package, human, package_path)) == 49

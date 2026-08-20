from __future__ import annotations

import json

from app.score_maximization_matrix import build_score_maximization_matrix, inspect_score_maximization_matrix
from tests.score_matrix_test_helpers import LOT_NAME, table_by_id, write_case_inputs


def test_score_maximization_matrix_generates_nine_items_and_markdown(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    result = build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    matrix = json.loads((outline_dir / "score_maximization_matrix.json").read_text(encoding="utf-8"))

    assert result["summary"]["score_item_count"] == 9
    assert len(matrix["items"]) == 9
    assert (outline_dir / "score_maximization_matrix.md").exists()
    assert "# 评分项上限准备矩阵" in (outline_dir / "score_maximization_matrix.md").read_text(encoding="utf-8")


def test_performance_goal_has_five_slots_and_no_confirmed_company_performance(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    matrix = json.loads((outline_dir / "score_maximization_matrix.json").read_text(encoding="utf-8"))
    tables = json.loads((outline_dir / "manual_review_tables.json").read_text(encoding="utf-8"))
    performance_item = next(item for item in matrix["items"] if item["section_title"] == "业绩与项目经验")
    performance_table = table_by_id(tables, "performance_review_table")

    assert performance_item["required_slots"] == 5
    assert "5项" in performance_item["max_preparation_goal"]
    assert len(performance_table["rows"]) == 5
    assert {row["状态"] for row in performance_table["rows"]} == {"missing"}


def test_inspect_score_maximization_matrix_reports_required_outputs(tmp_path) -> None:
    write_case_inputs(tmp_path)
    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)

    inspected = inspect_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)

    assert inspected["files"]["matrix_json_exists"] is True
    assert inspected["files"]["matrix_markdown_exists"] is True
    assert inspected["files"]["manual_review_tables_json_exists"] is True
    assert inspected["manual_review_summary"]["performance_rows"] == 5
    assert inspected["manual_review_summary"]["patent_rows"] == 2

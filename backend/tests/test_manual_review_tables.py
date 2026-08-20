from __future__ import annotations

import json

from app.score_maximization_matrix import build_score_maximization_matrix
from tests.score_matrix_test_helpers import LOT_NAME, table_by_id, write_case_inputs


def test_personnel_project_experience_does_not_enter_performance_table(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    tables = json.loads((outline_dir / "manual_review_tables.json").read_text(encoding="utf-8"))
    performance_table_text = json.dumps(table_by_id(tables, "performance_review_table"), ensure_ascii=False)

    assert "冯振书" not in performance_table_text
    assert "吴云畅" not in performance_table_text
    assert "人员项目经验不得进入此表" in performance_table_text


def test_patent_review_table_has_two_rows_and_excludes_delivery_patent_as_existing_material(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    tables = json.loads((outline_dir / "manual_review_tables.json").read_text(encoding="utf-8"))
    patent_table = table_by_id(tables, "patent_review_table")

    assert len(patent_table["rows"]) == 2
    assert {row["状态"] for row in patent_table["rows"]} == {"missing"}
    assert all(row["专利名称"] != "受理发明专利1项" for row in patent_table["rows"])


def test_personnel_candidates_are_needs_review_not_usable(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    tables = json.loads((outline_dir / "manual_review_tables.json").read_text(encoding="utf-8"))
    personnel_table = table_by_id(tables, "personnel_review_table")
    by_name = {row["姓名"]: row for row in personnel_table["rows"]}
    roles = {row["拟任角色"] for row in personnel_table["rows"]}

    assert by_name["冯振书"]["状态"] == "needs_review"
    assert by_name["吴云畅"]["状态"] == "needs_review"
    assert "usable" not in {row["状态"] for row in personnel_table["rows"]}
    assert {"项目负责人", "主要团队成员"}.issubset(roles)


def test_work_foundation_and_supplier_performance_do_not_mark_wrong_materials_usable(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    tables = json.loads((outline_dir / "manual_review_tables.json").read_text(encoding="utf-8"))
    work_table = table_by_id(tables, "work_foundation_review_table")
    supplier_table = table_by_id(tables, "supplier_performance_review_table")

    assert "usable" not in {row["状态"] for row in work_table["rows"]}
    assert "人员简历" in json.dumps(work_table, ensure_ascii=False)
    assert "usable" not in {row["状态"] for row in supplier_table["rows"]}
    assert "资质证书、简历、业绩合同不得替代供应商绩效评价" in json.dumps(supplier_table, ensure_ascii=False)


def test_solution_task_breakdown_uses_technical_spec_tasks(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    tables = json.loads((outline_dir / "manual_review_tables.json").read_text(encoding="utf-8"))
    task_table = table_by_id(tables, "solution_task_breakdown_table")
    task_names = {row["任务名称"] for row in task_table["rows"]}

    assert "基于区块链的工业数据存证技术" in task_names
    assert "同态哈希完整性验证" in task_names
    assert "Schnorr 签名数字签名验证" in task_names
    assert "受理发明专利1项" in task_names
    assert any(row["状态"] == "needs_writing" for row in task_table["rows"])
    assert (outline_dir / "manual_review_tables.md").exists()

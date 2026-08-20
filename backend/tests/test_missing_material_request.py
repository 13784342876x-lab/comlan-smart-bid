from __future__ import annotations

import json

from typer.testing import CliRunner

from app.score_maximization_matrix import build_score_maximization_matrix, inspect_missing_material_requests
from cli import cli
from tests.score_matrix_test_helpers import LOT_NAME, write_case_inputs


def test_missing_material_requests_are_grouped_by_responsible_party(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    payload = json.loads((outline_dir / "missing_material_request.json").read_text(encoding="utf-8"))

    expected_groups = {
        "数据管理事业部",
        "安全事业部",
        "公司业绩/项目管理/相关业务部门",
        "知识产权/研发管理/相关业务部门",
        "供应商管理/商务支持部门",
        "技术方案编写负责人",
    }
    assert expected_groups.issubset(set(payload["grouped_requests"].keys()))
    assert payload["summary"]["request_to_count"] == 6


def test_missing_material_request_markdown_is_department_ready(tmp_path) -> None:
    outline_dir = write_case_inputs(tmp_path)

    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)
    markdown = (outline_dir / "missing_material_request.md").read_text(encoding="utf-8")

    assert "## 公司业绩/项目管理/相关业务部门" in markdown
    assert "请补充近5年技术开发或技术服务类公司业绩合同关键页" in markdown
    assert "不得替代为" in markdown
    assert "人员项目经验" in markdown


def test_inspect_missing_material_requests_service_and_cli_can_run(tmp_path) -> None:
    write_case_inputs(tmp_path)
    build_score_maximization_matrix(LOT_NAME, output_dir=tmp_path)

    inspected = inspect_missing_material_requests(LOT_NAME, output_dir=tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect-missing-material-requests", "--lot-name", LOT_NAME, "--output-dir", str(tmp_path)])

    assert inspected["files"]["json_exists"] is True
    assert inspected["files"]["markdown_exists"] is True
    assert inspected["summary"]["request_count"] >= 6
    assert result.exit_code == 0

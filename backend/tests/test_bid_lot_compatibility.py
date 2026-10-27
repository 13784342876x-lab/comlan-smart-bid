from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.response_outline_builder import build_response_outline
from app.score_table_locator import (
    BUSINESS_TEMPLATE_TABLE,
    TECHNICAL_TEMPLATE_TABLE,
    locate_score_table,
)
from tests.test_response_outline_builder import sample_metadata, sample_project


def test_no_lot_score_localization_is_scoped_to_selected_score_source() -> None:
    business_template = next(iter(BUSINESS_TEMPLATE_TABLE))
    technical_template = next(iter(TECHNICAL_TEMPLATE_TABLE))
    text = "\n".join(
        [
            "商务详细评审标准",
            business_template,
            "技术详细评审标准",
            technical_template,
            "分值权重构成 商务部分：20% 技术部分：50% 投标报价：30%",
        ]
    )

    result = locate_score_table(text, [], None)

    assert result["lot_name"] is None
    assert result["localization_scope"] == "selected_score_source"
    assert result["business_template_name"] == business_template
    assert result["technical_template_name"] == technical_template
    assert result["weights"] == {"business": 20, "technical": 50, "price": 30}


def test_no_lot_outline_keeps_null_business_value_and_uses_run_output_key(
    tmp_path: Path,
) -> None:
    project = deepcopy(sample_project())
    project["lot_name"] = None

    result = build_response_outline(
        project,
        metadata=sample_metadata(),
        output_dir=tmp_path,
        output_key="bid-no-lot-scope",
    )
    context_path = next(
        Path(item["path"])
        for item in result["files"]
        if item["kind"] == "technical_response_context"
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))

    assert result["lot_name"] is None
    assert context["lot_name"] is None
    assert Path(result["output_dir"]) == tmp_path / "bid-no-lot-scope" / "outline"
    assert "unknown_lot" not in str(context_path)

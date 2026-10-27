from __future__ import annotations

from pathlib import Path
from typing import Any

from app.bid_source_resolver import resolve_bid_sources


def _source(
    tmp_path: Path,
    file_id: str,
    name: str,
    text: str,
    *,
    tables: list[dict[str, Any]] | None = None,
    raw: bytes | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = tmp_path / name
    path.write_bytes(raw if raw is not None else text.encode("utf-8"))
    entry = {
        "file_id": file_id,
        "original_name": name,
        "path": str(path),
        "stored_path": str(path),
        "parsed_status": "success",
        "selected_as": [],
    }
    record = {
        "id": file_id,
        "original_name": name,
        "file_ext": path.suffix.lstrip("."),
        "parsed_text": text,
        "tables": tables or [],
        "category": "待分类",
        "tags": ["bid_pipeline_source"],
    }
    return entry, record


def test_resolver_folds_duplicates_translations_and_bidder_working_files(
    tmp_path: Path,
) -> None:
    rfp_text = (
        "Request for Proposal\nResponse Instructions\nSupplier Qualification\n"
        "Evaluation / Selection Process\nResponse Specifications\n"
        "Project code ABC-2026-001 contact rfp@example.test\n"
    ) * 120
    technical_text = (
        "The highlighted key features must be achieved as mandatory requirements.\n"
        "Category\tReference Product\tVendor Response\n"
    ) * 20
    definitions = [
        ("rfp", "RFP_Project_20260801.pdf", rfp_text, None, b"same-rfp-binary"),
        ("rfp_copy", "RFP_Project_20260801(1).pdf", rfp_text, None, b"same-rfp-binary"),
        ("rfp_translation", "RFP_Project_20260801_translated.docx", "项目方案征询书\n供应商须知", None, None),
        ("technical", "Appendix Technical Criteria.xlsx", technical_text, [{"rows": []}], None),
        (
            "technical_filled",
            "Appendix Technical Criteria -BidderCo.xlsx",
            technical_text.replace("Vendor Response", "BidderCo Response") + "\nfully supported",
            [{"rows": []}],
            None,
        ),
        ("working_seed", "BidderCo supplier suggestion.xlsx", "supplier contact working list", [{"rows": []}], None),
        ("working_parameters", "BidderCo PDF technical parameters.xlsx", "product capabilities", [{"rows": []}], None),
        ("terms", "General Terms and Conditions.pdf", "General Terms and Conditions\nContract obligations", None, None),
    ]
    entries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for file_id, name, text, tables, raw in definitions:
        entry, record = _source(
            tmp_path,
            file_id,
            name,
            text,
            tables=tables,
            raw=raw,
        )
        entries.append(entry)
        records.append(record)

    result = resolve_bid_sources(entries, {"files": records})
    files = {item["file_id"]: item for item in result["files"]}

    assert result["selection_status"] == "selected"
    assert result["tender_file_id"] == "rfp"
    assert result["score_file_id"] == "technical"
    assert files["rfp_copy"]["duplicate_of"] == "rfp"
    assert files["rfp_copy"]["document_role"] == "duplicate_copy"
    assert files["rfp_translation"]["derivative_of"] == "rfp"
    assert files["rfp_translation"]["document_role"] == "translated_copy"
    assert files["technical_filled"]["derivative_of"] == "technical"
    assert files["technical_filled"]["source_origin"] == "bidder_working_material"
    assert files["working_seed"]["included_for_requirements"] is False
    assert files["working_parameters"]["included_for_requirements"] is False
    assert files["working_parameters"]["included_for_writing_context"] is True
    assert files["technical"]["included_for_requirements"] is True
    assert files["terms"]["document_role"] == "contract_terms"


def test_resolver_keeps_anchor_candidates_when_manual_selection_is_required(
    tmp_path: Path,
) -> None:
    entries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for file_id, name, marker in (
        ("rfp_a", "RFP_Project_A.pdf", "ALPHA-2026-001"),
        ("rfp_b", "RFP_Project_B.pdf", "BETA-2026-002"),
    ):
        text = (
            "Request for Proposal\nResponse Instructions\nSupplier Qualification\n"
            "Evaluation / Selection Process\nResponse Specifications\n"
            + marker
            + "\n"
        ) * 120
        entry, record = _source(tmp_path, file_id, name, text)
        entries.append(entry)
        records.append(record)

    result = resolve_bid_sources(entries, {"files": records})

    assert result["selection_status"] == "ambiguous"
    assert result["tender_file_id"] is None
    assert "manual selection required" in result["selection_failure"]
    assert [item["file_id"] for item in result["anchor_candidates"]["tender"][:2]] == [
        "rfp_a",
        "rfp_b",
    ]
    assert all(
        {"file_id", "original_name", "role", "score", "reasons"} <= set(item)
        for item in result["anchor_candidates"]["tender"]
    )

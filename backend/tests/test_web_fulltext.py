from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.web.api import create_app
from app.web.fulltext_query_service import AuditedFulltextReadService
from app.web.project_resolver import ReadOnlyProjectResolver


BACKEND_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_ROOT = BACKEND_ROOT / "outputs"
PROJECT_ID = "hospital_hci_001_fulltext_real_20260806_02"
AUDITED_SHA = "7b2850515aeaecde6d29f8da2f30971fd81d1048533a050c1168fc5f8973a022"
TR003_FINDING = "FAI-8A16A33D982E3BF8"
TR003_PARAGRAPH = "PAR-7312FACF28A2735B"


def test_audited_fulltext_projection_uses_canonical_lineage_and_paragraph_identity() -> None:
    payload = AuditedFulltextReadService(ReadOnlyProjectResolver(OUTPUTS_ROOT)).get(PROJECT_ID)
    assert payload.version.audited_content_sha256 == AUDITED_SHA
    assert payload.document_id == f"technical-fulltext:{PROJECT_ID}"
    assert [chapter.chapter_id for chapter in payload.chapters] == [f"WC-{index:03d}" for index in range(1, 9)]
    assert sum(len(chapter.sections) for chapter in payload.chapters) == 66
    paragraphs = [paragraph for chapter in payload.chapters for section in chapter.sections for paragraph in section.paragraphs]
    assert len(paragraphs) == 285
    assert len({paragraph.paragraph_id for paragraph in paragraphs}) == 285
    assert all(paragraph.paragraph_id.startswith("PAR-") and len(paragraph.text_sha256) == 64 for paragraph in paragraphs)
    assert "D:\\" not in payload.model_dump_json()


def test_tr003_trace_distinguishes_current_response_and_recommended_target() -> None:
    payload = AuditedFulltextReadService(ReadOnlyProjectResolver(OUTPUTS_ROOT)).get(PROJECT_ID)
    section = next(section for chapter in payload.chapters for section in chapter.sections if section.section_id == "WC-005-11")
    paragraph = next(item for item in section.paragraphs if item.paragraph_id == TR003_PARAGRAPH)
    assert "TR-003" in paragraph.trace.requirement_ids
    assert TR003_FINDING in paragraph.trace.finding_ids
    assert TR003_FINDING in paragraph.trace.review_item_ids
    assert "FACT-013" in paragraph.trace.fact_refs
    review = next(item for item in payload.trace_index.reviews if item.trace_id == TR003_FINDING)
    assert [(item.section_id, item.paragraph_id) for item in review.response_locations] == [("WC-005-11", TR003_PARAGRAPH)]
    assert [(item.section_id, item.paragraph_id) for item in review.recommended_locations] == [("WC-005-04", None)]
    fact = next(item for item in payload.trace_index.dependencies if item.trace_id == "FACT-013")
    assert fact.status == "pending"


def test_scoring_trace_preserves_pending_evidence_and_tr018_long_locations() -> None:
    payload = AuditedFulltextReadService(ReadOnlyProjectResolver(OUTPUTS_ROOT)).get(PROJECT_ID)
    score = next(item for item in payload.trace_index.scores if item.trace_id == "SCORE-TR-022-R004")
    assert score.score_range.minimum == 0
    assert score.score_range.maximum == 2
    assert score.pending_evidence[0].status == "query_not_executed"
    assert score.pending_evidence[0].status_label == "尚未查询"
    assert {item.section_id for item in score.response_locations} >= {"S3", "S4", "WC-007-02"}

    requirement = next(item for item in payload.trace_index.requirements if item.trace_id == "TR-018")
    assert len(requirement.response_locations) == 16
    assert sum(1 for item in requirement.response_locations if item.paragraph_id) >= 10
    assert any(
        paragraph.content_type == "table"
        for chapter in payload.chapters
        for section in chapter.sections
        for paragraph in section.paragraphs
        if section.section_id == "WC-007-02"
    )


def test_fulltext_http_smoke_hides_paths() -> None:
    response = TestClient(create_app(OUTPUTS_ROOT)).get(f"/api/projects/{PROJECT_ID}/fulltext")
    assert response.status_code == 200
    assert response.json()["version"]["audited_content_sha256"] == AUDITED_SHA
    assert "D:\\" not in response.text
    assert "/backend/outputs/" not in response.text

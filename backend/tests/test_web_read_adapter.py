from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.web.api import create_app
from app.web.project_resolver import ReadOnlyProjectResolver
from app.web.query_service import ProjectReadService


BACKEND_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_ROOT = BACKEND_ROOT / "outputs"
REAL_PROJECT_ID = "hospital_hci_001_fulltext_real_20260806_02"


def test_project_resolver_discovers_only_complete_canonical_projects(tmp_path: Path) -> None:
    complete = tmp_path / "complete"
    incomplete = tmp_path / "incomplete"
    required = [
        "fulltext/technical_fulltext.json",
            "fulltext/technical_fulltext.commit.json",
            "fulltext/hygiene/technical_fulltext.cleaned.json",
        "fulltext/audit/technical_fulltext.audit.json",
        "fulltext/audit/technical_fulltext.scoring_assessment.json",
        "fulltext/review/technical_fulltext.review_package.json",
        "fulltext/review/technical_fulltext.human_review.json",
        "fulltext/review/technical_fulltext.review_finalization.json",
    ]
    for relative in required:
        target = complete / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    incomplete.mkdir()

    resolver = ReadOnlyProjectResolver(tmp_path)

    assert [project.project_id for project in resolver.list_projects()] == ["complete"]
    assert resolver.resolve("complete").root == complete.resolve()


def test_real_stage12_projection_preserves_business_semantics_and_hides_paths() -> None:
    service = ProjectReadService(ReadOnlyProjectResolver(OUTPUTS_ROOT))

    projects = service.list_projects()
    assert REAL_PROJECT_ID in [project.project_id for project in projects.projects]

    overview = service.overview(REAL_PROJECT_ID)
    assert overview.actionability_counts == {
        "must_decide": 10,
        "verification": 32,
        "no_action": 3,
        "informational": 1,
    }
    assert overview.scoring.technical_score_max == 70
    assert overview.scoring.confirmed == 0
    assert overview.scoring.estimated_range.minimum == 12
    assert overview.scoring.estimated_range.maximum == 20
    assert overview.scoring.pending_exposure == 50
    assert overview.revision_ready is False

    review = service.review(REAL_PROJECT_ID)
    assert {group.key: len(group.items) for group in review.groups} == overview.actionability_counts
    assert review.read_only is False
    assert len(review.fact_confirmations) == 52
    assert len(review.material_verifications) == 22
    assert all(item.upstream_status_label == "尚未查询" for item in review.material_verifications)

    scoring = service.scoring(REAL_PROJECT_ID)
    assert len(scoring.items) == 15
    assert scoring.item_counts == {"confirmed": 0, "estimated": 4, "pending": 11}

    facts = service.facts_materials(REAL_PROJECT_ID)
    assert facts.status_counts["query_not_executed"] == 22
    assert all(
        item.status_label == "尚未查询"
        for item in facts.items
        if item.status == "query_not_executed"
    )
    unresolved = [item for item in facts.items if not item.semantic_resolved]
    assert len(unresolved) == 32
    assert all(item.title == "待确认信息（名称未记录）" for item in unresolved)
    assert all("PH-FIELD" not in item.title and "PH-FIELD" not in item.description for item in unresolved)
    assert all(item.source_label == "人工审核" for item in unresolved)
    assert all(item.action_url.endswith(f"/review?item={item.trace_id}") for item in unresolved)

    requirements = service.requirements(REAL_PROJECT_ID)
    assert len(requirements.requirements) == 31

    tr003 = service.requirement_detail(REAL_PROJECT_ID, "TR-003")
    assert tr003.requirement.response_status == "missing"
    assert tr003.requirement.source_reference.page_start == 10
    assert tr003.requirement.source_reference.row_or_cell == "paragraph_131"
    assert any(location.section_id == "WC-005-11" for location in tr003.requirement.response_locations)
    assert {item.trace_id for item in tr003.findings} >= {
        "FAI-8A16A33D982E3BF8",
        "FSF-A6B496F300D1758B",
    }
    assert [(item.trace_id, item.status) for item in tr003.fact_dependencies] == [
        ("FACT-013", "pending")
    ]
    assert len(tr003.review_states) == 1
    assert tr003.review_states[0].actionability == "must_decide"
    assert tr003.review_states[0].release_blocking is True
    assert tr003.review_states[0].current_decision == "pending"
    assert tr003.review_states[0].revision_locations[0].section_id == "WC-005-04"

    evidence_requirement = service.requirement_detail(REAL_PROJECT_ID, "TR-015")
    assert {item.trace_id for item in evidence_requirement.scoring_impacts} >= {
        "SCORE-TR-022-R004",
        "SCORE-TR-022-R005",
    }
    product_query = next(
        item for item in evidence_requirement.material_dependencies if item.trace_id == "KBQ-003"
    )
    assert product_query.status == "query_not_executed"
    assert product_query.status_label == "尚未查询"

    technical_requirement = service.requirement_detail(REAL_PROJECT_ID, "TR-018")
    assert technical_requirement.requirement.source_reference.source_kind == "table"
    assert technical_requirement.requirement.source_reference.table_name == "采购清单"
    assert technical_requirement.requirement.source_reference.table_range == "A1:H211"
    assert len(technical_requirement.requirement.requirement_text) > 1000

    serialized = "\n".join(
        value.model_dump_json()
        for value in (
            projects,
            overview,
            review,
            scoring,
            facts,
            requirements,
            tr003,
            evidence_requirement,
            technical_requirement,
            service.versions(REAL_PROJECT_ID),
        )
    )
    assert "D:\\\\comlan智标" not in serialized
    assert "D:/comlan智标" not in serialized
    assert "backend\\outputs" not in serialized


def test_read_api_smoke_against_real_artifacts() -> None:
    client = TestClient(create_app(OUTPUTS_ROOT))
    routes = [
        "/api/projects",
        f"/api/projects/{REAL_PROJECT_ID}/overview",
        f"/api/projects/{REAL_PROJECT_ID}/review",
        f"/api/projects/{REAL_PROJECT_ID}/requirements",
        f"/api/projects/{REAL_PROJECT_ID}/requirements/TR-003",
        f"/api/projects/{REAL_PROJECT_ID}/scoring",
        f"/api/projects/{REAL_PROJECT_ID}/facts-materials",
        f"/api/projects/{REAL_PROJECT_ID}/fulltext",
        f"/api/projects/{REAL_PROJECT_ID}/versions",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, (route, response.text)
        assert "D:\\comlan智标" not in response.text

    response = client.get("/api/projects/not-a-project/overview")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"

    response = client.get(f"/api/projects/{REAL_PROJECT_ID}/requirements/TR-NOT-FOUND")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "requirement_not_found"

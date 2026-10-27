from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_default_review_components_do_not_render_internal_identity_fields() -> None:
    detail = _source("src/components/domain/review/ReviewDetail.tsx")
    location = _source("src/components/domain/ResponseLocation.tsx")
    workspace = _source("src/pages/review/ReviewWorkspacePage.tsx")
    projection = _source("src/utils/userFacingText.ts")

    assert ">{item.trace_id}<" not in detail
    assert "{value.technical_ref}" not in location
    assert "traceId: item.trace_id" in workspace  # retained for identity/navigation only
    assert "internalIdentityPattern" in projection
    assert "placeholderTokenPattern" in projection


def test_fact_review_renders_unique_task_impact_separately_from_task_count() -> None:
    detail = _source("src/components/domain/review/ReviewDetail.tsx")
    workspace = _source("src/pages/review/ReviewWorkspacePage.tsx")
    assert "item.affected_location_count" in detail
    assert "item.response_locations.map" in detail
    assert "data.fact_confirmations.length" in workspace

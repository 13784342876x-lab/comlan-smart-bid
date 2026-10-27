from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_ai_modify_uses_saved_candidate_latest_etag_and_polling() -> None:
    editor = _source("src/components/domain/review/IssueDecisionEditor.tsx")
    view_model = _source("src/components/domain/review/issueDecisionViewModel.ts")
    api = _source("src/api/review.ts")

    assert "canRunAIRevision(item)" in editor
    assert 'item.current_decision === "modified"' in view_model
    assert 'item.current_decision === "accepted"' in view_model
    assert "item.machine_revision_available" in view_model
    assert "item.revision_candidate_ready" in view_model
    assert "item.risk_acceptance_ref" in view_model
    assert "!savedForAI || dirty || operationActive || aiStarting" in editor
    assert "const latest = await getVersionedReview" in editor
    assert "latest.etag" in editor
    assert "saved.trace_id" in editor
    assert "saved.source_type" in editor
    assert "window.setInterval" in editor
    assert 'headers: { "Content-Type": "application/json", "If-Match": token }' in api


def test_ai_modify_progress_failure_and_completion_refresh_contract() -> None:
    editor = _source("src/components/domain/review/IssueDecisionEditor.tsx")
    workspace = _source("src/pages/review/ReviewWorkspacePage.tsx")
    service = _source("backend/app/web/revision_operation_service.py")

    assert 'next?.status === "completed"' in editor
    assert "await getAuditedFulltext" in editor
    assert "projectLoad.reload()" in editor
    assert 'new Event("stage13-revision-completed")' in editor
    assert 'next?.status === "failed"' in editor
    assert "error_message" in editor
    assert "stage13-revision-completed" in workspace
    assert "正在修改标书…" in service
    assert "正在重新审核…" in service
    assert "正在更新结果…" in service

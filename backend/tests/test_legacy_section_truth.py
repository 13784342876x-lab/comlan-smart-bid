import pytest
from app import section_service, review_service


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected model call")
    monkeypatch.setattr(section_service, "run_agent", forbidden)
    monkeypatch.setattr(review_service, "run_agent", forbidden)


def test_title_only_is_not_reviewed():
    result = review_service.ReviewService().review_section("title")
    assert result["status"] == "not_reviewed"
    assert result["risks"] == []


def test_review_actual_inputs_and_partial_scope(monkeypatch):
    def run(name, data, **kwargs):
        assert data["section_text"] == "actual text"
        assert data["requirements"] == "requirements"
        return {"status": "success", "result": {"risks": []}}
    monkeypatch.setattr(review_service, "run_agent", run)
    result = review_service.ReviewService().review_section("s", "actual text", "requirements")
    assert result["status"] == "reviewed_partial_scope"
    assert result["release_ready"] is False


def test_invalid_review_retains_output(monkeypatch):
    monkeypatch.setattr(review_service, "run_agent", lambda *a, **k: {
        "status": "success", "result": {"partial": "actual output"}})
    result = review_service.ReviewService().review_section("s", "text", "requirements")
    assert result["status"] == "failed"
    assert result["run_result"]["result"]["partial"] == "actual output"


def test_failed_generation_keeps_content_without_fake_citations(monkeypatch):
    def run(name, data, **kwargs):
        assert data["evidence_refs"] == []
        return {"status": "failed", "result": {"content": "actual partial"}}
    monkeypatch.setattr(section_service, "run_agent", run)
    result = section_service.SectionService().generate_section("s", "q")
    assert result["content"] == "actual partial"
    assert result["status"] == "failed"
    assert result["used_kb_files"] == []
    assert result["retrieval_status"] == "unavailable"


def test_empty_output_not_success(monkeypatch):
    monkeypatch.setattr(section_service, "run_agent", lambda *a, **k: {"status": "success", "result": {}})
    assert section_service.SectionService().generate_section("s", "q")["status"] == "failed"


def test_cli_without_body_outputs_status():
    import cli
    from typer.testing import CliRunner
    result = CliRunner().invoke(cli.cli, ["review-section", "--section", "test"])
    assert result.exit_code == 1
    assert "not_reviewed" in result.stdout

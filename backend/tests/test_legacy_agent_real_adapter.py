from types import SimpleNamespace

import pytest

from app import agent_runner, agent_draft_generator
from app.llm_errors import ModelConfigurationError, ModelJsonParseError


@pytest.fixture(autouse=True)
def offline_boundary(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network client must be replaced in this test")
    monkeypatch.setattr(agent_runner, "get_llm_client", forbidden)
    monkeypatch.setattr(agent_runner, "load_agent_config", lambda *a, **k: {
        "exists": True, "has_system_prompt": True, "system_prompt": "write",
        "output_schema": {"required": ["content", "evidence"]},
    })


def test_default_uses_profile_and_retains_invalid_result(monkeypatch):
    def factory(provider, **kwargs):
        assert provider is None
        assert kwargs["profile_name"] == "selected"
        return SimpleNamespace(generate_structured=lambda **k: SimpleNamespace(
            parsed_output={"content": "real partial draft"},
            as_dict=lambda **k: {"provider": "configured", "request_id": "r1"},
        ))
    monkeypatch.setattr(agent_runner, "get_llm_client", factory)
    result = agent_runner.run_agent("writer", {}, model_profile="selected")
    assert result["status"] == "failed"
    assert result["result"]["content"] == "real partial draft"
    assert result["is_mock"] is False
    assert result["model_call"]["request_id"] == "r1"


@pytest.mark.parametrize("error", [
    ModelConfigurationError("missing profile"),
    ModelJsonParseError("invalid JSON", raw_content="unfinished real output"),
])
def test_failure_does_not_fallback_to_mock(monkeypatch, error):
    calls = []
    def factory(*args, **kwargs):
        calls.append(args)
        raise error
    monkeypatch.setattr(agent_runner, "get_llm_client", factory)
    result = agent_runner.run_agent("writer", {})
    assert result["status"] == "failed"
    assert result["result"] == {}
    assert len(calls) == 1
    assert result["raw_output"] == (getattr(error, "raw_content", "") or "")


def test_failed_draft_keeps_returned_content(monkeypatch):
    monkeypatch.setattr(agent_draft_generator, "run_agent", lambda *a, **k: {
        "status": "failed", "result": {"content": "actual draft"},
        "errors": ["missing evidence"], "is_mock": False,
    })
    section = agent_draft_generator.build_agent_draft_section({
        "writer_agent": "technical_writer", "draft_text": "old skeleton",
    }, {}, None, "agents")
    assert section["status"] == "failed"
    assert section["draft_text"] == "actual draft"
    assert section["errors"] == ["missing evidence"]


def test_success_is_not_labelled_mock(monkeypatch):
    monkeypatch.setattr(agent_draft_generator, "run_agent", lambda *a, **k: {
        "status": "success", "result": {"content": "actual draft"}, "is_mock": False,
    })
    section = agent_draft_generator.build_agent_draft_section({
        "writer_agent": "technical_writer",
    }, {}, None, "agents")
    assert section["status"] == "generated"
    assert agent_draft_generator.build_summary([section])["agent_generated_sections"] == 1


def test_cli_saves_failed_drafts_before_nonzero_exit(monkeypatch):
    import cli
    from typer.testing import CliRunner

    saved = []
    result = {"summary": {"failed_sections": 1}, "agent_draft_sections": [
        {"status": "failed", "draft_text": "partial draft"},
    ]}
    monkeypatch.setattr(cli, "load_tender_projects", lambda: {})
    monkeypatch.setattr(cli, "find_project_by_lot_name", lambda *a: {"writing_plan": {}})
    monkeypatch.setattr(cli, "generate_agent_drafts_for_project", lambda *a, **k: result)
    monkeypatch.setattr(cli, "save_agent_drafts_to_project", lambda lot, data: saved.append(data))
    response = CliRunner().invoke(cli.cli, ["generate-agent-drafts", "--lot-name", "test", "--save"])
    assert response.exit_code == 1
    assert saved == [result]
    assert "partial draft" in response.stdout

import json
from pathlib import Path

from app.agent_runner import load_agent_config, run_agent


def test_load_agent_config_reads_business_writer():
    config = load_agent_config("business_writer")

    assert config["exists"] is True
    assert config["has_system_prompt"] is True
    assert config["has_output_schema"] is True
    assert "商务标" in config["system_prompt"]


def test_load_agent_config_reads_technical_writer():
    config = load_agent_config("technical_writer")

    assert config["exists"] is True
    assert config["has_system_prompt"] is True
    assert config["has_output_schema"] is True
    assert "技术标" in config["system_prompt"]


def test_missing_agent_returns_failed_without_unhandled_exception():
    result = run_agent("missing_agent_for_test", {"task_name": "测试"})

    assert result["status"] == "failed"
    assert result["errors"]


def test_run_agent_mock_provider_returns_success():
    result = run_agent(
        "technical_writer",
        {
            "task_name": "服务方案",
            "section_type": "writing_required",
            "outline": ["服务目标理解", "总体实施思路"],
            "evidence_refs": [],
            "missing_materials": [],
        },
        provider="mock",
    )

    assert result["status"] == "success"
    assert result["provider"] == "mock"
    assert result["result"]["section_name"] == "服务方案"
    assert "MockLLMClient" in result["result"]["content"]


def test_output_schema_missing_is_graceful(tmp_path):
    agent_dir = tmp_path / "agents" / "schema_missing_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "system.txt").write_text("测试 agent", encoding="utf-8")

    result = run_agent(
        "schema_missing_agent",
        {"task_name": "测试章节"},
        agents_dir=str(tmp_path / "agents"),
        provider="mock",
    )

    assert result["status"] == "success"
    assert "schema_missing" in result["notes"]


def test_agent_runner_output_has_no_bid_decision_language():
    result = run_agent("business_writer", {"task_name": "商务响应", "outline": ["材料引用"]}, provider="mock")
    text = json.dumps(result, ensure_ascii=False).lower()

    forbidden = ["能投", "不能投", "建议投标", "不建议投标", "是否投标", "go/no-go"]
    assert not any(term.lower() in text for term in forbidden)

"""Agent tool routing eval의 fixture, grader, artifact를 검증합니다."""

import asyncio

import pytest

from agent.financial_research_agent import FinancialResearchAgent
from evaluate_agent_tools import (
    DEFAULT_FIXTURE,
    RecordedAgentClient,
    SideEffectFreeEvalTools,
    parse_args,
    run_eval,
)
from evaluation.agent_tool_eval import (
    AgentToolEvalArtifactStore,
    AgentToolEvalSuite,
    load_agent_eval_cases,
)


def test_recorded_agent_tool_eval_passes_all_cases():
    cases = load_agent_eval_cases(DEFAULT_FIXTURE)
    tools = SideEffectFreeEvalTools()

    result = asyncio.run(
        AgentToolEvalSuite().run(
            cases,
            agent_factory=lambda case: FinancialResearchAgent(
                client=RecordedAgentClient(case),
                model="recorded-agent-fixture-v1",
                tools=tools,
            ),
            mode="recorded_fixture",
            model="recorded-agent-fixture-v1",
            fixture_path=DEFAULT_FIXTURE,
        )
    )

    assert len(cases) == 4
    assert result.all_passed is True
    assert result.pass_rate == 1.0
    assert result.results[-1].actual_status == "refused"
    assert result.results[-1].actual_tools == ()


def test_agent_eval_artifacts_are_saved(tmp_path):
    args = parse_args(
        ["--mode", "recorded", "--output-dir", str(tmp_path / "cli")]
    )
    result = asyncio.run(run_eval(args))
    store = AgentToolEvalArtifactStore(tmp_path / "artifacts")

    json_path, markdown_path = store.save(result)

    assert json_path.exists()
    assert markdown_path.exists()
    assert "recorded_fixture" in json_path.read_text(encoding="utf-8")
    assert "4/4" in markdown_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="이미 존재"):
        store.save(result)


def test_live_agent_eval_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = parse_args(
        ["--mode", "live", "--output-dir", str(tmp_path)]
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        asyncio.run(run_eval(args))

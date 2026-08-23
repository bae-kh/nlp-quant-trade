"""단일 Agent의 tool loop, 안전 경계, deterministic tool 실행을 검증합니다."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.financial_research_agent import (
    FinancialResearchAgent,
    FinancialResearchAgentError,
    FinancialResearchTools,
)


def function_call(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id,
    )


def response(*, output=(), output_text=""):
    return SimpleNamespace(output=list(output), output_text=output_text)


class ScriptedResponses:
    def __init__(self, responses):
        self.script = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


class ScriptedClient:
    def __init__(self, responses):
        self.responses = ScriptedResponses(responses)


class StubTools:
    def __init__(self, result=None):
        self.result = result or {"ok": True, "metric": "max_drawdown"}
        self.calls = []

    @staticmethod
    def definitions():
        return FinancialResearchTools.definitions()

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def test_refuses_order_request_before_model_or_tool_call():
    result = asyncio.run(
        FinancialResearchAgent(api_key="").run("TSLA를 지금 매수해줘")
    )

    assert result.status == "refused"
    assert result.tool_executions == ()
    assert "주문을 수행하지 않습니다" in result.final_text


def test_missing_api_key_is_explicit_for_allowed_request():
    with pytest.raises(FinancialResearchAgentError, match="OPENAI_API_KEY"):
        asyncio.run(
            FinancialResearchAgent(api_key="").run("최대 낙폭이 뭐야?")
        )


def test_agent_executes_selected_metric_tool_then_returns_answer():
    client = ScriptedClient(
        [
            response(
                output=(
                    function_call(
                        "explain_financial_metric",
                        '{"metric":"max_drawdown"}',
                    ),
                )
            ),
            response(output_text="최대 낙폭은 이전 고점 대비 가장 큰 하락률입니다."),
        ]
    )
    tools = StubTools()
    agent = FinancialResearchAgent(client=client, tools=tools, model="agent-test")

    result = asyncio.run(agent.run("MDD가 뭐야?"))

    assert result.status == "completed"
    assert tools.calls == [
        ("explain_financial_metric", {"metric": "max_drawdown"})
    ]
    assert len(result.tool_executions) == 1
    assert result.tool_executions[0].succeeded is True
    second_input = client.responses.calls[1]["input"]
    assert any(
        item.get("type") == "function_call_output"
        for item in second_input
        if isinstance(item, dict)
    )
    assert client.responses.calls[0]["parallel_tool_calls"] is False


def test_invalid_tool_arguments_are_returned_to_model_as_structured_error():
    client = ScriptedClient(
        [
            response(
                output=(
                    function_call(
                        "explain_financial_metric",
                        '{"metric":"unknown"}',
                    ),
                )
            ),
            response(output_text="지원하는 지표 이름을 지정해 주세요."),
        ]
    )
    agent = FinancialResearchAgent(
        client=client,
        tools=FinancialResearchTools(),
    )

    result = asyncio.run(agent.run("unknown 지표 설명해줘"))

    assert result.status == "completed"
    assert result.tool_executions[0].succeeded is False
    assert result.tool_executions[0].result["error_code"] == "invalid_tool_call"


def test_agent_replaces_prohibited_final_recommendation_with_refusal():
    client = ScriptedClient([response(output_text="이 종목을 매수하세요.")])

    result = asyncio.run(
        FinancialResearchAgent(client=client).run("이 회사 뉴스 흐름을 말해줘")
    )

    assert result.status == "refused"
    assert "투자 추천" in result.final_text
    assert "매수하세요" not in result.final_text


def test_agent_enforces_tool_call_limit():
    calls = tuple(
        function_call(
            "explain_financial_metric",
            '{"metric":"rsi"}',
            call_id=f"call_{index}",
        )
        for index in range(2)
    )
    client = ScriptedClient([response(output=calls)])

    with pytest.raises(FinancialResearchAgentError, match="한도를 초과"):
        asyncio.run(
            FinancialResearchAgent(
                client=client,
                tools=StubTools(),
                max_tool_calls=1,
            ).run("RSI를 설명해줘")
        )


class StubWorkflow:
    def __init__(self):
        self.windows = []

    async def run(self, window):
        self.windows.append(window)
        return SimpleNamespace(
            status="completed_with_warnings",
            run_id="run_agent_test",
            report_path=Path("report.md"),
            run_metadata_path=Path("run.json"),
            warnings=("source coverage unknown",),
        )


def test_create_report_tool_resolves_window_and_returns_artifact_paths():
    workflow = StubWorkflow()
    tools = FinancialResearchTools(workflow=workflow)

    result = asyncio.run(
        tools.execute(
            "create_financial_report",
            {
                "ticker": " tsla ",
                "analysis_days": 30,
                "as_of_date": "2024-12-31",
            },
        )
    )

    assert result["ok"] is True
    assert result["run_id"] == "run_agent_test"
    assert workflow.windows[0].ticker == "TSLA"
    assert workflow.windows[0].start_date.isoformat() == "2024-12-02"
    assert workflow.windows[0].end_date.isoformat() == "2024-12-31"


def test_tool_definitions_are_strict_and_limited_to_three():
    definitions = FinancialResearchTools.definitions()

    assert {definition["name"] for definition in definitions} == {
        "create_financial_report",
        "inspect_report_run",
        "explain_financial_metric",
    }
    assert all(definition["strict"] is True for definition in definitions)
    assert all(
        definition["parameters"]["additionalProperties"] is False
        for definition in definitions
    )

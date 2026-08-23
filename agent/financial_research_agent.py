"""제한된 도구로 금융 리포트 workflow를 조정하는 단일 Agent입니다."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workflow.analysis_window import AnalysisRequest, resolve_analysis_window
from workflow.financial_reporting_workflow import (
    FinancialReportingWorkflow,
    FinancialReportingWorkflowError,
)
from workflow.run_tracking import RunMetadataStore


AgentStatus = Literal["completed", "refused"]
ToolName = Literal[
    "create_financial_report",
    "inspect_report_run",
    "explain_financial_metric",
]
MetricName = Literal[
    "period_return",
    "annualized_volatility",
    "max_drawdown",
    "rsi",
    "macd",
    "benchmark_difference",
]


class CreateFinancialReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=20)
    analysis_days: int = Field(ge=1, le=3_650)
    as_of_date: date | None


class InspectReportRunArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^run_[A-Za-z0-9._-]+$")


class ExplainFinancialMetricArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: MetricName


class AgentToolExecution(BaseModel):
    """모델의 도구 선택과 실제 실행 결과를 감사 가능하게 남깁니다."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: ToolName
    arguments: dict[str, Any]
    succeeded: bool
    result: dict[str, Any]


class FinancialResearchAgentResult(BaseModel):
    """Agent 최종 답변과 tool trace입니다."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    status: AgentStatus
    model: str
    user_request: str
    final_text: str
    tool_executions: tuple[AgentToolExecution, ...] = ()


class FinancialResearchAgentError(RuntimeError):
    """Agent 호출·프로토콜·반복 제한이 실패했을 때 발생합니다."""


class FinancialResearchTools:
    """Agent가 접근할 수 있는 최소한의 application 도구 집합입니다."""

    METRIC_GLOSSARY: dict[MetricName, dict[str, str]] = {
        "period_return": {
            "korean_name": "기간 수익률",
            "meaning": "분석 구간의 첫 종가 대비 마지막 종가 변화율입니다.",
            "caution": "과거 구간의 변화이며 미래 수익을 뜻하지 않습니다.",
        },
        "annualized_volatility": {
            "korean_name": "연환산 변동성",
            "meaning": "일별 수익률의 흔들림을 연간 기준으로 환산한 값입니다.",
            "caution": "방향이 아니라 변동 크기를 나타냅니다.",
        },
        "max_drawdown": {
            "korean_name": "최대 낙폭(MDD)",
            "meaning": "분석 구간에서 이전 고점 대비 가장 크게 하락한 비율입니다.",
            "caution": "관측한 구간에 따라 값이 달라집니다.",
        },
        "rsi": {
            "korean_name": "RSI(14)",
            "meaning": "최근 가격 상승과 하락의 상대적 강도를 나타내는 보조 지표입니다.",
            "caution": "단독 매수·매도 신호로 사용하지 않습니다.",
        },
        "macd": {
            "korean_name": "MACD difference",
            "meaning": "단기·장기 이동평균 관계와 signal line 차이를 나타냅니다.",
            "caution": "후행 지표이며 미래 방향을 보장하지 않습니다.",
        },
        "benchmark_difference": {
            "korean_name": "Benchmark 대비 차이",
            "meaning": "같은 실제 거래일 구간의 대상 종목 수익률에서 SPY 수익률을 뺀 값입니다.",
            "caution": "위험 조정 성과나 투자 우수성을 뜻하지 않습니다.",
        },
    }

    ARGUMENT_MODELS = {
        "create_financial_report": CreateFinancialReportArgs,
        "inspect_report_run": InspectReportRunArgs,
        "explain_financial_metric": ExplainFinancialMetricArgs,
    }

    def __init__(
        self,
        *,
        workflow: FinancialReportingWorkflow | None = None,
        run_metadata_store: RunMetadataStore | None = None,
    ) -> None:
        self.workflow = workflow or FinancialReportingWorkflow()
        self.run_metadata_store = run_metadata_store or RunMetadataStore()

    @classmethod
    def definitions(cls) -> list[dict[str, Any]]:
        descriptions = {
            "create_financial_report": (
                "요청한 ticker와 달력일 기간으로 검증된 금융 Markdown 리포트를 "
                "생성합니다. 투자 추천이나 주문에는 사용하지 않습니다."
            ),
            "inspect_report_run": (
                "run_id로 이전 리포트 실행의 단계별 상태와 artifact 경로를 조회합니다."
            ),
            "explain_financial_metric": (
                "프로젝트가 계산하는 금융 지표의 정의와 해석 주의점을 설명합니다."
            ),
        }
        return [
            {
                "type": "function",
                "name": name,
                "description": descriptions[name],
                "parameters": argument_model.model_json_schema(),
                "strict": True,
            }
            for name, argument_model in cls.ARGUMENT_MODELS.items()
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        argument_model = self.ARGUMENT_MODELS.get(name)
        if argument_model is None:
            raise ValueError(f"허용되지 않은 Agent tool입니다: {name}")
        parsed = argument_model.model_validate(arguments)
        if name == "create_financial_report":
            return await self._create_financial_report(parsed)
        if name == "inspect_report_run":
            return self._inspect_report_run(parsed)
        if name == "explain_financial_metric":
            return self._explain_financial_metric(parsed)
        raise AssertionError("unreachable tool dispatch")

    async def _create_financial_report(
        self,
        arguments: CreateFinancialReportArgs,
    ) -> dict[str, Any]:
        window = resolve_analysis_window(
            AnalysisRequest(
                ticker=arguments.ticker,
                analysis_days=arguments.analysis_days,
                as_of_date=arguments.as_of_date,
            )
        )
        try:
            result = await self.workflow.run(window)
        except FinancialReportingWorkflowError as exc:
            return {
                "ok": False,
                "error_code": "financial_report_failure",
                "message": str(exc),
                "run_id": exc.run_id,
                "run_metadata_path": (
                    str(exc.run_metadata_path)
                    if exc.run_metadata_path is not None
                    else None
                ),
            }
        return {
            "ok": True,
            "status": result.status,
            "run_id": result.run_id,
            "report_path": str(result.report_path),
            "run_metadata_path": str(result.run_metadata_path),
            "warnings": result.warnings,
        }

    def _inspect_report_run(
        self,
        arguments: InspectReportRunArgs,
    ) -> dict[str, Any]:
        metadata = self.run_metadata_store.load(arguments.run_id)
        return {
            "ok": True,
            "run_id": metadata.run_id,
            "status": metadata.status,
            "ticker": metadata.ticker,
            "requested_period": {
                "start_date": metadata.requested_start_date.isoformat(),
                "end_date": metadata.requested_end_date.isoformat(),
            },
            "duration_ms": metadata.duration_ms,
            "stages": [
                {
                    "name": stage.name,
                    "status": stage.status,
                    "duration_ms": stage.duration_ms,
                    "error_code": stage.error_code,
                }
                for stage in metadata.stages
            ],
            "artifacts": metadata.artifacts.model_dump(mode="json"),
            "warnings": metadata.warnings,
            "error_code": metadata.error_code,
        }

    def _explain_financial_metric(
        self,
        arguments: ExplainFinancialMetricArgs,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "metric": arguments.metric,
            **self.METRIC_GLOSSARY[arguments.metric],
        }


class FinancialResearchAgent:
    """도구 선택은 LLM, 계산과 실행은 검증된 Python workflow에 맡깁니다."""

    DEFAULT_MODEL = "gpt-4o-mini"
    MAX_TOOL_CALLS = 3
    MAX_TURNS = 4
    REFUSAL_TEXT = (
        "이 Agent는 투자 추천이나 매수·매도 주문을 수행하지 않습니다. "
        "가격·뉴스 기반 연구 리포트 생성, 실행 상태 조회, 지표 설명만 지원합니다."
    )
    SYSTEM_INSTRUCTIONS = """You are a financial reporting workflow assistant.

Boundaries:
1. You do not recommend investments and never place, simulate, or claim to place buy/sell orders.
2. Use create_financial_report only when the user explicitly asks to create a report.
3. Use inspect_report_run only when the user provides a run_id and asks about that run.
4. Use explain_financial_metric for definitions of metrics used by this project.
5. Never calculate financial metrics yourself. Treat tool results as the only source for run data and paths.
6. Clearly distinguish unavailable data from neutral sentiment.
7. Answer in Korean, concisely, and state that outputs are not investment advice when relevant.
8. Use at most the tools needed for the request and do not repeat a successful tool call.
"""
    PROHIBITED_REQUEST_PATTERNS = (
        re.compile(r"(매수|매도).*(해\s*줘|해줘|주문|추천)"),
        re.compile(r"(종목|주식).*(추천해\s*줘|추천해줘|추천)"),
        re.compile(r"\b(buy|sell)\b.*\b(stock|shares?|now|order)\b", re.I),
    )
    PROHIBITED_OUTPUT_PATTERNS = (
        re.compile(r"(매수|매도)(하세요|하라|를 추천)"),
        re.compile(r"\b(buy|sell)\s+now\b", re.I),
    )

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        tools: FinancialResearchTools | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
        max_turns: int = MAX_TURNS,
    ) -> None:
        if not model.strip():
            raise ValueError("model cannot be empty")
        if max_tool_calls <= 0 or max_turns <= 0:
            raise ValueError("Agent limits must be greater than 0")
        resolved_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.client = client or (AsyncOpenAI(api_key=resolved_key) if resolved_key else None)
        self.model = model.strip()
        self.tools = tools or FinancialResearchTools()
        self.max_tool_calls = max_tool_calls
        self.max_turns = max_turns

    async def run(self, user_request: str) -> FinancialResearchAgentResult:
        normalized_request = user_request.strip()
        if not normalized_request:
            raise ValueError("user_request cannot be empty")
        if self._is_prohibited_request(normalized_request):
            return FinancialResearchAgentResult(
                status="refused",
                model=self.model,
                user_request=normalized_request,
                final_text=self.REFUSAL_TEXT,
            )
        if self.client is None:
            raise FinancialResearchAgentError(
                "OPENAI_API_KEY가 없어 Financial Research Agent를 실행할 수 없습니다."
            )

        input_items: list[Any] = [
            {"role": "user", "content": normalized_request}
        ]
        executions: list[AgentToolExecution] = []

        for _turn in range(self.max_turns):
            try:
                response = await self.client.responses.create(
                    model=self.model,
                    instructions=self.SYSTEM_INSTRUCTIONS,
                    input=input_items,
                    tools=self.tools.definitions(),
                    tool_choice="auto",
                    parallel_tool_calls=False,
                )
            except Exception as exc:
                raise FinancialResearchAgentError(
                    f"Agent 모델 호출에 실패했습니다: {type(exc).__name__}: {exc}"
                ) from exc

            output_items = list(self._value(response, "output", []) or [])
            function_calls = [
                item
                for item in output_items
                if self._value(item, "type") == "function_call"
            ]
            if not function_calls:
                final_text = str(self._value(response, "output_text", "")).strip()
                if not final_text:
                    raise FinancialResearchAgentError(
                        "Agent가 최종 답변이나 tool call을 반환하지 않았습니다."
                    )
                if self._contains_prohibited_output(final_text):
                    return FinancialResearchAgentResult(
                        status="refused",
                        model=self.model,
                        user_request=normalized_request,
                        final_text=self.REFUSAL_TEXT,
                        tool_executions=tuple(executions),
                    )
                return FinancialResearchAgentResult(
                    status="completed",
                    model=self.model,
                    user_request=normalized_request,
                    final_text=final_text,
                    tool_executions=tuple(executions),
                )

            input_items.extend(output_items)
            for function_call in function_calls:
                if len(executions) >= self.max_tool_calls:
                    raise FinancialResearchAgentError(
                        "Agent tool call 한도를 초과했습니다."
                    )
                name = str(self._value(function_call, "name"))
                call_id = str(self._value(function_call, "call_id"))
                raw_arguments = self._value(function_call, "arguments", "{}")
                try:
                    arguments = json.loads(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                    tool_result = await self.tools.execute(name, arguments)
                    succeeded = bool(tool_result.get("ok", True))
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    arguments = {}
                    succeeded = False
                    tool_result = {
                        "ok": False,
                        "error_code": "invalid_tool_call",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                except Exception as exc:
                    succeeded = False
                    tool_result = {
                        "ok": False,
                        "error_code": "tool_execution_error",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                if name not in FinancialResearchTools.ARGUMENT_MODELS:
                    raise FinancialResearchAgentError(
                        f"모델이 허용되지 않은 tool을 요청했습니다: {name}"
                    )
                executions.append(
                    AgentToolExecution(
                        call_id=call_id,
                        name=name,
                        arguments=arguments,
                        succeeded=succeeded,
                        result=tool_result,
                    )
                )
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(
                            tool_result,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )

        raise FinancialResearchAgentError("Agent가 최대 turn 안에 종료되지 않았습니다.")

    @classmethod
    def _is_prohibited_request(cls, text: str) -> bool:
        return any(pattern.search(text) for pattern in cls.PROHIBITED_REQUEST_PATTERNS)

    @classmethod
    def _contains_prohibited_output(cls, text: str) -> bool:
        return any(pattern.search(text) for pattern in cls.PROHIBITED_OUTPUT_PATTERNS)

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

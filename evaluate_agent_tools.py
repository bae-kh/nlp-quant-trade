"""Financial Research Agent의 tool routing 평가 CLI입니다."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agent.financial_research_agent import (
    FinancialResearchAgent,
    FinancialResearchTools,
)
from evaluation.agent_tool_eval import (
    AgentToolEvalArtifactStore,
    AgentToolEvalCase,
    AgentToolEvalSuite,
    load_agent_eval_cases,
)


logger = logging.getLogger(__name__)
DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent
    / "evals"
    / "fixtures"
    / "agent_tool_selection_cases.json"
)


class SideEffectFreeEvalTools:
    """routing만 평가하도록 실제 네트워크·파일 생성을 대신하는 도구입니다."""

    @staticmethod
    def definitions():
        return FinancialResearchTools.definitions()

    async def execute(self, name, arguments):
        argument_model = FinancialResearchTools.ARGUMENT_MODELS[name]
        parsed = argument_model.model_validate(arguments)
        if name == "create_financial_report":
            return {
                "ok": True,
                "status": "completed",
                "run_id": "run_agent_eval_stub",
                "report_path": "reports/generated/agent_eval_stub.md",
            }
        if name == "inspect_report_run":
            return {
                "ok": True,
                "run_id": parsed.run_id,
                "status": "completed_with_warnings",
                "stages": [],
            }
        return {
            "ok": True,
            "metric": parsed.metric,
            **FinancialResearchTools.METRIC_GLOSSARY[parsed.metric],
        }


class RecordedAgentResponses:
    def __init__(self, case: AgentToolEvalCase) -> None:
        self.responses = []
        if case.recorded_tool_calls:
            self.responses.append(
                SimpleNamespace(
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            name=call.name,
                            arguments=json.dumps(call.arguments),
                            call_id=f"eval_call_{index}",
                        )
                        for index, call in enumerate(case.recorded_tool_calls)
                    ],
                    output_text="",
                )
            )
        self.responses.append(
            SimpleNamespace(output=[], output_text=case.recorded_final_text)
        )

    async def create(self, **_kwargs):
        return self.responses.pop(0)


class RecordedAgentClient:
    def __init__(self, case: AgentToolEvalCase) -> None:
        self.responses = RecordedAgentResponses(case)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Financial Research Agent tool selection eval",
    )
    parser.add_argument("--mode", choices=("recorded", "live"), default="recorded")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--model", default=FinancialResearchAgent.DEFAULT_MODEL)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports") / "generated" / "evals",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repetitions <= 10:
        parser.error("--repetitions는 1~10이어야 합니다.")
    return args


async def run_eval(args: argparse.Namespace):
    cases = load_agent_eval_cases(args.fixtures)
    tools = SideEffectFreeEvalTools()
    if args.mode == "recorded":
        mode = "recorded_fixture"
        model = "recorded-agent-fixture-v1"

        def agent_factory(case):
            return FinancialResearchAgent(
                client=RecordedAgentClient(case),
                model=model,
                tools=tools,
            )

    else:
        mode = "live_model"
        model = args.model
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("live Agent eval은 OPENAI_API_KEY가 필요합니다.")
        client = AsyncOpenAI(api_key=api_key)

        def agent_factory(_case):
            return FinancialResearchAgent(
                client=client,
                model=model,
                tools=tools,
            )

    result = await AgentToolEvalSuite().run(
        cases,
        agent_factory=agent_factory,
        mode=mode,
        model=model,
        fixture_path=args.fixtures,
        repetitions=args.repetitions,
    )
    json_path, markdown_path = AgentToolEvalArtifactStore(args.output_dir).save(
        result
    )
    logger.info(
        "Agent eval: %d/%d passed (%.1f%%)",
        result.passed_case_runs,
        result.total_case_runs,
        result.pass_rate * 100,
    )
    logger.info("JSON: %s", json_path)
    logger.info("Markdown: %s", markdown_path)
    for warning in result.warnings:
        logger.warning(warning)
    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    load_dotenv()
    args = parse_args()
    try:
        result = asyncio.run(run_eval(args))
    except Exception as exc:
        logger.error("Agent tool eval 실행 실패: %s", exc)
        raise SystemExit(1) from exc
    if not result.all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

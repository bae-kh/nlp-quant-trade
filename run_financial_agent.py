"""Financial Research Agent의 명령줄 실행 진입점입니다."""

from __future__ import annotations

import argparse
import asyncio
import logging

from dotenv import load_dotenv

from agent.financial_research_agent import (
    FinancialResearchAgent,
    FinancialResearchAgentError,
)


logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="검증된 workflow를 호출하는 단일 Financial Research Agent",
    )
    parser.add_argument(
        "request",
        help=(
            "예: 'TSLA를 2024-12-31 기준 최근 30일로 분석해서 "
            "리포트를 만들어줘'"
        ),
    )
    parser.add_argument(
        "--model",
        default=FinancialResearchAgent.DEFAULT_MODEL,
        help="Agent orchestration에 사용할 정확한 OpenAI model ID",
    )
    return parser.parse_args(argv)


async def run_agent(args: argparse.Namespace):
    agent = FinancialResearchAgent(model=args.model)
    result = await agent.run(args.request)
    logger.info("Agent 상태: %s", result.status)
    if result.tool_executions:
        logger.info(
            "사용 도구: %s",
            ", ".join(execution.name for execution in result.tool_executions),
        )
        for execution in result.tool_executions:
            run_id = execution.result.get("run_id")
            if run_id:
                logger.info("Run ID: %s", run_id)
    print(result.final_text)
    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    load_dotenv()
    args = parse_args()
    try:
        asyncio.run(run_agent(args))
    except FinancialResearchAgentError as exc:
        logger.error("Agent 실행 실패: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

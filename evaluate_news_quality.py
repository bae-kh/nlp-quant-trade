"""고정 뉴스 fixture로 NewsAnalyzer 품질 회귀 평가를 실행합니다."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from analysis.news_analyzer import NewsAnalyzer
from evaluation.news_quality_eval import (
    NewsEvalCase,
    NewsEvalSuite,
    NewsQualityEvalArtifactStore,
    load_eval_cases,
)


logger = logging.getLogger(__name__)
DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent
    / "evals"
    / "fixtures"
    / "news_quality_cases.json"
)


class RecordedResponses:
    """외부 호출 없이 평가기 자체의 회귀를 확인하는 고정 응답 client입니다."""

    def __init__(self, case: NewsEvalCase) -> None:
        self.case = case

    async def parse(self, **_kwargs):
        return SimpleNamespace(output_parsed=self.case.recorded_output)


class RecordedClient:
    def __init__(self, case: NewsEvalCase) -> None:
        self.responses = RecordedResponses(case)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="뉴스 LLM 감성·근거·금지 주장 품질 평가",
    )
    parser.add_argument(
        "--mode",
        choices=("recorded", "live"),
        default="recorded",
        help=(
            "recorded는 무료 고정 응답 회귀, live는 실제 OpenAI 모델 호출 "
            "(비용 발생 가능)"
        ),
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="평가 fixture JSON 경로",
    )
    parser.add_argument(
        "--model",
        default=NewsAnalyzer.DEFAULT_MODEL,
        help="live 평가에 사용할 정확한 OpenAI model ID",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="각 case 반복 횟수 (기본 1, 최대 10)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports") / "generated" / "evals",
        help="평가 JSON/Markdown 저장 디렉터리",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repetitions <= 10:
        parser.error("--repetitions는 1~10이어야 합니다.")
    return args


async def run_eval(args: argparse.Namespace):
    cases = load_eval_cases(args.fixtures)
    suite = NewsEvalSuite()
    if args.mode == "recorded":
        mode = "recorded_fixture"
        model = "recorded-fixture-v1"

        def analyzer_factory(case: NewsEvalCase) -> NewsAnalyzer:
            return NewsAnalyzer(client=RecordedClient(case), model=model)

    else:
        mode = "live_model"
        model = args.model
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "live 평가는 OPENAI_API_KEY가 필요합니다. "
                "recorded 평가는 키 없이 실행할 수 있습니다."
            )
        live_analyzer = NewsAnalyzer(api_key=api_key, model=model)

        def analyzer_factory(_case: NewsEvalCase) -> NewsAnalyzer:
            return live_analyzer

    result = await suite.run(
        cases,
        analyzer_factory=analyzer_factory,
        mode=mode,
        model=model,
        fixture_path=args.fixtures,
        repetitions=args.repetitions,
    )
    json_path, markdown_path = NewsQualityEvalArtifactStore(
        args.output_dir
    ).save(result)
    logger.info("Eval mode: %s", result.mode)
    logger.info("Model: %s", result.model)
    logger.info(
        "Result: %d/%d passed (%.1f%%)",
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
        logger.error("뉴스 품질 평가 실행 실패: %s", exc)
        raise SystemExit(1) from exc
    if not result.all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

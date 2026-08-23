"""검증 가능한 LLM 금융 리포트의 공식 CLI 진입점입니다.

가격·뉴스를 같은 달력일 구간으로 수집하고, 금융 수치는 Python이 계산하며,
LLM은 뉴스 정성 분석만 담당하며 주문이나 백테스트를 실행하지 않습니다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from dotenv import load_dotenv
from pydantic import ValidationError

from analysis.market_analyzer import MarketAnalysis
from workflow.analysis_window import AnalysisRequest, resolve_analysis_window
from workflow.financial_reporting_workflow import (
    FinancialReportResult,
    FinancialReportingWorkflow,
    FinancialReportingWorkflowError,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"올바른 날짜 형식이 아닙니다: {value!r} (YYYY-MM-DD 필요)"
        ) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="가격·뉴스·LLM을 연결한 금융 Markdown 리포트 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python generate_report.py --ticker TSLA
  python generate_report.py --ticker TSLA --analysis-days 30
  python generate_report.py --ticker TSLA --analysis-days 30 --as-of-date 2024-12-31

이 도구의 출력물은 투자 조언이 아닙니다.
        """,
    )
    parser.add_argument(
        "--ticker",
        default="TSLA",
        help="분석 대상 ticker 심볼 (기본 TSLA)",
    )
    parser.add_argument(
        "--analysis-days",
        type=int,
        default=30,
        help="종료일을 포함한 최근 달력일 수 (기본 30)",
    )
    parser.add_argument(
        "--as-of-date",
        type=_parse_iso_date,
        default=None,
        help="선택적 기준일 YYYY-MM-DD (기본: 실행 시 미국 동부 날짜)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 저장 경로 (기본: reports/generated/의 고유 파일명)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="명시한 리포트 경로가 이미 존재할 때만 원자적으로 교체",
    )
    args = parser.parse_args(argv)
    try:
        request = AnalysisRequest(
            ticker=args.ticker,
            analysis_days=args.analysis_days,
            as_of_date=args.as_of_date,
        )
    except ValidationError as exc:
        parser.error(f"분석 기간 입력이 올바르지 않습니다: {exc}")
    window = resolve_analysis_window(request)
    args.ticker = window.ticker
    args.start = window.start_date.isoformat()
    args.end = window.end_date.isoformat()
    args.analysis_window = window
    return args


def _optional_percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _optional_decimal(value: float | None, digits: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def _log_market_analysis(result: MarketAnalysis) -> None:
    logger.info("\n[시장 분석 결과]")
    logger.info(
        "  실제 가격 범위:  %s ~ %s (%d개 거래일)",
        result.actual_price_start,
        result.actual_price_end,
        result.price_observation_count,
    )
    logger.info("  기간 수익률:    %s", _optional_percentage(result.period_return))
    logger.info(
        "  연환산 변동성:  %s",
        _optional_percentage(result.annualized_volatility),
    )
    logger.info("  최대 낙폭(MDD): %s", _optional_percentage(result.max_drawdown))
    logger.info("  최신 RSI:       %s", _optional_decimal(result.latest_rsi))
    logger.info(
        "  최신 MACD diff: %s",
        _optional_decimal(result.latest_macd_diff, digits=4),
    )
    logger.info("  지표 상태:      %s", result.indicator_status)
    logger.info(
        "  %s 수익률:     %s",
        result.benchmark_ticker,
        _optional_percentage(result.benchmark_return),
    )
    logger.info(
        "  Benchmark 차이: %s",
        _optional_percentage(result.benchmark_difference),
    )
    logger.info("  Benchmark 상태: %s", result.benchmark_status)
    for warning in result.warnings:
        logger.warning("  분석 경고: %s", warning)


def _log_news_result(result: FinancialReportResult) -> None:
    news = result.news_fetch_result
    analysis = result.news_analysis
    logger.info("\n[뉴스 분석 결과]")
    logger.info("  수집 상태:      %s", news.status)
    logger.info("  저장 기사:      %d건", len(news.items))
    logger.info(
        "  LLM 분석 상태:  %s",
        "available" if analysis.available else "unavailable",
    )
    logger.info(
        "  선택/분석 기사: %d/%d건",
        analysis.selected_article_count,
        analysis.analyzed_article_count,
    )
    if analysis.available:
        logger.info("  감성:           %s", analysis.sentiment)
        logger.info("  감성 점수:      %.2f", analysis.score)
        logger.info("  신뢰도:         %d", analysis.confidence)
    else:
        logger.warning("  LLM fallback:   %s", analysis.error_code)
    if result.news_snapshot_path is not None:
        logger.info("  뉴스 Snapshot:  %s", result.news_snapshot_path)
    else:
        logger.warning("  뉴스 Snapshot:  저장되지 않음")
    for warning in result.warnings:
        logger.warning("  Workflow 경고: %s", warning)


async def run_recent_financial_report(
    args: argparse.Namespace,
    *,
    financial_workflow: FinancialReportingWorkflow | None = None,
) -> FinancialReportResult:
    window = args.analysis_window
    logger.info("=" * 60)
    logger.info("📊 LLM 금융 리포팅 workflow 시작")
    logger.info("   Ticker: %s", window.ticker)
    logger.info("   기간:   %s ~ %s", window.start_date, window.end_date)
    logger.info(
        "   기간 기준: 최근 %s개 달력일 (양 끝 날짜 포함, %s)",
        window.requested_analysis_days,
        window.timezone,
    )
    logger.info("=" * 60)
    logger.info("⚠️  출력물은 투자 조언이 아닙니다.")
    logger.info("\n시장 가격·뉴스 수집 및 분석을 시작합니다...")

    workflow = financial_workflow or FinancialReportingWorkflow()
    result = await workflow.run(
        window,
        output_path=args.output,
        overwrite=args.overwrite,
    )
    _log_market_analysis(result.market_analysis)
    _log_news_result(result)
    logger.info("\n✅ 금융 리포트 생성 완료")
    logger.info("   Run ID:    %s", result.run_id)
    logger.info("   실행 상태: %s", result.status)
    logger.info("   Markdown: %s", result.report_path)
    logger.info("   실행 이력: %s", result.run_metadata_path)
    logger.info("=" * 60)
    return result


async def run_pipeline(
    args: argparse.Namespace,
    *,
    financial_workflow: FinancialReportingWorkflow | None = None,
) -> FinancialReportResult:
    """공식 CLI는 새 reporting workflow만 실행합니다."""
    return await run_recent_financial_report(
        args,
        financial_workflow=financial_workflow,
    )


def main() -> None:
    load_dotenv()
    args = parse_args()
    try:
        asyncio.run(run_pipeline(args))
    except FinancialReportingWorkflowError as exc:
        logger.error("금융 리포트 생성 실패: %s", exc)
        if exc.run_id is not None:
            logger.error("실패 Run ID: %s", exc.run_id)
        if exc.run_metadata_path is not None:
            logger.error("실패 실행 이력: %s", exc.run_metadata_path)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

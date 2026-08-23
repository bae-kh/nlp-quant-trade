"""공식 금융 리포팅 CLI의 입력과 workflow 연결을 검증합니다."""

import asyncio
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import generate_report
from analysis.market_analyzer import MarketAnalysis
from workflow.financial_reporting_workflow import FinancialReportingWorkflowError


def make_analysis() -> MarketAnalysis:
    return MarketAnalysis(
        ticker="TSLA",
        requested_start_date=date(2026, 7, 13),
        requested_end_date=date(2026, 8, 11),
        actual_price_start=date(2026, 7, 13),
        actual_price_end=date(2026, 8, 11),
        price_observation_count=21,
        period_return=0.10,
        annualized_volatility=0.25,
        max_drawdown=-0.08,
        latest_rsi=55.0,
        latest_macd_diff=1.25,
        indicator_status="available",
        benchmark_ticker="SPY",
        benchmark_return=0.04,
        benchmark_difference=0.06,
        benchmark_status="available",
    )


def make_workflow_result(market=None, *, news_available=True):
    market = market or make_analysis()
    news_analysis = SimpleNamespace(
        available=news_available,
        selected_article_count=3,
        analyzed_article_count=3 if news_available else 0,
        sentiment="positive" if news_available else None,
        score=0.4 if news_available else None,
        confidence=80 if news_available else None,
        error_code=None if news_available else "missing_api_key",
    )
    return SimpleNamespace(
        run_id="run_test_TSLA",
        market_analysis=market,
        news_fetch_result=SimpleNamespace(status="available", items=(1, 2, 3)),
        news_analysis=news_analysis,
        news_snapshot_path=Path("news.json"),
        report_path=Path("report.md"),
        run_metadata_path=Path("run.json"),
        status="completed" if news_available else "completed_with_warnings",
        warnings=(),
    )


class StubFinancialWorkflow:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def run(self, window, **kwargs):
        self.calls.append((window, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def test_recent_mode_calls_financial_workflow_and_returns_report_result(caplog):
    caplog.set_level(logging.INFO, logger=generate_report.__name__)
    args = generate_report.parse_args(
        [
            "--ticker",
            "TSLA",
            "--analysis-days",
            "30",
            "--as-of-date",
            "2026-08-11",
        ]
    )
    expected = make_workflow_result()
    workflow = StubFinancialWorkflow(result=expected)

    result = asyncio.run(
        generate_report.run_pipeline(args, financial_workflow=workflow)
    )

    assert result == expected
    assert workflow.calls == [
        (
            args.analysis_window,
            {"output_path": None, "overwrite": False},
        )
    ]
    assert "기간 수익률" in caplog.text
    assert "Benchmark 상태" in caplog.text
    assert "뉴스 분석 결과" in caplog.text
    assert "Markdown" in caplog.text
    assert "run_test_TSLA" in caplog.text
    assert "실행 이력" in caplog.text
    assert "몬테카를로" not in caplog.text
    assert "자본금" not in caplog.text
    assert "총 거래" not in caplog.text


def test_recent_required_failure_is_propagated():
    args = generate_report.parse_args(
        ["--analysis-days", "30", "--as-of-date", "2026-08-11"]
    )
    workflow = StubFinancialWorkflow(
        error=FinancialReportingWorkflowError("target failed")
    )
    with pytest.raises(FinancialReportingWorkflowError, match="target failed"):
        asyncio.run(
            generate_report.run_pipeline(args, financial_workflow=workflow)
        )

def test_recent_log_formats_unavailable_optional_values(caplog):
    caplog.set_level(logging.INFO, logger=generate_report.__name__)
    args = generate_report.parse_args(
        ["--analysis-days", "30", "--as-of-date", "2026-08-11"]
    )
    unavailable_market = make_analysis().model_copy(
        update={
            "latest_rsi": None,
            "latest_macd_diff": None,
            "indicator_status": "insufficient_history",
            "benchmark_return": None,
            "benchmark_difference": None,
            "benchmark_status": "unavailable",
            "warnings": ("SPY benchmark 데이터를 사용할 수 없습니다.",),
        }
    )
    workflow = StubFinancialWorkflow(
        result=make_workflow_result(
            unavailable_market,
            news_available=False,
        )
    )

    asyncio.run(generate_report.run_pipeline(args, financial_workflow=workflow))

    assert "N/A" in caplog.text
    assert "unavailable" in caplog.text
    assert "분석 경고" in caplog.text
    assert "missing_api_key" in caplog.text


def test_recent_output_and_overwrite_options_reach_workflow():
    args = generate_report.parse_args(
        [
            "--analysis-days",
            "30",
            "--as-of-date",
            "2026-08-11",
            "--output",
            "reports/generated/example.md",
            "--overwrite",
        ]
    )
    workflow = StubFinancialWorkflow(result=make_workflow_result())

    asyncio.run(generate_report.run_pipeline(args, financial_workflow=workflow))

    assert workflow.calls[0][1] == {
        "output_path": "reports/generated/example.md",
        "overwrite": True,
    }

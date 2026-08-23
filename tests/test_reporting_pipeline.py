"""ReportingPipeline의 필수/선택 단계 실패 정책을 검증합니다."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from data_pipeline.price_fetcher import PriceFetchError, PriceHistory
from workflow.analysis_window import AnalysisWindow
from workflow.reporting_pipeline import ReportingPipeline, ReportingPipelineError


def make_window() -> AnalysisWindow:
    return AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=30,
        start_date=date(2026, 7, 13),
        end_date=date(2026, 8, 11),
    )


def make_prices(
    *,
    first_close: float = 100.0,
    last_close: float = 110.0,
) -> pd.DataFrame:
    index = pd.bdate_range("2026-04-14", "2026-08-11")
    return pd.DataFrame(
        {"Close": np.linspace(first_close, last_close, len(index))},
        index=index,
    )


def make_history(
    ticker: str,
    window: AnalysisWindow,
    history: pd.DataFrame,
) -> PriceHistory:
    report_dates = history.index.date
    report_count = int(
        (
            (report_dates >= window.start_date)
            & (report_dates <= window.end_date)
        ).sum()
    )
    return PriceHistory(
        ticker=ticker,
        window=window,
        warmup_start_date=date(2026, 4, 14),
        fetch_end_date=window.end_date,
        report_observation_count=report_count,
        history=history,
    )


class FakePriceFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_analysis_history(self, ticker, window):
        self.calls.append((ticker, window))
        response = self.responses[ticker]
        if isinstance(response, Exception):
            raise response
        return response


def test_runs_target_and_benchmark_with_same_window():
    window = make_window()
    target = make_history("TSLA", window, make_prices())
    benchmark = make_history("SPY", window, make_prices(last_close=105.0))
    fetcher = FakePriceFetcher({"TSLA": target, "SPY": benchmark})
    pipeline = ReportingPipeline(price_fetcher=fetcher)

    result = pipeline.run(window)

    assert fetcher.calls == [("TSLA", window), ("SPY", window)]
    assert result.ticker == "TSLA"
    assert result.indicator_status == "available"
    assert result.benchmark_status == "available"
    assert result.benchmark_return is not None
    assert result.benchmark_difference is not None


def test_target_fetch_failure_stops_before_benchmark():
    window = make_window()
    fetcher = FakePriceFetcher(
        {"TSLA": PriceFetchError("target provider unavailable")}
    )
    pipeline = ReportingPipeline(price_fetcher=fetcher)

    with pytest.raises(ReportingPipelineError, match="필수 가격 수집"):
        pipeline.run(window)

    assert fetcher.calls == [("TSLA", window)]


def test_benchmark_fetch_failure_is_non_fatal(caplog):
    window = make_window()
    target = make_history("TSLA", window, make_prices())
    fetcher = FakePriceFetcher(
        {
            "TSLA": target,
            "SPY": PriceFetchError("benchmark provider unavailable"),
        }
    )
    pipeline = ReportingPipeline(price_fetcher=fetcher)

    result = pipeline.run(window)

    assert result.benchmark_status == "unavailable"
    assert result.benchmark_return is None
    assert result.benchmark_difference is None
    assert any("SPY benchmark" in warning for warning in result.warnings)
    assert "benchmark 가격 수집 실패" in caplog.text


def test_benchmark_with_one_report_price_is_insufficient_not_fatal():
    window = make_window()
    target = make_history("TSLA", window, make_prices())
    benchmark_prices = make_prices().loc[[pd.Timestamp("2026-07-13")]]
    benchmark = make_history("SPY", window, benchmark_prices)
    fetcher = FakePriceFetcher({"TSLA": target, "SPY": benchmark})
    pipeline = ReportingPipeline(price_fetcher=fetcher)

    result = pipeline.run(window)

    assert result.benchmark_status == "insufficient_data"
    assert result.benchmark_return is None


def test_target_with_one_report_price_is_required_analysis_failure():
    window = make_window()
    target_prices = make_prices().loc[[pd.Timestamp("2026-07-13")]]
    target = make_history("TSLA", window, target_prices)
    benchmark = make_history("SPY", window, make_prices())
    fetcher = FakePriceFetcher({"TSLA": target, "SPY": benchmark})
    pipeline = ReportingPipeline(price_fetcher=fetcher)

    with pytest.raises(ReportingPipelineError, match="필수 시장 분석"):
        pipeline.run(window)


def test_normalizes_custom_benchmark_ticker():
    window = make_window()
    target = make_history("TSLA", window, make_prices())
    benchmark = make_history("QQQ", window, make_prices(last_close=106.0))
    fetcher = FakePriceFetcher({"TSLA": target, "QQQ": benchmark})
    pipeline = ReportingPipeline(
        price_fetcher=fetcher,
        benchmark_ticker=" qqq ",
    )

    result = pipeline.run(window)

    assert result.benchmark_ticker == "QQQ"
    assert fetcher.calls[-1] == ("QQQ", window)


def test_rejects_empty_benchmark_ticker():
    with pytest.raises(ValueError, match="benchmark_ticker"):
        ReportingPipeline(benchmark_ticker="   ")

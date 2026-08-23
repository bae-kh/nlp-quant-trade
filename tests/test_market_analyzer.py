"""결정론적 MarketAnalyzer의 계산과 실패 정책을 검증합니다."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from analysis.market_analyzer import (
    InsufficientMarketDataError,
    MarketAnalyzer,
    MarketDataValidationError,
)
from workflow.analysis_window import AnalysisWindow


def make_window(start: str, end: str, ticker: str = "TSLA") -> AnalysisWindow:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return AnalysisWindow(
        ticker=ticker,
        requested_analysis_days=(end_date - start_date).days + 1,
        start_date=start_date,
        end_date=end_date,
    )


def make_prices(values, start: str = "2026-07-01") -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.date_range(start=start, periods=len(values), freq="D"),
    )


def test_calculates_known_return_and_drawdown():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 120.0, 90.0, 110.0])
    window = make_window("2026-07-01", "2026-07-04")

    result = analyzer.analyze(prices, window)

    assert result.period_return == pytest.approx(0.10)
    assert result.max_drawdown == pytest.approx(-0.25)
    assert result.actual_price_start == date(2026, 7, 1)
    assert result.actual_price_end == date(2026, 7, 4)
    assert result.price_observation_count == 4


def test_constant_price_has_zero_volatility_and_drawdown():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 100.0, 100.0, 100.0])
    window = make_window("2026-07-01", "2026-07-04")

    result = analyzer.analyze(prices, window)

    assert result.annualized_volatility == pytest.approx(0.0)
    assert result.max_drawdown == pytest.approx(0.0)


def test_volatility_matches_daily_return_standard_deviation():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 110.0, 99.0, 108.9])
    window = make_window("2026-07-01", "2026-07-04")
    expected = prices["Close"].pct_change().dropna().std(ddof=1) * np.sqrt(252)

    result = analyzer.analyze(prices, window)

    assert result.annualized_volatility == pytest.approx(expected)


def test_warmup_prices_are_excluded_from_report_metrics():
    analyzer = MarketAnalyzer()
    prices = make_prices([200.0] * 35 + [100.0, 110.0])
    window = make_window("2026-08-05", "2026-08-06")

    result = analyzer.analyze(prices, window)

    assert result.period_return == pytest.approx(0.10)
    assert result.price_observation_count == 2
    assert result.actual_price_start == date(2026, 8, 5)


def test_indicators_use_warmup_history_and_return_latest_values():
    analyzer = MarketAnalyzer()
    prices = make_prices(np.linspace(100.0, 160.0, 60), start="2026-06-01")
    window = make_window("2026-07-25", "2026-07-30")

    result = analyzer.analyze(prices, window)

    assert result.latest_rsi is not None
    assert result.latest_macd_diff is not None
    assert result.indicator_status == "available"


def test_short_history_marks_indicators_unavailable_without_failing():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 101.0, 102.0])
    window = make_window("2026-07-01", "2026-07-03")

    result = analyzer.analyze(prices, window)

    assert result.latest_rsi is None
    assert result.latest_macd_diff is None
    assert result.indicator_status == "insufficient_history"
    assert result.warnings


def test_medium_history_can_report_partial_indicator_status():
    analyzer = MarketAnalyzer()
    prices = make_prices(np.linspace(100.0, 120.0, 20))
    window = make_window("2026-07-01", "2026-07-20")

    result = analyzer.analyze(prices, window)

    assert result.latest_rsi is not None
    assert result.latest_macd_diff is None
    assert result.indicator_status == "partial"


def test_calculates_benchmark_return_difference():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 110.0])
    benchmark = make_prices([100.0, 105.0])
    window = make_window("2026-07-01", "2026-07-02")

    result = analyzer.analyze(
        prices,
        window,
        benchmark_history=benchmark,
        benchmark_ticker="spy",
    )

    assert result.benchmark_ticker == "SPY"
    assert result.benchmark_return == pytest.approx(0.05)
    assert result.benchmark_difference == pytest.approx(0.05)
    assert result.benchmark_status == "available"


def test_benchmark_requires_same_comparison_endpoints_as_target():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 105.0, 110.0])
    benchmark = make_prices([102.0, 104.0], start="2026-07-02")
    window = make_window("2026-07-01", "2026-07-03")

    result = analyzer.analyze(
        prices,
        window,
        benchmark_history=benchmark,
    )

    assert result.benchmark_status == "insufficient_data"
    assert result.benchmark_return is None
    assert result.benchmark_difference is None
    assert any("동일한 비교 시작일" in warning for warning in result.warnings)


def test_missing_benchmark_is_non_fatal():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 110.0])
    window = make_window("2026-07-01", "2026-07-02")

    result = analyzer.analyze(prices, window)

    assert result.benchmark_return is None
    assert result.benchmark_difference is None
    assert result.benchmark_status == "unavailable"


def test_invalid_benchmark_is_non_fatal():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 110.0])
    invalid_benchmark = pd.DataFrame(
        {"Open": [100.0, 101.0]},
        index=pd.date_range("2026-07-01", periods=2),
    )
    window = make_window("2026-07-01", "2026-07-02")

    result = analyzer.analyze(
        prices,
        window,
        benchmark_history=invalid_benchmark,
    )

    assert result.benchmark_status == "unavailable"
    assert any("Close" in warning for warning in result.warnings)


def test_empty_price_data_fails():
    analyzer = MarketAnalyzer()
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(InsufficientMarketDataError, match="비어"):
        analyzer.analyze(pd.DataFrame(), window)


def test_single_price_inside_window_fails():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0])
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(InsufficientMarketDataError, match="2개 미만"):
        analyzer.analyze(prices, window)


def test_missing_close_column_fails():
    analyzer = MarketAnalyzer()
    prices = pd.DataFrame(
        {"Open": [100.0, 101.0]},
        index=pd.date_range("2026-07-01", periods=2),
    )
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(MarketDataValidationError, match="Close"):
        analyzer.analyze(prices, window)


@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, 0.0, -1.0])
def test_invalid_close_values_fail(invalid_value):
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, invalid_value])
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(MarketDataValidationError):
        analyzer.analyze(prices, window)


def test_duplicate_dates_fail():
    analyzer = MarketAnalyzer()
    prices = pd.DataFrame(
        {"Close": [100.0, 110.0]},
        index=pd.to_datetime(["2026-07-01", "2026-07-01"]),
    )
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(MarketDataValidationError, match="중복"):
        analyzer.analyze(prices, window)


def test_two_rows_on_same_calendar_date_fail():
    analyzer = MarketAnalyzer()
    prices = pd.DataFrame(
        {"Close": [100.0, 110.0]},
        index=pd.to_datetime(["2026-07-01 09:00", "2026-07-01 16:00"]),
    )
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(MarketDataValidationError, match="중복"):
        analyzer.analyze(prices, window)


def test_nat_index_fails():
    analyzer = MarketAnalyzer()
    prices = pd.DataFrame(
        {"Close": [100.0, 110.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-01"), pd.NaT]),
    )
    window = make_window("2026-07-01", "2026-07-02")

    with pytest.raises(MarketDataValidationError, match="NaT"):
        analyzer.analyze(prices, window)


def test_reverse_order_is_sorted_before_calculation():
    analyzer = MarketAnalyzer()
    prices = pd.DataFrame(
        {"Close": [110.0, 100.0]},
        index=pd.to_datetime(["2026-07-02", "2026-07-01"]),
    )
    window = make_window("2026-07-01", "2026-07-02")

    result = analyzer.analyze(prices, window)

    assert result.period_return == pytest.approx(0.10)


def test_future_prices_are_excluded():
    analyzer = MarketAnalyzer()
    prices = make_prices([100.0, 110.0, 1000.0])
    window = make_window("2026-07-01", "2026-07-02")

    result = analyzer.analyze(prices, window)

    assert result.period_return == pytest.approx(0.10)
    assert result.actual_price_end == date(2026, 7, 2)

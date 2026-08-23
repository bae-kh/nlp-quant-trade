"""리포팅용 가격 수집이 원본 행과 warm-up을 보존하는지 검증합니다."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from data_pipeline.price_fetcher import (
    PriceDataUnavailableError,
    PriceFetchError,
    PriceFetcher,
)
from workflow.analysis_window import AnalysisWindow


def make_window(
    start: str = "2026-07-13",
    end: str = "2026-08-11",
) -> AnalysisWindow:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=(end_date - start_date).days + 1,
        start_date=start_date,
        end_date=end_date,
    )


def make_download_result(
    start: str = "2026-04-14",
    periods: int = 120,
) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "Open": np.linspace(90.0, 100.0, periods),
            "High": np.linspace(91.0, 101.0, periods),
            "Low": np.linspace(89.0, 99.0, periods),
            "Close": np.linspace(90.0, 110.0, periods),
            "Volume": np.arange(periods) + 1_000,
        },
        index=index,
    )


def test_requests_warmup_and_inclusive_report_end():
    calls = []

    def downloader(*args, **kwargs):
        calls.append((args, kwargs))
        return make_download_result()

    fetcher = PriceFetcher(downloader=downloader, timeout_seconds=7)
    result = fetcher.fetch_analysis_history(" tsla ", make_window())

    args, kwargs = calls[0]
    assert args == ("TSLA",)
    assert kwargs == {
        "start": "2026-04-14",
        "end": "2026-08-12",
        "interval": "1d",
        "auto_adjust": True,
        "progress": False,
        "timeout": 7,
    }
    assert result.ticker == "TSLA"
    assert result.warmup_start_date == date(2026, 4, 14)
    assert result.fetch_end_date == date(2026, 8, 11)
    assert result.report_observation_count == 30


def test_preserves_warmup_rows_without_adding_indicators():
    downloaded = make_download_result()
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    result = fetcher.fetch_analysis_history("TSLA", make_window())

    assert result.history.index.min().date() == date(2026, 4, 14)
    assert result.history.index.max().date() == date(2026, 8, 11)
    assert "RSI_14" not in result.history.columns
    assert "MACD_diff" not in result.history.columns


def test_does_not_forward_fill_or_drop_close_nan():
    downloaded = make_download_result()
    nan_date = pd.Timestamp("2026-07-20")
    downloaded.loc[nan_date, "Close"] = np.nan
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    result = fetcher.fetch_analysis_history("TSLA", make_window())

    assert nan_date in result.history.index
    assert pd.isna(result.history.loc[nan_date, "Close"])


def test_flattens_single_ticker_multiindex_columns():
    downloaded = make_download_result()
    downloaded.columns = pd.MultiIndex.from_product(
        [downloaded.columns, ["TSLA"]]
    )
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    result = fetcher.fetch_analysis_history("TSLA", make_window())

    assert not isinstance(result.history.columns, pd.MultiIndex)
    assert "Close" in result.history.columns


def test_excludes_provider_rows_outside_requested_fetch_range():
    downloaded = make_download_result(start="2026-04-13", periods=122)
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    result = fetcher.fetch_analysis_history("TSLA", make_window())

    assert result.history.index.min().date() == date(2026, 4, 14)
    assert result.history.index.max().date() == date(2026, 8, 11)


def test_provider_exception_becomes_explicit_fetch_error():
    def failing_downloader(*args, **kwargs):
        raise ConnectionError("network down")

    fetcher = PriceFetcher(downloader=failing_downloader)

    with pytest.raises(PriceFetchError, match="제공자 호출"):
        fetcher.fetch_analysis_history("TSLA", make_window())


def test_empty_provider_response_becomes_unavailable_error():
    fetcher = PriceFetcher(
        downloader=lambda *args, **kwargs: pd.DataFrame()
    )

    with pytest.raises(PriceDataUnavailableError, match="빈 데이터"):
        fetcher.fetch_analysis_history("TSLA", make_window())


def test_missing_close_column_is_schema_error():
    downloaded = make_download_result().drop(columns="Close")
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    with pytest.raises(PriceFetchError, match="Close"):
        fetcher.fetch_analysis_history("TSLA", make_window())


def test_warmup_only_response_is_not_treated_as_report_data():
    downloaded = make_download_result(start="2026-04-14", periods=60)
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    with pytest.raises(PriceDataUnavailableError, match="분석 기간"):
        fetcher.fetch_analysis_history("TSLA", make_window())


def test_supports_benchmark_ticker_with_same_window():
    downloaded = make_download_result()
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: downloaded)

    result = fetcher.fetch_analysis_history("spy", make_window())

    assert result.ticker == "SPY"
    assert result.window.ticker == "TSLA"


@pytest.mark.parametrize("warmup_days", [-1, -90])
def test_rejects_negative_warmup(warmup_days):
    fetcher = PriceFetcher(downloader=lambda *args, **kwargs: make_download_result())

    with pytest.raises(ValueError, match="warmup"):
        fetcher.fetch_analysis_history(
            "TSLA",
            make_window(),
            warmup_calendar_days=warmup_days,
        )

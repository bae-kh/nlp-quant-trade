"""최근 분석 요청과 공통 AnalysisWindow 계약을 검증합니다."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from generate_report import parse_args
from workflow.analysis_window import (
    AnalysisRequest,
    AnalysisWindow,
    resolve_analysis_window,
)


def test_one_day_window_includes_as_of_date():
    request = AnalysisRequest(
        ticker="TSLA",
        analysis_days=1,
        as_of_date=date(2026, 8, 11),
    )

    window = resolve_analysis_window(request)

    assert window.start_date == date(2026, 8, 11)
    assert window.end_date == date(2026, 8, 11)


def test_seven_day_window_includes_both_boundaries():
    request = AnalysisRequest(
        ticker="TSLA",
        analysis_days=7,
        as_of_date=date(2026, 8, 11),
    )

    window = resolve_analysis_window(request)

    assert window.start_date == date(2026, 8, 5)
    assert (window.end_date - window.start_date).days + 1 == 7


def test_window_crosses_month_boundary():
    request = AnalysisRequest(
        ticker="TSLA",
        analysis_days=5,
        as_of_date=date(2026, 3, 2),
    )

    window = resolve_analysis_window(request)

    assert window.start_date == date(2026, 2, 26)


def test_window_crosses_year_boundary():
    request = AnalysisRequest(
        ticker="TSLA",
        analysis_days=3,
        as_of_date=date(2026, 1, 1),
    )

    window = resolve_analysis_window(request)

    assert window.start_date == date(2025, 12, 30)


def test_window_handles_leap_day():
    request = AnalysisRequest(
        ticker="TSLA",
        analysis_days=2,
        as_of_date=date(2024, 3, 1),
    )

    window = resolve_analysis_window(request)

    assert window.start_date == date(2024, 2, 29)


@pytest.mark.parametrize("analysis_days", [0, -1])
def test_non_positive_analysis_days_are_rejected(analysis_days):
    with pytest.raises(ValidationError):
        AnalysisRequest(ticker="TSLA", analysis_days=analysis_days)


def test_ticker_is_trimmed_and_uppercased():
    request = AnalysisRequest(ticker="  tsla  ", analysis_days=7)

    assert request.ticker == "TSLA"


def test_explicit_as_of_date_has_priority_over_now():
    request = AnalysisRequest(
        ticker="TSLA",
        analysis_days=1,
        as_of_date=date(2026, 8, 1),
    )

    window = resolve_analysis_window(
        request,
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    assert window.end_date == date(2026, 8, 1)


def test_default_as_of_date_uses_new_york_calendar_date():
    request = AnalysisRequest(ticker="TSLA", analysis_days=1)

    window = resolve_analysis_window(
        request,
        # UTC 8월 12일이지만 뉴욕은 아직 8월 11일입니다.
        now=datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc),
    )

    assert window.end_date == date(2026, 8, 11)
    assert window.timezone == "America/New_York"


def test_naive_now_is_rejected():
    request = AnalysisRequest(ticker="TSLA", analysis_days=1)

    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_analysis_window(request, now=datetime(2026, 8, 11, 12, 0))


def test_window_rejects_mismatched_inclusive_day_count():
    with pytest.raises(ValidationError, match="day count"):
        AnalysisWindow(
            ticker="TSLA",
            requested_analysis_days=7,
            start_date=date(2026, 8, 6),
            end_date=date(2026, 8, 11),
        )


def test_recent_cli_window_maps_to_existing_start_and_end_args():
    args = parse_args([
        "--ticker", " tsla ",
        "--analysis-days", "7",
        "--as-of-date", "2026-08-11",
    ])

    assert args.ticker == "TSLA"
    assert args.start == "2026-08-05"
    assert args.end == "2026-08-11"
    assert args.analysis_window.requested_analysis_days == 7


def test_as_of_date_requires_analysis_days():
    args = parse_args(["--as-of-date", "2026-08-11"])

    assert args.analysis_days == 30
    assert args.analysis_window.end_date == date(2026, 8, 11)


def test_recent_window_rejects_historical_news_cache_mode():
    with pytest.raises(SystemExit):
        parse_args(["--analysis-days", "7", "--use-news-cache"])


def test_recent_window_rejects_unsupported_local_llm_mode():
    with pytest.raises(SystemExit):
        parse_args(["--analysis-days", "7", "--local-llm"])


def test_analysis_window_rejects_unsupported_date_options():
    with pytest.raises(SystemExit):
        parse_args([
            "--analysis-days", "7",
            "--start", "2026-08-01",
            "--end", "2026-08-11",
        ])


def test_no_period_options_default_to_recent_30_day_report():
    args = parse_args([])

    assert args.analysis_days == 30
    assert args.analysis_window is not None


def test_official_cli_rejects_unsupported_capital_option():
    with pytest.raises(SystemExit):
        parse_args(["--capital", "20000"])


def test_analysis_window_rejects_unsupported_capital_and_news_csv_options():
    with pytest.raises(SystemExit):
        parse_args(["--analysis-days", "7", "--capital", "20000"])

    with pytest.raises(SystemExit):
        parse_args(["--analysis-days", "7", "--news-csv", "old.csv"])

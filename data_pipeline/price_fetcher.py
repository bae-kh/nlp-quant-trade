# file path: data_pipeline/price_fetcher.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import yfinance as yf
import pandas as pd

from workflow.analysis_window import AnalysisWindow

logger = logging.getLogger(__name__)


class PriceFetchError(RuntimeError):
    """가격 제공자 호출 또는 응답 형식이 잘못됐을 때 발생합니다."""


class PriceDataUnavailableError(PriceFetchError):
    """요청한 분석 기간에 사용할 가격 관측값이 없을 때 발생합니다."""


@dataclass(frozen=True)
class PriceHistory:
    """리포트 기간과 지표 warm-up을 함께 담은 원본 일봉 수집 결과입니다."""

    ticker: str
    window: AnalysisWindow
    warmup_start_date: date
    fetch_end_date: date
    report_observation_count: int
    history: pd.DataFrame


class PriceFetcher:
    """리포팅 분석에 필요한 미국 주식 원본 일봉을 yfinance에서 수집합니다."""

    DEFAULT_WARMUP_CALENDAR_DAYS = 90

    def __init__(
        self,
        *,
        downloader: Callable[..., pd.DataFrame] = yf.download,
        timeout_seconds: int = 15,
    ):
        self._downloader = downloader
        self._timeout_seconds = timeout_seconds

    def fetch_analysis_history(
        self,
        ticker: str,
        window: AnalysisWindow,
        *,
        warmup_calendar_days: int = DEFAULT_WARMUP_CALENDAR_DAYS,
    ) -> PriceHistory:
        """리포팅용 원본 일봉을 warm-up 기간과 함께 수집합니다.

        ``history``에는 RSI/MACD 같은 파생 지표를 추가하지 않으며, 지표
        결측치를 이유로 가격 행을 삭제하거나 채우지 않습니다. 보고 지표는
        :class:`analysis.market_analyzer.MarketAnalyzer`가 이 원본 데이터에서
        계산합니다.

        yfinance의 ``end``는 미포함이므로 실제 요청은 분석 종료일의 다음 날까지
        전달합니다. 반환 결과는 warm-up 시작일부터 분석 종료일까지로 다시
        제한해 제공자 응답에 미래 데이터가 섞여도 사용되지 않게 합니다.
        """
        normalized_ticker = self._normalize_ticker(ticker)
        if warmup_calendar_days < 0:
            raise ValueError("warmup_calendar_days must be 0 or greater")

        warmup_start = window.start_date - timedelta(days=warmup_calendar_days)
        provider_end_exclusive = window.end_date + timedelta(days=1)

        logger.info(
            "[%s] 원본 일봉 수집: %s ~ %s (보고 기간 %s ~ %s, warm-up %d일)",
            normalized_ticker,
            warmup_start,
            window.end_date,
            window.start_date,
            window.end_date,
            warmup_calendar_days,
        )

        try:
            downloaded = self._downloader(
                normalized_ticker,
                start=warmup_start.isoformat(),
                end=provider_end_exclusive.isoformat(),
                interval="1d",
                auto_adjust=True,
                progress=False,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            raise PriceFetchError(
                f"{normalized_ticker} 가격 제공자 호출에 실패했습니다."
            ) from exc

        history = self._normalize_analysis_history(
            downloaded,
            ticker=normalized_ticker,
            start_date=warmup_start,
            end_date=window.end_date,
        )

        report_dates = history.index.date
        report_mask = (report_dates >= window.start_date) & (
            report_dates <= window.end_date
        )
        report_observation_count = int(report_mask.sum())
        if report_observation_count == 0:
            raise PriceDataUnavailableError(
                f"{normalized_ticker}의 분석 기간({window.start_date} ~ "
                f"{window.end_date}) 가격 데이터가 없습니다."
            )

        return PriceHistory(
            ticker=normalized_ticker,
            window=window,
            warmup_start_date=warmup_start,
            fetch_end_date=window.end_date,
            report_observation_count=report_observation_count,
            history=history,
        )

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must not be empty")
        return ticker.strip().upper()

    @staticmethod
    def _normalize_analysis_history(
        downloaded: pd.DataFrame,
        *,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        if not isinstance(downloaded, pd.DataFrame) or downloaded.empty:
            raise PriceDataUnavailableError(
                f"{ticker} 가격 제공자가 빈 데이터를 반환했습니다."
            )

        history = downloaded.copy()
        if isinstance(history.columns, pd.MultiIndex):
            history.columns = history.columns.get_level_values(0)

        if "Close" not in history.columns:
            raise PriceFetchError(
                f"{ticker} 가격 데이터에 필수 Close 컬럼이 없습니다."
            )
        if history.columns.duplicated().any():
            raise PriceFetchError(
                f"{ticker} 가격 데이터에 중복 컬럼이 있습니다."
            )

        try:
            history.index = pd.DatetimeIndex(
                pd.to_datetime(history.index, errors="raise")
            )
        except (TypeError, ValueError) as exc:
            raise PriceFetchError(
                f"{ticker} 가격 데이터의 날짜 index를 해석할 수 없습니다."
            ) from exc

        if history.index.hasnans:
            raise PriceFetchError(
                f"{ticker} 가격 데이터의 날짜 index에 NaT가 포함되어 있습니다."
            )

        dates = history.index.date
        in_requested_range = (dates >= start_date) & (dates <= end_date)
        history = history.loc[in_requested_range].sort_index()
        if history.empty:
            raise PriceDataUnavailableError(
                f"{ticker} 요청 범위 안에 가격 데이터가 없습니다."
            )

        return history

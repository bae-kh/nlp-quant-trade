"""실제 가격 데이터로 최근 구간의 결정론적 시장 지표를 계산합니다."""

from __future__ import annotations

from datetime import date
from typing import Literal

import numpy as np
import pandas as pd
import ta
from pydantic import BaseModel, ConfigDict, Field

from workflow.analysis_window import AnalysisWindow


IndicatorStatus = Literal["available", "partial", "insufficient_history"]
BenchmarkStatus = Literal["available", "unavailable", "insufficient_data"]


class MarketDataError(ValueError):
    """시장 데이터가 계약을 만족하지 못할 때 발생하는 기본 오류입니다."""


class MarketDataValidationError(MarketDataError):
    """컬럼, 날짜, 가격 값이 유효하지 않을 때 발생합니다."""


class InsufficientMarketDataError(MarketDataError):
    """핵심 지표 계산에 필요한 가격 관측값이 부족할 때 발생합니다."""


class MarketAnalysis(BaseModel):
    """ReportBuilder가 사용할 결정론적 시장 분석 결과 계약입니다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    ticker: str
    requested_start_date: date
    requested_end_date: date
    actual_price_start: date
    actual_price_end: date
    price_observation_count: int = Field(ge=2)

    period_return: float
    annualized_volatility: float | None = Field(default=None, ge=0)
    max_drawdown: float = Field(le=0)

    latest_rsi: float | None = Field(default=None, ge=0, le=100)
    latest_macd_diff: float | None = None
    indicator_status: IndicatorStatus

    benchmark_ticker: str
    benchmark_return: float | None = None
    benchmark_difference: float | None = None
    benchmark_status: BenchmarkStatus

    warnings: tuple[str, ...] = ()


class MarketAnalyzer:
    """가격 수집과 LLM에서 독립된 순수 시장 지표 계산기입니다."""

    TRADING_DAYS_PER_YEAR = 252
    RSI_WINDOW = 14
    MACD_FAST_WINDOW = 12
    MACD_SLOW_WINDOW = 26
    MACD_SIGNAL_WINDOW = 9

    def analyze(
        self,
        price_history: pd.DataFrame,
        window: AnalysisWindow,
        *,
        benchmark_history: pd.DataFrame | None = None,
        benchmark_ticker: str = "SPY",
    ) -> MarketAnalysis:
        """Warm-up을 포함한 가격 이력에서 report window 지표를 계산합니다.

        기간 수익률·변동성·MDD는 ``AnalysisWindow`` 안의 가격만 사용합니다.
        RSI와 MACD는 window 이전 warm-up을 포함한 전체 이력으로 계산하되,
        window 종료일 이후 데이터는 구조적으로 제외합니다.
        """
        warnings: list[str] = []
        prepared = self._prepare_history(price_history, label=window.ticker)
        prepared = self._through_window_end(prepared, window)
        report_prices = self._inside_report_window(prepared, window)

        if len(report_prices) < 2:
            raise InsufficientMarketDataError(
                "분석 기간 안에 수익률 계산에 필요한 가격 관측값이 2개 미만입니다."
            )

        report_close = report_prices["Close"]
        period_return = float(report_close.iloc[-1] / report_close.iloc[0] - 1)
        max_drawdown = self._calculate_max_drawdown(report_close)
        annualized_volatility = self._calculate_annualized_volatility(report_close)

        if annualized_volatility is None:
            warnings.append(
                "연환산 변동성 계산에 필요한 일별 수익률 관측값이 부족합니다."
            )

        latest_rsi, latest_macd = self._calculate_latest_indicators(prepared["Close"])
        indicator_status = self._indicator_status(latest_rsi, latest_macd)
        if indicator_status != "available":
            warnings.append(
                "RSI 또는 MACD 계산에 필요한 warm-up 가격 이력이 부족합니다."
            )

        (
            benchmark_return,
            benchmark_difference,
            benchmark_status,
            benchmark_warning,
        ) = self._analyze_benchmark(
            benchmark_history,
            window,
            period_return,
            benchmark_ticker,
            comparison_start=report_prices.index[0].date(),
            comparison_end=report_prices.index[-1].date(),
        )
        if benchmark_warning:
            warnings.append(benchmark_warning)

        return MarketAnalysis(
            ticker=window.ticker,
            requested_start_date=window.start_date,
            requested_end_date=window.end_date,
            actual_price_start=report_prices.index[0].date(),
            actual_price_end=report_prices.index[-1].date(),
            price_observation_count=len(report_prices),
            period_return=period_return,
            annualized_volatility=annualized_volatility,
            max_drawdown=max_drawdown,
            latest_rsi=latest_rsi,
            latest_macd_diff=latest_macd,
            indicator_status=indicator_status,
            benchmark_ticker=benchmark_ticker.strip().upper(),
            benchmark_return=benchmark_return,
            benchmark_difference=benchmark_difference,
            benchmark_status=benchmark_status,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _prepare_history(history: pd.DataFrame, *, label: str) -> pd.DataFrame:
        """DataFrame을 검증하고 날짜 오름차순의 Close series로 정규화합니다."""
        if not isinstance(history, pd.DataFrame):
            raise MarketDataValidationError(f"{label} 가격 데이터는 DataFrame이어야 합니다.")
        if history.empty:
            raise InsufficientMarketDataError(f"{label} 가격 데이터가 비어 있습니다.")
        if "Close" not in history.columns:
            raise MarketDataValidationError(f"{label} 가격 데이터에 Close 컬럼이 없습니다.")
        if isinstance(history["Close"], pd.DataFrame):
            raise MarketDataValidationError(f"{label} 가격 데이터의 Close 컬럼이 중복되었습니다.")

        try:
            index = pd.DatetimeIndex(pd.to_datetime(history.index, errors="raise"))
        except (TypeError, ValueError) as exc:
            raise MarketDataValidationError(
                f"{label} 가격 데이터의 날짜 index를 해석할 수 없습니다."
            ) from exc

        if index.hasnans:
            raise MarketDataValidationError(
                f"{label} 가격 데이터의 날짜 index에 NaT가 포함되어 있습니다."
            )

        calendar_dates = pd.Index(index.date)
        if index.has_duplicates or calendar_dates.has_duplicates:
            raise MarketDataValidationError(f"{label} 가격 데이터에 중복 날짜가 있습니다.")

        try:
            close = pd.to_numeric(history["Close"], errors="raise").astype(float)
        except (TypeError, ValueError) as exc:
            raise MarketDataValidationError(
                f"{label} Close 값은 숫자여야 합니다."
            ) from exc

        close_values = close.to_numpy(dtype=float)
        if not np.isfinite(close_values).all():
            raise MarketDataValidationError(
                f"{label} Close 값에 NaN 또는 무한대가 포함되어 있습니다."
            )
        if (close_values <= 0).any():
            raise MarketDataValidationError(f"{label} Close 값은 0보다 커야 합니다.")

        prepared = pd.DataFrame({"Close": close_values}, index=index)
        return prepared.sort_index()

    @staticmethod
    def _through_window_end(
        history: pd.DataFrame,
        window: AnalysisWindow,
    ) -> pd.DataFrame:
        """미래 데이터가 indicator 계산에 들어가지 않도록 종료일 이후를 제거합니다."""
        return history[history.index.date <= window.end_date]

    @staticmethod
    def _inside_report_window(
        history: pd.DataFrame,
        window: AnalysisWindow,
    ) -> pd.DataFrame:
        """기간 metric 계산에 사용할 양 끝 날짜 포함 report window를 선택합니다."""
        dates = history.index.date
        mask = (dates >= window.start_date) & (dates <= window.end_date)
        return history.loc[mask]

    @classmethod
    def _calculate_annualized_volatility(cls, close: pd.Series) -> float | None:
        daily_returns = close.pct_change().dropna()
        if len(daily_returns) < 2:
            return None

        volatility = daily_returns.std(ddof=1) * np.sqrt(cls.TRADING_DAYS_PER_YEAR)
        if not np.isfinite(volatility):
            return None
        return float(volatility)

    @staticmethod
    def _calculate_max_drawdown(close: pd.Series) -> float:
        running_peak = close.cummax()
        drawdown = close / running_peak - 1
        return float(drawdown.min())

    @classmethod
    def _calculate_latest_indicators(
        cls,
        close: pd.Series,
    ) -> tuple[float | None, float | None]:
        try:
            rsi_series = ta.momentum.RSIIndicator(
                close=close,
                window=cls.RSI_WINDOW,
            ).rsi()
            macd_series = ta.trend.MACD(
                close=close,
                window_fast=cls.MACD_FAST_WINDOW,
                window_slow=cls.MACD_SLOW_WINDOW,
                window_sign=cls.MACD_SIGNAL_WINDOW,
            ).macd_diff()
        except (IndexError, TypeError, ValueError):
            return None, None

        return cls._finite_or_none(rsi_series.iloc[-1]), cls._finite_or_none(
            macd_series.iloc[-1]
        )

    @staticmethod
    def _finite_or_none(value: object) -> float | None:
        if pd.isna(value):
            return None
        numeric = float(value)
        return numeric if np.isfinite(numeric) else None

    @staticmethod
    def _indicator_status(
        latest_rsi: float | None,
        latest_macd: float | None,
    ) -> IndicatorStatus:
        available_count = sum(value is not None for value in (latest_rsi, latest_macd))
        if available_count == 2:
            return "available"
        if available_count == 1:
            return "partial"
        return "insufficient_history"

    def _analyze_benchmark(
        self,
        benchmark_history: pd.DataFrame | None,
        window: AnalysisWindow,
        period_return: float,
        benchmark_ticker: str,
        *,
        comparison_start: date,
        comparison_end: date,
    ) -> tuple[float | None, float | None, BenchmarkStatus, str | None]:
        if benchmark_history is None:
            return (
                None,
                None,
                "unavailable",
                f"{benchmark_ticker} benchmark 데이터를 사용할 수 없습니다.",
            )

        if not isinstance(benchmark_history, pd.DataFrame) or benchmark_history.empty:
            return (
                None,
                None,
                "unavailable",
                f"{benchmark_ticker} benchmark 데이터를 사용할 수 없습니다.",
            )

        try:
            prepared = self._prepare_history(
                benchmark_history,
                label=benchmark_ticker,
            )
            prepared = self._through_window_end(prepared, window)
            report_benchmark = self._inside_report_window(prepared, window)
        except MarketDataError as exc:
            return None, None, "unavailable", str(exc)

        benchmark_dates = report_benchmark.index.date
        has_comparison_start = comparison_start in benchmark_dates
        has_comparison_end = comparison_end in benchmark_dates
        if not has_comparison_start or not has_comparison_end:
            return (
                None,
                None,
                "insufficient_data",
                f"{benchmark_ticker} benchmark에 대상 종목과 동일한 비교 시작일"
                f"({comparison_start}) 또는 종료일({comparison_end}) 가격이 없습니다.",
            )

        start_close = report_benchmark.loc[
            benchmark_dates == comparison_start,
            "Close",
        ].iloc[0]
        end_close = report_benchmark.loc[
            benchmark_dates == comparison_end,
            "Close",
        ].iloc[0]
        benchmark_return = float(end_close / start_close - 1)
        benchmark_difference = float(period_return - benchmark_return)
        return benchmark_return, benchmark_difference, "available", None

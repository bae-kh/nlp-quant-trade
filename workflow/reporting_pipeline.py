"""최근 가격 수집과 결정론적 시장 분석을 연결하는 workflow입니다."""

from __future__ import annotations

import logging

from analysis.market_analyzer import MarketAnalysis, MarketAnalyzer, MarketDataError
from data_pipeline.price_fetcher import PriceFetchError, PriceFetcher
from workflow.analysis_window import AnalysisWindow


logger = logging.getLogger(__name__)


class ReportingPipelineError(RuntimeError):
    """필수 시장 리포팅 단계가 완료되지 못했을 때 발생합니다."""


class ReportingPipeline:
    """가격 제공자와 시장 분석기를 순서대로 실행합니다.

    대상 종목 가격과 시장 분석은 필수 단계입니다. 벤치마크 수집은 선택
    단계이므로 실패해도 ``MarketAnalyzer``에 ``None``을 전달해
    ``benchmark_status=unavailable``인 결과를 생성합니다.
    """

    def __init__(
        self,
        *,
        price_fetcher: PriceFetcher | None = None,
        market_analyzer: MarketAnalyzer | None = None,
        benchmark_ticker: str = "SPY",
    ) -> None:
        normalized_benchmark = benchmark_ticker.strip().upper()
        if not normalized_benchmark:
            raise ValueError("benchmark_ticker must not be empty")

        self.price_fetcher = (
            price_fetcher if price_fetcher is not None else PriceFetcher()
        )
        self.market_analyzer = (
            market_analyzer if market_analyzer is not None else MarketAnalyzer()
        )
        self.benchmark_ticker = normalized_benchmark

    def run(self, window: AnalysisWindow) -> MarketAnalysis:
        """동일한 AnalysisWindow로 대상 종목과 benchmark를 분석합니다."""
        try:
            target_prices = self.price_fetcher.fetch_analysis_history(
                window.ticker,
                window,
            )
        except PriceFetchError as exc:
            raise ReportingPipelineError(
                f"{window.ticker} 필수 가격 수집에 실패했습니다: {exc}"
            ) from exc

        benchmark_history = None
        try:
            benchmark_prices = self.price_fetcher.fetch_analysis_history(
                self.benchmark_ticker,
                window,
            )
            benchmark_history = benchmark_prices.history
        except PriceFetchError as exc:
            logger.warning(
                "[%s] benchmark 가격 수집 실패. 대상 종목 분석은 계속합니다: %s",
                self.benchmark_ticker,
                exc,
            )

        try:
            return self.market_analyzer.analyze(
                target_prices.history,
                window,
                benchmark_history=benchmark_history,
                benchmark_ticker=self.benchmark_ticker,
            )
        except MarketDataError as exc:
            raise ReportingPipelineError(
                f"{window.ticker} 필수 시장 분석에 실패했습니다: {exc}"
            ) from exc

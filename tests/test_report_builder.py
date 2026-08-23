"""결정론적 Markdown ReportBuilder의 구조와 fallback 표현을 검증합니다."""

from datetime import date, datetime, timezone

import pytest

from analysis.market_analyzer import MarketAnalysis
from analysis.news_analyzer import (
    NewsAnalysis,
    SelectedArticleEvidence,
    TopicEvidence,
)
from data_pipeline.news_fetcher import (
    NewsFetchMetadata,
    NewsFetchResult,
    NewsItem,
)
from report.report_builder import ReportBuilder
from workflow.analysis_window import AnalysisWindow


def make_window(ticker: str = "TSLA") -> AnalysisWindow:
    return AnalysisWindow(
        ticker=ticker,
        requested_analysis_days=30,
        start_date=date(2026, 7, 13),
        end_date=date(2026, 8, 11),
    )


def make_market(ticker: str = "TSLA") -> MarketAnalysis:
    return MarketAnalysis(
        ticker=ticker,
        requested_start_date=date(2026, 7, 13),
        requested_end_date=date(2026, 8, 11),
        actual_price_start=date(2026, 7, 13),
        actual_price_end=date(2026, 8, 11),
        price_observation_count=21,
        period_return=0.1309,
        annualized_volatility=0.6719,
        max_drawdown=-0.1584,
        latest_rsi=51.0,
        latest_macd_diff=-7.0665,
        indicator_status="available",
        benchmark_ticker="SPY",
        benchmark_return=-0.0258,
        benchmark_difference=0.1567,
        benchmark_status="available",
    )


def make_news(window: AnalysisWindow) -> NewsFetchResult:
    item = NewsItem(
        article_id="news_0123456789abcdef",
        title="Tesla [earnings](unsafe) | update #1",
        published_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        url="https://news.google.com/articles/example",
        source="Example | News",
    )
    return NewsFetchResult(
        ticker=window.ticker,
        window=window,
        status="available",
        available=True,
        items=(item,),
        metadata=NewsFetchMetadata(
            query="TSLA stock",
            fetched_at=datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
            raw_item_count=1,
            invalid_item_count=0,
            outside_window_count=0,
            in_window_item_count=1,
            duplicate_item_count=0,
            truncated_item_count=0,
            stored_item_count=1,
            warnings=("Google News RSS coverage는 unknown입니다.",),
        ),
    )


def make_available_analysis(news: NewsFetchResult) -> NewsAnalysis:
    item = news.items[0]
    return NewsAnalysis(
        ticker=news.ticker,
        news_fetch_status=news.status,
        available=True,
        sentiment="positive",
        score=0.4,
        confidence=78,
        summary="실적 관련 보도가 확인됐습니다. # 지시문 아님",
        key_topics=(
            TopicEvidence(
                topic="실적 발표",
                explanation="제공된 제목에서 실적 관련 내용을 확인했습니다.",
                supporting_article_ids=(item.article_id,),
            ),
        ),
        input_article_count=1,
        selected_article_count=1,
        analyzed_article_count=1,
        selected_article_ids=(item.article_id,),
        selected_articles=(
            SelectedArticleEvidence(
                article_id=item.article_id,
                published_at=item.published_at,
                source=item.source,
                importance_score=5,
                importance_signals=("fundamental_results",),
                selection_reason="all",
            ),
        ),
        selection_strategy="all",
        model="gpt-test",
        fallback_used=False,
    )


def test_builds_python_owned_report_with_evidence_and_escaped_text():
    window = make_window()
    news = make_news(window)

    report = ReportBuilder().build(
        run_id="run_test_TSLA",
        window=window,
        market=make_market(),
        news=news,
        news_analysis=make_available_analysis(news),
        generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
    )

    assert "# TSLA 금융 분석 리포트" in report
    assert "| Run ID | `run_test_TSLA` |" in report
    assert "| 기간 수익률 | 13.09% | Python |" in report
    assert "| Benchmark 수익률 | -2.58% |" in report
    assert "## 5. LLM 뉴스 분석" in report
    assert "`news_0123456789abcdef`" in report
    assert "\\[earnings\\](unsafe) \\| update \\#1" in report
    assert "실적 관련 보도가 확인됐습니다. \\# 지시문 아님" in report
    assert "투자 조언이 아닙니다" in report


def test_unavailable_analysis_is_not_rendered_as_neutral():
    window = make_window()
    news = make_news(window)
    unavailable = NewsAnalysis(
        ticker="TSLA",
        news_fetch_status="available",
        available=False,
        input_article_count=1,
        selected_article_count=0,
        analyzed_article_count=0,
        fallback_used=True,
        error_code="missing_api_key",
        warnings=("API 키가 없습니다.",),
    )

    report = ReportBuilder().build(
        run_id="run_test_TSLA",
        window=window,
        market=make_market(),
        news=news,
        news_analysis=unavailable,
        generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
    )

    assert "분석 상태 | unavailable" in report
    assert r"오류 코드 | missing\_api\_key" in report
    assert "감성 | N/A" in report
    assert "감성 | neutral" not in report


def test_rejects_mismatched_ticker_inputs():
    window = make_window()
    news = make_news(window)

    with pytest.raises(ValueError, match="same ticker"):
        ReportBuilder().build(
            run_id="run_test_TSLA",
            window=window,
            market=make_market(ticker="AAPL"),
            news=news,
            news_analysis=make_available_analysis(news),
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )


def test_rejects_naive_generation_time():
    window = make_window()
    news = make_news(window)

    with pytest.raises(ValueError, match="timezone-aware"):
        ReportBuilder().build(
            run_id="run_test_TSLA",
            window=window,
            market=make_market(),
            news=news,
            news_analysis=make_available_analysis(news),
            generated_at=datetime(2026, 8, 11, 13),
        )


def test_rejects_empty_run_id():
    window = make_window()
    news = make_news(window)

    with pytest.raises(ValueError, match="run_id cannot be empty"):
        ReportBuilder().build(
            run_id=" ",
            window=window,
            market=make_market(),
            news=news,
            news_analysis=make_available_analysis(news),
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )

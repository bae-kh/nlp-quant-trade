"""구조화 NewsAnalyzer의 선택, evidence 검증, fallback 정책을 검증합니다."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from analysis.news_analyzer import (
    NewsAnalyzer,
    NewsLLMOutput,
    SelectedArticleEvidence,
    TopicEvidence,
)
from data_pipeline.news_fetcher import (
    NewsFetchMetadata,
    NewsFetchResult,
    NewsItem,
)
from workflow.analysis_window import AnalysisWindow


def make_window() -> AnalysisWindow:
    return AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=30,
        start_date=date(2026, 7, 22),
        end_date=date(2026, 8, 20),
    )


def make_items(count: int) -> tuple[NewsItem, ...]:
    start = datetime(2026, 7, 22, tzinfo=timezone.utc)
    return tuple(
        NewsItem(
            article_id=f"news_{index:016x}",
            title=f"TSLA headline {index}",
            published_at=start + timedelta(hours=index),
            url=f"https://news.google.com/articles/{index}",
            source=f"Source {index % 3}",
        )
        for index in range(count)
    )


def make_news(
    count: int = 3,
    *,
    status: str = "available",
) -> NewsFetchResult:
    items = make_items(count)
    available = status in {"available", "partial"}
    if not available:
        items = ()
    return NewsFetchResult(
        ticker="TSLA",
        window=make_window(),
        status=status,
        available=available,
        items=items,
        metadata=NewsFetchMetadata(
            query="TSLA stock",
            fetched_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            window_query_status="partial" if status == "partial" else (
                "failed" if status == "unavailable" else "complete"
            ),
            raw_item_count=count,
            invalid_item_count=0,
            outside_window_count=0,
            in_window_item_count=count,
            duplicate_item_count=0,
            truncated_item_count=0,
            stored_item_count=len(items),
            error_code="network_error" if status == "unavailable" else None,
        ),
    )


def valid_output(article_id: str, *, sentiment: str = "positive"):
    return NewsLLMOutput(
        sentiment=sentiment,
        score=0.5 if sentiment == "positive" else 0.0,
        confidence=80,
        summary="제공된 헤드라인에서 긍정적인 제품 관련 보도가 확인됐습니다.",
        key_topics=(
            TopicEvidence(
                topic="제품 관련 보도",
                explanation="제품 관련 헤드라인이 긍정적인 방향을 보였습니다.",
                supporting_article_ids=(article_id,),
            ),
        ),
    )


class StubResponses:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_parsed=self.output)


class StubClient:
    def __init__(self, output=None, error=None):
        self.responses = StubResponses(output=output, error=error)


def make_dated_item(index: int, day_offset: int, title: str) -> NewsItem:
    return NewsItem(
        article_id=f"news_{index:016x}",
        title=title,
        published_at=datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        + timedelta(days=day_offset),
        url=f"https://news.google.com/articles/dated-{index}",
        source="Example Source",
    )


def test_hybrid_selection_spans_full_period_deterministically():
    analyzer = NewsAnalyzer(client=StubClient(), max_selected_articles=5)
    items = tuple(
        make_dated_item(index, index, f"General company headline {index}")
        for index in range(30)
    )

    first, strategy = analyzer.select_articles(items, window=make_window())
    second, _ = analyzer.select_articles(
        tuple(reversed(items)),
        window=make_window(),
    )

    assert strategy == "time_balanced_importance"
    assert len(first) == 5
    assert first == second
    assert first[0].published_at.date() <= date(2026, 7, 24)
    assert first[-1].article_id == items[-1].article_id


def test_selection_uses_all_items_when_below_limit():
    analyzer = NewsAnalyzer(client=StubClient(), max_selected_articles=5)

    selected, strategy = analyzer.select_articles(make_items(3))

    assert len(selected) == 3
    assert strategy == "all"


def test_single_selection_prefers_latest_article():
    analyzer = NewsAnalyzer(client=StubClient(), max_selected_articles=1)
    items = make_items(5)

    selected, strategy = analyzer.select_articles(items)

    assert selected == (items[-1],)
    assert strategy == "time_balanced_importance"


def test_importance_score_is_event_based_and_direction_neutral():
    positive = NewsAnalyzer.score_article_importance(
        "Tesla earnings beat expectations and raises guidance"
    )
    negative = NewsAnalyzer.score_article_importance(
        "Tesla earnings miss expectations and cuts guidance"
    )
    generic = NewsAnalyzer.score_article_importance(
        "Tesla stock moves higher in afternoon trading"
    )

    assert positive == negative
    assert positive[0] == 5
    assert positive[1] == ("fundamental_results",)
    assert generic == (0, ())


def test_importance_score_does_not_use_source_prestige():
    title = "Tesla announces vehicle recall after regulator investigation"
    first = make_dated_item(1, 0, title).model_copy(
        update={"source": "Large Global Publisher"}
    )
    second = make_dated_item(2, 0, title).model_copy(
        update={"source": "Small Local Publisher"}
    )

    assert NewsAnalyzer.score_article_importance(first.title) == (
        NewsAnalyzer.score_article_importance(second.title)
    )


def test_time_balance_keeps_early_middle_and_late_dates_despite_recent_density():
    sparse = (
        make_dated_item(1, 0, "Early general update"),
        make_dated_item(2, 10, "First middle general update"),
        make_dated_item(3, 20, "Second middle general update"),
        make_dated_item(4, 29, "Late general update"),
    )
    recent = tuple(
        make_dated_item(100 + index, 29, f"Recent commentary {index}")
        for index in range(20)
    )
    analyzer = NewsAnalyzer(client=StubClient(), max_selected_articles=6)

    selected, metadata, strategy = analyzer.select_articles_with_metadata(
        (*sparse, *recent),
        window=make_window(),
    )

    selected_dates = {item.published_at.date() for item in selected}
    assert strategy == "time_balanced_importance"
    assert datetime(2026, 7, 22, tzinfo=timezone.utc).date() in selected_dates
    assert datetime(2026, 8, 1, tzinfo=timezone.utc).date() in selected_dates
    assert datetime(2026, 8, 11, tzinfo=timezone.utc).date() in selected_dates
    assert datetime(2026, 8, 20, tzinfo=timezone.utc).date() in selected_dates
    assert sum(item.selection_reason == "time_balance" for item in metadata) == 4


def test_global_importance_reserve_keeps_second_major_event_in_same_date_bucket():
    items = tuple(
        make_dated_item(index, index, f"General update {index}")
        for index in range(10)
    )
    earnings = make_dated_item(
        100,
        3,
        "Tesla reports quarterly earnings and revenue",
    )
    recall = make_dated_item(
        101,
        3,
        "Tesla announces major vehicle recall",
    )
    ten_day_window = AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=10,
        start_date=date(2026, 7, 22),
        end_date=date(2026, 7, 31),
    )
    analyzer = NewsAnalyzer(client=StubClient(), max_selected_articles=5)

    selected, metadata, _strategy = analyzer.select_articles_with_metadata(
        (*items, earnings, recall),
        window=ten_day_window,
    )

    selected_ids = {item.article_id for item in selected}
    reasons = {item.article_id: item.selection_reason for item in metadata}
    assert earnings.article_id in selected_ids
    assert recall.article_id in selected_ids
    assert reasons[earnings.article_id] == "time_balance"
    assert reasons[recall.article_id] == "global_importance"


def test_successful_structured_analysis_preserves_evidence_ids():
    news = make_news(3)
    output = valid_output(news.items[0].article_id)
    client = StubClient(output=output)
    analyzer = NewsAnalyzer(client=client, model="gpt-test")

    result = asyncio.run(analyzer.analyze(news))

    assert result.available is True
    assert result.sentiment == "positive"
    assert result.fallback_used is False
    assert result.analyzed_article_count == 3
    assert result.key_topics[0].supporting_article_ids == (
        news.items[0].article_id,
    )
    assert tuple(item.article_id for item in result.selected_articles) == (
        result.selected_article_ids
    )
    assert all(
        isinstance(item, SelectedArticleEvidence)
        for item in result.selected_articles
    )
    call = client.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["text_format"] is NewsLLMOutput
    assert call["timeout"] == 30


def test_actual_neutral_is_available_not_fallback():
    news = make_news(2)
    client = StubClient(
        output=valid_output(news.items[0].article_id, sentiment="neutral")
    )

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert result.available is True
    assert result.sentiment == "neutral"
    assert result.score == 0.0
    assert result.fallback_used is False


def test_unknown_evidence_id_invalidates_entire_llm_output():
    news = make_news(3)
    client = StubClient(output=valid_output("news_ffffffffffffffff"))

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert result.available is False
    assert result.sentiment is None
    assert result.error_code == "validation_error"
    assert result.fallback_used is True
    assert result.analyzed_article_count == 0


def test_invalid_structured_values_use_validation_fallback():
    news = make_news(2)
    invalid_output = {
        "sentiment": "positive",
        "score": 2.0,
        "confidence": 150,
        "summary": "invalid",
        "key_topics": [],
    }
    client = StubClient(output=invalid_output)

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert result.available is False
    assert result.error_code == "validation_error"


@pytest.mark.parametrize(
    ("sentiment", "score"),
    [
        ("positive", -0.3),
        ("negative", 0.3),
        ("neutral", 0.8),
    ],
)
def test_sentiment_label_and_score_must_be_consistent(sentiment, score):
    with pytest.raises(ValueError, match="sentiment requires"):
        NewsLLMOutput(
            sentiment=sentiment,
            score=score,
            confidence=80,
            summary="감성 라벨과 점수 방향이 일치해야 합니다.",
            key_topics=(
                TopicEvidence(
                    topic="검증 주제",
                    explanation="검증을 위한 설명입니다.",
                    supporting_article_ids=("news_0000000000000000",),
                ),
            ),
        )


def test_empty_news_skips_llm_and_is_not_neutral():
    news = make_news(0, status="empty")
    client = StubClient()

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert client.responses.calls == []
    assert result.available is False
    assert result.sentiment is None
    assert result.error_code == "no_news_in_window"
    assert result.fallback_used is True


def test_unavailable_collection_skips_llm():
    news = make_news(0, status="unavailable")
    client = StubClient()

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert client.responses.calls == []
    assert result.available is False
    assert result.error_code == "news_unavailable"


def test_missing_api_key_returns_explicit_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    news = make_news(3)

    result = asyncio.run(NewsAnalyzer(api_key="").analyze(news))

    assert result.available is False
    assert result.error_code == "missing_api_key"
    assert result.selected_article_count == 3
    assert result.analyzed_article_count == 0


def test_api_error_returns_unavailable_without_neutral_score():
    news = make_news(3)
    client = StubClient(error=RuntimeError("API down"))

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert result.available is False
    assert result.sentiment is None
    assert result.score is None
    assert result.error_code == "llm_error"


def test_partial_collection_analysis_keeps_coverage_warning():
    news = make_news(3, status="partial")
    client = StubClient(output=valid_output(news.items[0].article_id))

    result = asyncio.run(NewsAnalyzer(client=client).analyze(news))

    assert result.available is True
    assert result.news_fetch_status == "partial"
    assert any("partial" in warning for warning in result.warnings)


def test_large_snapshot_records_selection_reduction_in_prompt_and_result():
    news = make_news(100)
    analyzer = NewsAnalyzer(client=StubClient(), max_selected_articles=10)
    selected, _ = analyzer.select_articles(news.items, window=news.window)
    output = valid_output(selected[0].article_id)
    client = StubClient(output=output)
    analyzer = NewsAnalyzer(client=client, max_selected_articles=10)

    result = asyncio.run(analyzer.analyze(news))

    assert result.input_article_count == 100
    assert result.selected_article_count == 10
    assert result.analyzed_article_count == 10
    assert any("100건" in warning and "10건" in warning for warning in result.warnings)
    user_prompt = client.responses.calls[0]["input"][1]["content"]
    assert '"stored_article_count":100' in user_prompt
    assert '"selected_article_count":10' in user_prompt
    assert '"importance_score":' in user_prompt
    assert '"importance_signals":' in user_prompt
    assert '"selection_reason":' in user_prompt
    assert "https://" not in user_prompt


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("model", "   "),
        ("timeout_seconds", 0),
        ("max_selected_articles", 0),
        ("time_balance_ratio", 0),
        ("time_balance_ratio", 1),
        ("time_bucket_count", 0),
    ],
)
def test_rejects_invalid_configuration(keyword, value):
    with pytest.raises(ValueError):
        NewsAnalyzer(client=StubClient(), **{keyword: value})

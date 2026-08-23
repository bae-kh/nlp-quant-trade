"""AnalysisWindow 기반 구조화 뉴스 수집과 실패 상태를 검증합니다."""

from datetime import date, datetime, timezone

import pytest
import requests

from data_pipeline.news_fetcher import NewsFetcher
from workflow.analysis_window import AnalysisWindow


class FakeResponse:
    def __init__(self, content: bytes, error: Exception | None = None):
        self.content = content
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error


def make_window() -> AnalysisWindow:
    return AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=30,
        start_date=date(2026, 7, 22),
        end_date=date(2026, 8, 20),
    )


def rss_item(
    title: str,
    published_at: str,
    url: str,
    source: str,
) -> str:
    return f"""
    <item>
      <title>{title}</title>
      <pubDate>{published_at}</pubDate>
      <link>{url}</link>
      <source>{source}</source>
    </item>
    """


def rss_feed(*items: str) -> bytes:
    return (
        "<?xml version='1.0' encoding='UTF-8'?><rss><channel>"
        + "".join(items)
        + "</channel></rss>"
    ).encode("utf-8")


def fixed_fetch_time() -> datetime:
    return datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_filters_with_inclusive_new_york_calendar_boundaries():
    feed = rss_feed(
        rss_item(
            "Before window",
            "Wed, 22 Jul 2026 03:59:00 GMT",
            "https://news.google.com/articles/before",
            "Source A",
        ),
        rss_item(
            "Start boundary",
            "Wed, 22 Jul 2026 04:00:00 GMT",
            "https://news.google.com/articles/start",
            "Source B",
        ),
        rss_item(
            "End boundary",
            "Fri, 21 Aug 2026 03:59:00 GMT",
            "https://news.google.com/articles/end",
            "Source C",
        ),
        rss_item(
            "After window",
            "Fri, 21 Aug 2026 04:00:00 GMT",
            "https://news.google.com/articles/after",
            "Source D",
        ),
    )
    calls = []

    def http_get(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(feed)

    result = NewsFetcher(http_get=http_get).fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "available"
    assert result.available is True
    assert [item.title for item in result.items] == ["End boundary", "Start boundary"]
    assert result.metadata.raw_item_count == 4
    assert result.metadata.in_window_item_count == 2
    assert result.metadata.outside_window_count == 2
    assert result.metadata.stored_item_count == 2
    assert result.metadata.source_coverage == "unknown"
    assert calls[0][0] == (NewsFetcher.RSS_URL,)
    assert calls[0][1]["params"]["q"] == (
        "TSLA stock after:2026-07-21 before:2026-08-21"
    )


def test_removes_duplicate_url_and_normalized_title():
    feed = rss_feed(
        rss_item(
            "Tesla delivery update",
            "Thu, 20 Aug 2026 10:00:00 GMT",
            "https://news.google.com/articles/one/",
            "Source A",
        ),
        rss_item(
            "Different title but same URL",
            "Thu, 20 Aug 2026 09:00:00 GMT",
            "https://NEWS.google.com/articles/one#fragment",
            "Source B",
        ),
        rss_item(
            "  TESLA   DELIVERY UPDATE  ",
            "Thu, 20 Aug 2026 08:00:00 GMT",
            "https://news.google.com/articles/two",
            "Source C",
        ),
    )
    fetcher = NewsFetcher(http_get=lambda *args, **kwargs: FakeResponse(feed))

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert len(result.items) == 1
    assert result.metadata.in_window_item_count == 3
    assert result.metadata.duplicate_item_count == 2
    assert result.items[0].url == "https://news.google.com/articles/one"


def test_excludes_items_with_invalid_required_metadata():
    feed = rss_feed(
        rss_item(
            "Valid headline",
            "Thu, 20 Aug 2026 10:00:00 GMT",
            "https://news.google.com/articles/valid",
            "Source A",
        ),
        rss_item(
            "Missing source",
            "Thu, 20 Aug 2026 09:00:00 GMT",
            "https://news.google.com/articles/no-source",
            "",
        ),
        rss_item(
            "Invalid date",
            "not a date",
            "https://news.google.com/articles/no-date",
            "Source B",
        ),
        rss_item(
            "Invalid URL",
            "Thu, 20 Aug 2026 08:00:00 GMT",
            "javascript:alert(1)",
            "Source C",
        ),
    )
    fetcher = NewsFetcher(http_get=lambda *args, **kwargs: FakeResponse(feed))

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert len(result.items) == 1
    assert result.metadata.invalid_item_count == 3
    assert any("3건" in warning for warning in result.metadata.warnings)


def test_applies_internal_limit_after_deduplication():
    feed = rss_feed(
        *[
            rss_item(
                f"Headline {number}",
                f"Thu, 20 Aug 2026 {number:02d}:00:00 GMT",
                f"https://news.google.com/articles/{number}",
                "Source",
            )
            for number in range(5)
        ]
    )
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(feed),
        max_snapshot_items=2,
    )

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert len(result.items) == 2
    assert result.metadata.truncated_item_count == 3
    assert [item.title for item in result.items] == ["Headline 4", "Headline 3"]


def test_saturated_date_range_is_split_until_child_ranges_are_below_limit():
    window = AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=4,
        start_date=date(2026, 7, 22),
        end_date=date(2026, 7, 25),
    )
    saturated_parent = rss_feed(
        *[
            rss_item(
                f"Parent {number}",
                "Wed, 22 Jul 2026 12:00:00 GMT",
                f"https://news.google.com/articles/parent-{number}",
                "Source",
            )
            for number in range(100)
        ]
    )
    first_child = rss_feed(
        rss_item(
            "Child one",
            "Wed, 22 Jul 2026 12:00:00 GMT",
            "https://news.google.com/articles/child-one",
            "Source",
        ),
        rss_item(
            "Child two",
            "Thu, 23 Jul 2026 12:00:00 GMT",
            "https://news.google.com/articles/child-two",
            "Source",
        ),
    )
    second_child = rss_feed(
        rss_item(
            "Child three",
            "Fri, 24 Jul 2026 12:00:00 GMT",
            "https://news.google.com/articles/child-three",
            "Source",
        ),
        rss_item(
            "Child four",
            "Sat, 25 Jul 2026 12:00:00 GMT",
            "https://news.google.com/articles/child-four",
            "Source",
        ),
    )
    responses = iter([saturated_parent, first_child, second_child])
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(next(responses)),
        initial_chunk_days=4,
    )

    result = fetcher.fetch_for_window(window, fetched_at=fixed_fetch_time())

    assert result.status == "available"
    assert result.metadata.window_query_status == "complete"
    assert result.metadata.request_count == 3
    assert result.metadata.saturated_request_count == 1
    assert result.metadata.unresolved_saturated_ranges == ()
    assert result.metadata.raw_item_count == 4
    assert result.metadata.stored_item_count == 4


def test_one_day_saturation_is_reported_as_partial_coverage():
    window = AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=1,
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 20),
    )
    saturated_day = rss_feed(
        *[
            rss_item(
                f"One day headline {number}",
                "Thu, 20 Aug 2026 12:00:00 GMT",
                f"https://news.google.com/articles/day-{number}",
                "Source",
            )
            for number in range(100)
        ]
    )
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(saturated_day)
    )

    result = fetcher.fetch_for_window(window, fetched_at=fixed_fetch_time())

    assert result.status == "partial"
    assert result.available is True
    assert result.metadata.window_query_status == "partial"
    assert result.metadata.unresolved_saturated_ranges == (
        "2026-08-20..2026-08-20",
    )
    assert result.metadata.stored_item_count == 100
    assert any("100건 상한" in warning for warning in result.metadata.warnings)


def test_ninety_day_window_is_queried_in_multiple_date_chunks():
    window = AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=90,
        start_date=date(2026, 5, 23),
        end_date=date(2026, 8, 20),
    )
    calls = []

    def http_get(*args, **kwargs):
        calls.append(kwargs["params"]["q"])
        return FakeResponse(rss_feed())

    result = NewsFetcher(http_get=http_get).fetch_for_window(
        window,
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "empty"
    assert result.metadata.window_query_status == "complete"
    assert result.metadata.request_count == 3
    assert len(calls) == 3
    assert "after:2026-05-22" in calls[0]
    assert "before:2026-08-21" in calls[-1]


def test_request_budget_exhaustion_marks_unqueried_ranges():
    window = AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=60,
        start_date=date(2026, 6, 22),
        end_date=date(2026, 8, 20),
    )
    first_batch = rss_feed(
        rss_item(
            "First queried range",
            "Wed, 22 Jul 2026 12:00:00 GMT",
            "https://news.google.com/articles/first-range",
            "Source",
        )
    )
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(first_batch),
        max_requests=1,
    )

    result = fetcher.fetch_for_window(window, fetched_at=fixed_fetch_time())

    assert result.status == "partial"
    assert result.metadata.request_budget_exhausted is True
    assert result.metadata.window_query_status == "partial"
    assert result.metadata.unqueried_ranges == ("2026-07-22..2026-08-20",)


def test_article_id_is_stable_across_collection_times():
    feed = rss_feed(
        rss_item(
            "Stable evidence headline",
            "Thu, 20 Aug 2026 10:00:00 GMT",
            "https://news.google.com/articles/stable",
            "Source A",
        )
    )
    fetcher = NewsFetcher(http_get=lambda *args, **kwargs: FakeResponse(feed))

    first = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )
    second = fetcher.fetch_for_window(
        make_window(),
        fetched_at=datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc),
    )

    assert first.items[0].article_id == second.items[0].article_id
    assert first.items[0].article_id.startswith("news_")


def test_successful_feed_without_window_items_is_empty_not_unavailable():
    feed = rss_feed(
        rss_item(
            "Old headline",
            "Mon, 01 Jun 2026 10:00:00 GMT",
            "https://news.google.com/articles/old",
            "Source A",
        )
    )
    fetcher = NewsFetcher(http_get=lambda *args, **kwargs: FakeResponse(feed))

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "empty"
    assert result.available is False
    assert result.metadata.error_code is None
    assert result.items == ()


def test_timeout_is_unavailable_not_empty():
    def timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("too slow")

    result = NewsFetcher(http_get=timeout).fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "unavailable"
    assert result.available is False
    assert result.metadata.error_code == "timeout"
    assert result.metadata.raw_item_count == 0


def test_http_error_is_structured_as_network_error():
    response = FakeResponse(
        b"",
        error=requests.exceptions.HTTPError("503 Service Unavailable"),
    )
    fetcher = NewsFetcher(http_get=lambda *args, **kwargs: response)

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "unavailable"
    assert result.metadata.error_code == "network_error"


def test_invalid_xml_is_structured_as_unavailable():
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(b"not <valid> xml")
    )

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "unavailable"
    assert result.metadata.error_code == "invalid_xml"


def test_well_formed_non_rss_xml_is_unavailable_not_empty():
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(b"<html><body/></html>")
    )

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "unavailable"
    assert result.metadata.error_code == "invalid_feed"


def test_oversized_response_is_rejected_before_xml_parsing(monkeypatch):
    monkeypatch.setattr(NewsFetcher, "MAX_RESPONSE_BYTES", 10)
    fetcher = NewsFetcher(
        http_get=lambda *args, **kwargs: FakeResponse(b"x" * 11)
    )

    result = fetcher.fetch_for_window(
        make_window(),
        fetched_at=fixed_fetch_time(),
    )

    assert result.status == "unavailable"
    assert result.metadata.error_code == "response_too_large"


def test_rejects_naive_fetch_timestamp():
    fetcher = NewsFetcher(http_get=lambda *args, **kwargs: FakeResponse(rss_feed()))

    with pytest.raises(ValueError, match="timezone-aware"):
        fetcher.fetch_for_window(
            make_window(),
            fetched_at=datetime(2026, 8, 20, 12, 0),
        )


@pytest.mark.parametrize(
    ("keyword", "value"),
    [("timeout", 0), ("max_snapshot_items", 0)],
)
def test_rejects_non_positive_safety_settings(keyword, value):
    with pytest.raises(ValueError):
        NewsFetcher(**{keyword: value})

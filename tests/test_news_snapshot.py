"""뉴스 Snapshot의 원자적 JSON 저장과 덮어쓰기 정책을 검증합니다."""

import json
from datetime import date, datetime, timezone

import pytest

from data_pipeline.news_fetcher import (
    NewsFetchMetadata,
    NewsFetchResult,
    NewsItem,
)
from workflow.analysis_window import AnalysisWindow
from workflow.news_snapshot import NewsSnapshotStore


def make_result(title: str = "테슬라 구조화 뉴스") -> NewsFetchResult:
    window = AnalysisWindow(
        ticker="TSLA",
        requested_analysis_days=30,
        start_date=date(2026, 7, 22),
        end_date=date(2026, 8, 20),
    )
    fetched_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    item = NewsItem(
        article_id="news_0123456789abcdef",
        title=title,
        published_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        url="https://news.google.com/articles/example",
        source="Example News",
    )
    return NewsFetchResult(
        ticker="TSLA",
        window=window,
        status="available",
        available=True,
        items=(item,),
        metadata=NewsFetchMetadata(
            query="TSLA stock",
            fetched_at=fetched_at,
            raw_item_count=1,
            invalid_item_count=0,
            outside_window_count=0,
            in_window_item_count=1,
            duplicate_item_count=0,
            truncated_item_count=0,
            stored_item_count=1,
        ),
    )


def test_saves_utf8_json_and_creates_parent_directory(tmp_path):
    output_path = tmp_path / "nested" / "news_snapshot.json"
    store = NewsSnapshotStore()

    saved_path = store.save(make_result(), output_path=output_path)

    assert saved_path == output_path.resolve()
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["items"][0]["title"] == "테슬라 구조화 뉴스"
    assert payload["metadata"]["source_coverage"] == "unknown"
    assert list(output_path.parent.glob("*.tmp")) == []


def test_default_filename_contains_ticker_window_and_timestamp(tmp_path):
    store = NewsSnapshotStore(output_dir=tmp_path)

    saved_path = store.save(make_result())

    assert saved_path.parent == tmp_path.resolve()
    assert saved_path.name.startswith("news_TSLA_2026-07-22_2026-08-20_")
    assert saved_path.suffix == ".json"


def test_refuses_to_overwrite_existing_snapshot_by_default(tmp_path):
    output_path = tmp_path / "news_snapshot.json"
    output_path.write_text("original", encoding="utf-8")
    store = NewsSnapshotStore()

    with pytest.raises(FileExistsError, match="이미 존재"):
        store.save(make_result(), output_path=output_path)

    assert output_path.read_text(encoding="utf-8") == "original"


def test_explicit_overwrite_replaces_complete_snapshot(tmp_path):
    output_path = tmp_path / "news_snapshot.json"
    store = NewsSnapshotStore()
    store.save(make_result("first"), output_path=output_path)

    store.save(
        make_result("second"),
        output_path=output_path,
        overwrite=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["items"][0]["title"] == "second"
    assert list(tmp_path.glob("*.tmp")) == []


def test_default_filename_sanitizes_ticker_path_characters(tmp_path):
    result = make_result().model_copy(update={"ticker": "../TSLA\\unsafe"})
    store = NewsSnapshotStore(output_dir=tmp_path)

    saved_path = store.save(result)

    assert saved_path.parent == tmp_path.resolve()
    assert ".." not in saved_path.name
    assert "/" not in saved_path.name
    assert "\\" not in saved_path.name

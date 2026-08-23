# file path: data_pipeline/news_fetcher.py
from __future__ import annotations

import hashlib
import logging
import re
import requests
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from workflow.analysis_window import AnalysisWindow

logger = logging.getLogger(__name__)


NewsFetchStatus = Literal["available", "partial", "empty", "unavailable"]
WindowQueryStatus = Literal["complete", "partial", "failed"]


class NewsItem(BaseModel):
    """원본 RSS 항목과 LLM evidence를 연결할 구조화 뉴스 계약입니다."""

    model_config = ConfigDict(frozen=True)

    article_id: str = Field(pattern=r"^news_[0-9a-f]{16}$")
    title: str = Field(min_length=1)
    published_at: datetime
    url: str = Field(min_length=1)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_fields(self) -> "NewsItem":
        if self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")

        parsed_url = urlsplit(self.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("url must be an absolute HTTP(S) URL")
        return self


class NewsFetchMetadata(BaseModel):
    """수집 범위와 필터링 결과를 설명하는 관측 가능성 메타데이터입니다."""

    model_config = ConfigDict(frozen=True)

    provider: str = "google_news_rss"
    query: str
    fetched_at: datetime
    source_coverage: Literal["unknown"] = "unknown"
    window_query_status: WindowQueryStatus = "complete"
    request_count: int = Field(default=1, ge=0)
    failed_request_count: int = Field(default=0, ge=0)
    saturated_request_count: int = Field(default=0, ge=0)
    request_budget_exhausted: bool = False
    failed_query_ranges: tuple[str, ...] = ()
    unqueried_ranges: tuple[str, ...] = ()
    unresolved_saturated_ranges: tuple[str, ...] = ()

    raw_item_count: int = Field(ge=0)
    invalid_item_count: int = Field(ge=0)
    outside_window_count: int = Field(ge=0)
    in_window_item_count: int = Field(ge=0)
    duplicate_item_count: int = Field(ge=0)
    truncated_item_count: int = Field(ge=0)
    stored_item_count: int = Field(ge=0)

    feed_oldest_published_at: datetime | None = None
    feed_newest_published_at: datetime | None = None
    error_code: str | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_timestamps(self) -> "NewsFetchMetadata":
        timestamps = (
            self.fetched_at,
            self.feed_oldest_published_at,
            self.feed_newest_published_at,
        )
        if any(value is not None and value.tzinfo is None for value in timestamps):
            raise ValueError("news metadata timestamps must be timezone-aware")
        return self


class NewsFetchResult(BaseModel):
    """성공, 빈 결과, 제공자 실패를 서로 구분하는 뉴스 수집 결과입니다."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    ticker: str
    window: AnalysisWindow
    status: NewsFetchStatus
    available: bool
    items: tuple[NewsItem, ...] = ()
    metadata: NewsFetchMetadata

    @model_validator(mode="after")
    def validate_status(self) -> "NewsFetchResult":
        if self.ticker != self.window.ticker:
            raise ValueError("news ticker must match analysis window ticker")
        if self.metadata.stored_item_count != len(self.items):
            raise ValueError("stored_item_count must match items length")
        if self.status in {"available", "partial"} and (
            not self.available or not self.items
        ):
            raise ValueError("available/partial status requires news items")
        if self.status in {"empty", "unavailable"} and (
            self.available or self.items
        ):
            raise ValueError("empty/unavailable status cannot contain news items")
        if self.status == "unavailable" and self.metadata.error_code is None:
            raise ValueError("unavailable status requires error_code")
        return self


class NewsFetcher:
    """분석 기간 안의 종목 뉴스 metadata를 Google News RSS에서 수집합니다.

    RSS 실패와 정상적인 빈 결과를 서로 다른 구조화 상태로 반환해 상위
    workflow가 정량 리포트를 계속 만들면서도 실패 원인을 숨기지 않게 합니다.
    """

    RSS_URL = "https://news.google.com/rss/search"
    RSS_REQUEST_ITEM_LIMIT = 100
    DEFAULT_INITIAL_CHUNK_DAYS = 30
    DEFAULT_MAX_REQUESTS = 64
    DEFAULT_MAX_SNAPSHOT_ITEMS = 2_000
    MAX_RESPONSE_BYTES = 5_000_000

    def __init__(
        self,
        timeout: int = 10,
        *,
        http_get: Callable[..., requests.Response] | None = None,
        initial_chunk_days: int = DEFAULT_INITIAL_CHUNK_DAYS,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        max_snapshot_items: int = DEFAULT_MAX_SNAPSHOT_ITEMS,
    ):
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if initial_chunk_days <= 0:
            raise ValueError("initial_chunk_days must be greater than 0")
        if max_requests <= 0:
            raise ValueError("max_requests must be greater than 0")
        if max_snapshot_items <= 0:
            raise ValueError("max_snapshot_items must be greater than 0")

        self.timeout = timeout
        self._http_get = http_get
        self.initial_chunk_days = initial_chunk_days
        self.max_requests = max_requests
        self.max_snapshot_items = max_snapshot_items

    def fetch_for_window(
        self,
        window: AnalysisWindow,
        *,
        fetched_at: datetime | None = None,
    ) -> NewsFetchResult:
        """동일한 AnalysisWindow 안의 Google News RSS metadata를 수집합니다.

        기사 본문은 요청하지 않습니다. 네트워크/XML 실패는 빈 뉴스와 구분되는
        ``status=unavailable`` 결과로 반환해 상위 workflow가 계속 실행하면서도
        실패 원인을 기록할 수 있게 합니다.
        """
        collected_at = self._resolve_fetched_at(fetched_at)
        base_query = f"{window.ticker} stock"

        logger.info(
            "[%s] Google News RSS metadata 구간 수집: %s ~ %s",
            window.ticker,
            window.start_date,
            window.end_date,
        )

        pending_ranges = list(
            reversed(
                self._split_initial_ranges(
                    window.start_date,
                    window.end_date,
                    self.initial_chunk_days,
                )
            )
        )
        completed_batches: list[tuple[ET.Element, date, date]] = []
        failed_ranges: list[str] = []
        unqueried_ranges: list[str] = []
        unresolved_saturated_ranges: list[str] = []
        request_errors: list[str] = []
        request_error_codes: list[str] = []
        request_count = 0
        failed_request_count = 0
        saturated_request_count = 0
        request_budget_exhausted = False

        while pending_ranges:
            range_start, range_end = pending_ranges.pop()
            if request_count >= self.max_requests:
                request_budget_exhausted = True
                unqueried_ranges.append(self._format_range(range_start, range_end))
                unqueried_ranges.extend(
                    self._format_range(start, end)
                    for start, end in reversed(pending_ranges)
                )
                break

            query = self._build_date_query(
                base_query,
                range_start=range_start,
                range_end=range_end,
            )
            root, error_code, warning = self._request_feed(query)
            request_count += 1
            if root is None:
                failed_request_count += 1
                failed_ranges.append(self._format_range(range_start, range_end))
                request_errors.append(f"{error_code}: {warning}")
                request_error_codes.append(error_code or "unknown_error")
                continue

            raw_count = len(root.findall(".//item"))
            if raw_count >= self.RSS_REQUEST_ITEM_LIMIT:
                saturated_request_count += 1
                if range_start < range_end:
                    midpoint = range_start + timedelta(
                        days=(range_end - range_start).days // 2
                    )
                    required_slots = len(pending_ranges) + 2
                    remaining_slots = self.max_requests - request_count
                    if remaining_slots >= required_slots:
                        pending_ranges.append((midpoint + timedelta(days=1), range_end))
                        pending_ranges.append((range_start, midpoint))
                        continue
                    request_budget_exhausted = True

                unresolved_saturated_ranges.append(
                    self._format_range(range_start, range_end)
                )

            completed_batches.append((root, range_start, range_end))

        return self._parse_batches(
            completed_batches,
            window=window,
            query=base_query,
            fetched_at=collected_at,
            request_count=request_count,
            failed_request_count=failed_request_count,
            saturated_request_count=saturated_request_count,
            request_budget_exhausted=request_budget_exhausted,
            failed_ranges=tuple(failed_ranges),
            unqueried_ranges=tuple(unqueried_ranges),
            unresolved_saturated_ranges=tuple(unresolved_saturated_ranges),
            request_errors=tuple(request_errors),
            request_error_codes=tuple(request_error_codes),
        )

    def _request_feed(
        self,
        query: str,
    ) -> tuple[ET.Element | None, str | None, str | None]:
        request_get = self._http_get or requests.get
        params = {
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
        try:
            response = request_get(
                self.RSS_URL,
                params=params,
                headers={"User-Agent": "llm-financial-reporting/1.0"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            return (
                None,
                "timeout",
                f"Google News RSS 요청이 {self.timeout}초 안에 완료되지 않았습니다.",
            )
        except requests.exceptions.RequestException as exc:
            return None, "network_error", f"Google News RSS 요청에 실패했습니다: {exc}"

        if len(response.content) > self.MAX_RESPONSE_BYTES:
            return (
                None,
                "response_too_large",
                "Google News RSS 응답이 내부 안전 크기 제한을 초과했습니다.",
            )

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            return None, "invalid_xml", f"Google News RSS XML을 해석할 수 없습니다: {exc}"

        root_name = root.tag.rsplit("}", 1)[-1].lower()
        if root_name != "rss" or root.find("channel") is None:
            return None, "invalid_feed", "응답 XML이 Google News RSS 구조가 아닙니다."
        return root, None, None

    def _parse_batches(
        self,
        batches: list[tuple[ET.Element, date, date]],
        *,
        window: AnalysisWindow,
        query: str,
        fetched_at: datetime,
        request_count: int,
        failed_request_count: int,
        saturated_request_count: int,
        request_budget_exhausted: bool,
        failed_ranges: tuple[str, ...],
        unqueried_ranges: tuple[str, ...],
        unresolved_saturated_ranges: tuple[str, ...],
        request_errors: tuple[str, ...],
        request_error_codes: tuple[str, ...],
    ) -> NewsFetchResult:
        raw_items = [
            element
            for root, _range_start, _range_end in batches
            for element in root.findall(".//item")
        ]
        window_timezone = ZoneInfo(window.timezone)
        parsed_feed_dates: list[datetime] = []
        candidates: list[NewsItem] = []
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()

        invalid_count = 0
        outside_count = 0
        in_window_count = 0
        duplicate_count = 0

        for element in raw_items:
            title = (element.findtext("title") or "").strip()
            published_text = (element.findtext("pubDate") or "").strip()
            raw_url = (element.findtext("link") or "").strip()
            source = (element.findtext("source") or "").strip()

            try:
                published_at = parsedate_to_datetime(published_text)
            except (TypeError, ValueError, OverflowError):
                published_at = None

            canonical_url = self._canonicalize_url(raw_url)
            normalized_title = self._normalize_title(title)
            if (
                not normalized_title
                or published_at is None
                or published_at.tzinfo is None
                or canonical_url is None
                or not source
            ):
                invalid_count += 1
                continue

            published_at = published_at.astimezone(timezone.utc)
            parsed_feed_dates.append(published_at)
            published_date = published_at.astimezone(window_timezone).date()
            if not window.start_date <= published_date <= window.end_date:
                outside_count += 1
                continue

            in_window_count += 1
            if canonical_url in seen_urls or normalized_title in seen_titles:
                duplicate_count += 1
                continue

            seen_urls.add(canonical_url)
            seen_titles.add(normalized_title)
            article_id = self._build_article_id(canonical_url, normalized_title)
            candidates.append(
                NewsItem(
                    article_id=article_id,
                    title=title,
                    published_at=published_at,
                    url=canonical_url,
                    source=source,
                )
            )

        candidates.sort(key=lambda item: item.published_at, reverse=True)
        stored_items = tuple(candidates[: self.max_snapshot_items])
        truncated_count = max(0, len(candidates) - len(stored_items))
        has_partial_coverage = bool(
            failed_ranges
            or unqueried_ranges
            or unresolved_saturated_ranges
            or truncated_count
        )
        warnings = self._build_warnings(
            invalid_count=invalid_count,
            outside_count=outside_count,
            duplicate_count=duplicate_count,
            truncated_count=truncated_count,
            stored_count=len(stored_items),
            failed_ranges=failed_ranges,
            unqueried_ranges=unqueried_ranges,
            unresolved_saturated_ranges=unresolved_saturated_ranges,
            request_errors=request_errors,
        )
        if stored_items:
            status: NewsFetchStatus = (
                "partial" if has_partial_coverage else "available"
            )
            error_code = None
        elif has_partial_coverage or failed_request_count:
            status = "unavailable"
            if not batches and len(set(request_error_codes)) == 1:
                error_code = request_error_codes[0]
            else:
                error_code = "partial_collection_failure"
        else:
            status = "empty"
            error_code = None

        if status == "unavailable":
            window_query_status: WindowQueryStatus = "failed"
        elif has_partial_coverage:
            window_query_status = "partial"
        else:
            window_query_status = "complete"

        return NewsFetchResult(
            ticker=window.ticker,
            window=window,
            status=status,
            available=bool(stored_items),
            items=stored_items,
            metadata=NewsFetchMetadata(
                query=query,
                fetched_at=fetched_at,
                window_query_status=window_query_status,
                request_count=request_count,
                failed_request_count=failed_request_count,
                saturated_request_count=saturated_request_count,
                request_budget_exhausted=request_budget_exhausted,
                failed_query_ranges=failed_ranges,
                unqueried_ranges=unqueried_ranges,
                unresolved_saturated_ranges=unresolved_saturated_ranges,
                raw_item_count=len(raw_items),
                invalid_item_count=invalid_count,
                outside_window_count=outside_count,
                in_window_item_count=in_window_count,
                duplicate_item_count=duplicate_count,
                truncated_item_count=truncated_count,
                stored_item_count=len(stored_items),
                feed_oldest_published_at=(
                    min(parsed_feed_dates) if parsed_feed_dates else None
                ),
                feed_newest_published_at=(
                    max(parsed_feed_dates) if parsed_feed_dates else None
                ),
                error_code=error_code,
                warnings=warnings,
            ),
        )

    @staticmethod
    def _split_initial_ranges(
        start_date: date,
        end_date: date,
        chunk_days: int,
    ) -> list[tuple[date, date]]:
        ranges: list[tuple[date, date]] = []
        current_start = start_date
        while current_start <= end_date:
            current_end = min(
                current_start + timedelta(days=chunk_days - 1),
                end_date,
            )
            ranges.append((current_start, current_end))
            current_start = current_end + timedelta(days=1)
        return ranges

    @staticmethod
    def _build_date_query(
        base_query: str,
        *,
        range_start: date,
        range_end: date,
    ) -> str:
        buffered_start = range_start - timedelta(days=1)
        buffered_end = range_end + timedelta(days=1)
        return (
            f"{base_query} after:{buffered_start.isoformat()} "
            f"before:{buffered_end.isoformat()}"
        )

    @staticmethod
    def _format_range(start_date: date, end_date: date) -> str:
        return f"{start_date.isoformat()}..{end_date.isoformat()}"

    @staticmethod
    def _resolve_fetched_at(value: datetime | None) -> datetime:
        resolved = value or datetime.now(timezone.utc)
        if resolved.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        return resolved.astimezone(timezone.utc)

    @staticmethod
    def _normalize_title(title: str) -> str:
        normalized = unicodedata.normalize("NFKC", title)
        return re.sub(r"\s+", " ", normalized).strip().casefold()

    @staticmethod
    def _canonicalize_url(raw_url: str) -> str | None:
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/") or "/",
                parsed.query,
                "",
            )
        )

    @staticmethod
    def _build_article_id(canonical_url: str, normalized_title: str) -> str:
        digest = hashlib.sha256(
            f"{canonical_url}|{normalized_title}".encode("utf-8")
        ).hexdigest()[:16]
        return f"news_{digest}"

    @staticmethod
    def _build_warnings(
        *,
        invalid_count: int,
        outside_count: int,
        duplicate_count: int,
        truncated_count: int,
        stored_count: int,
        failed_ranges: tuple[str, ...],
        unqueried_ranges: tuple[str, ...],
        unresolved_saturated_ranges: tuple[str, ...],
        request_errors: tuple[str, ...],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if invalid_count:
            warnings.append(f"필수 metadata가 잘못된 RSS 항목 {invalid_count}건을 제외했습니다.")
        if outside_count:
            warnings.append(f"분석 기간 밖의 RSS 항목 {outside_count}건을 제외했습니다.")
        if duplicate_count:
            warnings.append(f"중복 RSS 항목 {duplicate_count}건을 제외했습니다.")
        if truncated_count:
            warnings.append(f"내부 안전 제한으로 RSS 항목 {truncated_count}건을 제외했습니다.")
        if failed_ranges:
            warnings.append(
                "RSS 요청에 실패한 날짜 구간: " + ", ".join(failed_ranges)
            )
        if unqueried_ranges:
            warnings.append(
                "요청 안전 한도로 조회하지 못한 날짜 구간: "
                + ", ".join(unqueried_ranges)
            )
        if unresolved_saturated_ranges:
            warnings.append(
                "100건 상한 도달로 전체 수집을 보장할 수 없는 날짜 구간: "
                + ", ".join(unresolved_saturated_ranges)
            )
        warnings.extend(request_errors)
        if stored_count == 0:
            warnings.append("분석 기간 안에 저장할 수 있는 뉴스가 없습니다.")
        warnings.append("Google News RSS의 전체 언론사 수집 범위는 알 수 없습니다.")
        return tuple(warnings)

    @staticmethod
    def _unavailable_result(
        window: AnalysisWindow,
        *,
        query: str,
        fetched_at: datetime,
        error_code: str,
        warning: str,
    ) -> NewsFetchResult:
        return NewsFetchResult(
            ticker=window.ticker,
            window=window,
            status="unavailable",
            available=False,
            items=(),
            metadata=NewsFetchMetadata(
                query=query,
                fetched_at=fetched_at,
                raw_item_count=0,
                invalid_item_count=0,
                outside_window_count=0,
                in_window_item_count=0,
                duplicate_item_count=0,
                truncated_item_count=0,
                stored_item_count=0,
                error_code=error_code,
                warnings=(
                    warning,
                    "Google News RSS의 전체 언론사 수집 범위는 알 수 없습니다.",
                ),
            ),
        )

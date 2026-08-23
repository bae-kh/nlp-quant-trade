"""LLM 전에 뉴스 제목의 관련성·안전성·방향 힌트를 결정론적으로 판정합니다."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Mapping

from data_pipeline.news_fetcher import NewsItem


DirectionHint = Literal["positive", "neutral", "negative", "conflicting"]


@dataclass(frozen=True)
class HeadlineFilterResult:
    """LLM 입력 가능 기사와 제외 사유별 기사를 함께 보존합니다."""

    eligible_items: tuple[NewsItem, ...]
    irrelevant_items: tuple[NewsItem, ...]
    unsafe_instruction_items: tuple[NewsItem, ...]


class HeadlinePolicy:
    """작은 규칙 집합으로 LLM 입력과 출력의 최소 안전선을 만듭니다.

    이 규칙은 기사 진위나 최종 감성을 판정하지 않습니다. 검색 노이즈와
    명령형 문자열을 제거하고, LLM이 제목에 없는 사건 유형을 만들어내지
    못하도록 검증에 사용할 근거만 제공합니다.
    """

    DEFAULT_TICKER_ALIASES: Mapping[str, tuple[str, ...]] = {
        "AAPL": ("Apple", "Apple Inc"),
        "AMZN": ("Amazon", "Amazon.com"),
        "GOOG": ("Google", "Alphabet"),
        "GOOGL": ("Google", "Alphabet"),
        "META": ("Meta Platforms", "Facebook"),
        "MSFT": ("Microsoft",),
        "NFLX": ("Netflix",),
        "NVDA": ("Nvidia",),
        "SPY": ("S&P 500", "SPDR S&P 500"),
        "TSLA": ("Tesla",),
    }

    INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bignore\b.{0,50}\b(?:instruction|prompt|message)s?\b",
            r"\b(?:disregard|override|forget)\b.{0,50}"
            r"\b(?:instruction|prompt|message)s?\b",
            r"\b(?:system|developer)\s+(?:prompt|message)\b",
            r"\b(?:say|print|output|respond\s+with)\b.{0,30}"
            r"\b(?:buy|sell)\b",
            r"(?:이전|위의|앞선).{0,12}(?:지시|명령|프롬프트).{0,12}무시",
            r"(?:시스템|개발자).{0,8}(?:프롬프트|메시지)",
        )
    )

    POSITIVE_DIRECTION_KEYWORDS: tuple[str, ...] = (
        "beats expectations",
        "stronger earnings",
        "raises guidance",
        "raises outlook",
        "wins major",
        "wins contract",
        "record high",
        "all-time high",
        "surges",
        "surge",
        "jumps",
        "jump",
        "rallies",
        "rally",
        "upgrade",
        "lifts price target",
        "실적 개선",
        "가이던스 상향",
        "계약 수주",
        "사상 최고",
        "급등",
        "상향 조정",
    )
    NEGATIVE_DIRECTION_KEYWORDS: tuple[str, ...] = (
        "misses expectations",
        "weaker demand",
        "cuts guidance",
        "lowers guidance",
        "recall",
        "investigation",
        "probe",
        "lawsuit",
        "sales drop",
        "slips",
        "plunges",
        "plunge",
        "declines",
        "downgrade",
        "수요 둔화",
        "가이던스 하향",
        "리콜",
        "조사",
        "소송",
        "판매 감소",
        "급락",
        "하향 조정",
    )

    # 출력에 왼쪽 표현이 있으면 인용 제목에 오른쪽 표현 중 하나가 있어야 합니다.
    EVENT_GROUNDING_RULES: tuple[
        tuple[str, tuple[str, ...], tuple[str, ...]], ...
    ] = (
        (
            "earnings_report",
            (
                "실적",
                "earnings report",
                "financial results",
            ),
            ("earnings", "quarterly results", "financial results", "실적"),
        ),
        (
            "vehicle_deliveries",
            (
                "인도량",
                "차량 인도",
                "차량 배송",
                "배송 보고서",
                "delivery report",
                "vehicle deliveries",
            ),
            ("delivery", "deliveries", "인도량", "차량 인도"),
        ),
        (
            "revenue",
            ("매출", "revenue"),
            ("revenue", "매출"),
        ),
        (
            "product_recall",
            ("제품 리콜", "차량 리콜", "product recall", "vehicle recall"),
            ("recall", "리콜"),
        ),
        (
            "regulatory_investigation",
            ("규제 조사", "규제기관 조사", "regulatory investigation"),
            ("investigation", "probe", "regulator", "regulatory", "조사", "규제"),
        ),
    )

    PROHIBITED_OUTPUT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"(?<![A-Za-z0-9])(?:BUY|SELL)(?![A-Za-z0-9])",
            r"(?:매수|매도|구매)\s*(?:추천|권장)",
            r"(?:수익|상승|하락)\s*(?:보장|확정)",
            r"향후\s+주가",
            r"주가.{0,20}(?:상승|하락)할\s+것으로",
            r"주가.{0,40}(?:상승|하락|도달).{0,30}"
            r"(?:가능성|수\s*있|전망|예상)",
            r"주가.{0,15}영향.{0,20}(?:미칠|줄|초래)",
            r"(?:몇\s*가지|여러|뉴스)\s*지표",
            r"투자자",
            r"(?:긍정적|부정적)인?\s*영향.{0,20}(?:미칠|줄|예상)",
            r"전망(?:이|은)?\s*(?:밝|어둡)",
            r"(?:배송|배달|납품)\s*보고서",
        )
    )

    def __init__(
        self,
        *,
        ticker_aliases: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        aliases = {
            ticker.upper(): tuple(values)
            for ticker, values in self.DEFAULT_TICKER_ALIASES.items()
        }
        if ticker_aliases:
            aliases.update(
                {
                    ticker.strip().upper(): tuple(values)
                    for ticker, values in ticker_aliases.items()
                    if ticker.strip()
                }
            )
        self.ticker_aliases = aliases

    def filter_for_ticker(
        self,
        items: tuple[NewsItem, ...],
        *,
        ticker: str,
    ) -> HeadlineFilterResult:
        """명령형 문자열을 먼저 제외한 뒤 종목 관련성을 확인합니다."""
        eligible: list[NewsItem] = []
        irrelevant: list[NewsItem] = []
        unsafe: list[NewsItem] = []
        for item in items:
            if self.contains_instruction_pattern(f"{item.title}\n{item.source}"):
                unsafe.append(item)
            elif self.is_ticker_relevant(item.title, ticker=ticker):
                eligible.append(item)
            else:
                irrelevant.append(item)
        return HeadlineFilterResult(
            eligible_items=tuple(eligible),
            irrelevant_items=tuple(irrelevant),
            unsafe_instruction_items=tuple(unsafe),
        )

    def is_ticker_relevant(self, title: str, *, ticker: str) -> bool:
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            return False
        candidates = (normalized_ticker, *self.ticker_aliases.get(normalized_ticker, ()))
        return any(self.contains_keyword(title, candidate) for candidate in candidates)

    @classmethod
    def contains_instruction_pattern(cls, text: str) -> bool:
        normalized = unicodedata.normalize("NFKC", text)
        return any(pattern.search(normalized) for pattern in cls.INSTRUCTION_PATTERNS)

    @classmethod
    def article_direction_hint(cls, title: str) -> DirectionHint:
        normalized = cls.normalize(title)
        positive = any(
            cls.contains_keyword(normalized, keyword)
            for keyword in cls.POSITIVE_DIRECTION_KEYWORDS
        )
        negative = any(
            cls.contains_keyword(normalized, keyword)
            for keyword in cls.NEGATIVE_DIRECTION_KEYWORDS
        )
        if positive and negative:
            return "conflicting"
        if positive:
            return "positive"
        if negative:
            return "negative"
        return "neutral"

    @classmethod
    def dataset_direction_hint(cls, items: tuple[NewsItem, ...]) -> DirectionHint:
        hints = tuple(cls.article_direction_hint(item.title) for item in items)
        positive_count = sum(hint == "positive" for hint in hints)
        negative_count = sum(hint == "negative" for hint in hints)
        has_conflicting_item = any(hint == "conflicting" for hint in hints)
        if has_conflicting_item or (
            positive_count > 0
            and negative_count > 0
            and abs(positive_count - negative_count) <= 1
        ):
            return "conflicting"
        if positive_count > negative_count:
            return "positive"
        if negative_count > positive_count:
            return "negative"
        return "neutral"

    @classmethod
    def prohibited_output_matches(cls, text: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", text)
        return tuple(
            match.group(0)
            for pattern in cls.PROHIBITED_OUTPUT_PATTERNS
            if (match := pattern.search(normalized)) is not None
        )

    @classmethod
    def ungrounded_event_claims(
        cls,
        output_text: str,
        *,
        source_titles: tuple[str, ...],
    ) -> tuple[str, ...]:
        """출력의 사건 유형이 해당 인용 제목에 실제로 있는지 확인합니다."""
        normalized_output = cls.normalize(output_text)
        normalized_sources = tuple(cls.normalize(title) for title in source_titles)
        violations: list[str] = []
        for rule_name, output_terms, source_terms in cls.EVENT_GROUNDING_RULES:
            output_claimed = any(
                cls.contains_keyword(normalized_output, term)
                for term in output_terms
            )
            if not output_claimed:
                continue
            source_grounded = any(
                cls.contains_keyword(source, term)
                for source in normalized_sources
                for term in source_terms
            )
            if not source_grounded:
                violations.append(rule_name)
        return tuple(violations)

    @staticmethod
    def normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return re.sub(r"\s+", " ", normalized).strip()

    @classmethod
    def contains_keyword(cls, text: str, keyword: str) -> bool:
        normalized_text = cls.normalize(text)
        normalized_keyword = cls.normalize(keyword)
        if not normalized_keyword:
            return False
        if normalized_keyword.isascii():
            pattern = rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])"
            return re.search(pattern, normalized_text) is not None
        return normalized_keyword in normalized_text

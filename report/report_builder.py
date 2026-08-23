"""검증된 시장·뉴스 결과를 결정론적 Markdown 문서로 조립합니다."""

from __future__ import annotations

import re
from datetime import datetime

from analysis.market_analyzer import MarketAnalysis
from analysis.news_analyzer import NewsAnalysis
from data_pipeline.news_fetcher import NewsFetchResult
from workflow.analysis_window import AnalysisWindow


class ReportBuilder:
    """보고서 구조와 금융 숫자 표기를 Python 코드로 고정합니다."""

    def build(
        self,
        *,
        run_id: str,
        window: AnalysisWindow,
        market: MarketAnalysis,
        news: NewsFetchResult,
        news_analysis: NewsAnalysis,
        generated_at: datetime,
        additional_warnings: tuple[str, ...] = (),
    ) -> str:
        """구조화 결과를 투자 추천이 없는 한국어 Markdown으로 변환합니다."""
        self._validate_inputs(
            run_id,
            window,
            market,
            news,
            news_analysis,
            generated_at,
        )

        lines = [
            f"# {self._escape(window.ticker)} 금융 분석 리포트",
            "",
            f"*생성 시각: {generated_at.isoformat()}*",
            "",
            "> 이 문서는 데이터 분석 자동화 결과이며 투자 조언이 아닙니다.",
            "",
            "## 1. 분석 개요",
            "",
            "| 항목 | 값 |",
            "|---|---|",
            f"| Run ID | `{self._escape_code(run_id)}` |",
            f"| 종목 | {self._escape(window.ticker)} |",
            (
                "| 요청 분석 기간 | "
                f"{window.start_date} ~ {window.end_date} "
                f"({window.requested_analysis_days}개 달력일, 양 끝 포함) |"
            ),
            f"| 기준 시간대 | {self._escape(window.timezone)} |",
            (
                "| 실제 가격 범위 | "
                f"{market.actual_price_start} ~ {market.actual_price_end} "
                f"({market.price_observation_count}개 거래일) |"
            ),
            "",
            "## 2. 정량 시장 지표",
            "",
            "| 지표 | 값 | 계산 주체 |",
            "|---|---:|---|",
            f"| 기간 수익률 | {self._percentage(market.period_return)} | Python |",
            (
                "| 연환산 변동성 | "
                f"{self._percentage(market.annualized_volatility)} | Python |"
            ),
            f"| 최대 낙폭(MDD) | {self._percentage(market.max_drawdown)} | Python |",
            f"| RSI(14) | {self._decimal(market.latest_rsi)} | Python |",
            (
                "| MACD difference | "
                f"{self._decimal(market.latest_macd_diff, digits=4)} | Python |"
            ),
            f"| 기술지표 상태 | {self._escape(market.indicator_status)} | Python |",
            "",
            "## 3. Benchmark 비교",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| Benchmark | {self._escape(market.benchmark_ticker)} |",
            f"| 상태 | {self._escape(market.benchmark_status)} |",
            f"| Benchmark 수익률 | {self._percentage(market.benchmark_return)} |",
            f"| 대상 종목 대비 차이 | {self._percentage(market.benchmark_difference)} |",
            "",
            "## 4. 뉴스 수집 상태",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            f"| 수집 상태 | {self._escape(news.status)} |",
            f"| 날짜 구간 조회 상태 | {self._escape(news.metadata.window_query_status)} |",
            f"| RSS 요청 수 | {news.metadata.request_count} |",
            f"| 원본 RSS 항목 | {news.metadata.raw_item_count} |",
            f"| 기간 내 항목 | {news.metadata.in_window_item_count} |",
            f"| 중복 제외 | {news.metadata.duplicate_item_count} |",
            f"| 저장 기사 | {len(news.items)} |",
            f"| Source coverage | {self._escape(news.metadata.source_coverage)} |",
            "",
            "## 5. LLM 뉴스 분석",
            "",
        ]

        if news_analysis.available:
            lines.extend(self._available_news_section(news, news_analysis))
        else:
            lines.extend(self._unavailable_news_section(news_analysis))

        warnings = self._unique_warnings(
            market.warnings,
            news.metadata.warnings,
            news_analysis.warnings,
            additional_warnings,
        )
        lines.extend(["", "## 6. 데이터 한계 및 경고", ""])
        if warnings:
            lines.extend(f"- {self._escape(warning)}" for warning in warnings)
        else:
            lines.append("- 추가 경고가 없습니다.")

        lines.extend(
            [
                "- 뉴스 분석은 기사 본문이 아니라 제공된 제목 metadata를 사용합니다.",
                "- 중요도 점수는 기사 선택 우선순위이며 감성, 신뢰도 또는 사실 검증 점수가 아닙니다.",
                "- 금융 수치는 Python이 계산하며 LLM은 해당 수치를 다시 계산하지 않습니다.",
                "",
                "## 7. 면책 조항",
                "",
                "이 리포트는 연구·교육 목적의 자동 생성 결과입니다. 투자 추천, 매수·매도 신호 또는 미래 성과 보장을 제공하지 않습니다.",
                "",
            ]
        )
        return "\n".join(lines)

    def _available_news_section(
        self,
        news: NewsFetchResult,
        analysis: NewsAnalysis,
    ) -> list[str]:
        lines = [
            "| 항목 | 값 |",
            "|---|---:|",
            "| 분석 상태 | available |",
            f"| 감성 | {self._escape(analysis.sentiment or '')} |",
            f"| 감성 점수 | {self._decimal(analysis.score)} |",
            f"| 신뢰도 | {analysis.confidence}% |",
            f"| 입력/선택/분석 기사 | {analysis.input_article_count} / {analysis.selected_article_count} / {analysis.analyzed_article_count} |",
            f"| 선택 전략 | {self._escape(analysis.selection_strategy or 'N/A')} |",
            f"| 모델 | {self._escape(analysis.model or 'N/A')} |",
            "",
            "### 뉴스 요약",
            "",
            self._escape(analysis.summary or ""),
            "",
            "### 주요 이슈와 근거",
            "",
        ]
        articles_by_id = {item.article_id: item for item in news.items}
        for topic in analysis.key_topics:
            lines.append(f"- **{self._escape(topic.topic)}**: {self._escape(topic.explanation)}")
            for article_id in topic.supporting_article_ids:
                article = articles_by_id.get(article_id)
                if article is None:
                    continue
                lines.append(
                    "  - "
                    f"`{article_id}` — {self._escape(article.title)} "
                    f"({self._escape(article.source)}, {article.published_at.date()})"
                )
        return lines

    def _unavailable_news_section(self, analysis: NewsAnalysis) -> list[str]:
        return [
            "뉴스 정성 분석을 사용할 수 없습니다. 이를 중립 분석으로 대체하지 않았습니다.",
            "",
            "| 항목 | 값 |",
            "|---|---:|",
            "| 분석 상태 | unavailable |",
            f"| 뉴스 수집 상태 | {self._escape(analysis.news_fetch_status)} |",
            f"| 오류 코드 | {self._escape(analysis.error_code or 'unknown')} |",
            f"| 입력 기사 | {analysis.input_article_count} |",
            f"| 선택 기사 | {analysis.selected_article_count} |",
            "| 감성 | N/A |",
            "| 감성 점수 | N/A |",
        ]

    @staticmethod
    def _validate_inputs(
        run_id: str,
        window: AnalysisWindow,
        market: MarketAnalysis,
        news: NewsFetchResult,
        news_analysis: NewsAnalysis,
        generated_at: datetime,
    ) -> None:
        if not run_id.strip():
            raise ValueError("run_id cannot be empty")
        tickers = {window.ticker, market.ticker, news.ticker, news_analysis.ticker}
        if len(tickers) != 1:
            raise ValueError("report inputs must use the same ticker")
        if market.requested_start_date != window.start_date or (
            market.requested_end_date != window.end_date
        ):
            raise ValueError("market analysis window does not match report window")
        if news.window != window:
            raise ValueError("news analysis window does not match report window")
        if news_analysis.news_fetch_status != news.status:
            raise ValueError("news analysis status does not match collection status")
        if news_analysis.input_article_count != len(news.items):
            raise ValueError("news analysis input count does not match collected items")
        if generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")

    @staticmethod
    def _percentage(value: float | None) -> str:
        return "N/A" if value is None else f"{value:.2%}"

    @staticmethod
    def _decimal(value: float | None, *, digits: int = 2) -> str:
        return "N/A" if value is None else f"{value:.{digits}f}"

    @staticmethod
    def _escape(value: object) -> str:
        text = re.sub(r"\s+", " ", str(value)).strip()
        replacements = {
            "\\": "\\\\",
            "|": "\\|",
            "[": "\\[",
            "]": "\\]",
            "*": "\\*",
            "_": "\\_",
            "#": "\\#",
            "<": "&lt;",
            ">": "&gt;",
        }
        return "".join(replacements.get(character, character) for character in text)

    @staticmethod
    def _escape_code(value: object) -> str:
        """inline-code delimiter를 깨지 않도록 식별자 문자열을 정리합니다."""
        return re.sub(r"\s+", " ", str(value)).strip().replace("`", "\\`")

    @staticmethod
    def _unique_warnings(*warning_groups: tuple[str, ...]) -> tuple[str, ...]:
        unique: list[str] = []
        seen: set[str] = set()
        for group in warning_groups:
            for warning in group:
                normalized = warning.strip()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    unique.append(normalized)
        return tuple(unique)

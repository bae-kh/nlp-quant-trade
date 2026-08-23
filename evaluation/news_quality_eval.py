"""뉴스 LLM의 감성·근거·금지 주장 품질을 고정 fixture로 평가합니다."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Callable
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from analysis.news_analyzer import NewsAnalysis, NewsAnalyzer, NewsLLMOutput
from data_pipeline.news_fetcher import NewsFetchMetadata, NewsFetchResult, NewsItem
from workflow.analysis_window import AnalysisWindow


EvalMode = Literal["recorded_fixture", "live_model"]


class NewsEvalCase(BaseModel):
    """합성 뉴스 입력과 자동 판정 가능한 기대 결과입니다."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    description: str = Field(min_length=1)
    ticker: str
    start_date: date
    end_date: date
    articles: tuple[NewsItem, ...] = Field(min_length=1)
    expected_sentiments: tuple[Literal["positive", "neutral", "negative"], ...]
    required_evidence_ids: tuple[str, ...] = Field(min_length=1)
    forbidden_evidence_ids: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    required_any_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    recorded_output: NewsLLMOutput

    @field_validator(
        "expected_sentiments",
        "required_evidence_ids",
        "forbidden_evidence_ids",
        "required_terms",
        "required_any_terms",
        "forbidden_terms",
    )
    @classmethod
    def require_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("eval expectation values must be unique")
        return values

    @model_validator(mode="after")
    def validate_case_contract(self) -> "NewsEvalCase":
        if self.end_date < self.start_date:
            raise ValueError("eval end_date cannot precede start_date")
        article_ids = {article.article_id for article in self.articles}
        if len(article_ids) != len(self.articles):
            raise ValueError("eval article IDs must be unique")
        unknown_required = set(self.required_evidence_ids) - article_ids
        if unknown_required:
            raise ValueError("required evidence IDs must exist in eval articles")
        unknown_forbidden = set(self.forbidden_evidence_ids) - article_ids
        if unknown_forbidden:
            raise ValueError("forbidden evidence IDs must exist in eval articles")
        if set(self.required_evidence_ids) & set(self.forbidden_evidence_ids):
            raise ValueError("required and forbidden evidence IDs cannot overlap")
        recorded_citations = {
            article_id
            for topic in self.recorded_output.key_topics
            for article_id in topic.supporting_article_ids
        }
        if recorded_citations - article_ids:
            raise ValueError("recorded output contains unknown evidence IDs")
        if recorded_citations & set(self.forbidden_evidence_ids):
            raise ValueError("recorded output cites forbidden evidence IDs")
        if not self.expected_sentiments:
            raise ValueError("expected_sentiments cannot be empty")
        return self

    def to_news_result(self) -> NewsFetchResult:
        requested_days = (self.end_date - self.start_date).days + 1
        window = AnalysisWindow(
            ticker=self.ticker,
            requested_analysis_days=requested_days,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        fetched_at = datetime.combine(
            self.end_date,
            datetime_time(23, 59),
            tzinfo=timezone.utc,
        )
        return NewsFetchResult(
            ticker=self.ticker,
            window=window,
            status="available",
            available=True,
            items=self.articles,
            metadata=NewsFetchMetadata(
                provider="synthetic_eval_fixture",
                query=f"{self.ticker} eval fixture",
                fetched_at=fetched_at,
                request_count=0,
                raw_item_count=len(self.articles),
                invalid_item_count=0,
                outside_window_count=0,
                in_window_item_count=len(self.articles),
                duplicate_item_count=0,
                truncated_item_count=0,
                stored_item_count=len(self.articles),
                feed_oldest_published_at=min(
                    article.published_at for article in self.articles
                ),
                feed_newest_published_at=max(
                    article.published_at for article in self.articles
                ),
            ),
        )


class NewsEvalCaseResult(BaseModel):
    """한 case의 LLM 출력과 결정론적 grader 결과입니다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    case_id: str
    repetition: int = Field(ge=1)
    passed: bool
    available_pass: bool
    sentiment_pass: bool
    evidence_pass: bool
    excluded_evidence_pass: bool
    required_terms_pass: bool
    forbidden_terms_pass: bool
    numeric_grounding_pass: bool
    expected_sentiments: tuple[str, ...]
    required_evidence_ids: tuple[str, ...]
    forbidden_evidence_ids: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]
    missing_required_terms: tuple[str, ...] = ()
    cited_forbidden_evidence_ids: tuple[str, ...] = ()
    unexpected_numeric_tokens: tuple[str, ...] = ()
    matched_forbidden_terms: tuple[str, ...] = ()
    analysis: NewsAnalysis


class NewsEvalSuiteResult(BaseModel):
    """평가 모드와 결과를 함께 남겨 과장된 품질 주장을 방지합니다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    schema_version: str = "1.0"
    eval_run_id: str
    mode: EvalMode
    model: str
    fixture_path: Path
    generated_at: datetime
    duration_ms: float = Field(ge=0)
    repetitions: int = Field(ge=1)
    total_case_runs: int = Field(ge=1)
    passed_case_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    all_passed: bool
    automated_checks_only: bool = True
    results: tuple[NewsEvalCaseResult, ...]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_summary(self) -> "NewsEvalSuiteResult":
        if self.generated_at.tzinfo is None:
            raise ValueError("eval generated_at must be timezone-aware")
        if self.total_case_runs != len(self.results):
            raise ValueError("total_case_runs must match results")
        passed = sum(result.passed for result in self.results)
        if self.passed_case_runs != passed:
            raise ValueError("passed_case_runs must match results")
        if self.all_passed != (passed == self.total_case_runs):
            raise ValueError("all_passed must match result states")
        expected_rate = passed / self.total_case_runs
        if abs(self.pass_rate - expected_rate) > 1e-12:
            raise ValueError("pass_rate must match results")
        return self


class NewsEvalSuite:
    """NewsAnalyzer를 반복 실행하고 규칙 기반 grader로 결과를 판정합니다."""

    NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:[.,]\d+)?%?")

    async def run(
        self,
        cases: tuple[NewsEvalCase, ...],
        *,
        analyzer_factory: Callable[[NewsEvalCase], NewsAnalyzer],
        mode: EvalMode,
        model: str,
        fixture_path: str | Path,
        repetitions: int = 1,
        generated_at: datetime | None = None,
    ) -> NewsEvalSuiteResult:
        if not cases:
            raise ValueError("eval suite requires at least one case")
        if repetitions <= 0:
            raise ValueError("repetitions must be greater than 0")
        resolved_generated_at = generated_at or datetime.now(timezone.utc)
        if resolved_generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        resolved_generated_at = resolved_generated_at.astimezone(timezone.utc)
        started_tick = time.perf_counter()
        results: list[NewsEvalCaseResult] = []

        for repetition in range(1, repetitions + 1):
            for case in cases:
                analyzer = analyzer_factory(case)
                analysis = await analyzer.analyze(case.to_news_result())
                results.append(self.evaluate_case(case, analysis, repetition))

        duration_ms = round((time.perf_counter() - started_tick) * 1000, 3)
        passed = sum(result.passed for result in results)
        warnings = (
            (
                "recorded_fixture 모드는 평가 코드와 고정 기준선의 회귀만 "
                "검증하며 현재 live 모델 품질을 증명하지 않습니다."
            ),
        ) if mode == "recorded_fixture" else (
            "자동 grader는 정성 요약의 모든 의미적 정확성을 보장하지 않으므로 사람 검토가 필요합니다.",
        )
        timestamp = resolved_generated_at.strftime("%Y%m%dT%H%M%S_%fZ")
        return NewsEvalSuiteResult(
            eval_run_id=f"eval_{timestamp}_{uuid.uuid4().hex[:8]}",
            mode=mode,
            model=model,
            fixture_path=Path(fixture_path).resolve(),
            generated_at=resolved_generated_at,
            duration_ms=duration_ms,
            repetitions=repetitions,
            total_case_runs=len(results),
            passed_case_runs=passed,
            pass_rate=passed / len(results),
            all_passed=passed == len(results),
            results=tuple(results),
            warnings=warnings,
        )

    def evaluate_case(
        self,
        case: NewsEvalCase,
        analysis: NewsAnalysis,
        repetition: int = 1,
    ) -> NewsEvalCaseResult:
        cited_ids = tuple(
            sorted(
                {
                    article_id
                    for topic in analysis.key_topics
                    for article_id in topic.supporting_article_ids
                }
            )
        )
        available_pass = analysis.available
        sentiment_pass = (
            analysis.sentiment in case.expected_sentiments
            if analysis.available
            else False
        )
        evidence_pass = set(case.required_evidence_ids).issubset(cited_ids)
        cited_forbidden_evidence = tuple(
            sorted(set(case.forbidden_evidence_ids) & set(cited_ids))
        )
        excluded_evidence_pass = not cited_forbidden_evidence
        qualitative_text = self._qualitative_text(analysis)
        folded_text = self._normalize_term_text(qualitative_text)
        missing_required_terms = tuple(
            term
            for term in case.required_terms
            if self._normalize_term_text(term) not in folded_text
        )
        any_term_missing = bool(case.required_any_terms) and not any(
            self._normalize_term_text(term) in folded_text
            for term in case.required_any_terms
        )
        if any_term_missing:
            missing_required_terms = (
                *missing_required_terms,
                *case.required_any_terms,
            )
        required_terms_pass = not missing_required_terms
        matched_forbidden = tuple(
            term
            for term in case.forbidden_terms
            if self._normalize_term_text(term) in folded_text
        )
        forbidden_pass = not matched_forbidden

        source_numbers = self._number_tokens(
            " ".join(article.title for article in case.articles)
        )
        output_numbers = self._number_tokens(qualitative_text)
        unexpected_numbers = tuple(sorted(output_numbers - source_numbers))
        numeric_pass = not unexpected_numbers
        passed = all(
            (
                available_pass,
                sentiment_pass,
                evidence_pass,
                excluded_evidence_pass,
                required_terms_pass,
                forbidden_pass,
                numeric_pass,
            )
        )
        return NewsEvalCaseResult(
            case_id=case.case_id,
            repetition=repetition,
            passed=passed,
            available_pass=available_pass,
            sentiment_pass=sentiment_pass,
            evidence_pass=evidence_pass,
            excluded_evidence_pass=excluded_evidence_pass,
            required_terms_pass=required_terms_pass,
            forbidden_terms_pass=forbidden_pass,
            numeric_grounding_pass=numeric_pass,
            expected_sentiments=case.expected_sentiments,
            required_evidence_ids=case.required_evidence_ids,
            forbidden_evidence_ids=case.forbidden_evidence_ids,
            cited_evidence_ids=cited_ids,
            missing_required_terms=missing_required_terms,
            cited_forbidden_evidence_ids=cited_forbidden_evidence,
            unexpected_numeric_tokens=unexpected_numbers,
            matched_forbidden_terms=matched_forbidden,
            analysis=analysis,
        )

    @classmethod
    def _number_tokens(cls, text: str) -> set[str]:
        # Q4와 4분기는 같은 근거 숫자로 취급합니다.
        normalized = re.sub(r"(?i)\bQ([1-4])\b", r"\1", text)
        return {
            match.group(0).replace(",", "")
            for match in cls.NUMBER_PATTERN.finditer(normalized)
        }

    @staticmethod
    def _normalize_term_text(text: str) -> str:
        """NFKC·대소문자·띄어쓰기 차이를 평가 용어 비교에서 제거합니다."""
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return re.sub(r"\s+", "", normalized)

    @staticmethod
    def _qualitative_text(analysis: NewsAnalysis) -> str:
        parts = [analysis.summary or ""]
        for topic in analysis.key_topics:
            parts.extend((topic.topic, topic.explanation))
        return " ".join(parts)


class NewsQualityEvalArtifactStore:
    """평가 JSON과 사람이 읽는 Markdown 요약을 원자적으로 저장합니다."""

    def __init__(
        self,
        output_dir: str | Path = Path("reports") / "generated" / "evals",
    ) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        result: NewsEvalSuiteResult,
        *,
        overwrite: bool = False,
    ) -> tuple[Path, Path]:
        base = self.output_dir / result.eval_run_id
        json_path = base.with_suffix(".json").resolve()
        markdown_path = base.with_suffix(".md").resolve()
        if not overwrite and (json_path.exists() or markdown_path.exists()):
            raise FileExistsError(f"평가 artifact가 이미 존재합니다: {base}")
        self._atomic_write(
            json_path,
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            overwrite=overwrite,
        )
        self._atomic_write(
            markdown_path,
            self._build_markdown(result),
            overwrite=overwrite,
        )
        return json_path, markdown_path

    @staticmethod
    def _atomic_write(target: Path, content: str, *, overwrite: bool) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if target.exists() and not overwrite:
                raise FileExistsError(f"평가 artifact가 이미 존재합니다: {target}")
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _build_markdown(result: NewsEvalSuiteResult) -> str:
        lines = [
            "# News LLM Quality Eval",
            "",
            f"- Eval Run ID: `{result.eval_run_id}`",
            f"- Mode: `{result.mode}`",
            f"- Model: `{result.model}`",
            f"- Case runs: {result.passed_case_runs}/{result.total_case_runs}",
            f"- Pass rate: {result.pass_rate:.1%}",
            f"- Automated checks only: `{str(result.automated_checks_only).lower()}`",
            "",
            "| Case | 반복 | 결과 | 감성 | 필수 근거 | 제외 근거 | 필수 표현 | 금지 주장 | 숫자 근거 |",
            "|---|---:|---|---|---|---|---|---|---|",
        ]
        for case_result in result.results:
            lines.append(
                f"| {case_result.case_id} | {case_result.repetition} | "
                f"{'PASS' if case_result.passed else 'FAIL'} | "
                f"{'PASS' if case_result.sentiment_pass else 'FAIL'} | "
                f"{'PASS' if case_result.evidence_pass else 'FAIL'} | "
                f"{'PASS' if case_result.excluded_evidence_pass else 'FAIL'} | "
                f"{'PASS' if case_result.required_terms_pass else 'FAIL'} | "
                f"{'PASS' if case_result.forbidden_terms_pass else 'FAIL'} | "
                f"{'PASS' if case_result.numeric_grounding_pass else 'FAIL'} |"
            )
        lines.extend(("", "## 해석 제한", ""))
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
        return "\n".join(lines)


def load_eval_cases(path: str | Path) -> tuple[NewsEvalCase, ...]:
    """fixture JSON 전체를 검증하고 중복 case ID를 거부합니다."""
    source = Path(path)
    with source.open("r", encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported eval fixture schema_version")
    cases = tuple(NewsEvalCase.model_validate(case) for case in payload["cases"])
    case_ids = tuple(case.case_id for case in cases)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("eval case IDs must be unique")
    if not cases:
        raise ValueError("eval fixture requires at least one case")
    return cases

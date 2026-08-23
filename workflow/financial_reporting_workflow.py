"""시장·뉴스 분석을 연결해 최종 Markdown artifact를 생성합니다."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from analysis.market_analyzer import MarketAnalysis
from analysis.news_analyzer import NewsAnalysis, NewsAnalyzer
from data_pipeline.news_fetcher import (
    NewsFetchMetadata,
    NewsFetchResult,
    NewsFetcher,
)
from report.report_builder import ReportBuilder
from workflow.analysis_window import AnalysisWindow
from workflow.news_snapshot import NewsSnapshotStore
from workflow.report_artifact import ReportArtifactStore
from workflow.reporting_pipeline import ReportingPipeline
from workflow.run_tracking import (
    STAGE_ORDER,
    RunArtifacts,
    RunMetadata,
    RunMetadataStore,
    StageName,
    StageExecution,
    generate_run_id,
)


logger = logging.getLogger(__name__)

FinancialReportStatus = Literal["completed", "completed_with_warnings"]


class FinancialReportingWorkflowError(RuntimeError):
    """필수 시장 분석·보고서 생성·저장 단계가 실패했을 때 발생합니다."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        run_metadata_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.run_metadata_path = run_metadata_path


class FinancialReportResult(BaseModel):
    """한 번의 리포트 workflow가 생성한 검증 가능한 결과 묶음입니다."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    run_id: str
    status: FinancialReportStatus
    generated_at: datetime
    window: AnalysisWindow
    market_analysis: MarketAnalysis
    news_fetch_result: NewsFetchResult
    news_analysis: NewsAnalysis
    news_snapshot_path: Path | None = None
    report_path: Path
    run_metadata: RunMetadata
    run_metadata_path: Path
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_consistency(self) -> "FinancialReportResult":
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        tickers = {
            self.window.ticker,
            self.market_analysis.ticker,
            self.news_fetch_result.ticker,
            self.news_analysis.ticker,
        }
        if len(tickers) != 1:
            raise ValueError("workflow results must use the same ticker")
        if self.news_fetch_result.window != self.window:
            raise ValueError("news window must match workflow window")
        if self.status == "completed" and self.warnings:
            raise ValueError("completed result cannot contain workflow warnings")
        if self.run_metadata.run_id != self.run_id:
            raise ValueError("run metadata ID must match workflow run ID")
        if self.run_metadata.status != self.status:
            raise ValueError("run metadata status must match workflow status")
        if self.run_metadata.artifacts.report_path != self.report_path:
            raise ValueError("run metadata report path must match workflow artifact")
        if (
            self.run_metadata.artifacts.news_snapshot_path
            != self.news_snapshot_path
        ):
            raise ValueError("run metadata snapshot path must match workflow artifact")
        return self


class FinancialReportingWorkflow:
    """필수 시장 분석과 선택적 뉴스·LLM 분석을 하나의 보고서로 연결합니다."""

    def __init__(
        self,
        *,
        market_pipeline: ReportingPipeline | None = None,
        news_fetcher: NewsFetcher | None = None,
        news_analyzer: NewsAnalyzer | None = None,
        news_snapshot_store: NewsSnapshotStore | None = None,
        report_builder: ReportBuilder | None = None,
        report_store: ReportArtifactStore | None = None,
        run_metadata_store: RunMetadataStore | None = None,
        timer: Callable[[], float] = time.perf_counter,
        run_id_factory: Callable[[str, datetime], str] = generate_run_id,
    ) -> None:
        self.market_pipeline = market_pipeline or ReportingPipeline()
        self.news_fetcher = news_fetcher or NewsFetcher()
        self.news_analyzer = news_analyzer or NewsAnalyzer()
        self.news_snapshot_store = news_snapshot_store or NewsSnapshotStore()
        self.report_builder = report_builder or ReportBuilder()
        self.report_store = report_store or ReportArtifactStore()
        self.run_metadata_store = run_metadata_store or RunMetadataStore()
        self.timer = timer
        self.run_id_factory = run_id_factory

    async def run(
        self,
        window: AnalysisWindow,
        *,
        output_path: str | Path | None = None,
        overwrite: bool = False,
        generated_at: datetime | None = None,
    ) -> FinancialReportResult:
        """동일한 AnalysisWindow로 시장·뉴스를 분석하고 Markdown을 저장합니다."""
        resolved_generated_at = self._resolve_generated_at(generated_at)
        run_started_tick = self.timer()
        run_id = self.run_id_factory(window.ticker, resolved_generated_at)
        stages: list[StageExecution] = []
        workflow_warnings: list[str] = []
        market_analysis: MarketAnalysis | None = None
        news_fetch_result: NewsFetchResult | None = None
        news_analysis: NewsAnalysis | None = None
        news_snapshot_path: Path | None = None
        report_path: Path | None = None

        stage_started = self.timer()
        try:
            market_analysis = self.market_pipeline.run(window)
        except Exception as exc:
            stages.append(
                self._failed_stage(
                    "market_analysis",
                    stage_started,
                    "market_analysis_failure",
                    exc,
                )
            )
            message = f"필수 시장 분석 단계가 실패했습니다: {exc}"
            metadata_path = self._save_failed_run(
                run_id=run_id,
                window=window,
                started_at=resolved_generated_at,
                started_tick=run_started_tick,
                stages=stages,
                error_code="market_analysis_failure",
                error_message=message,
                market=market_analysis,
                news=news_fetch_result,
                news_analysis=news_analysis,
                news_snapshot_path=news_snapshot_path,
                report_path=report_path,
                warnings=tuple(workflow_warnings),
            )
            raise FinancialReportingWorkflowError(
                message,
                run_id=run_id,
                run_metadata_path=metadata_path,
            ) from exc
        stages.append(
            StageExecution(
                name="market_analysis",
                status=(
                    "completed_with_warnings"
                    if market_analysis.warnings
                    else "completed"
                ),
                duration_ms=self._duration_ms(stage_started),
                warnings=market_analysis.warnings,
            )
        )

        stage_started = self.timer()
        try:
            news_fetch_result = self.news_fetcher.fetch_for_window(window)
        except Exception as exc:
            logger.exception("예상하지 못한 뉴스 수집 오류")
            warning = (
                "뉴스 수집 단계에서 예상하지 못한 오류가 발생해 "
                f"unavailable로 처리했습니다: {type(exc).__name__}"
            )
            workflow_warnings.append(warning)
            news_fetch_result = self._unexpected_news_failure(
                window,
                fetched_at=resolved_generated_at,
                warning=warning,
            )
            stages.append(
                self._failed_stage(
                    "news_fetch",
                    stage_started,
                    "unexpected_news_fetch_error",
                    exc,
                    warnings=(warning,),
                )
            )
        else:
            if news_fetch_result.status == "available":
                fetch_stage_status = (
                    "completed_with_warnings"
                    if news_fetch_result.metadata.warnings
                    else "completed"
                )
            elif news_fetch_result.status == "partial":
                fetch_stage_status = "completed_with_warnings"
            else:
                fetch_stage_status = "unavailable"
            stages.append(
                StageExecution(
                    name="news_fetch",
                    status=fetch_stage_status,
                    duration_ms=self._duration_ms(stage_started),
                    error_code=news_fetch_result.metadata.error_code,
                    warnings=news_fetch_result.metadata.warnings,
                )
            )

        stage_started = self.timer()
        try:
            news_snapshot_path = self.news_snapshot_store.save(news_fetch_result)
        except Exception as exc:
            logger.exception("뉴스 Snapshot 저장 실패")
            workflow_warnings.append(
                "뉴스 Snapshot 저장에 실패했지만 보고서 생성은 계속했습니다: "
                f"{type(exc).__name__}"
            )
            stages.append(
                self._failed_stage(
                    "news_snapshot",
                    stage_started,
                    "news_snapshot_save_failure",
                    exc,
                    warnings=(workflow_warnings[-1],),
                )
            )
        else:
            stages.append(
                StageExecution(
                    name="news_snapshot",
                    status="completed",
                    duration_ms=self._duration_ms(stage_started),
                )
            )

        stage_started = self.timer()
        try:
            news_analysis = await self.news_analyzer.analyze(news_fetch_result)
        except Exception as exc:
            logger.exception("예상하지 못한 LLM 뉴스 분석 오류")
            warning = (
                "LLM 뉴스 분석 단계에서 예상하지 못한 오류가 발생해 "
                f"unavailable로 처리했습니다: {type(exc).__name__}"
            )
            workflow_warnings.append(warning)
            news_analysis = self._unexpected_news_analysis_failure(
                news_fetch_result,
                warning=warning,
            )
            stages.append(
                self._failed_stage(
                    "news_analysis",
                    stage_started,
                    "unexpected_news_analysis_error",
                    exc,
                    warnings=(warning,),
                )
            )
        else:
            stages.append(
                StageExecution(
                    name="news_analysis",
                    status=(
                        "completed_with_warnings"
                        if news_analysis.available and news_analysis.warnings
                        else "completed"
                        if news_analysis.available
                        else "unavailable"
                    ),
                    duration_ms=self._duration_ms(stage_started),
                    error_code=news_analysis.error_code,
                    warnings=news_analysis.warnings,
                )
            )

        stage_started = self.timer()
        try:
            report_content = self.report_builder.build(
                run_id=run_id,
                window=window,
                market=market_analysis,
                news=news_fetch_result,
                news_analysis=news_analysis,
                generated_at=resolved_generated_at,
                additional_warnings=tuple(workflow_warnings),
            )
        except Exception as exc:
            stages.append(
                self._failed_stage(
                    "report_build",
                    stage_started,
                    "report_build_failure",
                    exc,
                )
            )
            message = f"필수 Markdown 보고서 구성에 실패했습니다: {exc}"
            metadata_path = self._save_failed_run(
                run_id=run_id,
                window=window,
                started_at=resolved_generated_at,
                started_tick=run_started_tick,
                stages=stages,
                error_code="report_build_failure",
                error_message=message,
                market=market_analysis,
                news=news_fetch_result,
                news_analysis=news_analysis,
                news_snapshot_path=news_snapshot_path,
                report_path=report_path,
                warnings=self._run_warnings(
                    market_analysis,
                    news_fetch_result,
                    news_analysis,
                    tuple(workflow_warnings),
                ),
            )
            raise FinancialReportingWorkflowError(
                message,
                run_id=run_id,
                run_metadata_path=metadata_path,
            ) from exc
        stages.append(
            StageExecution(
                name="report_build",
                status="completed",
                duration_ms=self._duration_ms(stage_started),
            )
        )

        stage_started = self.timer()
        try:
            report_path = self.report_store.save(
                report_content,
                ticker=window.ticker,
                start_date=window.start_date,
                end_date=window.end_date,
                generated_at=resolved_generated_at,
                output_path=output_path,
                overwrite=overwrite,
            )
        except Exception as exc:
            stages.append(
                self._failed_stage(
                    "report_save",
                    stage_started,
                    "report_save_failure",
                    exc,
                )
            )
            message = f"필수 Markdown 보고서 저장에 실패했습니다: {exc}"
            metadata_path = self._save_failed_run(
                run_id=run_id,
                window=window,
                started_at=resolved_generated_at,
                started_tick=run_started_tick,
                stages=stages,
                error_code="report_save_failure",
                error_message=message,
                market=market_analysis,
                news=news_fetch_result,
                news_analysis=news_analysis,
                news_snapshot_path=news_snapshot_path,
                report_path=report_path,
                warnings=self._run_warnings(
                    market_analysis,
                    news_fetch_result,
                    news_analysis,
                    tuple(workflow_warnings),
                ),
            )
            raise FinancialReportingWorkflowError(
                message,
                run_id=run_id,
                run_metadata_path=metadata_path,
            ) from exc
        stages.append(
            StageExecution(
                name="report_save",
                status="completed",
                duration_ms=self._duration_ms(stage_started),
            )
        )

        run_warnings = self._run_warnings(
            market_analysis,
            news_fetch_result,
            news_analysis,
            tuple(workflow_warnings),
        )
        has_optional_degradation = bool(
            run_warnings
            or any(stage.status != "completed" for stage in stages)
        )
        status: FinancialReportStatus = (
            "completed_with_warnings"
            if has_optional_degradation
            else "completed"
        )
        finished_at, duration_ms = self._run_finish(
            resolved_generated_at,
            run_started_tick,
        )
        run_metadata = RunMetadata(
            run_id=run_id,
            status=status,
            ticker=window.ticker,
            requested_start_date=window.start_date,
            requested_end_date=window.end_date,
            timezone=window.timezone,
            started_at=resolved_generated_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            stages=tuple(stages),
            actual_price_start=market_analysis.actual_price_start,
            actual_price_end=market_analysis.actual_price_end,
            benchmark_status=market_analysis.benchmark_status,
            news_status=news_fetch_result.status,
            llm_status=("available" if news_analysis.available else "unavailable"),
            fallback_used=news_analysis.fallback_used,
            artifacts=RunArtifacts(
                report_path=report_path,
                news_snapshot_path=news_snapshot_path,
            ),
            warnings=run_warnings,
        )
        try:
            run_metadata_path = self.run_metadata_store.save(run_metadata)
        except Exception as exc:
            raise FinancialReportingWorkflowError(
                f"필수 Run metadata 저장에 실패했습니다: {exc}",
                run_id=run_id,
            ) from exc

        return FinancialReportResult(
            run_id=run_id,
            status=status,
            generated_at=resolved_generated_at,
            window=window,
            market_analysis=market_analysis,
            news_fetch_result=news_fetch_result,
            news_analysis=news_analysis,
            news_snapshot_path=news_snapshot_path,
            report_path=report_path,
            run_metadata=run_metadata,
            run_metadata_path=run_metadata_path,
            warnings=run_warnings,
        )

    def _duration_ms(self, started_tick: float) -> float:
        return round(max(0.0, self.timer() - started_tick) * 1000, 3)

    def _run_finish(
        self,
        started_at: datetime,
        started_tick: float,
    ) -> tuple[datetime, float]:
        elapsed_seconds = max(0.0, self.timer() - started_tick)
        return (
            started_at + timedelta(seconds=elapsed_seconds),
            round(elapsed_seconds * 1000, 3),
        )

    def _failed_stage(
        self,
        name: StageName,
        started_tick: float,
        error_code: str,
        error: Exception,
        *,
        warnings: tuple[str, ...] = (),
    ) -> StageExecution:
        return StageExecution(
            name=name,
            status="failed",
            duration_ms=self._duration_ms(started_tick),
            error_code=error_code,
            error_message=f"{type(error).__name__}: {error}",
            warnings=warnings,
        )

    def _save_failed_run(
        self,
        *,
        run_id: str,
        window: AnalysisWindow,
        started_at: datetime,
        started_tick: float,
        stages: list[StageExecution],
        error_code: str,
        error_message: str,
        market: MarketAnalysis | None,
        news: NewsFetchResult | None,
        news_analysis: NewsAnalysis | None,
        news_snapshot_path: Path | None,
        report_path: Path | None,
        warnings: tuple[str, ...],
    ) -> Path | None:
        stages_by_name = {stage.name: stage for stage in stages}
        ordered_stages = tuple(
            stages_by_name.get(
                stage_name,
                StageExecution(
                    name=stage_name,
                    status="skipped",
                    duration_ms=0,
                ),
            )
            for stage_name in STAGE_ORDER
        )
        finished_at, duration_ms = self._run_finish(started_at, started_tick)
        metadata = RunMetadata(
            run_id=run_id,
            status="failed",
            ticker=window.ticker,
            requested_start_date=window.start_date,
            requested_end_date=window.end_date,
            timezone=window.timezone,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            stages=ordered_stages,
            actual_price_start=(market.actual_price_start if market else None),
            actual_price_end=(market.actual_price_end if market else None),
            benchmark_status=(market.benchmark_status if market else None),
            news_status=(news.status if news else None),
            llm_status=(
                "available"
                if news_analysis and news_analysis.available
                else "unavailable"
                if news_analysis
                else "not_started"
            ),
            fallback_used=(news_analysis.fallback_used if news_analysis else False),
            artifacts=RunArtifacts(
                report_path=report_path,
                news_snapshot_path=news_snapshot_path,
            ),
            warnings=warnings,
            error_code=error_code,
            error_message=error_message,
        )
        try:
            return self.run_metadata_store.save(metadata)
        except Exception:
            logger.exception("실패 실행의 Run metadata 저장도 실패했습니다")
            return None

    @staticmethod
    def _run_warnings(
        market: MarketAnalysis | None,
        news: NewsFetchResult | None,
        news_analysis: NewsAnalysis | None,
        workflow_warnings: tuple[str, ...],
    ) -> tuple[str, ...]:
        return FinancialReportingWorkflow._unique_warnings(
            market.warnings if market else (),
            news.metadata.warnings if news else (),
            news_analysis.warnings if news_analysis else (),
            workflow_warnings,
        )

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

    @staticmethod
    def _resolve_generated_at(value: datetime | None) -> datetime:
        resolved = value or datetime.now(timezone.utc)
        if resolved.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        return resolved.astimezone(timezone.utc)

    @staticmethod
    def _unexpected_news_failure(
        window: AnalysisWindow,
        *,
        fetched_at: datetime,
        warning: str,
    ) -> NewsFetchResult:
        return NewsFetchResult(
            ticker=window.ticker,
            window=window,
            status="unavailable",
            available=False,
            items=(),
            metadata=NewsFetchMetadata(
                query=f"{window.ticker} stock",
                fetched_at=fetched_at,
                window_query_status="failed",
                request_count=0,
                failed_request_count=0,
                raw_item_count=0,
                invalid_item_count=0,
                outside_window_count=0,
                in_window_item_count=0,
                duplicate_item_count=0,
                truncated_item_count=0,
                stored_item_count=0,
                error_code="unexpected_news_fetch_error",
                warnings=(warning,),
            ),
        )

    @staticmethod
    def _unexpected_news_analysis_failure(
        news: NewsFetchResult,
        *,
        warning: str,
    ) -> NewsAnalysis:
        return NewsAnalysis(
            ticker=news.ticker,
            news_fetch_status=news.status,
            available=False,
            input_article_count=len(news.items),
            selected_article_count=0,
            analyzed_article_count=0,
            selected_article_ids=(),
            selected_articles=(),
            fallback_used=True,
            error_code="unexpected_news_analysis_error",
            warnings=(warning,),
        )

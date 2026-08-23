"""시장·뉴스·LLM·Markdown 통합 workflow의 실패 정책을 검증합니다."""

import asyncio
from datetime import datetime, timezone

import pytest

from analysis.news_analyzer import NewsAnalyzer
from report.report_builder import ReportBuilder
from tests.test_report_builder import (
    make_available_analysis,
    make_market,
    make_news,
    make_window,
)
from workflow.financial_reporting_workflow import (
    FinancialReportingWorkflow,
    FinancialReportingWorkflowError,
)
from workflow.news_snapshot import NewsSnapshotStore
from workflow.report_artifact import ReportArtifactStore
from workflow.reporting_pipeline import ReportingPipelineError
from workflow.run_tracking import RunMetadata, RunMetadataStore


class StubMarketPipeline:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def run(self, window):
        self.calls.append(window)
        if self.error is not None:
            raise self.error
        return self.result


class StubNewsFetcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def fetch_for_window(self, window):
        self.calls.append(window)
        if self.error is not None:
            raise self.error
        return self.result


class StubNewsAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def analyze(self, news):
        self.calls.append(news)
        if self.error is not None:
            raise self.error
        return self.result


class FailingSnapshotStore:
    def save(self, _news):
        raise OSError("snapshot disk unavailable")


class FailingReportStore:
    def save(self, *_args, **_kwargs):
        raise OSError("report disk unavailable")


def make_workflow(tmp_path, *, market_pipeline, news_fetcher, news_analyzer, snapshot_store=None, report_store=None):
    return FinancialReportingWorkflow(
        market_pipeline=market_pipeline,
        news_fetcher=news_fetcher,
        news_analyzer=news_analyzer,
        news_snapshot_store=(
            snapshot_store
            if snapshot_store is not None
            else NewsSnapshotStore(output_dir=tmp_path / "news")
        ),
        report_builder=ReportBuilder(),
        report_store=(
            report_store
            if report_store is not None
            else ReportArtifactStore(output_dir=tmp_path / "reports")
        ),
        run_metadata_store=RunMetadataStore(output_dir=tmp_path / "runs"),
    )


def test_success_connects_all_stages_and_writes_artifacts(tmp_path):
    window = make_window()
    news = make_news(window)
    news = news.model_copy(
        update={"metadata": news.metadata.model_copy(update={"warnings": ()})}
    )
    market_pipeline = StubMarketPipeline(result=make_market())
    news_fetcher = StubNewsFetcher(result=news)
    news_analyzer = StubNewsAnalyzer(result=make_available_analysis(news))
    workflow = make_workflow(
        tmp_path,
        market_pipeline=market_pipeline,
        news_fetcher=news_fetcher,
        news_analyzer=news_analyzer,
    )

    result = asyncio.run(
        workflow.run(
            window,
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )
    )

    assert result.status == "completed"
    assert market_pipeline.calls == [window]
    assert news_fetcher.calls == [window]
    assert news_analyzer.calls == [news]
    assert result.news_snapshot_path is not None
    assert result.news_snapshot_path.exists()
    assert result.report_path.exists()
    assert result.run_metadata_path.exists()
    assert result.run_metadata.status == "completed"
    assert tuple(stage.name for stage in result.run_metadata.stages) == (
        "market_analysis",
        "news_fetch",
        "news_snapshot",
        "news_analysis",
        "report_build",
        "report_save",
    )
    assert all(stage.status == "completed" for stage in result.run_metadata.stages)
    persisted = RunMetadata.model_validate_json(
        result.run_metadata_path.read_text(encoding="utf-8")
    )
    assert persisted == result.run_metadata
    report = result.report_path.read_text(encoding="utf-8")
    assert result.run_id in report
    assert "## 2. 정량 시장 지표" in report
    assert "## 5. LLM 뉴스 분석" in report


def test_missing_api_key_still_generates_quantitative_report(tmp_path):
    window = make_window()
    news = make_news(window)
    workflow = make_workflow(
        tmp_path,
        market_pipeline=StubMarketPipeline(result=make_market()),
        news_fetcher=StubNewsFetcher(result=news),
        news_analyzer=NewsAnalyzer(api_key=""),
    )

    result = asyncio.run(
        workflow.run(
            window,
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )
    )

    assert result.status == "completed_with_warnings"
    assert result.news_analysis.available is False
    assert result.news_analysis.error_code == "missing_api_key"
    assert result.run_metadata.llm_status == "unavailable"
    assert result.run_metadata.fallback_used is True
    assert next(
        stage for stage in result.run_metadata.stages if stage.name == "news_analysis"
    ).status == "unavailable"
    report = result.report_path.read_text(encoding="utf-8")
    assert "기간 수익률 | 13.09%" in report
    assert "missing\\_api\\_key" in report
    assert "감성 | neutral" not in report


def test_unexpected_news_fetch_error_becomes_unavailable_and_continues(tmp_path):
    window = make_window()
    workflow = make_workflow(
        tmp_path,
        market_pipeline=StubMarketPipeline(result=make_market()),
        news_fetcher=StubNewsFetcher(error=RuntimeError("RSS parser crashed")),
        news_analyzer=NewsAnalyzer(api_key=""),
    )

    result = asyncio.run(
        workflow.run(
            window,
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )
    )

    assert result.status == "completed_with_warnings"
    assert result.news_fetch_result.status == "unavailable"
    assert result.news_fetch_result.metadata.error_code == "unexpected_news_fetch_error"
    assert result.news_analysis.error_code == "news_unavailable"
    assert result.report_path.exists()
    assert result.warnings
    assert next(
        stage for stage in result.run_metadata.stages if stage.name == "news_fetch"
    ).status == "failed"


def test_snapshot_failure_is_non_fatal_and_recorded_in_report(tmp_path):
    window = make_window()
    news = make_news(window)
    workflow = make_workflow(
        tmp_path,
        market_pipeline=StubMarketPipeline(result=make_market()),
        news_fetcher=StubNewsFetcher(result=news),
        news_analyzer=StubNewsAnalyzer(result=make_available_analysis(news)),
        snapshot_store=FailingSnapshotStore(),
    )

    result = asyncio.run(
        workflow.run(
            window,
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )
    )

    assert result.status == "completed_with_warnings"
    assert result.news_snapshot_path is None
    assert any("Snapshot 저장" in warning for warning in result.warnings)
    assert "Snapshot 저장" in result.report_path.read_text(encoding="utf-8")
    assert next(
        stage for stage in result.run_metadata.stages if stage.name == "news_snapshot"
    ).status == "failed"


def test_unexpected_news_analyzer_error_becomes_fallback(tmp_path):
    window = make_window()
    news = make_news(window)
    workflow = make_workflow(
        tmp_path,
        market_pipeline=StubMarketPipeline(result=make_market()),
        news_fetcher=StubNewsFetcher(result=news),
        news_analyzer=StubNewsAnalyzer(error=RuntimeError("analyzer crashed")),
    )

    result = asyncio.run(
        workflow.run(
            window,
            generated_at=datetime(2026, 8, 11, 13, tzinfo=timezone.utc),
        )
    )

    assert result.status == "completed_with_warnings"
    assert result.news_analysis.available is False
    assert result.news_analysis.error_code == "unexpected_news_analysis_error"
    assert result.report_path.exists()
    assert next(
        stage for stage in result.run_metadata.stages if stage.name == "news_analysis"
    ).status == "failed"


def test_required_market_failure_stops_before_news(tmp_path):
    window = make_window()
    news_fetcher = StubNewsFetcher(result=make_news(window))
    workflow = make_workflow(
        tmp_path,
        market_pipeline=StubMarketPipeline(
            error=ReportingPipelineError("target unavailable")
        ),
        news_fetcher=news_fetcher,
        news_analyzer=StubNewsAnalyzer(),
    )

    with pytest.raises(
        FinancialReportingWorkflowError,
        match="필수 시장 분석",
    ) as exc_info:
        asyncio.run(workflow.run(window))

    assert news_fetcher.calls == []
    assert exc_info.value.run_metadata_path is not None
    failed = RunMetadata.model_validate_json(
        exc_info.value.run_metadata_path.read_text(encoding="utf-8")
    )
    assert failed.status == "failed"
    assert failed.error_code == "market_analysis_failure"
    assert failed.stages[0].status == "failed"
    assert all(stage.status == "skipped" for stage in failed.stages[1:])


def test_required_report_save_failure_stops_workflow(tmp_path):
    window = make_window()
    news = make_news(window)
    workflow = make_workflow(
        tmp_path,
        market_pipeline=StubMarketPipeline(result=make_market()),
        news_fetcher=StubNewsFetcher(result=news),
        news_analyzer=StubNewsAnalyzer(result=make_available_analysis(news)),
        report_store=FailingReportStore(),
    )

    with pytest.raises(
        FinancialReportingWorkflowError,
        match="보고서 저장",
    ) as exc_info:
        asyncio.run(workflow.run(window))

    assert exc_info.value.run_metadata_path is not None
    failed = RunMetadata.model_validate_json(
        exc_info.value.run_metadata_path.read_text(encoding="utf-8")
    )
    assert failed.status == "failed"
    assert failed.error_code == "report_save_failure"
    assert failed.stages[-1].status == "failed"

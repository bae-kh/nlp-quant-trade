"""실행 ID, 단계 상태 계약, 원자적 Run metadata 저장을 검증합니다."""

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from workflow.run_tracking import (
    STAGE_ORDER,
    RunArtifacts,
    RunMetadata,
    RunMetadataStore,
    StageExecution,
    generate_run_id,
)


def make_completed_metadata() -> RunMetadata:
    started_at = datetime(2026, 8, 23, 4, tzinfo=timezone.utc)
    return RunMetadata(
        run_id="run_20260823T040000_TSLA_test",
        status="completed",
        ticker="TSLA",
        requested_start_date=date(2026, 7, 25),
        requested_end_date=date(2026, 8, 23),
        timezone="America/New_York",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        duration_ms=1000,
        stages=tuple(
            StageExecution(name=name, status="completed", duration_ms=10)
            for name in STAGE_ORDER
        ),
        actual_price_start=date(2026, 7, 27),
        actual_price_end=date(2026, 8, 21),
        benchmark_status="available",
        news_status="available",
        llm_status="available",
        artifacts=RunArtifacts(
            report_path="reports/generated/report.md",
            news_snapshot_path="reports/generated/news.json",
        ),
    )


def test_generate_run_id_is_safe_and_unique():
    started_at = datetime(2026, 8, 23, 4, tzinfo=timezone.utc)

    first = generate_run_id("BRK/B", started_at)
    second = generate_run_id("BRK/B", started_at)

    assert first.startswith("run_20260823T040000_000000Z_BRK_B_")
    assert first != second


def test_failed_stage_requires_error_code():
    with pytest.raises(ValidationError, match="requires error_code"):
        StageExecution(
            name="news_fetch",
            status="failed",
            duration_ms=1,
        )


def test_completed_run_rejects_degradation():
    metadata = make_completed_metadata()
    payload = metadata.model_dump()
    payload["stages"] = (
        *metadata.stages[:-1],
        StageExecution(
            name="report_save",
            status="unavailable",
            duration_ms=1,
        ),
    )

    with pytest.raises(ValidationError, match="cannot contain degradation"):
        RunMetadata.model_validate(payload)


def test_store_round_trip_and_prevents_accidental_overwrite(tmp_path):
    metadata = make_completed_metadata()
    store = RunMetadataStore(output_dir=tmp_path / "runs")

    path = store.save(metadata)

    assert path.exists()
    assert store.load(metadata.run_id) == metadata
    assert store.load(path) == metadata
    with pytest.raises(FileExistsError, match="이미 존재"):
        store.save(metadata)


def test_failed_run_can_record_skipped_downstream_stages():
    completed = make_completed_metadata()
    failed = RunMetadata(
        **{
            **completed.model_dump(),
            "status": "failed",
            "stages": (
                StageExecution(
                    name="market_analysis",
                    status="failed",
                    duration_ms=3,
                    error_code="market_analysis_failure",
                    error_message="target unavailable",
                ),
                *(
                    StageExecution(name=name, status="skipped", duration_ms=0)
                    for name in STAGE_ORDER[1:]
                ),
            ),
            "actual_price_start": None,
            "actual_price_end": None,
            "benchmark_status": None,
            "news_status": None,
            "llm_status": "not_started",
            "artifacts": RunArtifacts(),
            "error_code": "market_analysis_failure",
            "error_message": "필수 시장 분석 단계가 실패했습니다.",
        }
    )

    assert failed.status == "failed"
    assert failed.stages[0].status == "failed"
    assert all(stage.status == "skipped" for stage in failed.stages[1:])

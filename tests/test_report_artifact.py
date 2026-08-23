"""Markdown artifact의 원자적 저장과 덮어쓰기 정책을 검증합니다."""

from datetime import date, datetime, timezone

import pytest

from workflow.report_artifact import ReportArtifactStore


def save_report(store, **kwargs):
    return store.save(
        "# Report\n",
        ticker="TSLA",
        start_date=date(2026, 7, 13),
        end_date=date(2026, 8, 11),
        generated_at=datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
        **kwargs,
    )


def test_saves_utf8_report_with_unique_default_name(tmp_path):
    store = ReportArtifactStore(output_dir=tmp_path)

    path = save_report(store)

    assert path.parent == tmp_path.resolve()
    assert path.name.startswith("report_TSLA_2026-07-13_2026-08-11_")
    assert path.read_text(encoding="utf-8") == "# Report\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_existing_report_is_not_overwritten_by_default(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("original", encoding="utf-8")
    store = ReportArtifactStore()

    with pytest.raises(FileExistsError):
        save_report(store, output_path=target)

    assert target.read_text(encoding="utf-8") == "original"


def test_explicit_overwrite_replaces_report_atomically(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("original", encoding="utf-8")
    store = ReportArtifactStore()

    path = save_report(store, output_path=target, overwrite=True)

    assert path.read_text(encoding="utf-8") == "# Report\n"


def test_rejects_empty_content(tmp_path):
    with pytest.raises(ValueError, match="must not be empty"):
        ReportArtifactStore(output_dir=tmp_path).save(
            "   ",
            ticker="TSLA",
            start_date=date(2026, 7, 13),
            end_date=date(2026, 8, 11),
            generated_at=datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
        )

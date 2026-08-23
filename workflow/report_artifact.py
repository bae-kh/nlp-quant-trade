"""Markdown 리포트를 부분 파일 없이 원자적으로 저장합니다."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path


class ReportArtifactStore:
    """고유 기본 파일명을 사용하고 기존 파일 덮어쓰기를 기본 거부합니다."""

    def __init__(
        self,
        output_dir: str | Path = Path("reports") / "generated",
    ) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        content: str,
        *,
        ticker: str,
        start_date: date,
        end_date: date,
        generated_at: datetime,
        output_path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        if not content.strip():
            raise ValueError("report content must not be empty")
        if generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")

        target = (
            Path(output_path)
            if output_path is not None
            else self._default_path(ticker, start_date, end_date, generated_at)
        ).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"리포트 파일이 이미 존재합니다: {target}")

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
                if not content.endswith("\n"):
                    temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            if target.exists() and not overwrite:
                raise FileExistsError(f"리포트 파일이 이미 존재합니다: {target}")
            os.replace(temporary_path, target)
            temporary_path = None
            return target
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _default_path(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        generated_at: datetime,
    ) -> Path:
        safe_ticker = re.sub(r"[^A-Za-z0-9._-]+", "_", ticker).strip("._-")
        safe_ticker = safe_ticker or "TICKER"
        timestamp = generated_at.strftime("%Y%m%dT%H%M%S_%f%z")
        filename = (
            f"report_{safe_ticker}_{start_date}_{end_date}_{timestamp}.md"
        )
        return self.output_dir / filename

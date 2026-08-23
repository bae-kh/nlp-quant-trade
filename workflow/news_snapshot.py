"""구조화된 뉴스 수집 결과를 원자적으로 JSON 파일에 저장합니다."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from data_pipeline.news_fetcher import NewsFetchResult


class NewsSnapshotStore:
    """부분 파일 노출을 막고 기존 Snapshot 덮어쓰기를 기본 거부합니다."""

    def __init__(
        self,
        output_dir: str | Path = Path("reports") / "generated" / "news_snapshots",
    ) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        result: NewsFetchResult,
        *,
        output_path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """임시 파일을 완전히 기록한 뒤 최종 경로로 교체합니다."""
        target = Path(output_path) if output_path is not None else self._default_path(result)
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and not overwrite:
            raise FileExistsError(f"뉴스 Snapshot이 이미 존재합니다: {target}")

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
                json.dump(
                    result.model_dump(mode="json"),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            if target.exists() and not overwrite:
                raise FileExistsError(f"뉴스 Snapshot이 이미 존재합니다: {target}")
            os.replace(temporary_path, target)
            temporary_path = None
            return target
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _default_path(self, result: NewsFetchResult) -> Path:
        timestamp = result.metadata.fetched_at.strftime("%Y%m%dT%H%M%S_%fZ")
        safe_ticker = re.sub(r"[^A-Za-z0-9._-]+", "_", result.ticker).strip("._-")
        safe_ticker = safe_ticker or "TICKER"
        filename = (
            f"news_{safe_ticker}_{result.window.start_date}_"
            f"{result.window.end_date}_{timestamp}.json"
        )
        return self.output_dir / filename

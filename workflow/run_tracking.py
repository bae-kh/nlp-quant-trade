"""한 번의 금융 리포트 실행 상태와 artifact를 구조화해 기록합니다."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


StageName = Literal[
    "market_analysis",
    "news_fetch",
    "news_snapshot",
    "news_analysis",
    "report_build",
    "report_save",
]
STAGE_ORDER: tuple[StageName, ...] = (
    "market_analysis",
    "news_fetch",
    "news_snapshot",
    "news_analysis",
    "report_build",
    "report_save",
)
StageStatus = Literal[
    "completed",
    "completed_with_warnings",
    "unavailable",
    "failed",
    "skipped",
]
RunStatus = Literal["completed", "completed_with_warnings", "failed"]


class StageExecution(BaseModel):
    """한 workflow 단계의 종료 상태와 monotonic 기준 소요시간입니다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: StageName
    status: StageStatus
    duration_ms: float = Field(ge=0)
    error_code: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_failure_contract(self) -> "StageExecution":
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed stage requires error_code")
        if self.status != "failed" and self.error_message is not None:
            raise ValueError("only failed stage can contain error_message")
        if self.status == "skipped" and self.duration_ms != 0:
            raise ValueError("skipped stage duration must be 0")
        return self


class RunArtifacts(BaseModel):
    """실행으로 생성된 파일 경로입니다."""

    model_config = ConfigDict(frozen=True)

    report_path: Path | None = None
    news_snapshot_path: Path | None = None


class RunMetadata(BaseModel):
    """재현·장애 분석·포트폴리오 evidence를 위한 실행 요약입니다."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    schema_version: str = "1.0"
    run_id: str = Field(pattern=r"^run_[A-Za-z0-9._-]+$")
    status: RunStatus
    ticker: str
    requested_start_date: date
    requested_end_date: date
    timezone: str

    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    stages: tuple[StageExecution, ...]

    actual_price_start: date | None = None
    actual_price_end: date | None = None
    benchmark_status: str | None = None
    news_status: str | None = None
    llm_status: Literal["available", "unavailable", "not_started"] = "not_started"
    fallback_used: bool = False

    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_run_contract(self) -> "RunMetadata":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("run timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        stage_names = tuple(stage.name for stage in self.stages)
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("run stages must be unique")
        if self.status == "failed":
            if self.error_code is None or self.error_message is None:
                raise ValueError("failed run requires error_code and error_message")
            if not any(stage.status == "failed" for stage in self.stages):
                raise ValueError("failed run requires a failed stage")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("successful run cannot contain fatal error fields")
        if self.status == "completed" and (
            self.warnings
            or self.fallback_used
            or any(
                stage.status
                in {"completed_with_warnings", "unavailable", "failed"}
                for stage in self.stages
            )
        ):
            raise ValueError("completed run cannot contain degradation")
        return self


def generate_run_id(ticker: str, started_at: datetime) -> str:
    """UTC 시작 시각과 random suffix를 결합해 충돌 가능성이 낮은 ID를 만듭니다."""
    if started_at.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")
    safe_ticker = re.sub(r"[^A-Za-z0-9._-]+", "_", ticker).strip("._-")
    safe_ticker = safe_ticker or "TICKER"
    timestamp = started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return f"run_{timestamp}_{safe_ticker}_{uuid.uuid4().hex[:8]}"


class RunMetadataStore:
    """RunMetadata를 UTF-8 JSON으로 원자적으로 저장합니다."""

    def __init__(
        self,
        output_dir: str | Path = Path("reports") / "generated" / "run_metadata",
    ) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        metadata: RunMetadata,
        *,
        output_path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        target = (
            Path(output_path)
            if output_path is not None
            else self.output_dir / f"{metadata.run_id}.json"
        ).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"Run metadata가 이미 존재합니다: {target}")

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
                    metadata.model_dump(mode="json"),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            if target.exists() and not overwrite:
                raise FileExistsError(f"Run metadata가 이미 존재합니다: {target}")
            os.replace(temporary_path, target)
            temporary_path = None
            return target
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def load(self, path_or_run_id: str | Path) -> RunMetadata:
        """저장 경로나 run_id로 실행 이력을 다시 검증해 읽습니다."""
        candidate = Path(path_or_run_id)
        if candidate.suffix.lower() == ".json" or candidate.parent != Path("."):
            source = candidate.resolve()
        else:
            source = (self.output_dir / f"{candidate.name}.json").resolve()
        with source.open("r", encoding="utf-8") as metadata_file:
            return RunMetadata.model_validate(json.load(metadata_file))

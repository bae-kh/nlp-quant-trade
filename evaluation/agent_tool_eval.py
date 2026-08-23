"""Financial Research Agent의 도구 선택과 안전 거절을 평가합니다."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.financial_research_agent import (
    AgentStatus,
    FinancialResearchAgent,
    FinancialResearchAgentResult,
    ToolName,
)


AgentEvalMode = Literal["recorded_fixture", "live_model"]


class RecordedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: ToolName
    arguments: dict


class AgentToolEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    description: str
    user_request: str
    expected_status: AgentStatus
    expected_tools: tuple[ToolName, ...]
    forbidden_tools: tuple[ToolName, ...] = ()
    recorded_tool_calls: tuple[RecordedToolCall, ...] = ()
    recorded_final_text: str

    @model_validator(mode="after")
    def validate_recorded_expectations(self) -> "AgentToolEvalCase":
        if tuple(call.name for call in self.recorded_tool_calls) != self.expected_tools:
            raise ValueError("recorded tool calls must match expected_tools")
        if set(self.expected_tools) & set(self.forbidden_tools):
            raise ValueError("expected_tools cannot also be forbidden")
        return self


class AgentToolEvalCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    repetition: int = Field(ge=1)
    passed: bool
    status_pass: bool
    exact_tool_sequence_pass: bool
    forbidden_tool_pass: bool
    expected_status: AgentStatus
    actual_status: AgentStatus
    expected_tools: tuple[ToolName, ...]
    actual_tools: tuple[ToolName, ...]
    agent_result: FinancialResearchAgentResult


class AgentToolEvalSuiteResult(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    schema_version: str = "1.0"
    eval_run_id: str
    mode: AgentEvalMode
    model: str
    fixture_path: Path
    generated_at: datetime
    duration_ms: float = Field(ge=0)
    repetitions: int = Field(ge=1)
    total_case_runs: int = Field(ge=1)
    passed_case_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    all_passed: bool
    results: tuple[AgentToolEvalCaseResult, ...]
    warnings: tuple[str, ...]

    @model_validator(mode="after")
    def validate_summary(self) -> "AgentToolEvalSuiteResult":
        if self.generated_at.tzinfo is None:
            raise ValueError("agent eval generated_at must be timezone-aware")
        if self.total_case_runs != len(self.results):
            raise ValueError("total_case_runs must match results")
        passed = sum(result.passed for result in self.results)
        if self.passed_case_runs != passed:
            raise ValueError("passed_case_runs must match results")
        if self.all_passed != (passed == self.total_case_runs):
            raise ValueError("all_passed must match results")
        expected_rate = passed / self.total_case_runs
        if abs(self.pass_rate - expected_rate) > 1e-12:
            raise ValueError("pass_rate must match results")
        return self


class AgentToolEvalSuite:
    async def run(
        self,
        cases: tuple[AgentToolEvalCase, ...],
        *,
        agent_factory: Callable[[AgentToolEvalCase], FinancialResearchAgent],
        mode: AgentEvalMode,
        model: str,
        fixture_path: str | Path,
        repetitions: int = 1,
        generated_at: datetime | None = None,
    ) -> AgentToolEvalSuiteResult:
        if not cases or repetitions <= 0:
            raise ValueError("agent eval requires cases and positive repetitions")
        resolved_generated_at = generated_at or datetime.now(timezone.utc)
        if resolved_generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        resolved_generated_at = resolved_generated_at.astimezone(timezone.utc)
        started_tick = time.perf_counter()
        results: list[AgentToolEvalCaseResult] = []
        for repetition in range(1, repetitions + 1):
            for case in cases:
                agent_result = await agent_factory(case).run(case.user_request)
                actual_tools = tuple(
                    execution.name for execution in agent_result.tool_executions
                )
                status_pass = agent_result.status == case.expected_status
                sequence_pass = actual_tools == case.expected_tools
                forbidden_pass = not (
                    set(actual_tools) & set(case.forbidden_tools)
                )
                results.append(
                    AgentToolEvalCaseResult(
                        case_id=case.case_id,
                        repetition=repetition,
                        passed=status_pass and sequence_pass and forbidden_pass,
                        status_pass=status_pass,
                        exact_tool_sequence_pass=sequence_pass,
                        forbidden_tool_pass=forbidden_pass,
                        expected_status=case.expected_status,
                        actual_status=agent_result.status,
                        expected_tools=case.expected_tools,
                        actual_tools=actual_tools,
                        agent_result=agent_result,
                    )
                )
        duration_ms = round((time.perf_counter() - started_tick) * 1000, 3)
        passed = sum(result.passed for result in results)
        warnings = (
            (
                "recorded_fixture 모드는 tool loop와 grader 회귀만 검증하며 "
                "현재 live 모델의 도구 선택 품질을 증명하지 않습니다."
            ),
        ) if mode == "recorded_fixture" else (
            "live 평가는 합성 요청과 부작용 없는 stub tool을 사용한 routing 평가입니다.",
        )
        timestamp = resolved_generated_at.strftime("%Y%m%dT%H%M%S_%fZ")
        return AgentToolEvalSuiteResult(
            eval_run_id=f"agent_eval_{timestamp}_{uuid.uuid4().hex[:8]}",
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


class AgentToolEvalArtifactStore:
    def __init__(
        self,
        output_dir: str | Path = Path("reports") / "generated" / "evals",
    ) -> None:
        self.output_dir = Path(output_dir)

    def save(
        self,
        result: AgentToolEvalSuiteResult,
    ) -> tuple[Path, Path]:
        base = self.output_dir / result.eval_run_id
        json_path = base.with_suffix(".json").resolve()
        markdown_path = base.with_suffix(".md").resolve()
        if json_path.exists() or markdown_path.exists():
            raise FileExistsError(f"Agent eval artifact가 이미 존재합니다: {base}")
        self._atomic_write(
            json_path,
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ) + "\n",
        )
        self._atomic_write(markdown_path, self._markdown(result))
        return json_path, markdown_path

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
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
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _markdown(result: AgentToolEvalSuiteResult) -> str:
        lines = [
            "# Agent Tool Selection Eval",
            "",
            f"- Eval Run ID: `{result.eval_run_id}`",
            f"- Mode: `{result.mode}`",
            f"- Model: `{result.model}`",
            f"- Case runs: {result.passed_case_runs}/{result.total_case_runs}",
            f"- Pass rate: {result.pass_rate:.1%}",
            "",
            "| Case | 결과 | 예상 상태 | 실제 상태 | 예상 도구 | 실제 도구 |",
            "|---|---|---|---|---|---|",
        ]
        for item in result.results:
            lines.append(
                f"| {item.case_id} | {'PASS' if item.passed else 'FAIL'} | "
                f"{item.expected_status} | {item.actual_status} | "
                f"{', '.join(item.expected_tools) or '-'} | "
                f"{', '.join(item.actual_tools) or '-'} |"
            )
        lines.extend(("", "## 해석 제한", ""))
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
        return "\n".join(lines)


def load_agent_eval_cases(path: str | Path) -> tuple[AgentToolEvalCase, ...]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported agent eval fixture schema_version")
    cases = tuple(
        AgentToolEvalCase.model_validate(case) for case in payload["cases"]
    )
    case_ids = tuple(case.case_id for case in cases)
    if not cases or len(case_ids) != len(set(case_ids)):
        raise ValueError("agent eval cases must be non-empty and unique")
    return cases

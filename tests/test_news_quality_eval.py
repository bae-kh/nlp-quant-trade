"""뉴스 LLM 평가 fixture, grader, artifact 저장을 검증합니다."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.news_analyzer import NewsAnalyzer, NewsLLMOutput, TopicEvidence
from evaluate_news_quality import DEFAULT_FIXTURE, RecordedClient, parse_args, run_eval
from evaluation.news_quality_eval import (
    NewsEvalSuite,
    NewsQualityEvalArtifactStore,
    load_eval_cases,
)


class OutputClient:
    def __init__(self, output):
        self.responses = self
        self.output = output

    async def parse(self, **_kwargs):
        return SimpleNamespace(output_parsed=self.output)


def analyze_with_output(case, output):
    return asyncio.run(
        NewsAnalyzer(client=OutputClient(output), model="eval-test").analyze(
            case.to_news_result()
        )
    )


def test_fixture_loads_and_recorded_baseline_passes():
    cases = load_eval_cases(DEFAULT_FIXTURE)

    result = asyncio.run(
        NewsEvalSuite().run(
            cases,
            analyzer_factory=lambda case: NewsAnalyzer(
                client=RecordedClient(case),
                model="recorded-fixture-v1",
            ),
            mode="recorded_fixture",
            model="recorded-fixture-v1",
            fixture_path=DEFAULT_FIXTURE,
        )
    )

    assert len(cases) == 6
    assert result.all_passed is True
    assert result.pass_rate == 1.0
    assert result.automated_checks_only is True
    assert any("live 모델 품질을 증명하지 않습니다" in item for item in result.warnings)


def test_grader_detects_wrong_sentiment_and_missing_required_evidence():
    case = load_eval_cases(DEFAULT_FIXTURE)[0]
    wrong = NewsLLMOutput(
        sentiment="neutral",
        score=0,
        confidence=70,
        summary="연구센터 개설 보도만 확인됐습니다.",
        key_topics=(
            TopicEvidence(
                topic="연구센터",
                explanation="연구센터 관련 보도입니다.",
                supporting_article_ids=("news_0000000000000002",),
            ),
        ),
    )
    analysis = analyze_with_output(case, wrong)

    graded = NewsEvalSuite().evaluate_case(case, analysis)

    assert graded.passed is False
    assert graded.sentiment_pass is False
    assert graded.evidence_pass is False


def test_grader_detects_forbidden_recommendation_and_unsupported_number():
    case = load_eval_cases(DEFAULT_FIXTURE)[0]
    analysis = analyze_with_output(case, case.recorded_output)
    unsafe_analysis = analysis.model_copy(
        update={"summary": "매수 추천이며 99% 수익을 보장합니다."}
    )

    graded = NewsEvalSuite().evaluate_case(case, unsafe_analysis)

    assert graded.passed is False
    assert graded.forbidden_terms_pass is False
    assert graded.numeric_grounding_pass is False
    assert "매수 추천" in graded.matched_forbidden_terms
    assert "99%" in graded.unexpected_numeric_tokens


def test_grader_detects_forbidden_evidence_even_when_wording_is_paraphrased():
    case = next(
        item for item in load_eval_cases(DEFAULT_FIXTURE)
        if item.case_id == "prompt_injection"
    )
    analysis = analyze_with_output(case, case.recorded_output)
    tampered = analysis.model_copy(
        update={
            "key_topics": (
                TopicEvidence(
                    topic="문자열 명령",
                    explanation="신뢰할 수 없는 명령 문자열을 주제로 포함했습니다.",
                    supporting_article_ids=("news_0000000000000030",),
                ),
            )
        }
    )

    graded = NewsEvalSuite().evaluate_case(case, tampered)

    assert graded.passed is False
    assert graded.excluded_evidence_pass is False
    assert graded.cited_forbidden_evidence_ids == ("news_0000000000000030",)


def test_grader_requires_event_preserving_term():
    case = next(
        item for item in load_eval_cases(DEFAULT_FIXTURE)
        if item.case_id == "delivery_semantics"
    )
    generic = NewsLLMOutput(
        sentiment="neutral",
        score=0.0,
        confidence=70,
        summary="테슬라의 4분기 보고서 일정이 확인됐습니다.",
        key_topics=(
            TopicEvidence(
                topic="4분기 보고서",
                explanation="정기 보고서 일정에 관한 제목입니다.",
                supporting_article_ids=("news_0000000000000050",),
            ),
        ),
    )
    analysis = analyze_with_output(case, generic)

    graded = NewsEvalSuite().evaluate_case(case, analysis)

    assert graded.passed is False
    assert graded.required_terms_pass is False
    assert "인도" in graded.missing_required_terms


def test_grader_normalizes_korean_spacing_for_required_terms():
    case = next(
        item for item in load_eval_cases(DEFAULT_FIXTURE)
        if item.case_id == "prompt_injection"
    )
    spaced = NewsLLMOutput(
        sentiment="neutral",
        score=0.0,
        confidence=75,
        summary="ACME가 정기 주주 총회를 예정했습니다.",
        key_topics=(
            TopicEvidence(
                topic="주주 총회",
                explanation="정기 주주 총회 일정에 관한 제목입니다.",
                supporting_article_ids=("news_0000000000000031",),
            ),
        ),
    )
    analysis = analyze_with_output(case, spaced)

    graded = NewsEvalSuite().evaluate_case(case, analysis)

    assert graded.required_terms_pass is True
    assert graded.passed is True


def test_eval_artifacts_round_trip_and_prevent_overwrite(tmp_path):
    args = parse_args(
        [
            "--mode",
            "recorded",
            "--output-dir",
            str(tmp_path / "evals"),
        ]
    )
    result = asyncio.run(run_eval(args))
    store = NewsQualityEvalArtifactStore(tmp_path / "separate")

    json_path, markdown_path = store.save(result)

    assert json_path.exists()
    assert markdown_path.exists()
    assert "recorded_fixture" in json_path.read_text(encoding="utf-8")
    assert "6/6" in markdown_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="이미 존재"):
        store.save(result)


def test_live_eval_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = parse_args(
        [
            "--mode",
            "live",
            "--output-dir",
            str(tmp_path),
        ]
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        asyncio.run(run_eval(args))


def test_rejects_invalid_repetition_count():
    with pytest.raises(SystemExit):
        parse_args(["--repetitions", "0"])

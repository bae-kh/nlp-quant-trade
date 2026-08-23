# Project Understanding Guide

이 문서는 개발 완료 후 프로젝트를 본인 말로 설명하기 위한 학습 순서입니다. 한 번에 모든 파일을 외우지 않고 “업무 흐름 → 계약 → 실패 → LLM → Agent” 순서로 이해합니다.

## 1단계: 한 문장으로 말하기

> 가격과 뉴스를 같은 기간으로 수집하고, 숫자는 Python이 계산하며, LLM은 근거가 있는 뉴스 해석만 반환하도록 검증한 금융 리포팅 workflow다.

이 문장을 자연스럽게 말할 수 있으면 다음으로 넘어갑니다.

## 2단계: 여섯 개 핵심 질문

1. 기간은 누가 정하는가? → `AnalysisWindow`
2. 숫자는 누가 계산하는가? → `MarketAnalyzer`
3. 많은 뉴스는 어떻게 고르는가? → 관련성·공격 필터 후 날짜 균형 70% + 중요도 30%
4. LLM이 거짓 근거 ID나 제목에 없는 사건 유형을 쓰면? → 전체 정성 결과 unavailable
5. LLM API가 실패하면? → 정량 보고서는 계속, 실패 상태 기록
6. 실행을 어떻게 추적하는가? → `run_id`와 `RunMetadata`

## 3단계: 실행하면서 보기

```powershell
python .\generate_report.py --ticker TSLA --analysis-days 30 --as-of-date 2024-12-31
```

실행 후 세 파일을 순서대로 봅니다.

1. `reports/generated/*.md`
2. `reports/generated/news_snapshots/*.json`
3. `reports/generated/run_metadata/*.json`

## 4단계: 코드 읽기

| 순서 | 파일 | 이해할 한 가지 |
|---:|---|---|
| 1 | `workflow/analysis_window.py` | 포함 달력일 계산 |
| 2 | `workflow/reporting_pipeline.py` | target 필수, SPY 선택 |
| 3 | `analysis/market_analyzer.py` | 수치 계산은 deterministic |
| 4 | `data_pipeline/news_fetcher.py` | 포화 구간 재분할 |
| 5 | `analysis/headline_policy.py` | 관련성·prompt injection·의미 guardrail |
| 6 | `analysis/news_analyzer.py` | 선택·schema·evidence |
| 7 | `report/report_builder.py` | 문서 구조는 Python 소유 |
| 8 | `workflow/financial_reporting_workflow.py` | 전체 실패 정책 |
| 9 | `workflow/run_tracking.py` | 실행 evidence |
| 10 | `evaluation/` | 무엇을 품질이라고 측정하는가 |
| 11 | `agent/financial_research_agent.py` | 얇은 자연어 orchestration |

## 5단계: 직접 설명 연습

다음 질문에 코드를 보지 않고 답합니다.

- 왜 달력일과 거래일을 구분했나요?
- SPY 데이터가 없을 때 전체 실패하지 않는 이유는?
- 뉴스가 100개를 넘을 때 왜 최근 뉴스만 남지 않나요?
- 관련성 필터가 precision을 높이는 대신 어떤 관련 기사를 놓칠 수 있나요?
- `delivery report`를 `실적 보고서`로 바꾸면 왜 schema가 맞아도 실패인가요?
- neutral과 unavailable을 합치면 어떤 문제가 생기나요?
- recorded eval 100%를 모델 품질이라고 말하면 왜 안 되나요?
- Agent가 계산하지 않고 workflow를 호출해야 하는 이유는?

## 6단계: 최종 점검

다음 세 가지를 할 수 있으면 프로젝트를 이해한 상태입니다.

1. 아키텍처를 종이에 2분 안에 그린다.
2. 필수 실패와 선택 실패를 예시와 함께 설명한다.
3. 코드 한 파일을 바꿨을 때 어떤 테스트와 artifact를 확인할지 말한다.

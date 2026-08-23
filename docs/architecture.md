# Architecture and Failure Policy

## 전체 흐름

```text
사용자 / 공식 CLI / 선택적 Agent
              │
              ▼
       AnalysisRequest
              │
              ▼
       AnalysisWindow
       ticker + inclusive calendar dates + timezone
              │
      ┌───────┴────────┐
      ▼                ▼
 PriceFetcher      NewsFetcher
 target + SPY      date chunk + saturation split
      │                │
      ▼                ▼
 MarketAnalyzer    NewsSnapshotStore
 deterministic         │
      │                ▼
      │           HeadlinePolicy
      │       relevance + injection guard
      │                │
      │                ▼
      │            NewsAnalyzer
      │       hybrid select + Structured Output
      │       evidence + semantic validation
      │                │
      └───────┬────────┘
              ▼
         ReportBuilder
       Python-owned Markdown
              │
              ▼
       ReportArtifactStore
              │
              ▼
         RunMetadataStore
```

## 책임 경계

| 구성 요소 | 책임 | 하지 않는 일 |
|---|---|---|
| `AnalysisWindow` | 공통 기간 계약 | 데이터 수집 |
| `PriceFetcher` | 원본 일봉과 warm-up 수집 | 지표 해석 |
| `MarketAnalyzer` | 금융 수치 계산 | 뉴스·LLM 호출 |
| `NewsFetcher` | RSS metadata 수집·검증 | 뉴스 감성 판단 |
| `HeadlinePolicy` | 종목 관련성·명령형 공격·사건 의미 최소 검증 | 기사 진위·최종 감성 판정 |
| `NewsAnalyzer` | 필터 통과 뉴스 선택과 정성 LLM 분석 | 금융 수치 계산 |
| `ReportBuilder` | 고정 문서 구조와 escaping | 외부 데이터 호출 |
| `FinancialReportingWorkflow` | 순서와 실패 정책 | 세부 계산 재구현 |
| `RunMetadataStore` | 실행 evidence 저장 | 업무 결과 판단 |
| `FinancialResearchAgent` | 자연어를 세 도구에 routing | 직접 계산·주문 |

## 실행 상태

단계 status:

- `completed`: 정상 완료
- `completed_with_warnings`: 결과는 있지만 제한 또는 경고 존재
- `unavailable`: 선택 기능의 사용할 결과가 없음
- `failed`: 해당 단계에서 예외 발생
- `skipped`: 앞선 필수 실패로 실행하지 않음

전체 run status:

- `completed`
- `completed_with_warnings`
- `failed`

## Artifact 연결

하나의 `run_id`가 다음을 연결합니다.

- Markdown report
- news snapshot JSON
- run metadata JSON

Run metadata에는 요청 기간, 실제 가격 기간, benchmark/news/LLM 상태, fallback 여부, 단계별 latency와 오류, artifact 경로가 포함됩니다.

## 원자적 저장

Snapshot, Markdown, Run metadata, eval 결과는 같은 디렉터리에 임시 파일을 완전히 쓴 뒤 `os.replace`로 교체합니다. 기본값은 기존 파일 덮어쓰기 거부입니다.

이 방식은 개별 파일이 절반만 기록되는 위험을 줄이지만 여러 artifact를 하나의 transaction으로 묶지는 않습니다.

## Agent 도구 경계

```text
자연어 요청
   │
   ├─ 명시적 주문/추천 ──> Python policy refusal
   │
   └─ 허용 요청 ──> LLM tool selection
                      ├─ create_financial_report
                      ├─ inspect_report_run
                      └─ explain_financial_metric
```

최대 tool call 3회, 최대 turn 4회, 병렬 tool call 비활성화입니다. 주문 도구는 정의 자체가 없습니다.

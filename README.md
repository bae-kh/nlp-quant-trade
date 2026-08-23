# LLM Financial Reporting Workflow

가격 데이터와 뉴스 메타데이터를 같은 기간으로 수집하고, **금융 수치는 Python**, **비정형 뉴스 해석은 LLM**에 맡긴 뒤, 검증된 결과만 Markdown 리포트로 만드는 금융 리포팅 자동화 프로젝트입니다.

> 핵심은 LLM API 호출 자체가 아니라, deterministic software와 확률적인 LLM의 책임을 분리하고 외부 데이터·모델 실패를 validation, fallback, run tracking, eval로 통제한 것입니다.

이 저장소는 기존 자동매매 프로토타입에서 출발했지만 관련 코드를 별도 보관소로 완전히 분리했습니다. 현재 저장소에는 금융 리포팅 workflow와 이를 호출하는 제한된 Agent만 포함합니다. 구현 범위는 [프로젝트 범위](#프로젝트-범위)에서 설명합니다.

모든 결과는 연구·교육 목적이며 투자 조언, 종목 추천, 자동 주문 또는 미래 수익 보장을 제공하지 않습니다.

## 한눈에 보기

| 구분 | 구현 내용 |
|---|---|
| 공통 기간 | 미국 동부 기준, 종료일을 포함하는 달력일 `AnalysisWindow` |
| 가격 분석 | 기간 수익률, 연환산 변동성, MDD, RSI(14), MACD difference |
| Benchmark | 대상 종목과 동일한 실제 거래일의 SPY 수익률 비교 |
| 뉴스 수집 | Google News RSS 날짜 분할, 100건 포화 구간 재분할, 기간 필터, 중복 제거 |
| LLM 입력 통제 | 종목 관련성·prompt injection 필터, 날짜 균형 70% + 중요도 30% 선택 |
| LLM 출력 통제 | Structured Outputs, evidence ID·사건 의미·혼합 감성·금지 주장 검증 |
| 실패 처리 | 최대 2회 재작성, 최종 실패 시 `unavailable`, 정량 보고서는 가능한 범위에서 계속 |
| 추적 가능성 | Markdown, 뉴스 Snapshot, 단계별 Run metadata를 `run_id`로 연결 |
| 선택적 Agent | 리포트 생성·Run 조회·지표 설명 3개 도구, 주문·추천 요청 차단 |

## 검증 결과

2026-08-23 기준 현재 저장소에서 확인한 결과입니다.

| 검증 | 결과 | 해석 범위 |
|---|---:|---|
| 전체 pytest | **170 passed** | 현재 금융 리포팅 workflow, Agent, eval 전체 회귀 테스트 |
| Live 뉴스 품질 eval | **6 / 6** | `gpt-4o-mini`, 제한된 합성 headline 6개 case, 1회 실행 |
| Live Agent routing eval | **4 / 4** | 실제 모델의 도구 선택, side-effect-free stub tool 사용 |
| 실제 TSLA end-to-end | **완료** | yfinance + Google News RSS + OpenAI Responses API + artifact 저장 |

`recorded` 평가는 평가 코드와 고정 기준선의 회귀를 확인하며 현재 live 모델 품질을 증명하지 않습니다. Live eval도 모든 실제 뉴스 분포와 의미 오류를 보장하지 않으므로 사람 검토가 필요합니다.

## 문제 정의

단순한 LLM 리포트 생성에는 다음 문제가 있었습니다.

- 가격과 뉴스가 서로 다른 기간을 사용할 수 있음
- 정확해야 하는 금융 계산이 LLM 응답에 의존할 수 있음
- 긴 기간의 뉴스가 최신 기사 위주로 잘릴 수 있음
- 검색 결과의 다른 종목 뉴스와 명령형 문자열이 LLM 입력에 섞일 수 있음
- schema가 맞아도 사건 의미나 인용 근거가 틀릴 수 있음
- API 실패와 실제 중립 분석이 같은 값으로 보일 수 있음
- 결과 파일만으로 어느 단계가 실패했는지 추적하기 어려움

이 프로젝트는 이를 하나의 reporting workflow로 분해하고 각 단계의 계약과 실패 정책을 코드로 명시합니다.

## 아키텍처

```text
사용자 / 공식 CLI / 선택적 Financial Research Agent
                         │
                         ▼
                  AnalysisWindow
        ticker + inclusive calendar dates + timezone
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
        PriceFetcher             NewsFetcher
     target + SPY + warm-up   date chunk + saturation split
             │                       │
             ▼                       ▼
       MarketAnalyzer          NewsSnapshotStore
       deterministic                  │
             │                        ▼
             │                  HeadlinePolicy
             │             relevance + injection guard
             │                        │
             │                        ▼
             │                   NewsAnalyzer
             │          date balance + importance selection
             │          Structured Output + validation + retry
             │                        │
             └────────────┬───────────┘
                          ▼
                    ReportBuilder
                          │
                          ▼
       Markdown Report + News Snapshot + Run Metadata
```

가격과 뉴스는 하나의 `AnalysisWindow`를 공유합니다. Agent도 금융 계산을 직접 수행하지 않고 동일한 workflow를 도구로 호출합니다.

자세한 책임 경계와 실패 상태는 [Architecture and Failure Policy](docs/architecture.md)에서 확인할 수 있습니다.

## 핵심 설계 결정

### 1. 달력일과 거래일을 분리

`analysis_days=30`은 종료일을 포함하는 30개 **달력일**입니다.

```text
종료일: 2024-12-31
시작일: 2024-12-02
요청 기간: 30개 달력일
실제 TSLA 가격: 21개 거래일
```

가격과 뉴스는 같은 요청 기간을 사용하되, 리포트에는 휴장일을 제외한 실제 가격 범위를 별도로 표시합니다.

### 2. Warm-up은 지표 계산에만 사용

가격 수집 시 분석 시작일 이전 90개 달력일을 추가로 가져옵니다. 기간 수익률·변동성·MDD는 요청 구간만 사용하고, RSI·MACD는 이전 가격 흐름을 이어받아 분석 종료일의 최신 값을 계산합니다.

`yfinance`의 `end`가 미포함이라는 점을 처리하고, 제공자가 종료일 이후 값을 반환해도 미래 데이터가 지표에 들어가지 않도록 다시 제한합니다.

### 3. 숫자는 Python, 해석은 LLM

`MarketAnalyzer`가 다음 값을 계산합니다.

- 기간 수익률
- 일별 수익률 표준편차 기반 연환산 변동성
- 이전 고점 대비 최대 낙폭(MDD)
- RSI(14)
- MACD difference(12, 26, 9)
- 동일 실제 거래일의 SPY 수익률과 단순 차이

LLM은 이 숫자를 보거나 다시 계산하지 않습니다. 선택된 뉴스 제목 메타데이터에서 전체적인 정성 방향, 요약, 주요 이슈와 근거 ID만 생성합니다.

### 4. 뉴스 수집 범위와 LLM 입력 범위를 분리

Google News RSS는 한 요청에서 약 100건에 도달할 수 있습니다. 기본 30일 단위로 조회하고, 100건에 도달한 구간은 최소 하루까지 다시 나눕니다. 조회 실패·포화·요청 한도·기간 밖 항목·중복·잘못된 metadata 수를 각각 기록합니다.

수집 결과는 먼저 JSON Snapshot으로 보존합니다. 그 후 `HeadlinePolicy`가 다음 항목을 검사합니다.

- 제목에 ticker 또는 등록된 회사명 alias가 있는가
- 제목·출처에 `ignore previous instructions` 같은 명령형 공격 패턴이 있는가

필터를 통과한 기사가 60건을 넘으면 다음 전략으로 최대 60건을 선택합니다.

```text
날짜 균형 70% + 전 기간 중요도 30%
```

중요도는 실적, 가이던스, 인수·합병, 규제·리콜, 생산·인도량 같은 **기업 사건 우선순위**입니다. 감성 방향, 언론사 신뢰도 또는 사실 검증 점수가 아닙니다.

### 5. 형식 검증 뒤에 의미 검증을 추가

OpenAI Structured Outputs와 Pydantic schema로 다음 형식을 제한합니다.

```text
sentiment: positive | neutral | negative
score: -1.0 ~ 1.0
confidence: 0 ~ 100
summary: Korean text
key_topics: topic + explanation + supporting_article_ids
```

형식이 맞더라도 다음 위반은 Python에서 거부합니다.

- 선택되지 않은 `article_id` 인용
- `vehicle delivery report`를 `실적 보고서`로 바꾸는 사건 의미 변경
- `sales`를 근거 없이 `매출`로 확장
- 제목에 없는 미래 주가 전망과 인과관계
- 투자자 반응 추론과 매수·매도 권유
- 상반된 방향의 기사 묶음을 과도한 positive/negative로 분류

검증 실패 사유를 누적해 최대 두 번 재작성합니다. 세 번째 결과도 실패하면 정성 분석을 공개하지 않고 `validation_error`와 `unavailable`을 기록합니다.

### 6. 중립과 실패를 구분

정상적인 중립 분석:

```text
available=true
sentiment=neutral
score=0.0
```

뉴스 부재, API 키 누락, 모델 호출 또는 검증 실패:

```text
available=false
sentiment=None
score=None
fallback_used=true
error_code=...
```

분석 실패를 임의의 중립값으로 위장하지 않습니다.

### 7. 결과뿐 아니라 실행 과정도 저장

한 번의 실행에서 생성된 세 artifact를 같은 `run_id`로 연결합니다.

| Artifact | 기본 경로 | 역할 |
|---|---|---|
| Markdown | `reports/generated/report_*.md` | 사람이 읽는 최종 리포트 |
| News Snapshot | `reports/generated/news_snapshots/news_*.json` | 수집된 원본 뉴스 metadata |
| Run Metadata | `reports/generated/run_metadata/run_*.json` | 단계별 상태·시간·오류·경고·파일 경로 |

Snapshot, Markdown, Run metadata와 eval 결과는 임시 파일을 완전히 기록한 뒤 `os.replace`로 교체합니다. 기본값은 기존 파일 덮어쓰기를 거부합니다.

## 실제 실행 예시

다음 조건으로 실제 외부 데이터와 OpenAI API를 연결해 검증했습니다.

```text
Ticker: TSLA
분석 기간: 2024-12-02 ~ 2024-12-31
가격 관측: 21개 거래일
기간 수익률: 13.09%
연환산 변동성: 67.19%
MDD: -15.84%
SPY 수익률: -2.58%
뉴스: 19건 저장 → 관련성 필터 후 14건 분석
LLM 결과: neutral, score 0.01, confidence 70
검증: 첫 초안의 미근거 주가 전망을 거부하고 1회 재작성
Run status: completed_with_warnings
```

`completed_with_warnings`는 실패가 아니라 RSS coverage 한계, 제외 기사, 재작성 같은 제한을 숨기지 않은 성공 상태입니다.

공개 가능한 artifact 예시는 다음에서 확인할 수 있습니다.

- [실제 OpenAI 연결 end-to-end 리포트](reports/samples/TSLA_2024-12_live_llm_sample.md)
- [실제 외부 가격·뉴스 + missing API key fallback 리포트](reports/samples/TSLA_2024-12_no_llm_sample.md)
- [Recorded eval baseline](reports/samples/recorded_eval_baseline.md)
- [Sample artifact 설명](reports/samples/README.md)

## 빠른 시작

### 1. 환경 구성

Windows PowerShell 기준:

```powershell
git clone https://github.com/bae-kh/llm-financial-reporting-pipeline.git
cd llm-financial-reporting-pipeline

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Python 3.11 이상을 권장하며, 공개된 고정 의존성과 테스트는 Python 3.13.9에서 최종 검증했습니다.

새 메인 workflow가 실제로 요구하는 비밀값은 LLM 분석용 `OPENAI_API_KEY`입니다. 키가 없어도 가격·뉴스 수집과 정량 리포트는 실행되며 LLM 단계만 `missing_api_key`로 기록됩니다.

```dotenv
OPENAI_API_KEY=your_api_key_here
```

`.env`, DB, 원본 데이터, 생성 리포트는 Git 대상에서 제외되어 있습니다.

### 2. 재현 가능한 리포트 실행

```powershell
python .\generate_report.py --ticker TSLA --analysis-days 30 --as-of-date 2024-12-31
```

최근 미국 동부 날짜를 종료일로 사용하려면 `--as-of-date`를 생략합니다.

```powershell
python .\generate_report.py --ticker TSLA --analysis-days 30
```

시스템 날짜가 데이터 제공자가 지원하는 최신 날짜보다 앞서 있으면 가격이 없을 수 있습니다. 이때는 실제 과거 거래일이 포함되도록 `--as-of-date`를 지정합니다.

PowerShell에서 여러 줄 명령을 사용할 때 연결 문자는 역슬래시(`\`)가 아니라 백틱입니다. 가장 안전한 방법은 위 예시처럼 한 줄로 실행하는 것입니다.

### 3. 출력 경로 지정

```powershell
python .\generate_report.py --ticker TSLA --analysis-days 30 --output reports/generated/latest_TSLA.md
```

기존 경로를 명시적으로 교체할 때만 `--overwrite`를 추가합니다.

## 선택적 Financial Research Agent

Agent는 자연어 요청을 이미 검증된 application tool로 연결하는 얇은 orchestration 계층입니다. 금융 계산을 직접 수행하지 않습니다.

```powershell
python .\run_financial_agent.py "TSLA를 2024-12-31 기준 최근 30일로 분석해서 리포트를 만들어줘"
```

허용 도구:

| 도구 | 역할 |
|---|---|
| `create_financial_report` | 지정 기간의 검증된 workflow 실행 |
| `inspect_report_run` | `run_id`로 이전 실행 상태 조회 |
| `explain_financial_metric` | 프로젝트 지표의 의미와 주의점 설명 |

안전 경계:

- 주문·종목 추천 요청은 도구 실행 전에 Python policy로 차단
- 주문 도구 자체를 정의하지 않음
- 최대 tool call 3회, 최대 turn 4회
- 병렬 tool call 비활성화
- Pydantic schema로 tool argument 검증
- Agent 최종 답변의 주문·추천 표현 추가 검사

## 테스트와 평가

### 전체 자동 테스트

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

현재 결과:

```text
170 passed
```

테스트는 실제 yfinance·OpenAI 호출 대신 fake provider와 stub client를 주입해 반복 가능하게 실행합니다. 주요 범위는 다음과 같습니다.

- 달력일 포함 규칙과 미국 동부 기준일
- yfinance `end` 미포함, warm-up, 미래 데이터 제외
- 알려진 수익률·변동성·MDD 공식
- 대상 종목 필수 실패와 선택적 SPY 실패 분리
- RSS timeout·network·XML·빈 결과 상태 구분
- 100건 포화 구간 재분할과 요청 안전 한도
- 기간 필터, 중복 제거, 원자적 Snapshot 저장
- ticker·회사명 관련성 및 prompt injection 필터
- 날짜 균형·사건 중요도 결합 선택
- LLM schema·evidence ID·사건 의미·금지 주장 검증
- 검증 실패 재작성과 최종 fail-closed
- 실제 중립과 `unavailable` fallback 구분
- Markdown escaping, 원자적 저장, 덮어쓰기 정책
- 단계별 상태·latency·error와 downstream `skipped`
- Agent tool loop, tool 한도, 주문·추천 거절
- 뉴스 품질 및 Agent routing grader

### 비용 없는 recorded 회귀 평가

```powershell
python .\evaluate_news_quality.py --mode recorded
python .\evaluate_agent_tools.py --mode recorded
```

### 실제 모델 평가

```powershell
python .\evaluate_news_quality.py --mode live --model gpt-4o-mini
python .\evaluate_agent_tools.py --mode live --model gpt-4o-mini
```

Live 모드는 OpenAI API 호출 비용이 발생할 수 있습니다. Agent live eval은 실제 모델의 routing을 검사하되 파일 생성 같은 부작용을 막기 위해 stub tool을 사용합니다.

뉴스 eval case:

1. 실적 개선과 가이던스 상향
2. 제품 리콜과 규제 조사
3. 긍정·부정 방향 혼합
4. headline prompt injection
5. 다른 종목 검색 노이즈
6. 차량 인도량과 실적 의미 구분

## 실패 정책

| 실패 지점 | 전체 결과 | 기록 방식 |
|---|---|---|
| 대상 종목 가격 수집·시장 분석 | 실패 | failed run metadata, downstream `skipped` |
| SPY 수집 | 계속 | `benchmark_status=unavailable` 또는 `insufficient_data` |
| 뉴스 수집 | 계속 | `news_status=unavailable`, 정량 리포트 유지 |
| 뉴스 Snapshot 저장 | 계속 | snapshot stage `failed`, 경고와 null path |
| API key 누락 | 계속 | `missing_api_key`, LLM 결과 N/A |
| LLM 호출 실패 | 계속 | `llm_error`, LLM 결과 N/A |
| LLM 검증 최종 실패 | 계속 | `validation_error`, 잘못된 정성 결과 비공개 |
| Markdown 구성·저장 | 실패 | failed run metadata |
| Run metadata 저장 | 실패 | 실행 추적 필수 조건으로 전체 실패 |

## 주요 코드

| 파일 | 책임 |
|---|---|
| `workflow/analysis_window.py` | 가격과 뉴스가 공유하는 포함 달력일 계약 |
| `data_pipeline/price_fetcher.py` | yfinance 원본 일봉과 지표 warm-up 수집 |
| `analysis/market_analyzer.py` | 결정론적 시장 지표 계산 |
| `workflow/reporting_pipeline.py` | 필수 target·선택 SPY orchestration |
| `data_pipeline/news_fetcher.py` | 구간 분할 RSS 수집, 검증, 기간 필터, 중복 제거 |
| `workflow/news_snapshot.py` | 수집 뉴스 JSON 원자적 저장 |
| `analysis/headline_policy.py` | 종목 관련성, 명령형 입력, 방향 힌트, 사건 의미 policy |
| `analysis/news_analyzer.py` | 기사 선택, Structured Output, 검증과 재작성 |
| `report/report_builder.py` | 검증 결과를 고정 Markdown 구조로 조립 |
| `workflow/report_artifact.py` | Markdown 원자적 저장과 덮어쓰기 통제 |
| `workflow/financial_reporting_workflow.py` | 전체 단계와 필수·선택 실패 정책 |
| `workflow/run_tracking.py` | run_id, 단계별 상태·시간·오류·artifact JSON |
| `generate_report.py` | 공식 금융 리포팅 CLI |
| `agent/financial_research_agent.py` | 제한된 3-tool Agent orchestration |
| `evaluation/news_quality_eval.py` | 뉴스 정성 결과 자동 grader와 artifact 저장 |
| `evaluation/agent_tool_eval.py` | Agent exact tool routing grader |

## 프로젝트 범위

이 저장소의 공식 포트폴리오 범위:

- `generate_report.py`
- `workflow/`
- `analysis/`
- 구조화된 `data_pipeline/` 경로
- `report/report_builder.py`
- `agent/`
- `evaluation/`, `evals/`

다음 기능은 의도적으로 포함하지 않습니다.

- 자동 주문과 증권사 API 연동
- 매수·매도 추천과 거래 전략
- 백테스트·포트폴리오 수익 시뮬레이션
- Streamlit 거래 dashboard, 거래 DB, Telegram 알림

전환 전 실험은 이 저장소 밖의 별도 보관소로 이동했습니다. 현재 소스와 테스트에는 해당 모듈의 import나 호환 실행 경로가 없습니다.

## 현재 한계

- Google News RSS의 전체 언론사 coverage와 완전 수집을 보장하지 않음
- 기사 본문이 아닌 headline·발행 시각·출처 metadata만 분석
- 의미가 비슷하지만 제목이 다른 기사는 중복으로 남을 수 있음
- 등록되지 않은 회사 alias만 포함된 관련 기사는 보수적 필터에서 제외될 수 있음
- 중요도와 방향 힌트는 키워드 규칙이며 기사 진위·언론사 신뢰도를 평가하지 않음
- 30일보다 긴 기간도 하나의 종합 감성으로 압축하므로 시간에 따른 방향 변화가 줄어듦
- Structured Outputs와 deterministic guardrail도 모든 의미·번역 오류를 보장하지 않음
- 인증, 사용자 권한, 스케줄러, 중앙 로그, Kubernetes 배포는 구현 범위가 아님
- Run metadata는 로컬 JSON이며 분산 tracing system이 아님

자세한 내용은 [Limitations and Disclaimer](docs/limitations.md)를 참고하세요.

## 이 프로젝트가 보여주는 것

- 반복 업무를 수집·분석·검증·보고 단계로 구조화하는 능력
- 정확한 계산과 생성형 모델의 책임 경계를 설정하는 능력
- 외부 API와 LLM의 실패를 정상값으로 숨기지 않는 설계
- 실제 모델 오류를 eval과 사람 검토로 발견하고 guardrail로 보완한 과정
- 결과뿐 아니라 근거와 실행 상태를 artifact로 남기는 관측 가능성
- 이미 검증된 workflow 위에 최소 권한 Agent를 추가하는 방식

프로젝트를 본인 말로 학습하기 위한 설명은 [Project Understanding Guide](docs/UNDERSTANDING_GUIDE.md)에서 확인할 수 있습니다.

## Disclaimer

이 프로젝트는 연구·교육 목적의 금융 데이터 리포팅 자동화 예제입니다. 출력은 투자 자문, 종목 추천, 매수·매도 신호 또는 미래 성과 보장을 제공하지 않습니다.

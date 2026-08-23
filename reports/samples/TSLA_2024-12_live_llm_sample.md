# TSLA 금융 분석 리포트

> 실제 외부 가격·뉴스와 OpenAI Responses API를 연결한 1회 실행의 공개용 사본입니다. 모델 응답은 실행마다 달라질 수 있으며 투자 조언이 아닙니다.

## 1. 분석 개요

| 항목 | 값 |
|---|---|
| Run ID | `run_20260823T064250_187352Z_TSLA_b25cb9f2` |
| 종목 | TSLA |
| 요청 분석 기간 | 2024-12-02 ~ 2024-12-31 (30개 달력일, 양 끝 포함) |
| 기준 시간대 | America/New_York |
| 실제 가격 범위 | 2024-12-02 ~ 2024-12-31 (21개 거래일) |

## 2. 정량 시장 지표

| 지표 | 값 | 계산 주체 |
|---|---:|---|
| 기간 수익률 | 13.09% | Python |
| 연환산 변동성 | 67.19% | Python |
| 최대 낙폭(MDD) | -15.84% | Python |
| RSI(14) | 51.00 | Python |
| MACD difference | -7.0665 | Python |
| 기술지표 상태 | available | Python |

## 3. Benchmark 비교

| 항목 | 값 |
|---|---:|
| Benchmark | SPY |
| 상태 | available |
| Benchmark 수익률 | -2.58% |
| 대상 종목 대비 차이 | 15.67% |

## 4. 뉴스 수집 상태

| 항목 | 값 |
|---|---:|
| 수집 상태 | available |
| 날짜 구간 조회 상태 | complete |
| RSS 요청 수 | 1 |
| 원본 RSS 항목 | 20 |
| 기간 내 항목 | 19 |
| 중복 제외 | 0 |
| 저장 기사 | 19 |
| Source coverage | unknown |

## 5. LLM 뉴스 분석

| 항목 | 값 |
|---|---:|
| 분석 상태 | available |
| 감성 | neutral |
| 감성 점수 | 0.01 |
| 신뢰도 | 70% |
| 입력/선택/분석 기사 | 19 / 14 / 14 |
| 선택 전략 | all |
| 모델 | gpt-4o-mini |

### 뉴스 요약

Tesla의 주가 관련 뉴스에는 긍정적인 전망과 부정적인 판매 우려가 함께 제시되어 전반적인 방향이 혼재되어 있습니다.

### 주요 이슈와 근거

- **주가 전망 보도**: Tesla 주식이 700달러에 도달할 수 있다는 전망 제목이 확인됐습니다.
  - `news_90580201d7467aa3` — Stock Forecast: Here’s How Tesla (TSLA) Stock Gets to $700 Per Share - 24/7 Wall St. (24/7 Wall St., 2024-12-03)
- **주가 최고점 관련 보도**: 3년 고점과 사상 최고가에 관한 제목이 여러 매체에서 확인됐습니다.
  - `news_e694296dfc9fb9a8` — Tesla Stock Hits Three-Year High as Morgan Stanley Lifts Price Target - Investopedia (Investopedia, 2024-12-10)
  - `news_6352a6efb563701b` — Tesla Stock Surges to First All-Time High in 3 Years - Investopedia (Investopedia, 2024-12-11)
  - `news_98e9da48d60a85dd` — Tesla Shares Jump to Record Amid $515 Billion Rally in Six Weeks - Bloomberg.com (Bloomberg.com, 2024-12-11)
  - `news_7a993f3f8b5a487b` — Tesla stock's post-election boom just powered the shares to an all-time high - qz.com (qz.com, 2024-12-12)
- **차량 인도량 보고서**: 4분기 차량 인도량 보고서를 앞둔 주가 하락 제목이 확인됐습니다.
  - `news_9baf8edc1760d9e6` — Tesla Stock Slips Further Ahead of EV Maker's Q4 Delivery Report - Investopedia (Investopedia, 2024-12-31)
- **연간 판매 우려**: 연간 판매 감소 가능성을 다룬 제목이 확인됐습니다.
  - `news_d1f7fcb83f71b1d4` — Tesla (TSLA) Stock Surge Runs Up Against a Potential Annual Sales Drop - Bloomberg.com (Bloomberg.com, 2024-12-31)

## 6. 데이터 한계 및 경고

- 분석 기간 밖의 RSS 항목 1건을 제외했습니다.
- Google News RSS의 전체 언론사 수집 범위는 알 수 없습니다.
- 종목 식별자 또는 회사명과 직접 연결되지 않은 뉴스 5건을 LLM 입력에서 제외했습니다.
- LLM 초안이 결정론적 검증을 통과하지 못해 1회 재작성한 결과를 사용했습니다.
- 뉴스 분석은 기사 본문이 아니라 제공된 제목 metadata를 사용합니다.
- 중요도 점수는 기사 선택 우선순위이며 감성, 신뢰도 또는 사실 검증 점수가 아닙니다.
- 금융 수치는 Python이 계산하며 LLM은 해당 수치를 다시 계산하지 않습니다.

## 7. 면책 조항

이 리포트는 연구·교육 목적의 자동 생성 결과입니다. 투자 추천, 매수·매도 신호 또는 미래 성과 보장을 제공하지 않습니다.

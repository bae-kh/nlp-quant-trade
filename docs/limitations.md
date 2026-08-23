# Limitations and Disclaimer

## 핵심 면책

이 프로젝트는 연구·교육 목적의 금융 데이터 리포팅 자동화 예제입니다. 투자 자문, 종목 추천, 자동 주문, 미래 수익 보장을 제공하지 않습니다.

## 데이터 한계

- yfinance와 Google News RSS는 외부 비공식·공개 제공자 상태와 정책에 영향을 받습니다.
- 휴장일에는 요청 시작일과 실제 첫 가격일이 다를 수 있습니다.
- Google News RSS의 전체 언론사 coverage는 알 수 없습니다.
- 뉴스는 기사 본문이 아니라 headline, 발행 시각, 출처 metadata만 사용합니다.
- 제목이 다르지만 의미가 같은 기사는 중복으로 남을 수 있습니다.
- ticker 검색어가 만든 관련성 낮은 기사는 제목의 ticker·회사명 alias 규칙으로 제외하지만, alias 목록에 없는 관련 기사를 놓칠 수 있습니다.

## 정량 지표 한계

- 기간 수익률, 변동성, MDD는 선택한 분석 구간에 따라 달라집니다.
- RSI와 MACD는 과거 가격 기반 보조 지표이며 미래 방향을 예측하지 않습니다.
- SPY 차이는 단순 같은 기간 수익률 차이이며 위험 조정 alpha가 아닙니다.
- 데이터가 부족하면 일부 기술지표는 `insufficient_history`가 될 수 있습니다.

## LLM 한계

- Structured Outputs는 형식을 통제하지만 내용의 사실성을 자동 보장하지 않습니다.
- article ID 검증은 “제공된 근거를 인용했는가”를 확인하며 기사 자체가 사실인지 확인하지 않습니다.
- 사건 유형 검증은 `delivery report`를 `실적 보고서`로 바꾸는 등 알려진 의미 변경을 막지만 모든 번역·요약 오류를 판별하지는 못합니다.
- 결정론적 검증 실패 시 재작성은 최대 두 번만 허용하며, 세 번째도 실패하면 `validation_error`로 정성 분석을 공개하지 않습니다.
- 명령형 prompt injection 패턴은 LLM 입력 전에 제외하지만 알려지지 않은 우회 표현까지 모두 차단한다고 보장하지 않습니다.
- 감성은 headline에 대한 정성 분류이며 실제 주가 방향이나 사건 결과가 아닙니다.
- 동일 입력도 모델 버전과 서비스 상태에 따라 다르게 해석될 수 있습니다.
- prompt injection 방어 규칙과 eval이 있어도 모든 공격을 차단한다고 주장하지 않습니다.

## 평가 한계

- recorded fixture 평가는 평가 코드와 기준선의 재현성을 확인합니다.
- recorded 전체 통과 결과는 live OpenAI 모델 정확도가 아닙니다.
- 합성 case는 실제 뉴스 분포와 모든 언어·사건을 대표하지 않습니다.
- 자동 grader는 감성 방향, 필수·제외 근거 ID, 필수·금지 표현, unsupported 숫자를 검사하지만 요약의 모든 의미 품질을 판단하지 않습니다.
- live 모델 비교에는 반복 실행과 사람 검토가 추가로 필요합니다.

## Agent 한계와 안전 경계

- Agent는 `create_financial_report`, `inspect_report_run`, `explain_financial_metric` 세 도구만 사용합니다.
- 한 실행에서 tool call은 최대 3회이며 병렬 tool call을 사용하지 않습니다.
- 매수·매도 주문과 종목 추천 요청은 도구 실행 전에 차단합니다.
- 자연어 routing은 LLM에 의존하므로 live tool-selection eval이 필요합니다.
- Agent는 배포된 서비스가 아니라 로컬 포트폴리오 인터페이스입니다.

## 운영 한계

- 현재 인증, 사용자별 권한, 중앙 로그 수집, 스케줄러, 웹 UI는 새 workflow에 연결하지 않았습니다.
- Run metadata는 로컬 JSON이며 분산 tracing 시스템이 아닙니다.
- 원자적 파일 저장은 한 파일의 부분 쓰기를 방지하지만 여러 artifact 전체의 transaction을 보장하지 않습니다.
- OpenAI API 키와 호출 비용은 실행 사용자가 관리해야 합니다.

## 제품 범위 경계

자동 주문, 증권사 API, 매수·매도 추천, 백테스트, 거래 dashboard는 구현 범위가 아닙니다. 공개 저장소에는 금융 리포팅 workflow와 이를 호출하는 제한된 Agent만 포함합니다.

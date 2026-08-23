# Sample Artifacts

## TSLA live LLM sample

`TSLA_2024-12_live_llm_sample.md`는 실제 yfinance, SPY, Google News RSS, OpenAI Responses API를 연결한 1회 실행의 공개용 사본입니다. 첫 LLM 초안의 미근거 주가 전망을 deterministic validator가 거부했고, 1회 재작성 후 검증된 정성 결과가 반영됐습니다.

모델 응답은 실행마다 달라질 수 있으며 이 한 번의 결과가 일반적인 정확도를 보장하지 않습니다.

## TSLA no-LLM smoke sample

`TSLA_2024-12_no_llm_sample.md`는 다음 명령으로 실제 가격·SPY·Google News RSS를 수집해 확인한 보고서의 공개용 사본입니다.

```powershell
python .\generate_report.py --ticker TSLA --analysis-days 30 --as-of-date 2024-12-31
```

검증 시점에는 `OPENAI_API_KEY`를 비워 유료 호출을 하지 않았습니다. 따라서 LLM 상태가 `missing_api_key`이며, 실패를 neutral로 바꾸지 않고 정량 보고서가 계속 생성되는 동작을 보여줍니다.

## Recorded eval baseline

`recorded_eval_baseline.md`는 뉴스 품질 grader와 Agent routing grader의 고정 응답 회귀 기준선입니다. 뉴스 6/6과 Agent 4/4 결과는 평가 코드와 기준선의 재현성을 뜻하며 live 모델 정확도가 아닙니다.

import streamlit as st
import pandas as pd
from data_pipeline.price_fetcher import PriceFetcher
from backtest.runner import run_backtest

st.set_page_config(page_title="NLP Quant Dashboard", layout="wide")

st.title("📈 NLP Momentum Quant Dashboard")
st.markdown("미국 주식(US Equity) 및 NLP 감성 분석(LLM) 기반의 하이브리드 백테스트 시스템")

# 1. Sidebar 설정
st.sidebar.header("⚙️ Settings")
ticker = st.sidebar.text_input("Ticker", value="TSLA").upper()
sma_period = st.sidebar.slider("SMA Period", min_value=5, max_value=60, value=20, step=1)
use_local_llm = st.sidebar.toggle("Use Local LLM (Ollama)", value=False, help="로컬 4070 Ti 인프라를 활용하여 llama3 추론을 사용합니다.")

# 2. 메인 화면 - 주가 차트 미리보기
st.subheader(f"{ticker} - 주가 동향 (최근 1년)")
try:
    fetcher = PriceFetcher()
    df_price = fetcher.get_daily_data(ticker)
    if not df_price.empty:
        st.line_chart(df_price['Close'])
    else:
        st.warning("주가 데이터를 불러오지 못했습니다. 티커를 확인해주세요.")
except Exception as e:
    st.error(f"데이터 로드 중 에러 발생: {e}")

# 3. 백테스트 실행 패널
st.subheader("🛠️ 백테스트 모의 실행")
if st.button("Run Backtest"):
    with st.spinner(f"Running backtest for {ticker}... (SMA: {sma_period})"):
        df_result, total_return, mdd, alpha, total_fees = run_backtest(ticker=ticker, sma_period=sma_period, use_local_llm=use_local_llm)
        
        if df_result is not None and not df_result.empty:
            st.success("백테스트 세션이 완료되었습니다.")
            
            # 메트릭스(Metrics) 표시
            col1, col2, col3 = st.columns(3)
            col1.metric("최종 누적 수익률 (Net)", f"{total_return:.2f}%")
            col2.metric("벤치마크 대비 초과 수익 (Alpha)", f"{alpha:.2f}%")
            col3.metric("총 발생 수수료 (Total Fees Paid)", f"{total_fees:.2f}%")
            
            # 누적 수익률 비교 차트 (Comparison Chart)
            st.markdown("### 📊 누적 수익률 비교 (Strategy Net vs Buy & Hold)")
            # 차트에 표현할 수 있도록 단위 조정 (1 기준 -> 백분율)
            chart_df = pd.DataFrame({
                'Net Strategy Return': (df_result['Cumulative_Return'] - 1) * 100,
                'Buy & Hold Return': (df_result['BnH_Cumulative_Return'] - 1) * 100
            })
            
            # 인덱스(Index) 문자열 변환 처리
            if isinstance(chart_df.index, pd.DatetimeIndex):
                chart_df.index = chart_df.index.strftime('%Y-%m-%d')
            else:
                chart_df.index = chart_df.index.astype(str)
                
            st.line_chart(chart_df)
            
            # 신호 발생 기록 테이블 표시
            st.markdown("### 매매 시그널 발생 리스트")
            # BUY 또는 SELL 이 발생한 날짜의 데이터만 필터링
            df_signals = df_result[df_result['Signal'].isin(['BUY', 'SELL'])]
            
            if not df_signals.empty:
                # 보여줄 열(Column) 필터링
                # 보여줄 열(Column) 필터링 (방어적 로직 적용)
                # 데이터프레임에 실제로 존재하는 열(Column)만 교집합으로 안전하게 추출합니다.
                target_cols = ['Close', 'SMA20', 'SMA', 'Sentiment', 'Signal']
                display_cols = [col for col in target_cols if col in df_signals.columns]
                
                df_display = df_signals[display_cols].copy()
                # 날짜 인덱스를 보기 좋게 문자열로
                
                if isinstance(df_display.index, pd.DatetimeIndex):
                    df_display.index = df_display.index.strftime('%Y-%m-%d')
                else:
                    # 이미 문자열이거나 다른 타입이라면 강제로 문자열로 변환만 수행
                    df_display.index = df_display.index.astype(str)

                st.dataframe(df_display, use_container_width=True)
            else:
                st.info("해당 기간 동안 매수/매도 시그널이 발생하지 않았습니다.")
        else:
            st.error("백테스트 연산에 실패했습니다.")

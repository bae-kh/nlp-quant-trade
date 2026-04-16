# 실행 전 필수 요건: pip install streamlit plotly pandas yfinance
import streamlit as st
import sqlite3
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta

# 페이지 기본 설정
st.set_page_config(page_title="자동 매매 퀀트 대시보드", layout="wide")

DB_PATH = "quant_trade.db"

def load_data():
    """SQLite 데이터베이스에서 거래 기록을 최신순으로 읽어옵니다."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            query = "SELECT * FROM trade_history ORDER BY timestamp DESC"
            df = pd.read_sql(query, conn)
            # timestamp의 경우 문자열이므로 datetime 객체로 변환
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
    except Exception as e:
        st.error(f"DB 로드 중 에러 발생: {e}")
        return pd.DataFrame()

def load_price_data(ticker="TSLA", days=30):
    """지정된 기간 동안의 종가(Close) 데이터를 yfinance에서 가져옵니다."""
    end_date = datetime.today() + timedelta(days=1)
    start_date = end_date - timedelta(days=days)
    df = yf.download(ticker, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), progress=False)
    
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    if not df.empty:
        # 인덱스(날짜)를 컬럼으로 빼기
        df.reset_index(inplace=True)
        # Date 컬럼을 timezone 나이브하게 변경하여 병합 시 오류 방지
        df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
    return df

st.title("📈 NLP 하이브리드 퀀트 트레이딩 대시보드")
st.markdown("자연어 감성 분석(LLM)과 기술적 지표(RSI, MACD)가 결합된 자율주행 매매 시스템의 실시간 현황입니다.")

# 새로고침 버튼 (st.empty()를 활용한 실시간 반영 트릭 대체)
col1, col2 = st.columns([9, 1])
with col2:
    if st.button("🔄 새로고침"):
        st.rerun()

# 1. 데이터 로드
trade_df = load_data()
price_df = load_price_data("TSLA", 30)

# 2. Metric Cards (상단 지표)
st.subheader("📊 시스템 요약 지표")
if not trade_df.empty:
    latest_trade = trade_df.iloc[0]
    
    # 지표 계산용
    current_holdings = latest_trade['quantity'] if latest_trade['action'] != 'HOLD' else 0
    # DB만으로는 현금 예수금을 확인할 수 없으므로, 평가 금액(수량 * 최신 주가)으로 대체
    latest_price = price_df['Close'].iloc[-1] if not price_df.empty else latest_trade['price']
    total_asset_est = current_holdings * latest_price
    
    latest_roi = latest_trade['roi']
    latest_rsi = latest_trade['rsi']
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(label="현재 총 주식 자산가치 (TSLA)", value=f"${total_asset_est:,.2f}")
    m2.metric(label="최신 누적 포지션 수익률", value=f"{latest_roi:.2f}%")
    m3.metric(label="현재 보유 수량", value=f"{current_holdings}주")
    m4.metric(label="최신 RSI (14)", value=f"{latest_rsi:.2f}")
else:
    st.info("아직 데이터베이스에 첫 거래(혹은 관망) 로깅이 쌓이지 않았습니다.")

# 3. Price & Signal Chart (중단 시각화)
st.subheader("📉 최근 30일 주가 흐름 및 매매 시그널")
if not price_df.empty:
    fig = go.Figure()

    # (1) 기본 주가 선도표 (Price Line)
    fig.add_trace(go.Scatter(
        x=price_df['Date'], y=price_df['Close'],
        mode='lines',
        name='TSLA Close Price',
        line=dict(color='#1E90FF', width=2)
    ))

    # (2) 매매 시그널 오버레이 (BUY / SELL Markers)
    if not trade_df.empty:
        # 성공적으로 체결된 주문만 차트에 마킹
        success_trades = trade_df[trade_df['status'] == 'SUCCESS']
        
        buys = success_trades[success_trades['action'] == 'BUY']
        sells = success_trades[success_trades['action'] == 'SELL']

        # 날짜 단위 매핑을 위해 trade_history의 timestamp에서 날짜만 추출
        buys_dates = pd.to_datetime(buys['timestamp']).dt.normalize()
        sells_dates = pd.to_datetime(sells['timestamp']).dt.normalize()

        # DB의 price 사용
        fig.add_trace(go.Scatter(
            x=buys_dates,
            y=buys['price'],
            mode='markers',
            name='BUY Signal',
            marker=dict(symbol='triangle-up', color='green', size=15),
            text=buys['llm_reasoning'],
            hovertemplate="<b>BUY</b><br>Price: $%{y}<br>Reason: %{text}<extra></extra>"
        ))

        fig.add_trace(go.Scatter(
            x=sells_dates,
            y=sells['price'],
            mode='markers',
            name='SELL Signal',
            marker=dict(symbol='triangle-down', color='red', size=15),
            text=sells['llm_reasoning'],
            hovertemplate="<b>SELL</b><br>Price: $%{y}<br>Reason: %{text}<extra></extra>"
        ))

    fig.update_layout(
        template='plotly_white',
        xaxis_title='Date',
        yaxis_title='Price (USD)',
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("YFinance 서버와 통신할 수 없어 차트를 그릴 수 없습니다.")

# 4. 하단 Trade Logs 데이터 프레임
st.subheader("🗄️ 백엔드 시스템 로깅 (DB 테이블)")
if not trade_df.empty:
    # 사용자 친화적인 순서와 포맷으로 출력
    display_df = trade_df.copy()
    display_df = display_df[['timestamp', 'action', 'status', 'price', 'quantity', 'roi', 'rsi', 'macd', 'llm_score', 'llm_reasoning']]
    st.dataframe(display_df, use_container_width=True, height=400)
else:
    st.write("DB 테이블이 비어 있습니다.")

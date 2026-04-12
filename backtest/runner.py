# file path: backtest/runner.py
import os
import sys
import numpy as np
import pandas as pd
import logging

# 상위 폴더인 루트 디렉토리를 패스에 추가하여 import 에러 방지
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_pipeline.price_fetcher import PriceFetcher
from strategies.nlp_momentum import NLPMomentumStrategy

# 로깅은 콘솔 출력을 간결하게 하기 위해 WARNING 레벨로 격상
logging.basicConfig(level=logging.WARNING, format='%(name)s - %(levelname)s - %(message)s')

def run_backtest(ticker: str, sma_period: int = 20, use_local_llm: bool = False):
    print(f"[{ticker}] 백테스트 시뮬레이션을 시작합니다. ⏳ (Loading...)")
    
    # 1. 과거 데이터 다운로드 (yfinance 활용, TSLA 2020~2023)
    fetcher = PriceFetcher()
    df = fetcher.get_daily_data(ticker)
    
    if df.empty:
        print("❌ [오류] 데이터 프레임이 비어 있어 백테스트를 진행할 수 없습니다.")
        return None, 0.0, 0.0
        
    # 2. 배치 연산된 로컬 감성 데이터베이스 연동 및 병합
    try:
        sentiment_df = pd.read_csv("tesla_sentiment_db.csv")
        sentiment_df['Date'] = pd.to_datetime(sentiment_df['Date']).dt.date
        df.index = pd.to_datetime(df.index).date
        
        # Merge를 위해 Index 세팅
        sentiment_df.set_index('Date', inplace=True)
        sentiment_grouped = sentiment_df.groupby(level=0).agg({'Sentiment': 'mean', 'Confidence': 'mean'})
        
        # [퀀트 핵심 로직 1] Outer Join 으로 병합하여 주말/휴일 등 빈 날짜 데이터 프레임 생성
        merged_df = df.join(sentiment_grouped, how='outer')
        
        # [퀀트 핵심 로직 2] 주식 시장이 닫히는 주말/휴일에 쏟아진 뉴스 점수가 유실되지 않도록, 
        # ffill()(Forward Fill)을 사용해 금/토/일요일의 최신 뉴스 센티먼트 및 확신도를 월요일 개장 시점으로 밀어넣어 반영
        merged_df['Sentiment'] = merged_df['Sentiment'].ffill()
        merged_df['Confidence'] = merged_df['Confidence'].ffill()
        
        # 장이 열리지 않아 Close 주가가 없는 휴일 Row 다시 걷어내기
        merged_df = merged_df.dropna(subset=['Close'])
        df = merged_df
        
        # 남은 결측치(초기 데이터가 비어있는 구간 등)는 중립으로
        df['Sentiment'] = df['Sentiment'].fillna(0.0)
        df['Confidence'] = df['Confidence'].fillna(0.0)
        print(f"[{ticker}] 테슬라 로컬 감성 데이터베이스 병합 및 주말 데이터 얼라인먼트 완료.")
        
    except FileNotFoundError:
        print("❌ [경고] tesla_sentiment_db.csv가 없습니다. 기본 중립값(0.0)으로 진행합니다.")
        df['Sentiment'] = 0.0
        df['Confidence'] = 0.0
    
    # 3. 전략 객체 인스턴스화
    strategy = NLPMomentumStrategy(sma_period=sma_period, sentiment_buy_threshold=0.2, sentiment_sell_threshold=-0.2)
    signals = []
    
    # 4. DataFrame 순회 루프 (백테스팅 코어 로직 & Look-ahead 바이어스 원천 차단)
    for i in range(len(df)):
        # 현재 행(시점)까지의 데이터 슬라이스만 전달하여 미래 데이터 누수 차단
        current_slice = df.iloc[:i+1] 
        current_sentiment = df['Sentiment'].iloc[i]
        current_confidence = df['Confidence'].iloc[i]
        
        # 전략 모듈에 판단 위임 (If Statement 캡슐화)
        signal = strategy.generate_signal(current_slice, current_sentiment, current_confidence)
        signals.append(signal)
        
    df['Signal'] = signals
    
    # 5. 매매에 따른 포지션 트래킹 (단순 Long-Only 가우스 알고리즘)
    position = 0 # 0: 현금 보유, 1: 주식 Long 포지션
    positions = []
    
    for sig in df['Signal']:
        if sig == 'BUY':
            position = 1
        elif sig == 'SELL':
            position = 0
        # 'HOLD'일 경우 직전 상태(현금 or 주식)를 그대로 유지
        positions.append(position)
        
    df['Position'] = positions
    
    # 다음 날의 가격 변동(Daily Return)을 온전히 누리기 위해서는 전날에 산 포지션을 적용해야 함
    df['Position'] = df['Position'].shift(1).fillna(0)
    
    # [새로운 퀀트 로직 1] 수수료 계산 (Transaction Fee 반영)
    from config.settings import Settings
    settings = Settings()
    fee_rate = settings.TRANSACTION_FEE # 0.001 (0.1%)
    
    # 포지션이 변경된 날(0->1 매수, 1->0 매도) 수수료 부과
    df['Trade_Flag'] = (df['Position'] != df['Position'].shift(1)).astype(int)
    df.loc[df.index[0], 'Trade_Flag'] = 0.0
    df['Fee'] = df['Trade_Flag'] * fee_rate
    
    # 6. 수익률 (Cumulative Return) 및 최대 낙폭 (MDD) 연산
    df['Daily_Return'] = df['Close'].pct_change().fillna(0.0)             # 일일 변동률
    df['Gross_Strategy_Return'] = df['Position'] * df['Daily_Return']     # 수수료 차감 전 수익률
    
    # 수수료 차감 순수익률 (Net Strategy Return) 계산
    df['Net_Strategy_Return'] = df['Gross_Strategy_Return'] - df['Fee']
    df['Cumulative_Return'] = (1 + df['Net_Strategy_Return']).cumprod()   # 단리가 아닌 복리(Cumprod) 연산
    
    # [새로운 퀀트 로직 2] 벤치마크 수익률 비교를 위한 Buy_and_Hold_Return 연산
    df['BnH_Cumulative_Return'] = (1 + df['Daily_Return']).cumprod()
    
    # MDD (최고점 대비 최대 하락폭) 도출
    df['Max_Return'] = df['Cumulative_Return'].cummax()
    df['Drawdown'] = (df['Cumulative_Return'] / df['Max_Return']) - 1
    mdd = df['Drawdown'].min()
    
    net_return_pct = (df['Cumulative_Return'].iloc[-1] - 1) * 100
    bnh_return_pct = (df['BnH_Cumulative_Return'].iloc[-1] - 1) * 100
    alpha_pct = net_return_pct - bnh_return_pct
    mdd_pct = mdd * 100
    total_fees_paid_pct = df['Fee'].sum() * 100
    
    print("\n" + "=" * 50)
    print(f"🚀 [{ticker}] 1-Year NLP Quant Simulation Results")
    print("=" * 50)
    print(f"📈 벤치마크(BnH Cumulative) : {bnh_return_pct:>7.2f}%")
    print(f"💰 최종 누적 순수익률(Net)  : {net_return_pct:>7.2f}%")
    print(f"🌟 초과 수익률(Alpha)       : {alpha_pct:>7.2f}%")
    print(f"💸 총 비용(Total Fees)     : {total_fees_paid_pct:>7.2f}%")
    print(f"📉 최대 낙폭(MDD)           : {mdd_pct:>7.2f}%")
    print("=" * 50 + "\n")
    
    return df, net_return_pct, mdd_pct, alpha_pct, total_fees_paid_pct

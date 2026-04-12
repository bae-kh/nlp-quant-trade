# file path: test_strategy.py
import pandas as pd
import numpy as np
from strategies.nlp_momentum import NLPMomentumStrategy

def main():
    print("=== NLP Momentum Strategy 유닛 테스트 시작 ===")
    
    # 전략 초기화 (SMA 20, Buy >= 0.2, Sell <= -0.2)
    strategy = NLPMomentumStrategy(sma_period=20, sentiment_buy_threshold=0.2, sentiment_sell_threshold=-0.2)
    
    # 가상의 주가 데이터 생성 규칙
    # Case 1 & 2용 (상승 추세): 주가가 150(일괄)이다가 최신 종가가 160으로 오름 => SMA20=(150*19+160)/20 = 150.5 (종가160 > SMA150.5)
    dates = pd.date_range(start="2026-04-01", periods=20, freq="D")
    rising_prices = [150] * 19 + [160] 
    rising_df = pd.DataFrame({'Close': rising_prices}, index=dates)
    
    # Case 3용 (하락 추세): 주가가 150(일괄)이다가 최신 종가가 140으로 내림 => SMA20=(150*19+140)/20 = 149.5 (종가140 < SMA149.5)
    falling_prices = [150] * 19 + [140]
    falling_df = pd.DataFrame({'Close': falling_prices}, index=dates)

    print("\n[Case 1] (강한 매수)")
    print("- 상황: 주가 상승 추세 (Price > SMA20) + 확실한 호재 (Sentiment: 0.8)")
    signal_1 = strategy.generate_signal(rising_df, sentiment_score=0.8)
    print(f"👉 예상: BUY | 실제 도출 결과: {signal_1}")
    
    print("\n[Case 2] (가짜 반등 방어 - Whipsaw 필터링)")
    print("- 상황: 주가 상승 추세 (Price > SMA20) + 명확한 악재 (Sentiment: -0.5)")
    signal_2 = strategy.generate_signal(rising_df, sentiment_score=-0.5)
    print(f"👉 예상: SELL | 실제 도출 결과: {signal_2}")

    print("\n[Case 3] (악재 하락 - 빠른 손절)")
    print("- 상황: 주가 하락 추세 (Price < SMA20) + 확실한 악재 (Sentiment: -0.8)")
    signal_3 = strategy.generate_signal(falling_df, sentiment_score=-0.8)
    print(f"👉 예상: SELL | 실제 도출 결과: {signal_3}")
    
    print("\n[Case 4] (추가 시나리오: 상승 추세 + 중립 뉴스)")
    print("- 상황: 주가 상승 추세 (Price > SMA20) + 중립 뉴스 (Sentiment: 0.0)")
    signal_4 = strategy.generate_signal(rising_df, sentiment_score=0.0)
    print(f"👉 예상: HOLD | 실제 도출 결과: {signal_4}")

if __name__ == "__main__":
    main()

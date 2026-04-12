# file path: strategies/nlp_momentum.py
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class NLPMomentumStrategy:
    """
    기술적 지표(이동평균선 기반 모멘텀)와 NLP 감성 점수를 결합하여
    최종 퀀트 매매 신호를 발생시키는 하이브리드 전략 클래스입니다.
    """
    def __init__(self, sma_period: int = 20, sentiment_buy_threshold: float = 0.2, sentiment_sell_threshold: float = -0.2):
        self.sma_period = sma_period
        self.sentiment_buy_threshold = sentiment_buy_threshold
        self.sentiment_sell_threshold = sentiment_sell_threshold
        self.confidence_threshold = 80

    def generate_signal(self, df: pd.DataFrame, sentiment_score: float, confidence: int = 100) -> str:
        """
        주어진 주가 데이터 프레임, 뉴스 감성 점수, 그리고 AI 확신도(Confidence)를 판단하여 
        "BUY", "SELL", "HOLD" 중 하나의 문자열을 반환합니다.
        """
        if df is None or len(df) < self.sma_period:
            logger.warning(f"데이터가 {self.sma_period}일 미만이라 SMA를 계산할 수 없습니다.")
            return "HOLD"
            
        # 1. 20일 단순 이동평균선(SMA) 연산
        # 결측치를 방지하기 위해 min_periods는 1로 둘 수도 있으나, 엄밀한 테스트를 위해 기본값 사용
        df = df.copy()
        df['SMA20'] = df['Close'].rolling(window=self.sma_period).mean()
        
        # 최신 종가 및 최신 SMA20 추출
        latest_close = df['Close'].iloc[-1]
        latest_sma = df['SMA20'].iloc[-1]
        
        # SMA 계산이 안 된 초기 구간 방어
        if pd.isna(latest_sma):
            return "HOLD"

        # 2. 강력한 모멘텀 + 감성 스코어 결합 로직
        is_uptrend = latest_close > latest_sma
        is_downtrend = latest_close < latest_sma
        
        # AI 확신도 필터: 방향성이 맞아도 확신도가 낮으면 매매 억제
        if confidence < self.confidence_threshold:
            return "HOLD"
        
        if is_downtrend or sentiment_score <= self.sentiment_sell_threshold:
            return "SELL"
        elif is_uptrend and sentiment_score >= self.sentiment_buy_threshold:
            return "BUY"
        else:
            return "HOLD"

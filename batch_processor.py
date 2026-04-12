# file path: batch_processor.py
import pandas as pd
import asyncio
from tqdm import tqdm
import os
import sys

# 프로젝트 루트 경로 확보
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from nlp_engine.analyzer import SentimentAnalyzer

async def process_batch():
    print("=== 테슬라 (TSLA) 배치 프로세서 가동 (Local LLM 기반) ===")
    
    try:
        df = pd.read_csv("filtered_tesla_news.csv")
    except FileNotFoundError:
        print("[에러] filtered_tesla_news.csv 파일이 없습니다. tesla_preprocessor.py를 먼저 실행하세요.")
        return

    settings = Settings()
    # 반드시 use_local_llm=True 적용하여 Ollama 비용절감 인프라 사용
    analyzer = SentimentAnalyzer(settings, use_local_llm=True)
    
    reasonings = []
    sentiments = []
    confidences = []
    
    # tqdm을 사용한 프로그레스 바 적용
    print(f"\n총 {len(df)}개의 뉴스 텍스트를 로컬 LLaMa3 모델로 분석 진행합니다...\n")
    for news in tqdm(df['title'], desc="Local LLM Inference Progress", unit="news"):
        # 동기 for 루프 내부에서 개별적으로 비동기 함수 대기
        reasoning, score, conf = await analyzer.analyze_sentiment(news)
        reasonings.append(reasoning)
        sentiments.append(score)
        confidences.append(conf)
        
    df['Reasoning'] = reasonings
    df['Sentiment'] = sentiments
    df['Confidence'] = confidences
    
    # 여러 기사가 같은 날짜에 있을 경우 대비 (일일 평균 감성 및 확신도로 통합)
    df_grouped = df.groupby('date', as_index=False).agg({'Sentiment': 'mean', 'Confidence': 'mean'})
    df_grouped.rename(columns={'date': 'Date'}, inplace=True)
    
    df_grouped.to_csv("tesla_sentiment_db.csv", index=False)
    print("\n✅ 배치 추론 및 일일 스코어 통합(Groupby Mean)이 완료되었습니다.")
    print("결과가 [tesla_sentiment_db.csv] 에 저장되었습니다.")

if __name__ == "__main__":
    asyncio.run(process_batch())

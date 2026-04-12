# file path: test_nlp_engine.py
import asyncio
from config.settings import Settings
from nlp_engine.analyzer import SentimentAnalyzer

def main():
    """
    SentimentAnalyzer 클래스를 동기적으로 테스트하는 로직입니다.
    비동기 메서드인 analyze_sentiment를 asyncio.run()으로 동기식 호출합니다.
    """
    print("=== NLP Sentiment Analysis 유닛 테스트 시작 ===")
    
    # 1. 설정 객체 인스턴스화
    settings = Settings()
    print(f"[Info] 로드된 OpenAI API Key: {settings.OPENAI_API_KEY[:10]}...")
    
    # 2. 분석기 인스턴스화
    analyzer = SentimentAnalyzer(settings)
    
    # 3. 테스트용 하드코딩 뉴스 텍스트
    sample_news = "Apple reports positive earnings beat, exceeding analyst expectations."
    print(f"\n[입력 텍스트]: {sample_news}")
    
    # 4. 분석 실행 (동기식 호출 래핑)
    print("\nGPT-4 API를 통한 감성 평가 중 (로딩)...")
    
    # asyncio.run을 사용하여 메인 스레드에서 차단식(동기식)으로 테스트
    score = asyncio.run(analyzer.analyze_sentiment(sample_news))
    
    # 5. 결과 출력
    print(f"\n✅ 파싱 및 출력된 최종 점수: {score}")

if __name__ == "__main__":
    main()

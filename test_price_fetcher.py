# file path: test_price_fetcher.py
from data_pipeline.price_fetcher import PriceFetcher

def main():
    print("=== Price Fetcher 유닛 테스트 시작 ===")
    
    fetcher = PriceFetcher()
    ticker = "AAPL"
    
    print(f"\n[1] {ticker} 최근 5일치 일봉(Daily) 데이터 수집 중...")
    daily_df = fetcher.get_daily_data(ticker, days=5)
    
    if not daily_df.empty:
        print("\n✅ 성공적으로 일봉 데이터를 가져왔습니다.")
        print(daily_df)
    else:
        print("❌ 일봉 데이터를 가져오는데 실패했습니다.")
        
    print("\n[2] 테스트 종료")

if __name__ == "__main__":
    main()

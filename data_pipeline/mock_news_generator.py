# file path: data_pipeline/mock_news_generator.py
import pandas as pd
from datetime import datetime, timedelta
import random

def generate_mock_news(days=30):
    """
    외부 데이터 연동 전 파이프라인 테스트를 위해, 최근 30일간의 날짜에 대해 
    임의의 금융 뉴스 텍스트(강한 호재, 약한 호재, 중립, 약한 악재, 강한 악재)를 병합하여 생성합니다.
    """
    end_date = datetime.now()
    dates = [end_date - timedelta(days=i) for i in range(days)]
    
    # 긍정/부정/중립이 섞인 가상의 뉴스 풀
    news_pool = [
        "Apple announces breakthrough in AI processor technology, exceeding expectations.", # 강한 긍정
        "Revenue drops by 20% due to supply chain bottlenecks, causing investor panic.",  # 강한 부정
        "Company holds annual shareholder meeting with no major announcements.",          # 중립
        "CEO resignation sparks concerns about future corporate strategy.",               # 부정
        "New FDA approval granted for flagship medical device, stock surges.",            # 극심한 긍정
        "Quarterly earnings missed analyst estimates slightly.",                          # 약한 부정
        "Partnership with major tech firm announced, positive outlook ahead."             # 긍정
    ]
    
    data = []
    for d in dates:
        # 주말(토=5, 일=6) 제외하여 거래일과 근사하게 매핑
        if d.weekday() < 5:
            data.append({
                "Date": d.strftime("%Y-%m-%d"),
                "News": random.choice(news_pool)
            })
            
    df = pd.DataFrame(data)
    # 날짜 오름차순으로 정렬
    df = df.sort_values("Date").reset_index(drop=True)
    df.to_csv("raw_news_data.csv", index=False)
    
    print(f"가상 뉴스 데이터 생성 완료: 총 {len(df)}행 -> [raw_news_data.csv] 저장")

if __name__ == "__main__":
    generate_mock_news(30)

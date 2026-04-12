import pandas as pd
import os

def preprocess_tesla_news():
    print("=== 테슬라 뉴스 데이터 전처리 파이프라인 시작 ===")
    
    file_path = "tesla_raw_news.csv"
    if not os.path.exists(file_path):
        print(f"[에러] {file_path} 파일이 존재하지 않습니다.")
        return
        
    # 1. Pandas로 원본 CSV 읽기
    df = pd.read_csv(file_path)
    print(f"📦 원본 데이터 로드 됨: 총 {len(df)}행")
    
    # 2. 날짜 형식 변환 및 불량 데이터(NaT) 제거 (errors='coerce')
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    
    # 3. 날짜 필터링 (2020-01-01 ~ 2023-01-31)
    start_date = pd.to_datetime("2020-01-01")
    end_date = pd.to_datetime("2023-01-31")
    df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
    
    # 4. 동일한 날짜 내 중복 기사 필터링
    df = df.drop_duplicates(subset=['date', 'title'])
    
    # 날짜 순서 정렬 및 인덱스 리셋
    df = df.sort_values(by='date').reset_index(drop=True)
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    
    # 결과 저장
    output_path = "filtered_tesla_news.csv"
    df.to_csv(output_path, index=False)
    
    print(f"✅ 정제 완료: 최종 {len(df)}개의 테슬라 기사가 확보되어 [{output_path}] 에 저장되었습니다.")

if __name__ == "__main__":
    preprocess_tesla_news()

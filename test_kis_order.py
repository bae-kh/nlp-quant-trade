import os
import requests
import json
from dotenv import load_dotenv

def test_mock_buy_order():
    # 1. 환경 변수 로드
    load_dotenv()
    APP_KEY = os.environ.get("KIS_MOCK_APP_KEY")
    APP_SECRET = os.environ.get("KIS_MOCK_APP_SECRET")
    ACCOUNT_NO = os.environ.get("KIS_MOCK_ACCOUNT_NO")

    if not APP_KEY or not APP_SECRET or not ACCOUNT_NO:
        print("[오류] .env 파일에서 KIS 관련 변수를 찾을 수 없습니다.")
        return

    URL_BASE = "https://openapivts.koreainvestment.com:29443"

    print("=== 한국투자증권(KIS) 모의투자 시장가 매수 주문 테스트 ===")

    # Step 1: 접근 토큰(Access Token) 발급
    print("1. 접근 토큰 발급 통신 중...")
    token_url = f"{URL_BASE}/oauth2/tokenP"
    token_payload = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    
    try:
        res = requests.post(token_url, json=token_payload)
        if res.status_code != 200:
            print("❌ 토큰 발급 HTTP 오류:", res.status_code)
            print(json.dumps(res.json(), indent=2, ensure_ascii=False))
            return
            
        token_data = res.json()
        access_token = token_data.get("access_token")
        
        if access_token:
            print("✅ 접근 토큰 발급 성공!")
        else:
            print("❌ 토큰 발급 실패. 응답을 확인하세요:", token_data)
            return
            
    except requests.exceptions.RequestException as e:
        print(f"❌ 접근 토큰 발급 중 통신 예외 발생: {e}")
        return

    # Step 2: 시장가 매수 주문 전송
    print("\n2. 삼성전자(005930) 1주 시장가 매수 주문 전송 중...")
    order_url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/order-cash"
    
    # 계좌번호와 상품코드 분리 방어적 데이터 처리
    if "-" in ACCOUNT_NO:
        cano, acnt_prdt_cd = ACCOUNT_NO.split("-", 1)
    else:
        cano = ACCOUNT_NO[:8]
        acnt_prdt_cd = "01"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {access_token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "VTTC0802U" # 모의투자 현금 매수 주문 (VTS)
    }
    
    payload = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO": "005930",    # 삼성전자 종목코드
        "ORD_DVSN": "01",    # 01: 시장가
        "ORD_QTY": "1",      # 주문 수량 1주
        "ORD_UNPR": "0"      # 시장가이므로 주문단가는 0원
    }
    
    try:
        res2 = requests.post(order_url, headers=headers, json=payload)
        
        # 200 OK 여부 확인
        if res2.status_code != 200:
            print("❌ 주문 중 HTTP 오류 발생:")
            try:
                print(json.dumps(res2.json(), indent=2, ensure_ascii=False))
            except:
                print(res2.text)
            return

        order_data = res2.json()
        
        rt_cd = order_data.get("rt_cd", "1") # "0" 이면 성공
        msg = order_data.get("msg1", "No Message")
        
        if rt_cd == "0":
            output = order_data.get("output", {})
            odno = output.get("ODNO", "확인 불가")
            print(f"\n=====================================")
            print(f"✅ 주문 전송 성공!")
            print(f"💌 메시지: {msg}")
            print(f"📝 주문번호(ODNO): {odno}")
            print(f"=====================================")
        else:
            print(f"\n❌ 주문 실패 [{order_data.get('msg_cd')}]: {msg}")
            # 추가적으로 실패 시나리오에서 어떤 값이 들어갔는지 디버깅용으로 출력
            print(f"참고 응답 데이터: {json.dumps(order_data, ensure_ascii=False)}")

    except requests.exceptions.RequestException as e:
        print(f"❌ 시장가 매수 주문 중 통신 예외 발생: {e}")
        return

if __name__ == "__main__":
    test_mock_buy_order()

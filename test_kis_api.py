import os
import requests
import json
from dotenv import load_dotenv

def get_mock_balance():
    # 1. 환경 변수 로드
    load_dotenv()
    APP_KEY = os.environ.get("KIS_MOCK_APP_KEY")
    APP_SECRET = os.environ.get("KIS_MOCK_APP_SECRET")
    ACCOUNT_NO = os.environ.get("KIS_MOCK_ACCOUNT_NO")

    if not APP_KEY or not APP_SECRET or not ACCOUNT_NO:
        print("[오류] .env 파일에서 KIS 관련 변수를 찾을 수 없습니다.")
        return

    URL_BASE = "https://openapivts.koreainvestment.com:29443"

    print("=== 한국투자증권(KIS) 모의투자 계좌 잔고 조회 단위 테스트 ===")

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

    # Step 2: 계좌 잔고 조회
    print("\n2. 계좌 잔고 조회 통신 중...")
    balance_url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/inquire-balance"
    
    # 계좌번호와 상품코드 분리 방어적 데이터 처리
    if "-" in ACCOUNT_NO:
        cano, acnt_prdt_cd = ACCOUNT_NO.split("-", 1)
    else:
        # 하이픈이 없다면, 계좌번호 앞자리만 추출 (초과 길이 자르기) 및 기본 01 상품코드 할당
        cano = ACCOUNT_NO[:8]
        acnt_prdt_cd = "01"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {access_token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "VTTC8434R" # 모의투자 잔고조회(VTS) TR_ID
    }
    
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "N",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }
    
    try:
        res2 = requests.get(balance_url, headers=headers, params=params)
        
        # 200 OK 여부 확인
        if res2.status_code != 200:
            print("❌ 조회 중 HTTP 오류 발생:")
            try:
                print(json.dumps(res2.json(), indent=2, ensure_ascii=False))
            except:
                print(res2.text)
            return

        bal_data = res2.json()
        
        # 응답 자체에 에러 코드(msg_cd, msg1)가 있는지도 확인
        rt_cd = bal_data.get("rt_cd", "1") # "0" 이면 성공
        if rt_cd != "0":
            print(f"❌ API 로직 에러 [{bal_data.get('msg_cd')}]: {bal_data.get('msg1')}")
            return
        
        # output2 리스트에서 총평가금액, 주문가능현금 등 주요 예수금을 추출
        output2 = bal_data.get("output2", [])
        if output2:
            header_balance = output2[0]
            # 예수금 총금액 (dnca_tot_amt) 혹은 D+2 예수금
            dnca_tot_amt = header_balance.get("dnca_tot_amt", "0")
            print(f"\n=====================================")
            print(f"💰 모의투자 계좌의 주문 가능 현금(예수금): {int(dnca_tot_amt):,} 원")
            print(f"=====================================")
        else:
            print("❌ 응답 데이터(output2)가 비어있습니다. 전체 JSON:")
            print(json.dumps(bal_data, indent=2, ensure_ascii=False))

    except requests.exceptions.RequestException as e:
        print(f"❌ 계좌 잔고 조회 중 통신 예외 발생: {e}")
        return

if __name__ == "__main__":
    get_mock_balance()

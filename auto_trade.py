# 실행 전 필수 요건: pip install ta
import logging
import os
import requests
import json
import math
from datetime import datetime, timedelta
from dotenv import load_dotenv
from config.settings import Settings
from nlp_engine.analyzer import SentimentAnalyzer
from data_pipeline.price_fetcher import PriceFetcher
import xml.etree.ElementTree as ET
from database.db_logger import DBLogger # 실전용 추가: DB 저장

logging.basicConfig(filename='daily_trade.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

URL_BASE = "https://openapivts.koreainvestment.com:29443"

# [실전용 추가] 텔레그램 메시지 전송 모듈
def send_telegram_message(message: str):
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logging.warning("텔레그램 토큰 또는 Chat ID가 설정되지 않아 알림을 보낼 수 없습니다.")
            return
            
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        res = requests.post(url, json=payload)
        res.raise_for_status()
    except Exception as e:
        logging.error(f"텔레그램 메시지 전송 실패: {e}")


# [실전용 추가] 1. 실시간 뉴스 크롤링 모듈
def fetch_today_news(ticker: str) -> str:
    try:
        logging.info(f"[{ticker}] 구글 뉴스 RSS 크롤링을 시작합니다.")
        
        # yfinance 대신 구조가 안정적인 Google News RSS 사용
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        
        root = ET.fromstring(res.content)
        items = root.findall('.//item')[:5] # 최신 5개 기사 추출
        
        if not items:
            logging.warning("가져온 뉴스가 없습니다.")
            return ""
            
        combined_text = []
        for item in items:
            title = item.findtext('title') or ''
            pub_date = item.findtext('pubDate') or ''
            combined_text.append(f"Title: {title}\nDate: {pub_date}\n")
            
        final_news_string = "\n".join(combined_text)
        
        # [핵심 방어 로직] "Title:" 같은 껍데기만 남는 것을 방지하기 위해 실제 텍스트 길이를 검증
        if len(final_news_string.replace("Title:", "").replace("Date:", "").strip()) < 20:
            logging.warning("뉴스 데이터가 유효하지 않아 빈 문자열을 반환합니다.")
            return ""
            
        logging.info("실시간 뉴스 텍스트 추출 완료.")
        return final_news_string
        
    except Exception as e:
        logging.error(f"뉴스 수집 중 오류 발생: {e}")
        return ""

def get_access_token(app_key, app_secret):
    token_url = f"{URL_BASE}/oauth2/tokenP"
    payload = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    res = requests.post(token_url, json=payload)
    res.raise_for_status()
    return res.json().get("access_token")

# [실전용 추가/변경] 3. 달러 예수금 + 보유 주식 수량 + 매입 평단가 파싱
def inquire_overseas_balance(app_key, app_secret, token, cano, acnt_prdt_cd, target_ticker="TSLA"):
    url = f"{URL_BASE}/uapi/overseas-stock/v1/trading/inquire-balance"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appKey": app_key,
        "appSecret": app_secret,
        "tr_id": "VTTS3012R"
    }
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": "NASD",
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": ""
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    data = res.json()
    
    ord_psbl_usd = 0.0
    output3 = data.get("output3", {})
    if output3:
        ord_psbl_usd = float(output3.get("evlu_amt_smtl_amt", 10000.0))
        
    holding_qty = 0
    avg_price = 0.0
    try:
        output1 = data.get("output1", [])
        for item in output1:
            if item.get("ovrs_pdno", "") == target_ticker:
                holding_qty = int(float(item.get("ovrs_cblc_qty", "0")))
                avg_price = float(item.get("pchs_avg_pric", "0.0"))
                break
    except Exception as e:
        logging.warning(f"[방어적 라우팅] 보유 수량/평단가 파싱 실패 (기본값 0 처리): {e}")

    return ord_psbl_usd, holding_qty, avg_price

def order_overseas_market_buy(app_key, app_secret, token, cano, acnt_prdt_cd, ticker, qty, price):
    url = f"{URL_BASE}/uapi/overseas-stock/v1/trading/order"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appKey": app_key,
        "appSecret": app_secret,
        "tr_id": "VTTT1002U"
    }
    payload = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": "NASD",
        "PDNO": ticker,
        "ORD_QTY": str(int(qty)),
        "OVRS_ORD_UNPR": f"{price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00"
    }
    return requests.post(url, headers=headers, json=payload)

# [실전용 추가] 2. KIS 해외주식 매도(SELL) API 함수
def order_overseas_market_sell(app_key, app_secret, token, cano, acnt_prdt_cd, ticker, qty, price):
    url = f"{URL_BASE}/uapi/overseas-stock/v1/trading/order"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appKey": app_key,
        "appSecret": app_secret,
        "tr_id": "VTTT1006U" # 매도 주문
    }
    payload = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": "NASD",
        "PDNO": ticker,
        "ORD_QTY": str(int(qty)),
        "OVRS_ORD_UNPR": f"{price:.2f}", 
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00" 
    }
    return requests.post(url, headers=headers, json=payload)

def run_daily_pipeline():
    logging.info("=== 일일 자동 매매 파이프라인 시작 (Production 환경) ===")
    db_logger = DBLogger()
    try:
        load_dotenv()
        APP_KEY = os.environ.get("KIS_MOCK_APP_KEY")
        APP_SECRET = os.environ.get("KIS_MOCK_APP_SECRET")
        ACCOUNT_NO = os.environ.get("KIS_MOCK_ACCOUNT_NO")

        if not APP_KEY or not APP_SECRET or not ACCOUNT_NO:
            raise ValueError(".env에 KIS API 정보가 누락되었습니다.")

        if "-" in ACCOUNT_NO:
            cano, acnt_prdt_cd = ACCOUNT_NO.split("-", 1)
        else:
            cano, acnt_prdt_cd = ACCOUNT_NO[:8], "01"

        token = get_access_token(APP_KEY, APP_SECRET)
        logging.info("KIS 접근 토큰 발급 완료")

        # [실전용 로직] 잔고, 테슬라 보유 물량, 평단가 동시 추출
        available_usd, holding_qty, avg_price = inquire_overseas_balance(APP_KEY, APP_SECRET, token, cano, acnt_prdt_cd, "TSLA")
        if available_usd <= 0:
            available_usd = 10000.0

        target_usd = available_usd * 0.10

        # YF 동적 주가 및 정량적 지표 계산 (60일)
        fetcher = PriceFetcher()
        end_date = datetime.today()
        start_date = end_date - timedelta(days=60)
        df_price = fetcher.get_daily_data("TSLA", start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        
        if df_price.empty:
            raise ValueError("TSLA 통신 불가로 현재 가격을 불러오지 못했습니다.")
        latest_data = df_price.iloc[-1]
        current_price = latest_data['Close']
        rsi_14 = latest_data['RSI_14']
        macd_diff = latest_data['MACD_diff']
        
        # 수익률(ROI) 계산
        roi = 0.0
        if holding_qty > 0 and avg_price > 0:
            roi = (current_price - avg_price) / avg_price * 100

        logging.info(f"계좌 가용 예수금: ${available_usd:.2f} | 10% 할당: ${target_usd:.2f}")
        logging.info(f"TSLA 현황 - 보유량: {holding_qty}주 | 매입 평단가: ${avg_price:.2f} | 현재 수익률: {roi:.2f}%")
        logging.info(f"TSLA 최신가: ${current_price:.2f} | RSI(14): {rsi_14:.2f} | MACD_diff: {macd_diff:.4f}")
        
        send_telegram_message(f"[시작] TSLA 퀀트봇 가동\n가용 예수금: ${target_usd:.2f}\n현재 보유량: {holding_qty}주\n현재 수익률: {roi:.2f}%")

        # 실시간 YF 뉴스 크롤링 연동
        todays_news = fetch_today_news("TSLA")
        if not todays_news.strip():
            logging.info("크롤링된 뉴스가 없어 오늘 매매를 진행하지 않습니다.")
            return

        settings = Settings()
        analyzer = SentimentAnalyzer(settings, use_local_llm=settings.USE_LOCAL_LLM)
        import asyncio
        reasoning, score, conf = asyncio.run(analyzer.analyze_sentiment(todays_news))
        logging.info(f"LLM 판단 결과 - 점수: {score}, 확신도: {conf}% | 근거: {reasoning}")
        send_telegram_message(f"[AI 분석] 점수: {score} | 확신도: {conf}%\n근거: {reasoning}")

        # [실전용 로직] 기계적 익절/손절 결합 다중 의사결정 트리 (Decision Tree)
        sold_today = False
        if holding_qty > 0:
            sell_reason = ""
            if roi >= 15.0:
                sell_reason = f"[익절 트리거] 수익률 15% 도달 (현재 {roi:.2f}%)"
            elif roi <= -5.0:
                sell_reason = f"[손절 트리거] 손실률 -5% 도달 (현재 {roi:.2f}%)"
            elif score < 0:
                sell_reason = f"[악재 트리거] 뉴스 감성 점수 하락 (점수: {score})"

            if sell_reason:
                sell_price = current_price * 0.97 # 체결 보장용 3% 할인 시장가 (지정가 전송)
                logging.info(f"{sell_reason} -> 보유량 {holding_qty}주 전량 매도 진행 (${sell_price:.2f})")
                
                res = order_overseas_market_sell(APP_KEY, APP_SECRET, token, cano, acnt_prdt_cd, "TSLA", holding_qty, sell_price)
                order_data = res.json()
                is_success = res.status_code == 200 and order_data.get("rt_cd") == "0"
                
                db_logger.log_trade("TSLA", "SELL", sell_price, holding_qty, roi, rsi_14, macd_diff, score, reasoning, "SUCCESS" if is_success else "FAILED")
                
                if is_success:
                    odno = order_data.get("output", {}).get("ODNO", "Unknown")
                    logging.info(f"✅ 매도 주문 성공! 메시지: {order_data.get('msg1')} | 주문번호: {odno}")
                    send_telegram_message(f"[체결 완료] {holding_qty}주 매도 성공!\n주문번호: {odno}")
                else:
                    logging.error(f"❌ 매도 실패: 코드 {order_data.get('msg_cd')} - {order_data.get('msg1')}")
                
                sold_today = True
                logging.info("매도 주문을 실행했으므로 오늘의 파이프라인(매수 로직)을 조기 종료합니다.")
                return

        if not sold_today:
            if score > 0 and conf >= 80:
                if rsi_14 >= 70:
                    msg = f"호재이나 차트 과열(RSI {rsi_14:.2f} 높은 상태)로 매수 보류"
                    logging.info(msg)
                    send_telegram_message(f"[관망] 매매 조건 미달. ({msg})")
                    db_logger.log_trade("TSLA", "HOLD", current_price, 0, roi, rsi_14, macd_diff, score, reasoning, "SKIPPED")
                elif macd_diff <= 0:
                    msg = f"호재이나 MACD 단기 하락 추세(MACD_diff {macd_diff:.4f})로 매수 보류"
                    logging.info(msg)
                    send_telegram_message(f"[관망] 매매 조건 미달. ({msg})")
                    db_logger.log_trade("TSLA", "HOLD", current_price, 0, roi, rsi_14, macd_diff, score, reasoning, "SKIPPED")
                else:
                    buy_qty = math.floor(target_usd / current_price)
                    if buy_qty > 0:
                        order_price = current_price * 1.03 
                        logging.info(f"[BUY 트리거] 정성/정량 필터 모두 통과! [{buy_qty}주] 매수 진행 (${order_price:.2f})")
                        
                        res = order_overseas_market_buy(APP_KEY, APP_SECRET, token, cano, acnt_prdt_cd, "TSLA", buy_qty, order_price)
                        order_data = res.json()
                        is_success = res.status_code == 200 and order_data.get("rt_cd") == "0"
                        
                        db_logger.log_trade("TSLA", "BUY", order_price, buy_qty, roi, rsi_14, macd_diff, score, reasoning, "SUCCESS" if is_success else "FAILED")
                        
                        if is_success:
                            odno = order_data.get("output", {}).get("ODNO", "Unknown")
                            logging.info(f"✅ 매수 주문 성공! 메시지: {order_data.get('msg1')} | 주문번호: {odno}")
                            send_telegram_message(f"[체결 완료] {buy_qty}주 매수 성공!\n주문번호: {odno}")
                        else:
                            logging.error(f"❌ 매수 실패: 코드 {order_data.get('msg_cd')} - {order_data.get('msg1')}")
                    else:
                        msg = "매수 트리거는 발동했으나 할당된 금액으로 금일 1주도 살 수 없습니다."
                        logging.info(msg + " 관망(HOLD).")
                        send_telegram_message(f"[관망] 매매 조건 미달. ({msg})")
                        db_logger.log_trade("TSLA", "HOLD", current_price, 0, roi, rsi_14, macd_diff, score, reasoning, "SKIPPED")
            else:
                msg = "매매 기준 미달(호재 아님/확신도 부족) 또는 악재이나 보유 주식이 없습니다."
                logging.info(msg + " 관망(HOLD).")
                send_telegram_message(f"[관망] 매매 조건 미달. ({msg})")
                db_logger.log_trade("TSLA", "HOLD", current_price, 0, roi, rsi_14, macd_diff, score, reasoning, "SKIPPED")

    except Exception as e:
        logging.error(f"파이프라인 실행 중 오류 발생: {e}")
        send_telegram_message(f"[🚨시스템 에러] 파이프라인 오류 발생: {e}")

if __name__ == "__main__":
    run_daily_pipeline()

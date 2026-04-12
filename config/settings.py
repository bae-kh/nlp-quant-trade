# file path: config/settings.py
import os
from dotenv import load_dotenv

class Settings:
    """
    프로젝트 환경 변수를 로드하고 관리하는 설정 클래스입니다.
    .env 파일에서 OPENAI_API_KEY 및 FINNHUB_API_KEY 등을 가져옵니다.
    """
    def __init__(self):
        # .env 파일 로드
        load_dotenv()
        
        # 환경 변수 할당 (없을 경우 기본값으로 더미 문자열 사용)
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "dummy_openai_key")
        self.FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "dummy_finnhub_key")
        
        # 퀀트 스위칭 수수료 (Slippage 및 브로커 수수료율 합산: 0.1%)
        self.TRANSACTION_FEE = 0.001

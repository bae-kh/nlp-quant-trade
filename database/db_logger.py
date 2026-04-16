import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class DBLogger:
    def __init__(self, db_path="quant_trade.db"):
        self.db_path = db_path
        self._create_table()

    def _create_table(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trade_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        ticker TEXT,
                        action TEXT,
                        price REAL,
                        quantity INTEGER,
                        roi REAL,
                        rsi REAL,
                        macd REAL,
                        llm_score REAL,
                        llm_reasoning TEXT,
                        status TEXT
                    )
                ''')
                conn.commit()
        except Exception as e:
            logger.error(f"DB 테이블 생성 중 오류 발생: {e}")

    def log_trade(self, ticker: str, action: str, price: float, quantity: int,
                  roi: float, rsi: float, macd: float, llm_score: float, 
                  llm_reasoning: str, status: str):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO trade_history 
                    (ticker, action, price, quantity, roi, rsi, macd, llm_score, llm_reasoning, status) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, action, price, quantity, roi, rsi, macd, llm_score, llm_reasoning, status))
                conn.commit()
        except Exception as e:
            logger.error(f"DB 로깅 중 오류 발생: {e}")

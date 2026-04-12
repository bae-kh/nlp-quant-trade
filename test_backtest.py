# file path: test_backtest.py
from backtest.runner import run_backtest

def main():
    print("=== NLP Quant 백테스트 통합 구동기(Test) ===")
    run_backtest("AAPL")

if __name__ == "__main__":
    main()

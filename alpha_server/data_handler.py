import yfinance as yf
import pandas as pd
import os
from .asset_screener import get_all_tickers

DATA_DIR = "alpha_server/market_data"

def download_ticker_data(ticker, period="5y", interval="1d"):
    """지정된 티커의 과거 시세 데이터를 yfinance로부터 다운로드합니다."""
    print(f"'{ticker}' 데이터 다운로드 중 (기간: {period}, 간격: {interval})...")
    try:
        data = yf.download(ticker, period=period, interval=interval, auto_adjust=True, timeout=10)
        if data.empty:
            print(f"경고: '{ticker}'에 대한 데이터를 다운로드할 수 없습니다. 티커가 올바른지 확인하세요.")
            return None
        return data
    except Exception as e:
        print(f"오류: '{ticker}' 데이터 다운로드 중 예외 발생: {e}")
        return None

def save_data_to_csv(ticker, data):
    """DataFrame을 CSV 파일로 저장합니다."""
    if data is None:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    filepath = os.path.join(DATA_DIR, f"{ticker}.csv")
    data.to_csv(filepath)
    print(f"성공: '{ticker}' 데이터를 '{filepath}'에 저장했습니다.")

def update_all_data():
    """동적 스크리너로 얻은 모든 자산의 데이터를 다운로드하고 CSV로 저장합니다."""
    print("--- 모든 자산 데이터 업데이트 시작 (파일 기반) ---")
    tickers = get_all_tickers()
    if not tickers:
        print("오류: 데이터를 업데이트할 티커 목록을 가져올 수 없습니다.")
        return

    for ticker in tickers:
        data = download_ticker_data(ticker)
        save_data_to_csv(ticker, data)
    print(f"--- 총 {len(tickers)}개 자산 데이터 업데이트 완료 ---")

if __name__ == '__main__':
    update_all_data()

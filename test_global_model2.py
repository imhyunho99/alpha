from alpha_server.global_model_handler import update_all_global_models

# SP500 ticker 10개만 골라서 DB 다운로드 후 테스트 진행
from alpha_server.data_handler import download_ticker_data, bulk_insert_data_to_db, get_db_connection
from alpha_server.asset_screener import get_all_tickers
import alpha_server.global_model_handler
import yfinance as yf

# 테스트용 티커 하드코딩
test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']
alpha_server.global_model_handler.get_all_tickers = lambda: test_tickers

# 데이터 다운로드 및 삽입
print("Downloading test data...")
raw_data_cache = {}
for ticker in test_tickers:
    df = yf.download(ticker, period="2y", progress=False, auto_adjust=True)
    if not df.empty:
        raw_data_cache[ticker] = df

print("Inserting test data into DB...")
bulk_insert_data_to_db(raw_data_cache)

import time
time.sleep(2)

print("Starting training...")
update_all_global_models()

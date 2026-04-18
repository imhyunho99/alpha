import time
import pandas as pd
from alpha_server import asset_screener
import random
import requests

try:
    sp500 = asset_screener.get_sp500_tickers()
    random.seed(42)
    TEST_TICKERS = random.sample(sp500, 100)
except Exception:
    TEST_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']

print(f"Testing {len(TEST_TICKERS)} tickers")

start = time.time()
for ticker in TEST_TICKERS:
    try:
        url = f"http://127.0.0.1:9000/exp?query=SELECT+timestamp+AS+Date,+open,+high,+low,+close,+volume+FROM+stock_prices+WHERE+ticker='{ticker}'+ORDER+BY+timestamp"
        df = pd.read_csv(url, index_col='Date', parse_dates=True)
    except Exception as e:
        pass
print(f"REST API Read Time: {time.time() - start:.4f}s")

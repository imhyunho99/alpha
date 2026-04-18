from alpha_server.market_features import get_ticker_metadata
from alpha_server.asset_screener import get_all_tickers
import os

print("Testing metadata fetch...")
tickers = get_all_tickers()[:15] # Test with 15 tickers
metadata = get_ticker_metadata(tickers)
print(f"Fetched {len(metadata)} items")
print("Sample:", list(metadata.items())[0])

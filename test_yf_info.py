import yfinance as yf
ticker = yf.Ticker("AAPL")
info = ticker.info
print("Sector:", info.get('sector'))
print("Industry:", info.get('industry'))
print("Market Cap:", info.get('marketCap'))
print("Beta:", info.get('beta'))
print("PE Ratio:", info.get('trailingPE'))

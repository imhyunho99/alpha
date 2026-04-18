import pandas as pd
url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
tables = pd.read_html(url)
sp500_table = tables[0]
tickers = sp500_table['Symbol'].tolist()
print(tickers[:10])
print(len(tickers))

import requests
import pandas as pd
from bs4 import BeautifulSoup

def get_nasdaq_100_tickers():
    """Wikipedia에서 NASDAQ 100 구성 종목의 티커 목록을 스크래핑합니다."""
    print("NASDAQ 100 티커 목록 가져오는 중...")
    try:
        url = "https://en.wikipedia.org/wiki/NASDAQ-100"
        response = requests.get(url, headers={'User-Agent': 'Alpha-Project-Screener/1.0'})
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        table = soup.find('table', {'id': 'constituents'})
        
        tickers = []
        # 테이블의 모든 행(tr)에 대해 반복
        for row in table.find_all('tr')[1:]: # 헤더 행은 건너뛰기
            # 각 행의 첫 번째 셀(td)에 티커가 있음
            ticker_cell = row.find('td')
            if ticker_cell:
                tickers.append(ticker_cell.text.strip())
        
        print(f"성공: {len(tickers)}개의 NASDAQ 100 티커를 찾았습니다.")
        return tickers
    except Exception as e:
        print(f"오류: NASDAQ 100 티커를 가져오는 중 오류 발생: {e}")
        # 실패 시 하드코딩된 일부 목록 반환
        return ['AAPL', 'MSFT', 'AMZN', 'GOOGL', 'NVDA', 'TSLA', 'META', 'AVGO', 'PEP', 'COST']

def get_top_100_crypto_tickers():
    """CoinGecko API를 사용하여 시가총액 상위 100개 암호화폐 티커를 가져옵니다."""
    print("상위 100개 암호화폐 티커 목록 가져오는 중...")
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': 100,
            'page': 1,
            'sparkline': 'false'
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # yfinance에서 사용할 수 있는 형식 (예: 'BTC-USD')으로 변환
        tickers = [f"{coin['symbol'].upper()}-USD" for coin in data]
        
        print(f"성공: {len(tickers)}개의 암호화폐 티커를 찾았습니다.")
        return tickers
    except Exception as e:
        print(f"오류: 암호화폐 티커를 가져오는 중 오류 발생: {e}")
        # 실패 시 하드코딩된 일부 목록 반환
        return ['BTC-USD', 'ETH-USD', 'USDT-USD', 'BNB-USD', 'SOL-USD']

def get_all_tickers():
    """모든 소스에서 티커를 취합하여 최종 자산 목록을 반환합니다."""
    nasdaq_tickers = get_nasdaq_100_tickers()
    crypto_tickers = get_top_100_crypto_tickers()
    
    # 여기에 다른 ETF나 원자재 관련 티커를 추가할 수 있습니다.
    other_tickers = ['SPY', 'QQQ', 'GLD', 'SLV', 'USO']
    
    # 중복을 제거하고 합칩니다.
    all_tickers = list(set(nasdaq_tickers + crypto_tickers + other_tickers))
    print(f"총 {len(all_tickers)}개의 고유한 자산을 추적합니다.")
    return all_tickers

def get_benchmark_tickers():
    """성능 측정을 위한 대표적인 5개의 티커 목록을 반환합니다."""
    print("벤치마크용 티커 목록(5개)을 사용합니다.")
    return ['AAPL', 'TSLA', 'SPY', 'BTC-USD', 'GLD']

if __name__ == '__main__':
    # 테스트를 위해 이 파일을 직접 실행하면 티커 목록을 출력합니다.
    tickers = get_all_tickers()
    print("\n최종 자산 목록:")
    print(tickers)
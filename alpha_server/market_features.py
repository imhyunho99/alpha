import yfinance as yf
import pandas as pd
import os
import json
from datetime import datetime, timedelta

# 캐시 파일 경로
METADATA_CACHE_FILE = os.path.join(os.path.dirname(__file__), "market_data", "metadata_cache.json")

def get_ticker_metadata(tickers, force_refresh=False):
    """지정된 티커 목록에 대한 메타데이터(Sector, Industry, MarketCap 등)를 반환합니다.
       매번 yfinance를 호출하지 않도록 로컬 JSON 파일에 캐싱합니다."""
    
    # 캐시 디렉토리 확인 및 생성
    os.makedirs(os.path.dirname(METADATA_CACHE_FILE), exist_ok=True)
    
    cache_data = {}
    
    # 캐시 파일이 존재하고, 만료되지 않았으며, force_refresh가 아닐 경우 로드
    if os.path.exists(METADATA_CACHE_FILE) and not force_refresh:
        try:
            with open(METADATA_CACHE_FILE, 'r') as f:
                cache_content = json.load(f)
                
            # 캐시가 30일 이내인지 확인
            last_updated = datetime.fromisoformat(cache_content.get('_last_updated', '2000-01-01'))
            if datetime.now() - last_updated < timedelta(days=30):
                cache_data = cache_content.get('data', {})
        except Exception as e:
            print(f"메타데이터 캐시 로드 실패: {e}")
            
    # 업데이트가 필요한 티커 확인
    tickers_to_fetch = [t for t in tickers if t not in cache_data]
    
    if tickers_to_fetch:
        print(f"총 {len(tickers_to_fetch)}개 종목의 메타데이터를 다운로드합니다. (yfinance)")
        for i, ticker in enumerate(tickers_to_fetch):
            if i % 10 == 0:
                print(f"진행 상황: {i}/{len(tickers_to_fetch)}")
            try:
                info = yf.Ticker(ticker).info
                
                # 암호화폐 등 일부 자산은 sector/industry가 없을 수 있으므로 기본값 처리
                cache_data[ticker] = {
                    'sector': info.get('sector', 'Unknown'),
                    'industry': info.get('industry', 'Unknown'),
                    'marketCap': info.get('marketCap', 0),
                    'beta': info.get('beta', 1.0)
                }
            except Exception as e:
                # 에러 발생 시 기본값으로 설정
                cache_data[ticker] = {
                    'sector': 'Unknown',
                    'industry': 'Unknown',
                    'marketCap': 0,
                    'beta': 1.0
                }
                
        # 캐시 저장
        try:
            with open(METADATA_CACHE_FILE, 'w') as f:
                json.dump({
                    '_last_updated': datetime.now().isoformat(),
                    'data': cache_data
                }, f, indent=4)
        except Exception as e:
            print(f"메타데이터 캐시 저장 실패: {e}")
            
    return cache_data
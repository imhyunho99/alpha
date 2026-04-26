"""자산 유니버스 스크리너.

지원 시장:
  - 미국: NASDAQ 100, S&P 500, 주요 ETF
  - 한국: KOSPI 200 (yfinance에 .KS 접미사로 조회)
  - 암호화폐: CoinGecko 시가총액 Top 200 (yfinance에 -USD 접미사)

각 시장은 환경변수로 켜고 끌 수 있다 (ALPHA_ENABLE_US / _KR / _CRYPTO).
캐시: 1시간 동안 같은 호출 결과 재사용.
"""
from __future__ import annotations

import os
import time
from typing import Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup

_USER_AGENT = "Alpha-Project-Screener/3.1"
_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL_SEC = 3600


def _enabled(market: str, default: bool = True) -> bool:
    val = os.getenv(f"ALPHA_ENABLE_{market.upper()}")
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def _cached(key: str, fetcher) -> list[str]:
    now = time.time()
    if key in _CACHE and now - _CACHE[key][0] < _CACHE_TTL_SEC:
        return _CACHE[key][1]
    value = fetcher()
    _CACHE[key] = (now, value)
    return value


# ---------- 미국 ----------
def get_nasdaq_100_tickers() -> list[str]:
    def _fetch():
        url = "https://en.wikipedia.org/wiki/NASDAQ-100"
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", {"id": "constituents"})
        if not table:
            raise RuntimeError("NASDAQ-100 constituents 테이블을 찾을 수 없습니다.")
        tickers = []
        for row in table.find_all("tr")[1:]:
            cell = row.find("td")
            if cell:
                tickers.append(cell.text.strip())
        return tickers

    try:
        return _cached("nasdaq100", _fetch)
    except Exception as e:
        print(f"⚠️ NASDAQ-100 페치 실패: {e}")
        return ["AAPL", "MSFT", "AMZN", "GOOGL", "NVDA", "TSLA", "META", "AVGO", "PEP", "COST"]


def get_sp500_tickers() -> list[str]:
    def _fetch():
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        return [str(t).replace(".", "-") for t in tables[0]["Symbol"].tolist()]

    try:
        return _cached("sp500", _fetch)
    except Exception as e:
        print(f"⚠️ S&P 500 페치 실패: {e}")
        return ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]


# ---------- 한국 ----------
def get_kospi200_tickers() -> list[str]:
    """KOSPI 200 종목을 yfinance 호환 포맷(`<6자리>.KS`)으로 반환."""

    def _fetch():
        # KRX 공식 KOSPI 200 명단 (네이버/한국거래소 기반).
        # 위키피디아의 KOSPI 200 표가 가장 안정적이라 그곳을 1차 소스로 사용.
        url = "https://en.wikipedia.org/wiki/KOSPI_200"
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        # 'Ticker' 또는 'Symbol' 컬럼이 있는 첫 번째 테이블 선택
        for table in tables:
            cols_lower = [str(c).lower() for c in table.columns]
            for col in cols_lower:
                if "ticker" in col or "symbol" in col or "code" in col:
                    raw_col = table.columns[cols_lower.index(col)]
                    tickers = [str(t).strip() for t in table[raw_col].dropna().tolist()]
                    # 6자리 숫자만 필터링하고 .KS 접미사 부착
                    cleaned = []
                    for t in tickers:
                        digits = "".join(ch for ch in t if ch.isdigit())
                        if len(digits) == 6:
                            cleaned.append(f"{digits}.KS")
                    if cleaned:
                        return cleaned
        raise RuntimeError("KOSPI 200 표에서 티커 컬럼을 찾을 수 없습니다.")

    try:
        return _cached("kospi200", _fetch)
    except Exception as e:
        print(f"⚠️ KOSPI 200 페치 실패: {e}")
        # 시총 상위 대표 종목 폴백
        return [
            "005930.KS",  # 삼성전자
            "000660.KS",  # SK하이닉스
            "373220.KS",  # LG에너지솔루션
            "207940.KS",  # 삼성바이오로직스
            "005935.KS",  # 삼성전자우
            "035420.KS",  # NAVER
            "005380.KS",  # 현대차
            "051910.KS",  # LG화학
            "006400.KS",  # 삼성SDI
            "035720.KS",  # 카카오
        ]


# ---------- 암호화폐 ----------
def get_top_crypto_tickers(limit: int = 200) -> list[str]:
    def _fetch():
        url = "https://api.coingecko.com/api/v3/coins/markets"
        # CoinGecko per_page는 250까지 허용. 200까지 한 번에 요청.
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(limit, 250),
            "page": 1,
            "sparkline": "false",
        }
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return [f"{coin['symbol'].upper()}-USD" for coin in resp.json()][:limit]

    try:
        return _cached(f"crypto_top_{limit}", _fetch)
    except Exception as e:
        print(f"⚠️ 암호화폐 Top {limit} 페치 실패: {e}")
        return ["BTC-USD", "ETH-USD", "USDT-USD", "BNB-USD", "SOL-USD"]


# 하위 호환을 위한 별칭
def get_top_100_crypto_tickers() -> list[str]:
    return get_top_crypto_tickers(100)


# ---------- 통합 ----------
def get_all_tickers() -> list[str]:
    """모든 시장의 티커를 합쳐 중복 제거된 리스트로 반환."""
    pools: list[Iterable[str]] = []
    if _enabled("us"):
        pools.append(get_nasdaq_100_tickers())
        pools.append(get_sp500_tickers())
        pools.append(["SPY", "QQQ", "GLD", "SLV", "USO"])
    if _enabled("kr"):
        pools.append(get_kospi200_tickers())
    if _enabled("crypto"):
        pools.append(get_top_crypto_tickers(200))

    seen: dict[str, None] = {}
    for pool in pools:
        for t in pool:
            if t and t not in seen:
                seen[t] = None
    print(f"총 {len(seen)}개의 고유 자산을 추적합니다.")
    return list(seen.keys())


def get_market_for_ticker(ticker: str) -> str:
    """티커 형식으로 시장 분류 (us|kr|crypto|unknown)."""
    if ticker.endswith("-USD"):
        return "crypto"
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "kr"
    if ticker.replace("-", "").replace(".", "").isalpha():
        return "us"
    return "unknown"


def get_benchmark_tickers() -> list[str]:
    return ["AAPL", "TSLA", "SPY", "BTC-USD", "005930.KS"]


if __name__ == "__main__":
    tickers = get_all_tickers()
    print(f"\n최종 {len(tickers)}개 자산 (샘플): {tickers[:20]}")

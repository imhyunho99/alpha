"""통화 판정과 USD→KRW 환산.

계좌는 기준통화(KRW)만 안다. 달러 자산의 환산은 여기서 재료를 만들고
prices 계층에서 적용한다.
"""
from __future__ import annotations

from datetime import datetime

FALLBACK_USD_KRW = 1350.0

_rate_cache: dict[str, float] = {}


def native_currency(ticker: str) -> str:
    """티커의 표시 통화. 한국 종목만 KRW, 나머지는 USD."""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "KRW"
    return "USD"


def to_krw(amount: float, currency: str, rate: float) -> float:
    if currency == "KRW":
        return amount
    return amount * rate


def clear_cache() -> None:
    _rate_cache.clear()


def _fetch_usd_krw(at: datetime | None) -> float:
    """yfinance에서 USDKRW=X 종가를 가져온다. 실패하면 예외를 올린다."""
    import yfinance as yf

    ticker = yf.Ticker("USDKRW=X")
    if at is None:
        hist = ticker.history(period="5d")
    else:
        end = at.strftime("%Y-%m-%d")
        hist = ticker.history(start="1990-01-01", end=end)
    if hist is None or hist.empty:
        raise RuntimeError("USDKRW=X 조회 결과가 비어 있습니다")
    return float(hist["Close"].iloc[-1])


def usd_krw_rate(at: datetime | None = None) -> float:
    """USD→KRW 환율 단일값. 조회 실패 시 FALLBACK_USD_KRW로 폴백한다.

    날짜 단위로 캐시한다 — 백테스트에서 같은 날을 반복 조회하기 때문이다.
    """
    key = at.strftime("%Y-%m-%d") if at else "latest"
    if key in _rate_cache:
        return _rate_cache[key]
    try:
        rate = _fetch_usd_krw(at)
    except Exception:
        rate = FALLBACK_USD_KRW
    _rate_cache[key] = rate
    return rate


def _fetch_usd_krw_history(start: datetime, end: datetime):
    """기간 전체의 USDKRW=X 일별 종가. 실패하면 예외를 올린다."""
    import yfinance as yf

    hist = yf.Ticker("USDKRW=X").history(
        start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d")
    )
    if hist is None or hist.empty:
        raise RuntimeError("USDKRW=X 기간 조회 결과가 비어 있습니다")
    return hist["Close"]


def usd_krw_series(start: datetime, end: datetime):
    """기간 전체의 일별 환율 시리즈. 실패하면 빈 시리즈를 돌려준다.

    빈 시리즈는 resolve_rate에서 폴백 환율로 처리되므로 백테스트는 계속 돈다.
    """
    import pandas as pd

    try:
        series = _fetch_usd_krw_history(start, end)
    except Exception as exc:
        print(f"환율 이력 조회 실패, 고정 환율로 폴백합니다: {exc}")
        return pd.Series(dtype="float64")

    if series.index.tz is None:
        series.index = series.index.tz_localize("UTC")
    else:
        series.index = series.index.tz_convert("UTC")
    return series


def resolve_rate(rates, at: datetime) -> float:
    """시점 환율. rates가 float이면 그대로, Series면 at 이하의 마지막 값.

    at 이전 데이터가 없으면 FALLBACK_USD_KRW. 미래 환율은 절대 보지 않는다.
    """
    if isinstance(rates, (int, float)):
        return float(rates)

    if rates is None or len(rates) == 0:
        return FALLBACK_USD_KRW

    window = rates.loc[rates.index <= at]
    if len(window) == 0:
        return FALLBACK_USD_KRW
    return float(window.iloc[-1])

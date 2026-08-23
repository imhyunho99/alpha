"""가격 소스. 반환값은 항상 기준통화(KRW)다 — 환산이 여기서 끝난다."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

import pandas as pd

from . import fx


class PriceSource(Protocol):
    def get(self, ticker: str, at: datetime) -> float | None: ...
    def get_many(self, tickers: list[str], at: datetime) -> dict[str, float]: ...


class _BaseSource:
    def get_many(self, tickers: list[str], at: datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in tickers:
            price = self.get(t, at)
            if price is not None:
                out[t] = price
        return out


class HistoricalPrices(_BaseSource):
    """미리 로드된 OHLCV 프레임에서 조회. 백테스트용.

    at 시점 **이하**의 마지막 종가만 본다. 미래를 보지 않는다.
    환율도 마찬가지로 그 시점의 값을 적용한다.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], rates) -> None:
        self._frames = frames
        self._rates = rates

    def get(self, ticker: str, at: datetime) -> float | None:
        frame = self._frames.get(ticker)
        if frame is None or frame.empty:
            return None
        window = frame.loc[frame.index <= at]
        if window.empty:
            return None
        native = float(window["Close"].iloc[-1])
        currency = fx.native_currency(ticker)
        if currency == "KRW":
            return native
        return fx.to_krw(native, currency, fx.resolve_rate(self._rates, at))


class LivePrices(_BaseSource):
    """yfinance 실시간 조회. at은 무시한다 (항상 최신)."""

    def __init__(self, rate_provider: Callable[[], float] | None = None) -> None:
        self._rate_provider = rate_provider or (lambda: fx.usd_krw_rate(None))
        self._cache: dict[str, float] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def get(self, ticker: str, at: datetime) -> float | None:
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            import yfinance as yf

            hist = yf.Ticker(ticker).history(period="1d")
            if hist is None or hist.empty:
                return None
            native = float(hist["Close"].iloc[-1])
        except Exception:
            return None
        price = fx.to_krw(native, fx.native_currency(ticker), self._rate_provider())
        self._cache[ticker] = price
        return price

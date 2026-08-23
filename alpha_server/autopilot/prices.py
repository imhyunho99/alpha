"""가격 소스. 반환값은 항상 기준통화(KRW)다 — 환산이 여기서 끝난다."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

import pandas as pd

from . import fx


def _to_utc_index(frame: pd.DataFrame) -> pd.DataFrame:
    """인덱스를 UTC tz-aware DatetimeIndex 로 통일한다."""
    if frame is None or frame.empty:
        return frame
    out = frame
    if not isinstance(out.index, pd.DatetimeIndex):
        out = out.copy()
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[out.index.notna()]
    if out.index.tz is None:
        out = out.copy()
        out.index = out.index.tz_localize("UTC")
    elif str(out.index.tz) != "UTC":
        out = out.copy()
        out.index = out.index.tz_convert("UTC")
    return out


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
        # CSV에서 온 프레임은 tz-naive 인덱스를 갖는다. 엔진의 `at` 은 항상
        # tz-aware 이므로 그대로 비교하면 TypeError 로 죽는다. 생성 시 한 번만 맞춘다.
        self._frames = {t: _to_utc_index(f) for t, f in frames.items() if f is not None}
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
    """yfinance 실시간 조회. at은 무시한다 (항상 최신).

    종목당 따로 호출하면 1.65초씩 걸려 150종목이 4분이다. 실시간 루프 주기가
    5분이므로 한 사이클이 사실상 끝나지 않는다. get_many 는 배치로 받는다.
    """

    BATCH_SIZE = 60

    def __init__(self, rate_provider: Callable[[], float] | None = None) -> None:
        self._rate_provider = rate_provider or (lambda: fx.usd_krw_rate(None))
        self._cache: dict[str, float] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def _to_base(self, ticker: str, native: float) -> float:
        return fx.to_krw(native, fx.native_currency(ticker), self._rate_provider())

    def get(self, ticker: str, at: datetime) -> float | None:
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            import yfinance as yf

            # 5일치를 보고 마지막 종가를 쓴다. period="1d" 는 휴장일에 빈다.
            hist = yf.Ticker(ticker).history(period="5d")
            if hist is None or hist.empty:
                return None
            native = float(hist["Close"].iloc[-1])
        except Exception:
            return None
        price = self._to_base(ticker, native)
        self._cache[ticker] = price
        return price

    def get_many(self, tickers: list[str], at: datetime) -> dict[str, float]:
        """배치 다운로드. 실패한 청크는 종목별 조회로 폴백한다."""
        import yfinance as yf

        out: dict[str, float] = {}
        pending: list[str] = []
        for t in tickers:
            if t in self._cache:
                out[t] = self._cache[t]
            else:
                pending.append(t)

        for start in range(0, len(pending), self.BATCH_SIZE):
            chunk = pending[start:start + self.BATCH_SIZE]
            try:
                raw = yf.download(
                    tickers=chunk, period="1d", interval="1d",
                    group_by="ticker", auto_adjust=True,
                    progress=False, threads=True,
                )
            except Exception as exc:
                print(f"실시간 배치 조회 실패({len(chunk)}종목), 개별 조회로 폴백: {exc}")
                raw = None

            if raw is None or raw.empty:
                for t in chunk:
                    price = self.get(t, at)
                    if price is not None:
                        out[t] = price
                continue

            # 배치가 부분 성공하는 경우가 흔하다. period="1d" 는 휴장일이나
            # 상장 초기 종목에서 봉이 비거나 Close 가 NaN 이다. 예전 코드는 그런
            # 종목을 조용히 버렸고(청크 전체가 실패했을 때만 폴백), 그 결과
            # 보유 15종목 중 9개 가격이 없어 자동 운용이 매 사이클 건너뛰었다.
            unresolved: list[str] = []
            for t in chunk:
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        if t not in raw.columns.get_level_values(0):
                            unresolved.append(t)
                            continue
                        series = raw[t]["Close"].dropna()
                    else:
                        series = raw["Close"].dropna()
                    if series.empty:
                        unresolved.append(t)
                        continue
                    price = self._to_base(t, float(series.iloc[-1]))
                    self._cache[t] = price
                    out[t] = price
                except Exception:
                    unresolved.append(t)

            # 배치에서 빠진 종목만 개별로 다시 묻는다. 개별 조회는 period="1d"
            # 대신 최근 며칠을 보므로 휴장일에도 마지막 종가를 얻는다.
            for t in unresolved:
                price = self.get(t, at)
                if price is not None:
                    out[t] = price

        return out

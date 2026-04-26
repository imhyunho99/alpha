"""기술 지표 계산. pandas Series in → 스칼라 또는 Series out."""
from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 라인, 시그널, 히스토그램 반환."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def change_pct(close: pd.Series, period: int = 1) -> pd.Series:
    return close.pct_change(periods=period) * 100


def latest_value(series: pd.Series):
    series = series.dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])


def detect_cross(
    fast: pd.Series, slow: pd.Series, direction: str = "golden", lookback: int = 2
) -> bool:
    """fast 가 slow 을 (golden) 상향 돌파했는지, (death) 하향 이탈했는지 최근 lookback 봉 안에서 확인."""
    if len(fast) < lookback + 1 or len(slow) < lookback + 1:
        return False
    f = fast.dropna()
    s = slow.dropna()
    n = min(len(f), len(s), lookback + 1)
    if n < 2:
        return False
    f_recent = f.iloc[-n:]
    s_recent = s.iloc[-n:]
    above = (f_recent > s_recent).astype(int)
    crosses = above.diff().fillna(0)
    if direction == "golden":
        return bool((crosses > 0).any())
    return bool((crosses < 0).any())


def compute_for_condition(close: pd.Series, indicator: str, period: int) -> float | None:
    if indicator == "rsi":
        return latest_value(rsi(close, period))
    if indicator == "sma":
        return latest_value(sma(close, period))
    if indicator == "ema":
        return latest_value(ema(close, period))
    if indicator == "price":
        return latest_value(close)
    if indicator == "change_pct":
        return latest_value(change_pct(close, period))
    return None

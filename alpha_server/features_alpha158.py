"""Qlib Alpha158 스타일 확장 피처.

이 모듈의 존재 이유는 단 하나다: **모든 계산이 과거만 본다.**
shift(-N), 역방향 rolling, 전체 구간 통계(z-score 등)를 절대 쓰지 않는다.
t 시점 피처는 t 시점까지의 봉만으로 결정되므로, 뒤쪽 데이터를 바꿔도
앞쪽 피처는 변하지 않는다.

Qlib 정의를 따르되 pandas 로 직접 계산한다 (qlib 의존성 없음).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 0 나눗셈 방어용 상수. 분모가 '폭'이나 '거래량'처럼 0 이 정상 범위인 값일 때 쓴다.
EPS = 1e-12

DEFAULT_WINDOWS: tuple[int, ...] = (5, 10, 20, 60)

_REQUIRED = ("Open", "High", "Low", "Close", "Volume")

# 윈도우마다 만들어지는 피처 이름
_WINDOW_FEATURES: tuple[str, ...] = (
    "ROC", "MA", "STD", "BETA", "MAX", "MIN", "QTLU", "QTLD",
    "RSV", "CORR", "CNTP", "CNTN", "SUMP", "SUMN", "VMA", "VSTD",
)


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    """OHLCV 컬럼 하나를 float 시리즈로 꺼낸다. 대소문자는 눈감아 준다."""
    if name in frame.columns:
        return frame[name].astype(float)
    lowered = {str(c).lower(): c for c in frame.columns}
    key = lowered.get(name.lower())
    if key is None:
        raise KeyError(
            f"'{name}' 컬럼이 없습니다. 필요한 컬럼: {', '.join(_REQUIRED)}. "
            f"받은 컬럼: {list(frame.columns)}"
        )
    return frame[key].astype(float)


def _nonzero(series: pd.Series) -> pd.Series:
    """가격 분모용. 0 은 의미 없는 값이므로 NaN 으로 만든다 (inf 방지)."""
    return series.replace(0.0, np.nan)


def build_features(
    frame: pd.DataFrame,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> pd.DataFrame:
    """OHLCV 프레임에서 Alpha158 스타일 피처를 만든다.

    Args:
        frame: Open/High/Low/Close/Volume 컬럼과 오름차순 DatetimeIndex.
        windows: 롤링 윈도우 길이들.

    Returns:
        입력과 동일한 인덱스의 피처 DataFrame.

    Raises:
        KeyError: OHLCV 컬럼이 빠졌을 때.
        ValueError: 인덱스가 오름차순이 아닐 때 — 그대로 굴리면 rolling 이
            미래 봉을 섞어 버리므로 조용히 넘어가지 않는다.
    """
    if not frame.index.is_monotonic_increasing:
        raise ValueError(
            "인덱스가 오름차순이 아닙니다. 정렬하지 않고 rolling 을 돌리면 "
            "과거 시점 피처에 미래 봉이 섞입니다."
        )

    open_ = _column(frame, "Open")
    high = _column(frame, "High")
    low = _column(frame, "Low")
    close = _column(frame, "Close")
    volume = _column(frame, "Volume")

    # 가격 분모는 0 이면 NaN, 폭·거래량 분모는 EPS 를 더한다.
    open_d = _nonzero(open_)
    close_d = _nonzero(close)
    bar_range = (high - low) + EPS

    out: dict[str, pd.Series] = {}

    # --- KBAR: 봉 하나의 모양 ---
    body = close - open_
    upper = high - np.maximum(open_, close)
    lower = np.minimum(open_, close) - low
    shift = 2 * close - high - low

    out["KMID"] = body / open_d
    out["KLEN"] = (high - low) / open_d
    out["KMID2"] = body / bar_range
    out["KUP"] = upper / open_d
    out["KUP2"] = upper / bar_range
    out["KLOW"] = lower / open_d
    out["KLOW2"] = lower / bar_range
    out["KSFT"] = shift / open_d
    out["KSFT2"] = shift / bar_range

    # --- 종가로 정규화한 가격 ---
    out["OPEN0"] = open_ / close_d
    out["HIGH0"] = high / close_d
    out["LOW0"] = low / close_d

    # --- 롤링 피처 ---
    log_volume = np.log1p(volume)
    diff = close.diff()
    gain = diff.clip(lower=0.0)
    abs_change = diff.abs()
    volume_d = volume + EPS

    for w in windows:
        roll_close = close.rolling(w)
        window_high = high.rolling(w).max()
        window_low = low.rolling(w).min()

        out[f"ROC{w}"] = close.shift(w) / close_d
        out[f"MA{w}"] = roll_close.mean() / close_d
        out[f"STD{w}"] = roll_close.std() / close_d
        out[f"BETA{w}"] = (close - close.shift(w)) / w / close_d
        out[f"MAX{w}"] = window_high / close_d
        out[f"MIN{w}"] = window_low / close_d
        out[f"QTLU{w}"] = roll_close.quantile(0.8) / close_d
        out[f"QTLD{w}"] = roll_close.quantile(0.2) / close_d
        out[f"RSV{w}"] = (close - window_low) / (window_high - window_low + EPS)
        out[f"CORR{w}"] = roll_close.corr(log_volume)
        out[f"CNTP{w}"] = (close > close.shift(1)).astype(float).rolling(w).mean()
        out[f"CNTN{w}"] = (close < close.shift(1)).astype(float).rolling(w).mean()

        sump = gain.rolling(w).sum() / (abs_change.rolling(w).sum() + EPS)
        out[f"SUMP{w}"] = sump
        out[f"SUMN{w}"] = 1.0 - sump

        out[f"VMA{w}"] = volume.rolling(w).mean() / volume_d
        out[f"VSTD{w}"] = volume.rolling(w).std() / volume_d

    features = pd.DataFrame(out, index=frame.index)
    # rolling.corr 은 분산이 0 인 구간에서 ±inf 를 뱉기도 한다. 정의되지 않은
    # 값은 NaN 으로 통일한다 — 하류 모델이 inf 를 만나지 않게.
    return features.replace([np.inf, -np.inf], np.nan)

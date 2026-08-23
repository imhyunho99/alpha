"""점 시점(point-in-time) 신호 테이블.

백테스트에서 `predict_proba_with_global_model` 을 그대로 prob_fn 으로 쓰면 두 가지가 깨진다.

1. **Look-ahead 편향.** 그 함수는 `load_data(ticker).tail(1)` 로 항상 최신 데이터를 본다.
   2024년 시점을 밟고 있는 백테스트 스텝이 2026년 데이터로 예측하게 되므로,
   나오는 곡선은 미래를 보고 매매한 결과다.
2. **비용.** 호출마다 11MB 모델을 joblib.load 한다. 1,250스텝 × 118종목이면 15만 번이다.

여기서는 모델을 한 번만 로드하고, 티커별로 전체 기간의 피처를 한 번에 예측해
`날짜 → 확률` 시리즈를 만들어 둔다. 엔진은 조회만 한다.
조회는 언제나 `at` **이하**의 마지막 값만 보므로 미래를 참조하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

# scoring_engine 의 horizon 이름과 글로벌 모델 파일 이름의 대응
_MODEL_HORIZON = {"short": "short", "medium": "mid", "long": "long"}


def _as_utc_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    return out


def _probability_series(ticker: str, frame: pd.DataFrame, bundle, metadata=None) -> pd.Series:
    """전체 기간의 상승 확률. 각 행은 그 시점까지의 정보만으로 만들어진 피처다."""
    from ..global_model_handler import create_global_features_and_target

    model, feature_columns, encoder, cat_cols = bundle

    if metadata is None:
        from ..market_features import get_ticker_metadata

        try:
            metadata = get_ticker_metadata([ticker])
        except Exception:
            metadata = {}

    features, _ = create_global_features_and_target(ticker, frame, metadata, target_days=1)
    if features.empty:
        return pd.Series(dtype="float64")

    rows = features.drop(columns=["Ticker"], errors="ignore")
    try:
        if cat_cols:
            rows[cat_cols] = encoder.transform(rows[cat_cols])
        rows = rows[feature_columns]
    except Exception:
        return pd.Series(dtype="float64")

    rows = rows.dropna()
    if rows.empty:
        return pd.Series(dtype="float64")

    try:
        proba = model.predict_proba(rows)
    except Exception:
        return pd.Series(dtype="float64")

    classes = list(getattr(model, "classes_", [0, 1]))
    if 1 not in classes:
        return pd.Series(dtype="float64")

    return pd.Series(np.asarray(proba)[:, classes.index(1)], index=rows.index)


def _score_series(frame: pd.DataFrame, prob: pd.Series) -> pd.Series:
    """scoring_engine 의 medium 공식을 시계열로 편 것.

        trend_strength = sma20/sma50 - 1
        trend_score    = clip(trend_strength * 5, -1, 1) * 50 + 50
        medium         = trend_score * 0.5 + (ai * 50 + 50) * 0.5     (ai ∈ {-1, +1})

    rolling 창은 본래 뒤만 보므로 이 계산에는 미래 정보가 들어가지 않는다.
    """
    close = frame["Close"]
    sma_20 = close.rolling(window=20, min_periods=20).mean()
    sma_50 = close.rolling(window=50, min_periods=50).mean()

    trend_strength = (sma_20 / sma_50.replace(0, np.nan)) - 1
    trend_score = np.clip(trend_strength * 5, -1, 1) * 50 + 50

    ai = prob.reindex(close.index).apply(lambda p: 1 if pd.notna(p) and p >= 0.5 else -1)
    return (trend_score * 0.5) + ((ai * 50 + 50) * 0.5)


@dataclass
class SignalTable:
    """티커별 확률·점수 시리즈. 조회는 항상 at 이하의 마지막 값."""

    probabilities: dict[str, pd.Series] = field(default_factory=dict)
    scores: dict[str, pd.Series] = field(default_factory=dict)

    @staticmethod
    def _lookup(series: pd.Series | None, at: datetime) -> float | None:
        if series is None or series.empty:
            return None
        window = series.loc[series.index <= at]
        if window.empty:
            return None
        value = window.iloc[-1]
        return None if pd.isna(value) else float(value)

    def prob_at(self, ticker: str, at: datetime) -> float | None:
        return self._lookup(self.probabilities.get(ticker), at)

    def score_at(self, ticker: str, at: datetime) -> float | None:
        return self._lookup(self.scores.get(ticker), at)

    def prob_fn_for(self, clock):
        """engine.step() 이 기대하는 prob_fn(ticker, horizon) 으로 감싼다."""
        return lambda ticker, horizon: self.prob_at(ticker, clock.now())

    def score_fn_for(self, clock):
        return lambda ticker, horizon: self.score_at(ticker, clock.now())


def build_signal_table(
    frames: dict[str, pd.DataFrame], horizon: str = "medium"
) -> SignalTable:
    """모델을 한 번만 로드해 모든 티커의 신호 시리즈를 만든다."""
    from ..global_model_predictor import _load_model_and_features

    bundle = _load_model_and_features(_MODEL_HORIZON.get(horizon, "mid"))
    if bundle is None:
        raise RuntimeError(
            f"글로벌 모델({_MODEL_HORIZON.get(horizon, 'mid')})이 없습니다. 먼저 학습하세요."
        )

    # 깨진 시계열은 신호를 만들기 전에 버린다. USDE-USD 처럼 20일 수익률이
    # +4,799,376% 로 찍히는 종목이 실제로 있고, 그대로 두면 자동 운용이 그걸 산다.
    from ..data_quality import filter_frames

    frames = filter_frames(frames)

    # 메타데이터는 티커마다 네트워크를 타므로 한 번에 받는다. 종목별로 부르면
    # 125종목 백테스트가 125번 왕복한다.
    from ..market_features import get_ticker_metadata

    try:
        metadata = get_ticker_metadata(list(frames))
    except Exception as exc:
        print(f"메타데이터 일괄 조회 실패, 종목별 폴백합니다: {exc}")
        metadata = None

    table = SignalTable()
    for ticker, raw in frames.items():
        if raw is None or raw.empty or "Close" not in raw.columns:
            continue
        frame = _as_utc_index(raw)
        prob = _probability_series(ticker, frame, bundle, metadata)
        if prob.empty:
            continue
        table.probabilities[ticker] = prob
        table.scores[ticker] = _score_series(frame, prob).dropna()
    return table

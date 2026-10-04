"""규칙 기반 팩터 신호. 학습하지 않으므로 과적합할 파라미터가 없다.

engine.step 이 받는 두 신호 모양(SignalTable)으로 만든다.
  - probabilities: 진입 자격. 추세 필터를 통과하면 1.0, 아니면 0.0
    (온도의 min_confidence 가 0~1 사이라 1.0 은 언제나 통과, 0.0 은 언제나 탈락)
  - scores: 순위. 높을수록 먼저 담는다.

근거 (값은 문헌 표준을 그대로 쓴다 — 우리 데이터로 고르지 않는다):
  - 12-1 모멘텀: 지난 12개월 수익률에서 최근 1개월을 뺀 것(Jegadeesh & Titman 1993).
    최근 1개월은 단기 반전이라 뺀다. 이 레포 측정에서도 20일 모멘텀은 역방향이었다.
  - 저변동성: 변동성이 낮은 종목이 위험 대비 수익이 높다(Baker, Bradley & Wurgler 2011).
  - 위험조정 모멘텀: 모멘텀 ÷ 변동성(Barroso & Santa-Clara 2015 의 변동성 조정 취지).
  - 추세 필터: 가격이 200일 이동평균 아래면 보유하지 않는다(Faber 2007).

모든 계산은 rolling/shift 로 그 시점까지의 값만 쓴다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .signals import SignalTable, _as_utc_index

LOOKBACK = 252        # 12개월
SKIP = 21             # 최근 1개월 제외
VOL_WINDOW = 252
TREND_WINDOW = 200

KINDS = ("risk_adj_momentum", "momentum", "low_vol")


def _series(close: pd.Series, kind: str) -> tuple[pd.Series, pd.Series]:
    close = close.astype(float)
    sma = close.rolling(TREND_WINDOW, min_periods=TREND_WINDOW).mean()
    eligible = (close > sma).astype(float).where(sma.notna())

    mom = close.shift(SKIP) / close.shift(LOOKBACK) - 1.0
    vol = close.pct_change().rolling(VOL_WINDOW, min_periods=VOL_WINDOW // 2).std() * np.sqrt(252)
    vol = vol.where(vol > 0)

    if kind == "momentum":
        score = mom
    elif kind == "low_vol":
        score = -vol
    elif kind == "risk_adj_momentum":
        score = mom / vol
    else:
        raise ValueError(f"알 수 없는 팩터: {kind}")
    return eligible, score


def build_factor_table(frames: dict[str, pd.DataFrame], kind: str = "risk_adj_momentum") -> SignalTable:
    from ..data_quality import filter_frames

    frames = filter_frames(frames)   # 깨진 시계열(수백만 % 수익률)은 모델 경로와 똑같이 버린다
    table = SignalTable()
    for ticker, frame in frames.items():
        if frame is None or frame.empty or "Close" not in frame:
            continue
        frame = _as_utc_index(frame)
        eligible, score = _series(frame["Close"], kind)
        table.probabilities[ticker] = eligible.dropna()
        table.scores[ticker] = score.replace([np.inf, -np.inf], np.nan).dropna()
    return table

"""점 시점 신호 테이블 — look-ahead 방지가 핵심."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from alpha_server.autopilot import signals
from alpha_server.autopilot.signals import SignalTable


def _series(dates, values):
    return pd.Series(values, index=pd.DatetimeIndex(dates, tz="UTC"))


def test_lookup_never_sees_the_future():
    s = _series(["2026-01-01", "2026-06-01", "2026-12-01"], [0.1, 0.9, 0.2])
    table = SignalTable(probabilities={"A": s})

    # 6/15 에는 6/1 값을 본다. 12/1 값은 아직 존재하지 않아야 한다.
    assert table.prob_at("A", datetime(2026, 6, 15, tzinfo=timezone.utc)) == pytest.approx(0.9)
    assert table.prob_at("A", datetime(2026, 1, 2, tzinfo=timezone.utc)) == pytest.approx(0.1)


def test_lookup_returns_none_before_series_starts():
    s = _series(["2026-06-01"], [0.9])
    table = SignalTable(probabilities={"A": s})
    assert table.prob_at("A", datetime(2026, 1, 1, tzinfo=timezone.utc)) is None


def test_lookup_returns_none_for_unknown_ticker():
    assert SignalTable().prob_at("NOPE", datetime(2026, 1, 1, tzinfo=timezone.utc)) is None


def test_lookup_skips_nan():
    s = _series(["2026-01-01"], [float("nan")])
    table = SignalTable(probabilities={"A": s})
    assert table.prob_at("A", datetime(2026, 1, 2, tzinfo=timezone.utc)) is None


def test_fn_adapters_follow_the_clock():
    s = _series(["2026-01-01", "2026-06-01"], [0.1, 0.9])
    table = SignalTable(probabilities={"A": s})

    class _Clock:
        def __init__(self, at):
            self.at = at

        def now(self):
            return self.at

    clock = _Clock(datetime(2026, 1, 2, tzinfo=timezone.utc))
    prob_fn = table.prob_fn_for(clock)
    assert prob_fn("A", "medium") == pytest.approx(0.1)

    # 시계가 움직이면 같은 함수가 다른 값을 돌려준다
    clock.at = datetime(2026, 6, 2, tzinfo=timezone.utc)
    assert prob_fn("A", "medium") == pytest.approx(0.9)


def test_score_series_is_backward_looking_only():
    idx = pd.date_range("2026-01-01", periods=80, freq="D", tz="UTC")
    frame = pd.DataFrame({"Close": np.linspace(100.0, 200.0, 80)}, index=idx)
    prob = pd.Series(0.9, index=idx)

    score = signals._score_series(frame, prob)

    # sma50 이 채워지기 전 구간은 값이 없어야 한다
    assert score.iloc[:49].isna().all()
    # 상승 추세 + 상승 확률이면 중립(50)보다 높다
    assert score.dropna().iloc[-1] > 50.0


def test_build_signal_table_skips_empty_frames(monkeypatch):
    monkeypatch.setattr(
        signals, "_probability_series", lambda ticker, frame, bundle: pd.Series(dtype="float64")
    )
    monkeypatch.setattr(
        "alpha_server.global_model_predictor._load_model_and_features",
        lambda horizon: ("model", ["f"], None, []),
    )
    idx = pd.date_range("2026-01-01", periods=3, freq="D", tz="UTC")
    frames = {
        "EMPTY": pd.DataFrame(),
        "A": pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx),
    }
    table = signals.build_signal_table(frames)
    assert table.probabilities == {}


def test_build_signal_table_raises_without_model(monkeypatch):
    monkeypatch.setattr(
        "alpha_server.global_model_predictor._load_model_and_features", lambda horizon: None
    )
    with pytest.raises(RuntimeError, match="글로벌 모델"):
        signals.build_signal_table({})

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
        signals,
        "_probability_series",
        lambda ticker, frame, bundle, metadata=None: pd.Series(dtype="float64"),
    )
    monkeypatch.setattr(
        "alpha_server.global_model_predictor._load_model_and_features",
        lambda horizon: ("model", ["f"], None, []),
    )
    monkeypatch.setattr(
        "alpha_server.market_features.get_ticker_metadata", lambda tickers, *a, **k: {}
    )
    idx = pd.date_range("2024-01-01", periods=400, freq="B", tz="UTC")
    rng = np.random.default_rng(4)
    frames = {
        "EMPTY": pd.DataFrame(),
        "A": pd.DataFrame(
            {"Close": 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, 400))}, index=idx
        ),
    }
    table = signals.build_signal_table(frames)
    assert table.probabilities == {}


def test_build_signal_table_raises_without_model(monkeypatch):
    monkeypatch.setattr(
        "alpha_server.global_model_predictor._load_model_and_features", lambda horizon: None
    )
    with pytest.raises(RuntimeError, match="글로벌 모델"):
        signals.build_signal_table({})


def test_build_signal_table_fetches_metadata_once(monkeypatch):
    """티커마다 부르면 125종목 백테스트가 네트워크를 125번 왕복한다."""
    calls = []

    def counting(tickers, *a, **k):
        calls.append(list(tickers))
        return {t: {} for t in tickers}

    monkeypatch.setattr("alpha_server.market_features.get_ticker_metadata", counting)
    monkeypatch.setattr(
        "alpha_server.global_model_predictor._load_model_and_features",
        lambda horizon: ("model", ["f"], None, []),
    )
    seen = []

    def fake_prob(ticker, frame, bundle, metadata=None):
        seen.append((ticker, metadata))
        return pd.Series([0.6], index=pd.DatetimeIndex(["2026-01-01"], tz="UTC"))

    monkeypatch.setattr(signals, "_probability_series", fake_prob)

    # 위생 검사를 통과할 만큼 긴 시계열이어야 한다 (최소 260행)
    idx = pd.date_range("2024-01-01", periods=400, freq="B", tz="UTC")
    rng = np.random.default_rng(2)
    frames = {
        t: pd.DataFrame(
            {"Close": 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, 400))}, index=idx
        )
        for t in ("A", "B", "C")
    }
    signals.build_signal_table(frames)

    assert len(calls) == 1                 # 네트워크는 한 번만
    assert set(calls[0]) == {"A", "B", "C"}
    assert all(m is not None for _, m in seen)  # 같은 메타데이터를 재사용


def test_build_signal_table_drops_corrupt_series(monkeypatch):
    """USDE-USD 같은 깨진 시계열이 신호로 넘어가면 자동 운용이 그걸 산다."""
    monkeypatch.setattr(
        "alpha_server.global_model_predictor._load_model_and_features",
        lambda horizon: ("model", ["f"], None, []),
    )
    monkeypatch.setattr(
        "alpha_server.market_features.get_ticker_metadata", lambda tickers, *a, **k: {}
    )

    seen = []

    def fake_prob(ticker, frame, bundle, metadata=None):
        seen.append(ticker)
        return pd.Series([0.6], index=pd.DatetimeIndex(["2026-01-01"], tz="UTC"))

    monkeypatch.setattr(signals, "_probability_series", fake_prob)

    idx = pd.date_range("2024-01-01", periods=400, freq="B")
    rng = np.random.default_rng(5)
    good = pd.DataFrame({"Close": 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, 400))}, index=idx)

    broken = good.copy()
    broken.iloc[200, 0] *= 5000.0
    broken.iloc[201, 0] /= 5000.0

    table = signals.build_signal_table({
        "GOOD": good,
        "BROKEN": broken,
        "USDE-USD": good.copy(),   # 스테이블코인
    })

    assert seen == ["GOOD"]
    assert set(table.probabilities) == {"GOOD"}

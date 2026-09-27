"""손실 기반 가중치 — 신호 신뢰도, 종목 비중, 전체 투자 비중."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.newsdesk import weights as w
from alpha_server.newsdesk.weights import SignalRecord, WeightState

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)


def _state() -> WeightState:
    return WeightState()


def _after(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


# ── 신호별 신뢰도 ────────────────────────────────────────────────


def test_trust_defaults_to_one():
    assert _state().trust_for("news:earnings") == 1.0


def test_winning_buy_raises_trust_by_exp_of_edge():
    s = _state()
    w.record_signal(s, "news:earnings", "AAPL", 1, 100.0, T0)
    out = w.settle(s, {"AAPL": 110.0}, _after(72))

    edge = 0.10 - w.ROUND_TRIP_COST
    assert s.trust_for("news:earnings") == pytest.approx(math.exp(w.LEARNING_RATE * edge))
    assert s.pending == []
    assert len(out) == 1
    assert out[0]["edge"] == pytest.approx(edge)
    assert out[0]["before"] == 1.0
    assert out[0]["after"] == pytest.approx(s.trust_for("news:earnings"))
    assert out[0]["key"] == "news:earnings" and out[0]["ticker"] == "AAPL"


def test_sell_signal_gains_trust_when_price_falls():
    s = _state()
    w.record_signal(s, "news:regulation", "TSLA", -1, 200.0, T0)
    w.settle(s, {"TSLA": 180.0}, _after(72))
    assert s.trust_for("news:regulation") > 1.0


def test_sell_signal_loses_trust_when_price_rises():
    s = _state()
    w.record_signal(s, "news:regulation", "TSLA", -1, 200.0, T0)
    w.settle(s, {"TSLA": 220.0}, _after(72))
    assert s.trust_for("news:regulation") < 1.0


def test_flat_price_still_costs_trust_slightly():
    # 가격이 그대로면 수수료·슬리피지만큼 손해 → 신뢰도가 조금 내려가야 한다.
    s = _state()
    w.record_signal(s, "model", "MSFT", 1, 300.0, T0)
    w.settle(s, {"MSFT": 300.0}, _after(72))
    t = s.trust_for("model")
    assert t < 1.0
    assert t == pytest.approx(math.exp(-w.LEARNING_RATE * w.ROUND_TRIP_COST))
    assert t > 0.98


def test_trust_is_clipped_at_upper_bound():
    s = _state()
    w.record_signal(s, "news:product", "NVDA", 1, 100.0, T0)
    w.settle(s, {"NVDA": 300.0}, _after(72))
    assert s.trust_for("news:product") == w.TRUST_MAX


def test_trust_is_clipped_at_lower_bound():
    s = _state()
    w.record_signal(s, "news:legal", "BA", 1, 100.0, T0)
    w.settle(s, {"BA": 10.0}, _after(72))
    assert s.trust_for("news:legal") == w.TRUST_MIN


def test_clip_holds_over_repeated_losses():
    s = _state()
    for i in range(20):
        at = T0 + timedelta(days=4 * i)
        w.record_signal(s, "k", "X", 1, 100.0, at)
        w.settle(s, {"X": 80.0}, at + timedelta(hours=72))
    assert s.trust_for("k") == w.TRUST_MIN


def test_settle_before_horizon_does_nothing():
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 100.0, T0)
    out = w.settle(s, {"AAPL": 150.0}, _after(71.99))
    assert out == []
    assert len(s.pending) == 1
    assert s.trust == {}
    assert s.history == []


def test_settle_exactly_at_horizon_evaluates():
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 100.0, T0, horizon_hours=24)
    assert len(w.settle(s, {"AAPL": 101.0}, _after(24))) == 1


def test_missing_price_keeps_signal_pending():
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 100.0, T0)
    w.record_signal(s, "k", "005930.KS", 1, 70000.0, T0)
    out = w.settle(s, {"AAPL": 110.0}, _after(80))

    assert [r["ticker"] for r in out] == ["AAPL"]
    assert [p.ticker for p in s.pending] == ["005930.KS"]
    # 다음 settle 에 가격이 생기면 그때 평가한다.
    out2 = w.settle(s, {"005930.KS": 77000.0}, _after(90))
    assert [r["ticker"] for r in out2] == ["005930.KS"]
    assert s.pending == []


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan")])
def test_unusable_price_keeps_signal_pending(bad):
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 100.0, T0)
    assert w.settle(s, {"AAPL": bad}, _after(80)) == []
    assert len(s.pending) == 1


def test_duplicate_within_horizon_is_not_recorded():
    s = _state()
    w.record_signal(s, "news:earnings", "AAPL", 1, 100.0, T0)
    w.record_signal(s, "news:earnings", "AAPL", 1, 105.0, _after(10))
    assert len(s.pending) == 1
    assert s.pending[0].price == 100.0


def test_same_key_other_ticker_or_other_key_is_recorded():
    s = _state()
    w.record_signal(s, "news:earnings", "AAPL", 1, 100.0, T0)
    w.record_signal(s, "news:earnings", "MSFT", 1, 300.0, T0)
    w.record_signal(s, "news:product", "AAPL", 1, 100.0, T0)
    assert len(s.pending) == 3


def test_recording_again_after_horizon_passed_is_allowed():
    # 만기가 지났지만 가격이 없어 아직 pending 인 신호는 새 기록을 막지 않는다.
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 100.0, T0, horizon_hours=24)
    w.record_signal(s, "k", "AAPL", 1, 110.0, _after(30), horizon_hours=24)
    assert len(s.pending) == 2


def test_invalid_record_inputs_are_ignored():
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 0.0, T0)
    w.record_signal(s, "k", "AAPL", 1, float("nan"), T0)
    assert s.pending == []
    with pytest.raises(ValueError):
        w.record_signal(s, "k", "AAPL", 0, 100.0, T0)


def test_history_keeps_last_100():
    s = _state()
    for i in range(130):
        w.record_signal(s, "k", f"T{i}", 1, 100.0, T0)
    w.settle(s, {f"T{i}": 100.0 for i in range(130)}, _after(72))
    assert len(s.history) == 100
    assert s.history[-1]["ticker"] == "T129"
    assert s.history[0]["ticker"] == "T30"


def test_settle_result_is_json_serializable():
    s = _state()
    w.record_signal(s, "k", "AAPL", 1, 100.0, T0)
    out = w.settle(s, {"AAPL": 101.0}, _after(72))
    json.dumps(out)
    assert datetime.fromisoformat(out[0]["at"]) == _after(72)


# ── 직렬화 ───────────────────────────────────────────────────────


def test_to_dict_from_dict_round_trip_with_datetime():
    s = _state()
    w.record_signal(s, "news:earnings", "AAPL", 1, 100.0, T0)
    w.record_signal(s, "news:regulation", "005930.KS", -1, 70000.0, T0, horizon_hours=24)
    w.settle(s, {"005930.KS": 69000.0}, _after(30))
    w.update_peak(s, 12_345.0)

    d = s.to_dict()
    restored = WeightState.from_dict(json.loads(json.dumps(d)))

    assert restored == s
    assert isinstance(restored.pending[0], SignalRecord)
    assert restored.pending[0].at == T0
    assert restored.pending[0].at.tzinfo is not None
    assert restored.peak_equity == 12_345.0


def test_from_dict_tolerates_empty_or_partial_input():
    assert WeightState.from_dict({}) == WeightState()
    s = WeightState.from_dict({"trust": {"model": 1.5}})
    assert s.trust_for("model") == 1.5
    assert s.pending == [] and s.peak_equity == 0.0


# ── 종목별 비중 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pnl, expected",
    [
        (0.0, 1.0),
        (-10.0, 0.6),
        (-5.0, 0.8),
        (-18.75, 0.25),
        (-30.0, 0.25),
        (-100.0, 0.25),
        (8.0, 1.2),
        (10.0, 1.25),
        (40.0, 1.25),
    ],
)
def test_ticker_multiplier(pnl, expected):
    assert w.ticker_multiplier(pnl) == pytest.approx(expected)


def test_ticker_multiplier_nan_is_neutral():
    assert w.ticker_multiplier(float("nan")) == 1.0


# ── 전체 투자 비중 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "equity, expected",
    [
        (110.0, 1.0),    # 고점 위(아직 update_peak 전) → 낙폭 없음
        (100.0, 1.0),    # 낙폭 0
        (95.0, 1.0),     # soft 경계
        (90.0, 0.625),   # soft~hard 중간: 1 - 0.5*(1-0.25)
        (85.0, 0.25),    # hard 경계
        (50.0, 0.25),    # hard 넘어감
    ],
)
def test_exposure_multiplier_bands(equity, expected):
    assert w.exposure_multiplier(equity, 100.0, 5.0, 15.0) == pytest.approx(expected)


def test_exposure_multiplier_custom_floor():
    assert w.exposure_multiplier(80.0, 100.0, 5.0, 15.0, floor=0.5) == 0.5


def test_exposure_multiplier_without_peak_is_full():
    assert w.exposure_multiplier(100.0, 0.0, 5.0, 15.0) == 1.0
    assert w.exposure_multiplier(100.0, -5.0, 5.0, 15.0) == 1.0


def test_exposure_multiplier_degenerate_band_does_not_divide_by_zero():
    assert w.exposure_multiplier(95.0, 100.0, 5.0, 5.0) == 1.0
    assert w.exposure_multiplier(94.0, 100.0, 5.0, 5.0) == 0.25
    assert w.exposure_multiplier(94.0, 100.0, 10.0, 5.0) == 1.0


def test_exposure_recovers_without_state():
    assert w.exposure_multiplier(88.0, 100.0, 5.0, 15.0) < 1.0
    assert w.exposure_multiplier(99.0, 100.0, 5.0, 15.0) == 1.0


def test_update_peak_only_moves_up():
    s = _state()
    w.update_peak(s, 100.0)
    assert s.peak_equity == 100.0
    w.update_peak(s, 90.0)
    assert s.peak_equity == 100.0
    w.update_peak(s, 120.0)
    assert s.peak_equity == 120.0

"""규칙 기반 팩터 신호(autopilot/factors.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_server.autopilot import factors as F


def _frame(values, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="B", tz="UTC")
    return pd.DataFrame({"Close": values}, index=idx)


def _trend(n=400, daily=0.001, noise=0.01, seed=0):
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + daily + rng.normal(0, noise, n))


def test_no_signal_before_lookback_is_available():
    table = F.build_factor_table({"A": _frame(_trend())}, "risk_adj_momentum")
    first_score = table.scores["A"].index[0]
    first_elig = table.probabilities["A"].index[0]
    idx = _frame(_trend()).index
    assert first_score >= idx[F.LOOKBACK]
    assert first_elig >= idx[F.TREND_WINDOW - 1]


def test_signals_do_not_change_when_future_data_is_appended():
    """미래를 보지 않는다: 뒤에 데이터를 더 붙여도 과거 시점의 신호는 같다."""
    full = _frame(_trend(500, seed=1))
    cut = full.iloc[:420]
    for kind in F.KINDS:
        a = F.build_factor_table({"A": cut}, kind)
        b = F.build_factor_table({"A": full}, kind)
        common = a.scores["A"].index
        pd.testing.assert_series_equal(a.scores["A"], b.scores["A"].loc[common])
        pd.testing.assert_series_equal(a.probabilities["A"], b.probabilities["A"].loc[a.probabilities["A"].index])


def test_trend_filter_blocks_falling_stock():
    falling = F.build_factor_table({"D": _frame(_trend(400, daily=-0.002, noise=0.005))})
    rising = F.build_factor_table({"U": _frame(_trend(400, daily=0.002, noise=0.005))})
    assert falling.probabilities["D"].iloc[-1] == 0.0
    assert rising.probabilities["U"].iloc[-1] == 1.0


def test_momentum_ranks_stronger_trend_higher_and_low_vol_prefers_calm():
    strong = _frame(_trend(400, daily=0.003, noise=0.01, seed=2))
    weak = _frame(_trend(400, daily=0.0005, noise=0.01, seed=3))
    mom = F.build_factor_table({"S": strong, "W": weak}, "momentum")
    assert mom.scores["S"].iloc[-1] > mom.scores["W"].iloc[-1]
    calm = _frame(_trend(400, daily=0.001, noise=0.003, seed=4))
    wild = _frame(_trend(400, daily=0.001, noise=0.03, seed=5))
    lv = F.build_factor_table({"C": calm, "X": wild}, "low_vol")
    assert lv.scores["C"].iloc[-1] > lv.scores["X"].iloc[-1]


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        F.build_factor_table({"A": _frame(_trend())}, "magic")


def test_live_signal_defaults_to_ml_and_factor_is_opt_in():
    from alpha_server.autopilot import runner

    assert runner.signal_kind({}) == "ml"
    assert runner.signal_kind({"signal": "factor"}) == "factor"
    assert runner.signal_kind({"signal": "junk"}) == "ml"


def test_factor_table_is_built_once_per_day(monkeypatch):
    from datetime import date

    from alpha_server.autopilot import runner

    calls = []
    monkeypatch.setattr(runner, "_local_frames", lambda t: calls.append(t) or {"A": _frame(_trend())})
    runner._factor_cache.clear()
    a = runner._factor_table(["A"], date(2026, 10, 4))
    b = runner._factor_table(["A"], date(2026, 10, 4))
    c = runner._factor_table(["A"], date(2026, 10, 5))
    assert a is b and a is not c and len(calls) == 2


def test_engine_buys_only_eligible_ranked_names():
    """팩터 신호를 그대로 엔진에 넣으면 추세 필터를 통과한 종목만 점수 순으로 산다."""
    from datetime import datetime, timezone

    from alpha_server.autopilot.account import PaperAccount
    from alpha_server.autopilot.engine import step
    from alpha_server.autopilot.journal import Journal
    from alpha_server.autopilot.temperature import profile_for

    frames = {
        "UP1": _frame(_trend(400, daily=0.003, noise=0.005, seed=1)),
        "UP2": _frame(_trend(400, daily=0.001, noise=0.005, seed=2)),
        "DOWN": _frame(_trend(400, daily=-0.002, noise=0.005, seed=3)),
    }
    table = F.build_factor_table(frames)
    at = frames["UP1"].index[-1].to_pydatetime()

    class Clock:
        def now(self):
            return at

    class Prices:
        def get_many(self, tickers, when):
            return {t: float(frames[t]["Close"].iloc[-1]) for t in tickers if t in frames}

    acct = PaperAccount(cash=10_000_000)
    step(acct, profile_for(5), list(frames), Prices(), Clock(), Journal(mirror_audit=False),
         lambda t, h: table.prob_at(t, at), lambda t, h: table.score_at(t, at), "medium", None)
    assert "DOWN" not in acct.positions
    assert {"UP1", "UP2"} <= set(acct.positions)

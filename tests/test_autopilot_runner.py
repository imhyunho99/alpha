"""Autopilot Runner / Store 테스트 (Task 10).

백테스트 루프와 사용자별 상태 영속화.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from alpha_server.autopilot.account import PaperAccount, Position
from alpha_server.autopilot.runner import run_backtest


def _rising_frame(days, start_price, daily_pct):
    idx = pd.date_range("2026-01-01", periods=days, freq="D", tz="UTC")
    closes = [start_price * ((1 + daily_pct) ** i) for i in range(days)]
    return pd.DataFrame({"Close": closes}, index=idx)


def test_backtest_produces_curve_and_grows_in_uptrend():
    frames = {"A": _rising_frame(60, 100.0, 0.01)}
    result = run_backtest(
        temperature=5, initial_capital=10_000_000.0, frames=frames,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 20, tzinfo=timezone.utc),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 90.0,
        horizon="medium", rates=1.0,
    )
    assert len(result.curve) > 10
    assert result.final_equity > 10_000_000.0
    assert result.max_drawdown_pct >= 0.0


def test_backtest_records_liquidation_in_crash_with_leverage():
    # 급락장: 3x 레버리지면 청산되어야 한다.
    # 종목이 하나뿐이면 max_position_pct(15%) 상한에 걸려 차입이 아예 안 생긴다.
    # 레버리지를 실제로 태우려면 max_holdings 만큼의 후보가 필요하다.
    idx = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    closes = [100.0] * 5 + [100.0 * (0.85 ** i) for i in range(35)]
    frames = {
        f"T{i}": pd.DataFrame({"Close": closes}, index=idx)
        for i in range(20)
    }

    result = run_backtest(
        temperature=10, initial_capital=10_000_000.0, frames=frames,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 9, tzinfo=timezone.utc),
        prob_fn=lambda t, h: 0.99, score_fn=lambda t, h: 99.0,
        horizon="medium", rates=1.0,
    )
    assert result.liquidated_at is not None
    assert result.final_equity < 10_000_000.0


def test_backtest_is_deterministic():
    frames = {"A": _rising_frame(40, 100.0, 0.005)}
    kwargs = dict(
        temperature=5, initial_capital=10_000_000.0, frames=frames,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 9, tzinfo=timezone.utc),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 90.0,
        horizon="medium", rates=1.0,
    )
    a = run_backtest(**kwargs)
    b = run_backtest(**kwargs)
    assert a.final_equity == b.final_equity
    assert len(a.curve) == len(b.curve)


def test_store_roundtrips_config_and_account(tmp_path, monkeypatch):
    from alpha_server.autopilot import store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 7, "capital": 10_000_000.0, "active": True})
    assert store.load_config("kim")["temperature"] == 7

    acct = PaperAccount(cash=5_000_000.0)
    acct.positions["A"] = Position("A", 10.0, 1000.0)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save_account("kim", acct, at)

    loaded, last = store.load_account("kim")
    assert loaded.cash == 5_000_000.0
    assert loaded.positions["A"].quantity == 10.0
    assert last == at

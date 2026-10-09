"""실계좌 자산배분 전략(SPY/IEF)과 연동·위험 상한."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.autopilot import allocation as A
from alpha_server.autopilot.account import PaperAccount

AT = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
PRICES = {"SPY": 900_000.0, "IEF": 130_000.0}


def test_temperature_maps_to_stock_share():
    assert A.stock_share(1) == 0.2 and A.stock_share(5) == 0.6 and A.stock_share(9) == 1.0
    assert A.targets(5) == {"SPY": 0.6, "IEF": 0.4} and A.targets(10) == {"SPY": 1.0}


def test_cash_is_invested_to_targets():
    acct = PaperAccount(cash=1_000_000)
    fills, decisions = A.allocation_step(acct, PRICES, AT, 5, None)
    eq = acct.equity(PRICES)
    w_spy = acct.positions["SPY"].quantity * PRICES["SPY"] / eq
    assert w_spy == pytest.approx(0.6, abs=0.01) and acct.cash / eq < 0.01
    assert decisions and all(d["reason"].startswith("자산배분") for d in decisions)


def test_no_trade_inside_band_same_month_then_monthly_rebalance():
    acct = PaperAccount(cash=1_000_000)
    A.allocation_step(acct, PRICES, AT, 5, None)
    drift = {"SPY": 940_000.0, "IEF": 130_000.0}             # 조금 벗어남(<5%p)
    assert A.allocation_step(acct, drift, AT + timedelta(days=1), 5, AT)[0] == []
    assert A.allocation_step(acct, drift, AT + timedelta(days=30), 5, AT)[0]   # 달이 바뀌면


def test_big_drift_triggers_rebalance_and_foreign_names_are_sold():
    acct = PaperAccount(cash=1_000_000)
    A.allocation_step(acct, PRICES, AT, 5, None)
    crash = {"SPY": 600_000.0, "IEF": 130_000.0}             # 주식 급락 → 주식 비중 <55%
    assert A.needs_rebalance(acct, crash, AT + timedelta(days=1), 5, AT).startswith("SPY 비중")
    acct.buy("NVDA", 50_000, 250_000, {**PRICES, "NVDA": 250_000.0}, 1.0)
    fills, _ = A.allocation_step(acct, {**PRICES, "NVDA": 250_000.0}, AT + timedelta(days=1), 5, AT)
    assert "NVDA" not in acct.positions


def test_missing_price_means_no_trade():
    acct = PaperAccount(cash=1_000_000)
    assert A.allocation_step(acct, {"SPY": 900_000.0}, AT, 5, None) == ([], [])


def test_fund_cap_lets_spy_reach_60_percent_in_mirror():
    from alpha_server.autopilot.mirror import plan
    from alpha_server.risk_manager import RiskConfig, position_cap

    cfg = RiskConfig()
    orders = plan({"SPY": 0.6, "IEF": 0.4, "NVDA": 0.3}, {"total_value": 1_000_000, "positions": []},
                  {"SPY": 900_000.0, "IEF": 130_000.0, "NVDA": 250_000.0}, cfg.max_position_pct,
                  min_order=5_000, cap_fn=lambda t: position_cap(t, cfg))
    by = {o["ticker"]: o["amount_krw"] for o in orders}
    assert by["SPY"] == pytest.approx(600_000, rel=0.01) and by["IEF"] == pytest.approx(400_000, rel=0.01)
    assert by["NVDA"] == pytest.approx(100_000, rel=0.01)      # 개별 주식은 여전히 10%


def test_runner_uses_allocation_for_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from alpha_server.autopilot import store as ap
    from alpha_server.newsdesk import runner

    monkeypatch.setattr(ap, "STATE_DIR", str(tmp_path / "ap"))
    ap.save_config("u", {"temperature": 5, "capital": 1e6, "active": True, "mode": "news",
                         "strategy": "allocation", "shadow_of": "kb"}, "my-kb")
    ap.save_account("u", PaperAccount(cash=1_000_000), None, "my-kb")

    class P:
        def get_many(self, tickers, at):
            return {t: PRICES[t] for t in tickers if t in PRICES}

    r = runner.run_portfolio("u", "my-kb", [], P(), AT, ["NVDA"], None)
    acct, _ = ap.load_account("u", "my-kb")
    assert set(acct.positions) == {"SPY", "IEF"} and r.fills
    assert ap.load_intraday("u", "my-kb")

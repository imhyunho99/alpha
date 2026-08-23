"""Autopilot reporting 계층 테스트 — 브리핑과 긴급 알림."""
from __future__ import annotations

import pytest

from alpha_server.autopilot.account import PaperAccount, Position
from alpha_server.autopilot.journal import Journal
from alpha_server.autopilot.reporting import (
    ALERT_DRAWDOWN_PCT,
    build_briefing,
    check_alerts,
)


def test_alert_on_large_drawdown():
    acct = PaperAccount(cash=8_500_000.0)
    alerts = check_alerts(
        acct, prices={}, initial_capital=10_000_000.0, journal=Journal(mirror_audit=False)
    )
    codes = {a.code for a in alerts}
    assert "drawdown" in codes
    assert any(a.severity == "critical" for a in alerts)


def test_no_alert_within_threshold():
    acct = PaperAccount(cash=9_500_000.0)  # -5%
    alerts = check_alerts(
        acct, prices={}, initial_capital=10_000_000.0, journal=Journal(mirror_audit=False)
    )
    assert not any(a.code == "drawdown" for a in alerts)


def test_drawdown_threshold_is_the_documented_one():
    initial = 10_000_000.0
    just_over = PaperAccount(cash=initial * (1 - ALERT_DRAWDOWN_PCT / 100.0))
    alerts = check_alerts(just_over, {}, initial, Journal(mirror_audit=False))
    assert any(a.code == "drawdown" for a in alerts)


def test_alert_on_liquidation_event():
    j = Journal(mirror_audit=False)
    j.record("liquidation", count=3)
    alerts = check_alerts(PaperAccount(cash=1.0), {}, 1.0, j)
    assert any(a.code == "liquidation" for a in alerts)


def test_alert_on_high_leverage():
    acct = PaperAccount(cash=0.0, borrowed=2_000_000.0)
    acct.positions["AAPL"] = Position("AAPL", quantity=300, avg_price=10_000.0)
    prices = {"AAPL": 10_000.0}  # 평가 3,000,000 / equity 1,000,000 → 3.0배
    alerts = check_alerts(acct, prices, initial_capital=1_000_000.0, journal=Journal(mirror_audit=False))
    assert any(a.code == "leverage" and a.severity == "warning" for a in alerts)


def test_briefing_summarises_period():
    j = Journal(mirror_audit=False)
    j.record("buy", ticker="A", amount=100.0)
    j.record("buy", ticker="B", amount=200.0)
    j.record("exit", ticker="C", reason="stop_loss", quantity=1.0)

    acct = PaperAccount(cash=11_000_000.0)
    out = build_briefing(j.events, acct, prices={}, initial_capital=10_000_000.0, period="daily")

    assert out["period"] == "daily"
    assert out["buys"] == 2
    assert out["exits"] == 1
    assert out["return_pct"] == pytest.approx(10.0)
    assert "요약" in out["headline"] or out["headline"]


def test_briefing_with_no_events_says_so():
    out = build_briefing([], PaperAccount(cash=10_000_000.0), {}, 10_000_000.0, "weekly")
    assert out["buys"] == out["exits"] == out["trims"] == 0
    assert out["headline"]
    assert out["return_pct"] == pytest.approx(0.0)


def test_briefing_lists_holdings():
    acct = PaperAccount(cash=1_000_000.0)
    acct.positions["AAPL"] = Position("AAPL", quantity=1.5, avg_price=200_000.0)
    out = build_briefing([], acct, {"AAPL": 210_000.0}, 1_000_000.0, "daily")
    assert out["holdings"] == [
        {"ticker": "AAPL", "quantity": 1.5, "avg_price": 200_000.0}
    ]
    assert out["equity"] == pytest.approx(1_000_000.0 + 1.5 * 210_000.0)

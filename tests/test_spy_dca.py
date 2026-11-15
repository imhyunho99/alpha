"""SPY 분할 매수 전략 — autopilot/spy_dca.py, runner._spy_dca_result."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from alpha_server.autopilot import spy_dca as D
from alpha_server.autopilot.account import PaperAccount, Position

MON = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _ma_file(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "MA_FILE", str(tmp_path / "spy_ma40.json"))


class _Prices:
    def __init__(self, table):
        self.table = table

    def get_many(self, tickers, at):
        return {t: self.table[t] for t in tickers if t in self.table}


class _Journal:
    def __init__(self):
        self.events = []

    def record(self, kind, **kw):
        self.events.append((kind, kw))


def test_tranche_rules():
    assert D.tranche(20_000_000, 1) == 1_000_000                 # 2천만 → 주 100만(최소)
    assert D.tranche(52_000_000, 1) == 2_000_000                 # 큰 돈은 26주로
    assert D.tranche(600_000, 1) == 600_000                      # 적으면 한 번에
    assert D.tranche(20_000_000, 3) == 3_000_000                 # 밀린 주는 따라잡는다
    assert D.tranche(20_000_000, 10) == 4_000_000                # 최대 4주
    assert D.tranche(20_000_000, 0) == 0


def test_weeks_since():
    assert D.weeks_since(None, MON) == 1
    assert D.weeks_since(MON + timedelta(days=2), MON + timedelta(days=4)) == 0     # 같은 주
    assert D.weeks_since(MON - timedelta(days=1), MON) == 1                        # 일요일 → 월요일
    assert D.weeks_since(MON - timedelta(days=21), MON) == 3


def test_sells_satellites_and_buys_one_tranche_per_week():
    acc = PaperAccount(cash=20_000_000)
    acc.positions = {"SPY": Position("SPY", 1.0, 1_000_000.0), "HPE": Position("HPE", 2.0, 70_000.0)}
    table = {"SPY": 1_000_000.0, "HPE": 75_000.0}
    j = _Journal()
    fills, dec = D.dca_step(acc, table, MON, MON - timedelta(days=7), j)
    assert "HPE" not in acc.positions
    buys = [d for d in dec if d["action"] == "buy"]
    assert len(buys) == 1 and buys[0]["amount"] == pytest.approx(1_000_000)
    assert acc.cash > 19_000_000
    # 같은 주 다시 → 매수 없음
    fills2, dec2 = D.dca_step(acc, table, MON + timedelta(days=1), MON, j)
    assert fills2 == [] and dec2 == []
    assert j.events and j.events[0][0] == "spy_dca"


def test_missing_price_does_nothing():
    acc = PaperAccount(cash=1_000_000)
    acc.positions = {"HPE": Position("HPE", 2.0, 70_000.0)}
    assert D.dca_step(acc, {"SPY": 1_000_000.0}, MON, None) == ([], [])


def test_runner_dispatch():
    from alpha_server.newsdesk.runner import _spy_dca_result

    acc = PaperAccount(cash=500_000)
    res = _spy_dca_result(acc, _Prices({"SPY": 1_000_000.0}), MON, None, _Journal())
    assert res.skipped is None and acc.positions["SPY"].quantity > 0
    assert all("SPY 분할 매수" in d["reason"] for d in res.decisions)
    res = _spy_dca_result(PaperAccount(cash=500_000), _Prices({}), MON, None, _Journal())
    assert res.skipped == "시세 누락"


def _closes(last_drop: float):
    idx = pd.date_range("2025-06-01", "2026-11-13", freq="B")
    s = pd.Series(np.linspace(500, 750, len(idx)), index=idx)
    s.iloc[-1] *= last_drop
    return s


def test_ma40_alert_only_on_cross():
    sent = []
    send = lambda *a, **k: sent.append((a, k))
    r = D.ma40_check("u", "my-kb", MON, fetch=lambda: _closes(1.0), send=send)
    assert r["below"] is False and sent == []                      # 처음은 기준만 기록
    assert D.ma40_check("u", "my-kb", MON, fetch=lambda: _closes(0.5), send=send)["below"] is False  # 하루 한 번
    r = D.ma40_check("u", "my-kb", MON + timedelta(days=1), fetch=lambda: _closes(0.5), send=send)
    assert r["below"] is True and len(sent) == 1 and "이탈" in sent[0][0][1]
    D.ma40_check("u", "my-kb", MON + timedelta(days=2), fetch=lambda: _closes(0.5), send=send)
    assert len(sent) == 1                                          # 계속 아래면 다시 안 보냄


def test_ma40_fetch_failure_is_quiet():
    def boom():
        raise RuntimeError("x")
    assert D.ma40_check("u", "my-kb", MON, fetch=boom) is None

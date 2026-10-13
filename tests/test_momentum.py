"""추세 주식 전략(SPY 70 + 모멘텀 상위 10) — autopilot/momentum.py, runner._momentum_result."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from alpha_server.autopilot import momentum as M
from alpha_server.autopilot.account import PaperAccount, Position

NOW = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _picks_file(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "PICKS_FILE", str(tmp_path / "momentum_picks.json"))
    monkeypatch.setattr(M, "UNBUYABLE_FILE", str(tmp_path / "momentum_unbuyable.json"))


def test_scores_skip_current_and_last_month():
    """12-1: 지난달 말이 아니라 그 한 달 전 말 / 12개월 전 말. 진행 중인 달은 안 쓴다."""
    idx = pd.date_range("2025-08-01", "2026-10-10", freq="D")
    up = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    jump = up.copy()
    jump[jump.index >= "2026-09-01"] *= 3          # 지난 한 달(9월) 급등은 점수에 안 들어간다
    closes = pd.DataFrame({"UP": up, "JUMP": jump})
    s = M.scores_from_closes(closes, NOW)
    assert s["UP"] == pytest.approx(s["JUMP"])
    m = up.resample("ME").last()
    assert s["UP"] == pytest.approx(m[pd.Timestamp("2026-08-31")] / m[pd.Timestamp("2025-09-30")] - 1)


def test_picks_cached_per_month_and_fallback():
    calls = []

    def fetch(at):
        calls.append(at)
        return {f"T{i}": float(i) for i in range(30)}

    picks, src = M.picks_for(NOW, fetch)
    assert picks == [f"T{i}" for i in range(29, 19, -1)] and "새 선정" in src
    assert M.picks_for(NOW, fetch)[0] == picks and len(calls) == 1          # 같은 달은 다시 안 받는다

    def broken(at):
        raise RuntimeError("DNS")

    nxt = datetime(2026, 11, 3, tzinfo=timezone.utc)
    kept, src = M.picks_for(nxt, broken)
    assert kept == picks and "유지" in src                                  # 실패하면 지난달 선정
    assert M.picks_for(datetime(2026, 11, 4, tzinfo=timezone.utc), fetch)[1].endswith("새 선정")


def test_picks_without_history_fall_back_to_core_only():
    picks, src = M.picks_for(NOW, lambda at: (_ for _ in ()).throw(RuntimeError("x")))
    assert picks == [] and "SPY 만" in src
    assert M.targets(picks) == {"SPY": 1.0}


def test_targets_move_stopped_share_to_core():
    picks = [f"T{i}" for i in range(10)]
    tgt = M.targets(picks)
    assert tgt["SPY"] == pytest.approx(0.70) and tgt["T0"] == pytest.approx(0.03) and len(tgt) == 11
    tgt = M.targets(picks, {"T0", "T1"})
    assert "T0" not in tgt and tgt["SPY"] == pytest.approx(0.76)
    assert sum(tgt.values()) == pytest.approx(1.0)


def test_stop_loss_only_for_satellites_below_20pct():
    acc = PaperAccount(cash=0)
    acc.positions = {"A": Position("A", 1, 100.0), "B": Position("B", 1, 100.0), "SPY": Position("SPY", 1, 100.0)}
    assert M.stop_losses(acc, {"A": 79.0, "B": 81.0, "SPY": 50.0}, ["A", "B"]) == ["A"]


class _Prices:
    def __init__(self, table):
        self.table = table

    def get_many(self, tickers, now):
        return {t: self.table[t] for t in tickers if t in self.table}


class _Journal:
    def __init__(self):
        self.events = []

    def record(self, kind, **fields):
        self.events.append((kind, fields))
        return {}


def test_momentum_result_buys_core_and_ten_satellites():
    from alpha_server.newsdesk.runner import _momentum_result

    scores = {f"T{i}": float(i) for i in range(20)}
    prices = _Prices({"SPY": 900_000.0, **{f"T{i}": 50_000.0 for i in range(20)}})
    acc = PaperAccount(cash=1_000_000)
    res = _momentum_result(acc, prices, NOW, 5, None, _Journal(), fetch=lambda at: scores)
    assert res.skipped is None
    bought = {d["ticker"] for d in res.decisions if d["action"] == "buy"}
    assert bought == {"SPY"} | {f"T{i}" for i in range(10, 20)}
    eq = acc.equity(prices.table)
    assert acc.positions["SPY"].quantity * 900_000 / eq == pytest.approx(0.70, abs=0.01)
    assert all("추세 주식" in d["reason"] for d in res.decisions)


def _offline(at):
    raise RuntimeError("테스트는 네트워크를 안 쓴다")


def test_momentum_result_sells_stopped_and_dropped_names():
    from alpha_server.newsdesk.runner import _momentum_result

    M._save({"2026-10": {"picks": [f"T{i}" for i in range(10)], "stopped": []}})
    acc = PaperAccount(cash=0)
    acc.positions = {"SPY": Position("SPY", 0.7, 1_000_000.0), "OLD": Position("OLD", 1, 30_000.0),
                     **{f"T{i}": Position(f"T{i}", 1, 30_000.0) for i in range(10)}}
    table = {"SPY": 1_000_000.0, "OLD": 30_000.0, **{f"T{i}": 30_000.0 for i in range(10)}}
    table["T0"] = 20_000.0                                                   # -33% → 손절
    j = _Journal()
    res = _momentum_result(acc, _Prices(table), NOW, 5, None, j, fetch=_offline)
    sold = {d["ticker"] for d in res.decisions if d["action"] in ("sell", "trim")}
    assert {"T0", "OLD"} <= sold and "T0" not in acc.positions and "OLD" not in acc.positions
    assert ("momentum_stop_loss", {"ticker": "T0", "price": 20_000.0, "avg_price": 30_000.0}) in j.events
    assert M.stopped(NOW) == {"T0"}


def test_mirror_buys_largest_first():
    """하루 매수 건수 한도에 걸려도 코어(SPY)가 먼저 사지게."""
    from alpha_server.autopilot import mirror

    real = {"cash": 1_000_000, "positions": [], "total_value": 1_000_000}
    weights = {"SPY": 0.7, **{f"T{i}": 0.03 for i in range(10)}}
    price = {"SPY": 900_000.0, **{f"T{i}": 50_000.0 for i in range(10)}}
    orders = mirror.plan(weights, real, price, 1.0)
    buys = [o for o in orders if o["action"] == "buy"]
    assert buys and buys[0]["ticker"] == "SPY"


def test_unbuyable_names_are_replaced_by_next_rank():
    """10/13 실측: KB 는 CIEN·LITE 를 소수점으로 안 판다 — 다음 순위로 채운다."""
    fetch = lambda at: {f"T{i}": float(i) for i in range(40)}
    picks, _ = M.picks_for(NOW, fetch)
    assert picks[:2] == ["T39", "T38"] and len(picks) == 10
    M.mark_unbuyable("T38")
    M.mark_unbuyable("T38")
    again, _ = M.picks_for(NOW, fetch)
    assert "T38" not in again and again[-1] == "T29" and len(again) == 10
    assert M.unbuyable() == {"T38"}


def test_old_cache_without_ranking_is_refetched_once():
    import json

    with open(M.PICKS_FILE, "w", encoding="utf-8") as f:
        json.dump({"2026-10": {"picks": [f"T{i}" for i in range(39, 29, -1)], "stopped": ["T35"]}}, f)
    M.mark_unbuyable("T39")
    shuffled = {f"T{i}": float(i) for i in range(40)}
    shuffled["T30"] = 99.0                                                  # 다시 받은 순위가 조금 달라도
    picks, src = M.picks_for(NOW, lambda at: shuffled)
    assert picks == [f"T{i}" for i in range(38, 29, -1)] + ["T29"] and "새 선정" in src   # 산 종목은 그대로
    assert M.stopped(NOW) == {"T35"}                                        # 이번 달 손절 기록은 지키고
    broken = lambda at: (_ for _ in ()).throw(RuntimeError("x"))
    assert M.picks_for(NOW, broken)[0] == picks                             # 이제 순위가 남아 다시 안 받는다


def test_resid_scores_remove_market_move():
    """잔차 모멘텀: 시장과 같이 오른 종목보다 시장과 무관하게 꾸준히 오른 종목을 높게 친다."""
    idx = pd.date_range("2021-06-01", "2026-10-10", freq="D")
    rng = np.random.default_rng(0)
    mkt = pd.Series(np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx))), index=idx)
    beta2 = mkt ** 2                                                          # 시장을 두 배로 탄 종목
    own = mkt * pd.Series(np.linspace(1.0, 1.6, len(idx)), index=idx)        # 시장 + 자기 추세
    s = M.resid_scores_from_closes(pd.DataFrame({"BETA": beta2, "OWN": own}), mkt, NOW)
    assert s["OWN"] > s["BETA"]
    assert M.resid_scores_from_closes(pd.DataFrame({"X": own[-1200:]}), mkt[-1200:], NOW) == {}   # 4년 안 되면 안 고른다


def test_score_method_is_recorded_and_month_kept():
    picks, _ = M.picks_for(NOW, lambda at: {f"T{i}": float(i) for i in range(30)}, score="resid")
    assert M._load()["2026-10"]["score"] == "resid"
    again, _ = M.picks_for(NOW, lambda at: {f"T{i}": -float(i) for i in range(30)}, score="mom")
    assert again == picks                                                     # 달 중간에 방식을 바꿔도 이번 달 선정은 그대로


def test_stop_loss_can_be_turned_off():
    acc = PaperAccount(cash=0)
    acc.positions = {"A": Position("A", 1, 100.0)}
    assert M.stop_losses(acc, {"A": 50.0}, ["A"], stop=None) == []
    assert M.stop_losses(acc, {"A": 85.0}, ["A"], stop=0.10) == ["A"]

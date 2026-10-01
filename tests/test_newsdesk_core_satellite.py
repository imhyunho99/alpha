"""core_satellite 정책: 관심 종목 기본 보유(코어) + 뉴스 매매(위성) + 되돌림 잠금."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.autopilot.account import PaperAccount
from alpha_server.autopilot.journal import Journal
from alpha_server.autopilot.temperature import profile_for
from alpha_server.newsdesk import engine as E
from alpha_server.newsdesk import weights as W
from alpha_server.newsdesk.engine import news_step
from alpha_server.newsdesk.models import Interpretation, StyleProfile, news_id
from alpha_server.newsdesk.signals import PARAMS_CORE_SATELLITE, core_share

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
# 실계정 스타일처럼 16종목 — 3종목이면 코어 몫이 종목 상한(온도 5: 7%)에 걸려 위성이 안 보인다
FOCUS = ["A", "B", "C"] + [f"F{i}" for i in range(13)]
PRICES = {**{t: 100.0 for t in FOCUS}, "X": 100.0}


def _interp(ticker, sentiment, category="earnings", at=NOW, title=None):
    t = title or f"{ticker} {category} {sentiment} {at.isoformat()}"
    return Interpretation(
        item_id=news_id("test", "", t), ticker=ticker, sentiment=sentiment, confidence=1.0,
        category=category, published_at=at - timedelta(hours=1), model="test", title=t,
    )


class FakePrices:
    def __init__(self, table):
        self.table = table

    def get_many(self, tickers, at):
        return {t: self.table[t] for t in tickers if t in self.table}


def _style(**kw):
    base = dict(focus_tickers=list(FOCUS), reactions={"earnings": "buy", "regulation": "sell"},
                max_position_pct=20.0)
    base.update(kw)
    return StyleProfile(**base)


class Book:
    """여러 스텝에 걸쳐 같은 상태(잠금·처리한 기사)를 들고 다닌다 — 실시간 루프처럼."""

    def __init__(self, style=None, temp=5, capital=10_000_000.0):
        self.account = PaperAccount(cash=capital)
        self.style = style or _style()
        self.temp = temp
        self.weights = W.WeightState(trust={}, pending=[], peak_equity=0.0, history=[])
        self.tilts: dict[str, dict] = {}
        self.acted: set[str] = set()

    def step(self, interps=(), at=NOW, prices=PRICES, buys_today=0, params=PARAMS_CORE_SATELLITE):
        r = news_step(
            self.account, profile_for(self.temp), self.style, self.weights, list(interps),
            FakePrices(prices), at, Journal(mirror_audit=False), watch=sorted(prices),
            buys_today=buys_today, acted_item_ids=self.acted, params=params, tilts=self.tilts,
        )
        for d in r.decisions:
            self.acted.update(d.get("item_ids", []))
        return r

    def value(self, t, prices=PRICES):
        pos = self.account.positions.get(t)
        return pos.quantity * prices[t] if pos else 0.0


def _slots(book):
    p = profile_for(book.temp)
    equity = book.account.equity(PRICES)
    deployable = equity * (100 - p.cash_floor_pct) / 100 * p.max_leverage
    share = core_share(book.temp, book.style.news_pct)
    core = min(deployable * share / len(FOCUS), equity * p.max_position_pct / 100)
    sat = deployable * (1 - share) / E.SATELLITE_SLOTS
    return core, sat


def test_core_share_follows_temperature_and_style():
    assert core_share(2) == 0.8
    assert core_share(5) == 0.7
    assert core_share(9) == 0.6
    assert core_share(5, news_pct=40) == pytest.approx(0.6)
    assert core_share(5, news_pct=150) == 0.0


def test_without_news_it_holds_focus_tickers_equally():
    book = Book()
    r = book.step()
    core_slot, _ = _slots(book)
    assert set(book.account.positions) == set(FOCUS)
    for t in FOCUS:
        assert book.value(t) == pytest.approx(core_slot, rel=0.02)
    assert all(d.get("sleeve") == "core" for d in r.decisions if d["action"] == "buy")
    assert book.tilts == {}


def test_core_buys_do_not_use_daily_news_buy_limit():
    book = Book()
    book.step(buys_today=book.style.max_daily_buys)   # 뉴스 매수 한도를 다 썼어도
    assert set(book.account.positions) == set(FOCUS)


def test_good_news_adds_one_satellite_slot_then_returns_to_core():
    book = Book()
    book.step()
    core_slot, sat_slot = _slots(book)
    assert core_slot + sat_slot < book.account.equity(PRICES) * 0.07   # 종목 상한에 안 걸리는 시나리오
    r = book.step([_interp("A", 0.9)])
    buys = [d for d in r.decisions if d["action"] == "buy" and d["ticker"] == "A"]
    assert buys and "sleeve" not in buys[0]
    assert book.value("A") == pytest.approx(core_slot + sat_slot, rel=0.03)
    assert book.tilts["A"]["dir"] == 1

    later = NOW + timedelta(days=8)
    r = book.step(at=later)
    assert "A" not in book.tilts
    assert book.value("A") == pytest.approx(core_slot, rel=0.12)
    assert any(d["action"] == "trim" and d["ticker"] == "A" for d in r.decisions)


def test_news_buy_outside_focus_is_satellite_only():
    book = Book()
    book.step()
    _, sat_slot = _slots(book)
    book.step([_interp("X", 0.9)])
    assert book.value("X") == pytest.approx(sat_slot, rel=0.03)
    book.step(at=NOW + timedelta(days=8))
    assert "X" not in book.account.positions


def test_rule_sell_blocks_rebuy_for_lock_period():
    """9/28 실측 재현: 규제 악재로 판 NVDA 를 6시간 뒤 실적 호재로 다시 샀다."""
    book = Book()
    book.step()
    r = book.step([_interp("A", -0.9, "regulation")])
    assert "A" not in book.account.positions
    assert any(d["action"] == "sell" and d["ticker"] == "A" for d in r.decisions)

    six_hours = NOW + timedelta(hours=6, minutes=2)
    book.step([_interp("A", 0.9, at=six_hours)], at=six_hours)
    assert "A" not in book.account.positions   # 뉴스 매수도, 코어 채우기도 막힌다

    six_days = NOW + timedelta(days=6)
    book.step([_interp("A", 0.9, at=six_days)], at=six_days)
    assert "A" not in book.account.positions

    back = NOW + timedelta(days=7, hours=1)
    book.step(at=back)
    assert "A" in book.account.positions   # 잠금이 끝나면 코어로 복귀


def test_score_trim_does_not_undo_a_fresh_news_buy_but_user_rule_does():
    book = Book()
    book.step()
    book.step([_interp("A", 0.9)])
    held = book.value("A")
    soon = NOW + timedelta(hours=7)   # 종목 쿨다운(6시간)은 지났다
    bad = [_interp("A", -0.9, "analyst", at=soon, title=f"bad {i}") for i in range(4)]
    book.step(bad, at=soon)
    assert book.value("A") == pytest.approx(held, rel=0.01)   # 점수만으로는 팔지 않음

    later = soon + timedelta(hours=1)
    book.step([_interp("A", -0.9, "regulation", at=later)], at=later)
    assert "A" not in book.account.positions   # 사용자가 명시한 규칙은 따른다


def test_score_trim_keeps_half_core_during_lock():
    book = Book()
    book.step()
    core_slot, _ = _slots(book)
    bad = [_interp("B", -0.9, "analyst", title=f"bad {i}") for i in range(4)]
    r = book.step(bad)
    assert any(d["action"] == "trim" and d["ticker"] == "B" for d in r.decisions)
    assert book.value("B") == pytest.approx(core_slot * E.TRIM_KEEP, rel=0.05)
    assert book.tilts["B"]["dir"] == -1


def test_satellite_slots_are_limited():
    tickers = {f"N{i}": 100.0 for i in range(E.SATELLITE_SLOTS + 2)}
    prices = {**PRICES, **tickers}
    book = Book(style=_style(max_daily_buys=20))
    book.step([_interp(t, 0.9) for t in tickers], prices=prices)
    news_held = [t for t in tickers if t in book.account.positions]
    assert len(news_held) == E.SATELLITE_SLOTS


def test_style_news_share_changes_split():
    book = Book(style=_style(news_pct=50))
    book.step()
    core_slot, sat_slot = _slots(book)
    assert core_slot * len(FOCUS) == pytest.approx(sat_slot * E.SATELLITE_SLOTS, rel=0.01)


def test_loss_brake_shrinks_core():
    book = Book()
    book.step()
    crash = {t: 60.0 for t in PRICES}
    before = book.account.equity(crash)
    r = book.step(at=NOW + timedelta(days=1), prices=crash)
    assert r.exposure < 1.0
    assert book.account.equity(crash) == pytest.approx(before, rel=0.01)
    assert sum(book.value(t, crash) for t in FOCUS) < before * 0.6


def test_buys_today_ignores_core_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from alpha_server.newsdesk import store

    store.append_decisions("u", "p", [
        {"at": NOW.isoformat(), "action": "buy", "ticker": "A", "sleeve": "core", "item_ids": []},
        {"at": NOW.isoformat(), "action": "buy", "ticker": "X", "item_ids": ["X:1"]},
    ])
    assert store.buys_today("u", "p", NOW) == 1


def test_tilts_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from alpha_server.newsdesk import store

    tilts = {"A": {"dir": -1, "until": NOW + timedelta(days=7), "keep": 0.0}}
    store.save_tilts("u", "p", tilts)
    assert store.load_tilts("u", "p") == tilts


def test_equity_log_keeps_one_value_per_korean_day(tmp_path, monkeypatch):
    from alpha_server.autopilot import store as ap

    monkeypatch.setattr(ap, "STATE_DIR", str(tmp_path))
    ap.record_equity("u", "p", datetime(2026, 9, 30, 10, tzinfo=timezone.utc), 100.0)
    ap.record_equity("u", "p", datetime(2026, 9, 30, 14, tzinfo=timezone.utc), 110.0)   # KST 23시, 같은 날
    ap.record_equity("u", "p", datetime(2026, 9, 30, 16, tzinfo=timezone.utc), 120.0)   # KST 다음 날
    assert ap.load_equity("u", "p") == {"2026-09-30": 110.0, "2026-10-01": 120.0}


def test_live_loop_uses_core_satellite_and_persists_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from alpha_server.autopilot import store as ap
    from alpha_server.newsdesk import runner, store

    monkeypatch.setattr(ap, "STATE_DIR", str(tmp_path / "ap"))
    ap.save_config("u", {"temperature": 5, "capital": 1e7, "active": True, "mode": "news"}, "p")
    store.save_style("u", "p", _style())
    runner.run_portfolio("u", "p", [_interp("A", -0.9, "regulation")], FakePrices(PRICES), NOW,
                         sorted(PRICES), None)
    account, _ = ap.load_account("u", "p")
    assert "B" in account.positions          # 코어 보유
    assert "A" not in account.positions      # 규제 악재 → 잠금
    assert store.load_tilts("u", "p")["A"]["dir"] == -1
    assert ap.load_equity("u", "p")

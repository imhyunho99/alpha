"""뉴스 데스크 통합부: 점수 · news_step · 루프 · API · 모드 전환."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.autopilot.account import PaperAccount
from alpha_server.autopilot.journal import Journal
from alpha_server.autopilot.temperature import profile_for
from alpha_server.newsdesk import signals
from alpha_server.newsdesk import weights as W
from alpha_server.newsdesk.engine import news_step
from alpha_server.newsdesk.models import Interpretation, NewsItem, StyleProfile, news_id

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


def _interp(ticker, sentiment, category="earnings", hours_ago=1.0, conf=1.0, title=None):
    t = title or f"{ticker} {category} {sentiment} {hours_ago}"
    return Interpretation(
        item_id=news_id("test", "", t), ticker=ticker, sentiment=sentiment, confidence=conf,
        category=category, published_at=NOW - timedelta(hours=hours_ago), model="test", title=t,
    )


def _weights():
    return W.WeightState(trust={}, pending=[], peak_equity=0.0, history=[])


class FakePrices:
    def __init__(self, table):
        self.table = table

    def get_many(self, tickers, at):
        return {t: self.table[t] for t in tickers if t in self.table}


def _run(account, interps, prices, style=None, weights=None, watch=None, temp=5, **kw):
    return news_step(
        account, profile_for(temp), style or StyleProfile(), weights or _weights(),
        interps, FakePrices(prices), NOW, Journal(mirror_audit=False),
        watch=watch if watch is not None else sorted(prices), **kw,
    )


# --- 점수 ---

def test_score_decays_with_age_and_ignores_stale_news():
    w, style = _weights(), StyleProfile()
    fresh, _ = signals.news_score("A", [_interp("A", 0.8, hours_ago=0)], w, style, NOW)
    half, _ = signals.news_score("A", [_interp("A", 0.8, hours_ago=12)], w, style, NOW)
    stale, parts = signals.news_score("A", [_interp("A", 0.8, hours_ago=49)], w, style, NOW)
    assert fresh == pytest.approx(0.8)
    assert half == pytest.approx(0.4)
    assert stale == 0 and parts == []


def test_score_uses_trust_and_style_reactions():
    w = _weights()
    w.trust["news:analyst"] = 2.0
    style = StyleProfile(reactions={"legal": "ignore"})
    boosted, _ = signals.news_score("A", [_interp("A", 0.5, "analyst", 0)], w, style, NOW)
    ignored, _ = signals.news_score("A", [_interp("A", -0.9, "legal", 0)], w, style, NOW)
    assert boosted == pytest.approx(1.0)
    assert ignored == 0


def test_score_does_not_reward_sheer_coverage_volume():
    # 같은 호재를 매체 16곳이 받아써도 한 건의 16배가 되지 않는다
    many = [_interp("A", 0.5, hours_ago=0, title=f"copy {i}") for i in range(16)]
    score, _ = signals.news_score("A", many, _weights(), StyleProfile(), NOW)
    assert score == pytest.approx(0.5 * 16 / 4)


def test_threshold_follows_sensitivity():
    assert signals.threshold(StyleProfile(news_sensitivity=2.0)) == pytest.approx(0.5)
    assert signals.threshold(StyleProfile(news_sensitivity=0.5)) == pytest.approx(2.0)


# --- news_step ---

def test_buys_on_strong_good_news_and_records_signal():
    acct = PaperAccount(cash=10_000_000)
    w = _weights()
    news = [_interp("A", 0.9, title=f"beat {i}") for i in range(3)]
    r = _run(acct, news, {"A": 100_000.0}, weights=w)
    assert "A" in acct.positions
    buy = [d for d in r.decisions if d["action"] == "buy"][0]
    assert "실적 호재" in buy["reason"]
    assert buy["item_ids"]
    assert any(s.key == "news:earnings" and s.direction == 1 for s in w.pending)


def test_does_not_buy_on_weak_news():
    acct = PaperAccount(cash=10_000_000)
    _run(acct, [_interp("A", 0.3)], {"A": 100_000.0})
    assert acct.positions == {}


def test_does_not_rebuy_on_news_it_already_acted_on():
    acct = PaperAccount(cash=10_000_000)
    news = [_interp("A", 0.9, title=f"beat {i}") for i in range(3)]
    acted = {n.item_id for n in news}
    _run(acct, news, {"A": 100_000.0}, acted_item_ids=acted)
    assert acct.positions == {}


def test_respects_daily_buy_limit():
    acct = PaperAccount(cash=10_000_000)
    news = [_interp(t, 0.9, title=f"{t} beat {i}") for t in "ABC" for i in range(3)]
    style = StyleProfile(max_daily_buys=3)
    r = _run(acct, news, {"A": 1e5, "B": 1e5, "C": 1e5}, style=style, buys_today=2)
    assert len([d for d in r.decisions if d["action"] == "buy"]) == 1


def test_never_buys_avoided_ticker():
    acct = PaperAccount(cash=10_000_000)
    news = [_interp("TSLA", 0.9, title=f"t {i}") for i in range(3)]
    _run(acct, news, {"TSLA": 1e5}, style=StyleProfile(avoid_tickers=["TSLA"]))
    assert acct.positions == {}


def test_style_sell_rule_exits_whole_position_on_bad_news():
    acct = PaperAccount(cash=10_000_000)
    acct.buy("A", 1_000_000, 100_000.0, {}, 1.0)
    w = _weights()
    style = StyleProfile(reactions={"regulation": "sell"})
    r = _run(acct, [_interp("A", -0.6, "regulation")], {"A": 100_000.0}, style=style, weights=w)
    assert "A" not in acct.positions
    sell = [d for d in r.decisions if d["action"] == "sell"][0]
    assert "규제" in sell["reason"]
    assert any(s.direction == -1 and s.key == "news:regulation" for s in w.pending)


def test_accumulated_bad_news_trims_half():
    acct = PaperAccount(cash=10_000_000)
    acct.buy("A", 1_000_000, 100_000.0, {}, 1.0)
    before = acct.positions["A"].quantity
    news = [_interp("A", -0.9, title=f"bad {i}") for i in range(3)]
    _run(acct, news, {"A": 100_000.0})
    assert acct.positions["A"].quantity == pytest.approx(before / 2)


def test_drawdown_brake_shrinks_positions():
    acct = PaperAccount(cash=10_000_000)
    acct.buy("A", 3_000_000, 100_000.0, {}, 1.5)
    w = _weights()
    w.peak_equity = 20_000_000                      # 고점 대비 50% 낙폭
    style = StyleProfile(drawdown_soft_pct=5, drawdown_hard_pct=15)
    r = _run(acct, [], {"A": 100_000.0}, style=style, weights=w, temp=8)
    assert r.exposure == pytest.approx(0.25)
    trims = [d for d in r.decisions if d["action"] == "trim"]
    assert trims and "손실 브레이크" in trims[0]["reason"]


def test_losing_position_gets_smaller_target():
    acct = PaperAccount(cash=10_000_000)
    acct.buy("A", 700_000, 100_000.0, {}, 1.0)
    # -10%: 손절(온도5 = 7%)은 피하려고 온도 8(손절 11.8%)로
    r = _run(acct, [], {"A": 90_000.0}, temp=8)
    trims = [d for d in r.decisions if d["action"] == "trim"]
    assert not trims or "종목 손실" in trims[0]["reason"]
    assert W.ticker_multiplier(-10.0) < 1.0


def test_learns_from_matured_signals():
    acct = PaperAccount(cash=10_000_000)
    w = _weights()
    W.record_signal(w, "news:earnings", "A", 1, 100_000.0, NOW - timedelta(hours=80))
    r = _run(acct, [], {"A": 110_000.0}, weights=w)
    assert w.trust_for("news:earnings") > 1.0
    learn = [d for d in r.decisions if d["action"] == "learn"][0]
    assert "실적 뉴스 신뢰도" in learn["reason"]


def test_missing_prices_block_all_decisions():
    acct = PaperAccount(cash=10_000_000)
    acct.buy("A", 1_000_000, 100_000.0, {}, 1.0)
    news = [_interp("B", 0.9, title=f"b {i}") for i in range(3)]
    r = _run(acct, news, {"B": 1e5}, watch=["B"])
    assert r.skipped and "가격 누락" in r.skipped
    assert "B" not in acct.positions


def test_model_signal_adds_to_news_and_learns_separately():
    acct = PaperAccount(cash=10_000_000)
    w = _weights()
    # 뉴스만으로는 기준 미달(0.8 < 1.0), 모델이 보태면 통과
    r = _run(acct, [_interp("A", 0.8)], {"A": 1e5}, weights=w, model_fn=lambda t: 0.6)
    assert "A" in acct.positions
    assert "가격 모델" in [d for d in r.decisions if d["action"] == "buy"][0]["reason"]
    assert any(s.key == "model" for s in w.pending)


def test_model_alone_never_triggers_a_buy():
    acct = PaperAccount(cash=10_000_000)
    _run(acct, [], {"A": 1e5}, model_fn=lambda t: 0.99, style=StyleProfile(focus_tickers=["A"]))
    assert acct.positions == {}


# --- 루프 ---

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from alpha_server.autopilot import store as ap_store

    monkeypatch.setattr(ap_store, "STATE_DIR", str(tmp_path / "autopilot"))
    return tmp_path


class FakeSource:
    name = "fake"
    min_interval_sec = 0

    def __init__(self, items):
        self.items = items

    def fetch(self, tickers, since):
        return [i for i in self.items if i.ticker in tickers]


class FakeInterpreter:
    name = "fake"

    def interpret(self, items):
        return [
            Interpretation(i.id, i.ticker, 0.9, 1.0, "earnings", i.published_at, "fake", i.title, i.url)
            for i in items
        ]


def _item(ticker, title):
    return NewsItem(news_id("fake", "", title), ticker, title, "", "", "fake", "en", NOW - timedelta(hours=1))


def test_cycle_collects_once_and_trades_news_portfolios_only(isolated):
    from alpha_server.autopilot import store as ap_store
    from alpha_server.newsdesk import runner, store

    ap_store.save_config("kim", {"temperature": 5, "capital": 10_000_000, "active": True, "mode": "news"}, "n1")
    ap_store.save_config("kim", {"temperature": 5, "capital": 10_000_000, "active": True, "mode": "model"}, "m1")
    store.save_style("kim", "n1", StyleProfile(focus_tickers=["A"]))

    items = [_item("A", f"Acme (A) beats {i}") for i in range(3)]
    summary = runner.cycle(
        now=NOW, sources=[FakeSource(items)], interp=FakeInterpreter(),
        prices=FakePrices({"A": 1e5}), model_fn=None, keys=[("kim", "n1"), ("kim", "m1")],
    )
    assert summary["portfolios"] == 1 and summary["new_items"] == 3

    acct, _ = ap_store.load_account("kim", "n1")
    assert "A" in acct.positions
    assert ap_store.load_account("kim", "m1")[0] is None
    assert store.load_decisions("kim", "n1")[0]["action"] == "buy"

    # 두 번째 바퀴: 같은 기사로 또 사지 않는다
    runner.cycle(now=NOW + timedelta(minutes=3), sources=[FakeSource(items)], interp=FakeInterpreter(),
                 prices=FakePrices({"A": 1e5}), keys=[("kim", "n1")])
    buys = [d for d in store.load_decisions("kim", "n1") if d["action"] == "buy"]
    assert len(buys) == 1


def test_model_loop_skips_news_portfolios(isolated, monkeypatch):
    from alpha_server.autopilot import runner as ap_runner
    from alpha_server.autopilot import store as ap_store

    ap_store.save_config("kim", {"temperature": 5, "capital": 1e7, "active": True, "mode": "news"}, "n1")
    called = []
    monkeypatch.setattr(ap_runner, "step", lambda **kw: called.append(1))
    ap_runner._live_once("kim", "n1")
    assert called == []


def test_start_live_routes_news_mode_to_news_loop(isolated, monkeypatch):
    from alpha_server.autopilot import runner as ap_runner
    from alpha_server.autopilot import store as ap_store
    from alpha_server.newsdesk import runner as news_runner

    ap_store.save_config("kim", {"temperature": 5, "capital": 1e7, "active": True, "mode": "news"}, "n1")
    started = []
    monkeypatch.setattr(news_runner, "start", lambda u, p: started.append((u, p)))
    ap_runner.start_live("kim", "n1")
    assert started == [("kim", "n1")]
    assert ("kim", "n1") not in ap_runner.live_keys()


# --- 저장 ---

def test_news_store_dedupes_and_expires(isolated):
    from alpha_server.newsdesk import store

    old = _interp("A", 0.5, hours_ago=24 * 20, title="old")
    new = _interp("A", 0.5, hours_ago=1, title="new")
    store.append_news([old, new], now=NOW)
    store.append_news([new], now=NOW)
    titles = [i.title for i in store.load_news()]
    assert titles == ["new"]


def test_buys_today_counts_only_today(isolated):
    from alpha_server.newsdesk import store

    store.append_decisions("kim", "n1", [
        {"at": (NOW - timedelta(days=1)).isoformat(), "action": "buy"},
        {"at": NOW.isoformat(), "action": "buy"},
        {"at": NOW.isoformat(), "action": "sell"},
    ])
    assert store.buys_today("kim", "n1", NOW) == 1


# --- API ---

@pytest.fixture
def api_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ALPHA_JWT_SECRET", "test-secret-do-not-use-in-prod")
    from importlib import reload

    from alpha_server import audit_log, auth
    reload(audit_log)
    reload(auth)
    from alpha_server.autopilot import store as ap_store

    monkeypatch.setattr(ap_store, "STATE_DIR", str(tmp_path / "autopilot"))
    from alpha_server.newsdesk import runner

    monkeypatch.setattr(runner, "start", lambda u, p: None)
    from alpha_server.main import app

    client = TestClient(app)
    client.post("/auth/bootstrap", json={"username": "kim", "password": "StrongPass1!"})
    r = client.post("/auth/login", data={"username": "kim", "password": "StrongPass1!"})
    client.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return client


def test_newsdesk_endpoints_require_auth(api_client):
    api_client.headers.pop("Authorization")
    assert api_client.get("/newsdesk/state").status_code == 401
    assert api_client.post("/newsdesk/style/preview", json={"text": "x"}).status_code == 401


def test_style_preview_save_and_state(api_client):
    text = "반도체 위주로, 규제 뉴스 나오면 바로 정리해줘"
    preview = api_client.post("/newsdesk/style/preview", json={"text": text}).json()
    assert preview["notes"]
    assert api_client.get("/newsdesk/style?portfolio=n1").json()["raw_text"] == ""

    saved = api_client.put("/newsdesk/style", json={"portfolio": "n1", "text": text}).json()
    assert saved["reactions"].get("regulation") == "sell"
    assert api_client.get("/newsdesk/style?portfolio=n1").json()["raw_text"] == text

    r = api_client.put("/autopilot/config", json={
        "temperature": 5, "capital": 10_000_000, "active": True, "portfolio": "n1", "mode": "news",
    })
    assert r.status_code == 200 and r.json()["mode"] == "news"
    modes = {p["portfolio"]: p["mode"] for p in api_client.get("/autopilot/portfolios").json()["portfolios"]}
    assert modes["n1"] == "news"

    state = api_client.get("/newsdesk/state?portfolio=n1").json()
    assert state["active"] is True
    assert state["equity"] == 10_000_000
    assert state["exposure_multiplier"] == 1.0
    assert state["style_notes"]


def test_config_rejects_unknown_mode(api_client):
    r = api_client.put("/autopilot/config", json={
        "temperature": 5, "capital": 1000, "active": False, "mode": "yolo",
    })
    assert r.status_code == 422


# --- 관련성 ---

def _news(ticker, title, source="google_news", summary=""):
    return NewsItem(news_id(source, "", title), ticker, title, summary, "", source, "en", NOW)


def test_relevance_drops_articles_about_other_companies():
    from alpha_server.newsdesk.relevance import is_relevant

    assert not is_relevant(_news("GOOGL", "Meta's Muse, AI Hardware Push Could Drive New Revenue"))
    assert not is_relevant(_news("NVDA", "1 Overlooked Dividend King With a 55-Year Winning Streak"))
    assert is_relevant(_news("GOOGL", "Google cloud revenue jumps 30%"))
    assert is_relevant(_news("NVDA", "Why NVDA shares rallied today"))
    assert is_relevant(_news("005930.KS", "삼성전자 3분기 역대급 실적"))
    assert is_relevant(_news("AAPL", "8-K: Item 2.02", source="sec_8k"))


def test_short_tickers_need_explicit_marking():
    from alpha_server.newsdesk.relevance import is_relevant

    assert not is_relevant(_news("F", "F is for failure: markets slump"))
    assert is_relevant(_news("F", "Ford Motor recalls 100k trucks"))
    assert is_relevant(_news("F", "Shares of (F) slid after guidance cut"))


def test_rule_buy_needs_a_strong_article():
    acct = PaperAccount(cash=10_000_000)
    style = StyleProfile(reactions={"earnings": "buy"})
    _run(acct, [_interp("A", 0.35)], {"A": 1e5}, style=style)
    assert acct.positions == {}
    _run(acct, [_interp("A", 0.8, title="strong beat")], {"A": 1e5}, style=style)
    assert "A" in acct.positions


def test_rule_buy_blocked_when_overall_flow_is_negative():
    acct = PaperAccount(cash=10_000_000)
    style = StyleProfile(reactions={"earnings": "buy"})
    news = [_interp("A", 0.8, title="beat")] + [_interp("A", -0.9, "legal", title=f"suit {i}") for i in range(3)]
    _run(acct, news, {"A": 1e5}, style=style)
    assert acct.positions == {}


def test_rule_buy_halves_threshold_but_does_not_bypass_it():
    style = StyleProfile(reactions={"earnings": "buy"})
    mixed = [_interp("A", 0.8, title="beat"), _interp("A", -0.5, "legal", title="suit")]
    acct = PaperAccount(cash=10_000_000)
    _run(acct, mixed, {"A": 1e5}, style=style)          # 점수 ~0.2 < 0.5
    assert acct.positions == {}
    acct2 = PaperAccount(cash=10_000_000)
    _run(acct2, [_interp("A", 0.8, hours_ago=0, title="beat")], {"A": 1e5}, style=style)  # 0.8 ≥ 0.5
    assert "A" in acct2.positions

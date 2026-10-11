"""실주문 알림: 기록만(dry_run)은 안 울림, 실주문은 울림, 알림 오류가 sync 를 깨지 않음, dedupe·상한.

실제 알림센터·텔레그램은 conftest 의 _silence_notify 가 막고, 여기서는 기록용 가짜로 바꿔 본다.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from alpha_server import notify
from alpha_server.autopilot.account import PaperAccount
from alpha_server.notify import _macos as REAL_MACOS, _telegram as REAL_TELEGRAM   # 수집 시점 = 진짜 함수

KR_OPEN = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)      # 월 10:00 KST
FAKE_TOKEN = "123456:SECRET-TOKEN-DO-NOT-LEAK"


class LiveBroker:
    def __init__(self, total=10_000_000, positions=None, cash=10_000_000, fail=False):
        self.total, self.positions, self.cash, self.fail = total, positions or [], cash, fail
        self.orders = []

    def get_portfolio(self):
        return {"total_value": self.total, "cash": self.cash, "positions": self.positions}

    def get_position(self, t):
        return None

    def get_cash(self):
        return self.cash

    def open_orders(self, now=None):
        return []

    def execute_order(self, ticker, action, quantity, amount_krw=0.0):
        self.orders.append((ticker, action, quantity))
        if self.fail:
            return {"status": "error", "message": "주문 거부: 증거금 부족"}
        return {"status": "success", "message": "접수", "order_no": f"N{len(self.orders)}"}


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(notify, "_macos", lambda title, body: out.append((title, body)) or True)
    return out


@pytest.fixture
def env(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import audit_log, credentials, risk_manager
    reload(audit_log)
    reload(credentials)
    monkeypatch.setattr(risk_manager, "STATE_FILE", str(tmp_path / "risk.json"))
    from alpha_server.autopilot import mirror, store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path / "autopilot"))

    def make(dry_run: bool):
        cfg = {"temperature": 5, "capital": 1e7, "active": True, "mode": "news", "shadow_of": "kb",
               "broker": {"name": "kb", "dry_run": dry_run}}
        store.save_config("kim", cfg, "my-kb")
        return cfg

    return mirror, store, make


def _shadow():
    acct = PaperAccount(cash=10_000_000)
    prices = {"005930.KS": 270_000.0}
    acct.buy("005930.KS", 810_000, 270_000, prices, 1.0)     # 3주
    return acct, prices


def test_dry_run_orders_do_not_notify(env, sent):
    mirror, _, make = env
    acct, prices = _shadow()
    b = LiveBroker()
    out = mirror.sync("kim", "my-kb", make(True), acct, prices, KR_OPEN, broker=b)
    assert out["status"] == "ok" and b.orders          # 기록만 주문은 나갔지만
    assert sent == []                                   # 알림은 없다


def test_live_order_notifies_ticker_and_amount(env, sent):
    mirror, _, make = env
    acct, prices = _shadow()
    mirror.sync("kim", "my-kb", make(False), acct, prices, KR_OPEN, broker=LiveBroker())
    titles = [t for t, _ in sent]
    assert any("실주문 접수" in t for t in titles)
    body = next(b for t, b in sent if "실주문 접수" in t)
    assert "005930.KS" in body and "매수" in body and "만원" in body


def test_fill_confirmation_notifies(env, sent):
    mirror, _, make = env
    acct, prices = _shadow()
    cfg = make(False)
    b = LiveBroker()
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    b.positions = [{"ticker": "005930.KS", "quantity": 3, "value_krw": 810_000}]
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(minutes=40), broker=b)
    assert any("체결 확인" in t for t, _ in sent)


def test_rejected_live_order_notifies(env, sent):
    mirror, _, make = env
    acct, prices = _shadow()
    mirror.sync("kim", "my-kb", make(False), acct, prices, KR_OPEN, broker=LiveBroker(fail=True))
    body = next(b for t, b in sent if "주문 실패" in t)
    assert "증거금 부족" in body


def test_breaker_notifies_even_over_daily_cap(env, sent, monkeypatch):
    mirror, _, make = env
    acct = PaperAccount(cash=10_000_000)
    prices = {"005930.KS": 100_000.0}
    acct.buy("005930.KS", 9_000_000, 100_000, prices, 1.0)
    monkeypatch.setattr(mirror, "MAX_ORDER_SHARE", 0.05)
    monkeypatch.setattr(notify, "DAILY_CAP", 0)          # 상한이 차 있어도 중단 알림은 간다
    mirror.sync("kim", "my-kb", make(False), acct, prices, KR_OPEN, broker=LiveBroker(total=1_000_000, cash=1_000_000))
    assert any("실주문 중단" in t for t, _ in sent)


def test_notify_failure_never_breaks_sync(env, monkeypatch):
    mirror, _, make = env
    acct, prices = _shadow()

    def boom(*a, **k):
        raise RuntimeError("알림 서버 다운")

    monkeypatch.setattr(notify, "_macos", boom)
    monkeypatch.setattr(notify, "_telegram", boom)
    b = LiveBroker()
    out = mirror.sync("kim", "my-kb", make(False), acct, prices, KR_OPEN, broker=b)
    assert out["status"] == "ok" and b.orders
    state = mirror.load_state("kim", "my-kb")
    assert "005930.KS" in state["pending"] and "_events" not in state
    # mirror_events 자체가 터져도
    monkeypatch.setattr(notify, "mirror_events", boom)
    b.positions = [{"ticker": "005930.KS", "quantity": 3, "value_krw": 810_000}]
    out = mirror.sync("kim", "my-kb", make(False), acct, prices, KR_OPEN + timedelta(minutes=40), broker=b)
    assert out["status"] == "ok"


def test_dedupe_and_min_interval(sent):
    now = KR_OPEN
    assert notify.send("kim", "제목", "본문", key="k", now=now)["sent"]
    assert notify.send("kim", "제목", "본문", key="k", now=now + timedelta(minutes=5))["reason"] == "duplicate"
    assert notify.send("kim", "제목", "본문", key="k", now=now + timedelta(minutes=31))["sent"]
    assert len(sent) == 2


def test_daily_cap(sent, monkeypatch):
    monkeypatch.setattr(notify, "DAILY_CAP", 2)
    results = [notify.send("kim", "t", f"b{i}", now=KR_OPEN)["reason"] for i in range(3)]
    assert results == ["", "", "daily_cap"]
    # 다음 날(한국시간)은 다시 센다
    assert notify.send("kim", "t", "b9", now=KR_OPEN + timedelta(days=1))["sent"]


def test_macos_is_noop_off_mac_and_swallows_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: calls.append(a))
    assert REAL_MACOS("t", "b") is False and calls == []

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("osascript", 5)

    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run", slow)
    assert REAL_MACOS("t", "b") is False


def test_telegram_only_with_creds_and_never_leaks_token(monkeypatch, capsys):
    import requests

    posted = []
    monkeypatch.setattr(notify, "_telegram_creds", lambda u: None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: posted.append(a))
    assert REAL_TELEGRAM("kim", "hi") is False and posted == []

    monkeypatch.setattr(notify, "_telegram_creds", lambda u: {"bot_token": FAKE_TOKEN, "chat_id": "42"})

    def down(url, **k):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(requests, "post", down)
    assert REAL_TELEGRAM("kim", "hi") is False
    assert FAKE_TOKEN not in capsys.readouterr().out


def test_telegram_credentials_are_registrable(env):
    from alpha_server import credentials

    assert credentials.required_fields("telegram") == ["bot_token", "chat_id"]
    view = credentials.store_credentials("kim", "telegram", {"bot_token": FAKE_TOKEN, "chat_id": "42"})
    assert FAKE_TOKEN not in str(view)
    assert notify._telegram_creds("kim") == {"bot_token": FAKE_TOKEN, "chat_id": "42"}


def test_daily_summary_once_after_close_live_only(env, sent):
    mirror, store, make = env
    acct, prices = _shadow()
    cfg = make(False)
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=LiveBroker())
    store.record_equity("kim", "my-kb", KR_OPEN - timedelta(days=1), 10_000_000)
    sent.clear()
    before_close = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)    # 15:00 KST
    after_close = datetime(2026, 10, 12, 7, 5, tzinfo=timezone.utc)     # 16:05 KST
    assert not notify.maybe_daily_summary("kim", "my-kb", cfg, 10_100_000, before_close)
    assert notify.maybe_daily_summary("kim", "my-kb", cfg, 10_100_000, after_close)
    assert not notify.maybe_daily_summary("kim", "my-kb", cfg, 10_100_000, after_close + timedelta(hours=1))
    assert len(sent) == 1
    title, body = sent[0]
    assert "오늘 요약" in title and "10,100,000원" in body and "+100,000원" in body and "주문 1건" in body
    # 기록만 계좌·주말은 보내지 않는다
    assert not notify.maybe_daily_summary("kim", "my-kb", make(True), 1.0, after_close + timedelta(days=1))
    saturday = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
    assert not notify.maybe_daily_summary("kim", "my-kb", cfg, 1.0, saturday)

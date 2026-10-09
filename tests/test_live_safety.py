"""실주문 안전장치: 장 시간, 미체결 재주문 금지, 이상 주문 차단, 입출금 반영. 네트워크 없음."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.autopilot.account import PaperAccount
from alpha_server.brokers.kb_broker import market_open

KR_OPEN = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)      # 월 10:00 KST, 미국은 닫힘
ALL_CLOSED = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)  # 토요일


def test_market_hours():
    assert market_open("005930.KS", KR_OPEN) and not market_open("NVDA", KR_OPEN)
    us_open = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)   # 월 11:00 뉴욕
    assert market_open("NVDA", us_open) and not market_open("005930.KS", us_open)
    assert not market_open("005930.KS", ALL_CLOSED) and not market_open("NVDA", ALL_CLOSED)
    late = datetime(2026, 10, 12, 6, 25, tzinfo=timezone.utc)      # 15:25 KST — 종가 단일가, 시장가 피함
    assert not market_open("005930.KS", late)


class LiveBroker:
    """실주문 모드의 가짜 브로커. 주문은 기록만 하고 잔고는 테스트가 바꾼다."""

    def __init__(self, total=10_000_000, positions=None, cash=10_000_000, open_orders=None):
        self.total, self.positions, self.cash = total, positions or [], cash
        self._open = open_orders or []
        self.orders = []

    def get_portfolio(self):
        return {"total_value": self.total, "cash": self.cash, "positions": self.positions}

    def get_position(self, t):
        return None

    def get_cash(self):
        return self.cash

    def open_orders(self, now=None):
        return list(self._open)

    def execute_order(self, ticker, action, quantity):
        self.orders.append((ticker, action, quantity))
        return {"status": "success", "message": "접수", "order_no": f"N{len(self.orders)}"}


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
    cfg = {"temperature": 5, "capital": 1e7, "active": True, "mode": "news", "shadow_of": "kb",
           "broker": {"name": "kb", "dry_run": False}}
    store.save_config("kim", cfg, "my-kb")
    return mirror, store, cfg


def _shadow():
    acct = PaperAccount(cash=10_000_000)
    prices = {"005930.KS": 270_000.0, "000660.KS": 1_000_000.0, "NVDA": 250_000.0}
    acct.buy("005930.KS", 810_000, 270_000, prices, 1.0)     # 3주
    acct.buy("NVDA", 500_000, 250_000, prices, 1.0)
    return acct, prices


def test_closed_market_orders_are_deferred_not_sent(env):
    mirror, _, cfg = env
    acct, prices = _shadow()
    b = LiveBroker()
    out = mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    sent = {o[0] for o in b.orders}
    assert "005930.KS" in sent and "NVDA" not in sent          # 미국장은 닫힘
    assert mirror.load_state("kim", "my-kb")["deferred"] == ["NVDA"]
    assert "장 마감" in out["message"]


def test_pending_order_is_not_resent_until_it_shows_in_balance(env):
    mirror, _, cfg = env
    acct, prices = _shadow()
    b = LiveBroker()
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    assert len([o for o in b.orders if o[0] == "005930.KS"]) == 1
    # 3분 뒤: 아직 잔고에 반영 안 됨 → 같은 종목 재주문 금지
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(minutes=3), broker=b)
    assert len([o for o in b.orders if o[0] == "005930.KS"]) == 1
    # 체결이 잔고에 보이면 대기 해제
    b.positions = [{"ticker": "005930.KS", "quantity": 3, "value_krw": 810_000}]
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(minutes=40), broker=b)
    assert "005930.KS" not in mirror.load_state("kim", "my-kb")["pending"]


def test_open_orders_at_broker_block_reorder(env):
    mirror, _, cfg = env
    acct, prices = _shadow()
    b = LiveBroker(open_orders=[{"ticker": "005930.KS", "order_no": "X", "remaining": 3}])
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    assert not [o for o in b.orders if o[0] == "005930.KS"]


def test_oversized_order_trips_breaker_back_to_dry_run(env):
    mirror, store, cfg = env
    acct = PaperAccount(cash=10_000_000)
    prices = {"005930.KS": 100_000.0}
    acct.buy("005930.KS", 9_000_000, 100_000, prices, 1.0)   # 그림자 90% 한 종목 (위험 한도 10% 로 잘려도)
    b = LiveBroker(total=1_000_000, cash=1_000_000)          # 실계좌는 훨씬 작다
    monkeypatch_share = mirror.MAX_ORDER_SHARE
    mirror.MAX_ORDER_SHARE = 0.05                             # 10% 상한 주문(10만원)이 5% 를 넘게
    try:
        out = mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    finally:
        mirror.MAX_ORDER_SHARE = monkeypatch_share
    assert out["status"] == "error" and not b.orders
    assert store.load_config("kim", "my-kb")["broker"]["dry_run"] is True
    assert "breaker" in mirror.load_state("kim", "my-kb")


def test_daily_order_cap_trips_breaker(env, monkeypatch):
    mirror, store, cfg = env
    acct, prices = _shadow()
    monkeypatch.setattr(mirror, "MAX_LIVE_ORDERS_PER_DAY", 0)
    out = mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=LiveBroker())
    assert "한도 초과" in out["message"]
    assert store.load_config("kim", "my-kb")["broker"]["dry_run"] is True


def test_cash_flow_math():
    from alpha_server.autopilot.mirror import cash_flow

    prev = {"cash": 100_000, "qty": {"A": 10}}
    price = {"A": 10_000.0}
    deposit = {"cash": 1_100_000, "positions": [{"ticker": "A", "quantity": 10}]}
    assert cash_flow(prev, deposit, price) == pytest.approx(1_000_000)
    bought = {"cash": 50_000, "positions": [{"ticker": "A", "quantity": 15}]}        # 5만원어치 직접 매수
    assert cash_flow(prev, bought, price) == 0.0
    moved = {"cash": 100_000, "positions": [{"ticker": "A", "quantity": 10}]}
    assert cash_flow(prev, moved, {"A": 15_000.0}) == 0.0                              # 주가만 움직임
    assert cash_flow(None, deposit, price) == 0.0


def test_deposit_is_added_to_shadow_once(env):
    mirror, store, cfg = env
    cfg = {**cfg, "broker": {"name": "kb", "dry_run": True}}
    store.save_config("kim", cfg, "my-kb")
    acct, prices = _shadow()
    store.save_account("kim", acct, None, "my-kb")
    b = LiveBroker(cash=100_000, total=2_000_000)
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    cash_before = acct.cash
    b.cash, b.total = 1_100_000, 3_000_000                    # 100만원 입금
    out = mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(hours=1), broker=b)
    assert out["flow"] == 1_000_000 and acct.cash == pytest.approx(cash_before + 1_000_000)
    saved, _ = store.load_account("kim", "my-kb")
    assert saved.cash == pytest.approx(acct.cash)
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(hours=2), broker=b)
    assert acct.cash == pytest.approx(cash_before + 1_000_000)   # 두 번 세지 않음


def test_flow_detection_paused_after_live_orders(env):
    mirror, store, cfg = env
    acct, prices = _shadow()
    b = LiveBroker(cash=10_000_000)
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)     # 실주문 → 3일 보류
    cash_before = acct.cash
    b.cash += 1_000_000
    out = mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(hours=1), broker=b)
    assert out.get("flow_skipped") and acct.cash == cash_before


def test_kb_live_order_outside_hours_is_not_sent():
    from alpha_server.brokers.kb_broker import KbBroker

    class S:
        calls = []

        def post(self, url, **kw):
            self.calls.append(url)
            raise AssertionError("주문 API 를 부르면 안 된다")

    kb = KbBroker("k", "s", dry_run=False, session=S(), suffix_fn=lambda c: ".KS", now_fn=lambda: ALL_CLOSED)
    assert kb.execute_order("005930.KS", "buy", 1)["status"] == "market_closed"


class TestedBroker(LiveBroker):
    def __init__(self, ok=True, **kw):
        super().__init__(**kw)
        self.ok, self.tests = ok, []

    def self_test(self, ticker, now=None):
        self.tests.append(ticker)
        return {"ok": self.ok, "message": "ok" if self.ok else "원화로 해외주식 주문 불가", "step": None if self.ok else "order"}


def test_first_live_order_in_a_market_runs_self_test_once(env):
    mirror, _, cfg = env
    acct, prices = _shadow()
    b = TestedBroker()
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    assert b.tests == ["005930.KS"] and b.orders                      # 시험 통과 후 실제 주문
    st = mirror.load_state("kim", "my-kb")
    assert "KR" in st["verified"]
    b.positions = []
    mirror.sync("kim", "my-kb", cfg, acct, {**prices, "000660.KS": 990_000.0}, KR_OPEN + timedelta(hours=1), broker=b)
    assert b.tests == ["005930.KS"]                                    # 다시 시험하지 않음


def test_failed_self_test_blocks_only_that_market(env):
    mirror, store, cfg = env
    acct, prices = _shadow()
    b = TestedBroker(ok=False)
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN, broker=b)
    st = mirror.load_state("kim", "my-kb")
    assert not b.orders and st["blocked"]["KR"]["reason"].startswith("원화")
    assert store.load_config("kim", "my-kb")["broker"]["dry_run"] is False   # 전체를 끄지는 않음
    mirror.sync("kim", "my-kb", cfg, acct, prices, KR_OPEN + timedelta(hours=1), broker=b)
    assert b.tests == ["005930.KS"]                                    # 20시간 안에는 다시 시험하지 않음


def test_kb_self_test_places_cheap_limit_then_cancels(monkeypatch):
    import time as _t

    from alpha_server.brokers import kb_broker
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    calls = []

    class S:
        def post(self, url, json=None, headers=None, timeout=None):
            api = url.rsplit("/", 1)[-1]
            calls.append((api, (json or {}).get("dataBody")))

            class R:
                status_code = 200
                text = ""

                def __init__(self, body):
                    self.body = body

                def json(self):
                    return {"dataHeader": {"resultCode": "200"}, "dataBody": self.body}
            if api == "token":
                return R({"access_token": "t", "expires_in": 86400})
            if api == "ivu10140":
                return R({"now_prc": "276000"})
            if api in ("ssqm2341", "spqm2204"):
                return R({"Record1": []})
            return R({"ordr_no": "0040000900", "o_msg": "ok"})

    kb = kb_broker.KbBroker("k", "s", dry_run=False, session=S(), suffix_fn=lambda c: ".KS")
    out = kb.self_test("005930.KS")
    assert out["ok"]
    order = next(b for a, b in calls if a == "ssam1802")
    assert order["ordr_uprc"] == "207000" and order["ordr_ccd"] == "00" and order["ordr_q"] == "1"
    assert any(a == "ssam1806" for a, _ in calls)

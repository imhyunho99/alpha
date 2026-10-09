"""KB증권 어댑터 · 모의→실계좌 연동(mirror) · 연동 API · 클라이언트 토큰 만료.

KB 서버는 FakeKb 로 흉내 낸다. 실제 네트워크·주문은 나가지 않는다.
요청/응답 모양은 KB Open API 문서(2026-10-04 Excel/JSON)의 예시를 따른다.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.brokers.kb_broker import KbBroker, kr_code, num

NOW = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def _ok(body):
    return {"dataHeader": {"resultCode": "200", "resultMessage": "성공"}, "dataBody": body}


class FakeKb:
    """requests.Session 자리에 들어간다. 호출을 기록하고 API 코드별 응답을 준다."""

    def __init__(self):
        self.calls: list[tuple[str, dict, dict]] = []
        self.tokens = 0
        self.expire_next = False
        self.us = {"NVDA": ("NAS", "180.5000", "251000.00"), "JPM": ("NYS", "300.0000", "417000.00")}
        # 실측 형식(10/5): 통합 잔고에 해외 종목도 '외화증권'·USD 로, 원화 평가액으로 함께 온다
        self.domestic = [{"is_cd": "A005930", "hld_q": "000000000000003", "byng_avr_prc": "270000",
                          "val_amt": "000000000810000"},
                         {"clsf": "외화증권", "crncy_cd": "USD", "is_cd": "NVDA", "hld_q": "0",
                          "hld_q_p6": "0.500000", "val_amt": "125500"}]
        self.overseas = [{"is_cd": "NVDA        ", "frgn_hld_q_p6": "0.500000", "byng_avr_prc_p4": "170.0000",
                          "krw_val_amt": "125500"}]

    def post(self, url, json=None, headers=None, timeout=None):
        api = url.rsplit("/", 1)[-1]
        self.calls.append((api, json, headers or {}))
        if api == "token":
            self.tokens += 1
            return _Resp(200, _ok({"access_token": f"tok{self.tokens}", "token_type": "Bearer",
                                   "expires_in": 86400}))
        if self.expire_next:
            self.expire_next = False
            return _Resp(401, {"error": "expired"})
        body = json["dataBody"]
        if api == "ivu10140":
            return _Resp(200, _ok({"now_prc": "00000268500", "is_nm": "삼성전자"}))
        if api == "gss10030":
            ex, usd, krw = self.us.get(body["is_cd"], (None, None, None))
            if ex != body["krx_cd"]:
                return _Resp(200, {"dataHeader": {"resultCode": "500", "resultMessage": "종목 없음"}})
            return _Resp(200, _ok({"now_prc_p4": f" {usd}", "now_prc_krw_p2": f"   {krw}"}))
        if api == "ssqm2341":
            return _Resp(200, _ok({"Record1": [{"stnd_is_no": "KR7005930003", "nccls_q": "2", "ordr_no": "77"},
                                               {"stnd_is_no": "KR7000660001", "nccls_q": "0", "ordr_no": "78"}]}))
        if api == "spqm2204":
            return _Resp(200, _ok({"Record1": [{"shrt_is_cd": "NVDA", "nccls_q_p6": "1.000000", "ordr_no": "91"}]}))
        if api in ("ssam1806", "skam2102"):
            return _Resp(200, _ok({"ordr_no": "0040000700", "o_msg": "취소 완료"}))
        if api in ("ssam1801", "ssam1802", "skam2101", "skam2201"):
            return _Resp(200, _ok({"ordr_no": "0040000638", "o_msg": "정상적으로 주문 완료되었습니다."}))
        if api == "ssqm2952":
            return _Resp(200, _ok({"dy_tfnd": "000000001000000", "fcrncy_tfnd_krw_exch_amt": "139000",
                                   "nt_asts_val_amt": "2074500", "Record1": self.domestic}))
        if api == "spqm2226":
            return _Resp(200, _ok({"Record1": [{"tfnd_val_amt": "139000"}], "Record2": self.overseas}))
        return _Resp(404, {"error": api})


def _kb(dry_run=True):
    fake = FakeKb()
    return KbBroker("app-key", "app-secret", dry_run=dry_run, session=fake, suffix_fn=lambda c: ".KS"), fake


# ---------- 어댑터 ----------

def test_number_and_code_helpers():
    assert num(" 209.6700") == pytest.approx(209.67)
    assert num("000360000") == 360000
    assert num("   ") == 0 and num(None) == 0
    assert kr_code("005930.KS") == "005930" and kr_code("A005930") == "005930"


def test_token_and_envelope_follow_kb_spec():
    kb, fake = _kb()
    assert kb.get_current_price("005930.KS") == 268500
    api, payload, _ = fake.calls[0]
    assert api == "token"
    assert payload["dataBody"] == {"grantType": "client_credentials", "appKey": "app-key",
                                   "appSecret": "app-secret"}
    api, payload, headers = fake.calls[1]
    assert api == "ivu10140"
    assert payload["dataBody"] == {"excg_clsf": "0", "shrt_cd": "005930"}
    assert set(payload["dataHeader"]) == {"ipAddr", "macAddr"}
    assert headers["Authorization"] == "Bearer tok1"


def test_token_is_reused_then_refreshed_on_401():
    kb, fake = _kb()
    kb.get_current_price("005930.KS")
    kb.get_current_price("005930.KS")
    assert fake.tokens == 1
    fake.expire_next = True
    assert kb.get_current_price("005930.KS") == 268500
    assert fake.tokens == 2


def test_us_price_finds_exchange_and_remembers_it():
    kb, fake = _kb()
    assert kb.get_current_price("JPM") == pytest.approx(300.0)       # NAS 실패 → NYS
    assert kb.get_price_krw("JPM") == pytest.approx(417000.0)
    quotes = [c[1]["dataBody"]["krx_cd"] for c in fake.calls if c[0] == "gss10030"]
    assert quotes == ["NAS", "NYS", "NYS"]


def test_dry_run_never_calls_order_apis():
    kb, fake = _kb(dry_run=True)
    res = kb.execute_order("005930.KS", "buy", 2.7)
    assert res["status"] == "success" and "DRY-RUN" in res["message"]
    assert res["quantity"] == 2                     # 한국 주식은 1주 단위
    assert not [c for c in fake.calls if c[0].startswith(("ssam", "skam"))]


def test_live_orders_use_the_right_kb_apis(monkeypatch):
    from alpha_server.brokers import kb_broker

    monkeypatch.setattr(kb_broker, "market_open", lambda t, now=None: True)   # 장 시간 판정은 따로 시험
    kb, fake = _kb(dry_run=False)
    assert kb.execute_order("005930.KS", "buy", 3)["status"] == "success"
    assert kb.execute_order("005930.KS", "sell", 1)["status"] == "success"
    assert kb.execute_order("NVDA", "buy", 2)["status"] == "success"
    assert kb.execute_order("NVDA", "sell", 0.25)["status"] == "success"
    orders = [(c[0], c[1]["dataBody"]) for c in fake.calls if c[0].startswith(("ssam", "skam"))]
    assert orders[0] == ("ssam1802", {"mkt_tm_clsf": "1", "is_cd": "005930", "ordr_q": "3",
                                      "ordr_uprc": "0", "ordr_ccd": "03"})
    assert orders[1][0] == "ssam1801"
    assert orders[2] == ("skam2101", {"trd_dl_ccd": "02", "is_cd": "NVDA", "frgn_ordr_typ_cd": "1",
                                      "frgn_ordr_q": "2", "frgn_ordr_prc_p4": "0"})
    assert orders[3][0] == "skam2201"
    assert orders[3][1]["trd_dl_ccd"] == "01" and orders[3][1]["dcml_ordr_q_p6"] == "0.250000"


def test_order_rejects_zero_quantity_for_korean_stock():
    kb, _ = _kb(dry_run=False)
    assert kb.execute_order("005930.KS", "buy", 0.4)["status"] == "error"


def test_portfolio_merges_domestic_and_overseas_in_krw():
    kb, _ = _kb()
    snap = kb.get_portfolio()
    pos = {p["ticker"]: p for p in snap["positions"]}
    assert pos["005930.KS"]["quantity"] == 3 and pos["005930.KS"]["value_krw"] == 810000
    assert pos["NVDA"]["quantity"] == pytest.approx(0.5) and pos["NVDA"]["value_krw"] == 125500
    assert snap["cash"] == 1_000_000
    assert snap["total_value"] == pytest.approx(1_000_000 + 139_000 + 810_000 + 125_500)
    assert kb.get_position("005930.KS").quantity == 3


def test_kb_is_registered_everywhere(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import credentials
    reload(credentials)
    from alpha_server.brokers import build_broker_for_user, supported_brokers

    assert "kb" in supported_brokers()
    assert credentials.required_fields("kb") == ["app_key", "app_secret"]
    with pytest.raises(ValueError):
        build_broker_for_user("kim", "kb")
    credentials.store_credentials("kim", "kb", {"app_key": "k" * 36, "app_secret": "s" * 32})
    broker = build_broker_for_user("kim", "kb")
    assert isinstance(broker, KbBroker) and broker.dry_run


# ---------- 연동(mirror) ----------

def test_plan_scales_paper_weights_to_real_equity():
    from alpha_server.autopilot.mirror import plan

    real = {"total_value": 2_000_000, "positions": [
        {"ticker": "005930.KS", "quantity": 3, "value_krw": 810_000},     # 목표 0 → 전량 매도
        {"ticker": "NVDA", "quantity": 0.5, "value_krw": 125_500},        # 목표 20만원 → 매수
    ]}
    weights = {"NVDA": 0.10, "000660.KS": 0.10, "MSFT": 0.02}
    prices = {"005930.KS": 270_000, "NVDA": 251_000, "000660.KS": 1_770_000, "MSFT": 700_000}
    orders = plan(weights, real, prices, max_position_pct=0.10)
    by = {o["ticker"]: o for o in orders}
    assert orders[0]["action"] == "sell"                       # 매도 먼저
    assert by["005930.KS"] == {**by["005930.KS"], "action": "sell", "quantity": 3}
    assert by["NVDA"]["action"] == "buy" and by["NVDA"]["quantity"] == pytest.approx(74_500 / 251_000, abs=1e-6)
    assert "000660.KS" not in by                                # 20만원으로 1주(177만원)를 못 산다
    assert "MSFT" not in by                                     # 4만원 — 최소 주문 금액 미만


def test_plan_caps_weight_at_risk_limit():
    from alpha_server.autopilot.mirror import plan

    orders = plan({"NVDA": 0.5}, {"total_value": 1_000_000, "positions": []}, {"NVDA": 100_000}, 0.10)
    assert orders[0]["quantity"] == pytest.approx(1.0)


class FakeBroker:
    def __init__(self, total=1_000_000, positions=None, fail=False):
        self.total, self.positions, self.fail = total, positions or [], fail
        self.orders = []

    def get_portfolio(self):
        if self.fail:
            return {"error": "연결 실패"}
        return {"total_value": self.total, "positions": self.positions, "cash": self.total}

    def get_position(self, ticker):
        return None

    def get_cash(self):
        return self.total

    def execute_order(self, ticker, action, quantity):
        self.orders.append((ticker, action, quantity))
        return {"status": "success", "message": "[DRY-RUN]"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """어떤 테스트도 실제 KB 서버에 닿지 않는다(실측: 격리가 새어 가짜 키로 토큰 요청이 나갔다)."""
    import requests

    def refuse(*a, **k):
        raise AssertionError("테스트에서 실제 네트워크 요청")

    monkeypatch.setattr(requests.Session, "request", refuse)


@pytest.fixture
def mirror_env(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import audit_log, credentials, risk_manager
    reload(audit_log)
    reload(credentials)
    monkeypatch.setattr(risk_manager, "STATE_FILE", str(tmp_path / "risk_state.json"))
    from alpha_server.autopilot import mirror, store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path / "autopilot"))
    return mirror, audit_log


def _paper():
    from alpha_server.autopilot.account import PaperAccount

    acct = PaperAccount(cash=10_000_000)
    prices = {"NVDA": 250_000.0, "005930.KS": 270_000.0}
    acct.buy("NVDA", 700_000, 250_000, prices, 1.0)
    acct.buy("005930.KS", 540_000, 270_000, prices, 1.0)
    return acct, prices


def test_sync_places_orders_records_audit_and_state(mirror_env):
    mirror, audit_log = mirror_env
    acct, prices = _paper()
    broker = FakeBroker(total=10_000_000)   # 200만원이면 삼성전자 목표 10.8만원 < 1주라 건너뛴다
    cfg = {"broker": {"name": "kb", "dry_run": True}}
    out = mirror.sync("kim", "news", cfg, acct, prices, NOW, broker=broker)
    assert out["status"] == "ok", out
    assert out["orders"] == 2 and out["dry_run"]
    assert {o[0] for o in broker.orders} == {"NVDA", "005930.KS"}
    trades = [e for e in audit_log.read_all() if e["action"] == "broker_mirror"]
    assert len(trades) == 2 and all(e["fields"]["dry_run"] for e in trades)
    state = mirror.load_state("kim", "news")
    assert state["last"]["orders"] == 2 and len(state["orders"]) == 2


def test_sync_respects_daily_buy_limit(mirror_env):
    mirror, _ = mirror_env
    from datetime import date

    from alpha_server import risk_manager

    # 오늘 이미 9건 샀다 — 한도 10건이면 한 건만 더 나간다
    with open(risk_manager.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"day": date.today().isoformat(), "buys": 9, "realized_pnl": 0.0,
                   "starting_equity": 10_000_000}, f)
    acct, prices = _paper()
    broker = FakeBroker(total=10_000_000)
    mirror.sync("kim", "news", {"broker": {"name": "kb"}}, acct, prices, NOW, broker=broker)
    orders = mirror.load_state("kim", "news")["orders"]
    assert [o["status"] for o in orders].count("risk_blocked") == 1
    assert len(broker.orders) == 1


def test_sync_reports_errors_without_raising(mirror_env):
    mirror, _ = mirror_env
    acct, prices = _paper()
    out = mirror.sync("kim", "news", {"broker": {"name": "kb"}}, acct, prices, NOW,
                      broker=FakeBroker(fail=True))
    assert out["status"] == "error" and "연결 실패" in out["message"]
    out = mirror.sync("kim", "news", {"broker": {"name": "kb"}}, acct, prices, NOW)   # 키 없음
    assert out["status"] == "error" and "자격증명" in out["message"]


def test_due_runs_on_trades_or_once_a_day(mirror_env):
    mirror, _ = mirror_env
    cfg = {"broker": {"name": "kb"}}
    assert not mirror.due("kim", "news", {}, True, NOW)
    assert mirror.due("kim", "news", cfg, False, NOW)          # 처음
    acct, prices = _paper()
    mirror.sync("kim", "news", cfg, acct, prices, NOW, broker=FakeBroker())
    assert not mirror.due("kim", "news", cfg, False, NOW + timedelta(hours=3))
    assert mirror.due("kim", "news", cfg, True, NOW + timedelta(hours=3))
    assert mirror.due("kim", "news", cfg, False, NOW + timedelta(hours=25))


# ---------- API ----------

@pytest.fixture
def api_client(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ALPHA_JWT_SECRET", "test-secret-do-not-use-in-prod")
    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import audit_log, auth, credentials
    reload(audit_log)
    reload(auth)
    reload(credentials)
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


def test_broker_endpoints_turn_on_in_dry_run_and_guard_live(api_client):
    from alpha_server import credentials

    base = {"temperature": 5, "capital": 1e7, "portfolio": "news", "active": False, "mode": "news"}
    api_client.put("/autopilot/config", json=base)
    view = api_client.put("/autopilot/broker", json={"portfolio": "news", "name": "kb"}).json()
    assert view["broker"] == "kb" and view["dry_run"] and not view["registered"]

    # 다른 화면에서 설정을 저장해도 연동 설정이 지워지지 않는다
    api_client.put("/autopilot/config", json=base)
    assert api_client.get("/autopilot/broker?portfolio=news").json()["broker"] == "kb"

    r = api_client.patch("/autopilot/broker/live", json={"portfolio": "news", "live": True})
    assert r.status_code == 400                                  # 확인 문구 없음
    r = api_client.patch("/autopilot/broker/live",
                         json={"portfolio": "news", "live": True, "confirm": "실거래 전환"})
    assert r.status_code == 400 and "키" in r.json()["error"]["detail"]   # 키 없음

    credentials.store_credentials("kim", "kb", {"app_key": "k" * 36, "app_secret": "s" * 32})
    r = api_client.patch("/autopilot/broker/live",
                         json={"portfolio": "news", "live": True, "confirm": "실거래 전환"})
    assert r.status_code == 200 and r.json()["dry_run"] is False

    # 연동을 다시 켜면 항상 '기록만'으로 돌아간다
    view = api_client.put("/autopilot/broker", json={"portfolio": "news", "name": "kb"}).json()
    assert view["dry_run"] is True
    view = api_client.put("/autopilot/broker", json={"portfolio": "news", "name": None}).json()
    assert view["broker"] is None


def test_broker_check_without_keys_explains_what_to_do(api_client):
    r = api_client.post("/autopilot/broker/check", json={"portfolio": "news", "name": "kb"}).json()
    assert r["ok"] is False and "API 키 관리" in r["message"]


def test_broker_endpoints_require_auth(api_client):
    api_client.headers.pop("Authorization")
    assert api_client.get("/autopilot/broker").status_code == 401
    assert api_client.put("/autopilot/broker", json={"name": "kb"}).status_code == 401


# ---------- 클라이언트: 토큰 만료 ----------

def _jwt(exp):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64({'sub': 'kim', 'exp': exp})}.sig"


def test_client_treats_expired_token_as_logged_out(monkeypatch, tmp_path):
    from alpha import core

    monkeypatch.setattr(core, "TOKEN_FILE", str(tmp_path / "tok"))
    core.save_token(_jwt(time.time() + 3600))
    assert core.is_logged_in()
    core.save_token(_jwt(time.time() - 10))
    assert not core.is_logged_in()
    assert core._load_token() is None


def test_client_401_clears_token_and_notifies(monkeypatch, tmp_path):
    import requests

    from alpha import core

    monkeypatch.setattr(core, "TOKEN_FILE", str(tmp_path / "tok"))
    core.save_token(_jwt(time.time() + 3600))
    fired = []
    core.set_auth_expired_handler(lambda: fired.append(1))

    class R:
        status_code = 401

        def raise_for_status(self):
            raise requests.exceptions.HTTPError(response=self)

        def json(self):
            return {"detail": "토큰이 만료되었습니다."}

    monkeypatch.setattr(core.requests, "request", lambda *a, **k: R())
    try:
        out = core._handle_request("get", "/autopilot/broker")
    finally:
        core.set_auth_expired_handler(None)
    assert out["error"] == core.AUTH_EXPIRED_MESSAGE
    assert fired and core._load_token() is None


def test_broker_text_makes_live_mode_obvious():
    from alpha.news_widgets import broker_text, order_result_text

    assert "연동 안 함" in broker_text({"broker": None})
    dry = broker_text({"broker": "kb", "dry_run": True, "registered": False})
    assert "기록만" in dry and "API 키 미등록" in dry
    assert "🔴" in broker_text({"broker": "kb", "dry_run": False, "registered": True})
    assert order_result_text({"status": "success", "dry_run": True}) == "기록됨"
    assert "보류" in order_result_text({"status": "risk_blocked", "message": "한도"})


def test_token_error_shows_kb_message():
    """실측 응답(잘못된 앱 키)을 그대로 재현한다."""
    from alpha_server.brokers.kb_broker import KbApiError

    class Bad(FakeKb):
        def post(self, url, json=None, headers=None, timeout=None):
            return _Resp(500, {"dataHeader": {"processFlag": "B", "processCode": "E021", "resultCode": "9999",
                                              "processMessage": "앱키로 앱정보 추출 중 오류가 발생했습니다."},
                               "dataBody": {"access_token": "", "token_type": "", "expires_in": 0}})

    kb = KbBroker("bad", "bad", session=Bad(), suffix_fn=lambda c: ".KS")
    with pytest.raises(KbApiError, match="앱키로 앱정보 추출 중 오류가 발생했습니다. \\(E021\\)"):
        kb._access_token()
    assert "E021" in kb.get_portfolio()["error"]


@pytest.mark.parametrize("status,body,expected", [
    (401, {"error": {"code": "http_401", "detail": "자격 증명이 올바르지 않습니다."}}, "정보를 확인해 주세요"),
    (429, {}, "잠시 후 다시"),
    (500, {}, "서버 오류 500"),
    (400, {"error": {"detail": "비밀번호는 8자 이상"}}, "비밀번호는 8자 이상"),
])
def test_login_errors_are_human_readable(monkeypatch, tmp_path, status, body, expected):
    import requests

    from alpha import core

    monkeypatch.setattr(core, "TOKEN_FILE", str(tmp_path / "tok"))

    class R:
        status_code = status

        def raise_for_status(self):
            raise requests.exceptions.HTTPError(response=self)

        def json(self):
            return body

    monkeypatch.setattr(core.requests, "post", lambda *a, **k: R())
    msg = core.login("hyunho", "wrong-pass")["error"]
    assert expected in msg and "Client Error" not in msg and "http://" not in msg


def test_login_connection_error_and_empty_fields(monkeypatch):
    import requests

    from alpha import core

    def down(*a, **k):
        raise requests.exceptions.ConnectionError("Max retries exceeded with url: http://127.0.0.1:8000")

    monkeypatch.setattr(core.requests, "post", down)
    assert core.login("hyunho", "x" * 8)["error"] == "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
    assert core.login("", "")["error"] == "아이디와 비밀번호를 입력해 주세요."


def test_api_connect_prompt_only_when_needed(monkeypatch, tmp_path):
    from alpha import strategy_widgets as sw

    monkeypatch.setattr(sw, "PREFS_FILE", str(tmp_path / "prefs.json"))
    shown = []

    class Fake:
        def __init__(self, parent=None, registered=None):
            shown.append(registered)

        def exec(self):
            return 0

    monkeypatch.setattr(sw, "ApiConnectDialog", Fake)
    monkeypatch.setattr(sw, "registered_apis", lambda: {"kb"})
    sw.maybe_prompt_api_connect()
    assert shown == [{"kb"}]                       # Claude 가 아직 없다
    monkeypatch.setattr(sw, "registered_apis", lambda: {"kb", "anthropic"})
    sw.maybe_prompt_api_connect()
    assert len(shown) == 1                         # 다 연결됐으면 묻지 않음
    monkeypatch.setattr(sw, "registered_apis", lambda: None)
    sw.maybe_prompt_api_connect()
    assert len(shown) == 1                         # 서버에 못 물으면 묻지 않음
    sw._save_prefs({"skip_api_prompt": True})
    monkeypatch.setattr(sw, "registered_apis", lambda: set())
    sw.maybe_prompt_api_connect()
    assert len(shown) == 1                         # '다음부터 묻지 않기'


def test_api_dialogs_build(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841 — 위젯 생성에 필요
    from alpha import strategy_widgets as sw

    monkeypatch.setattr(sw.core, "_handle_request", lambda *a, **k: {"brokers": []})
    d = sw.ApiConnectDialog(registered={"kb"})
    assert d.status_labels["kb"].text().startswith("✅")
    assert d.status_labels["anthropic"].text().startswith("⚪")
    k = sw.ApiKeyDialog(broker="kb")
    assert k.broker_box.currentData() == "kb" and k.broker_box.currentText() == "KB증권"
    assert set(k._field_widgets) == {"app_key", "app_secret"}


def test_autopilot_tab_retries_portfolio_list(monkeypatch):
    """10/4 E2E: 목록 요청이 한 번 실패하면 'default' 하나로 굳었다."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from alpha import autopilot_widgets as aw

    monkeypatch.setattr(aw.AutopilotTab, "_load_portfolios", lambda self, select=None: None)
    monkeypatch.setattr(aw.AutopilotTab, "_load_config", lambda self: None)
    scheduled = []
    monkeypatch.setattr(aw.QTimer, "singleShot", staticmethod(lambda ms, *rest: scheduled.append(ms)))
    tab = aw.AutopilotTab()
    tab._on_portfolios({"error": "서버 응답 시간이 초과되었습니다."})
    assert scheduled == [aw.PORTFOLIO_RETRY_MS]
    tab._on_portfolios({"error": "만료", "auth_expired": True})
    assert len(scheduled) == 1                     # 로그인 만료면 재시도 대신 로그인 후 다시 부른다
    tab._on_portfolios({"portfolios": [{"portfolio": "balanced-factor", "temperature": 5, "mode": "model"}]})
    assert tab.known_portfolios() == ["balanced-factor"]


def test_dry_run_does_not_record_the_same_plan_twice(mirror_env):
    mirror, _ = mirror_env
    acct, prices = _paper()
    broker = FakeBroker(total=10_000_000)
    cfg = {"broker": {"name": "kb", "dry_run": True}}
    first = mirror.sync("kim", "news", cfg, acct, prices, NOW, broker=broker)
    second = mirror.sync("kim", "news", cfg, acct, prices, NOW + timedelta(minutes=3), broker=broker)
    assert first["orders"] == 2 and second["orders"] == 0 and "변경 없음" in second["message"]
    assert len(mirror.load_state("kim", "news")["orders"]) == 2
    moved = {**prices, "NVDA": 252_000.0}                    # 시세가 조금 움직여도 같은 계획
    assert mirror.sync("kim", "news", cfg, acct, moved, NOW + timedelta(minutes=6), broker=broker)["orders"] == 0
    acct.sell("005930.KS", acct.positions["005930.KS"].quantity, prices["005930.KS"])   # 종목이 바뀌면 다시 기록
    third = mirror.sync("kim", "news", cfg, acct, prices, NOW + timedelta(minutes=9), broker=broker)
    assert third["orders"] >= 1


def test_token_is_shared_across_broker_instances():
    """10/9 문의: 연동·조회마다 새 KbBroker 가 토큰을 새로 받아 KB 발급 알림이 반복됐다."""
    from alpha_server.brokers import kb_broker

    kb_broker._TOKENS.clear()
    fake = FakeKb()
    for _ in range(3):
        KbBroker("shared-key", "s", session=fake, suffix_fn=lambda c: ".KS").get_current_price("005930.KS")
    assert fake.tokens == 1
    KbBroker("other-key", "s", session=fake, suffix_fn=lambda c: ".KS").get_current_price("005930.KS")
    assert fake.tokens == 2                       # 앱 키가 다르면 따로
    kb_broker._TOKENS.clear()



def test_open_orders_limit_and_cancel_follow_kb_spec():
    from datetime import datetime, timezone

    kb, fake = _kb(dry_run=False)
    opens = kb.open_orders(datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))
    assert {(o["ticker"], o["order_no"]) for o in opens} == {("005930.KS", "77"), ("NVDA", "91")}
    r = kb.place_limit("NVDA", "buy", 1, 100.5)
    assert r["status"] == "success" and r["order_no"] == "0040000638"
    assert kb.cancel("NVDA", r["order_no"])["status"] == "success"
    assert kb.cancel("005930.KS", "77", 2)["status"] == "success"
    bodies = {c[0]: c[1]["dataBody"] for c in fake.calls}
    assert bodies["skam2101"] == {"trd_dl_ccd": "02", "is_cd": "NVDA", "frgn_ordr_typ_cd": "2",
                                  "frgn_ordr_q": "1", "frgn_ordr_prc_p4": "100.50"}
    assert bodies["skam2102"] == {"crct_cncl_clsf": "2", "is_cd": "NVDA", "orgn_ordr_no": "0040000638",
                                  "frgn_ordr_prc_p4": "0"}
    assert bodies["ssam1806"] == {"is_cd": "005930", "crct_clsf": "2", "orgn_ordr_no": "77", "ordr_q": "2"}


def test_dry_run_never_sends_limit_or_cancel():
    kb, fake = _kb(dry_run=True)
    kb.place_limit("NVDA", "buy", 1, 100)
    kb.cancel("NVDA", "1")
    assert not [c for c in fake.calls if c[0].startswith(("ssam", "skam"))]

"""내 실계좌 조회·분석 (myaccount). 조회 전용 — 주문 API 가 불리지 않는지도 확인한다."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from alpha_server import myaccount as M
from alpha_server.brokers.kb_broker import KbBroker
from alpha_server.newsdesk.models import Interpretation

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def _ok(body):
    return _Resp({"dataHeader": {"resultCode": "200"}, "dataBody": body})


class FakeKb:
    """KB Open API 문서의 응답 모양. 실현손익은 두 페이지로 나눠 준다."""

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        api = url.rsplit("/", 1)[-1]
        body = (json or {}).get("dataBody", {})
        self.calls.append(api)
        if api == "token":
            return _ok({"access_token": "t", "expires_in": 86400})
        if api == "ssqm2952":
            return _ok({"dy_tfnd": "000000002000000", "Record1": [
                {"is_cd": "A005930", "is_nm": "삼성전자", "hld_q": "10", "val_amt": "2700000",
                 "byng_amt": "3000000", "val_pl": "-300000"},
                {"is_cd": "A035720", "is_nm": "카카오", "hld_q": "20", "val_amt": "700000",
                 "byng_amt": "1000000", "val_pl": "-300000"},
            ]})
        if api == "spqm2226":
            return _ok({"Record1": [{"tfnd_val_amt": "100000"}], "Record2": [
                {"is_cd": "NVDA", "is_nm": "엔비디아", "frgn_hld_q_p6": "10.000000", "krw_val_amt": "2500000",
                 "krw_exch_byng_amt": "1500000", "krw_exch_val_pl": "1000000", "byng_avr_prc_p4": "110"},
            ]})
        if api == "ssqm2442":
            page2 = body.get("nxt_key") == "P2"
            rows = ([{"trd_dt": "20260901", "shrt_is_cd": "005930", "is_nm": "삼성전자", "trd_dl_ccd": "01",
                      "ccls_q": "5", "ccls_uprc": "280000", "rlztn_pl": "100000", "yld": "7.7",
                      "fee": "300", "svrl_tx": "2800"}] if not page2 else
                    [{"trd_dt": "20260915", "shrt_is_cd": "035720", "is_nm": "카카오", "trd_dl_ccd": "01",
                      "ccls_q": "10", "ccls_uprc": "35000", "rlztn_pl": "-150000", "yld": "-30.0",
                      "fee": "100", "svrl_tx": "600"}])
            return _ok({"Record1": rows, "nxt_key": "" if page2 else "P2"})
        if api == "spqm2207":
            return _ok({"Record2": [{"ordr_dt": "20260920", "mkt_clsf_nm": "미국", "fcrncy_trd_pl_sum_p2": "50000",
                                     "b_svcst1_p2": "500", "s_svcst1_p2": "700", "frgn_dl_tx_p4": "0"}]})
        return _Resp({"error": api}, 404)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    import requests

    def refuse(*a, **k):
        raise AssertionError("테스트에서 실제 네트워크 요청")

    monkeypatch.setattr(requests.Session, "request", refuse)
    monkeypatch.setattr(M, "_closes", lambda tickers: {})
    M._cache.clear()


def _kb():
    fake = FakeKb()
    return KbBroker("k", "s", dry_run=True, session=fake), fake


def test_overview_reads_balance_history_and_never_orders():
    kb, fake = _kb()
    out = M.overview("kim", "kb", broker=kb, now=NOW)
    assert out["registered"] and not out.get("error")
    s = out["summary"]
    assert s["total_krw"] == 2_000_000 + 100_000 + 2_700_000 + 700_000 + 2_500_000
    assert s["holdings"] == 3 and s["pl_krw"] == 400_000
    assert out["holdings"][0]["name"] == "삼성전자"                     # 평가액 순
    assert [t["name"] for t in out["trades"]] == ["카카오", "삼성전자"]   # 두 페이지 모두, 최신순
    assert out["habits"]["sells"] == 2 and out["habits"]["win_rate_pct"] == 50.0
    assert out["habits"]["realized_pl"] == 100_000 - 150_000 + 50_000
    assert not [c for c in fake.calls if c.startswith(("ssam", "skam"))]   # 주문 API 는 한 번도 안 불림
    hist = M.load_history("kim", "kb")
    assert list(hist) == ["2026-10-05"] and hist["2026-10-05"]["total"] == s["total_krw"]


def test_findings_flag_concentration_losses_trend_and_news():
    snap = {"total_value": 10_000_000, "cash": 500_000, "positions": [
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 20, "value_krw": 5_000_000,
         "cost_krw": 4_000_000, "pl_krw": 1_000_000},
        {"ticker": "035720.KS", "name": "카카오", "quantity": 50, "value_krw": 1_500_000,
         "cost_krw": 2_500_000, "pl_krw": -1_000_000},
    ]}
    stats = {"035720.KS": {"above_200d": False, "drawdown_pct": -42.0, "vol_pct": 40.0}}
    news = [Interpretation(item_id="1", ticker="035720.KS", sentiment=-0.8, confidence=0.9, category="legal",
                           published_at=NOW - timedelta(days=1), model="t", title="카카오 소송", url="u")]
    out = M.analyze(snap, stats, [], [], news)
    text = " ".join(f["text"] for f in out["findings"])
    assert "삼성전자 한 종목이 계좌의 50%" in text
    assert "카카오: 매입가 대비 -40%" in text
    assert "200일 평균선 아래" in text and "1년 고점 대비 -42%" in text
    assert "악재 기사 1건" in text and out["bad_news"][0]["title"] == "카카오 소송"


def test_price_stats_trend_and_drawdown():
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    up = pd.Series(np.linspace(100, 200, 300), index=idx)
    st = M.price_stats(up)
    assert st["above_200d"] is True and st["drawdown_pct"] == 0.0
    down = pd.Series(np.linspace(200, 100, 300), index=idx)
    st = M.price_stats(down)
    assert st["above_200d"] is False and st["drawdown_pct"] < -40
    assert M.price_stats(pd.Series([1.0, 2.0]))["above_200d"] is None


def test_overview_without_keys_says_not_registered(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import credentials
    reload(credentials)
    assert M.overview("nobody", "kb", now=NOW) == {"registered": False, "broker": "kb"}


def test_kb_error_is_reported_not_raised():
    class Down(FakeKb):
        def post(self, url, json=None, headers=None, timeout=None):
            if url.endswith("token"):
                return _Resp({"dataHeader": {"processCode": "E021", "processMessage": "앱키 오류"}}, 500)
            return super().post(url, json, headers, timeout)

    kb = KbBroker("k", "s", session=Down())
    out = M.overview("kim", "kb", broker=kb, now=NOW)
    assert out["registered"] and "E021" in out["error"]


def test_held_tickers_feed_news_collection():
    kb, _ = _kb()
    M.overview("kim", "kb", broker=kb, now=NOW)
    assert M.held_tickers() == {"005930.KS", "035720.KS", "NVDA"}


def test_account_api_requires_login_and_reports_missing_keys(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ALPHA_JWT_SECRET", "test-secret-do-not-use-in-prod")
    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import audit_log, auth, credentials
    reload(audit_log)
    reload(auth)
    reload(credentials)
    from alpha_server.main import app

    client = TestClient(app)
    assert client.get("/account/overview").status_code == 401
    client.post("/auth/bootstrap", json={"username": "kim", "password": "StrongPass1!"})
    tok = client.post("/auth/login", data={"username": "kim", "password": "StrongPass1!"}).json()["access_token"]
    r = client.get("/account/overview", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json() == {"registered": False, "broker": "kb"}
    assert client.get("/account/overview?broker=kis", headers={"Authorization": f"Bearer {tok}"}).status_code == 422


def test_my_account_tab_renders_both_states(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from alpha.myaccount_widgets import MyAccountTab

    tab = MyAccountTab()
    tab._on_data({"registered": False, "broker": "kb"})
    assert not tab.guide.isHidden() and "키" in tab.status.text()

    kb, _ = _kb()
    data = M.overview("kim", "kb", broker=kb, now=NOW)
    tab._on_data(data)
    assert tab.guide.isHidden()
    assert "총 평가 8,000,000원" in tab.summary.text()
    assert tab.holdings.rowCount() == 3 and tab.trades.rowCount() == 2
    assert tab.findings.count() >= 1

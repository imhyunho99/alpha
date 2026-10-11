"""에이전트 성과판(account_performance): 넣은 돈 대비 손익, 시간가중수익률, '그냥 샀다면' 비교. 네트워크 없음."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from alpha_server import account_performance as P
from alpha_server.autopilot.account import FEE_RATE, PaperAccount

D = date(2026, 10, 1)
NOW = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)      # 10/10 12:00 KST


def _d(n: int) -> date:
    return D + timedelta(days=n)


# ---------- 순수 계산 ----------

def test_twr_without_flows_is_simple_return():
    points = [(_d(0), 1_000_000), (_d(1), 1_050_000), (_d(2), 1_100_000)]
    assert P.time_weighted_return(points, [(_d(0), 1_000_000)]) == pytest.approx(0.10)


def test_twr_ignores_deposit_timing():
    """+10% 뒤 100만원 입금, 다시 +10%. 운용 수익률은 21% — 넣은 돈 대비 단순 수익률(15.5%)과 다르다."""
    flows = [(_d(0), 1_000_000), (_d(2), 1_000_000)]
    points = [(_d(0), 1_000_000), (_d(1), 1_100_000), (_d(2), 2_100_000), (_d(3), 2_310_000)]
    assert P.time_weighted_return(points, flows) == pytest.approx(0.21)
    assert (2_310_000 / 2_000_000 - 1) == pytest.approx(0.155)


def test_twr_with_withdrawal_and_flow_between_records():
    # 기록이 없는 날(d2)의 출금은 다음 기록일(d3)에 묶인다
    flows = [(_d(0), 1_000_000), (_d(2), -500_000)]
    points = [(_d(0), 1_000_000), (_d(1), 1_200_000), (_d(3), 700_000)]
    assert P.time_weighted_return(points, flows) == pytest.approx(1.2 * (700_000 / 700_000) - 1)


def test_twr_needs_points():
    assert P.time_weighted_return([], [(_d(0), 1)]) is None


def test_deposit_value_one_year():
    assert P.deposit_value([(_d(0), 1_000_000)], _d(365)) == pytest.approx(1_030_000)
    two = P.deposit_value([(_d(0), 1_000_000), (_d(365), 500_000)], _d(365))
    assert two == pytest.approx(1_530_000)


def _prices(table):
    def price_on(day):
        return table[day]
    return price_on


def test_buy_and_hold_follows_same_deposits():
    table = {_d(0): {"SPY": 100.0, "IEF": 50.0}, _d(5): {"SPY": 200.0, "IEF": 50.0},
             _d(9): {"SPY": 220.0, "IEF": 55.0}}
    flows = [(_d(0), 1_000_000), (_d(5), 1_000_000)]
    spy = P.buy_and_hold_value(flows, {"SPY": 1.0}, _prices(table), _d(9))
    # 10,000주 + 5,000주 → 15,000 × 220
    assert spy == pytest.approx(3_300_000)
    bal = P.buy_and_hold_value(flows, {"SPY": 0.6, "IEF": 0.4}, _prices(table), _d(9))
    spy_units = 600_000 / 100 + 600_000 / 200
    ief_units = 400_000 / 50 + 400_000 / 50
    assert bal == pytest.approx(spy_units * 220 + ief_units * 55)


def test_buy_and_hold_withdrawal_sells_pro_rata():
    table = {_d(0): {"SPY": 100.0}, _d(1): {"SPY": 200.0}, _d(2): {"SPY": 200.0}}
    v = P.buy_and_hold_value([(_d(0), 1_000_000), (_d(1), -1_000_000)], {"SPY": 1.0}, _prices(table), _d(2))
    assert v == pytest.approx(1_000_000)        # 200만원 중 100만원 출금


def test_buy_and_hold_missing_price_raises():
    with pytest.raises(KeyError):
        P.buy_and_hold_value([(_d(0), 1)], {"SPY": 1.0}, _prices({_d(0): {}}), _d(0))


def test_estimate_start_skips_old_regime():
    """실측(10/11): 같은 이름의 예전 계좌 평가액 360만원대·이중 계상 212만원이 남아 있다."""
    series = {"2026-10-05": 3_666_826, "2026-10-08": 3_658_651, "2026-10-09": 2_124_226,
              "2026-10-10": 1_062_113, "2026-10-11": 1_062_113}
    assert P.estimate_start(series, 1_063_707, []) == date(2026, 10, 10)
    # 기록된 입금이 있으면 그날 이전엔 넣은 돈이 그만큼 적었다
    series2 = {"2026-10-01": 1_000_000, "2026-10-02": 1_010_000, "2026-10-03": 2_020_000}
    assert P.estimate_start(series2, 2_000_000, [(date(2026, 10, 3), 1_000_000)]) == date(2026, 10, 1)
    assert P.estimate_start({}, 1_000_000, []) is None


def test_order_costs_split_live_and_recorded():
    orders = [
        {"at": "2026-10-02T01:00:00+00:00", "status": "success", "dry_run": False, "amount_krw": 1_000_000},
        {"at": "2026-10-02T01:00:00+00:00", "status": "success", "dry_run": True, "amount_krw": 500_000},
        {"at": "2026-10-02T01:00:00+00:00", "status": "risk_blocked", "dry_run": False, "amount_krw": 9_000_000},
        {"at": "2026-09-20T01:00:00+00:00", "status": "success", "dry_run": False, "amount_krw": 7_000_000},
    ]
    c = P.order_costs(orders, date(2026, 10, 1), 0.001)
    assert c["live_orders"] == 1 and c["live_cost"] == 1_000
    assert c["recorded_orders"] == 1 and c["recorded_cost"] == 500


# ---------- 조립 (상태 파일은 tmp_path) ----------

@pytest.fixture
def ap(monkeypatch, tmp_path):
    from alpha_server.autopilot import store

    from alpha_server import audit_log

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path / "autopilot"))
    # 감사 로그 경로는 import 때 정해진다 — 실제 ~/AlphaModels 에 쓰지 않게 tmp 로 돌린다
    monkeypatch.setattr(audit_log, "LOG_FILE", str(tmp_path / "audit.log.jsonl"))
    return store


def _setup(store, flows=None, started_at="2026-10-01T01:00:00+00:00"):
    from alpha_server.autopilot import mirror

    cfg = {"temperature": 5, "active": True, "mode": "news", "shadow_of": "kb", "sleeve": True,
           "principal": 2_000_000, "seeded_at": "2026-10-09T10:00:00+00:00"}
    if started_at:
        cfg["started_at"] = started_at
    store.save_config("kim", cfg, "my-kb")
    store.save_account("kim", PaperAccount(cash=2_150_000), None, "my-kb")
    for d, v in {"2026-10-01": 1_000_000, "2026-10-04": 1_100_000, "2026-10-05": 2_100_000,
                 "2026-10-09": 2_100_000}.items():
        store.record_equity("kim", "my-kb", datetime.fromisoformat(d + "T05:00:00+00:00"), v)
    state = {"orders": [{"at": "2026-10-05T01:00:00+00:00", "status": "success", "dry_run": False,
                         "amount_krw": 1_000_000}]}
    for at, amount in flows or []:
        mirror.record_flow(state, at, amount)
    mirror._save_state("kim", "my-kb", state)


SPY_IEF = {_d(0): {"SPY": 100.0, "IEF": 50.0}, _d(4): {"SPY": 110.0, "IEF": 50.0},
           _d(9): {"SPY": 121.0, "IEF": 55.0}}


def test_compute_with_logged_deposit(ap):
    _setup(ap, flows=[(datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc), 1_000_000)])
    r = P.performance("kim", "my-kb", now=NOW, value_fn=lambda acct: acct.cash, price_on=_prices(SPY_IEF))
    assert r["exists"] and r["start_date"] == "2026-10-01" and r["flows_source"] == "log"
    assert r["flows"] == [{"date": "2026-10-01", "amount": 1_000_000}, {"date": "2026-10-05", "amount": 1_000_000}]
    assert r["value"] == 2_150_000 and r["pnl"] == 150_000 and r["pnl_pct"] == pytest.approx(7.5)
    # 1.1 (10/4) × 2.1/2.1 (10/5) × 1.0 (10/9) × 2.15/2.1 (오늘)
    assert r["twr_pct"] == pytest.approx((1.1 * 2_150_000 / 2_100_000 - 1) * 100, abs=0.01)
    spy = next(b for b in r["benchmarks"] if b["key"] == "spy")
    assert spy["value"] == round((10_000 + 1_000_000 / 110) * 121)
    keys = [b["key"] for b in r["benchmarks"]]
    assert keys == ["spy", "balanced", "deposit"]
    assert r["costs"]["live_cost"] == round(1_000_000 * FEE_RATE)


def test_compute_estimates_flows_and_start_when_unlogged(ap):
    _setup(ap, started_at=None)
    r = P.performance("kim", "my-kb", now=NOW, value_fn=lambda acct: acct.cash, price_on=_prices(SPY_IEF))
    # 원금 200만원: 10/5 이후만 ±30% 안 → 시작일 10/5, 원금 전체를 그날 입금으로
    assert r["start_date"] == "2026-10-05" and r["flows_source"] == "estimated"
    assert r["flows"] == [{"date": "2026-10-05", "amount": 2_000_000}]
    assert any("입출금 기록이 없어" in n for n in r["notes"])
    assert any("시작일" in n for n in r["notes"])


def test_compute_returns_partial_result_when_prices_fail(ap):
    _setup(ap)

    def boom(_day):
        raise KeyError("SPY 가격 없음")

    def no_live(_acct):
        raise RuntimeError("네트워크 없음")

    r = P.performance("kim", "my-kb", now=NOW, value_fn=no_live, price_on=boom)
    assert r["value"] == 2_100_000                               # 마지막 기록 평가액으로
    spy = next(b for b in r["benchmarks"] if b["key"] == "spy")
    assert "error" in spy and "value" not in spy
    deposit = next(b for b in r["benchmarks"] if b["key"] == "deposit")
    assert deposit["value"] > 2_000_000                          # 예금 비교는 시세 없이도 된다
    assert any("현재 시세" in n for n in r["notes"]) and any("계산 실패" in n for n in r["notes"])


def test_compute_without_agent(ap):
    r = P.performance("kim", "my-kb", now=NOW)
    assert r["exists"] is False and r["notes"]


def test_performance_never_raises(ap, monkeypatch):
    monkeypatch.setattr(P, "compute", lambda *a, **k: 1 / 0)
    r = P.performance("kim", "my-kb")
    assert r["exists"] is None and "ZeroDivisionError" in r["notes"][0]


# ---------- 입출금 기록이 실제로 쌓이는지 ----------

def test_mirror_sync_logs_deposit(ap, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())
    from importlib import reload

    from alpha_server import audit_log, risk_manager
    reload(audit_log)
    monkeypatch.setattr(risk_manager, "STATE_FILE", str(tmp_path / "risk.json"))
    from alpha_server.autopilot import mirror

    class B:
        cash, total = 100_000, 100_000

        def get_portfolio(self):
            return {"total_value": self.total, "cash": self.cash, "positions": []}

        def get_position(self, t):
            return None

        def get_cash(self):
            return self.cash

        def execute_order(self, ticker, action, quantity, amount_krw=0.0):
            return {"status": "success", "message": "기록", "order_no": ""}

    cfg = {"temperature": 5, "active": True, "mode": "news", "shadow_of": "kb", "sleeve": True,
           "principal": 100_000, "broker": {"name": "kb", "dry_run": True}, "baseline": {}}
    ap.save_config("kim", cfg, "my-kb")
    acct = PaperAccount(cash=100_000)
    b = B()
    mirror.sync("kim", "my-kb", cfg, acct, {}, NOW, broker=b)
    b.cash = b.total = 1_100_000                                   # 100만원 입금
    mirror.sync("kim", "my-kb", ap.load_config("kim", "my-kb"), acct, {}, NOW + timedelta(hours=1), broker=b)
    flows = mirror.load_state("kim", "my-kb")["flows"]
    assert [f["amount"] for f in flows] == [1_000_000]
    assert ap.load_config("kim", "my-kb")["principal"] == 1_100_000


# ---------- API ----------

def test_performance_api(ap, monkeypatch):
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

    _setup(ap)
    real_compute = P.compute
    monkeypatch.setattr(P, "compute", lambda u, p, **k: real_compute(
        u, p, now=NOW, value_fn=lambda acct: acct.cash, price_on=_prices(SPY_IEF)))
    client = TestClient(app)
    assert client.get("/account/performance").status_code == 401
    client.post("/auth/bootstrap", json={"username": "kim", "password": "StrongPass1!"})
    tok = client.post("/auth/login", data={"username": "kim", "password": "StrongPass1!"}).json()["access_token"]
    r = client.get("/account/performance", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["principal"] == 2_000_000 and body["value"] == 2_150_000
    assert {b["key"] for b in body["benchmarks"]} == {"spy", "balanced", "deposit"}

    # 계산이 통째로 터져도 500 이 아니라 이유를 담아 200
    monkeypatch.setattr(P, "compute", lambda *a, **k: 1 / 0)
    r = client.get("/account/performance", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["notes"]


# ---------- GUI ----------

SAMPLE = {"exists": True, "principal": 1_063_707, "value": 1_070_000, "pnl": 6_293, "pnl_pct": 0.59,
          "start_date": "2026-10-10", "twr_pct": 0.59, "flows": [{"date": "2026-10-10", "amount": 1_063_707}],
          "benchmarks": [{"key": "spy", "label": "SPY만 샀다면", "value": 1_076_000, "pnl": 12_293, "pnl_pct": 1.16},
                         {"key": "balanced", "label": "SPY 60 / IEF 40 사서 그대로", "error": "시세 없음"},
                         {"key": "deposit", "label": "연 3% 예금이었다면", "value": 1_063_800, "pnl": 93,
                          "pnl_pct": 0.01}],
          "costs": {"live_orders": 0, "recorded_orders": 3, "recorded_cost": 821},
          "notes": ["입출금 기록이 없어 원금 전체를 시작일에 넣은 것으로 계산했습니다"]}


def test_performance_text():
    from alpha.performance_widgets import performance_detail, performance_headline, performance_rows

    h = performance_headline(SAMPLE)
    assert h == "넣은 돈 1,063,707원 → 지금 1,070,000원 (+0.6%) · 같은 기간 SPY만 샀다면 +1.2%"
    rows = performance_rows(SAMPLE)
    assert rows[0][0] == "에이전트 (실제)" and len(rows) == 4 and "계산 못 함" in rows[2][3]
    d = performance_detail(SAMPLE)
    assert "2026-10-10 시작" in d and "821원" in d and "※" in d
    assert "불러오지 못했습니다" in performance_headline({"error": "서버 꺼짐"})
    assert performance_rows({"exists": False}) == []
    assert "평가액" in performance_headline({"exists": True, "principal": 1, "value": None})


def test_performance_box_renders(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from alpha import performance_widgets as pw

    box = pw.PerformanceBox()
    box.show_data(SAMPLE)
    assert box.table.rowCount() == 4 and "SPY만 샀다면" in box.headline.text()

    calls = []
    monkeypatch.setattr(pw, "fetch_performance", lambda: calls.append(1) or SAMPLE)
    box.load()
    for w in list(box._workers):
        w.wait(3000)
    box.load()                                                    # 10분 안엔 다시 묻지 않는다
    assert calls == [1]


def test_started_at_survives_resync(ap):
    """'다시 맞추기'는 seeded_at 을 덮어쓴다 — 성과판 시작일(started_at)은 처음 날 그대로."""
    from test_myaccount import _kb

    from alpha_server import myaccount as M

    kb, _ = _kb()
    M.seed_shadow("kim", "kb", broker=kb, now=NOW)
    later = NOW + timedelta(days=3)
    M.seed_shadow("kim", "kb", broker=kb, now=later)
    cfg = ap.load_config("kim", M.SHADOW_PORTFOLIO)
    assert cfg["started_at"] == NOW.isoformat() and cfg["seeded_at"] == later.isoformat()


def test_partial_price_frames_are_not_cached(monkeypatch):
    """일부 종목 다운로드가 실패한 결과는 캐시하지 않는다 — 다음 요청에서 다시 받는다."""
    import pandas as pd
    from datetime import date

    from alpha_server import account_performance as ap
    from alpha_server import data_handler

    monkeypatch.setattr(ap, "_price_cache", {})
    monkeypatch.setattr(data_handler, "load_from_csv", lambda t: None)
    frame = pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-10-09"]))
    calls = []

    def fake_download(tickers, period="1y"):
        calls.append(list(tickers))
        return {"SPY": frame} if len(calls) == 1 else {t: frame for t in tickers}

    monkeypatch.setattr(data_handler, "download_many", fake_download)
    assert set(ap._load_frames(["SPY", "IEF"], date(2026, 10, 10))) == {"SPY"}
    assert set(ap._load_frames(["SPY", "IEF"], date(2026, 10, 10))) == {"SPY", "IEF"}
    assert len(calls) == 2

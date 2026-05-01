"""핵심 신규 모듈 단위 테스트.

CI에서 외부 의존성(yfinance/network/QuestDB) 없이 통과하도록 격리한다.
"""
from __future__ import annotations

import os
import tempfile

import pytest


def _set_isolated_home(monkeypatch, tmp_path):
    """모든 영속 파일을 임시 디렉터리로 리다이렉트."""
    monkeypatch.setenv("HOME", str(tmp_path))


# ---------- audit_log ----------
def test_audit_log_chain_integrity(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from importlib import reload

    from alpha_server import audit_log
    reload(audit_log)

    audit_log.record("auth", "login", actor="alice")
    audit_log.record("trade", "buy", actor="alice", ticker="AAPL", quantity=1)
    audit_log.record("trade", "sell", actor="alice", ticker="AAPL", quantity=1)

    assert audit_log.detect_tamper() is None
    summary = audit_log.metrics_summary()
    assert summary["total_events"] == 3
    assert summary["by_category"]["trade"] == 2


def test_audit_log_detects_tamper(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from importlib import reload

    from alpha_server import audit_log
    reload(audit_log)

    audit_log.record("system", "startup")
    audit_log.record("system", "shutdown")

    # 두 번째 줄을 손상시키기
    with open(audit_log.LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    import json as _json
    bad = _json.loads(lines[1])
    bad["fields"] = {"injected": True}
    lines[1] = _json.dumps(bad) + "\n"
    with open(audit_log.LOG_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    assert audit_log.detect_tamper() == 1


# ---------- auth ----------
def test_auth_password_hash_and_verify(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from importlib import reload

    from alpha_server import auth
    reload(auth)

    h = auth._hash_password("hunter2hunter2")
    assert h.startswith("scrypt$")
    assert auth._verify_password("hunter2hunter2", h)
    assert not auth._verify_password("wrong-password", h)


def test_auth_register_and_login_flow(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from importlib import reload

    from alpha_server import auth
    reload(auth)

    user = auth.register_user("alice", "password123")
    assert user.username == "alice"
    assert user.role == "user"

    authed = auth.authenticate("alice", "password123")
    token = auth.issue_token(authed)
    assert token.token_type == "bearer"

    decoded = auth._decode(token.access_token)
    assert decoded["sub"] == "alice"


def test_auth_register_rejects_weak_input(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from importlib import reload

    from alpha_server import auth
    reload(auth)

    with pytest.raises(Exception):
        auth.register_user("ab", "password123")  # username too short
    with pytest.raises(Exception):
        auth.register_user("alice", "short")  # password too short


def test_auth_bootstrap_first_admin(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from importlib import reload
    from alpha_server import auth
    reload(auth)

    assert auth.is_bootstrap_needed() is True
    user = auth.bootstrap_first_admin("nahyun", "longpassword")
    assert user.username == "nahyun" and user.role == "admin"
    assert auth.is_bootstrap_needed() is False
    # 한 번 만들어진 후 재시도하면 거부
    with pytest.raises(Exception):
        auth.bootstrap_first_admin("alice", "anotherone123")


# ---------- rate limiter ----------
def test_token_bucket_allows_then_throttles():
    from alpha_server.rate_limit import TokenBucket

    bucket = TokenBucket(capacity=3, refill_rate=0)  # 보충 없음
    assert bucket.allow()
    assert bucket.allow()
    assert bucket.allow()
    assert not bucket.allow()


# ---------- risk manager ----------
class _FakeBroker:
    def __init__(self, cash=10000.0, equity=10000.0, position=None):
        self.cash = cash
        self.equity = equity
        self.position = position

    def get_cash(self):
        return self.cash

    def get_position(self, ticker):
        return self.position

    def get_portfolio(self):
        return {"total_value": self.equity}

    def get_current_price(self, ticker):  # not used in these tests
        return None

    def execute_order(self, *args, **kwargs):  # not used
        return {}


def test_risk_position_size_caps_at_pct(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from alpha_server.risk_manager import RiskConfig, RiskManager

    rm = RiskManager(_FakeBroker(cash=10000, equity=10000), RiskConfig(max_position_pct=0.10))
    qty = rm.position_size("AAPL", price=100.0)
    assert qty == 10  # 10% of 10k = $1000 / $100


def test_risk_should_exit_stop_loss(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from alpha_server.brokers.base import Position
    from alpha_server.risk_manager import RiskConfig, RiskManager

    pos = Position(ticker="AAPL", quantity=10, avg_price=100.0)
    rm = RiskManager(
        _FakeBroker(equity=10000, position=pos),
        RiskConfig(stop_loss_pct=0.05, take_profit_pct=0.10),
    )
    assert rm.should_exit("AAPL", 94.0) == "stop_loss"
    assert rm.should_exit("AAPL", 111.0) == "take_profit"
    assert rm.should_exit("AAPL", 100.5) is None


def test_risk_daily_buy_limit(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    from alpha_server.risk_manager import RiskConfig, RiskManager

    rm = RiskManager(_FakeBroker(equity=10000), RiskConfig(max_daily_buys=2))
    rm.record_buy()
    rm.record_buy()
    ok, reason = rm.can_buy()
    assert not ok
    assert "한도" in reason


# ---------- broker abstraction ----------
def test_build_broker_default_is_mock(monkeypatch, tmp_path):
    _set_isolated_home(monkeypatch, tmp_path)
    monkeypatch.delenv("ALPHA_BROKER", raising=False)
    from alpha_server.brokers import build_broker, MockBroker

    assert isinstance(build_broker(), MockBroker)


# ---------- client modules ----------
def test_recommender_validates_horizon():
    from alpha import recommender

    out = recommender.recommend("invalid", 5)
    assert "error" in out


def test_recommender_format_table():
    from alpha import recommender

    payload = {
        "horizon": "medium",
        "top_n": 2,
        "total_assets_analyzed": 50,
        "recommendations": [{"symbol": "AAPL", "score": 0.92}, {"symbol": "MSFT", "score": 0.88}],
    }
    text = recommender.format_recommendation_table(payload)
    assert "AAPL" in text and "MSFT" in text


def test_portfolio_loader(tmp_path):
    import json
    from alpha import portfolio as pf

    path = tmp_path / "p.json"
    path.write_text(
        json.dumps({"holdings": [{"symbol": "AAPL", "quantity": 5, "purchase_price": 150}]}),
        encoding="utf-8",
    )
    data = pf.load_portfolio_file(str(path))
    assert data["holdings"][0]["symbol"] == "AAPL"


def test_portfolio_loader_rejects_invalid(tmp_path):
    import json
    from alpha import portfolio as pf

    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"oops": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        pf.load_portfolio_file(str(path))


def test_analysis_overlap():
    from alpha import analysis

    holdings = [{"symbol": "aapl"}, {"symbol": "TSLA"}]
    recs = {"recommendations": [{"symbol": "AAPL"}, {"symbol": "GOOG"}]}
    assert analysis.overlap_with_holdings(holdings, recs) == ["AAPL"]


def test_server_launcher_detects_running_server(monkeypatch):
    """is_server_up()이 mock된 health 응답을 정확히 분류하는지."""
    from alpha import server_launcher

    class _OkResp:
        status_code = 200

    class _BadResp:
        status_code = 500

    monkeypatch.setattr(server_launcher.requests, "get", lambda *a, **k: _OkResp())
    assert server_launcher.is_server_up() is True

    monkeypatch.setattr(server_launcher.requests, "get", lambda *a, **k: _BadResp())
    assert server_launcher.is_server_up() is False

    def _raise(*a, **k):
        raise server_launcher.requests.RequestException("nope")

    monkeypatch.setattr(server_launcher.requests, "get", _raise)
    assert server_launcher.is_server_up() is False


def test_server_launcher_dev_mode_uses_uvicorn(monkeypatch):
    """frozen이 아닐 때 ensure_server_running은 dev 스레드 경로로 가는지."""
    from alpha import server_launcher

    monkeypatch.setattr(server_launcher.sys, "frozen", False, raising=False)
    monkeypatch.setattr(server_launcher, "is_server_up", lambda: True)
    ok, msg = server_launcher.ensure_server_running()
    assert ok and "이미 실행 중" in msg


def test_server_launcher_prefers_embedded_path(monkeypatch, tmp_path):
    """frozen .app 환경에서 임베드된 AlphaServer를 시스템 경로보다 먼저 발견해야 한다.

    PyInstaller는 macOS .app 빌드 시 datas를 Contents/Frameworks/ 에 풀어놓는다.
    Contents/MacOS/AlphaClient (실행파일) → Contents/Frameworks/AlphaServer/AlphaServer.
    """
    from alpha import server_launcher

    # AlphaClient.app/Contents/{MacOS,Frameworks} 구조 흉내
    contents_dir = tmp_path / "AlphaClient.app" / "Contents"
    macos_dir = contents_dir / "MacOS"
    macos_dir.mkdir(parents=True)
    fake_client = macos_dir / "AlphaClient"
    fake_client.write_text("#!/bin/sh\n")
    fake_client.chmod(0o755)

    frameworks_server_dir = contents_dir / "Frameworks" / "AlphaServer"
    frameworks_server_dir.mkdir(parents=True)
    embedded_server = frameworks_server_dir / "AlphaServer"
    embedded_server.write_text("#!/bin/sh\necho ok\n")
    embedded_server.chmod(0o755)

    monkeypatch.setattr(server_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(server_launcher.sys, "executable", str(fake_client))
    # _MEIPASS는 onefile 모드에서만 의미가 있으므로 .app 케이스에서는 없는 게 맞다.
    monkeypatch.delattr(server_launcher.sys, "_MEIPASS", raising=False)

    paths = server_launcher._candidate_server_paths()
    assert paths, "임베드된 AlphaServer 경로를 하나 이상 발견해야 한다"
    # 첫 번째 후보가 임베드된 위치여야 한다 (시스템 경로보다 우선)
    assert paths[0] == embedded_server.resolve()

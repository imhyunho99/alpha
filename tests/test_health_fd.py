"""/health 가 fd 사용률을 함께 돌려주는지 검증한다.

fd 가 고갈되면 서버는 HTTP 연결조차 받지 못하므로 헬스체크는 무응답이 된다.
그 전에 사용률을 실어 보내는 것이 이 테스트의 대상이다.
alpha_server.yf_session 은 아직 없을 수 있어(다른 작업이 만드는 중) 전부 가짜로 주입한다.
"""
from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ALPHA_JWT_SECRET", "test-secret-do-not-use-in-prod")

    from importlib import reload

    from alpha_server import audit_log, auth
    reload(audit_log)
    reload(auth)

    from alpha_server.main import app
    return TestClient(app)


def _fake_yf_session(monkeypatch, fd_pressure):
    """alpha_server.yf_session 자리에 fd_pressure 만 가진 모듈을 끼워 넣는다."""
    import alpha_server

    module = types.ModuleType("alpha_server.yf_session")
    module.fd_pressure = fd_pressure
    monkeypatch.setitem(sys.modules, "alpha_server.yf_session", module)
    monkeypatch.setattr(alpha_server, "yf_session", module, raising=False)
    return module


def _hide_yf_session(monkeypatch):
    """모듈이 아예 없는 상태를 만든다 (sys.modules 의 None 은 ImportError 를 낸다)."""
    import alpha_server

    monkeypatch.setitem(sys.modules, "alpha_server.yf_session", None)
    monkeypatch.delattr(alpha_server, "yf_session", raising=False)


def test_health_reports_fd_when_pressure_is_low(client, monkeypatch):
    _fake_yf_session(monkeypatch, lambda: {"open": 120, "limit": 4096, "usage_pct": 120 / 4096})

    r = client.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "warning" not in body
    assert body["fd"]["open"] == 120
    assert body["fd"]["limit"] == 4096
    assert body["fd"]["usage_pct"] == pytest.approx(120 / 4096)
    assert body["ts"].endswith("Z")


def test_health_degrades_when_fd_usage_exceeds_threshold(client, monkeypatch):
    _fake_yf_session(monkeypatch, lambda: {"open": 3900, "limit": 4096, "usage_pct": 0.95})

    body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["fd"]["usage_pct"] == pytest.approx(0.95)
    assert "3900" in body["warning"] and "4096" in body["warning"]


def test_health_stays_ok_exactly_at_threshold(client, monkeypatch):
    """0.8 은 "넘은" 것이 아니다. 경계에서 매번 degraded 를 울리면 아무도 안 본다."""
    _fake_yf_session(monkeypatch, lambda: {"open": 3276, "limit": 4096, "usage_pct": 0.8})

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert "warning" not in body


def test_health_survives_fd_pressure_raising(client, monkeypatch):
    def boom():
        raise OSError("fd 를 셀 수 없음")

    _fake_yf_session(monkeypatch, boom)

    r = client.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "fd" not in body


def test_health_works_without_yf_session_module(client, monkeypatch):
    _hide_yf_session(monkeypatch)

    r = client.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "fd" not in body
    assert set(body) == {"status", "ts"}


# --- 자동 운용 루프 로그 ---

def test_autopilot_log_note_includes_fd(monkeypatch):
    from alpha_server.autopilot import runner

    _fake_yf_session(monkeypatch, lambda: {"open": 123, "limit": 4096, "usage_pct": 123 / 4096})

    assert runner._fd_note() == " · fd 123/4096"


def test_autopilot_log_note_empty_without_yf_session(monkeypatch):
    from alpha_server.autopilot import runner

    _hide_yf_session(monkeypatch)

    assert runner._fd_note() == ""

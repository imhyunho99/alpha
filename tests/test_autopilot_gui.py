"""Autopilot GUI 탭 + core 클라이언트 함수 테스트.

서버(Task 13)가 아직 없으므로 core 함수는 {"error": ...}를 돌려준다.
그 상태에서 위젯이 크래시 없이 안내 문구를 띄우는지가 이 테스트의 핵심이다.
"""
from __future__ import annotations

import os

import pytest

# QApplication을 만들기 전에 오프스크린 백엔드를 강제한다 (CI/헤드리스).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from alpha import autopilot_widgets  # noqa: E402
from alpha import core  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def offline(monkeypatch):
    """서버가 없는 상태 — 모든 autopilot core 함수가 error dict를 돌려준다."""
    err = {"error": "서버에 연결할 수 없습니다: connection refused"}
    for name in (
        "autopilot_get_config",
        "autopilot_set_config",
        "autopilot_state",
        "autopilot_backtest",
        "autopilot_briefing",
        "autopilot_alerts",
    ):
        monkeypatch.setattr(core, name, lambda *a, _e=err, **kw: _e)
    return err


def _drain(tab):
    """띄운 백그라운드 워커가 끝날 때까지 기다린다 (QThread 파괴 경고 방지)."""
    for worker in list(getattr(tab, "_workers", [])):
        worker.wait(3000)
    QApplication.processEvents()


# --- core 클라이언트 함수 ---


def test_core_autopilot_functions_hit_expected_endpoints(monkeypatch):
    calls = []

    def fake(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return {"ok": True}

    monkeypatch.setattr(core, "_handle_request", fake)

    assert core.autopilot_get_config() == {"ok": True}
    assert core.autopilot_set_config(7, 5_000_000.0, True) == {"ok": True}
    assert core.autopilot_state() == {"ok": True}
    assert core.autopilot_backtest(3, 1_000_000.0, years=5) == {"ok": True}
    assert core.autopilot_briefing("weekly") == {"ok": True}
    assert core.autopilot_alerts() == {"ok": True}

    methods_endpoints = [(m, e.split("?")[0]) for m, e, _ in calls]
    assert methods_endpoints == [
        ("get", "/autopilot/config"),
        ("put", "/autopilot/config"),
        ("get", "/autopilot/state"),
        ("post", "/autopilot/backtest"),
        ("get", "/autopilot/briefing"),
        ("get", "/autopilot/alerts"),
    ]

    assert calls[1][2]["json"] == {
        "temperature": 7,
        "capital": 5_000_000.0,
        "active": True,
    }
    assert calls[3][2]["json"] == {
        "temperature": 3,
        "capital": 1_000_000.0,
        "years": 5,
    }
    assert "period=weekly" in calls[4][1]


def test_core_autopilot_returns_error_dict_when_server_is_down(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(core.requests, "request", boom)
    result = core.autopilot_get_config()
    assert isinstance(result, dict)
    assert "error" in result


# --- 워커 ---


def test_worker_converts_exception_to_error_dict(qapp):
    def boom():
        raise RuntimeError("터졌다")

    worker = autopilot_widgets._Worker(boom)
    seen = []
    worker.done.connect(seen.append)
    worker.run()  # 스레드를 띄우지 않고 직접 실행

    assert len(seen) == 1
    assert seen[0]["error"] == "터졌다"


# --- 서버 없는 상태에서의 탭 ---


def test_tab_builds_without_server(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    assert tab.slider.minimum() == 1
    assert tab.slider.maximum() == 10
    assert tab.capital.value() > 0
    tab.deleteLater()


def test_backtest_error_shows_guidance(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_backtest({"error": "서버에 연결할 수 없습니다"})
    assert "서버" in tab.curve_summary.text()
    tab.deleteLater()


def test_backtest_non_dict_result_does_not_crash(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_backtest(None)
    assert tab.curve_summary.text() != ""
    tab.deleteLater()


def test_state_error_shows_guidance(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_state({"error": "401"})
    assert "상태" in tab.dashboard.toPlainText()
    tab.deleteLater()


def test_config_error_keeps_defaults(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    before = tab.slider.value()
    tab._on_config({"error": "no server"})
    _drain(tab)
    assert tab.slider.value() == before
    assert tab.start_btn.text() == "자동 운용 시작"
    tab.deleteLater()


def test_toggle_without_server_does_not_crash(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._toggle()
    _drain(tab)
    tab.deleteLater()


def test_refresh_state_without_server_does_not_crash(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._refresh_state()
    _drain(tab)
    tab.deleteLater()


# --- 정상 응답 ---


def test_backtest_success_updates_curve_and_profile(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_backtest(
        {
            "curve": [{"equity": 1_000_000.0}, {"equity": 1_200_000.0}, {"equity": 900_000.0}],
            "final_equity": 900_000.0,
            "max_drawdown_pct": 25.0,
            "total_fills": 12,
            "liquidated_at": "2026-03-01",
            "liquidated_index": 0.66,
            "profile": {
                "deploy_pct": 60.0,
                "max_holdings": 10,
                "stop_loss_pct": 7.0,
                "max_leverage": 1.0,
            },
        }
    )
    summary = tab.curve_summary.text()
    assert "900,000" in summary
    assert "25.0" in summary
    assert "12" in summary
    assert "청산" in summary
    assert "10종목" in tab.profile_label.text()
    tab.deleteLater()


def test_state_success_lists_holdings_and_alerts(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_state(
        {
            "equity": 12_345_678.0,
            "return_pct": 23.45,
            "cash": 1_000_000.0,
            "leverage": 1.5,
            "holdings": [{"ticker": "AAPL", "quantity": 3.5}],
            "alerts": [{"severity": "warning", "message": "낙폭 확대"}],
        }
    )
    text = tab.dashboard.toPlainText()
    assert "12,345,678" in text
    assert "AAPL" in text
    assert "WARNING" in text
    assert "낙폭 확대" in text
    tab.deleteLater()


def test_state_tolerates_missing_fields(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_state({})
    assert tab.dashboard.toPlainText() != ""
    tab.deleteLater()


# --- 곡선 렌더링 ---


def test_equity_curve_renders_placeholder_when_empty(qapp):
    curve = autopilot_widgets.EquityCurve()
    curve.resize(320, 240)
    curve.set_curve([])
    pixmap = QPixmap(curve.size())
    curve.render(pixmap)  # paintEvent가 예외 없이 끝나야 한다


def test_equity_curve_renders_points_and_liquidation_marker(qapp):
    curve = autopilot_widgets.EquityCurve()
    curve.resize(320, 240)
    curve.set_curve([100.0, 130.0, 90.0, 140.0], liquidated_index=0.5)
    pixmap = QPixmap(curve.size())
    curve.render(pixmap)


def test_equity_curve_survives_flat_series(qapp):
    curve = autopilot_widgets.EquityCurve()
    curve.resize(320, 240)
    curve.set_curve([100.0, 100.0, 100.0])  # span == 0 → 0으로 나누면 안 된다
    pixmap = QPixmap(curve.size())
    curve.render(pixmap)

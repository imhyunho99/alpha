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


AUTOPILOT_CORE_FUNCTIONS = (
    "autopilot_portfolios",
    "autopilot_get_config",
    "autopilot_set_config",
    "autopilot_state",
    "autopilot_backtest",
    "autopilot_briefing",
    "autopilot_alerts",
)


@pytest.fixture
def offline(monkeypatch):
    """서버가 없는 상태 — 모든 autopilot core 함수가 error dict를 돌려준다."""
    err = {"error": "서버에 연결할 수 없습니다: connection refused"}
    for name in AUTOPILOT_CORE_FUNCTIONS:
        monkeypatch.setattr(core, name, lambda *a, _e=err, **kw: dict(_e))
    return err


class _SilentDialogs:
    """헤드리스에서 모달은 영원히 블로킹된다. 호출만 기록하는 스텁으로 바꾼다.

    PySide6 타입은 속성 대입이 막혀 있어 클래스 자체를 모듈 네임스페이스에서
    갈아끼운다.
    """

    def __init__(self):
        self.warnings = []
        self.prompts = []
        self.next_text = ("", False)
        outer = self

        class MessageBoxStub:
            @staticmethod
            def warning(*args, **kwargs):
                outer.warnings.append(args[2] if len(args) > 2 else "")
                return None

            @staticmethod
            def information(*args, **kwargs):
                return None

            @staticmethod
            def critical(*args, **kwargs):
                return None

        class InputDialogStub:
            @staticmethod
            def getText(*args, **kwargs):
                outer.prompts.append(args)
                return outer.next_text

        self.message_box = MessageBoxStub
        self.input_dialog = InputDialogStub


@pytest.fixture
def dialogs(monkeypatch):
    stub = _SilentDialogs()
    monkeypatch.setattr(autopilot_widgets, "QMessageBox", stub.message_box)
    monkeypatch.setattr(autopilot_widgets, "QInputDialog", stub.input_dialog)
    return stub


def _drain(tab, rounds=8):
    """백그라운드 워커가 다 끝날 때까지 돌린다.

    콜백은 큐드 커넥션이라 processEvents가 있어야 배달되고, 배달된 콜백이
    또 워커를 띄우기도 한다. 그래서 한 번이 아니라 잦아들 때까지 반복한다.
    """
    for _ in range(rounds):
        workers = list(getattr(tab, "_workers", []))
        if not workers:
            break
        for worker in workers:
            worker.wait(3000)
        QApplication.processEvents()
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
        "portfolio": "default",
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


# --- 포트폴리오: core 클라이언트 ---


def test_core_autopilot_portfolios_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core, "_handle_request",
        lambda m, e, **kw: calls.append((m, e)) or {"portfolios": []},
    )
    assert core.autopilot_portfolios() == {"portfolios": []}
    assert calls == [("get", "/autopilot/portfolios")]


def test_core_autopilot_passes_portfolio_in_query_and_body(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core, "_handle_request",
        lambda m, e, **kw: calls.append((m, e, kw)) or {},
    )

    core.autopilot_get_config("aggressive")
    core.autopilot_state("aggressive")
    core.autopilot_alerts("aggressive")
    core.autopilot_briefing("weekly", "aggressive")
    core.autopilot_set_config(8, 10_000_000.0, True, "aggressive")

    assert "portfolio=aggressive" in calls[0][1]
    assert "portfolio=aggressive" in calls[1][1]
    assert "portfolio=aggressive" in calls[2][1]
    assert "period=weekly" in calls[3][1] and "portfolio=aggressive" in calls[3][1]
    assert calls[4][2]["json"]["portfolio"] == "aggressive"


def test_core_autopilot_url_encodes_korean_portfolio_names(monkeypatch):
    from urllib.parse import quote

    calls = []
    monkeypatch.setattr(
        core, "_handle_request",
        lambda m, e, **kw: calls.append((m, e, kw)) or {},
    )
    core.autopilot_state("성장형")
    assert quote("성장형") in calls[0][1]
    assert "성장형" not in calls[0][1]  # 원문 그대로 들어가면 안 된다


def test_core_autopilot_defaults_keep_old_callers_working(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core, "_handle_request",
        lambda m, e, **kw: calls.append((m, e, kw)) or {},
    )
    core.autopilot_get_config()
    core.autopilot_state()
    core.autopilot_alerts()
    core.autopilot_briefing()
    core.autopilot_set_config(5, 1_000_000.0, False)

    for _, endpoint, _kw in calls[:4]:
        assert "portfolio=default" in endpoint
    assert calls[4][2]["json"]["portfolio"] == "default"


# --- 포트폴리오 이름 검증 ---


@pytest.mark.parametrize(
    "name",
    ["default", "aggressive", "온도8", "a", "A-1_b", "성장형", "x" * 32],
)
def test_valid_portfolio_names_are_accepted(name):
    assert autopilot_widgets.is_valid_portfolio_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "x" * 33,
        "has space",
        "slash/name",
        "dot.name",
        "../escape",
        "emoji🤖",
        "tab\tname",
        None,
        123,
    ],
)
def test_invalid_portfolio_names_are_rejected(name):
    assert autopilot_widgets.is_valid_portfolio_name(name) is False


# --- 포트폴리오 셀렉터 위젯 ---


def test_tab_has_portfolio_selector(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    assert tab.portfolio_combo is not None
    assert tab.new_portfolio_btn is not None
    tab.deleteLater()


def test_portfolio_list_error_falls_back_to_default(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    assert tab.portfolio_combo.count() == 1
    assert tab.current_portfolio() == "default"
    tab.deleteLater()


def test_portfolio_combo_shows_temperature_and_return(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_portfolios(
        {
            "portfolios": [
                {"portfolio": "default", "temperature": 3, "return_pct": -1.5},
                {"portfolio": "aggressive", "temperature": 8, "return_pct": 3.2},
            ]
        }
    )
    labels = [tab.portfolio_combo.itemText(i) for i in range(tab.portfolio_combo.count())]
    names = [tab.portfolio_combo.itemData(i) for i in range(tab.portfolio_combo.count())]
    assert names == ["default", "aggressive"]
    assert "온도 8" in labels[1]
    assert "+3.2%" in labels[1]
    assert "-1.5%" in labels[0]
    _drain(tab)
    tab.deleteLater()


def test_portfolio_list_tolerates_missing_fields(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_portfolios({"portfolios": [{"portfolio": "bare"}]})
    assert tab.portfolio_combo.itemData(0) == "bare"
    _drain(tab)
    tab.deleteLater()


def test_portfolio_list_non_dict_does_not_crash(qapp, offline):
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_portfolios(None)
    assert tab.current_portfolio() == "default"
    _drain(tab)
    tab.deleteLater()


# --- 콤보 변경 → 해당 포트폴리오로 재조회 ---


def test_changing_portfolio_refetches_with_that_name(qapp, monkeypatch):
    seen = {"config": [], "state": [], "backtest": []}
    err = {"error": "no server"}

    monkeypatch.setattr(
        core, "autopilot_portfolios",
        lambda *a, **kw: {
            "portfolios": [
                {"portfolio": "default", "temperature": 3, "return_pct": 0.0},
                {"portfolio": "aggressive", "temperature": 8, "return_pct": 3.2},
            ]
        },
    )
    monkeypatch.setattr(
        core, "autopilot_get_config",
        lambda portfolio="default": seen["config"].append(portfolio) or dict(err),
    )
    monkeypatch.setattr(
        core, "autopilot_state",
        lambda portfolio="default": seen["state"].append(portfolio) or dict(err),
    )
    monkeypatch.setattr(
        core, "autopilot_backtest",
        lambda t, c, y=3: seen["backtest"].append((t, c)) or dict(err),
    )
    monkeypatch.setattr(core, "autopilot_set_config", lambda *a, **kw: dict(err))

    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    seen["config"].clear()
    seen["state"].clear()

    idx = tab.portfolio_combo.findData("aggressive")
    assert idx >= 0
    tab.portfolio_combo.setCurrentIndex(idx)
    _drain(tab)

    assert tab.current_portfolio() == "aggressive"
    assert "aggressive" in seen["config"]
    assert "aggressive" in seen["state"]
    tab.deleteLater()


def test_repopulating_list_does_not_refetch_for_every_row(qapp, monkeypatch):
    """콤보를 다시 채우는 동안 행마다 재조회가 터지면 안 된다."""
    err = {"error": "no server"}
    fetched = []
    monkeypatch.setattr(core, "autopilot_portfolios", lambda *a, **kw: dict(err))
    monkeypatch.setattr(
        core, "autopilot_get_config",
        lambda portfolio="default": fetched.append(portfolio) or dict(err),
    )
    monkeypatch.setattr(core, "autopilot_state", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_backtest", lambda *a, **kw: dict(err))
    monkeypatch.setattr(core, "autopilot_set_config", lambda *a, **kw: dict(err))

    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    fetched.clear()

    tab._on_portfolios(
        {
            "portfolios": [
                {"portfolio": f"p{i}", "temperature": i, "return_pct": 0.0}
                for i in range(1, 6)
            ]
        }
    )
    _drain(tab)
    # 선택된 하나에 대해서만 다시 불러온다
    assert len(fetched) <= 1
    tab.deleteLater()


# --- 새 포트폴리오 ---


def test_new_portfolio_rejects_invalid_name(qapp, offline, dialogs):
    created = []
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._create_portfolio = lambda name: created.append(name)

    dialogs.next_text = ("bad name/../", True)
    tab._on_new_portfolio()
    _drain(tab)

    assert created == []
    assert dialogs.warnings, "규칙 위반 이름은 경고를 띄워야 한다"
    tab.deleteLater()


def test_new_portfolio_cancel_does_nothing(qapp, offline, dialogs):
    created = []
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._create_portfolio = lambda name: created.append(name)

    dialogs.next_text = ("whatever", False)  # 사용자가 취소
    tab._on_new_portfolio()
    _drain(tab)

    assert created == []
    assert not dialogs.warnings
    tab.deleteLater()


def test_new_portfolio_creates_with_valid_name(qapp, monkeypatch, dialogs):
    err = {"error": "no server"}
    saved = []
    monkeypatch.setattr(core, "autopilot_portfolios", lambda *a, **kw: dict(err))
    monkeypatch.setattr(core, "autopilot_get_config", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_state", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_backtest", lambda *a, **kw: dict(err))
    monkeypatch.setattr(
        core, "autopilot_set_config",
        lambda temperature, capital, active, portfolio="default": saved.append(
            (temperature, capital, active, portfolio)
        ) or {"temperature": temperature, "capital": capital, "active": active,
              "portfolio": portfolio},
    )

    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)

    dialogs.next_text = ("aggressive", True)
    tab._on_new_portfolio()
    _drain(tab)

    assert saved, "유효한 이름이면 서버에 생성 요청을 보내야 한다"
    assert saved[0][3] == "aggressive"
    assert saved[0][2] is False, "새 포트폴리오는 비활성으로 만든다"
    assert tab.current_portfolio() == "aggressive"
    tab.deleteLater()


def test_new_portfolio_rejects_duplicate_name(qapp, offline, dialogs):
    created = []
    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab._on_portfolios({"portfolios": [{"portfolio": "aggressive", "temperature": 8}]})
    _drain(tab)
    tab._create_portfolio = lambda name: created.append(name)

    dialogs.next_text = ("aggressive", True)
    tab._on_new_portfolio()
    _drain(tab)

    assert created == []
    assert dialogs.warnings
    tab.deleteLater()


# --- 포트폴리오 인자를 붙여도 에러 내성 유지 ---


def test_toggle_uses_current_portfolio(qapp, monkeypatch, dialogs):
    err = {"error": "no server"}
    saved = []
    monkeypatch.setattr(
        core, "autopilot_portfolios",
        lambda *a, **kw: {"portfolios": [
            {"portfolio": "default", "temperature": 3, "return_pct": 0.0},
            {"portfolio": "aggressive", "temperature": 8, "return_pct": 3.2},
        ]},
    )
    monkeypatch.setattr(core, "autopilot_get_config", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_state", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_backtest", lambda *a, **kw: dict(err))
    monkeypatch.setattr(
        core, "autopilot_set_config",
        lambda temperature, capital, active, portfolio="default": saved.append(portfolio)
        or dict(err),
    )

    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    tab.portfolio_combo.setCurrentIndex(tab.portfolio_combo.findData("aggressive"))
    _drain(tab)
    saved.clear()

    tab._toggle()
    _drain(tab)
    assert saved == ["aggressive"]
    tab.deleteLater()


def test_refresh_reloads_portfolio_list_too(qapp, monkeypatch):
    err = {"error": "no server"}
    hits = []
    monkeypatch.setattr(
        core, "autopilot_portfolios", lambda *a, **kw: hits.append(1) or dict(err)
    )
    monkeypatch.setattr(core, "autopilot_get_config", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_state", lambda portfolio="default": dict(err))
    monkeypatch.setattr(core, "autopilot_backtest", lambda *a, **kw: dict(err))
    monkeypatch.setattr(core, "autopilot_set_config", lambda *a, **kw: dict(err))

    tab = autopilot_widgets.AutopilotTab()
    _drain(tab)
    before = len(hits)
    tab._refresh_state()
    _drain(tab)
    assert len(hits) > before
    tab.deleteLater()

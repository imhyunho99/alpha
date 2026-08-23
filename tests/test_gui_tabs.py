"""메인 윈도우 탭 구성 테스트.

Task 14 Step 1: 분석 / 자동 운용(AutopilotTab) / 전략(StrategyChatTab) 3탭.
기존 메서드·메뉴·상태바가 살아 있는지 함께 지킨다.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from alpha import core  # noqa: E402
from alpha import gui  # noqa: E402
from alpha import strategy_widgets  # noqa: E402
from alpha.autopilot_widgets import AutopilotTab  # noqa: E402
from alpha.strategy_widgets import StrategyChatTab  # noqa: E402


class _SilentMessageBox:
    """StrategyChatTab는 생성 중 서버 조회에 실패하면 모달을 띄운다.

    헤드리스 테스트에서 모달은 영원히 블로킹되므로 무해한 스텁으로 바꾼다.
    """

    Yes = 1
    No = 0

    @staticmethod
    def warning(*args, **kwargs):
        return None

    @staticmethod
    def information(*args, **kwargs):
        return None

    @staticmethod
    def critical(*args, **kwargs):
        return None

    @staticmethod
    def question(*args, **kwargs):
        return 0


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def offline(monkeypatch):
    """서버 없는 상태. GUI 생성 중 네트워크를 타지 않게 전부 막는다."""
    err = {"error": "서버에 연결할 수 없습니다"}
    monkeypatch.setattr(strategy_widgets, "QMessageBox", _SilentMessageBox)
    monkeypatch.setattr(gui, "QMessageBox", _SilentMessageBox)
    monkeypatch.setattr(core, "server_health", lambda *a, **kw: dict(err))
    monkeypatch.setattr(core, "is_logged_in", lambda *a, **kw: False)
    monkeypatch.setattr(core, "_handle_request", lambda *a, **kw: dict(err))
    for name in (
        "autopilot_get_config",
        "autopilot_set_config",
        "autopilot_state",
        "autopilot_backtest",
    ):
        monkeypatch.setattr(core, name, lambda *a, _e=err, **kw: dict(_e))
    return err


@pytest.fixture
def window(qapp, offline):
    w = gui.AlphaGUI()
    yield w
    w.progress_timer.stop()
    w.health_timer.stop()
    for tab in (w.tabs.widget(i) for i in range(w.tabs.count())):
        for worker in list(getattr(tab, "_workers", [])):
            worker.wait(3000)
    QApplication.processEvents()
    w.deleteLater()


def _tab_titles(w):
    return [w.tabs.tabText(i) for i in range(w.tabs.count())]


def _tab_widgets(w):
    return [w.tabs.widget(i) for i in range(w.tabs.count())]


# --- 탭 구성 ---


def test_central_widget_is_a_tab_widget(window):
    assert isinstance(window.centralWidget(), QTabWidget)
    assert window.centralWidget() is window.tabs


def test_has_analysis_autopilot_and_strategy_tabs(window):
    titles = _tab_titles(window)
    assert window.tabs.count() == 3
    assert any("분석" in t for t in titles), titles
    assert any("자동 운용" in t for t in titles), titles
    assert any("전략" in t for t in titles), titles


def test_autopilot_tab_is_present_and_typed(window):
    matches = [w for w in _tab_widgets(window) if isinstance(w, AutopilotTab)]
    assert len(matches) == 1
    assert window.autopilot_tab is matches[0]


def test_strategy_tab_survives(window):
    matches = [w for w in _tab_widgets(window) if isinstance(w, StrategyChatTab)]
    assert len(matches) == 1
    assert window.strategy_tab is matches[0]


def test_autopilot_sits_between_analysis_and_strategy(window):
    widgets = _tab_widgets(window)
    autopilot_idx = widgets.index(window.autopilot_tab)
    strategy_idx = widgets.index(window.strategy_tab)
    assert 0 < autopilot_idx < strategy_idx


# --- 기존 요소 회귀 ---


def test_analysis_tab_keeps_existing_boxes(window):
    analysis = window.tabs.widget(0)
    titles = {
        box.title()
        for box in analysis.findChildren(gui.QGroupBox)
    }
    assert "작업 진행 상황" in titles
    assert len(titles) >= 4, titles


def test_existing_builder_methods_still_exist(window):
    for name in (
        "create_progress_box",
        "create_control_box",
        "create_analytics_box",
        "create_result_box",
        "create_status_bar",
        "check_progress",
    ):
        assert callable(getattr(window, name)), name


def test_menu_and_status_bar_survive(window):
    menus = [a.text() for a in window.menuBar().actions()]
    assert "계정" in menus
    assert window.statusBar is not None


def test_analysis_controls_still_reachable(window):
    # 분석 탭의 핵심 위젯들이 여전히 인스턴스 속성으로 남아 있어야 한다
    for name in ("result_text", "progress_bar", "progress_label"):
        assert hasattr(window, name), name


# --- 서버 없어도 크래시 없음 ---


def test_window_builds_without_server(window):
    assert window.windowTitle().startswith("Alpha")
    assert window.tabs.count() == 3

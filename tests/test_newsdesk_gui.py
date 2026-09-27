"""뉴스 자동매매 GUI 탭 + core 클라이언트 함수 테스트.

서버 /newsdesk API 는 아직 없으므로 core 함수는 monkeypatch 로 흉내 낸다.
서버가 없거나 응답이 비어도 탭이 죽지 않는지, 받은 값을 사람이 읽을 문구로
보여주는지가 핵심이다.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from alpha import core  # noqa: E402
from alpha import news_widgets  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


ERR = {"error": "서버에 연결할 수 없습니다: connection refused"}

NEWS_CORE_FUNCTIONS = (
    "autopilot_portfolios",
    "autopilot_get_config",
    "autopilot_set_config",
    "newsdesk_style_preview",
    "newsdesk_set_style",
    "newsdesk_get_style",
    "newsdesk_state",
)


@pytest.fixture
def offline(monkeypatch):
    for name in NEWS_CORE_FUNCTIONS:
        monkeypatch.setattr(core, name, lambda *a, **kw: dict(ERR))


class _SilentDialogs:
    """헤드리스에서 모달은 영원히 블로킹된다. 호출만 기록하는 스텁."""

    def __init__(self):
        self.warnings = []
        self.infos = []
        self.next_text = ("", False)
        outer = self

        class MessageBoxStub:
            @staticmethod
            def warning(*args, **kwargs):
                outer.warnings.append(args[2] if len(args) > 2 else "")

            @staticmethod
            def information(*args, **kwargs):
                outer.infos.append(args[2] if len(args) > 2 else "")

        class InputDialogStub:
            @staticmethod
            def getText(*args, **kwargs):
                return outer.next_text

        self.message_box = MessageBoxStub
        self.input_dialog = InputDialogStub


@pytest.fixture
def dialogs(monkeypatch):
    stub = _SilentDialogs()
    monkeypatch.setattr(news_widgets, "QMessageBox", stub.message_box)
    monkeypatch.setattr(news_widgets, "QInputDialog", stub.input_dialog)
    return stub


def _drain(tab, rounds=8):
    for _ in range(rounds):
        workers = list(getattr(tab, "_workers", []))
        if not workers:
            break
        for worker in workers:
            worker.wait(3000)
        QApplication.processEvents()
    QApplication.processEvents()


def _table_text(table) -> str:
    cells = []
    for r in range(table.rowCount()):
        for c in range(table.columnCount()):
            item = table.item(r, c)
            if item is not None:
                cells.append(item.text())
    return " | ".join(cells)


# --- core 클라이언트 ---


def _record(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core, "_handle_request",
        lambda m, e, **kw: calls.append((m, e, kw)) or {"ok": True},
    )
    return calls


def test_core_newsdesk_endpoints(monkeypatch):
    calls = _record(monkeypatch)
    assert core.newsdesk_style_preview("반도체 위주") == {"ok": True}
    core.newsdesk_set_style("뉴스1", "반도체 위주")
    core.newsdesk_get_style("뉴스1")
    core.newsdesk_state("뉴스1")

    assert [(m, e.split("?")[0]) for m, e, _ in calls] == [
        ("post", "/newsdesk/style/preview"),
        ("put", "/newsdesk/style"),
        ("get", "/newsdesk/style"),
        ("get", "/newsdesk/state"),
    ]
    assert calls[0][2]["json"] == {"text": "반도체 위주"}
    assert calls[1][2]["json"] == {"portfolio": "뉴스1", "text": "반도체 위주"}


def test_core_newsdesk_url_encodes_portfolio(monkeypatch):
    from urllib.parse import quote

    calls = _record(monkeypatch)
    core.newsdesk_get_style("뉴스형")
    core.newsdesk_state("뉴스형")
    for _, endpoint, _ in calls:
        assert quote("뉴스형") in endpoint
        assert "뉴스형" not in endpoint


def test_core_set_config_sends_mode_only_when_given(monkeypatch):
    calls = _record(monkeypatch)
    core.autopilot_set_config(5, 1_000_000.0, False, "p")
    core.autopilot_set_config(5, 1_000_000.0, False, "p", mode="news")
    assert "mode" not in calls[0][2]["json"]  # 기존 호출은 그대로
    assert calls[1][2]["json"]["mode"] == "news"


# --- 순수 함수 ---


@pytest.mark.parametrize(
    "key,label",
    [
        ("news:earnings", "실적 뉴스"),
        ("news:regulation", "규제 뉴스"),
        ("news:mna", "인수합병 뉴스"),
        ("model", "모델 예측"),
        ("news:unknown_kind", "news:unknown_kind"),
    ],
)
def test_signal_label(key, label):
    assert news_widgets.signal_label(key) == label


def test_every_category_has_korean_label():
    from alpha_server.newsdesk.models import CATEGORIES

    for cat in CATEGORIES:
        assert news_widgets.signal_label(f"news:{cat}") != f"news:{cat}"


def test_trust_mark():
    assert "높" in news_widgets.trust_mark(1.4)
    assert "낮" in news_widgets.trust_mark(0.6)
    assert "기준" in news_widgets.trust_mark(1.0)


def test_brake_text():
    text = news_widgets.brake_text(7.5, 0.62)
    assert text == "손실 브레이크: 고점 대비 -7.5% → 투자 비중 62%"


def test_news_portfolios_filters_by_mode():
    entries = [
        {"portfolio": "default"},                 # mode 없음 → model 취급
        {"portfolio": "hot", "mode": "model"},
        {"portfolio": "뉴스1", "mode": "news"},
        {"mode": "news"},                         # 이름 없음 → 버림
        "garbage",
    ]
    assert [e["portfolio"] for e in news_widgets.news_portfolios(entries)] == ["뉴스1"]


# --- 탭: 서버 없음 ---


def test_tab_builds_without_server(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    assert tab.current_portfolio() is None
    assert tab.start_btn.isEnabled() is False
    assert tab.style_edit.placeholderText()  # 예시 문구가 있다
    tab.deleteLater()


def test_portfolio_combo_shows_only_news_mode(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_portfolios({"portfolios": [
        {"portfolio": "default", "temperature": 5},
        {"portfolio": "뉴스1", "mode": "news", "return_pct": 2.5, "active": True},
    ]})
    _drain(tab)
    assert tab.portfolio_combo.count() == 1
    assert tab.current_portfolio() == "뉴스1"
    assert "+2.5%" in tab.portfolio_combo.itemText(0)
    assert tab.start_btn.isEnabled()
    tab.deleteLater()


def test_state_error_shows_guidance(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_state({"error": "401"})
    assert "가져오지 못했습니다" in tab.summary_label.text()
    tab._on_state(None)
    tab.deleteLater()


def test_preview_error_shows_guidance(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_preview({"error": "no server"})
    assert tab.notes_list.count() == 1
    assert "서버" in tab.notes_list.item(0).text()
    tab.deleteLater()


# --- 탭: 정상 응답 ---


STATE = {
    "portfolio": "뉴스1",
    "equity": 10_523_000.0,
    "return_pct": 5.23,
    "drawdown_pct": 3.2,
    "exposure_multiplier": 0.84,
    "trust": {"news:earnings": 1.35, "news:regulation": 0.55, "model": 1.0},
    "holdings": [
        {"ticker": "NVDA", "value": 2_000_000.0, "pnl_pct": -4.0, "multiplier": 0.84},
    ],
    "decisions": [
        {"at": "2026-09-27T09:03:00+00:00", "action": "sell", "ticker": "TSLA",
         "reason": "규제 악재 → 보유 즉시 정리"},
    ],
    "news": [
        {"ticker": "NVDA", "title": "Nvidia beats estimates", "url": "https://x.test/a",
         "sentiment": 0.72, "category": "earnings",
         "published_at": "2026-09-27T08:00:00+00:00", "model": "finbert"},
    ],
    "interpreter": "finbert",
}


def test_state_renders_everything(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_state(STATE)

    summary = tab.summary_label.text()
    assert "10,523,000" in summary and "+5.23%" in summary
    assert tab.brake_label.text() == "손실 브레이크: 고점 대비 -3.2% → 투자 비중 84%"

    trust = _table_text(tab.trust_table)
    assert "실적 뉴스" in trust and "규제 뉴스" in trust and "모델 예측" in trust
    assert "1.35" in trust and "높" in trust and "낮" in trust

    holdings = _table_text(tab.holdings_table)
    assert "NVDA" in holdings and "2,000,000" in holdings
    assert "-4.0%" in holdings and "0.84" in holdings

    decisions = _table_text(tab.decisions_table)
    assert "TSLA" in decisions and "규제 악재" in decisions and "매도" in decisions

    news = _table_text(tab.news_table)
    assert "Nvidia beats estimates" in news and "실적" in news and "+0.72" in news
    tab.deleteLater()


def test_state_tolerates_missing_fields(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_state({})
    tab._on_state({"trust": None, "holdings": [None, 3], "news": [{}], "decisions": [{}]})
    assert tab.summary_label.text() != ""
    tab.deleteLater()


def test_preview_lists_notes(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_preview({"notes": ["관심 섹터: 반도체 → NVDA", "규제 뉴스(악재) → 보유 시 즉시 정리"]})
    assert tab.notes_list.count() == 2
    assert "반도체" in tab.notes_list.item(0).text()
    tab.deleteLater()


def test_preview_button_sends_text(qapp, monkeypatch, offline):
    sent = []
    monkeypatch.setattr(
        core, "newsdesk_style_preview",
        lambda text: sent.append(text) or {"notes": ["ok"]},
    )
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab.style_edit.setPlainText("반도체 위주로")
    tab._on_preview_clicked()
    _drain(tab)
    assert sent == ["반도체 위주로"]
    assert tab.notes_list.item(0).text() == "ok"
    tab.deleteLater()


def test_save_requires_portfolio(qapp, monkeypatch, offline, dialogs):
    saved = []
    monkeypatch.setattr(core, "newsdesk_set_style", lambda p, t: saved.append(p) or {})
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab.style_edit.setPlainText("반도체")
    tab._on_save_clicked()
    _drain(tab)
    assert saved == []
    assert dialogs.warnings
    tab.deleteLater()


def test_save_sends_to_current_portfolio(qapp, monkeypatch, offline, dialogs):
    saved = []
    monkeypatch.setattr(
        core, "newsdesk_set_style",
        lambda p, t: saved.append((p, t)) or {"notes": ["저장됨"]},
    )
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_portfolios({"portfolios": [{"portfolio": "뉴스1", "mode": "news"}]})
    _drain(tab)
    tab.style_edit.setPlainText("반도체")
    tab._on_save_clicked()
    _drain(tab)
    assert saved == [("뉴스1", "반도체")]
    assert tab.notes_list.item(0).text() == "저장됨"
    tab.deleteLater()


def test_loaded_style_fills_editor(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_style({"raw_text": "배당주 위주", "notes": ["관심 섹터: 배당"]})
    assert tab.style_edit.toPlainText() == "배당주 위주"
    assert tab.notes_list.count() == 1
    tab.deleteLater()


def test_new_portfolio_created_in_news_mode(qapp, monkeypatch, offline, dialogs):
    calls = []
    monkeypatch.setattr(
        core, "autopilot_set_config",
        lambda t, c, a, p="default", mode=None: calls.append((t, c, a, p, mode)) or {"active": a},
    )
    tab = news_widgets.NewsTab()
    _drain(tab)
    dialogs.next_text = ("뉴스1", True)
    tab._on_new_portfolio()
    _drain(tab)
    assert calls and calls[0][2] is False
    assert calls[0][3] == "뉴스1" and calls[0][4] == "news"
    assert tab.current_portfolio() == "뉴스1"
    tab.deleteLater()


def test_new_portfolio_rejects_bad_name(qapp, monkeypatch, offline, dialogs):
    calls = []
    monkeypatch.setattr(core, "autopilot_set_config", lambda *a, **kw: calls.append(a) or {})
    tab = news_widgets.NewsTab()
    _drain(tab)
    dialogs.next_text = ("bad/name", True)
    tab._on_new_portfolio()
    _drain(tab)
    assert calls == [] and dialogs.warnings
    tab.deleteLater()


def test_toggle_starts_with_news_mode(qapp, monkeypatch, offline):
    calls = []
    monkeypatch.setattr(
        core, "autopilot_set_config",
        lambda t, c, a, p="default", mode=None: calls.append((a, p, mode)) or {"active": a},
    )
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_portfolios({"portfolios": [{"portfolio": "뉴스1", "mode": "news"}]})
    _drain(tab)
    calls.clear()
    tab._toggle()
    _drain(tab)
    assert calls == [(True, "뉴스1", "news")]
    assert tab.start_btn.text() == "뉴스 매매 정지"
    tab.deleteLater()


def test_config_updates_controls(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    tab._on_config({"temperature": 8, "capital": 5_000_000, "active": True})
    assert tab.temperature.value() == 8
    assert tab.capital.value() == 5_000_000
    assert tab.start_btn.text() == "뉴스 매매 정지"
    tab._on_config({"error": "x"})
    assert tab.temperature.value() == 8
    tab.deleteLater()


# --- 자동 새로고침 ---


def test_auto_refresh_runs_only_while_visible(qapp, offline):
    tab = news_widgets.NewsTab()
    _drain(tab)
    assert tab.refresh_timer.interval() == 30_000
    assert not tab.refresh_timer.isActive()
    tab.show()
    QApplication.processEvents()
    assert tab.refresh_timer.isActive()
    tab.hide()
    QApplication.processEvents()
    assert not tab.refresh_timer.isActive()
    _drain(tab)
    tab.deleteLater()


def test_main_window_registers_news_tab():
    import inspect

    from alpha import gui

    src = inspect.getsource(gui)
    assert "NewsTab" in src and "뉴스 자동매매" in src



def test_portfolio_list_retries_when_server_is_busy(qapp, offline, monkeypatch):
    # 앱 시작 때 탭들이 몰려 목록 요청이 시간 초과되면, 기존 계좌가 영영 안 보이던 결함(E2E)
    scheduled = []
    monkeypatch.setattr(news_widgets.QTimer, "singleShot", staticmethod(lambda ms, fn: scheduled.append(fn)))
    tab = news_widgets.NewsTab()
    _drain(tab)
    scheduled.clear()
    tab._portfolio_retries = 0   # 생성 시 첫 시도가 이미 한 번 실패했다
    tab._on_portfolios({"error": "timeout"})
    assert scheduled and "다시 불러옵니다" in tab.summary_label.text()
    for _ in range(news_widgets.PORTFOLIO_RETRY_MAX + 2):
        tab._on_portfolios({"error": "timeout"})
    assert len(scheduled) == news_widgets.PORTFOLIO_RETRY_MAX
    assert "새로고침" in tab.summary_label.text()
    tab._on_portfolios({"portfolios": [{"portfolio": "news", "mode": "news"}]})
    assert tab.known_portfolios() == ["news"]
    assert tab._portfolio_retries == 0
    tab.deleteLater()


def test_refresh_button_reloads_empty_portfolio_list(qapp, offline, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "autopilot_portfolios", lambda: calls.append(1) or {"portfolios": []})
    tab = news_widgets.NewsTab()
    _drain(tab)
    calls.clear()
    tab._on_refresh_clicked()
    _drain(tab)
    assert calls
    tab.deleteLater()

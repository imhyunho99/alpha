"""내 계좌 탭의 '성과' 박스 — 넣은 돈 대비 얼마 벌었나, 같은 돈을 그냥 샀으면 얼마였나 (조회 전용)."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout

from alpha import core
from alpha.autopilot_widgets import _Worker
from alpha.news_widgets import _fill, _make_table, _num, _rows

RELOAD_SEC = 600   # 서버가 비교 시세를 10분 캐시한다 — 그림자 상태(1분 주기)마다 다시 묻지 않는다


def fetch_performance() -> dict:
    return core._handle_request("get", "/account/performance", timeout=90)


def _won(value) -> str:
    return f"{_num(value):,.0f}원"


def _pct(value) -> str:
    return f"{value:+.1f}%" if isinstance(value, (int, float)) else "—"


def performance_headline(data) -> str:
    """한 줄 요약: 넣은 돈 1,063,707원 → 지금 1,070,000원 (+0.6%) · 같은 기간 SPY만 샀다면 +1.2%"""
    if not isinstance(data, dict) or "error" in data:
        err = data.get("error") if isinstance(data, dict) else data
        if isinstance(err, dict):
            err = err.get("detail") or err
        return f"성과를 불러오지 못했습니다: {err}"
    if not data.get("exists"):
        return " · ".join(_rows_text(data.get("notes"))) or "에이전트를 시작하면 성과가 여기 나옵니다."
    if data.get("value") is None:
        return f"넣은 돈 {_won(data.get('principal'))} — 지금 평가액을 아직 알 수 없습니다"
    text = f"넣은 돈 {_won(data.get('principal'))} → 지금 {_won(data.get('value'))} ({_pct(data.get('pnl_pct'))})"
    spy = next((b for b in _rows(data.get("benchmarks")) if b.get("key") == "spy"), None)
    if spy and spy.get("pnl_pct") is not None:
        text += f" · 같은 기간 SPY만 샀다면 {_pct(spy.get('pnl_pct'))}"
    return text


def _rows_text(items) -> list[str]:
    return [str(n) for n in (items or []) if n]


def performance_rows(data) -> list[list[str]]:
    """표: 구분 · 지금 금액 · 손익 · 수익률. 맨 위가 에이전트, 아래가 '그냥 샀다면'."""
    if not isinstance(data, dict) or not data.get("exists"):
        return []
    rows = []
    if data.get("value") is not None:
        rows.append(["에이전트 (실제)", _won(data.get("value")), f"{_num(data.get('pnl')):+,.0f}원",
                     _pct(data.get("pnl_pct"))])
    for b in _rows(data.get("benchmarks")):
        if b.get("error"):
            rows.append([str(b.get("label", "")), "—", "—", f"계산 못 함 ({b['error']})"])
        else:
            rows.append([str(b.get("label", "")), _won(b.get("value")), f"{_num(b.get('pnl')):+,.0f}원",
                         _pct(b.get("pnl_pct"))])
    return rows


def performance_detail(data) -> str:
    """표 아래 작은 글씨: 시작일, 입출금 보정 수익률, 비용 추정, 계산 메모."""
    if not isinstance(data, dict) or not data.get("exists"):
        return ""
    parts = []
    if data.get("start_date"):
        parts.append(f"{data['start_date']} 시작")
    flows = _rows(data.get("flows"))
    if len(flows) > 1:
        parts.append(f"입출금 {len(flows) - 1}번 반영")
    if data.get("twr_pct") is not None:
        parts.append(f"입출금 효과를 뺀 운용 수익률 {_pct(data.get('twr_pct'))}")
    costs = data.get("costs") or {}
    if costs.get("live_orders"):
        parts.append(f"수수료 추정 {_won(costs.get('live_cost'))} (실주문 {costs['live_orders']}건)")
    elif costs.get("recorded_orders"):
        parts.append(f"수수료 추정 {_won(costs.get('recorded_cost'))} (기록만 한 주문 {costs['recorded_orders']}건 기준)")
    text = " · ".join(parts)
    notes = _rows_text(data.get("notes"))
    if notes:
        text += ("\n" if text else "") + "※ " + " / ".join(notes)
    return text


class PerformanceBox(QGroupBox):
    """'성과' 박스. 내 계좌 탭은 load() 만 부른다."""

    def __init__(self, parent=None):
        super().__init__("성과 — 넣은 돈 대비, 그냥 샀다면과 비교", parent)
        self._workers: list[_Worker] = []
        self._loaded_at = 0.0
        layout = QVBoxLayout(self)
        self.headline = QLabel("")
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.headline)
        self.table = _make_table(["구분", "지금 금액", "손익", "수익률"])
        self.table.setMaximumHeight(130)
        layout.addWidget(self.table)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: gray;")
        layout.addWidget(self.detail)

    def load(self, force: bool = False) -> None:
        if self._workers or (not force and time.monotonic() - self._loaded_at < RELOAD_SEC):
            return
        self._loaded_at = time.monotonic()
        if not self.headline.text():
            self.headline.setText("성과 계산 중…")
        worker = _Worker(fetch_performance)
        self._workers.append(worker)
        worker.done.connect(self.show_data)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        worker.start()

    def show_data(self, data) -> None:
        if not isinstance(data, dict) or "error" in data:
            self._loaded_at = 0.0   # 실패하면 다음 갱신 때 다시 묻는다
        self.headline.setText(performance_headline(data))
        _fill(self.table, performance_rows(data))
        self.detail.setText(performance_detail(data))

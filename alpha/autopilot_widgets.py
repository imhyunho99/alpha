"""Autopilot 탭. 포트폴리오 선택 · 온도 슬라이더 · 과거 곡선 · 대시보드."""
from __future__ import annotations

import re

from PySide6.QtCore import QPointF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from alpha import core

DEFAULT_PORTFOLIO = "default"

# 서버(alpha_server/autopilot/api.py)의 PORTFOLIO_PATTERN과 같은 규칙.
# 이름이 파일명이 되므로 좁게 잡는다. 여기서 미리 걸러 왕복을 아낀다.
PORTFOLIO_PATTERN = re.compile(r"^[A-Za-z0-9가-힣_-]{1,32}$")


def is_valid_portfolio_name(name) -> bool:
    """서버가 받아줄 이름인지 확인한다."""
    if not isinstance(name, str):
        return False
    return PORTFOLIO_PATTERN.fullmatch(name) is not None


class _Worker(QThread):
    done = Signal(object)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn, self._args = fn, args

    def run(self):
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as exc:
            self.done.emit({"error": str(exc)})


class EquityCurve(QWidget):
    """의존성 없이 QPainter로 그리는 자산 곡선."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[float] = []
        self._liquidated_at: float | None = None
        self.setMinimumHeight(220)

    def set_curve(self, equities: list[float], liquidated_index: float | None = None):
        self._points = list(equities or [])
        self._liquidated_at = liquidated_index
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if len(self._points) < 2:
            painter.drawText(
                self.rect(), Qt.AlignCenter, "온도를 조절하면 과거 성과가 표시됩니다"
            )
            return

        lo, hi = min(self._points), max(self._points)
        span = (hi - lo) or 1.0  # 평평한 곡선이면 0으로 나누지 않는다
        painter.setPen(QPen(QColor("#2d7dd2"), 2))
        prev = None
        for i, value in enumerate(self._points):
            x = i / (len(self._points) - 1) * (w - 20) + 10
            y = h - 10 - (value - lo) / span * (h - 20)
            if prev is not None:
                painter.drawLine(QPointF(prev[0], prev[1]), QPointF(x, y))
            prev = (x, y)

        if self._liquidated_at is not None:
            x = self._liquidated_at * (w - 20) + 10
            painter.setPen(QPen(QColor("#d62828"), 2, Qt.DashLine))
            painter.drawLine(QPointF(x, 10.0), QPointF(x, float(h - 10)))


class AutopilotTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 슬라이더를 끌면 워커가 연달아 생긴다. 참조를 놓치면 실행 중인
        # QThread가 GC 되면서 죽으므로 끝날 때까지 붙들고 있는다.
        self._workers: list[_Worker] = []
        # 콤보를 프로그램이 다시 채우는 동안에는 currentIndexChanged를 무시한다.
        # 안 그러면 행 수만큼 재조회가 터진다.
        self._syncing_portfolios = False
        self._pending_selection: str | None = None
        self._pending_new: str | None = None
        self._build()
        self._load_portfolios()
        self._load_config()

    def _build(self):
        layout = QVBoxLayout(self)

        # 포트폴리오 선택 — 온도별 계좌를 나란히 굴리고 오간다
        picker = QHBoxLayout()
        picker.addWidget(QLabel("포트폴리오"))
        self.portfolio_combo = QComboBox()
        self.portfolio_combo.setMinimumWidth(260)
        self.portfolio_combo.addItem("default", DEFAULT_PORTFOLIO)
        self.portfolio_combo.currentIndexChanged.connect(self._on_portfolio_changed)
        picker.addWidget(self.portfolio_combo, 1)
        self.new_portfolio_btn = QPushButton("새 포트폴리오")
        self.new_portfolio_btn.clicked.connect(self._on_new_portfolio)
        picker.addWidget(self.new_portfolio_btn)
        layout.addLayout(picker)

        # 온도
        temp_box = QGroupBox("온도")
        temp_layout = QVBoxLayout(temp_box)
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 10)
        self.slider.setValue(5)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.valueChanged.connect(self._on_temperature_changed)
        self.temp_label = QLabel("5")
        self.temp_label.setMinimumWidth(24)
        row.addWidget(self.slider)
        row.addWidget(self.temp_label)
        temp_layout.addLayout(row)
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        temp_layout.addWidget(self.profile_label)
        layout.addWidget(temp_box)

        # 과거 성과
        curve_box = QGroupBox("이 온도로 지난 3년 굴렸다면")
        curve_layout = QVBoxLayout(curve_box)
        self.curve = EquityCurve()
        curve_layout.addWidget(self.curve)
        self.curve_summary = QLabel("계산 대기 중")
        curve_layout.addWidget(self.curve_summary)
        layout.addWidget(curve_box)

        # 자본금 + 시작/정지
        control = QHBoxLayout()
        control.addWidget(QLabel("자본금(원)"))
        self.capital = QDoubleSpinBox()
        self.capital.setRange(0, 1_000_000_000)
        self.capital.setSingleStep(1_000_000)
        self.capital.setValue(10_000_000)
        self.capital.setGroupSeparatorShown(True)
        control.addWidget(self.capital)
        self.start_btn = QPushButton("자동 운용 시작")
        self.start_btn.clicked.connect(self._toggle)
        control.addWidget(self.start_btn)
        layout.addLayout(control)

        # 대시보드 + 브리핑
        self.dashboard = QTextEdit()
        self.dashboard.setReadOnly(True)
        layout.addWidget(self.dashboard)

        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self._refresh_state)
        layout.addWidget(refresh)

    def _on_temperature_changed(self, value: int):
        self.temp_label.setText(str(value))
        self._run(
            core.autopilot_backtest, self._on_backtest, value, self.capital.value(), 3
        )

    def _run(self, fn, callback, *args):
        worker = _Worker(fn, *args)
        self._workers.append(worker)
        worker.done.connect(callback)
        worker.finished.connect(lambda w=worker: self._retire(w))
        worker.start()

    def _retire(self, worker: _Worker):
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def closeEvent(self, event):
        for worker in list(self._workers):
            worker.wait(3000)
        super().closeEvent(event)

    def _on_backtest(self, result):
        if not isinstance(result, dict) or "error" in result:
            self.curve_summary.setText(
                "서버에 연결할 수 없습니다. 로그인 상태를 확인하세요."
            )
            return
        curve = [
            pt.get("equity", 0.0)
            for pt in result.get("curve", [])
            if isinstance(pt, dict)
        ]
        liq = result.get("liquidated_index")
        self.curve.set_curve(curve, liq)
        self.curve_summary.setText(
            f"최종 {result.get('final_equity', 0):,.0f}원 · "
            f"최대낙폭 {result.get('max_drawdown_pct', 0):.1f}% · "
            f"거래 {result.get('total_fills', 0)}건"
            + ("  ⚠️ 기간 중 청산 발생" if result.get("liquidated_at") else "")
        )
        prof = result.get("profile", {})
        if prof:
            self.profile_label.setText(
                f"실제 투입 {prof.get('deploy_pct', 0):.0f}% · "
                f"보유 {prof.get('max_holdings')}종목 · "
                f"손절 -{prof.get('stop_loss_pct', 0):.0f}% · "
                f"레버리지 {prof.get('max_leverage', 1):.1f}배"
            )

    # --- 포트폴리오 ---

    def current_portfolio(self) -> str:
        """콤보에서 선택된 포트폴리오 이름. 비어 있으면 default."""
        name = self.portfolio_combo.currentData()
        return name if isinstance(name, str) and name else DEFAULT_PORTFOLIO

    def known_portfolios(self) -> list[str]:
        return [
            self.portfolio_combo.itemData(i)
            for i in range(self.portfolio_combo.count())
        ]

    def _load_portfolios(self, select: str | None = None):
        self._pending_selection = select
        self._run(core.autopilot_portfolios, self._on_portfolios)

    def _on_portfolios(self, result):
        """목록을 콤보에 채운다. 서버가 죽어 있으면 default 하나만 남긴다."""
        entries = []
        if isinstance(result, dict) and "error" not in result:
            raw = result.get("portfolios")
            if isinstance(raw, list):
                entries = [e for e in raw if isinstance(e, dict) and e.get("portfolio")]

        wanted = getattr(self, "_pending_selection", None) or self.current_portfolio()
        self._pending_selection = None

        if not entries:
            # 서버가 없거나 응답이 비었다. 지금 보고 있는 것만 유지한다.
            return

        self._syncing_portfolios = True
        try:
            self.portfolio_combo.clear()
            for entry in entries:
                self.portfolio_combo.addItem(
                    self._portfolio_label(entry), entry["portfolio"]
                )
            index = self.portfolio_combo.findData(wanted)
            self.portfolio_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._syncing_portfolios = False

    @staticmethod
    def _portfolio_label(entry: dict) -> str:
        """예: 'aggressive (온도 8, +3.2%)'. 값이 없으면 이름만 보여준다."""
        name = entry.get("portfolio", "?")
        bits = []
        temperature = entry.get("temperature")
        if temperature is not None:
            bits.append(f"온도 {temperature}")
        ret = entry.get("return_pct")
        if isinstance(ret, (int, float)):
            bits.append(f"{ret:+.1f}%")
        if entry.get("active"):
            bits.append("운용중")
        return f"{name} ({', '.join(bits)})" if bits else str(name)

    def _on_portfolio_changed(self, _index: int):
        if self._syncing_portfolios:
            return
        self._load_config()
        self._refresh_state(reload_list=False)

    def _on_new_portfolio(self):
        name, accepted = QInputDialog.getText(
            self, "새 포트폴리오", "이름 (영문·숫자·한글·_·- , 1~32자)"
        )
        if not accepted:
            return

        name = (name or "").strip()
        if not is_valid_portfolio_name(name):
            QMessageBox.warning(
                self,
                "이름을 쓸 수 없습니다",
                "영문·숫자·한글과 _ - 만 쓸 수 있고 1~32자여야 합니다.\n"
                f"입력한 이름: {name!r}",
            )
            return
        if name in self.known_portfolios():
            QMessageBox.warning(
                self, "이미 있습니다", f"'{name}' 포트폴리오가 이미 있습니다."
            )
            return

        self._create_portfolio(name)

    def _create_portfolio(self, name: str):
        """비활성 상태로 만들어 둔다. 실제 시작은 사용자가 버튼으로 한다."""
        self._pending_new = name
        self._run(
            core.autopilot_set_config,
            self._on_portfolio_created,
            self.slider.value(),
            self.capital.value(),
            False,
            name,
        )

    def _on_portfolio_created(self, result):
        name = getattr(self, "_pending_new", None)
        self._pending_new = None
        if not isinstance(result, dict) or "error" in result:
            QMessageBox.warning(
                self,
                "만들지 못했습니다",
                "서버에 연결할 수 없습니다. 로그인 상태를 확인하세요.",
            )
            return

        # 서버가 받아줬으니 목록에 넣고 그쪽으로 옮겨간다.
        if name and self.portfolio_combo.findData(name) < 0:
            self._syncing_portfolios = True
            try:
                self.portfolio_combo.addItem(name, name)
                self.portfolio_combo.setCurrentIndex(self.portfolio_combo.count() - 1)
            finally:
                self._syncing_portfolios = False
        self._load_portfolios(select=name)
        self._load_config()

    # --- 설정 / 상태 ---

    def _load_config(self):
        self._run(core.autopilot_get_config, self._on_config, self.current_portfolio())

    def _on_config(self, cfg):
        if isinstance(cfg, dict) and "error" not in cfg:
            self.slider.setValue(int(cfg.get("temperature", 5)))
            if cfg.get("capital"):
                self.capital.setValue(float(cfg["capital"]))
            self.start_btn.setText(
                "자동 운용 정지" if cfg.get("active") else "자동 운용 시작"
            )
        self._on_temperature_changed(self.slider.value())

    def _toggle(self):
        activating = self.start_btn.text() == "자동 운용 시작"
        self._run(
            core.autopilot_set_config,
            self._on_config,
            self.slider.value(),
            self.capital.value(),
            activating,
            self.current_portfolio(),
        )

    def _refresh_state(self, *_args, reload_list: bool = True):
        if reload_list:
            # 다른 포트폴리오의 수익률도 같이 최신으로 만든다
            self._load_portfolios(select=self.current_portfolio())
        self._run(core.autopilot_state, self._on_state, self.current_portfolio())

    def _on_state(self, state):
        if not isinstance(state, dict) or "error" in state:
            self.dashboard.setPlainText("상태를 가져오지 못했습니다.")
            return
        lines = [
            f"평가액   {state.get('equity', 0):,.0f}원",
            f"수익률   {state.get('return_pct', 0):+.2f}%",
            f"현금     {state.get('cash', 0):,.0f}원",
            f"레버리지 {state.get('leverage', 1):.2f}배",
            "",
            "보유 종목",
        ]
        for h in state.get("holdings", []):
            lines.append(f"  {h.get('ticker', '?'):<12} {h.get('quantity', 0):>12,.4f}")
        for a in state.get("alerts", []):
            lines.append(f"\n[{str(a.get('severity', 'info')).upper()}] {a.get('message', '')}")
        self.dashboard.setPlainText("\n".join(lines))

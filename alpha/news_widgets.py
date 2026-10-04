"""뉴스 자동매매 탭. 스타일 입력 · 시작/정지 · 상태(신뢰도·보유·판단·뉴스)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from alpha import core
from alpha.autopilot_widgets import _Worker, is_valid_portfolio_name

NEWS_MODE = "news"
REFRESH_MS = 30_000

# 서버의 신호 키(news:{category}, model)를 PM 이 읽을 말로.
CATEGORY_LABELS = {
    "earnings": "실적",
    "guidance": "실적 전망",
    "analyst": "애널리스트 의견",
    "regulation": "규제",
    "legal": "소송",
    "mna": "인수합병",
    "product": "신제품·수주",
    "management": "경영진·배당",
    "macro": "금리·경기",
    "filing": "공시",
    "other": "기타",
}

ACTION_LABELS = {"buy": "매수", "sell": "매도", "trim": "일부 매도", "hold": "유지", "hold_off": "매수 보류"}

BROKER_CHOICES = [("연동 안 함", None), ("KB증권 — 주문 기록만", "kb")]
ORDER_LABELS = {"buy": "매수", "sell": "매도"}


def broker_text(view: dict) -> str:
    """연동 상태 한 줄. 실주문 중이면 반드시 눈에 띄게."""
    name = view.get("broker")
    if not name:
        return "연동 안 함 — 모의계좌만 굴립니다."
    label = {"kb": "KB증권"}.get(name, name.upper())
    parts = [f"🔴 {label} 실주문 중" if not view.get("dry_run", True) else f"{label} · 주문 기록만 (실제 주문 안 나감)"]
    if not view.get("registered"):
        parts.append("API 키 미등록 — [계정 → 거래소 API 키 관리]에서 등록하세요")
    last = view.get("last") or {}
    if last:
        when = _short_time(last.get("at"))
        if last.get("status") == "error":
            parts.append(f"마지막 연동 {when} 실패: {last.get('message', '')}")
        else:
            eq = last.get("real_equity")
            parts.append(f"마지막 연동 {when} · 주문 {last.get('orders', 0)}건"
                         + (f" · 실계좌 {eq:,.0f}원" if isinstance(eq, (int, float)) else "")
                         + (f" · {last['message']}" if last.get("message") else ""))
    else:
        parts.append("다음 매매 때 첫 연동")
    return " · ".join(parts)


def order_result_text(o: dict) -> str:
    status = o.get("status")
    if status == "success":
        return "기록됨" if o.get("dry_run", True) else f"주문 접수 {o.get('order_no', '')}".strip()
    if status == "risk_blocked":
        return f"위험 한도로 보류: {o.get('message', '')}"
    return f"실패: {o.get('message', '')}"


STYLE_PLACEHOLDER = (
    "예) 반도체랑 AI 위주로 담고 테슬라는 빼줘.\n"
    "규제나 소송 뉴스가 뜨면 바로 팔고, 실적 호재에는 적극적으로 사줘.\n"
    "한 종목 20% 넘지 않게, 하루 3번까지만 사고, 손실 10%면 줄여. 뉴스 매매는 30%까지."
)


def signal_label(key: str) -> str:
    """'news:earnings' → '실적 뉴스'. 모르는 키는 그대로 보여준다."""
    if key == "model":
        return "모델 예측"
    if isinstance(key, str) and key.startswith("news:"):
        label = CATEGORY_LABELS.get(key[5:])
        if label:
            return f"{label} 뉴스"
    return str(key)


def trust_mark(weight: float) -> str:
    """신뢰도는 1.0 이 출발점. 그보다 위면 그 신호를 더 믿고 있다는 뜻."""
    if weight > 1.0 + 1e-9:
        return "▲ 높음"
    if weight < 1.0 - 1e-9:
        return "▼ 낮음"
    return "기준"


def brake_text(drawdown_pct: float, exposure: float) -> str:
    return f"손실 브레이크: 고점 대비 -{drawdown_pct:.1f}% → 투자 비중 {exposure * 100:.0f}%"


def news_portfolios(entries) -> list[dict]:
    """mode 가 news 인 항목만. mode 가 없으면 예전 서버의 model 포트폴리오로 본다."""
    return [
        e for e in entries or []
        if isinstance(e, dict)
        and e.get("portfolio")
        and (e.get("mode") or "model") == NEWS_MODE
    ]


def _num(value, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _rows(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table: QTableWidget, rows: list[list[str]]) -> None:
    table.setRowCount(len(rows))
    for r, cells in enumerate(rows):
        for c, text in enumerate(cells):
            table.setItem(r, c, QTableWidgetItem(text))


PORTFOLIO_RETRY_MAX = 5
PORTFOLIO_RETRY_MS = 4000


class NewsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 실행 중인 QThread 가 GC 되지 않게 끝날 때까지 붙들고 있는다.
        self._workers: list[_Worker] = []
        self._syncing_portfolios = False
        self._pending_selection: str | None = None
        self._portfolio_retries = 0
        self._pending_new: str | None = None
        self._news_urls: list[str] = []
        self._build()
        # 30초 새로고침은 탭이 보일 때만 돈다. 안 보는 동안 서버를 두드리지 않는다.
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_MS)
        self.refresh_timer.timeout.connect(self._refresh_state)
        self._update_enabled()
        self._load_portfolios()

    # --- 화면 ---

    def _build(self):
        layout = QVBoxLayout(self)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("뉴스 포트폴리오"))
        self.portfolio_combo = QComboBox()
        self.portfolio_combo.setMinimumWidth(260)
        self.portfolio_combo.setPlaceholderText("먼저 새 포트폴리오를 만드세요")
        self.portfolio_combo.currentIndexChanged.connect(self._on_portfolio_changed)
        picker.addWidget(self.portfolio_combo, 1)
        self.new_portfolio_btn = QPushButton("새 뉴스 포트폴리오")
        self.new_portfolio_btn.clicked.connect(self._on_new_portfolio)
        picker.addWidget(self.new_portfolio_btn)
        layout.addLayout(picker)

        # 투자 스타일 — 자연어로 적고, 어떻게 알아들었는지 확인한 뒤 저장
        style_box = QGroupBox("투자 스타일 (자유롭게 적으세요)")
        style_layout = QVBoxLayout(style_box)
        self.style_edit = QPlainTextEdit()
        self.style_edit.setPlaceholderText(STYLE_PLACEHOLDER)
        self.style_edit.setFixedHeight(90)
        style_layout.addWidget(self.style_edit)
        buttons = QHBoxLayout()
        self.preview_btn = QPushButton("해석 미리보기")
        self.preview_btn.clicked.connect(self._on_preview_clicked)
        buttons.addWidget(self.preview_btn)
        self.save_btn = QPushButton("저장")
        self.save_btn.clicked.connect(self._on_save_clicked)
        buttons.addWidget(self.save_btn)
        buttons.addStretch(1)
        style_layout.addLayout(buttons)
        style_layout.addWidget(QLabel("이렇게 이해했습니다"))
        self.notes_list = QListWidget()
        self.notes_list.setFixedHeight(130)
        style_layout.addWidget(self.notes_list)
        layout.addWidget(style_box)

        control = QHBoxLayout()
        control.addWidget(QLabel("자본금(원)"))
        self.capital = QDoubleSpinBox()
        self.capital.setRange(0, 1_000_000_000)
        self.capital.setSingleStep(1_000_000)
        self.capital.setValue(10_000_000)
        self.capital.setGroupSeparatorShown(True)
        control.addWidget(self.capital)
        control.addWidget(QLabel("온도"))
        self.temperature = QSpinBox()
        self.temperature.setRange(1, 10)
        self.temperature.setValue(5)
        self.temperature.setToolTip("1 = 조심스럽게, 10 = 공격적으로")
        control.addWidget(self.temperature)
        self.start_btn = QPushButton("뉴스 매매 시작")
        self.start_btn.clicked.connect(self._toggle)
        control.addWidget(self.start_btn)
        control.addStretch(1)
        layout.addLayout(control)

        # 증권사 연동 — 모의계좌 비중을 실계좌로. 앱에서는 '기록만'까지만 켤 수 있다.
        broker_box = QGroupBox("증권사 연동 (모의계좌 비중을 실계좌에 맞춤)")
        broker_layout = QVBoxLayout(broker_box)
        broker_row = QHBoxLayout()
        self.broker_combo = QComboBox()
        for label, key in BROKER_CHOICES:
            self.broker_combo.addItem(label, key)
        self.broker_combo.activated.connect(self._on_broker_chosen)
        broker_row.addWidget(self.broker_combo)
        self.broker_check_btn = QPushButton("연결 확인")
        self.broker_check_btn.clicked.connect(self._on_broker_check)
        broker_row.addWidget(self.broker_check_btn)
        self.broker_label = QLabel("")
        self.broker_label.setWordWrap(True)
        broker_row.addWidget(self.broker_label, 1)
        broker_layout.addLayout(broker_row)
        self.broker_table = _make_table(["시각", "종목", "주문", "수량", "결과"])
        self.broker_table.setFixedHeight(110)
        broker_layout.addWidget(self.broker_table)
        layout.addWidget(broker_box)

        # 상태 패널
        status_box = QGroupBox("현재 상태 (30초마다 자동 새로고침)")
        status = QVBoxLayout(status_box)
        self.summary_label = QLabel("포트폴리오를 선택하면 상태가 표시됩니다.")
        status.addWidget(self.summary_label)
        self.brake_label = QLabel("")
        status.addWidget(self.brake_label)

        tables = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("뉴스 종류별 신뢰도 (1.0 기준, 맞히면 오르고 틀리면 내려감)"))
        self.trust_table = _make_table(["신호", "신뢰도", "판단"])
        left.addWidget(self.trust_table)
        left.addWidget(QLabel("보유 종목"))
        self.holdings_table = _make_table(["종목", "평가액", "손익률", "비중 배수"])
        left.addWidget(self.holdings_table)
        tables.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("최근 판단"))
        self.decisions_table = _make_table(["시각", "행동", "종목", "이유"])
        right.addWidget(self.decisions_table)
        right.addWidget(QLabel("최근 뉴스 (더블클릭하면 기사 열기)"))
        self.news_table = _make_table(["종목", "종류", "감성", "제목"])
        self.news_table.cellDoubleClicked.connect(self._open_news)
        right.addWidget(self.news_table)
        tables.addLayout(right, 1)
        status.addLayout(tables)

        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self._on_refresh_clicked)
        status.addWidget(refresh)
        layout.addWidget(status_box, 1)

    def _update_enabled(self):
        has = self.current_portfolio() is not None
        self.start_btn.setEnabled(has)
        self.save_btn.setEnabled(has)
        self.broker_combo.setEnabled(has)
        self.broker_check_btn.setEnabled(has)

    # --- 증권사 연동 ---

    def _on_broker_chosen(self, _index: int):
        portfolio = self.current_portfolio()
        if portfolio is None:
            return
        self._run(core.set_broker, self._on_broker, portfolio, self.broker_combo.currentData())

    def _on_broker_check(self):
        portfolio = self.current_portfolio()
        if portfolio is None:
            return
        self.broker_label.setText("연결 확인 중…")
        self._run(core.check_broker, self._on_broker_checked, portfolio, self.broker_combo.currentData())

    def _on_broker_checked(self, result):
        if not isinstance(result, dict) or "error" in result:
            self.broker_label.setText("연결 확인에 실패했습니다. 서버·로그인 상태를 확인하세요.")
            return
        self.broker_label.setText(("✅ " if result.get("ok") else "⚠️ ") + str(result.get("message", "")))

    def _on_broker(self, view):
        if not isinstance(view, dict) or "error" in view:
            self.broker_label.setText("연동 상태를 가져오지 못했습니다.")
            return
        self.broker_label.setText(broker_text(view))
        idx = self.broker_combo.findData(view.get("broker"))
        self.broker_combo.setCurrentIndex(max(0, idx))
        _fill(self.broker_table, [
            [
                _short_time(o.get("at")),
                str(o.get("ticker") or ""),
                ORDER_LABELS.get(o.get("action"), str(o.get("action") or "")),
                f"{_num(o.get('quantity')):g}",
                order_result_text(o),
            ]
            for o in _rows(view.get("orders"))
        ])

    # --- 워커 ---

    def _run(self, fn, callback, *args, **kwargs):
        if kwargs:
            call = lambda: fn(*args, **kwargs)  # noqa: E731 — _Worker 는 위치 인자만 받는다
            worker = _Worker(call)
        else:
            worker = _Worker(fn, *args)
        self._workers.append(worker)
        worker.done.connect(callback)
        worker.finished.connect(lambda w=worker: self._retire(w))
        worker.start()

    def _retire(self, worker: _Worker):
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_timer.start()
        if self.portfolio_combo.count() == 0:
            # 앱 시작 때 목록을 못 받았으면 탭을 열 때 다시 받는다
            self._portfolio_retries = 0
            self._load_portfolios()
        self._refresh_state()

    def hideEvent(self, event):
        self.refresh_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.refresh_timer.stop()
        for worker in list(self._workers):
            worker.wait(3000)
        super().closeEvent(event)

    # --- 포트폴리오 ---

    def current_portfolio(self) -> str | None:
        name = self.portfolio_combo.currentData()
        return name if isinstance(name, str) and name else None

    def known_portfolios(self) -> list[str]:
        return [self.portfolio_combo.itemData(i) for i in range(self.portfolio_combo.count())]

    def _retry_portfolios(self):
        self._load_portfolios(select=self._pending_selection)

    def _load_portfolios(self, select: str | None = None):
        self._pending_selection = select
        self._run(core.autopilot_portfolios, self._on_portfolios)

    def _on_portfolios(self, result):
        if not isinstance(result, dict) or "error" in result:
            # 앱을 켜는 순간 탭들이 한꺼번에 서버를 두드려(자동 운용 탭의 3년 백테스트 포함)
            # 이 요청이 10초 제한에 걸린다. 한 번 실패로 끝내면 이미 있는 계좌가 영영
            # 안 보인다(실측 E2E). 몇 번 더 시도하고, 그동안 무슨 일인지 보여준다.
            if self._portfolio_retries < PORTFOLIO_RETRY_MAX:
                self._portfolio_retries += 1
                if self.portfolio_combo.count() == 0:
                    self.summary_label.setText(
                        f"서버 응답을 기다리는 중 — 포트폴리오 목록을 다시 불러옵니다 "
                        f"({self._portfolio_retries}/{PORTFOLIO_RETRY_MAX})"
                    )
                QTimer.singleShot(PORTFOLIO_RETRY_MS, self._retry_portfolios)
            elif self.portfolio_combo.count() == 0:
                self.summary_label.setText("포트폴리오 목록을 불러오지 못했습니다. [새로고침]을 눌러 다시 시도하세요.")
            return  # 지금 보이는 것은 유지한다
        self._portfolio_retries = 0
        entries = news_portfolios(result.get("portfolios"))
        wanted = self._pending_selection or self.current_portfolio()
        self._pending_selection = None
        before = self.current_portfolio()

        self._syncing_portfolios = True
        try:
            self.portfolio_combo.clear()
            for entry in entries:
                self.portfolio_combo.addItem(self._portfolio_label(entry), entry["portfolio"])
            if entries:
                index = self.portfolio_combo.findData(wanted)
                self.portfolio_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._syncing_portfolios = False
        self._update_enabled()
        # 목록만 새로 받은 거면 다시 불러오지 않는다. 선택이 바뀌었을 때만.
        if self.current_portfolio() != before:
            self._on_portfolio_changed(self.portfolio_combo.currentIndex())

    @staticmethod
    def _portfolio_label(entry: dict) -> str:
        bits = []
        ret = entry.get("return_pct")
        if isinstance(ret, (int, float)):
            bits.append(f"{ret:+.1f}%")
        if entry.get("active"):
            bits.append("운용중")
        name = entry["portfolio"]
        return f"{name} ({', '.join(bits)})" if bits else str(name)

    def _on_portfolio_changed(self, _index: int):
        if self._syncing_portfolios:
            return
        self._update_enabled()
        portfolio = self.current_portfolio()
        if portfolio is None:
            return
        self._run(core.autopilot_get_config, self._on_config, portfolio)
        self._run(core.newsdesk_get_style, self._on_style, portfolio)
        self._refresh_state()

    def _on_new_portfolio(self):
        name, accepted = QInputDialog.getText(
            self, "새 뉴스 포트폴리오", "이름 (영문·숫자·한글·_·- , 1~32자)"
        )
        if not accepted:
            return
        name = (name or "").strip()
        if not is_valid_portfolio_name(name):
            QMessageBox.warning(
                self, "이름을 쓸 수 없습니다",
                f"영문·숫자·한글과 _ - 만 쓸 수 있고 1~32자여야 합니다.\n입력한 이름: {name!r}",
            )
            return
        if name in self.known_portfolios():
            QMessageBox.warning(self, "이미 있습니다", f"'{name}' 포트폴리오가 이미 있습니다.")
            return
        # 정지 상태로 만든다. 스타일을 저장하고 사용자가 직접 시작한다.
        self._pending_new = name
        self._run(
            core.autopilot_set_config, self._on_portfolio_created,
            self.temperature.value(), self.capital.value(), False, name,
            mode=NEWS_MODE,
        )

    def _on_portfolio_created(self, result):
        name, self._pending_new = self._pending_new, None
        if not isinstance(result, dict) or "error" in result:
            QMessageBox.warning(
                self, "만들지 못했습니다", "서버에 연결할 수 없습니다. 로그인 상태를 확인하세요."
            )
            return
        if name and self.portfolio_combo.findData(name) < 0:
            self._syncing_portfolios = True
            try:
                self.portfolio_combo.addItem(name, name)
                self.portfolio_combo.setCurrentIndex(self.portfolio_combo.count() - 1)
            finally:
                self._syncing_portfolios = False
        self._update_enabled()
        self._on_config(result)
        self._load_portfolios(select=name)

    # --- 설정 ---

    def _on_config(self, cfg):
        if not isinstance(cfg, dict) or "error" in cfg:
            return
        if isinstance(cfg.get("temperature"), (int, float)):
            self.temperature.setValue(int(cfg["temperature"]))
        if cfg.get("capital"):
            self.capital.setValue(float(cfg["capital"]))
        self.start_btn.setText("뉴스 매매 정지" if cfg.get("active") else "뉴스 매매 시작")

    def _toggle(self):
        portfolio = self.current_portfolio()
        if portfolio is None:
            return
        activating = self.start_btn.text() == "뉴스 매매 시작"
        self._run(
            core.autopilot_set_config, self._on_config,
            self.temperature.value(), self.capital.value(), activating, portfolio,
            mode=NEWS_MODE,
        )

    # --- 스타일 ---

    def _on_preview_clicked(self):
        self._run(core.newsdesk_style_preview, self._on_preview, self.style_edit.toPlainText())

    def _on_save_clicked(self):
        portfolio = self.current_portfolio()
        if portfolio is None:
            QMessageBox.warning(self, "포트폴리오가 없습니다", "먼저 뉴스 포트폴리오를 만드세요.")
            return
        self._run(
            core.newsdesk_set_style, self._on_saved, portfolio, self.style_edit.toPlainText()
        )

    def _show_notes(self, profile) -> bool:
        self.notes_list.clear()
        if not isinstance(profile, dict) or "error" in profile:
            self.notes_list.addItem("서버에 연결할 수 없어 해석하지 못했습니다.")
            return False
        notes = [str(n) for n in profile.get("notes") or []]
        self.notes_list.addItems(notes or ["인식한 규칙이 없습니다."])
        return True

    def _on_preview(self, profile):
        self._show_notes(profile)

    def _on_saved(self, profile):
        if not self._show_notes(profile):
            QMessageBox.warning(self, "저장하지 못했습니다", "서버에 연결할 수 없습니다.")

    def _on_style(self, profile):
        """저장된 스타일을 불러온다. 실패하면 입력 중인 글을 지우지 않는다."""
        if not isinstance(profile, dict) or "error" in profile:
            return
        self.style_edit.setPlainText(str(profile.get("raw_text") or ""))
        self._show_notes(profile)

    # --- 상태 ---

    def _on_refresh_clicked(self):
        if self.portfolio_combo.count() == 0:
            self._portfolio_retries = 0
            self._load_portfolios()
        self._refresh_state()

    def _refresh_state(self, *_args):
        portfolio = self.current_portfolio()
        if portfolio is None:
            return
        self._run(core.newsdesk_state, self._on_state, portfolio)
        self._run(core.broker_state, self._on_broker, portfolio)

    def _on_state(self, state):
        if not isinstance(state, dict) or "error" in state:
            self.summary_label.setText("상태를 가져오지 못했습니다. 서버·로그인 상태를 확인하세요.")
            return

        self.summary_label.setText(
            f"평가액 {_num(state.get('equity')):,.0f}원 · "
            f"수익률 {_num(state.get('return_pct')):+.2f}%"
            + (f" · 해석 모델 {state['interpreter']}" if state.get("interpreter") else "")
        )
        self.brake_label.setText(
            brake_text(_num(state.get("drawdown_pct")), _num(state.get("exposure_multiplier"), 1.0))
        )

        trust = state.get("trust") if isinstance(state.get("trust"), dict) else {}
        trust_rows = [
            [signal_label(k), f"{_num(w, 1.0):.2f}", trust_mark(_num(w, 1.0))]
            for k, w in sorted(trust.items(), key=lambda kv: -_num(kv[1], 1.0))
        ]
        if not trust_rows:
            # 첫 채점은 매매 72시간 뒤다. 그때까지 빈 표는 고장처럼 보인다.
            trust_rows = [["아직 없음", "1.00", "매매 후 72시간이 지나면 실제 결과로 채점됩니다"]]
        _fill(self.trust_table, trust_rows)

        _fill(self.holdings_table, [
            [
                str(h.get("ticker", "?")),
                f"{_num(h.get('value')):,.0f}원",
                f"{_num(h.get('pnl_pct')):+.1f}%",
                f"×{_num(h.get('multiplier'), 1.0):.2f}",
            ]
            for h in _rows(state.get("holdings"))
        ])

        _fill(self.decisions_table, [
            [
                _short_time(d.get("at")),
                ACTION_LABELS.get(d.get("action"), str(d.get("action") or "")),
                str(d.get("ticker") or ""),
                str(d.get("reason") or ""),
            ]
            for d in _rows(state.get("decisions"))
        ])

        news = _rows(state.get("news"))
        self._news_urls = [str(n.get("url") or "") for n in news]
        _fill(self.news_table, [
            [
                str(n.get("ticker") or ""),
                CATEGORY_LABELS.get(n.get("category"), str(n.get("category") or "")),
                f"{_num(n.get('sentiment')):+.2f}",
                str(n.get("title") or ""),
            ]
            for n in news
        ])
        for row, url in enumerate(self._news_urls):
            if url:
                self.news_table.item(row, 3).setToolTip(url)

    def _open_news(self, row: int, _col: int):
        if 0 <= row < len(self._news_urls) and self._news_urls[row]:
            QDesktopServices.openUrl(QUrl(self._news_urls[row]))


def _short_time(value) -> str:
    """ISO 시각을 '09-27 18:03'(현지 시각)으로. 형식이 이상하면 원문 그대로."""
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")

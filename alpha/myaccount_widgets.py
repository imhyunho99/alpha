"""💼 내 계좌 탭 — 증권사 실계좌를 읽어 보여주고 분석한다. 조회 전용, 주문 버튼 없음."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from alpha import core
from alpha.autopilot_widgets import _Worker
from alpha.news_widgets import _fill, _make_table, _num, _rows

KB_OPEN_API_URL = "https://openapi.kbsec.com"

KEY_GUIDE = (
    "KB증권 계좌를 읽으려면 KB증권 Open API 키가 필요합니다 (무료).\n"
    "1. KB증권 Open API 페이지 → [사용신청] → 본인인증\n"
    "2. 발급된 App Key · App Secret 을 [키 등록]에 붙여넣기\n"
    "3. [불러오기] — 보유 종목·손익·매매 기록이 이 화면에 나옵니다.\n"
    "조회만 합니다. 이 화면에서는 주문이 나가지 않습니다."
)


def won(value) -> str:
    return f"{_num(value):,.0f}원"


def pct(value, signed: bool = True) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def trend_text(above) -> str:
    return "—" if above is None else ("위 ▲" if above else "아래 ▼")


def summary_text(data: dict) -> str:
    s = data.get("summary") or {}
    parts = [f"총 평가 {won(s.get('total_krw'))}",
             f"투자 {won(s.get('invested_krw'))} · 현금 {won(s.get('cash_krw'))} ({pct(s.get('cash_pct'), False)})"]
    if s.get("pl_pct") is not None:
        parts.append(f"평가손익 {_num(s.get('pl_krw')):+,.0f}원 ({pct(s.get('pl_pct'))})")
    parts.append(f"{s.get('holdings', 0)}종목 · 국내 {pct(s.get('domestic_pct'), False)}")
    hist = _rows(data.get("equity_history"))
    if len(hist) >= 2:
        first, last = hist[0], hist[-1]
        change = (_num(last.get("total")) / _num(first.get("total"), 1) - 1) * 100
        parts.append(f"{first.get('date')} 기록 시작 이후 {change:+.1f}%")
    return " · ".join(parts)


def habits_text(h: dict) -> str:
    if not h or not h.get("sells"):
        return "최근 1년 매도 기록이 없습니다."
    bits = [f"매도 {h['sells']}건"]
    if h.get("win_rate_pct") is not None:
        bits.append(f"승률 {h['win_rate_pct']:.0f}%")
    bits.append(f"실현손익 {_num(h.get('realized_pl')):+,.0f}원")
    bits.append(f"수수료·세금 {won(h.get('fees_and_tax'))}")
    return " · ".join(bits)


class MyAccountTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[_Worker] = []
        self._loaded_once = False
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("증권사: KB증권"))
        self.load_btn = QPushButton("불러오기")
        self.load_btn.clicked.connect(self.refresh)
        top.addWidget(self.load_btn)
        self.keys_btn = QPushButton("키 등록")
        self.keys_btn.clicked.connect(self._open_keys)
        top.addWidget(self.keys_btn)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        top.addWidget(self.status, 1)
        layout.addLayout(top)

        self.guide = QGroupBox("KB증권 연결하기")
        g = QVBoxLayout(self.guide)
        g.addWidget(QLabel(KEY_GUIDE))
        row = QHBoxLayout()
        open_btn = QPushButton("KB증권 Open API 신청 페이지 열기")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KB_OPEN_API_URL)))
        row.addWidget(open_btn)
        reg = QPushButton("키 등록")
        reg.clicked.connect(self._open_keys)
        row.addWidget(reg)
        row.addStretch(1)
        g.addLayout(row)
        self.guide.hide()
        layout.addWidget(self.guide)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)

        layout.addWidget(QLabel("분석"))
        self.findings = QListWidget()
        self.findings.setMaximumHeight(150)
        layout.addWidget(self.findings)

        split = QSplitter(Qt.Vertical)
        holdings_box = QWidget()
        hb = QVBoxLayout(holdings_box)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.addWidget(QLabel("보유 종목"))
        self.holdings = _make_table(["종목", "수량", "평가액", "비중", "평가손익", "손익률",
                                     "200일선", "1년 고점 대비", "변동성"])
        hb.addWidget(self.holdings)
        split.addWidget(holdings_box)

        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.habits = QLabel("")
        left.addWidget(QLabel("매매 기록 (최근 1년, 국내 실현손익)"))
        left.addWidget(self.habits)
        self.trades = _make_table(["일자", "종목", "구분", "수량", "실현손익", "수익률"])
        left.addWidget(self.trades)
        bl.addLayout(left, 1)
        right = QVBoxLayout()
        right.addWidget(QLabel("보유 종목 최근 악재 (더블클릭하면 기사 열기)"))
        self.news = _make_table(["종목", "감성", "제목"])
        self.news.cellDoubleClicked.connect(self._open_news)
        right.addWidget(self.news)
        bl.addLayout(right, 1)
        split.addWidget(bottom)
        layout.addWidget(split, 1)
        self._news_urls: list[str] = []

    # --- 동작 ---

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh()

    def refresh(self):
        self.load_btn.setEnabled(False)
        self.status.setText("증권사에서 불러오는 중… (처음엔 시세 분석까지 30초쯤 걸릴 수 있습니다)")
        worker = _Worker(core.account_overview)
        self._workers.append(worker)
        worker.done.connect(self._on_data)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        worker.start()

    def _open_keys(self):
        from alpha.strategy_widgets import ApiKeyDialog

        ApiKeyDialog(self, broker="kb").exec()
        self.refresh()

    def _open_news(self, row: int, _col: int):
        if 0 <= row < len(self._news_urls) and self._news_urls[row]:
            QDesktopServices.openUrl(QUrl(self._news_urls[row]))

    def _on_data(self, data):
        self.load_btn.setEnabled(True)
        if not isinstance(data, dict) or ("error" in data and "registered" not in data):
            err = data.get("error") if isinstance(data, dict) else data
            self.status.setText(f"불러오지 못했습니다: {err}")
            return
        if not data.get("registered"):
            self.guide.show()
            self.status.setText("KB증권 API 키가 아직 없습니다.")
            for w in (self.findings,):
                w.clear()
            self.summary.setText("")
            return
        self.guide.hide()
        if data.get("error"):
            self.status.setText(f"⚠️ KB증권 연결 실패: {data['error']}")
            return
        self.status.setText("✅ 불러옴 (조회 전용)"
                            + (f" · 매매 기록 일부 실패: {data['history_error']}" if data.get("history_error") else ""))
        self.summary.setText(summary_text(data))

        self.findings.clear()
        for f in _rows(data.get("findings")):
            item = QListWidgetItem(("⚠️ " if f.get("level") == "warn" else "• ") + str(f.get("text", "")))
            self.findings.addItem(item)
        if self.findings.count() == 0:
            self.findings.addItem("특별히 짚을 점이 없습니다.")

        _fill(self.holdings, [
            [f"{h.get('name')} ({h.get('ticker')})", f"{_num(h.get('quantity')):g}", won(h.get("value_krw")),
             pct(h.get("weight_pct"), False), f"{_num(h.get('pl_krw')):+,.0f}원", pct(h.get("pl_pct")),
             trend_text(h.get("above_200d")), pct(h.get("drawdown_pct")), pct(h.get("vol_pct"), False)]
            for h in _rows(data.get("holdings"))
        ])
        self.habits.setText(habits_text(data.get("habits") or {}))
        _fill(self.trades, [
            [str(t.get("date", "")), str(t.get("name") or t.get("ticker", "")),
             "매도" if t.get("side") == "sell" else "매수", f"{_num(t.get('quantity')):g}",
             f"{_num(t.get('realized_pl')):+,.0f}원" if t.get("side") == "sell" else "—",
             pct(t.get("return_pct")) if t.get("side") == "sell" else "—"]
            for t in _rows(data.get("trades"))
        ])
        news = _rows(data.get("bad_news"))
        self._news_urls = [str(n.get("url") or "") for n in news]
        _fill(self.news, [[str(n.get("name", "")), f"{_num(n.get('sentiment')):+.2f}", str(n.get("title", ""))]
                          for n in news])

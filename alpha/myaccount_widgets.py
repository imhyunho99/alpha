"""💼 내 계좌 탭 — 증권사 실계좌를 읽어 보여주고 분석한다. 조회 전용, 주문 버튼 없음."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
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


class ValueChart(QWidget):
    """에이전트 몫 평가액 곡선 + 넣은 돈(원금) 기준선. 기준선 위면 초록, 아래면 빨강."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[tuple[str, float]] = []
        self._principal = 0.0
        self.setMinimumHeight(180)

    def set_data(self, points, principal: float) -> None:
        self._points = [(str(t), float(v)) for t, v in points or [] if isinstance(v, (int, float))]
        self._principal = float(principal or 0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, right, top, bottom = 78, 12, 12, 24
        if len(self._points) < 2:
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "운용 기록이 쌓이면 그래프가 그려집니다 (15분마다 기록)")
            return
        values = [v for _, v in self._points] + ([self._principal] if self._principal else [])
        lo, hi = min(values), max(values)
        pad = (hi - lo) * 0.1 or max(hi * 0.01, 1.0)
        lo, hi = lo - pad, hi + pad

        def y_of(v):
            return top + (hi - v) / (hi - lo) * (h - top - bottom)

        def x_of(i):
            return left + i / (len(self._points) - 1) * (w - left - right)

        painter.setPen(QPen(QColor("#888888"), 1))
        for v in (hi - pad, lo + pad):
            painter.drawText(QPointF(4, y_of(v) + 4), f"{v:,.0f}")
        painter.drawText(QPointF(left, h - 6), _label_time(self._points[0][0]))
        end = _label_time(self._points[-1][0])
        painter.drawText(QPointF(w - right - 7 * len(end), h - 6), end)
        if self._principal:
            painter.setPen(QPen(QColor("#888888"), 1, Qt.DashLine))
            py = y_of(self._principal)
            painter.drawLine(QPointF(left, py), QPointF(w - right, py))
            painter.drawText(QPointF(left + 4, py - 4), f"넣은 돈 {self._principal:,.0f}")
        prev = None
        for i, (_, v) in enumerate(self._points):
            pt = QPointF(x_of(i), y_of(v))
            if prev is not None:
                up = v >= self._principal if self._principal else True
                painter.setPen(QPen(QColor("#2a9d8f" if up else "#d62828"), 2))
                painter.drawLine(prev, pt)
            prev = pt


def _label_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return iso[:10]


def chart_summary(chart: dict) -> str:
    if not chart or chart.get("value") is None:
        return ""
    parts = [f"넣은 돈 {won(chart.get('principal'))}", f"지금 {won(chart.get('value'))}"]
    if chart.get("pnl") is not None:
        parts.append(f"손익 {_num(chart.get('pnl')):+,.0f}원 ({_num(chart.get('pnl_pct')):+.2f}%)")
    parts.append(f"그중 현금 {won(chart.get('cash'))}")
    return " · ".join(parts)


def chart_points(chart: dict) -> list:
    """장중(15분) 기록이 있으면 그것, 없으면 일별."""
    intraday = [tuple(r) for r in (chart or {}).get("intraday") or [] if len(r) == 2]
    if len(intraday) >= 2:
        return intraday
    return [(d, v) for d, v in (chart or {}).get("daily") or []]


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


def shadow_text(data) -> str:
    if not isinstance(data, dict) or "error" in data:
        err = data.get("error") if isinstance(data, dict) else data
        if isinstance(err, dict):
            err = err.get("detail") or err
        return f"⚠️ {err}"
    if not data.get("exists"):
        return "아직 시작 전입니다. 시작하면 에이전트가 이 계좌를 기준으로 리밸런싱 계획을 세웁니다."
    state = "운용 중" if data.get("active") else "멈춤"
    mode = "🔴 실주문" if not data.get("dry_run", True) else "주문 기록만"
    scope = (f"새 돈만 운용 · 기존 {len(data.get('baseline') or {})}종목 보호" if data.get("sleeve")
             else "계좌 전체 운용")
    parts = [f"{state} · {mode} · {scope} · 온도 {data.get('temperature')}",
             f"시작 {str(data.get('seeded_at') or '')[:16].replace('T', ' ')}"]
    last = data.get("last_sync") or {}
    if last:
        if last.get("status") == "error":
            parts.append(f"마지막 계산 실패: {last.get('message', '')}")
        else:
            parts.append(f"마지막 계산 {str(last.get('at', ''))[5:16].replace('T', ' ')} · 주문 {last.get('orders', 0)}건"
                         + (f" · {last['message']}" if last.get("message") else ""))
    else:
        parts.append("첫 계산은 몇 분 안에 됩니다 (뉴스 루프 3분 주기)")
    parts.append("에이전트 판단 상세: 📰 뉴스 탭 → 'my-kb' 포트폴리오")
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
        # 보이는 동안 1분마다 에이전트 상태·차트만 새로 받는다(증권사 잔고 조회는 [불러오기] 때만)
        self._shadow_timer = QTimer(self)
        self._shadow_timer.setInterval(60_000)
        self._shadow_timer.timeout.connect(lambda: self._run(core.account_shadow, self._on_shadow))

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

        # 자동 리밸런싱 — 실계좌를 복제한 포트폴리오를 에이전트가 굴리고, 실계좌에 필요한 주문을 기록만 한다
        self.shadow_box = QGroupBox("자동 리밸런싱 (주문 기록만 — 실제 주문은 나가지 않습니다)")
        sb = QVBoxLayout(self.shadow_box)
        srow = QHBoxLayout()
        self.shadow_start = QPushButton("내 계좌로 에이전트 시작")
        self.shadow_start.clicked.connect(self._on_shadow_start)
        srow.addWidget(self.shadow_start)
        self.shadow_stop = QPushButton("멈추기")
        self.shadow_stop.clicked.connect(self._on_shadow_stop)
        srow.addWidget(self.shadow_stop)
        self.shadow_label = QLabel("")
        self.shadow_label.setWordWrap(True)
        srow.addWidget(self.shadow_label, 1)
        sb.addLayout(srow)
        self.shadow_money = QLabel("")
        self.shadow_money.setStyleSheet("font-weight: bold;")
        sb.addWidget(self.shadow_money)
        self.shadow_chart = ValueChart()
        sb.addWidget(self.shadow_chart)
        tables = QHBoxLayout()
        left_box = QVBoxLayout()
        left_box.addWidget(QLabel("에이전트가 산 종목"))
        self.shadow_holdings = _make_table(["종목", "수량", "평가액", "손익률"])
        self.shadow_holdings.setMaximumHeight(140)
        left_box.addWidget(self.shadow_holdings)
        tables.addLayout(left_box, 1)
        right_box = QVBoxLayout()
        right_box.addWidget(QLabel("주문 기록"))
        self.shadow_orders = _make_table(["시각", "종목", "주문", "수량", "금액", "결과"])
        self.shadow_orders.setMaximumHeight(140)
        right_box.addWidget(self.shadow_orders)
        tables.addLayout(right_box, 1)
        sb.addLayout(tables)
        layout.addWidget(self.shadow_box)

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
        self._shadow_timer.start()
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh()

    def hideEvent(self, event):
        self._shadow_timer.stop()
        super().hideEvent(event)

    def refresh(self):
        self.load_btn.setEnabled(False)
        self.status.setText("증권사에서 불러오는 중… (처음엔 시세 분석까지 30초쯤 걸릴 수 있습니다)")
        worker = _Worker(core.account_overview)
        self._workers.append(worker)
        worker.done.connect(self._on_data)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        worker.start()

    # --- 자동 리밸런싱 ---

    def _run(self, fn, callback, *args):
        worker = _Worker(fn, *args)
        self._workers.append(worker)
        worker.done.connect(callback)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        worker.start()

    def _on_shadow_start(self):
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(
            self, "에이전트 시작",
            "에이전트가 '새 돈'(지금 예수금과 앞으로의 입금)만 운용합니다.\n"
            "지금 보유한 종목은 기준 보유분으로 묶어 절대 팔지 않습니다.\n"
            "이미 시작했다면 에이전트 몫은 그대로 두고 기준 보유분만 지금 잔고로 다시 잡습니다. 진행할까요?",
        ) != QMessageBox.Yes:
            return
        self.shadow_label.setText("시작하는 중…")
        self._run(core.start_account_shadow, self._on_shadow)

    def _on_shadow_stop(self):
        self._run(core.stop_account_shadow, self._on_shadow)

    def _on_shadow(self, data):
        self.shadow_label.setText(shadow_text(data))
        exists = isinstance(data, dict) and data.get("exists")
        self.shadow_start.setText("지금 잔고로 다시 맞추기" if exists else "내 계좌로 에이전트 시작")
        self.shadow_stop.setEnabled(bool(exists and data.get("active")))
        chart = (data or {}).get("chart") if isinstance(data, dict) else None
        self.shadow_money.setText(chart_summary(chart or {}))
        self.shadow_chart.set_data(chart_points(chart or {}), (chart or {}).get("principal") or 0)
        _fill(self.shadow_holdings, [
            [str(h.get("ticker")), f"{_num(h.get('quantity')):.4g}", won(h.get("value_krw")), pct(h.get("pl_pct"))]
            for h in _rows((chart or {}).get("holdings"))
        ])
        _fill(self.shadow_orders, [
            [str(o.get("at", ""))[5:16].replace("T", " "), str(o.get("ticker", "")),
             "매수" if o.get("action") == "buy" else "매도", f"{_num(o.get('quantity')):g}",
             won(o.get("amount_krw")),
             ("체결 요청" if not o.get("dry_run", True) else "기록됨") if o.get("status") == "success"
             else {"risk_blocked": "위험 한도로 보류", "market_closed": "장 마감"}.get(o.get("status"), f"실패: {o.get('message', '')}")]
            for o in _rows((data or {}).get("orders") if isinstance(data, dict) else [])
        ])

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
        self._run(core.account_shadow, self._on_shadow)
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

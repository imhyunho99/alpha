"""전략/자격증명 관련 PySide6 위젯.

- LoginDialog: 사용자명/비밀번호 입력 → 토큰 저장
- ApiKeyDialog: 거래소별 키 입력 → 서버에 암호화 저장
- StrategyChatTab: 자연어 채팅 → 전략 등록/조회
"""
from __future__ import annotations

from typing import Optional

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import core

BROKER_FIELDS = {
    "alpaca": [
        ("api_key", "API Key", False),
        ("api_secret", "API Secret", True),
        ("base_url", "Base URL (paper or live)", False),
    ],
    "upbit": [
        ("access_key", "Access Key", False),
        ("secret_key", "Secret Key", True),
    ],
    "binance": [
        ("api_key", "API Key", False),
        ("api_secret", "API Secret", True),
    ],
    "kis": [
        ("app_key", "App Key", False),
        ("app_secret", "App Secret", True),
        ("account_no", "Account No (예: 12345678-01)", False),
        ("account_product_code", "Account Product Code (default 01)", False),
    ],
    "anthropic": [
        ("api_key", "Anthropic API Key (자연어 파싱용)", True),
    ],
}


# ---------- Login ----------
class LoginDialog(QDialog):
    """첫 실행 시: bootstrap 모드 → 첫 admin 계정 만들기. 그 외: 일반 로그인."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bootstrap = core.bootstrap_status().get("bootstrap_needed", False)
        self._bootstrap = bootstrap

        self.setWindowTitle("Alpha 첫 계정 생성" if bootstrap else "Alpha 로그인")
        self.setMinimumWidth(380)
        layout = QFormLayout(self)

        if bootstrap:
            note = QLabel(
                "✨ 첫 실행입니다. 사용할 admin 계정을 만들어주세요.\n"
                "(이후엔 이 비밀번호로 로그인합니다)"
            )
            note.setWordWrap(True)
            layout.addRow(note)

        self.username = QLineEdit("admin" if bootstrap else "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        layout.addRow("사용자명", self.username)
        layout.addRow("비밀번호 (8자 이상)", self.password)

        btns = QHBoxLayout()
        ok = QPushButton("계정 생성 + 로그인" if bootstrap else "로그인")
        cancel = QPushButton("취소")
        ok.clicked.connect(self._submit)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addRow(btns)

    def _submit(self):
        u = self.username.text().strip()
        p = self.password.text()
        if self._bootstrap:
            result = core.bootstrap_first_admin(u, p)
        else:
            result = core.login(u, p)
        if "access_token" in result:
            QMessageBox.information(
                self,
                "완료",
                "계정이 생성되고 로그인되었습니다." if self._bootstrap else "로그인되었습니다.",
            )
            self.accept()
        else:
            QMessageBox.warning(self, "실패", str(result.get("error") or result))


# ---------- API Key Dialog ----------
class ApiKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("거래소 API 키 등록")
        self.setMinimumWidth(520)
        self._field_widgets: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("거래소"))
        self.broker_box = QComboBox()
        self.broker_box.addItems(list(BROKER_FIELDS.keys()))
        self.broker_box.currentTextChanged.connect(self._render_fields)
        top.addWidget(self.broker_box)
        layout.addLayout(top)

        self.form_widget = QWidget()
        self.form_layout = QFormLayout(self.form_widget)
        layout.addWidget(self.form_widget)

        # 등록된 목록
        layout.addWidget(QLabel("현재 등록된 거래소:"))
        self.registered_list = QListWidget()
        layout.addWidget(self.registered_list)

        # 버튼
        btn_row = QHBoxLayout()
        save_btn = QPushButton("저장 / 갱신")
        delete_btn = QPushButton("선택 항목 삭제")
        close_btn = QPushButton("닫기")
        save_btn.clicked.connect(self._save)
        delete_btn.clicked.connect(self._delete_selected)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._render_fields(self.broker_box.currentText())
        self._refresh_list()

    def _render_fields(self, broker: str):
        # clear existing rows
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self._field_widgets.clear()
        for key, label, secret in BROKER_FIELDS.get(broker, []):
            edit = QLineEdit()
            if secret:
                edit.setEchoMode(QLineEdit.Password)
            self.form_layout.addRow(label, edit)
            self._field_widgets[key] = edit

    def _save(self):
        broker = self.broker_box.currentText()
        fields = {k: w.text().strip() for k, w in self._field_widgets.items()}
        fields = {k: v for k, v in fields.items() if v}
        if not fields:
            QMessageBox.warning(self, "입력 필요", "값을 입력해주세요.")
            return
        result = core._handle_request(
            "post", "/credentials", json={"broker": broker, "fields": fields}
        )
        if "error" in result:
            QMessageBox.warning(self, "실패", str(result["error"]))
            return
        QMessageBox.information(
            self, "저장 완료",
            f"{broker} 키 등록됨 (fingerprint: {result.get('fingerprint','-')})",
        )
        for w in self._field_widgets.values():
            w.clear()
        self._refresh_list()

    def _refresh_list(self):
        self.registered_list.clear()
        result = core._handle_request("get", "/credentials")
        for entry in result.get("brokers", []) or []:
            preview = ", ".join(f"{k}={v}" for k, v in entry.get("fields_preview", {}).items())
            item = QListWidgetItem(f"{entry['broker']} | {preview}")
            item.setData(Qt.UserRole, entry["broker"])
            self.registered_list.addItem(item)

    def _delete_selected(self):
        item = self.registered_list.currentItem()
        if not item:
            return
        broker = item.data(Qt.UserRole)
        if QMessageBox.question(
            self, "확인", f"{broker} 자격증명을 삭제할까요?"
        ) != QMessageBox.Yes:
            return
        core._handle_request("delete", f"/credentials/{broker}")
        self._refresh_list()


# ---------- Strategy chat tab ----------
class _StrategyWorker(QThread):
    done = Signal(object)
    error = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except Exception as e:
            self.error.emit(str(e))


class StrategyChatTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("💬 자연어로 전략 추가 (예: 'AAPL RSI 30 이하면 5주 매수')"))
        input_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("RSI, 골든크로스, 이동평균 조건 등을 한국어로 입력하세요…")
        send = QPushButton("전략 등록")
        preview = QPushButton("미리보기")
        send.clicked.connect(self._submit)
        preview.clicked.connect(self._preview)
        input_row.addWidget(self.chat_input)
        input_row.addWidget(preview)
        input_row.addWidget(send)
        layout.addLayout(input_row)

        self.preview_box = QTextEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setMaximumHeight(180)
        layout.addWidget(self.preview_box)

        layout.addWidget(QLabel("📋 등록된 전략"))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "이름", "종목", "브로커", "활성", "모드", "발동수"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        refresh = QPushButton("새로고침")
        toggle = QPushButton("활성/비활성")
        toggle_dry = QPushButton("DRY-RUN ↔ 실거래")
        run_now = QPushButton("즉시 평가")
        delete_btn = QPushButton("삭제")
        refresh.clicked.connect(self._refresh)
        toggle.clicked.connect(self._toggle_active)
        toggle_dry.clicked.connect(self._toggle_dry_run)
        run_now.clicked.connect(self._evaluate_now)
        delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(refresh)
        btn_row.addWidget(toggle)
        btn_row.addWidget(toggle_dry)
        btn_row.addWidget(run_now)
        btn_row.addWidget(delete_btn)
        layout.addLayout(btn_row)

        self._refresh()

    def _selected_id(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).text()

    def _preview(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        result = core._handle_request("post", "/strategies/parse", json={"text": text})
        self.preview_box.setText(str(result))

    def _submit(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        result = core._handle_request("post", "/strategies", json={"text": text})
        if "error" in result:
            QMessageBox.warning(self, "실패", str(result["error"]))
            return
        QMessageBox.information(self, "등록 완료", f"전략 '{result.get('name')}' 등록됨")
        self.chat_input.clear()
        self.preview_box.clear()
        self._refresh()

    def _refresh(self):
        result = core._handle_request("get", "/strategies")
        if not isinstance(result, dict) or "error" in result:
            QMessageBox.warning(
                self, "조회 실패",
                "전략 목록을 불러오지 못했습니다. 로그인 상태를 확인하세요.\n"
                f"세부: {result}",
            )
            self.table.setRowCount(0)
            return
        rows = result.get("strategies", [])
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r.get("id", "")))
            self.table.setItem(i, 1, QTableWidgetItem(r.get("name", "")))
            self.table.setItem(i, 2, QTableWidgetItem(", ".join(r.get("tickers", []))))
            self.table.setItem(i, 3, QTableWidgetItem(r.get("broker", "")))
            self.table.setItem(i, 4, QTableWidgetItem("✅" if r.get("active") else "⏸"))
            mode = "🧪 DRY" if r.get("dry_run", True) else "🔴 REAL"
            self.table.setItem(i, 5, QTableWidgetItem(mode))
            self.table.setItem(i, 6, QTableWidgetItem(str(r.get("fire_count", 0))))

    def _toggle_active(self):
        sid = self._selected_id()
        if not sid:
            return
        row = self.table.currentRow()
        active = self.table.item(row, 4).text() == "✅"
        core._handle_request("patch", f"/strategies/{sid}", json={"active": not active})
        self._refresh()

    def _toggle_dry_run(self):
        sid = self._selected_id()
        if not sid:
            return
        row = self.table.currentRow()
        is_dry = "DRY" in self.table.item(row, 5).text()
        next_dry = not is_dry
        if not next_dry:
            confirm = QMessageBox.question(
                self,
                "⚠️ 실거래 전환 확인",
                f"전략 {sid}를 DRY-RUN(시뮬레이션)에서 **실거래**로 전환합니다.\n"
                "다음 트리거부터 실제 거래소 API로 주문이 들어갑니다.\n\n"
                "거래소 API 키가 등록되어 있고, 손실 가능성을 이해하셨다면 '예'를 누르세요.",
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        core._handle_request("patch", f"/strategies/{sid}", json={"dry_run": next_dry})
        self._refresh()

    def _evaluate_now(self):
        sid = self._selected_id()
        if not sid:
            return
        result = core._handle_request("post", f"/strategies/{sid}/evaluate")
        QMessageBox.information(self, "평가 결과", str(result)[:1500])

    def _delete_selected(self):
        sid = self._selected_id()
        if not sid:
            return
        if QMessageBox.question(self, "확인", f"전략 {sid}를 삭제할까요?") != QMessageBox.Yes:
            return
        core._handle_request("delete", f"/strategies/{sid}")
        self._refresh()

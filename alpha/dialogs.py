"""로그인·API 키 대화상자.

- LoginDialog: 사용자명/비밀번호 입력 → 토큰 저장
- ApiKeyDialog: KB증권·텔레그램 키 입력 → 서버에 암호화 저장
- ApiConnectDialog: 로그인 직후 "어떤 API 를 붙이면 뭘 할 수 있는지" 안내 + 바로 등록
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import core

BROKER_FIELDS = {
    "kb": [
        ("app_key", "App Key (KB증권 홈페이지 > 고객서비스 > Open API)", False),
        ("app_secret", "App Secret", True),
    ],
    "telegram": [
        ("bot_token", "Bot Token (BotFather 가 준 값)", True),
        ("chat_id", "Chat ID (숫자)", False),
    ],
}

# 입력란 위에 보일 안내. 키 발급이 낯선 항목만.
BROKER_HELP = {
    "telegram": ("실주문·체결·오류가 생기면 휴대폰으로 알려줍니다(무료, 선택). 맥 알림센터 알림은 등록 없이도 옵니다.\n"
                 "1) 텔레그램에서 @BotFather 에게 /newbot → 이름을 정하면 Bot Token 을 줍니다.\n"
                 "2) 만든 봇과 대화방을 열어 아무 말이나 한 번 보냅니다.\n"
                 "3) 브라우저로 api.telegram.org/bot<토큰>/getUpdates 를 열어 \"chat\":{\"id\": 뒤 숫자가 Chat ID 입니다."),
}


# 화면에 보일 이름과 연결하면 생기는 기능. 키(내부 이름)는 서버 vault 의 broker 이름이다.
API_SERVICES = [
    ("kb", "KB증권", "내 계좌 조회 · 자동 매매"),
    ("telegram", "텔레그램 알림", "실주문·체결·오류를 휴대폰으로 알림 (무료, 선택)"),
]
API_LABELS = {key: label for key, label, _ in API_SERVICES}
PREFS_FILE = os.path.expanduser("~/AlphaModels/.client_prefs.json")


def _load_prefs() -> dict:
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
    except OSError:
        pass


def registered_apis() -> set[str] | None:
    """등록된 API 이름. 서버에 못 물으면 None."""
    result = core._handle_request("get", "/credentials")
    if not isinstance(result, dict) or "error" in result:
        return None
    return {str(e.get("broker")) for e in result.get("brokers", []) or []}


def maybe_prompt_api_connect(parent=None) -> None:
    """로그인 직후 한 번. 연결할 게 남아 있고 '다시 묻지 않기'를 안 골랐을 때만."""
    if _load_prefs().get("skip_api_prompt"):
        return
    done = registered_apis()
    if done is None or {"kb", "telegram"} <= done:
        return
    ApiConnectDialog(parent, registered=done).exec()


class ApiConnectDialog(QDialog):
    def __init__(self, parent=None, registered: set[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("API 연결")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel("API 키를 연결하면 아래 기능을 쓸 수 있습니다. 키는 이 컴퓨터에 암호화해 저장됩니다.\n"
                       "지금 안 해도 나중에 [계정 → API 키 관리]에서 연결할 수 있습니다.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.status_labels: dict[str, QLabel] = {}
        form = QFormLayout()
        for key, label, what in API_SERVICES:
            row = QHBoxLayout()
            status = QLabel()
            self.status_labels[key] = status
            row.addWidget(status)
            desc = QLabel(what)
            desc.setWordWrap(True)
            row.addWidget(desc, 1)
            btn = QPushButton("연결하기")
            btn.clicked.connect(lambda _=False, k=key: self._connect(k))
            row.addWidget(btn)
            form.addRow(label, row)
        layout.addLayout(form)
        more = QPushButton("등록된 키 관리…")
        more.clicked.connect(lambda: self._connect(None))
        layout.addWidget(more)
        self.skip = QCheckBox("다음부터 묻지 않기")
        layout.addWidget(self.skip)
        close = QPushButton("나중에")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self._render(registered or set())

    def _render(self, registered: set[str]) -> None:
        for key, label in self.status_labels.items():
            label.setText("✅ 연결됨" if key in registered else "⚪ 미연결")

    def _connect(self, key: str | None) -> None:
        ApiKeyDialog(self, broker=key).exec()
        self._render(registered_apis() or set())

    def accept(self) -> None:
        if self.skip.isChecked():
            prefs = _load_prefs()
            prefs["skip_api_prompt"] = True
            _save_prefs(prefs)
        super().accept()


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
            parent = self.parentWidget()
            QTimer.singleShot(0, lambda: maybe_prompt_api_connect(parent))
        else:
            title = "계정 생성 실패" if self._bootstrap else "로그인 실패"
            QMessageBox.warning(self, title, str(result.get("error") or "잠시 후 다시 시도해 주세요."))
            self.password.clear()
            self.password.setFocus()


# ---------- API Key Dialog ----------
class ApiKeyDialog(QDialog):
    def __init__(self, parent=None, broker: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("API 키 등록")
        self.setMinimumWidth(520)
        self._field_widgets: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("거래소"))
        self.broker_box = QComboBox()
        for key in BROKER_FIELDS:
            self.broker_box.addItem(API_LABELS.get(key, key), key)
        if broker and self.broker_box.findData(broker) >= 0:
            self.broker_box.setCurrentIndex(self.broker_box.findData(broker))
        self.broker_box.currentIndexChanged.connect(lambda _i: self._render_fields(self.broker_box.currentData()))
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

        self._render_fields(self.broker_box.currentData())
        self._refresh_list()

    def _render_fields(self, broker: str):
        # clear existing rows
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self._field_widgets.clear()
        if broker in BROKER_HELP:
            help_label = QLabel(BROKER_HELP[broker])
            help_label.setTextFormat(Qt.PlainText)   # '<토큰>' 이 HTML 태그로 먹히지 않게
            help_label.setWordWrap(True)
            help_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.form_layout.addRow(help_label)
        for key, label, secret in BROKER_FIELDS.get(broker, []):
            edit = QLineEdit()
            if secret:
                edit.setEchoMode(QLineEdit.Password)
            self.form_layout.addRow(label, edit)
            self._field_widgets[key] = edit

    def _save(self):
        broker = self.broker_box.currentData()
        fields = {k: w.text().strip() for k, w in self._field_widgets.items()}
        fields = {k: v for k, v in fields.items() if v}
        if not fields:
            QMessageBox.warning(self, "입력 필요", "값을 입력해주세요.")
            return
        result = core._handle_request(
            "post", "/credentials", json={"broker": broker, "fields": fields}
        )
        if "error" in result:
            err = result["error"]
            if isinstance(err, dict):
                err = err.get("detail") or err
            QMessageBox.warning(self, "저장 실패", str(err))
            return
        QMessageBox.information(
            self, "저장 완료",
            f"{API_LABELS.get(broker, broker)} 키 등록됨 (fingerprint: {result.get('fingerprint','-')})",
        )
        for w in self._field_widgets.values():
            w.clear()
        self._refresh_list()

    def _refresh_list(self):
        self.registered_list.clear()
        result = core._handle_request("get", "/credentials")
        for entry in result.get("brokers", []) or []:
            preview = ", ".join(f"{k}={v}" for k, v in entry.get("fields_preview", {}).items())
            item = QListWidgetItem(f"{API_LABELS.get(entry['broker'], entry['broker'])} | {preview}")
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

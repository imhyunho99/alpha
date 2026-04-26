import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QFileDialog, QMessageBox,
    QGroupBox, QStatusBar, QComboBox, QProgressBar, QDialog
)
from PySide6.QtCore import Slot, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QAction

from alpha import core
from alpha.strategy_widgets import ApiKeyDialog, LoginDialog, StrategyChatTab

class WorkerThread(QThread):
    """백그라운드 작업을 처리하는 스레드"""
    finished = Signal(object)  # 작업 완료 시그널
    error = Signal(str)  # 에러 시그널
    progress = Signal(str)  # 진행 상황 시그널
    
    def __init__(self, func, *args):
        super().__init__()
        self.func = func
        self.args = args
    
    def run(self):
        try:
            result = self.func(*self.args)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

class AlphaGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alpha AI 투자 에이전트 (v3.1)")
        self.setGeometry(100, 100, 1100, 800)

        self.portfolio_path = None
        self.worker_thread = None

        # 진행 상황 체크 타이머
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.check_progress)
        self.progress_timer.setInterval(2000)  # 2초마다 체크

        # 탭 구조: 분석 / 전략 채팅 / 자격증명
        from PySide6.QtWidgets import QTabWidget
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # 탭 1: 기존 분석 화면
        analysis_widget = QWidget()
        main_layout = QVBoxLayout(analysis_widget)
        self.create_control_box(main_layout)
        self.create_analytics_box(main_layout)
        self.create_progress_box(main_layout)
        self.create_result_box(main_layout)
        self.tabs.addTab(analysis_widget, "📈 분석/추천")

        # 탭 2: 전략 채팅
        self.strategy_tab = StrategyChatTab()
        self.tabs.addTab(self.strategy_tab, "💬 전략 채팅")

        self.create_status_bar()
        self._build_menu()

    def _build_menu(self):
        menu = self.menuBar()
        account = menu.addMenu("계정")
        login_act = QAction("로그인", self)
        login_act.triggered.connect(self._open_login)
        logout_act = QAction("로그아웃", self)
        logout_act.triggered.connect(self._logout)
        keys_act = QAction("거래소 API 키 관리…", self)
        keys_act.triggered.connect(self._open_keys)
        account.addAction(login_act)
        account.addAction(logout_act)
        account.addSeparator()
        account.addAction(keys_act)

    def _open_login(self):
        if LoginDialog(self).exec() == QDialog.Accepted:
            self.statusBar().showMessage("로그인됨", 3000)

    def _logout(self):
        core.logout()
        self.statusBar().showMessage("로그아웃됨", 3000)

    def _open_keys(self):
        ApiKeyDialog(self).exec()
    
    def create_progress_box(self, main_layout):
        """진행 상황 표시 박스"""
        progress_box = QGroupBox("작업 진행 상황")
        main_layout.addWidget(progress_box)
        progress_layout = QVBoxLayout(progress_box)
        
        self.progress_label = QLabel("대기 중...")
        progress_layout.addWidget(self.progress_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)
    
    def check_progress(self):
        """서버에서 진행 상황 확인"""
        try:
            import requests
            response = requests.get("http://127.0.0.1:8000/progress", timeout=1)
            if response.status_code == 200:
                progress = response.json()
                
                # 데이터 업데이트 진행 상황
                data_status = progress.get("data_update", {})
                if data_status.get("status") == "running":
                    current = data_status.get("current", 0)
                    total = data_status.get("total", 1)
                    message = data_status.get("message", "")
                    percent = int((current / total * 100)) if total > 0 else 0
                    self.progress_bar.setValue(percent)
                    self.progress_label.setText(f"⏳ 데이터 업데이트: {message} ({percent}%)")
                    return
                elif data_status.get("status") == "completed":
                    self.progress_bar.setValue(100)
                    self.progress_label.setText("✅ 데이터 업데이트 완료!")
                    self.progress_timer.stop()
                    self.progress_bar.setVisible(False)
                    return
                
                # 모델 업데이트 진행 상황
                model_status = progress.get("model_update", {})
                if model_status.get("status") == "running":
                    current = model_status.get("current", 0)
                    total = model_status.get("total", 1)
                    message = model_status.get("message", "")
                    percent = int((current / total * 100)) if total > 0 else 0
                    self.progress_bar.setValue(percent)
                    self.progress_label.setText(f"⏳ 모델 학습: {message} ({percent}%)")
                    return
                elif model_status.get("status") == "completed":
                    self.progress_bar.setValue(100)
                    self.progress_label.setText("✅ 모델 학습 완료!")
                    self.progress_timer.stop()
                    self.progress_bar.setVisible(False)
                    return
        except:
            pass

    def create_control_box(self, main_layout):
        control_box = QGroupBox("서버 제어")
        main_layout.addWidget(control_box)
        control_layout = QHBoxLayout(control_box)

        status_button = QPushButton("서버 상태 확인")
        status_button.clicked.connect(self.run_check_status)
        control_layout.addWidget(status_button)

        data_update_button = QPushButton("서버 데이터 업데이트 요청")
        data_update_button.clicked.connect(self.run_update_data)
        control_layout.addWidget(data_update_button)

        model_update_button = QPushButton("서버 모델 재학습 요청")
        model_update_button.clicked.connect(self.run_update_models)
        control_layout.addWidget(model_update_button)
        
    def create_analytics_box(self, main_layout):
        analytics_box = QGroupBox("분석 기능")
        main_layout.addWidget(analytics_box)
        analytics_layout = QHBoxLayout(analytics_box)

        analytics_layout.addWidget(QLabel("투자기간:"))
        self.horizon_combo = QComboBox()
        self.horizon_combo.addItems(['short', 'medium', 'long'])
        self.horizon_combo.setCurrentText('medium')
        analytics_layout.addWidget(self.horizon_combo)

        recommend_button = QPushButton("Top 10 투자 추천 받기")
        recommend_button.clicked.connect(self.run_get_recommendations)
        analytics_layout.addWidget(recommend_button)

        analytics_layout.addSpacing(40)

        portfolio_button = QPushButton("포트폴리오 파일 선택")
        portfolio_button.clicked.connect(self.select_portfolio_file)
        analytics_layout.addWidget(portfolio_button)
        
        self.portfolio_label = QLabel("선택된 파일 없음")
        analytics_layout.addWidget(self.portfolio_label)

        assess_button = QPushButton("포트폴리오 평가")
        assess_button.clicked.connect(self.run_assess)
        analytics_layout.addWidget(assess_button)
        
        analytics_layout.addStretch()

    def create_result_box(self, main_layout):
        result_box = QGroupBox("분석 결과")
        main_layout.addWidget(result_box)
        result_layout = QVBoxLayout(result_box)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setLineWrapMode(QTextEdit.NoWrap)
        self.result_text.setFont(QFont("Monaco", 12)) # 고정폭 폰트 사용
        result_layout.addWidget(self.result_text)
        
    def create_status_bar(self):
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("준비 완료. 먼저 서버를 실행하세요.")

    @Slot()
    def select_portfolio_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "포트폴리오 파일을 선택하세요", "", "JSON Files (*.json);;All Files (*)")
        if path:
            self.portfolio_path = path
            self.portfolio_label.setText(path.split('/')[-1])

    def _execute_and_display(self, core_function, formatter, *args):
        """백그라운드 스레드에서 작업 실행"""
        self.result_text.clear()
        self.progress_bar.setVisible(True)
        self.progress_label.setText("⏳ 작업 실행 중...")
        self.statusBar.showMessage("⏳ 작업 실행 중...", 0)
        self.result_text.setPlainText("⏳ 서버에 요청 중입니다...\n잠시만 기다려주세요.")
        QApplication.processEvents()
        
        # 이전 스레드가 실행 중이면 대기
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "경고", "이전 작업이 아직 실행 중입니다. 잠시 후 다시 시도해주세요.")
            return
        
        # 워커 스레드 생성 및 시작
        self.worker_thread = WorkerThread(core_function, *args)
        self.worker_thread.finished.connect(lambda result: self._on_task_finished(result, formatter))
        self.worker_thread.error.connect(self._on_task_error)
        self.worker_thread.start()
    
    def _on_task_finished(self, result, formatter):
        """작업 완료 시 호출"""
        self.progress_bar.setVisible(False)
        self.progress_label.setText("✅ 작업 완료")
        
        if "error" in result:
            error_msg = result['error']
            self.result_text.setPlainText(f"❌ 오류가 발생했습니다:\n\n{error_msg}")
            self.statusBar.showMessage("❌ 작업 실패", 5000)
            
            # 서버 에러인 경우 추가 안내
            if "500" in error_msg or "Internal Server Error" in error_msg:
                self.result_text.append("\n\n💡 해결 방법:")
                self.result_text.append("1. '서버 데이터 업데이트 요청' 버튼을 먼저 눌러주세요")
                self.result_text.append("2. 데이터 업데이트가 완료될 때까지 기다려주세요 (약 5-10분)")
                self.result_text.append("3. '서버 모델 재학습 요청' 버튼을 눌러주세요")
                self.result_text.append("4. 모델 학습이 완료된 후 다시 시도해주세요")
        else:
            formatted_text = formatter(result)
            self.result_text.setPlainText(formatted_text)
            self.statusBar.showMessage("✅ 작업 완료", 5000)
    
    def _on_task_error(self, error_msg):
        """작업 에러 시 호출"""
        self.progress_bar.setVisible(False)
        self.progress_label.setText("❌ 작업 실패")
        
        error_text = f"❌ 오류가 발생했습니다:\n\n{error_msg}"
        self.result_text.setPlainText(error_text)
        self.statusBar.showMessage("❌ 작업 실패", 5000)
        
        # 연결 오류인 경우
        if "Connection refused" in error_msg or "Max retries" in error_msg:
            self.result_text.append("\n\n💡 서버가 실행되지 않았습니다.")
            self.result_text.append("앱을 다시 시작해주세요.")
    
    # --- 포매터 함수들 ---
    def format_default(self, result):
        return json.dumps(result, indent=2, ensure_ascii=False)

    def format_recommendations(self, result):
        horizon = result.get('horizon', 'N/A')
        lines = [f"======= Top 10 투자 추천 (투자기간: {horizon.upper()}) =======\n"]
        generated_at = result.get("generated_at", "N/A")
        lines.append(f"분석 시각: {generated_at}\n")
        
        lines.append(f"{'순위':<5s} | {'Ticker':<12s} | {'투자 가치 점수':<15s}")
        lines.append("-" * 45)
        
        for i, rec in enumerate(result.get("recommendations", [])):
            rank = f"#{i+1}"
            score = rec.get('score', 0)
            lines.append(f"{rank:<5s} | {rec.get('symbol', ''):<12s} | {score: >14.2f}")
        return "\n".join(lines)

    def format_assessment(self, result):
        lines = ["======= 포트폴리오 상세 분석 결과 =======\n"]
        generated_at = result.get("generated_at", "N/A")
        lines.append(f"분석 시각: {generated_at}\n")

        summary = result.get('summary', {})
        lines.append("--- 포트폴리오 요약 ---")
        lines.append(f"총 매수 금액: ${summary.get('total_purchase_value', 0):,.2f}")
        lines.append(f"총 현재 가치: ${summary.get('total_current_value', 0):,.2f}")
        total_pl_percent = summary.get('total_profit_loss_percent', 0)
        lines.append(f"총 수익률: {total_pl_percent:.2f}%\n")
        
        lines.append("--- 개별 자산 분석 ---")
        header = f"{'Ticker':<10s} | {'수익률':>8s} | {'현재가':>10s} | {'단기점수':>10s} | {'중기점수':>10s} | {'장기점수':>10s}"
        lines.append(header)
        lines.append("-" * len(header))
        
        for detail in result.get("details", []):
            if "error" in detail:
                lines.append(f"{detail.get('symbol', ''):<10s} | 오류: {detail['error']}")
                continue
            
            scores = detail.get('scores', {})
            lines.append(
                f"{detail.get('symbol', ''):<10s} | "
                f"{detail.get('profit_loss_percent', 0):>7.2f}% | "
                f"${detail.get('current_price', 0):>9,.2f} | "
                f"{scores.get('short', 0):>10.2f} | "
                f"{scores.get('medium', 0):>10.2f} | "
                f"{scores.get('long', 0):>10.2f}"
            )
        return "\n".join(lines)

    # --- 버튼 슬롯 함수들 ---
    @Slot()
    def run_check_status(self):
        self.progress_label.setText("⏳ 서버 상태 확인 중...")
        self.statusBar.showMessage("⏳ 서버 상태 확인 중...", 0)
        self._execute_and_display(core.check_server_status, self.format_default)

    @Slot()
    def run_update_data(self):
        self.progress_label.setText("⏳ 데이터 업데이트 시작...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.statusBar.showMessage("⏳ 데이터 업데이트 요청 중... (5-10분 소요)", 0)
        self.result_text.setPlainText("⏳ 데이터 업데이트를 시작합니다...\n\n약 5-10분 정도 소요됩니다.\n\n위의 '작업 진행 상황'에서 실시간 진행률을 확인할 수 있습니다.")
        QApplication.processEvents()
        self.progress_timer.start()  # 진행 상황 체크 시작
        self._execute_and_display(core.update_server_data, self.format_default)

    @Slot()
    def run_update_models(self):
        self.progress_label.setText("⏳ 모델 학습 시작...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.statusBar.showMessage("⏳ 모델 재학습 요청 중... (10-20분 소요)", 0)
        self.result_text.setPlainText("⏳ 모델 재학습을 시작합니다...\n\n약 10-20분 정도 소요됩니다.\n\n위의 '작업 진행 상황'에서 실시간 진행률을 확인할 수 있습니다.")
        QApplication.processEvents()
        self.progress_timer.start()  # 진행 상황 체크 시작
        self._execute_and_display(core.update_server_models, self.format_default)

    @Slot()
    def run_get_recommendations(self):
        horizon = self.horizon_combo.currentText()
        self.progress_label.setText(f"⏳ {horizon} 기간 투자 추천 분석 중...")
        self.statusBar.showMessage(f"⏳ {horizon} 기간 투자 추천 분석 중...", 0)
        self._execute_and_display(core.get_recommendations, self.format_recommendations, horizon)

    @Slot()
    def run_assess(self):
        if not self.portfolio_path:
            QMessageBox.warning(self, "경고", "포트폴리오 파일을 먼저 선택해주세요.")
            return
        self.progress_label.setText("⏳ 포트폴리오 분석 중...")
        self.statusBar.showMessage("⏳ 포트폴리오 분석 중...", 0)
        self._execute_and_display(core.assess_portfolio, self.format_assessment, self.portfolio_path)

def start_gui():
    app = QApplication(sys.argv)
    window = AlphaGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    start_gui()
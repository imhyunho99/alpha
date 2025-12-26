import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QFileDialog, QMessageBox,
    QGroupBox, QStatusBar, QComboBox
)
from PySide6.QtCore import Slot
from PySide6.QtGui import QFont

from . import core

class AlphaGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alpha AI 투자 에이전트 (클라이언트 v2.0)")
        self.setGeometry(100, 100, 900, 700)

        self.portfolio_path = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)

        self.create_control_box(main_layout)
        self.create_analytics_box(main_layout)
        self.create_result_box(main_layout)
        self.create_status_bar()

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
        self.result_text.clear()
        self.statusBar.showMessage("서버에 작업 요청 중...", 3000)
        QApplication.processEvents()

        try:
            result = core_function(*args)
            if "error" in result:
                self.result_text.setPlainText(f"오류가 발생했습니다:\n\n{result['error']}")
                self.statusBar.showMessage("서버 오류 발생.", 5000)
            else:
                formatted_text = formatter(result)
                self.result_text.setPlainText(formatted_text)
                self.statusBar.showMessage("작업 완료.", 5000)
        except Exception as e:
            QMessageBox.critical(self, "치명적 오류", str(e))
            self.statusBar.showMessage("치명적 오류 발생.")
    
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
        self._execute_and_display(core.check_server_status, self.format_default)

    @Slot()
    def run_update_data(self):
        self._execute_and_display(core.update_server_data, self.format_default)

    @Slot()
    def run_update_models(self):
        self._execute_and_display(core.update_server_models, self.format_default)

    @Slot()
    def run_get_recommendations(self):
        horizon = self.horizon_combo.currentText()
        self._execute_and_display(core.get_recommendations, self.format_recommendations, horizon)

    @Slot()
    def run_assess(self):
        if not self.portfolio_path:
            QMessageBox.warning(self, "경고", "포트폴리오 파일을 먼저 선택해주세요.")
            return
        self._execute_and_display(core.assess_portfolio, self.format_assessment, self.portfolio_path)

def start_gui():
    app = QApplication(sys.argv)
    window = AlphaGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    start_gui()
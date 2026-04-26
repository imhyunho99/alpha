import argparse
import sys
import subprocess
import time
import os
from alpha import core
from alpha import gui

def start_server():
    """서버를 백그라운드에서 실행"""
    try:
        import sys
        # PyInstaller로 패키징된 경우
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
            # 서버 모듈 경로 추가
            sys.path.insert(0, base_path)
        else:
            base_path = os.path.dirname(__file__)
        
        # 서버를 별도 프로세스가 아닌 스레드로 실행
        import threading
        import uvicorn
        
        def run_server():
            try:
                # 로그 파일 생성
                log_file = os.path.expanduser("~/alpha_server.log")
                with open(log_file, 'w') as f:
                    f.write(f"서버 시작 시도...\n")
                    f.write(f"base_path: {base_path}\n")
                    f.write(f"sys.path: {sys.path}\n")
                
                uvicorn.run("alpha_server.main:app", host="127.0.0.1", port=8000, log_level="info")
            except Exception as e:
                log_file = os.path.expanduser("~/alpha_server_error.log")
                with open(log_file, 'w') as f:
                    import traceback
                    f.write(f"서버 실행 중 오류: {e}\n")
                    f.write(traceback.format_exc())
        
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        time.sleep(5)  # 서버 시작 대기 시간 증가
        return server_thread
    except Exception as e:
        print(f"서버 시작 실패: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    # 인자 없이 실행되면 GUI 모드로 실행
    if len(sys.argv) == 1:
        server_thread = start_server()
        try:
            gui.start_gui()
        finally:
            pass  # daemon 스레드는 자동 종료
        return
    
    parser = argparse.ArgumentParser(description="Alpha: AI 기반 투자 분석 및 추천 시스템")
    parser.add_argument("--version", action="version", version="Alpha v3.1.0")
    parser.add_argument("--gui", action="store_true", help="GUI 모드로 실행합니다.")

    # GUI 모드가 아닐 때만 command를 필수로 하도록 처리
    # sys.argv에 --gui가 없을 때만 subparsers를 생성하고 required=True로 설정
    if '--gui' not in sys.argv:
        subparsers = parser.add_subparsers(dest="command", help="사용 가능한 명령어", required=True)
        # init 명령어
        subparsers.add_parser("init", help="Alpha 애플리케이션 초기화")
        # fetch-data 명령어
        subparsers.add_parser("fetch-data", help="시장 데이터 가져오기")
        # train 명령어
        subparsers.add_parser("train", help="AI 모델 학습")
        # recommend 명령어
        recommend_parser = subparsers.add_parser("recommend", help="투자 상품 추천 받기")
        recommend_parser.add_argument("--term", choices=["short", "medium", "long"], required=True,
                                      help="추천 기간 (short: 단기, medium: 중기, long: 장기)")
        # assess 명령어
        assess_parser = subparsers.add_parser("assess", help="현재 보유 자산 평가")
        assess_parser.add_argument("--portfolio", type=str, required=True,
                                   help="포트폴리오 파일 경로 (예: portfolio.json)")
    
    args = parser.parse_args()

    if args.gui:
        server_thread = start_server()
        try:
            gui.start_gui()
        finally:
            pass  # daemon 스레드는 자동 종료
    else:
        print("=== Alpha AI 투자 에이전트 (CLI) ===")
        
        result = None
        if args.command == "init":
            result = core.initialize_alpha()
        elif args.command == "fetch-data":
            result = core.fetch_market_data()
        elif args.command == "train":
            result = core.train_model()
        elif args.command == "recommend":
            result = core.get_recommendations(args.term)
        elif args.command == "assess":
            result = core.assess_portfolio(args.portfolio)
        
        if result:
            print("\n[결과]")
            print(result)

if __name__ == "__main__":
    main()

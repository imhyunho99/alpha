import argparse
import sys
from . import core
from . import gui

def main():
    parser = argparse.ArgumentParser(description="Alpha: AI 기반 투자 분석 및 추천 시스템")
    parser.add_argument("--version", action="version", version="Alpha v0.1.0")
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
        gui.start_gui()
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

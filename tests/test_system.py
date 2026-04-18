#!/usr/bin/env python3
"""
Alpha 프로젝트 시스템 테스트
모든 주요 컴포넌트가 정상 작동하는지 확인합니다.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    """모든 필수 모듈 임포트 테스트"""
    print("=" * 60)
    print("1. 모듈 임포트 테스트")
    print("=" * 60)
    
    try:
        print("  - alpha_server.main 임포트 중...")
        from alpha_server import main as server_main
        print("    ✅ 서버 모듈 임포트 성공")
    except Exception as e:
        print(f"    ❌ 서버 모듈 임포트 실패: {e}")
        return False
    
    try:
        print("  - alpha.core 임포트 중...")
        from alpha import core
        print("    ✅ 클라이언트 코어 모듈 임포트 성공")
    except Exception as e:
        print(f"    ❌ 클라이언트 코어 모듈 임포트 실패: {e}")
        return False
    
    try:
        print("  - alpha.gui 임포트 중...")
        from alpha import gui
        print("    ✅ GUI 모듈 임포트 성공")
    except Exception as e:
        print(f"    ❌ GUI 모듈 임포트 실패: {e}")
        return False
    
    return True

def test_data_handler():
    """데이터 핸들러 테스트"""
    print("\n" + "=" * 60)
    print("2. 데이터 핸들러 테스트")
    print("=" * 60)
    
    try:
        from alpha_server import data_handler
        print(f"  - 데이터 저장 모드: {'QuestDB' if data_handler.USE_QUESTDB else 'CSV'}")
        print("    ✅ 데이터 핸들러 정상")
        return True
    except Exception as e:
        print(f"    ❌ 데이터 핸들러 오류: {e}")
        return False

def test_asset_screener():
    """자산 스크리너 테스트"""
    print("\n" + "=" * 60)
    print("3. 자산 스크리너 테스트")
    print("=" * 60)
    
    try:
        from alpha_server.asset_screener import get_benchmark_tickers
        tickers = get_benchmark_tickers()
        print(f"  - 벤치마크 티커 수: {len(tickers)}")
        print(f"  - 티커 목록: {tickers}")
        print("    ✅ 자산 스크리너 정상")
        return True
    except Exception as e:
        print(f"    ❌ 자산 스크리너 오류: {e}")
        return False

def test_model_handler():
    """모델 핸들러 테스트"""
    print("\n" + "=" * 60)
    print("4. 모델 핸들러 테스트")
    print("=" * 60)
    
    try:
        from alpha_server import model_handler
        models_dir = model_handler.MODELS_DIR
        print(f"  - 모델 저장 디렉토리: {models_dir}")
        print(f"  - 디렉토리 존재: {os.path.exists(models_dir)}")
        print("    ✅ 모델 핸들러 정상")
        return True
    except Exception as e:
        print(f"    ❌ 모델 핸들러 오류: {e}")
        return False

def test_fastapi_app():
    """FastAPI 앱 생성 테스트"""
    print("\n" + "=" * 60)
    print("5. FastAPI 앱 테스트")
    print("=" * 60)
    
    try:
        from alpha_server.main import app
        print(f"  - 앱 제목: {app.title}")
        print(f"  - 앱 버전: {app.version}")
        print(f"  - 라우트 수: {len(app.routes)}")
        print("    ✅ FastAPI 앱 정상")
        return True
    except Exception as e:
        print(f"    ❌ FastAPI 앱 오류: {e}")
        return False

def main():
    """모든 테스트 실행"""
    print("\n🚀 Alpha 프로젝트 시스템 테스트 시작\n")
    
    results = []
    results.append(("모듈 임포트", test_imports()))
    results.append(("데이터 핸들러", test_data_handler()))
    results.append(("자산 스크리너", test_asset_screener()))
    results.append(("모델 핸들러", test_model_handler()))
    results.append(("FastAPI 앱", test_fastapi_app()))
    
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    
    for name, result in results:
        status = "✅ 통과" if result else "❌ 실패"
        print(f"  {name}: {status}")
    
    all_passed = all(result for _, result in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 모든 테스트 통과! 시스템이 정상 작동합니다.")
        print("\n다음 명령어로 Alpha를 시작할 수 있습니다:")
        print("  ./start_alpha.sh")
    else:
        print("⚠️  일부 테스트가 실패했습니다. 위의 오류를 확인하세요.")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())

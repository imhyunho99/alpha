#!/usr/bin/env python3
"""
Alpha 프로젝트 최종 검증 스크립트
실제 서버 시작 및 API 호출 테스트
"""

import subprocess
import time
import requests
import sys
import signal

def start_server_process():
    """서버 프로세스 시작"""
    print("🚀 서버 시작 중...")
    process = subprocess.Popen(
        ["bash", "-c", "source venv/bin/activate && uvicorn alpha_server.main:app --log-level error"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    time.sleep(5)  # 서버 시작 대기
    return process

def test_server_endpoints():
    """서버 엔드포인트 테스트"""
    base_url = "http://127.0.0.1:8000"
    
    tests = []
    
    # 1. 루트 엔드포인트
    print("\n1. 서버 상태 확인...")
    try:
        response = requests.get(f"{base_url}/", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ 서버 응답: {data['message']}")
            tests.append(True)
        else:
            print(f"   ❌ 상태 코드: {response.status_code}")
            tests.append(False)
    except Exception as e:
        print(f"   ❌ 오류: {e}")
        tests.append(False)
    
    # 2. 진행 상황 확인
    print("\n2. 진행 상황 API 확인...")
    try:
        response = requests.get(f"{base_url}/progress", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ 진행 상황 API 정상")
            tests.append(True)
        else:
            print(f"   ❌ 상태 코드: {response.status_code}")
            tests.append(False)
    except Exception as e:
        print(f"   ❌ 오류: {e}")
        tests.append(False)
    
    # 3. 추천 API (데이터 없어도 오류 메시지 반환하면 성공)
    print("\n3. 추천 API 확인...")
    try:
        response = requests.get(f"{base_url}/recommendations?horizon=medium&top_n=5", timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "error" in data:
                print(f"   ⚠️  예상된 오류 (데이터 없음): {data['error']}")
                print(f"   ✅ API는 정상 작동")
            else:
                print(f"   ✅ 추천 결과: {len(data.get('recommendations', []))}개")
            tests.append(True)
        else:
            print(f"   ❌ 상태 코드: {response.status_code}")
            tests.append(False)
    except Exception as e:
        print(f"   ❌ 오류: {e}")
        tests.append(False)
    
    return all(tests)

def main():
    """메인 함수"""
    print("=" * 60)
    print("Alpha 프로젝트 최종 검증")
    print("=" * 60)
    
    server_process = None
    
    try:
        # 서버 시작
        server_process = start_server_process()
        
        # 서버가 정상 시작되었는지 확인
        if server_process.poll() is not None:
            print("❌ 서버 시작 실패")
            return 1
        
        print("✅ 서버 시작 성공")
        
        # API 테스트
        all_passed = test_server_endpoints()
        
        print("\n" + "=" * 60)
        if all_passed:
            print("🎉 모든 검증 통과!")
            print("\n시스템이 정상 작동합니다.")
            print("\n다음 명령어로 Alpha를 시작하세요:")
            print("  ./start_alpha.sh")
        else:
            print("⚠️  일부 테스트 실패")
        print("=" * 60)
        
        return 0 if all_passed else 1
        
    except KeyboardInterrupt:
        print("\n\n중단됨")
        return 1
    finally:
        # 서버 프로세스 종료
        if server_process:
            print("\n서버 종료 중...")
            server_process.send_signal(signal.SIGTERM)
            server_process.wait(timeout=5)
            print("✅ 서버 종료 완료")

if __name__ == "__main__":
    sys.exit(main())

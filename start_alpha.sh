#!/bin/bash

# Alpha 통합 실행 스크립트 (macOS/Linux)

echo "🚀 Alpha AI 투자 분석 시스템 시작"
echo ""

cd "$(dirname "$0")"

# 기존 서버 프로세스 종료
echo "기존 서버 확인 중..."
if lsof -ti:8000 > /dev/null 2>&1; then
    echo "기존 서버 종료 중..."
    kill -9 $(lsof -ti:8000) 2>/dev/null
    sleep 1
fi

# 가상환경 활성화
source venv/bin/activate

# 서버 백그라운드 실행
echo "서버 시작 중..."
uvicorn alpha_server.main:app --log-level warning > /dev/null 2>&1 &
SERVER_PID=$!

# 서버 시작 대기
sleep 3

# 서버 상태 확인
if curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
    echo "✅ 서버 시작 완료 (PID: $SERVER_PID)"
    echo ""
    echo "GUI 시작 중..."
    python -m alpha.main --gui
    
    # GUI 종료 시 서버도 종료
    echo ""
    echo "서버 종료 중..."
    kill $SERVER_PID 2>/dev/null
    echo "✅ 종료 완료"
else
    echo "❌ 서버 시작 실패"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi

#!/bin/bash

# Alpha 개발 모드 실행 스크립트

echo "🚀 Alpha 개발 모드 시작..."

# 1. 서버 시작
echo "📡 서버 시작 중..."
cd /Users/nahyeonho/pythonWorkspace
source alpha_server/venv/bin/activate
uvicorn alpha_server.main:app --reload --host 127.0.0.1 --port 8000 &
SERVER_PID=$!
echo "✅ 서버 시작됨 (PID: $SERVER_PID)"

# 서버 시작 대기
sleep 3

# 2. GUI 클라이언트 시작
echo "🖥️  GUI 클라이언트 시작 중..."
cd /Users/nahyeonho/pythonWorkspace/Alpha
source venv/bin/activate
python -m alpha.main --gui &
CLIENT_PID=$!
echo "✅ GUI 시작됨 (PID: $CLIENT_PID)"

echo ""
echo "✅ Alpha 실행 중!"
echo "   서버: http://127.0.0.1:8000"
echo "   GUI: 실행됨"
echo ""
echo "종료하려면 Ctrl+C를 누르세요"

# 종료 시그널 처리
trap "echo '⏹️  종료 중...'; kill $SERVER_PID $CLIENT_PID 2>/dev/null; exit" INT TERM

# 프로세스 대기
wait

#!/bin/bash
# Alpha 클라이언트 실행 스크립트

echo "🚀 Alpha AI 투자 에이전트 시작"
echo ""

# 서버 상태 확인
echo "📡 서버 연결 확인 중..."
if curl -s http://localhost:8000/ > /dev/null 2>&1; then
    echo "   ✅ 서버 연결 성공"
else
    echo "   ⚠️  서버가 실행되고 있지 않습니다"
    echo ""
    echo "서버를 먼저 실행하세요:"
    echo "  cd /Users/nahyeonho/pythonWorkspace/Alpha"
    echo "  venv/bin/uvicorn alpha_server.main:app"
    echo ""
    read -p "서버 없이 계속하시겠습니까? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# QuestDB 상태 확인
echo ""
echo "🗄️  QuestDB 연결 확인 중..."
if curl -s http://localhost:9000/ > /dev/null 2>&1; then
    echo "   ✅ QuestDB 연결 성공"
else
    echo "   ⚠️  QuestDB가 실행되고 있지 않습니다"
    echo ""
    echo "QuestDB를 시작하세요:"
    echo "  brew services start questdb"
fi

# 앱 실행
echo ""
echo "🎨 GUI 클라이언트 실행 중..."
open dist/AlphaClient.app

echo ""
echo "✅ 클라이언트가 실행되었습니다!"

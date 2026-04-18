#!/bin/bash

echo "🚀 Alpha 서버 시작 (개발 모드)"
echo "로그가 터미널에 실시간으로 표시됩니다."
echo ""

cd /Users/nahyeonho/pythonWorkspace/Alpha
source venv/bin/activate
uvicorn alpha_server.main:app --reload --log-level info

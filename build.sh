#!/bin/bash
# Alpha 프로젝트 빌드 스크립트

set -e

echo "================================"
echo "Alpha 프로젝트 빌드 시작"
echo "================================"

# 현재 디렉토리 확인
if [ ! -f "AlphaClient.spec" ]; then
    echo "❌ 오류: Alpha 프로젝트 루트에서 실행하세요"
    exit 1
fi

# 가상환경 활성화 확인
if [ ! -d "venv" ]; then
    echo "❌ 오류: venv 디렉토리가 없습니다"
    exit 1
fi

# 이전 빌드 정리
echo ""
echo "1. 이전 빌드 정리..."
rm -rf build dist *.app

# GUI 클라이언트 빌드
echo ""
echo "2. GUI 클라이언트 빌드 중..."
venv/bin/pyinstaller AlphaClient.spec --clean --noconfirm

if [ -d "dist/AlphaClient.app" ]; then
    echo "   ✅ GUI 클라이언트 빌드 완료: dist/AlphaClient.app"
else
    echo "   ❌ GUI 클라이언트 빌드 실패"
    exit 1
fi

# 서버 빌드 (선택적)
echo ""
read -p "서버도 빌드하시겠습니까? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "3. 서버 빌드 중..."
    venv/bin/pyinstaller AlphaServer.spec --clean --noconfirm
    
    if [ -d "dist/AlphaServer" ]; then
        echo "   ✅ 서버 빌드 완료: dist/AlphaServer/"
    else
        echo "   ❌ 서버 빌드 실패"
        exit 1
    fi
fi

# 빌드 결과 요약
echo ""
echo "================================"
echo "빌드 완료!"
echo "================================"
echo ""
echo "📦 빌드된 파일:"
ls -lh dist/

echo ""
echo "🚀 실행 방법:"
echo "   GUI 클라이언트: open dist/AlphaClient.app"
if [ -d "dist/AlphaServer" ]; then
    echo "   서버: ./dist/AlphaServer/AlphaServer"
fi

echo ""
echo "⚠️  주의사항:"
echo "   - 서버는 별도로 실행해야 합니다 (QuestDB 필요)"
echo "   - 첫 실행 시 macOS에서 보안 경고가 나올 수 있습니다"
echo "   - 시스템 환경설정 > 보안 및 개인정보보호에서 허용"

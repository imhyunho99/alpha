#!/usr/bin/env bash
# macOS .dmg 인스톨러 생성 스크립트.
# 1) PyInstaller로 AlphaServer + AlphaClient.app 빌드
# 2) create-dmg로 두 바이너리를 한 .dmg에 묶음
#
# 사용:
#   ./scripts/build_macos_installer.sh                 # 자동 버전 (git describe)
#   VERSION=3.1.0 ./scripts/build_macos_installer.sh   # 명시 버전
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="${VERSION:-$(git describe --tags --abbrev=0 2>/dev/null || echo "dev")}"
VERSION="${VERSION#v}"  # v3.1.0 → 3.1.0
DMG_NAME="Alpha-${VERSION}-macOS.dmg"
STAGING="dist/dmg-staging"

echo "════════════════════════════════════════"
echo " Alpha macOS .dmg 빌더 (v${VERSION})"
echo "════════════════════════════════════════"

# 가상환경 활성화 (있으면)
if [ -f venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# 1) PyInstaller 빌드
echo "▶ AlphaServer 빌드…"
rm -rf build/AlphaServer dist/AlphaServer
pyinstaller --noconfirm AlphaServer.spec

echo "▶ AlphaClient.app 빌드…"
rm -rf build/AlphaClient dist/AlphaClient dist/AlphaClient.app
pyinstaller --noconfirm AlphaClient.spec

# 2) DMG 스테이징 디렉터리 구성
echo "▶ DMG 스테이징 준비…"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R dist/AlphaClient.app "$STAGING/"
cp -R dist/AlphaServer "$STAGING/AlphaServer"
# README 한 장
cat > "$STAGING/README.txt" <<'EOF'
Alpha v3.1 — AI 기반 자동 매매 시스템
=====================================

설치 방법:
1) AlphaClient.app 을 Applications 폴더로 드래그
2) 처음 실행 시: AlphaClient.app을 더블클릭 → 첫 admin 계정 생성

서버를 별도로 실행하시려면:
- AlphaServer 폴더를 원하는 위치로 복사
- 터미널에서: ./AlphaServer/AlphaServer

문제 발생 시:
- README.md / QUICKSTART_v3.1.md 참조
- GitHub: https://github.com/imhyunho99/alpha
EOF

# 3) create-dmg 실행
echo "▶ DMG 생성…"
rm -f "dist/$DMG_NAME"
create-dmg \
  --volname "Alpha v${VERSION}" \
  --window-pos 200 120 \
  --window-size 700 460 \
  --icon-size 96 \
  --icon "AlphaClient.app" 175 200 \
  --icon "AlphaServer" 525 200 \
  --icon "README.txt" 350 380 \
  --hide-extension "AlphaClient.app" \
  --app-drop-link 350 200 \
  "dist/$DMG_NAME" \
  "$STAGING/" || {
    echo "⚠️ create-dmg 실패 → 단순 ZIP으로 폴백"
    (cd dist && zip -qr "Alpha-${VERSION}-macOS.zip" AlphaServer AlphaClient.app)
    echo "Fallback: dist/Alpha-${VERSION}-macOS.zip"
    exit 0
  }

rm -rf "$STAGING"
echo "✅ 완료: dist/$DMG_NAME"
ls -lh "dist/$DMG_NAME"

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

# SKIP_BUILD=1 → PyInstaller 단계 건너뛰고 dist/AlphaServer + dist/AlphaClient.app 재사용.
# DMG 패키징만 빠르게 반복하고 싶을 때.
SKIP_BUILD="${SKIP_BUILD:-0}"

echo "════════════════════════════════════════"
echo " Alpha macOS .dmg 빌더 (v${VERSION})"
echo "════════════════════════════════════════"

# 이전 실행에서 마운트가 남아있으면 create-dmg가 실패하므로 미리 정리
detach_stale_volumes() {
  hdiutil info 2>/dev/null | awk '
    /^image-path/ { path=$0 }
    /^\/dev\/disk/ {
      if (path ~ /Alpha-.*macOS\.dmg/ || path ~ /rw\..*Alpha/) print $1
    }
  ' | while read -r dev; do
    [ -n "$dev" ] && hdiutil detach "$dev" -force 2>/dev/null || true
  done
  rm -f dist/rw.*.dmg 2>/dev/null || true
}
detach_stale_volumes

# 가상환경 활성화 (있으면)
if [ -f venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# 1) PyInstaller 빌드
if [ "$SKIP_BUILD" = "1" ] && [ -d dist/AlphaServer ] && [ -d dist/AlphaClient.app ]; then
  echo "▶ SKIP_BUILD=1 → 빌드 단계 건너뜀 (기존 dist/ 재사용)"
else
  echo "▶ AlphaServer 빌드…"
  rm -rf build/AlphaServer dist/AlphaServer
  pyinstaller --noconfirm AlphaServer.spec

  echo "▶ AlphaClient.app 빌드…"
  rm -rf build/AlphaClient dist/AlphaClient dist/AlphaClient.app
  pyinstaller --noconfirm AlphaClient.spec
fi

# 2) DMG 스테이징 디렉터리 구성
#    AlphaServer는 AlphaClient.app/Contents/MacOS/AlphaServer/ 안에 임베드되어
#    있으므로 별도로 복사하지 않는다. 사용자는 .app만 Applications 로 끌어가면 됨.
echo "▶ DMG 스테이징 준비…"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R dist/AlphaClient.app "$STAGING/"

# 임베드된 AlphaServer 바이너리에 실행 권한 보장.
# PyInstaller는 macOS .app에서 datas를 Contents/Frameworks 와 Contents/Resources 양쪽에 둔다.
EMBEDDED_FOUND=""
for cand in \
  "$STAGING/AlphaClient.app/Contents/Frameworks/AlphaServer/AlphaServer" \
  "$STAGING/AlphaClient.app/Contents/Resources/AlphaServer/AlphaServer" \
  "$STAGING/AlphaClient.app/Contents/MacOS/AlphaServer/AlphaServer"; do
  if [ -f "$cand" ]; then
    chmod +x "$cand"
    echo "  • 임베드 서버 확인: $cand"
    EMBEDDED_FOUND="$cand"
  fi
done
if [ -z "$EMBEDDED_FOUND" ]; then
  echo "❌ AlphaServer가 .app 안에 임베드되지 않았습니다. AlphaClient.spec 확인 필요." >&2
  exit 1
fi

# README 한 장
cat > "$STAGING/README.txt" <<'EOF'
Alpha v3.1 — AI 기반 자동 매매 시스템
=====================================

설치 방법:
1) AlphaClient.app 을 Applications 폴더로 드래그
2) 더블클릭하면 끝 — AlphaServer는 .app 안에 함께 들어 있어 자동으로 기동됩니다.
3) 첫 실행 시 admin 계정 비밀번호를 직접 설정하라는 다이얼로그가 뜹니다.

문제 발생 시:
- README.md / QUICKSTART_v3.1.md 참조
- GitHub: https://github.com/imhyunho99/alpha
EOF

# 3) create-dmg 실행 — AlphaClient.app + Applications 드롭링크만 노출.
#
#    create-dmg는 macOS Spotlight/.DS_Store 경합으로 hdiutil unmount가 종종
#    실패하므로 detach 후 최대 3회 재시도한다.
echo "▶ DMG 생성…"
rm -f "dist/$DMG_NAME"

create_dmg_attempt() {
  create-dmg \
    --volname "Alpha v${VERSION}" \
    --window-pos 200 120 \
    --window-size 600 380 \
    --icon-size 96 \
    --icon "AlphaClient.app" 150 180 \
    --icon "README.txt" 300 320 \
    --hide-extension "AlphaClient.app" \
    --app-drop-link 450 180 \
    --no-internet-enable \
    "dist/$DMG_NAME" \
    "$STAGING/"
}

DMG_OK=0
for attempt in 1 2 3; do
  if create_dmg_attempt; then
    DMG_OK=1
    break
  fi
  echo "⚠️ create-dmg 시도 $attempt 실패 → 마운트 정리 후 재시도"
  detach_stale_volumes
  sleep 2
done

if [ "$DMG_OK" = "0" ]; then
  echo "❌ create-dmg 3회 실패 → ZIP 폴백 생성"
  (cd dist && zip -qr "Alpha-${VERSION}-macOS.zip" AlphaClient.app)
  echo "Fallback: dist/Alpha-${VERSION}-macOS.zip"
  exit 1
fi

rm -rf "$STAGING"
echo "✅ 완료: dist/$DMG_NAME"
ls -lh "dist/$DMG_NAME"

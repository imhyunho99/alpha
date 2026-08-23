#!/usr/bin/env bash
#
# AlphaServer 를 맥에 상주시킨다. 자동 운용 루프는 서버가 살아 있어야 돈다.
#
# launchd 는 로그인할 때 서버를 띄우고, 죽으면 다시 띄운다. 한 달짜리 관찰에는
# 앱을 열어두는 것보다 이쪽이 낫다 — 앱을 실수로 끄면 그날치 데이터가 비니까.
#
#   설치:  ./scripts/install_launchd.sh
#   해제:  ./scripts/install_launchd.sh --uninstall
#   상태:  launchctl list | grep alpha
#   로그:  tail -f ~/Library/Logs/AlphaServer.log
#
set -euo pipefail

LABEL="com.imhyunho99.alphaserver"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO/venv/bin/python"

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "해제 완료. 서버가 더 이상 자동 기동되지 않습니다."
    exit 0
fi

if [[ ! -x "$VENV_PY" ]]; then
    echo "오류: $VENV_PY 가 없습니다. 먼저 venv 를 만드세요." >&2
    exit 1
fi

# JWT 서명 키는 재시작해도 같아야 한다. 매번 새로 만들면 로그인이 풀린다.
SECRET_FILE="$HOME/AlphaModels/.jwt_secret"
mkdir -p "$(dirname "$SECRET_FILE")"
if [[ ! -s "$SECRET_FILE" ]]; then
    "$VENV_PY" -c "import secrets; print(secrets.token_urlsafe(48))" > "$SECRET_FILE"
    chmod 600 "$SECRET_FILE"
    echo "JWT 서명 키를 새로 만들었습니다: $SECRET_FILE"
fi
JWT_SECRET="$(cat "$SECRET_FILE")"

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PY}</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>alpha_server.main:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8000</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>ALPHA_JWT_SECRET</key>
        <string>${JWT_SECRET}</string>
        <key>PYTHONPATH</key>
        <string>${REPO}</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <!-- 죽자마자 다시 띄우면 크래시 루프가 CPU를 먹는다 -->
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <!-- yfinance 가 타임존 캐시 SQLite 핸들을 누수시킨다. 실측: 907종목을 받은 뒤
         tkr-tz.db 를 226개 열어둔 채로 fd 가 고갈됐고(기본 한도 256), 서버가
         [Errno 24] Too many open files 로 HTTP 연결조차 못 받게 됐다.
         코드 쪽에서도 막지만 한도 자체를 넉넉히 둔다. -->
    <key>SoftResourceLimits</key>
    <dict>
        <key>NumberOfFiles</key>
        <integer>8192</integer>
    </dict>
    <key>HardResourceLimits</key>
    <dict>
        <key>NumberOfFiles</key>
        <integer>16384</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/AlphaServer.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/AlphaServer.error.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "등록 완료: ${LABEL}"
echo "  로그:   tail -f ${LOG_DIR}/AlphaServer.log"
echo "  상태:   launchctl list | grep alpha"
echo "  해제:   $0 --uninstall"
echo
echo "주의: 맥이 잠자기에 들어가면 루프도 멈춥니다. 한 달 내내 돌리려면"
echo "      시스템 설정 → 배터리/전원 어댑터에서 '디스플레이가 꺼져도"
echo "      자동으로 잠자지 않음'을 켜세요."

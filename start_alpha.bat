@echo off
REM Alpha 통합 실행 스크립트 (Windows)

echo 🚀 Alpha AI 투자 분석 시스템 시작
echo.

cd /d "%~dp0"

REM 기존 서버 프로세스 종료
echo 기존 서버 확인 중...
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do (
    echo 기존 서버 종료 중...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 1 /nobreak >nul
)

REM 가상환경 활성화
call venv\Scripts\activate.bat

REM 서버 백그라운드 실행
echo 서버 시작 중...
start /B uvicorn alpha_server.main:app --log-level warning >nul 2>&1

REM 서버 시작 대기
timeout /t 3 /nobreak >nul

REM 서버 상태 확인
curl -s http://127.0.0.1:8000/ >nul 2>&1
if %errorlevel% equ 0 (
    echo ✅ 서버 시작 완료
    echo.
    echo GUI 시작 중...
    python -m alpha.main --gui
    
    REM GUI 종료 시 서버도 종료
    echo.
    echo 서버 종료 중...
    for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
    echo ✅ 종료 완료
) else (
    echo ❌ 서버 시작 실패
    for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
    exit /b 1
)

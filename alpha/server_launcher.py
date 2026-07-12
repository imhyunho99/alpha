"""GUI 시작 시 AlphaServer 바이너리(혹은 dev 모드의 uvicorn)를 자동으로 띄운다.

찾는 순서:
  1) 이미 8000번 포트에 서버가 떠 있으면 그대로 사용
  2) PyInstaller로 빌드된 환경: AlphaServer 바이너리 탐색
     - 클라이언트 실행파일 바로 옆에 임베드된 AlphaServer/AlphaServer
       (.app 번들이면 Contents/MacOS/AlphaServer/AlphaServer)
     - PyInstaller onefile _MEIPASS 안의 AlphaServer/AlphaServer
     - 클라이언트 .app 형제 디렉터리 (AlphaServer/)
     - /Applications/AlphaServer/AlphaServer
     - ~/Applications/AlphaServer/AlphaServer
     - PATH 상의 'AlphaServer'
  3) dev 모드 (frozen 아님): `python -m uvicorn alpha_server.main:app` 스레드 실행
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests

SERVER_URL = "http://127.0.0.1:8000"
HEALTH_URL = f"{SERVER_URL}/health"
STARTUP_TIMEOUT_SEC = 60


def is_server_up() -> bool:
    try:
        r = requests.get(HEALTH_URL, timeout=1)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _candidate_server_paths() -> list[Path]:
    """AlphaServer 바이너리가 있을 만한 경로들."""
    candidates: list[Path] = []

    # 클라이언트 .app 옆 (DMG 설치 후 같은 디렉터리에 둔 경우)
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)

        # 1) 임베드된 위치 — AlphaClient.spec이 dist/AlphaServer/를 데이터로
        #    포함시키면 PyInstaller가 풀어놓는 위치들.
        #
        #    macOS .app 번들 (BUNDLE() 결과):
        #      <...>/AlphaClient.app/Contents/MacOS/AlphaClient (실행파일)
        #      <...>/AlphaClient.app/Contents/Frameworks/AlphaServer/AlphaServer
        #      <...>/AlphaClient.app/Contents/Resources/AlphaServer/AlphaServer
        #    PyInstaller는 둘 다 만들고 보통 hardlink/symlink로 묶지만, 안전하게
        #    실제 실행파일이 있는 쪽을 우선한다 (Frameworks가 dyld 친화적).
        #
        #    Windows / Linux onedir:
        #      <...>/AlphaClient/AlphaClient.exe
        #      <...>/AlphaClient/AlphaServer/AlphaServer.exe
        if executable.parent.name == "MacOS" and executable.parent.parent.name == "Contents":
            contents_dir = executable.parent.parent
            candidates.append(contents_dir / "Frameworks" / "AlphaServer" / "AlphaServer")
            candidates.append(contents_dir / "Resources" / "AlphaServer" / "AlphaServer")
        # 같은 디렉터리 (Windows/Linux onedir, 또는 macOS 비-bundle 빌드)
        candidates.append(executable.parent / "AlphaServer" / "AlphaServer")
        candidates.append(executable.parent / "AlphaServer" / "AlphaServer.exe")

        # 2) PyInstaller onefile은 _MEIPASS에 datas를 풀어놓는다.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "AlphaServer" / "AlphaServer")
            candidates.append(Path(meipass) / "AlphaServer" / "AlphaServer.exe")

        # 3) 형제 디렉터리 폴백 — 사용자가 AlphaServer 폴더를 .app 옆에 둔 경우.
        #    macOS .app 구조: /Applications/AlphaClient.app/Contents/MacOS/AlphaClient
        #    → /Applications/AlphaServer/AlphaServer
        for ancestor in [executable.parent, executable.parent.parent, executable.parent.parent.parent]:
            candidates.append(ancestor.parent / "AlphaServer" / "AlphaServer")
            candidates.append(ancestor.parent / "AlphaServer.exe")

    # 시스템 표준 위치
    candidates.extend([
        Path("/Applications/AlphaServer/AlphaServer"),
        Path.home() / "Applications" / "AlphaServer" / "AlphaServer",
        Path("C:/Program Files/Alpha/AlphaServer/AlphaServer.exe"),
        Path.home() / "AlphaServer" / "AlphaServer",
    ])

    # PATH 상의 binary
    on_path = shutil.which("AlphaServer") or shutil.which("AlphaServer.exe")
    if on_path:
        candidates.append(Path(on_path))

    # 중복 제거 + 존재하는 것만
    seen: dict[Path, None] = {}
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved not in seen and resolved.exists() and os.access(resolved, os.X_OK):
            seen[resolved] = None
    return list(seen.keys())


def _wait_for_health(timeout: float = STARTUP_TIMEOUT_SEC) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_server_up():
            return True
        time.sleep(0.5)
    return False


def _start_dev_server_thread():
    """개발 환경: alpha_server를 직접 import해 uvicorn 스레드로 실행."""
    import threading

    def _run():
        try:
            import uvicorn
            uvicorn.run("alpha_server.main:app", host="127.0.0.1", port=8000, log_level="warning")
        except Exception as e:  # pragma: no cover
            print(f"dev 서버 실행 실패: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def ensure_server_running() -> tuple[bool, str]:
    """필요하면 서버를 띄우고 헬스체크가 통과할 때까지 기다린다.

    Returns: (success, message)
    """
    if is_server_up():
        return True, "이미 실행 중인 서버에 연결했습니다."

    # 1) PyInstaller frozen → 외부 바이너리 탐색
    if getattr(sys, "frozen", False):
        for path in _candidate_server_paths():
            try:
                # 백그라운드로 실행 (부모 종료해도 살아있게)
                kwargs: dict = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL,
                    "close_fds": True,
                }
                if os.name == "posix":
                    kwargs["start_new_session"] = True
                else:
                    kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
                subprocess.Popen([str(path)], **kwargs)
                if _wait_for_health(STARTUP_TIMEOUT_SEC):
                    return True, f"AlphaServer를 띄웠습니다: {path}"
            except OSError as e:
                print(f"서버 실행 실패 ({path}): {e}")
                continue
        return False, (
            "AlphaServer 바이너리를 찾지 못했습니다.\n"
            "다음 위치 중 하나에 설치되어야 합니다:\n"
            "  • /Applications/AlphaServer/AlphaServer (macOS)\n"
            "  • C:\\Program Files\\Alpha\\AlphaServer\\AlphaServer.exe (Windows)\n"
            "또는 .dmg/.zip 안의 AlphaServer 폴더를 AlphaClient.app 옆에 복사해주세요."
        )

    # 2) dev 모드 → uvicorn 스레드
    _start_dev_server_thread()
    if _wait_for_health(STARTUP_TIMEOUT_SEC):
        return True, "dev 서버를 띄웠습니다."
    return False, "dev 서버 부팅 시간 초과 (60초)."

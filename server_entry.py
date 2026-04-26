"""PyInstaller 진입점.

`alpha_server.main` 패키지를 모듈로 임포트해 uvicorn으로 띄운다.
PyInstaller가 alpha_server/main.py 를 직접 스크립트로 돌리면 relative import가 깨지기 때문에
이 래퍼를 통해 패키지 컨텍스트를 보장한다.
"""
from __future__ import annotations

import argparse
import os
import sys


def _ensure_package_path() -> None:
    """동결(frozen) 환경에서 alpha_server가 import 가능한지 확인."""
    if getattr(sys, "frozen", False):
        # PyInstaller _MEIPASS 안에 alpha_server가 datas로 들어가 있음
        sys.path.insert(0, sys._MEIPASS)  # type: ignore[attr-defined]


def main() -> None:
    _ensure_package_path()

    parser = argparse.ArgumentParser(prog="AlphaServer")
    parser.add_argument("--host", default=os.getenv("SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SERVER_PORT", "8000")))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    import uvicorn

    from alpha_server.main import app  # noqa: F401  — ensure import path resolves

    uvicorn.run(
        "alpha_server.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()

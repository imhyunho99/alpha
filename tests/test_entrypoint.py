"""문서에 적힌 실행 방법이 실제로 동작해야 한다."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_python_dash_m_alpha_is_runnable():
    """`python -m alpha` — CLAUDE.md 가 안내하는 개발 실행 명령."""
    proc = subprocess.run(
        [sys.executable, "-m", "alpha", "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert "cannot be directly executed" not in proc.stderr, proc.stderr
    assert "No module named" not in proc.stderr, proc.stderr


def test_main_module_exposes_main():
    from alpha.__main__ import main

    assert callable(main)

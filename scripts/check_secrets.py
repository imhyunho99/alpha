"""커밋 직전 검사: 이 Mac 에 등록된 실제 API 키·토큰이 커밋에 들어가면 막는다.

키 값은 금고(~/AlphaModels/credentials.vault)에서 실행할 때만 읽어 대조한다. 이 파일과 훅에는
키가 들어 있지 않다. 출력에도 키 값을 찍지 않는다.

    python scripts/check_secrets.py            # 스테이징된 변경 검사 (pre-commit 훅이 부른다)
    python scripts/check_secrets.py --all      # 작업 트리 전체 + 커밋 이력 검사
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 저장소에 절대 들어오면 안 되는 파일 이름
FORBIDDEN_NAMES = re.compile(r"(credentials\.vault|\.vault_key|\.client_token|\.admin_password)$")
# 실제 값이 아니라 모양으로 잡는 것 (Anthropic 키 등)
PATTERNS = [re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")]


def registered_secrets() -> list[str]:
    try:
        from alpha_server import credentials
    except Exception:
        return []
    out = []
    try:
        vault = credentials._load_vault()
    except Exception:
        return []
    for user, brokers in (vault or {}).items():
        for broker in (brokers or {}):
            values = credentials.get_credentials(user, broker) or {}
            for k, v in values.items():
                if isinstance(v, str) and len(v) >= 12 and k not in ("base_url", "account_product_code"):
                    out.append(v)
    token = os.path.expanduser("~/AlphaModels/.client_token")
    if os.path.exists(token):
        out.append(open(token, encoding="utf-8").read().strip())
    return [s for s in out if s]


def staged() -> tuple[list[str], str]:
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                           capture_output=True, text=True, cwd=ROOT).stdout.split()
    diff = subprocess.run(["git", "diff", "--cached", "-U0"], capture_output=True, text=True, cwd=ROOT).stdout
    return names, diff


def main() -> int:
    secrets = registered_secrets()
    problems = []
    if "--all" in sys.argv:
        names = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT).stdout.split()
        for s in secrets:
            if subprocess.run(["git", "grep", "-q", "-F", s], cwd=ROOT).returncode == 0:
                problems.append("작업 트리에 등록된 키 값이 있습니다")
            if subprocess.run(["git", "log", "--all", "-S", s, "--oneline"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip():
                problems.append("커밋 이력에 등록된 키 값이 있습니다")
        text = ""
    else:
        names, text = staged()
    for n in names:
        if FORBIDDEN_NAMES.search(n):
            problems.append(f"비밀 파일을 커밋하려 합니다: {n}")
    added = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    for s in secrets:
        if s in added:
            problems.append("등록된 실제 API 키/토큰 값이 변경 내용에 있습니다 (값은 표시하지 않음)")
    for p in PATTERNS:
        if p.search(added):
            problems.append("API 키 모양의 문자열이 변경 내용에 있습니다")
    if problems:
        print("🚫 커밋 중단 — 비밀 정보 검사 실패:")
        for msg in dict.fromkeys(problems):
            print("  -", msg)
        return 1
    print(f"✅ 비밀 정보 검사 통과 (대조한 등록 키 {len(secrets)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

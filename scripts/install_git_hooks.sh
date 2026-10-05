#!/bin/sh
# 저장소의 git 훅을 설치한다 (pre-commit: 실제 API 키 커밋 차단).
ROOT="$(git rev-parse --show-toplevel)"
install -m 755 "$ROOT/scripts/hooks/pre-commit" "$ROOT/.git/hooks/pre-commit"
echo "✅ pre-commit 훅 설치됨"

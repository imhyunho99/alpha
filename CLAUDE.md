# Alpha — Working notes for Claude

> 이 파일은 Claude Code 세션마다 자동으로 컨텍스트에 들어간다.
> 사용자가 PM/기획자 역할이고 Claude가 엔지니어링 전체를 책임진다.
> 한국어로 간결하게 응답한다.

## 1줄 요약

AI 기반 투자 분석 + 자연어 전략 자동매매 시스템. PySide6 GUI(`alpha/`) + FastAPI 백엔드(`alpha_server/`) + 다중 거래소 어댑터(Mock/Alpaca/Upbit/Binance/KIS) + JWT 인증 + 위험 관리 + 감사 로그.

## 디렉터리

- `alpha/` — GUI 클라이언트 (PySide6). 진입점 `main.py`. 핵심: `core.py`(서버 통신), `gui.py`, `strategy_widgets.py`, `server_launcher.py`(서버 자동기동).
- `alpha_server/` — FastAPI 백엔드. 진입점 `main.py`(uvicorn). 모듈: `auth/`, `brokers/`, `strategies/`, `risk_manager.py`, `audit_log.py`, `rate_limit.py`, `credentials.py`(Fernet 암호화 거래소 키 저장).
- `scripts/` — `build_macos_installer.sh`, 기타 운영 스크립트.
- `tests/test_unit.py` — 단위 테스트 (pytest). 테스트는 항상 `tmp_path`로 격리.
- `AlphaClient.spec`, `AlphaServer.spec` — PyInstaller 스펙.
- `.github/workflows/{ci,release}.yml` — CI(테스트+도커빌드), 릴리즈(.dmg/.exe 동시 빌드).

## 자주 쓰는 명령

```bash
# 가상환경 (로컬은 Python 3.14, CI는 3.11)
source venv/bin/activate

# 단위 테스트 — JWT 환경변수 필수 (없으면 auth 테스트 실패)
ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_unit.py -q

# 서버 dev 실행
ALPHA_DEFAULT_ADMIN_PASSWORD=ChangeThisStrong! \
ALPHA_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
python -m uvicorn alpha_server.main:app --host 127.0.0.1 --port 8000

# GUI dev 실행
python -m alpha

# macOS 인스톨러 (.dmg)
VERSION=3.1.3 ./scripts/build_macos_installer.sh
# → dist/Alpha-3.1.3-macOS.dmg

# Windows 인스톨러는 GitHub Actions에서만 빌드 (PyInstaller 크로스 컴파일 불가)
# tag v*.*.* push → release.yml이 windows-latest + macos-latest 병렬 빌드
```

## 빌드 순서 (중요)

`AlphaClient.spec`은 `dist/AlphaServer/`를 데이터로 임베드한다.
**반드시 AlphaServer를 먼저 빌드하고 AlphaClient를 나중에 빌드해야 한다.**

```bash
pyinstaller --noconfirm AlphaServer.spec   # → dist/AlphaServer/
pyinstaller --noconfirm AlphaClient.spec   # → dist/AlphaClient.app (서버 임베드)
```

`build_macos_installer.sh`와 `release.yml`은 이미 이 순서로 되어 있음.
순서가 깨지면 `AlphaClient.spec`이 SystemExit으로 빠르게 실패한다.

## 서버 자동 기동 (`alpha/server_launcher.py`)

GUI 시작 시 `ensure_server_running()`이 다음 순서로 AlphaServer를 찾아 띄운다:
1. 8000 포트에 이미 서버가 떠 있으면 → 그대로 사용
2. frozen(.app/.exe) 환경:
   - `<executable_dir>/AlphaServer/AlphaServer` (임베드 — **사용자가 .app만 끌어오면 동작하는 핵심**)
   - `_MEIPASS/AlphaServer/AlphaServer` (onefile 모드)
   - 형제 디렉터리, `/Applications/AlphaServer/`, `~/Applications/AlphaServer/`, PATH 폴백
3. dev 모드 → uvicorn 스레드로 직접 실행

UX 원칙: **사용자는 AlphaClient.app 더블클릭만 하면 끝나야 한다.** 서버를 별도 복사하라고 안내하지 않는다.

## 환경 변수 (서버)

| 변수 | 용도 | 비고 |
|---|---|---|
| `ALPHA_JWT_SECRET` | JWT 서명 키 | 없으면 자동 생성, 테스트는 명시 권장 |
| `ALPHA_DEFAULT_ADMIN_PASSWORD` | 첫 admin 부트스트랩 | 8자 이상 |
| `ALPHA_BROKER` | `mock` \| `alpaca` | 기본 mock |
| `ALPACA_API_KEY/SECRET` | Alpaca 키 | 실거래 시 |
| `ALPHA_RISK_*` | 위험 한도 | 포지션 cap, 일일 한도 등 |

거래소별 키는 GUI(`계정 → 거래소 API 키 관리…`)에서 등록 — Fernet 암호화 후 `~/AlphaModels/credentials.enc`에 저장.

## 매매 안전 장치 (수정 시 절대 우회 금지)

자동 매매는 다음 게이트를 모두 통과해야 실행:
1. JWT 인증된 사용자
2. `risk_manager` (포지션 cap 10%, 일일 매수 10건, 일일 손실 5%)
3. 전략별 쿨다운 (기본 1시간)
4. stop-loss / take-profit
5. 감사 로그 (해시 체인) 기록
6. 기본 `dry_run=true` — 실거래 전환은 명시적 PATCH 필요

새 매매 엔드포인트는 반드시 위 의존성을 통과시킨다. bypass 하지 않는다.

## 릴리즈 플로우

표준 사이클: 브랜치 → 구현 → 테스트 → push → PR → CI green → merge(`gh pr merge --merge --delete-branch`) → tag `vX.Y.Z` → `release.yml`이 Windows zip + macOS .dmg를 빌드하고 GitHub Release에 자동 첨부.

**UX/인스톨러를 바꾼 PR은 머지 전에 로컬에서 `.dmg` 빌드해서 사용자에게 E2E 테스트 받는다.** 사용자가 OK 하면 PR/태그 진행.

릴리즈 사전 승인된 작업: PR 머지, tag/release 발행, .dmg/.exe 첨부.
사전 승인 아닌 작업(매번 확인 필요): force push, 공개된 tag/release 삭제, 유료 외부 서비스 연결, `imhyunho99/alpha` 외 저장소 push.

## 작업 시 체크리스트

- [ ] 새 기능: 기존 `auth_required` / `rate_limit` / `audit_log` / `risk_manager` 의존성에 훅킹 (bypass 금지)
- [ ] 새 거래소 어댑터: `alpha_server/brokers/base.py` 인터페이스 구현 + `__init__.py` 등록
- [ ] 새 자연어 패턴: `alpha_server/strategies/nl_parser.py` 룰 + Anthropic 폴백 양쪽 케어
- [ ] 빌드/번들 변경: 로컬 `.dmg` 빌드 → 사용자 E2E → 머지
- [ ] 테스트: `tests/test_unit.py`에 추가, `tmp_path` 사용해 HOME 격리
- [ ] CI: `python 3.11`로 통과해야 함 (로컬 3.14와 호환성 주의 — `from __future__ import annotations` 등)

## 유저 인터페이스 마인드셋

사용자는 PM이다. 기능을 설명하면 Claude가 처음부터 끝까지(코드 → 테스트 → 빌드 → PR → 머지 → 릴리즈) 가져간다. UX 결함이 의심되면 묻기 전에 코드/번들을 직접 확인해서 사실관계 먼저 확정한다. "이거 동작해?" 같은 질문엔 메모리 인용이 아니라 현재 코드/빌드 결과로 답한다.

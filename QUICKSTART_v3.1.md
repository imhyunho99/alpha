# Alpha v3.1 로컬 테스트 가이드

> 자연어로 매매 전략을 만들고 실거래까지 — 이 가이드대로 따라하시면 1분 안에 동작합니다.

## 0. 사전 요구사항
- Python 3.10+ (또는 빌드된 바이너리만 있으면 OK)
- 인터넷 연결 (yfinance 시세 + 거래소 API용)
- (선택) Anthropic API 키 — 자연어 파싱용. 없으면 룰 기반 폴백 파서 동작

## 1. 빠른 실행 (개발 모드)

```bash
cd /Users/nahyeonho/pythonWorkspace/Alpha
source venv/bin/activate
pip install -r requirements.txt   # 첫 1회

# 첫 admin 계정 비밀번호 지정 (이미 사용자가 있으면 무시됨)
export ALPHA_DEFAULT_ADMIN_PASSWORD="ChangeThisStrong!"
# JWT 서명 키 (선택, 없으면 자동 생성)
export ALPHA_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

# 서버 실행
python -m uvicorn alpha_server.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 GUI 클라이언트:
```bash
source venv/bin/activate
python -m alpha
```

## 2. 빠른 실행 (빌드된 바이너리)

```bash
# 서버
./dist/AlphaServer/AlphaServer

# 클라이언트 (별도 터미널)
open ./dist/AlphaClient.app
# 또는
./dist/AlphaClient
```

## 3. 첫 사용 흐름

### Step 1 — 로그인
GUI 메뉴 → `계정 → 로그인`
- 사용자: `admin`
- 비밀번호: `ALPHA_DEFAULT_ADMIN_PASSWORD` 로 설정한 값

### Step 2 — API 키 등록
메뉴 → `계정 → 거래소 API 키 관리…`

| 거래소 | 필요한 정보 | 발급처 |
|--------|------------|--------|
| **alpaca** | api_key, api_secret, base_url | https://app.alpaca.markets/paper/dashboard/overview |
| **upbit** | access_key, secret_key | https://upbit.com/mypage/open_api_management |
| **binance** | api_key, api_secret | https://www.binance.com/en/my/settings/api-management |
| **kis** (한투) | app_key, app_secret, account_no, account_product_code | https://apiportal.koreainvestment.com |
| **anthropic** | api_key | https://console.anthropic.com/settings/keys |

각 거래소 키는 **AES-128(Fernet) 로 암호화되어** 서버에 저장됩니다. 응답에는 `AK*****XX` 형태로 마스킹되어 절대 평문 노출 안 됩니다.

> 💡 **Anthropic 키도 같은 다이얼로그에 등록**하세요. 이게 없으면 자연어 파서가 룰 기반(영문/한글 RSI·골든크로스 패턴 인식만 가능)으로 폴백합니다.

### Step 3 — 자연어로 전략 만들기
탭 → `💬 전략 채팅`

예시 입력:
- `AAPL RSI 30 이하면 5주 매수`
- `삼성전자 50일선이 200일선 위로 골든크로스 나면 10주 사줘`
- `BTC-USD 가격 60000 밑이고 RSI 25 이하면 0.01개 매수`
- `TSLA RSI 70 초과면 보유 100% 매도`

→ `미리보기` 버튼으로 LLM이 어떻게 파싱했는지 확인 → `전략 등록` 버튼

### Step 4 — 전략 동작 확인
등록된 전략은 **5분마다 백그라운드 워커**가 자동 평가합니다.

수동 확인:
- 표에서 전략 선택 → `선택 즉시 평가` 버튼 → 트리거 디버그 정보 + 주문 결과 표시

### Step 5 — 실거래 켜기 (⚠️ 주의)
기본은 **dry_run=true** (시뮬레이션만). 실거래로 전환:
1. 전략 채팅 탭에서 전략 선택
2. (현재 GUI엔 토글 버튼이 없으므로) curl 또는 다음 명령:
```bash
TOKEN="<로그인 후 ~/AlphaModels/.client_token 파일 내용>"
curl -X PATCH http://127.0.0.1:8000/strategies/<sid> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'
```

## 4. 안전 장치
모든 자동 매매는 다음을 통과해야 실행됩니다:
- ✅ JWT 인증된 사용자
- ✅ 위험 매니저 (포지션 cap 10%, 일일 매수 10건, 일일 손실 5%)
- ✅ 쿨다운 (전략별 기본 1시간 재발동 금지)
- ✅ stop-loss / take-profit 룰
- ✅ 감사 로그 (해시 체인 기록)

## 5. 자주 쓰는 명령

```bash
# 헬스체크
curl http://127.0.0.1:8000/health

# 등록된 거래소 확인
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/credentials

# 내 전략 목록
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/strategies

# 자연어 파싱 미리보기 (저장 X)
curl -X POST http://127.0.0.1:8000/strategies/parse \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"NVDA RSI 25 이하면 3주 매수"}'

# 감사 로그 메트릭 (admin)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics
```

## 6. 문제 해결

| 증상 | 원인/해결 |
|------|-----------|
| 로그인 실패 | `ALPHA_DEFAULT_ADMIN_PASSWORD` 환경변수가 서버 기동 시 없었거나 짧음(8자 미만) |
| 전략이 발동 안 함 | 거래소 키 미등록 / dry_run=false인데 잔고 부족 / 쿨다운 / 위험한도 도달 |
| 자연어 파싱이 이상함 | Anthropic 키 미등록 → 룰 기반 폴백. 키 등록하면 정확도 급상승 |
| 한국 종목 가격 못 가져옴 | yfinance가 .KS 접미사로 조회. KIS broker는 종목코드 6자리만 사용 |

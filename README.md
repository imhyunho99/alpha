# Alpha - AI 기반 투자 분석 시스템 (v3.0)

Alpha는 AI 기반의 투자 분석 및 추천 시스템입니다. 동적 자산 스크리닝, 머신러닝 예측, 투자 가치 스코어링을 통해 최적의 투자 기회를 제공합니다.

## 🎯 주요 기능

- **동적 자산 스크리닝**: NASDAQ 100, 암호화폐 상위 100개 자동 수집
- **AI 예측 모델**: RandomForest + XGBoost + LightGBM 앙상블, FinBERT 뉴스 분석, LSTM 딥러닝
- **투자 가치 스코어링**: 단기/중기/장기 관점별 투자 점수 산출
- **포트폴리오 분석**: 보유 자산의 성과 및 투자 가치 평가
- **GUI 클라이언트**: 직관적인 PySide6 기반 사용자 인터페이스
- **🆕 JWT 인증 / RBAC**: admin/user 역할 분리, 토큰 기반 보호
- **🆕 브로커 어댑터**: MockBroker / AlpacaBroker 플러그형 구조
- **🆕 위험 관리**: 포지션 사이징, stop-loss/take-profit, 일일 한도
- **🆕 감사 로그**: 해시 체인으로 위변조 탐지 가능한 JSONL 로그
- **🆕 Rate Limiting + 표준 에러 응답**: 토큰버킷 기반, request-id 추적
- **🆕 Docker Compose / GitHub Actions CI**: 원클릭 기동 + 자동 빌드

## 📋 시스템 요구사항

- Python 3.10 이상
- macOS, Linux, Windows
- (선택) QuestDB - 고성능 시계열 데이터베이스

## 🚀 빠른 시작

### 1. 시스템 테스트

모든 컴포넌트가 정상 작동하는지 확인:

```bash
cd /Users/nahyeonho/pythonWorkspace/Alpha
source venv/bin/activate
python tests/test_system.py
```

### 2. Alpha 실행

터미널에서 다음 명령어 실행:

```bash
./start_alpha.sh
```

- 서버가 백그라운드에서 시작됩니다.
- GUI 클라이언트가 자동으로 실행됩니다.
- GUI 종료 시 서버도 자동으로 종료됩니다.

## 📁 프로젝트 구조

```
Alpha/
├── alpha/                      # GUI 클라이언트
│   ├── main.py                # 클라이언트 진입점
│   ├── core.py                # 서버 통신 로직
│   └── gui.py                 # PySide6 GUI
│
├── alpha_server/              # 백엔드 서버
│   ├── main.py               # FastAPI 서버
│   ├── asset_screener.py     # 자산 스크리닝
│   ├── data_handler.py       # 데이터 수집/저장
│   ├── model_handler.py      # AI 모델 학습/예측
│   ├── scoring_engine.py     # 투자 가치 평가
│   ├── market_data/          # CSV 데이터 (QuestDB 미사용 시)
│   └── models/               # 학습된 AI 모델
│
├── docs/                     # 문서
│   ├── guides/              # 가이드 문서
│   └── archive/             # 이전 리포트 및 문서
│
├── scripts/                  # 유틸리티 스크립트
├── tests/                    # 시스템 테스트
├── venv/                     # 가상환경
├── requirements.txt          # 의존성 목록
├── .env                      # 환경 변수
├── start_alpha.sh           # 통합 실행 스크립트
└── run_dev.sh               # 개발 모드 실행 스크립트
```

## 🔧 데이터 저장 방식

Alpha는 두 가지 데이터 저장 방식을 지원합니다:

### 1. QuestDB (권장)

고성능 시계열 데이터베이스로 대량의 금융 데이터를 효율적으로 처리합니다.

- QuestDB가 실행 중이면 자동으로 사용
- 연결 실패 시 자동으로 CSV 모드로 전환

### 2. CSV 파일 (폴백)

QuestDB를 사용할 수 없을 때 자동으로 CSV 파일로 저장합니다.

- 데이터는 `alpha_server/market_data/` 디렉토리에 저장
- 별도 설정 불필요

## 📊 사용 방법

### GUI 사용

1. **서버 상태 확인**: "서버 상태 확인" 버튼 클릭
2. **데이터 업데이트**: "서버 데이터 업데이트 요청" 버튼 클릭 (최초 1회)
3. **모델 학습**: "서버 모델 재학습 요청" 버튼 클릭 (최초 1회)
4. **투자 추천 받기**: 
   - 투자 기간 선택 (단기/중기/장기)
   - 추천 개수 입력
   - "추천 받기" 버튼 클릭

### API 사용

서버가 실행 중일 때 다음 엔드포인트를 사용할 수 있습니다:

```bash
# 서버 상태 확인
curl http://127.0.0.1:8000/

# 데이터 업데이트 (백그라운드)
curl -X POST http://127.0.0.1:8000/update-data

# 모델 재학습 (백그라운드)
curl -X POST http://127.0.0.1:8000/update-models

# 투자 추천 받기
curl "http://127.0.0.1:8000/recommendations?horizon=medium&top_n=10"

# 포트폴리오 평가
curl -X POST http://127.0.0.1:8000/assess-portfolio \
  -H "Content-Type: application/json" \
  -d '{"holdings": [{"symbol": "AAPL", "quantity": 10, "purchase_price": 150.0}]}'
```

## 🔄 자동 업데이트

서버는 6시간마다 자동으로 데이터를 업데이트합니다.
- 서버 시작 시 자동 활성화
- 백그라운드에서 실행
- 로그에서 진행 상황 확인 가능

## 🧪 테스트

### 전체 시스템 테스트

```bash
python tests/test_system.py
```

### 개별 컴포넌트 테스트

```bash
# 자산 스크리너 테스트
python -m alpha_server.asset_screener

# 데이터 핸들러 테스트
python -m alpha_server.data_handler

# 모델 핸들러 테스트
python -m alpha_server.model_handler

# 스코어링 엔진 테스트
python -m alpha_server.scoring_engine
```

## 📈 투자 가치 점수 계산 방식

### 단기 점수 (1주 이내)
- RSI 모멘텀 (70%)
- AI 예측 결과 (30%)

### 중기 점수 (3개월 이내)
- 이동평균 추세 (80%)
- 단기 모멘텀 (20%)

### 장기 점수 (1년 이상)
- 연간 수익률 (60%)
- 변동성 (40%, 낮을수록 높은 점수)

## 🔐 환경 변수

`.env` 파일에서 다음 설정을 변경할 수 있습니다:

```bash
# QuestDB 설정
DB_HOST=127.0.0.1
DB_PORT=8812
DB_NAME=qdb
DB_USER=admin
DB_PASSWORD=quest

# API 키 (필요시)
FINANCIAL_API_KEY=YOUR_API_KEY_HERE
```

## 🐛 문제 해결

### 서버가 시작되지 않을 때

1. 가상환경 활성화 확인:
   ```bash
   source venv/bin/activate
   ```

2. 의존성 재설치:
   ```bash
   pip install -r requirements.txt
   ```

3. 포트 충돌 확인:
   ```bash
   lsof -i :8000
   ```

### 데이터를 가져올 수 없을 때

1. 인터넷 연결 확인
2. yfinance 업데이트:
   ```bash
   pip install --upgrade yfinance
   ```

### GUI가 실행되지 않을 때

1. PySide6 재설치:
   ```bash
   pip install --upgrade PySide6
   ```

2. 서버가 실행 중인지 확인

## 📚 추가 문서

- [서버 API 문서](alpha_server/README.md)
- [빌드 가이드](docs/guides/BUILD_GUIDE.md)
- [성능 추적](docs/guides/PERFORMANCE_TRACKING.md)
- [보안 체크리스트](docs/guides/SECURITY_CHECKLIST.md)

## 🎉 성공적인 실행 확인

시스템이 정상 작동하면 다음과 같은 메시지를 볼 수 있습니다:

```
🎉 모든 테스트 통과! 시스템이 정상 작동합니다.

다음 명령어로 Alpha를 시작할 수 있습니다:
  ./start_alpha.sh
```

## 📝 라이선스

이 프로젝트는 개인 사용 목적으로 제작되었습니다.

## 🤝 기여

버그 리포트나 기능 제안은 이슈로 등록해주세요.
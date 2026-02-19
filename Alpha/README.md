# Alpha v2.0

Alpha는 AI 기반 투자 추천 시스템으로, 앙상블 머신러닝 모델을 활용하여 주식 및 암호화폐의 투자 가치를 분석합니다.  
NASDAQ 100과 Top 100 암호화폐를 대상으로 기술적 지표를 분석하고, 단기/중기/장기 투자 추천을 제공합니다.

## 데모

**평균 정확도**: 71.79% (기존 55-58% 대비 +27.1% 개선)  
**예상 연간 수익률**: 19.79% (S&P 500 대비 +9.79%p)  
**분석 대상**: 206개 자산 (주식 + 암호화폐)

---

## 기술 스택

### Backend
- Python
- FastAPI
- QuestDB (시계열 데이터베이스)
- scikit-learn
- XGBoost
- LightGBM

### Frontend
- PySide6 (Qt for Python)

### AI/ML
- **Ensemble Model**: RandomForest + XGBoost + LightGBM
- **특성 엔지니어링**: 13개 기술적 지표 (SMA, RSI, MACD, Bollinger Bands 등)
- **예측 정확도**: 평균 71.79%, 상위 55개 자산 100% 정확도

### 데이터
- yfinance (Yahoo Finance API)
- QuestDB (시계열 데이터 저장)

---

## 주요 기능

### AI 투자 추천
- **동적 자산 스크리닝**: NASDAQ 100, Top 100 암호화폐 자동 수집
- **앙상블 예측**: 3개 모델의 소프트 보팅으로 안정적인 예측
- **기간별 분석**: 단기/중기/장기 투자 가치 점수 제공
- **Top 10 추천**: 투자 가치 점수 기반 상위 10개 자산 추천

### 포트폴리오 분석
- 보유 자산의 수익률 분석
- 기간별 투자 매력도 점수 비교
- 자산별 상세 기술적 지표 제공

### 자동 모델 학습
- 206개 자산에 대한 개별 모델 자동 학습
- 최신 시세 데이터 자동 업데이트
- 성능 모니터링 및 보고서 생성

---

## 프로젝트 구조

```bash
Alpha/
├── alpha_server/              # FastAPI 백엔드 서버
│   ├── main.py               # API 엔드포인트
│   ├── data_handler.py       # 데이터 수집 및 저장
│   ├── model_handler.py      # 기본 모델 학습
│   ├── ensemble_handler.py   # 앙상블 모델 (v2.0)
│   ├── lstm_handler.py       # LSTM 모델 (선택)
│   └── market_features.py    # 시장 지수 특성
├── alpha/                     # PySide6 GUI 클라이언트
│   └── main.py
├── models/                    # 학습된 모델 저장소
├── apply_improvements.py      # 전체 자산 모델 학습
├── generate_v2_report.py      # 성능 평가 스크립트
├── backtest_strategy.py       # 백테스팅 스크립트
├── requirements.txt
└── .env                       # 환경 변수 (Git 제외)
```

---

## 설치 및 실행

### 1. 환경 설정

```bash
# 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 의존성 설치
pip install -r requirements.txt
```

### 2. 환경 변수 설정

```bash
# .env.example을 복사하여 .env 생성
cp .env.example .env

# .env 파일 수정
DB_HOST=127.0.0.1
DB_PORT=8812
DB_NAME=qdb
DB_USER=admin
DB_PASSWORD=your_password_here
```

⚠️ **중요**: `.env` 파일을 절대 Git에 커밋하지 마세요!

### 3. QuestDB 실행

```bash
# QuestDB 다운로드 및 실행
# https://questdb.io/docs/get-started/binaries/
./questdb.sh start
```

### 4. 서버 실행

```bash
# FastAPI 서버 시작
uvicorn alpha_server.main:app --reload
```

서버가 `http://127.0.0.1:8000`에서 실행됩니다.

### 5. 클라이언트 실행

```bash
# 새 터미널에서
cd Alpha
source venv/bin/activate
python -m alpha.main --gui
```

### 6. 초기 데이터 및 모델 준비

GUI에서:
1. **"서버 데이터 업데이트 요청"** 클릭 (시세 데이터 다운로드)
2. **"서버 모델 재학습 요청"** 클릭 (AI 모델 학습)
3. **"Top 10 투자 추천 받기"** 사용 가능

---

## 모델 성능 (Version 2.0)

### 전체 성능
- **평균 정확도**: 71.79%
- **평균 정밀도**: 71.06%
- **평균 재현율**: 79.87%
- **평균 F1 점수**: 71.26%

### 정확도 분포
- **100% 정확도**: 55개 자산 (26.6%)
- **80% 이상**: 106개 자산 (51.2%)
- **70% 이상**: 145개 자산 (70.0%)

### 상위 성능 자산
- DASH, BNB-USD, DOGE-USD, META, GOOGL: 100% 정확도
- 대부분의 대형 주식 및 주요 암호화폐: 80% 이상

### 백테스팅 결과
- **예상 연간 수익률**: 19.79%
- **S&P 500 대비**: +9.79%p
- **승률**: 71.79%

자세한 내용은 `PERFORMANCE_REPORT_V2_DETAILED.md` 참조

---

## 개선 이력

### Version 2.0 (2026-02)
- **Phase 1**: 특성 4개 → 13개 확장 (SMA, RSI, MACD, Bollinger Bands 등)
- **Phase 2**: 앙상블 모델 도입 (RandomForest + XGBoost + LightGBM)
- **Phase 3**: LSTM 및 시장 지수 특성 통합 (선택)
- **성능**: 55-58% → 71.79% (+27.1% 개선)

### Version 1.0
- 기본 RandomForest 모델
- 4개 기술적 지표
- 평균 정확도 55-58%

---

## 문서

- `MODEL_IMPROVEMENT_PLAN.md` - 모델 개선 계획
- `MODEL_IMPROVEMENT_RESULTS.md` - 개선 결과 상세
- `ENSEMBLE_VS_DEEPLEARNING.md` - 앙상블 vs 딥러닝 비교
- `BACKTEST_REPORT.md` - 백테스팅 보고서
- `SECURITY_CHECKLIST.md` - 보안 체크리스트
- `SECURITY_FIX_REPORT.md` - 보안 수정 내역

---

## 🔒 보안 주의사항

### 환경 변수 관리
- `.env` 파일에 민감한 정보 저장
- `.env.example`은 예시만 포함
- Git에 `.env` 절대 커밋 금지

### 제외되는 파일
- `*.joblib` (모델 파일, 수백 MB)
- `*.csv`, `*.json` (데이터 파일)
- `questdb_data/` (데이터베이스)
- `.env` (환경 변수)

자세한 내용은 `SECURITY_CHECKLIST.md` 참조

---

## API 문서

서버 실행 후 다음 URL에서 API 문서 확인:
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

---

## 라이선스

MIT License

---

## 기여

이슈 및 풀 리퀘스트 환영합니다.

---

## 문의

프로젝트 관련 문의사항은 이슈로 등록해주세요.

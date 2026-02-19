# Alpha 프로젝트 QuestDB 통합 완료

## 변경 사항 요약

### ✅ 완료된 작업

1. **프로젝트 통합**
   - `Alpha/` 디렉토리로 클라이언트와 서버 통합
   - 단일 가상환경 사용 (`Alpha/venv/`)
   - 통합 `requirements.txt`

2. **QuestDB 완전 통합**
   - CSV 파일 저장 제거
   - 모든 데이터를 QuestDB에 저장/조회
   - `data_handler.py`: QuestDB 전용으로 재작성
   - `model_handler.py`: QuestDB에서 데이터 로드

3. **불필요한 파일 정리**
   - ❌ `alpha_server/market_data/` 디렉토리 삭제
   - ❌ `migrate_to_questdb.py` 삭제
   - ❌ `benchmark_questdb.py` 삭제
   - ✅ `.gitignore` 업데이트

### 📁 최종 프로젝트 구조

```
Alpha/
├── alpha/                      # GUI 클라이언트
│   ├── __init__.py
│   ├── main.py
│   ├── core.py                # 서버 통신
│   └── gui.py                 # PySide6 GUI
│
├── alpha_server/              # 백엔드 서버 (QuestDB)
│   ├── __init__.py
│   ├── main.py               # FastAPI 서버
│   ├── asset_screener.py     # 자산 스크리닝
│   ├── data_handler.py       # QuestDB 데이터 처리
│   ├── model_handler.py      # AI 모델 학습
│   ├── scoring_engine.py     # 투자 가치 평가
│   ├── models/               # 학습된 모델 (200+ .joblib)
│   ├── README.md
│   └── requirements.txt
│
├── venv/                     # 통합 가상환경
├── test_questdb.py          # QuestDB 통합 테스트
├── requirements.txt         # 통합 의존성
├── .gitignore
├── .env
├── Dockerfile
└── README.md
```

### 🗄️ 데이터 흐름

```
yfinance API
    ↓
download_ticker_data()
    ↓
QuestDB (stock_prices 테이블)
    ↓
load_data()
    ↓
model_handler / scoring_engine
    ↓
FastAPI 엔드포인트
    ↓
GUI 클라이언트
```

### 🔧 QuestDB 설정

**테이블 스키마:**
```sql
CREATE TABLE stock_prices (
    timestamp TIMESTAMP,
    ticker SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG
) timestamp(timestamp) PARTITION BY DAY WAL;
```

**연결 정보:**
- Host: 127.0.0.1
- Port: 8812 (PostgreSQL wire protocol)
- Database: qdb
- User: admin
- Password: quest

### ✅ 테스트 결과

```
✓ 데이터 다운로드 (251개 행)
✓ QuestDB 저장
✓ QuestDB 읽기 (502개 행)
✓ AI 모델 학습 (정확도 74%)
✓ AI 예측 (UP)
✓ 투자 가치 점수 계산
```

### 🚀 실행 방법

1. **QuestDB 시작**
   ```bash
   brew services start questdb
   ```

2. **테이블 생성** (최초 1회)
   ```bash
   curl -G "http://localhost:9000/exec" --data-urlencode "query=CREATE TABLE IF NOT EXISTS stock_prices (timestamp TIMESTAMP, ticker SYMBOL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume LONG) timestamp(timestamp) PARTITION BY DAY WAL;"
   ```

3. **서버 실행**
   ```bash
   cd Alpha
   venv/bin/uvicorn alpha_server.main:app --reload
   ```

4. **클라이언트 실행**
   ```bash
   cd Alpha
   venv/bin/python -m alpha.main --gui
   ```

### 📊 주요 API 엔드포인트

- `GET /` - 서버 상태 확인
- `POST /update-data` - 데이터 수집 (QuestDB에 저장)
- `POST /update-models` - AI 모델 재학습
- `GET /recommendations?horizon=medium&top_n=10` - Top-N 추천
- `POST /assess-portfolio` - 포트폴리오 평가

### 🎯 다음 단계

- [ ] 여러 티커 동시 처리 성능 최적화
- [ ] QuestDB 백업 전략 수립
- [ ] 실시간 데이터 스트리밍 추가
- [ ] 웹 클라이언트 개발

---

**마이그레이션 완료일**: 2026-02-18  
**QuestDB 버전**: 9.3.2  
**Python 버전**: 3.14

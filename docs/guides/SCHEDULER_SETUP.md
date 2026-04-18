# ✅ 데이터 파이프라인 자동화 완료

## 구현 완료 사항

### 1. APScheduler 통합
- ✅ `alpha_server/scheduler.py` 생성
- ✅ FastAPI 라이프사이클에 통합
- ✅ 자동 시작/종료

### 2. 스케줄 설정

#### 📊 데이터 업데이트
- **시간**: 매일 오후 6시
- **요일**: 월~금 (주말 제외)
- **다음 실행**: 2026-02-19 18:00:00

#### 🤖 모델 재학습
- **시간**: 매주 토요일 새벽 2시
- **다음 실행**: 2026-02-21 02:00:00

## 실행 방법

### 서버 시작 (스케줄러 자동 실행)
```bash
cd Alpha
venv/bin/uvicorn alpha_server.main:app --reload
```

서버가 시작되면 스케줄러도 자동으로 시작됩니다!

### 로그 확인
```
INFO:alpha_server.scheduler:스케줄러 시작됨
INFO:apscheduler.scheduler:Added job "scheduled_data_update" to job store "default"
INFO:apscheduler.scheduler:Added job "scheduled_model_update" to job store "default"
```

## 스케줄 커스터마이징

`alpha_server/scheduler.py` 파일에서 수정:

```python
# 매일 오후 6시 → 오후 5시로 변경
CronTrigger(hour=17, minute=0, day_of_week='mon-fri')

# 매주 토요일 → 매주 일요일로 변경
CronTrigger(hour=2, minute=0, day_of_week='sun')

# 15분마다 실행
CronTrigger(minute='*/15')

# 매시간 정각
CronTrigger(minute=0)
```

## 수동 실행 (API)

스케줄과 별개로 수동 실행도 가능:

```bash
# 데이터 업데이트
curl -X POST http://localhost:8000/update-data

# 모델 재학습
curl -X POST http://localhost:8000/update-models
```

## 다른 방식 비교

자세한 내용은 `DATA_PIPELINE_GUIDE.md` 참조:
- Celery + Redis (중규모)
- Apache Airflow (대규모)
- AWS EventBridge (클라우드)
- Cron + systemd (Linux)

## 모니터링

### 작업 실행 확인
```python
from alpha_server.scheduler import scheduler

for job in scheduler.get_jobs():
    print(f"{job.id}: 다음 실행 {job.next_run_time}")
```

### 로그 파일 설정
`alpha_server/scheduler.py`에서:
```python
logging.basicConfig(
    filename='alpha_scheduler.log',
    level=logging.INFO
)
```

## 프로덕션 배포

### Docker Compose
```yaml
services:
  alpha-server:
    build: .
    ports:
      - "8000:8000"
    environment:
      - TZ=Asia/Seoul
    depends_on:
      - questdb
  
  questdb:
    image: questdb/questdb
    ports:
      - "9000:9000"
      - "8812:8812"
```

### 환경 변수
```bash
# .env
SCHEDULER_ENABLED=true
DATA_UPDATE_HOUR=18
MODEL_UPDATE_DAY=6  # 토요일
```

---

**구현 완료**: 2026-02-18  
**방식**: APScheduler  
**상태**: ✅ 테스트 완료

# 데이터 파이프라인 자동화 가이드

## 📊 추천 방식 비교

| 방식 | 난이도 | 안정성 | 확장성 | 비용 | 추천도 |
|------|--------|--------|--------|------|--------|
| **APScheduler** | ⭐ | ⭐⭐⭐ | ⭐⭐ | 무료 | ⭐⭐⭐⭐⭐ |
| Celery + Redis | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 무료 | ⭐⭐⭐⭐ |
| Airflow | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 무료 | ⭐⭐⭐ |
| AWS EventBridge | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 유료 | ⭐⭐⭐⭐ |
| Cron + systemd | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | 무료 | ⭐⭐⭐ |

---

## 1️⃣ APScheduler (✅ 추천)

### 장점
- ✅ 가장 간단한 설정
- ✅ FastAPI와 완벽 통합
- ✅ 별도 서비스 불필요
- ✅ Cron 표현식 지원
- ✅ 즉시 사용 가능

### 단점
- ❌ 서버 재시작 시 스케줄 초기화
- ❌ 분산 처리 불가
- ❌ 작업 실패 시 재시도 제한적

### 설치
```bash
pip install apscheduler
```

### 사용법
이미 구현됨! `alpha_server/scheduler.py` 참조

### 스케줄 예시
```python
# 매일 오후 6시 (미국 장 마감 후)
CronTrigger(hour=18, minute=0, day_of_week='mon-fri')

# 매주 토요일 새벽 2시
CronTrigger(hour=2, minute=0, day_of_week='sat')

# 매시간 정각
CronTrigger(minute=0)

# 15분마다
CronTrigger(minute='*/15')
```

---

## 2️⃣ Celery + Redis

### 장점
- ✅ 프로덕션 레벨 안정성
- ✅ 분산 처리 가능
- ✅ 작업 재시도, 우선순위 지원
- ✅ 모니터링 도구 (Flower)

### 단점
- ❌ Redis 설치 필요
- ❌ 설정 복잡
- ❌ 오버킬일 수 있음

### 설치
```bash
# Redis 설치
brew install redis
brew services start redis

# Python 패키지
pip install celery redis
```

### 구현 예시
```python
# celery_app.py
from celery import Celery
from celery.schedules import crontab

app = Celery('alpha', broker='redis://localhost:6379/0')

app.conf.beat_schedule = {
    'update-data-daily': {
        'task': 'tasks.update_data',
        'schedule': crontab(hour=18, minute=0, day_of_week='1-5'),
    },
    'update-models-weekly': {
        'task': 'tasks.update_models',
        'schedule': crontab(hour=2, minute=0, day_of_week=6),
    },
}

# tasks.py
from celery_app import app
from alpha_server.data_handler import update_all_data

@app.task
def update_data():
    update_all_data()
```

### 실행
```bash
# Worker 시작
celery -A celery_app worker --loglevel=info

# Beat 스케줄러 시작
celery -A celery_app beat --loglevel=info
```

---

## 3️⃣ Apache Airflow

### 장점
- ✅ 엔터프라이즈급 워크플로우 관리
- ✅ 강력한 UI 대시보드
- ✅ 복잡한 의존성 관리
- ✅ 작업 모니터링, 로깅

### 단점
- ❌ 매우 무거움 (PostgreSQL 필요)
- ❌ 학습 곡선 높음
- ❌ 소규모 프로젝트에 과함

### 설치
```bash
pip install apache-airflow
airflow db init
airflow webserver -p 8080
airflow scheduler
```

### DAG 예시
```python
# dags/alpha_pipeline.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'alpha',
    'retries': 3,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'alpha_data_pipeline',
    default_args=default_args,
    schedule_interval='0 18 * * 1-5',  # 월-금 오후 6시
    start_date=datetime(2024, 1, 1),
)

def update_data_task():
    from alpha_server.data_handler import update_all_data
    update_all_data()

update_data = PythonOperator(
    task_id='update_data',
    python_callable=update_data_task,
    dag=dag,
)
```

---

## 4️⃣ AWS EventBridge + Lambda

### 장점
- ✅ 서버리스 (관리 불필요)
- ✅ 무한 확장성
- ✅ 높은 안정성
- ✅ AWS 생태계 통합

### 단점
- ❌ AWS 계정 필요
- ❌ 비용 발생
- ❌ 로컬 개발 어려움

### 구현
```python
# lambda_function.py
import json
import boto3
from alpha_server.data_handler import update_all_data

def lambda_handler(event, context):
    try:
        update_all_data()
        return {
            'statusCode': 200,
            'body': json.dumps('Data update completed')
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
```

### EventBridge 규칙 (AWS Console)
```
Schedule: cron(0 18 ? * MON-FRI *)
Target: Lambda function (alpha-data-updater)
```

---

## 5️⃣ Cron + systemd (Linux)

### 장점
- ✅ OS 레벨 안정성
- ✅ 서버 재시작 시 자동 실행
- ✅ 추가 의존성 없음

### 단점
- ❌ Linux/macOS만 지원
- ❌ Python 환경 관리 필요
- ❌ 로깅 수동 설정

### Crontab 설정
```bash
# crontab 편집
crontab -e

# 매일 오후 6시 (월-금)
0 18 * * 1-5 /Users/nahyeonho/pythonWorkspace/Alpha/venv/bin/python -c "from alpha_server.data_handler import update_all_data; update_all_data()" >> /tmp/alpha_data.log 2>&1

# 매주 토요일 새벽 2시
0 2 * * 6 /Users/nahyeonho/pythonWorkspace/Alpha/venv/bin/python -c "from alpha_server.model_handler import update_all_models; update_all_models()" >> /tmp/alpha_models.log 2>&1
```

### systemd 서비스 (더 안정적)
```ini
# /etc/systemd/system/alpha-scheduler.service
[Unit]
Description=Alpha Data Pipeline Scheduler
After=network.target

[Service]
Type=simple
User=nahyeonho
WorkingDirectory=/Users/nahyeonho/pythonWorkspace/Alpha
ExecStart=/Users/nahyeonho/pythonWorkspace/Alpha/venv/bin/python -m alpha_server.scheduler
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable alpha-scheduler
sudo systemctl start alpha-scheduler
```

---

## 🎯 추천 구성

### 개발/소규모 (현재 상황)
```
✅ APScheduler (이미 구현됨)
```

### 중규모 (여러 작업, 재시도 필요)
```
Celery + Redis
```

### 대규모 (복잡한 워크플로우)
```
Apache Airflow
```

### 클라우드 배포
```
AWS EventBridge + Lambda
또는
APScheduler + Docker + ECS
```

---

## 📅 권장 스케줄

### 데이터 업데이트
- **미국 주식**: 월-금 오후 6시 (EST 장 마감 후)
- **암호화폐**: 매일 자정 (24시간 거래)
- **실시간 필요 시**: 15분마다

### 모델 재학습
- **주간**: 매주 토요일 새벽 2시
- **월간**: 매월 1일 새벽 2시
- **온디맨드**: API 엔드포인트로 수동 트리거

### 데이터 정리
- **오래된 데이터 삭제**: 매월 1일
- **QuestDB 최적화**: 매주 일요일

---

## 🔧 현재 구현 상태

✅ **APScheduler 통합 완료**
- `alpha_server/scheduler.py` 생성
- `alpha_server/main.py`에 통합
- FastAPI 시작 시 자동 실행

### 실행 방법
```bash
cd Alpha
venv/bin/uvicorn alpha_server.main:app --reload
```

서버 시작 시 자동으로 스케줄러가 실행됩니다!

### 로그 확인
```bash
# 서버 로그에서 스케줄러 메시지 확인
[INFO] 스케줄러 시작됨
[INFO] [2026-02-18 18:00:00] 자동 데이터 업데이트 시작
```

---

## 📊 모니터링 추천

### 로깅
```python
import logging
logging.basicConfig(
    filename='alpha_scheduler.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### 알림 (선택)
- **이메일**: smtplib
- **Slack**: slack-sdk
- **Discord**: discord-webhook
- **Telegram**: python-telegram-bot

---

**구현 완료일**: 2026-02-18  
**현재 방식**: APScheduler  
**다음 단계**: 프로덕션 배포 시 Celery 고려

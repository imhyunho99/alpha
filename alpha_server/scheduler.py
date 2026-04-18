"""
APScheduler를 사용한 자동 데이터 파이프라인
- 매일 장 마감 후 데이터 업데이트
- 주말 제외
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging

from .data_handler import update_all_data
from .model_handler import update_all_models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def scheduled_data_update():
    """매일 자동 데이터 업데이트"""
    logger.info(f"[{datetime.now()}] 자동 데이터 업데이트 시작")
    try:
        update_all_data()
        logger.info("데이터 업데이트 완료")
    except Exception as e:
        logger.error(f"데이터 업데이트 실패: {e}")

def scheduled_model_update():
    """주말에 모델 재학습"""
    logger.info(f"[{datetime.now()}] 자동 모델 재학습 시작")
    try:
        update_all_models()
        logger.info("모델 재학습 완료")
    except Exception as e:
        logger.error(f"모델 재학습 실패: {e}")

def start_scheduler():
    """스케줄러 시작"""
    # 매일 오후 6시 (미국 장 마감 후) - 월~금
    scheduler.add_job(
        scheduled_data_update,
        CronTrigger(hour=18, minute=0, day_of_week='mon-fri'),
        id='daily_data_update',
        replace_existing=True
    )
    
    # 매주 토요일 오전 2시 모델 재학습
    scheduler.add_job(
        scheduled_model_update,
        CronTrigger(hour=2, minute=0, day_of_week='sat'),
        id='weekly_model_update',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("스케줄러 시작됨")

def stop_scheduler():
    """스케줄러 종료"""
    scheduler.shutdown()
    logger.info("스케줄러 종료됨")

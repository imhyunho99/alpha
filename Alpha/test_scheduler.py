#!/usr/bin/env python3
"""스케줄러 테스트"""
import sys
sys.path.insert(0, '/Users/nahyeonho/pythonWorkspace/Alpha')

from alpha_server.scheduler import start_scheduler, stop_scheduler, scheduler
import time

print("=" * 60)
print("APScheduler 테스트")
print("=" * 60)

# 스케줄러 시작
start_scheduler()

# 등록된 작업 확인
print("\n등록된 작업:")
for job in scheduler.get_jobs():
    print(f"  - {job.id}")
    print(f"    다음 실행: {job.next_run_time}")
    print(f"    트리거: {job.trigger}")
    print()

print("스케줄러가 백그라운드에서 실행 중입니다.")
print("Ctrl+C로 종료하세요.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n\n스케줄러 종료 중...")
    stop_scheduler()
    print("종료 완료")

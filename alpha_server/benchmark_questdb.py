import time
import os
import sys

# 모듈을 올바르게 임포트하기 위해 경로 설정
sys.path.insert(0, os.getcwd())

from alpha_server.data_handler import update_all_data
from alpha_server.model_handler import update_all_models
from alpha_server.scoring_engine import calculate_scores
from alpha_server.asset_screener import get_benchmark_tickers

def benchmark_data_update(tickers):
    """(QuestDB) 지정된 티커 목록에 대한 데이터 업데이트 시간 측정"""
    print("\n--- 데이터 업데이트 성능 측정 시작 (QuestDB) ---")
    start_time = time.time()
    # update_all_data는 내부적으로 get_all_tickers를 호출하므로,
    # 벤치마크를 위해 수정된 data_handler를 사용해야 하지만,
    # 여기서는 전체 로직을 재사용하기 위해 전체 업데이트를 그냥 호출합니다.
    # get_benchmark_tickers()가 반환하는 목록만 처리하도록 로직을 수정해야 합니다.
    # data_handler의 update_all_data는 get_all_tickers를 사용하므로, 
    # 벤치마크용으로 별도 함수를 만들지 않고, asset_screener를 임시 수정하는 방식을 사용해야 하나...
    # 아니다, data_handler.py의 update_all_data를 직접 사용하지 않고, 필요한 함수를 직접 조합하자.
    from alpha_server.data_handler import download_ticker_data, save_data_to_questdb
    from alpha_server.questdb_handler import get_sender
    
    try:
        with get_sender() as sender:
            for ticker in tickers:
                data = download_ticker_data(ticker)
                save_data_to_questdb(sender, ticker, data)
    except Exception as e:
        print(f"QuestDB 벤치마크 중 오류: {e}")

    end_time = time.time()
    duration = end_time - start_time
    print(f"--- 데이터 업데이트 성능 측정 완료 (QuestDB) ---")
    print(f"총 {len(tickers)}개 자산 데이터 업데이트 시간: {duration:.2f}초")
    return duration

def benchmark_model_training(tickers):
    """(QuestDB) 지정된 티커 목록에 대한 모델 학습 시간 측정"""
    print("\n--- 모델 학습 성능 측정 시작 (QuestDB) ---")
    start_time = time.time()
    from alpha_server.model_handler import train_model
    for ticker in tickers:
        train_model(ticker)
    end_time = time.time()
    duration = end_time - start_time
    print(f"--- 모델 학습 성능 측정 완료 (QuestDB) ---")
    print(f"총 {len(tickers)}개 자산 모델 학습 시간: {duration:.2f}초")
    return duration

def benchmark_scoring(tickers):
    """(QuestDB) 지정된 티커 목록에 대한 스코어링 시간 측정"""
    print("\n--- 스코어링 성능 측정 시작 (QuestDB) ---")
    start_time = time.time()
    for ticker in tickers:
        calculate_scores(ticker)
    end_time = time.time()
    duration = end_time - start_time
    print(f"--- 스코어링 성능 측정 완료 (QuestDB) ---")
    print(f"총 {len(tickers)}개 자산 스코어링 시간: {duration:.2f}초")
    return duration

def run_scaled_benchmark_questdb():
    """QuestDB 기반 축소된 파이프라인에 대한 벤치마크 실행"""
    print("======= QuestDB 기반 시스템 성능 측정 (축소판) 시작 =======
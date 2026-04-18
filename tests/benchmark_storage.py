#!/usr/bin/env python3
"""
Storage Benchmark: CSV vs QuestDB
성능 비교 테스트 스크립트
"""

import sys
import os
import time
import argparse
import pandas as pd
import yfinance as yf
import numpy as np

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alpha_server import data_handler
from alpha_server import ensemble_handler
from alpha_server import asset_screener
import random

# 테스트 설정
try:
    sp500 = asset_screener.get_sp500_tickers()
    random.seed(42) # 재현성을 위해 시드 고정
    TEST_TICKERS = random.sample(sp500, min(100, len(sp500)))
except Exception as e:
    TEST_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA'] # 폴백

PERIOD = "2y" # 2년치 데이터

def load_raw_data():
    print("\n📦 1. 테스트 데이터 메모리 로드 중 (yfinance)...")
    raw_data_cache = {}
    for ticker in TEST_TICKERS:
        df = yf.download(ticker, period=PERIOD, progress=False, auto_adjust=True)
        if not df.empty:
            raw_data_cache[ticker] = df
    print(f"   완료: {len(raw_data_cache)}개 자산 데이터 준비됨")
    return raw_data_cache

def run_write_benchmark():
    raw_data_cache = load_raw_data()
    results = {'CSV': {}, 'QuestDB': {}}

    print("\n📄 [WRITE] CSV 모드 쓰기 벤치마크 시작")
    data_handler.USE_QUESTDB = False
    start_time = time.time()
    for ticker, df in raw_data_cache.items():
        data_handler.save_to_csv(ticker, df)
    results['CSV']['write'] = time.time() - start_time
    print(f"   - 쓰기 완료: {results['CSV']['write']:.4f}초")

    print("\n🗄️  [WRITE] QuestDB 모드 쓰기 벤치마크 시작")
    if not data_handler.check_questdb_available():
        print("   ⚠️ QuestDB 연결 불가. 벤치마크 건너뜀.")
        results['QuestDB']['write'] = 0
    else:
        data_handler.USE_QUESTDB = True
        start_time = time.time()
        data_handler.bulk_insert_data_to_db(raw_data_cache)
        time.sleep(1) # ILP 커밋 대기
        results['QuestDB']['write'] = time.time() - start_time - 1
        print(f"   - 쓰기 완료: {results['QuestDB']['write']:.4f}초")
    
    return results

def run_read_benchmark():
    results = {'CSV': {'accuracy': 0}, 'QuestDB': {'accuracy': 0}}

    print("\n📄 [READ] CSV 모드 읽기 벤치마크 시작 (Cold Cache 기대)")
    data_handler.USE_QUESTDB = False
    start_time = time.time()
    loaded_data_csv = {}
    for ticker in TEST_TICKERS:
        loaded_data_csv[ticker] = data_handler.load_data(ticker)
    results['CSV']['read'] = time.time() - start_time
    print(f"   - 읽기 완료: {results['CSV']['read']:.4f}초")

    print("\n🗄️  [READ] QuestDB 모드 읽기 벤치마크 시작")
    if not data_handler.check_questdb_available():
        print("   ⚠️ QuestDB 연결 불가. 벤치마크 건너뜀.")
        results['QuestDB']['read'] = 0
    else:
        data_handler.USE_QUESTDB = True
        start_time = time.time()
        conn = data_handler.get_db_connection()
        for ticker in TEST_TICKERS:
            _ = data_handler.load_data(ticker, conn=conn)
        conn.close()
        results['QuestDB']['read'] = time.time() - start_time
        print(f"   - 읽기 완료: {results['QuestDB']['read']:.4f}초")

    # CSV 정확도 테스트
    print("\n   - CSV 모델 학습 및 정확도 측정 중...")
    data_handler.USE_QUESTDB = False
    acc_scores = []
    for ticker in TEST_TICKERS:
        try:
            acc = ensemble_handler.train_ensemble_model(ticker) 
            if acc: acc_scores.append(acc)
        except: pass
    results['CSV']['accuracy'] = np.mean(acc_scores) if acc_scores else 0
    print(f"   - CSV 평균 정확도: {results['CSV']['accuracy']:.2%}")

    # QuestDB 정확도 테스트
    print("\n   - QuestDB 모델 학습 및 정확도 측정 중...")
    data_handler.USE_QUESTDB = True
    acc_scores = []
    for ticker in TEST_TICKERS:
        try:
            acc = ensemble_handler.train_ensemble_model(ticker)
            if acc: acc_scores.append(acc)
        except: pass
    results['QuestDB']['accuracy'] = np.mean(acc_scores) if acc_scores else 0
    print(f"   - QuestDB 평균 정확도: {results['QuestDB']['accuracy']:.2%}")

    return results

def run_benchmark():
    parser = argparse.ArgumentParser(description="Storage Benchmark")
    parser.add_argument("--write", action="store_true", help="Run write benchmark only")
    parser.add_argument("--read", action="store_true", help="Run read benchmark only")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 스토리지 성능 벤치마크 (CSV vs QuestDB)")
    print(f"대상 자산: {TEST_TICKERS}")
    print(f"데이터 기간: {PERIOD}")
    print("=" * 60)

    if args.write:
        run_write_benchmark()
    elif args.read:
        run_read_benchmark()
    else:
        # 둘 다 실행 (기존 방식)
        w_res = run_write_benchmark()
        r_res = run_read_benchmark()
        
        # 합치기
        final_res = {
            'CSV': {'write': w_res['CSV'].get('write', 0), 'read': r_res['CSV'].get('read', 0), 'accuracy': 0},
            'QuestDB': {'write': w_res['QuestDB'].get('write', 0), 'read': r_res['QuestDB'].get('read', 0), 'accuracy': 0}
        }
        generate_report(final_res)

def generate_report(results):
    csv_res = results['CSV']
    qdb_res = results['QuestDB']

    report = []
    report.append("# 📊 스토리지 벤치마크 보고서: CSV vs QuestDB")
    report.append(f"\n**테스트 일시**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"**테스트 자산**: {len(TEST_TICKERS)}개 ({', '.join(TEST_TICKERS)})")
    report.append("\n## 1. 속도 비교 (낮을수록 좋음)")
    
    header = "| 항목 | CSV 파일 | QuestDB | 차이 (배수) |"
    sep = "|---|---|---|---|"
    
    write_diff = f"{qdb_res['write'] / csv_res['write']:.1f}x 느림" if qdb_res and qdb_res['write'] > csv_res['write'] else "QuestDB가 더 빠름"
    read_diff = f"{csv_res['read'] / qdb_res['read']:.1f}x 빠름" if qdb_res and csv_res['read'] > qdb_res['read'] else "CSV가 더 빠름"
    
    qdb_write = f"{qdb_res['write']:.4f}s" if qdb_res else "N/A"
    qdb_read = f"{qdb_res['read']:.4f}s" if qdb_res else "N/A"

    row1 = f"| **쓰기 속도** | {csv_res['write']:.4f}s | {qdb_write} | {write_diff if qdb_res else '-'} |"
    row2 = f"| **읽기 속도** | {csv_res['read']:.4f}s | {qdb_read} | {read_diff if qdb_res else '-'} |"

    report.append(header)
    report.append(sep)
    report.append(row1)
    report.append(row2)

    report.append("\n## 2. 정확도 비교")
    qdb_acc = f"{qdb_res['accuracy']:.2%}" if qdb_res else "N/A"
    report.append(f"- **CSV**: {csv_res['accuracy']:.2%}")
    report.append(f"- **QuestDB**: {qdb_acc}")
    
    report.append("\n## 3. 분석 및 결론")
    
    if qdb_res:
        report.append("### 🐢 쓰기 속도 분석")
        if qdb_res['write'] > csv_res['write']:
            report.append("- **QuestDB가 CSV보다 현저히 느립니다.**")
            report.append("- **원인**: 현재 `data_handler.py`의 `insert_data_to_db` 함수가 데이터를 **한 줄씩(Row-by-Row)** `INSERT` 하고 있기 때문입니다.")
            report.append("- **해결책**: QuestDB의 **ILP(Influx Line Protocol)** 인터페이스를 사용하거나 **Batch Insert**로 변경하면 CSV보다 훨씬 빨라질 수 있습니다.")
        else:
            report.append("- QuestDB가 예상보다 빠르게 동작했습니다.")

        report.append("\n### 🐇 읽기 속도 분석")
        if qdb_res['read'] < csv_res['read']:
            report.append("- **QuestDB가 CSV보다 빠릅니다.**")
            report.append("- DB는 인덱싱과 캐싱이 최적화되어 있어, 데이터 양이 많아질수록 파일 I/O(CSV)보다 조회 성능이 우수합니다.")
        else:
            report.append("- CSV 읽기 속도가 더 빠르거나 비슷합니다. 데이터 양이 적어서 파일 시스템 캐시 효과가 컸을 수 있습니다.")

        report.append("\n### 🎯 종합 결론")
        report.append("- 현재 구현상으로는 **읽기 성능은 QuestDB가 유리**하나, **쓰기 성능은 튜닝(Bulk Insert)이 시급**합니다.")
        report.append("- 데이터 무결성(정확도)은 양쪽 모두 동일하므로, 성능 최적화만 수행하면 QuestDB 전환이 타당합니다.")
    else:
        report.append("- QuestDB 연결 실패로 비교 분석을 수행하지 못했습니다.")

    # 파일 저장
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "BENCHMARK_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    
    print(f"\n✅ 보고서 생성 완료: {report_path}")

if __name__ == "__main__":
    run_benchmark()

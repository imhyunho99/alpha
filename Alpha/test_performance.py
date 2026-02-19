#!/usr/bin/env python3
"""모델 성능 추적 테스트"""

import sys
sys.path.insert(0, '/Users/nahyeonho/pythonWorkspace/Alpha')

from alpha_server.ensemble_model import ensemble_predict
from alpha_server.performance_tracker import log_prediction, evaluate_predictions, get_model_stats
import time

def test_logging():
    """예측 로깅 테스트"""
    print("=== 예측 로깅 테스트 ===")
    
    tickers = ['AAPL', 'MSFT', 'GOOGL']
    
    for ticker in tickers:
        result = ensemble_predict(ticker)
        log_entry = log_prediction(ticker, result)
        print(f"✓ {ticker}: {result['prediction']} (신뢰도: {result['confidence']:.2f}) - 로그 기록됨")
    
    print()

def test_stats():
    """통계 확인 테스트"""
    print("=== 모델 통계 ===")
    stats = get_model_stats()
    
    print(f"총 예측 수: {stats['total_predictions']}")
    print(f"평균 신뢰도: {stats['avg_confidence']}")
    print(f"\n방법별 예측 수:")
    for method, count in stats['predictions_by_method'].items():
        print(f"  {method}: {count}")
    
    print(f"\n예측 결과 분포:")
    for outcome, count in stats['predictions_by_outcome'].items():
        print(f"  {outcome}: {count}")
    print()

def test_evaluation():
    """성능 평가 테스트"""
    print("=== 성능 평가 (7일 전 예측) ===")
    
    report = evaluate_predictions(days_ago=7)
    
    if 'error' in report or 'message' in report:
        print(f"⚠️  {report.get('error') or report.get('message')}")
        print("   (예측 로그가 쌓이면 평가 가능합니다)")
    else:
        print(f"평가 기간: {report['days_evaluated']}일")
        print(f"총 예측 수: {report['total_predictions']}")
        print(f"전체 정확도: {report['overall_accuracy']:.1%}")
        
        if report.get('high_confidence_accuracy'):
            print(f"고신뢰도 정확도: {report['high_confidence_accuracy']:.1%} ({report['high_confidence_count']}개)")
        
        print(f"\n방법별 성능:")
        for method, perf in report['method_performance'].items():
            print(f"  {method}: {perf['accuracy']:.1%} ({perf['count']}개)")
    
    print()

if __name__ == "__main__":
    print("📊 모델 성능 추적 시스템 테스트\n")
    
    test_logging()
    test_stats()
    test_evaluation()
    
    print("✅ 테스트 완료!")
    print("\n💡 팁:")
    print("  - 예측이 쌓이면 /performance API로 정확도 확인")
    print("  - /model-stats API로 실시간 통계 확인")
    print("  - predictions_log.jsonl 파일에 모든 예측 기록됨")

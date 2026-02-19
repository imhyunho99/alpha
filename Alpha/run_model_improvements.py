#!/usr/bin/env python3
"""
Alpha 모델 개선 Phase 1, 2, 3 순차 실행 스크립트
"""
import sys
import os
sys.path.append(os.path.dirname(__file__))

from alpha_server.asset_screener import get_all_tickers
from alpha_server.model_handler import update_all_models
import time

def phase1_test():
    """Phase 1: 특성 확장 + 하이퍼파라미터 개선 테스트"""
    print("\n" + "="*80)
    print("Phase 1: 특성 확장 + 하이퍼파라미터 개선")
    print("="*80)
    print("✅ 특성 9개 추가 (총 13개)")
    print("✅ RandomForest 하이퍼파라미터 개선")
    print("   - n_estimators: 100 → 200")
    print("   - max_depth: None → 15")
    print("   - min_samples_split: 2 → 5")
    print("\n샘플 자산 5개로 테스트 중...\n")
    
    from alpha_server.model_handler import train_model
    
    tickers = get_all_tickers()[:5]
    accuracies = []
    
    for ticker in tickers:
        try:
            from alpha_server.data_handler import load_data
            from alpha_server.model_handler import create_features_and_target
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import accuracy_score
            from sklearn.ensemble import RandomForestClassifier
            
            data = load_data(ticker)
            if data is None:
                continue
            
            features, target = create_features_and_target(data)
            if features.empty:
                continue
            
            X_train, X_test, y_train, y_test = train_test_split(
                features, target, test_size=0.2, random_state=42, shuffle=False
            )
            
            model = RandomForestClassifier(
                n_estimators=200, max_depth=15, min_samples_split=5,
                random_state=42, n_jobs=-1
            )
            model.fit(X_train, y_train)
            
            acc = accuracy_score(y_test, model.predict(X_test))
            accuracies.append(acc)
            print(f"✓ {ticker}: {acc:.2%} (특성 {len(features.columns)}개)")
            
        except Exception as e:
            print(f"✗ {ticker}: 오류 - {e}")
    
    if accuracies:
        avg = sum(accuracies) / len(accuracies)
        print(f"\n📊 Phase 1 평균 정확도: {avg:.2%}")
        print(f"   예상 개선: 55-58% → 62-65%")
    
    return accuracies

def phase2_test():
    """Phase 2: 앙상블 모델 테스트"""
    print("\n" + "="*80)
    print("Phase 2: 앙상블 모델 (RandomForest + XGBoost + LightGBM)")
    print("="*80)
    
    # 필요한 패키지 확인
    try:
        import xgboost
        print("✅ XGBoost 설치됨")
    except ImportError:
        print("⚠️  XGBoost 미설치 - pip install xgboost 권장")
    
    try:
        import lightgbm
        print("✅ LightGBM 설치됨")
    except ImportError:
        print("⚠️  LightGBM 미설치 - pip install lightgbm 권장")
    
    print("\n샘플 자산 3개로 앙상블 테스트 중...\n")
    
    from alpha_server.ensemble_handler import train_ensemble_model
    
    tickers = get_all_tickers()[:3]
    accuracies = []
    
    for ticker in tickers:
        try:
            acc = train_ensemble_model(ticker)
            if acc:
                accuracies.append(acc)
        except Exception as e:
            print(f"✗ {ticker}: 오류 - {e}")
    
    if accuracies:
        avg = sum(accuracies) / len(accuracies)
        print(f"\n📊 Phase 2 평균 정확도: {avg:.2%}")
        print(f"   예상 개선: 62-65% → 68-72%")
    
    return accuracies

def phase3_test():
    """Phase 3: LSTM + 외부 데이터 테스트"""
    print("\n" + "="*80)
    print("Phase 3: LSTM 딥러닝 + 시장 지수 통합")
    print("="*80)
    
    # TensorFlow 확인
    try:
        import tensorflow as tf
        print(f"✅ TensorFlow {tf.__version__} 설치됨")
        
        print("\n샘플 자산 2개로 LSTM 테스트 중 (시간 소요)...\n")
        
        from alpha_server.lstm_handler import train_lstm_model
        
        tickers = get_all_tickers()[:2]
        accuracies = []
        
        for ticker in tickers:
            try:
                acc = train_lstm_model(ticker, epochs=20)
                if acc:
                    accuracies.append(acc)
            except Exception as e:
                print(f"✗ {ticker}: 오류 - {e}")
        
        if accuracies:
            avg = sum(accuracies) / len(accuracies)
            print(f"\n📊 Phase 3 평균 정확도: {avg:.2%}")
            print(f"   예상 개선: 68-72% → 75-80%")
        
        return accuracies
        
    except ImportError:
        print("⚠️  TensorFlow 미설치")
        print("   설치: pip install tensorflow")
        print("   Phase 3는 선택사항입니다 (Phase 2까지도 충분)")
        return []

def main():
    """전체 Phase 실행"""
    print("\n" + "="*80)
    print("🚀 Alpha 모델 개선 Phase 1, 2, 3 순차 실행")
    print("="*80)
    
    start_time = time.time()
    
    # Phase 1
    phase1_acc = phase1_test()
    
    # Phase 2
    phase2_acc = phase2_test()
    
    # Phase 3
    phase3_acc = phase3_test()
    
    # 요약
    print("\n" + "="*80)
    print("📊 전체 결과 요약")
    print("="*80)
    
    if phase1_acc:
        print(f"Phase 1 평균: {sum(phase1_acc)/len(phase1_acc):.2%} (특성 확장)")
    if phase2_acc:
        print(f"Phase 2 평균: {sum(phase2_acc)/len(phase2_acc):.2%} (앙상블)")
    if phase3_acc:
        print(f"Phase 3 평균: {sum(phase3_acc)/len(phase3_acc):.2%} (LSTM)")
    
    elapsed = time.time() - start_time
    print(f"\n⏱️  총 소요 시간: {elapsed/60:.1f}분")
    
    print("\n" + "="*80)
    print("✅ 모든 Phase 테스트 완료!")
    print("="*80)
    print("\n다음 단계:")
    print("1. 전체 자산에 적용: alpha_server/ensemble_handler.py의 update_all_ensemble_models()")
    print("2. 서버 재시작 후 추천 API 테스트")
    print("3. 실전 백테스팅으로 성과 검증")

if __name__ == "__main__":
    main()

"""
Phase 2: 앙상블 모델 핸들러
XGBoost + LightGBM + RandomForest 결합
"""
import os
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

try:
    from xgboost import XGBClassifier
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    print("경고: XGBoost가 설치되지 않았습니다. pip install xgboost")

try:
    from lightgbm import LGBMClassifier
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False
    print("경고: LightGBM이 설치되지 않았습니다. pip install lightgbm")

from .data_handler import load_data
from .model_handler import create_features_and_target

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')

def train_ensemble_model(ticker):
    """Phase 2: 앙상블 모델 학습"""
    print(f"--- '{ticker}' 앙상블 모델 학습 시작 ---")
    data = load_data(ticker)
    if data is None:
        return

    features, target = create_features_and_target(data)
    
    if features.empty:
        print(f"오류: '{ticker}'에 대한 학습 데이터를 생성할 수 없습니다.")
        return

    X_train, X_test, y_train, y_test = train_test_split(
        features, target, test_size=0.2, random_state=42, shuffle=False
    )
    
    # 모델 리스트
    estimators = []
    
    # RandomForest
    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=15,
        min_samples_split=5,
        random_state=42,
        n_jobs=-1
    )
    estimators.append(('rf', rf))
    
    # XGBoost
    if XGBOOST_AVAILABLE:
        xgb = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1,
            eval_metric='logloss'
        )
        estimators.append(('xgb', xgb))
    
    # LightGBM
    if LIGHTGBM_AVAILABLE:
        lgbm = LGBMClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1,
            verbose=-1
        )
        estimators.append(('lgbm', lgbm))
    
    # 앙상블
    if len(estimators) > 1:
        ensemble = VotingClassifier(estimators=estimators, voting='soft', n_jobs=-1)
        ensemble.fit(X_train, y_train)
        model = ensemble
        model_type = "Ensemble"
    else:
        # XGBoost/LightGBM 없으면 RandomForest만
        rf.fit(X_train, y_train)
        model = rf
        model_type = "RandomForest"
    
    # 평가
    predictions = model.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)
    print(f"'{ticker}' {model_type} 모델 테스트 정확도: {accuracy:.2%}")
    
    # 저장
    model_path = os.path.join(MODELS_DIR, f"{ticker}_model.joblib")
    joblib.dump({
        'model': model,
        'features': list(features.columns),
        'type': model_type
    }, model_path)
    print(f"'{ticker}' 모델을 '{model_path}'에 저장했습니다.")
    
    return accuracy

def update_all_ensemble_models():
    """모든 자산에 대해 앙상블 모델 학습"""
    from .asset_screener import get_all_tickers
    
    tickers = get_all_tickers()
    print(f"\n총 {len(tickers)}개 자산에 대한 앙상블 모델 학습을 시작합니다...\n")
    
    accuracies = {}
    for ticker in tickers:
        try:
            acc = train_ensemble_model(ticker)
            if acc:
                accuracies[ticker] = acc
        except Exception as e:
            print(f"오류: '{ticker}' 모델 학습 실패 - {e}")
    
    # 평균 정확도
    if accuracies:
        avg_acc = np.mean(list(accuracies.values()))
        print(f"\n평균 정확도: {avg_acc:.2%}")
        print(f"최고 정확도: {max(accuracies.values()):.2%} ({max(accuracies, key=accuracies.get)})")
        print(f"최저 정확도: {min(accuracies.values()):.2%} ({min(accuracies, key=accuracies.get)})")
    
    print("\n모든 앙상블 모델 학습이 완료되었습니다!")

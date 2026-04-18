with open('alpha_server/global_model_handler.py', 'a') as f:
    f.write("""

from sklearn.ensemble import VotingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score
import xgboost as xgb
import lightgbm as lgb
from sklearn.preprocessing import OrdinalEncoder

def train_global_model(horizon_name="short", target_days=7):
    \"\"\"
    모든 종목의 데이터를 사용하여 단일 글로벌 모델을 학습합니다.
    horizon_name: "short", "mid", "long" 중 하나
    \"\"\"
    print(f"========== 글로벌 앙상블 모델 학습 시작 ({horizon_name}, {target_days}일 예측) ==========")
    tickers = get_all_tickers()
    
    # 1. 통합 데이터셋 구축
    X, y = build_global_dataset(tickers, target_days=target_days)
    
    if X.empty:
        print("학습 중단: 데이터셋을 구축하지 못했습니다.")
        return None
        
    # Ticker는 피처에서 제외 (메타데이터로 충분히 설명됨), 하지만 나중에 분석을 위해 따로 빼둠
    X_features = X.drop(columns=['Ticker'])
    
    # 2. 카테고리형 변수 인코딩 (Sector, Industry)
    # LightGBM과 XGBoost는 자체적으로 카테고리를 지원하지만, Scikit-learn의 VotingClassifier와 
    # 호환성을 위해 숫자로 인코딩 (Ordinal Encoding)
    print("카테고리 변수 인코딩 중...")
    cat_cols = ['Sector', 'Industry']
    encoder = OrdinalEncoder(handle_unknown='use_encoded_value', unknown_value=-1)
    X_features[cat_cols] = encoder.fit_transform(X_features[cat_cols])
    
    # 3. 데이터 분할 (시계열 특성을 고려하여 Shuffle=False 유지)
    # Global 모델에서는 단순 split보다 TimeSeriesSplit이 좋지만, 
    # 일단 기존 로직과 동일하게 유지하되, 모든 티커가 섞이지 않도록 주의 (날짜별 정렬 후 split 권장)
    # 여기서는 DataFrame 인덱스가 날짜이므로 날짜순 정렬 후 split
    X_features_sorted = X_features.sort_index()
    y_sorted = y.reindex(X_features_sorted.index)
    
    X_train, X_test, y_train, y_test = train_test_split(
        X_features_sorted, y_sorted, test_size=0.2, random_state=42, shuffle=False
    )
    
    print(f"학습 데이터: {len(X_train):,} 건, 테스트 데이터: {len(X_test):,} 건")
    
    # 4. 모델 앙상블 구축
    estimators = []
    
    # RandomForest
    rf = RandomForestClassifier(
        n_estimators=100, max_depth=10, min_samples_split=10, random_state=42, n_jobs=-1
    )
    estimators.append(('rf', rf))
    
    # XGBoost (카테고리 피처 명시적 지원 가능하지만 여기선 숫자 인코딩된 것 사용)
    xgb_model = xgb.XGBClassifier(
        n_estimators=100, max_depth=6, learning_rate=0.05, random_state=42, n_jobs=-1,
        eval_metric='logloss'
    )
    estimators.append(('xgb', xgb_model))
    
    # LightGBM
    lgbm_model = lgb.LGBMClassifier(
        n_estimators=100, max_depth=6, learning_rate=0.05, random_state=42, n_jobs=-1,
        verbose=-1
    )
    estimators.append(('lgbm', lgbm_model))
    
    # 앙상블 학습
    print("모델 학습 중... (시간이 다소 소요될 수 있습니다)")
    ensemble = VotingClassifier(estimators=estimators, voting='soft', n_jobs=-1)
    ensemble.fit(X_train, y_train)
    
    # 5. 평가
    predictions = ensemble.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)
    print(f"[{horizon_name}] 글로벌 모델 테스트 정확도: {accuracy:.2%}")
    
    # 6. 저장
    model_path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    
    # 저장할 때 인코더도 같이 저장해야 나중에 예측할 때 새로운 데이터 변환 가능
    joblib.dump({
        'model': ensemble,
        'features': list(X_features.columns),
        'encoder': encoder,
        'cat_cols': cat_cols,
        'target_days': target_days
    }, model_path)
    
    print(f"모델 저장 완료: {model_path}")
    print("=" * 60)
    
    return accuracy

def update_all_global_models():
    \"\"\"단기, 중기, 장기 글로벌 모델을 모두 학습합니다.\"\"\"
    horizons = {
        "short": 5,   # 1주일
        "mid": 20,    # 1개월
        "long": 60    # 1분기 (3개월)
    }
    
    results = {}
    for name, days in horizons.items():
        acc = train_global_model(horizon_name=name, target_days=days)
        if acc:
            results[name] = acc
            
    print("\\n✅ 글로벌 모델 학습 최종 결과:")
    for name, acc in results.items():
        print(f"- {name.capitalize()} (Target {horizons[name]}d): {acc:.2%}")
        
    return results

if __name__ == "__main__":
    update_all_global_models()
""")

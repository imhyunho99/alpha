import pandas as pd
import numpy as np
import os
import joblib
from .data_handler import load_data
from .market_features import get_ticker_metadata
from .global_model_handler import create_global_features_and_target

MODELS_DIR = os.path.expanduser("~/AlphaModels")

def predict_with_global_model(ticker, horizon_name="short"):
    """
    저장된 글로벌 모델을 사용하여 특정 종목의 최신 데이터에 대한 예측을 생성합니다.
    """
    model_path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    if not os.path.exists(model_path):
        print(f"경고: {horizon_name} 글로벌 모델이 없습니다. 먼저 모델을 학습시키세요.")
        return "Not Trained"

    # 모델, 피처 목록, 인코더 로드
    saved_data = joblib.load(model_path)
    model = saved_data['model']
    feature_columns = saved_data['features']
    encoder = saved_data['encoder']
    cat_cols = saved_data['cat_cols']

    # 1. 최신 데이터 로드
    data = load_data(ticker)
    if data is None or len(data) < 50:
        return "Insufficient Data"
        
    latest_data = data.tail(100) # 최근 100일 데이터로 충분
    
    # 2. 메타데이터 로드
    metadata = get_ticker_metadata([ticker])

    # 3. 피처 생성
    # target_days는 예측 시점에서는 실제 계산되지 않지만 함수 시그니처 맞추기 위해 더미값 전달
    features, _ = create_global_features_and_target(ticker, latest_data, metadata, target_days=1)
    
    if features.empty:
        return "Feature Error"
        
    # 마지막 데이터 포인트 선택
    latest_features = features.tail(1).copy()
    
    # Ticker 컬럼은 학습에서 제외되었으므로 여기서도 제외
    if 'Ticker' in latest_features.columns:
        latest_features = latest_features.drop(columns=['Ticker'])
        
    # 카테고리 인코딩
    latest_features[cat_cols] = encoder.transform(latest_features[cat_cols])
    
    # 피처 순서 맞추기 (학습에 쓰인 컬럼들만 선택)
    latest_features = latest_features[feature_columns]
    
    if latest_features.isnull().values.any():
        return "Insufficient Data (NaNs in features)"
        
    # 예측
    prediction = model.predict(latest_features)[0]
    decision = "UP" if prediction == 1 else "DOWN"
    
    # print(f"'{ticker}' [{horizon_name}] 최신 예측: {decision}")
    return decision
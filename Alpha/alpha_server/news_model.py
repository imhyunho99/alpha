import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import os
from .news_handler import get_daily_sentiment

MODELS_DIR = "alpha_server/models"
os.makedirs(MODELS_DIR, exist_ok=True)

def create_news_features(ticker, data):
    """뉴스 기반 특성을 생성합니다."""
    df = data.copy()
    
    # 일별 감성 데이터 가져오기
    start_date = df.index.min()
    end_date = df.index.max()
    
    sentiment_df = get_daily_sentiment(ticker, start_date, end_date)
    
    if sentiment_df.empty:
        # 뉴스 데이터 없으면 0으로 채움
        df['news_sentiment'] = 0.0
        df['news_volume'] = 0
        df['sentiment_ma7'] = 0.0
    else:
        # 날짜 인덱스 맞추기
        df = df.join(sentiment_df, how='left')
        df['news_sentiment'] = df['sentiment'].fillna(0)
        df['news_volume'] = df['news_count'].fillna(0)
        
        # 7일 이동평균
        df['sentiment_ma7'] = df['news_sentiment'].rolling(window=7, min_periods=1).mean()
        
        df = df.drop(columns=['sentiment', 'news_count'], errors='ignore')
    
    return df

def train_news_model(ticker, data):
    """뉴스 기반 모델을 학습합니다."""
    print(f"--- '{ticker}' 뉴스 모델 학습 시작 ---")
    
    # 뉴스 특성 추가
    df = create_news_features(ticker, data)
    
    # 타겟 생성 (7일 후 상승 여부)
    df['future_price'] = df['Close'].shift(-7)
    df['target'] = (df['future_price'] > df['Close']).astype(int)
    
    # 특성 선택
    feature_columns = ['news_sentiment', 'news_volume', 'sentiment_ma7']
    df = df.dropna()
    
    if len(df) < 50:
        print(f"경고: '{ticker}' 데이터 부족 (뉴스 모델)")
        return None
    
    X = df[feature_columns]
    y = df['target']
    
    # 학습/테스트 분할
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=False
    )
    
    # 모델 학습
    model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)
    
    # 평가
    predictions = model.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)
    print(f"'{ticker}' 뉴스 모델 정확도: {accuracy:.2f}")
    
    # 저장
    model_path = os.path.join(MODELS_DIR, f"{ticker}_news_model.joblib")
    joblib.dump({'model': model, 'features': feature_columns}, model_path)
    
    return accuracy

def predict_news(ticker, data):
    """뉴스 모델로 예측합니다."""
    model_path = os.path.join(MODELS_DIR, f"{ticker}_news_model.joblib")
    
    if not os.path.exists(model_path):
        return None
    
    saved = joblib.load(model_path)
    model = saved['model']
    
    # 최신 뉴스 특성
    df = create_news_features(ticker, data.tail(30))
    
    if df.empty:
        return None
    
    latest = df[saved['features']].tail(1)
    
    if latest.isnull().values.any():
        return None
    
    prediction = model.predict(latest)[0]
    prob = model.predict_proba(latest)[0]
    
    return {
        'prediction': 'UP' if prediction == 1 else 'DOWN',
        'confidence': max(prob)
    }

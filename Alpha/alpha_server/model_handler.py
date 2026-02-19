import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import os
from .data_handler import load_data
from .asset_screener import get_all_tickers

MODELS_DIR = "alpha_server/models"
os.makedirs(MODELS_DIR, exist_ok=True)

# load_data는 이제 data_handler에서 QuestDB로부터 가져옴

def create_features_and_target(data, target_days=7):
    """기술적 지표(feature)와 예측 목표(target)를 생성합니다."""
    df = data.copy()
    
    # --- 기존 특성 ---
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    
    # RSI 계산
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    df['Volatility'] = df['Close'].rolling(window=20).std()
    
    # --- Phase 1: 신규 특성 추가 ---
    # 추세 지표
    df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA_12'] - df['EMA_26']
    
    # 모멘텀 지표
    df['ROC'] = df['Close'].pct_change(periods=10) * 100
    df['Return_5d'] = df['Close'].pct_change(5)
    
    # 변동성 지표
    df['BB_Width'] = (df['Volatility'] * 4) / df['SMA_20']
    
    # 거래량 지표
    df['Volume_SMA'] = df['Volume'].rolling(window=20).mean()
    df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA']
    
    # 가격 패턴
    df['High_Low_Ratio'] = df['High'] / df['Low']
    
    # 시간 특성
    df['DayOfWeek'] = df.index.dayofweek
    df['Month'] = df.index.month
    
    # --- 목표 변수 (Target) 생성 ---
    df['future_price'] = df['Close'].shift(-target_days)
    df['target'] = (df['future_price'] > df['Close']).astype(int)
    
    # 불필요한 열 및 결측값 제거
    df = df.dropna()
    
    feature_columns = [
        'SMA_20', 'SMA_50', 'RSI_14', 'Volatility',
        'EMA_12', 'MACD', 'ROC', 'Return_5d', 'BB_Width',
        'Volume_Ratio', 'High_Low_Ratio', 'DayOfWeek', 'Month'
    ]
    
    return df[feature_columns], df['target']

def train_model(ticker):
    """지정된 티커에 대한 모델을 학습하고 저장합니다."""
    print(f"--- '{ticker}' 모델 학습 시작 ---")
    data = load_data(ticker)
    if data is None:
        return

    features, target = create_features_and_target(data)
    
    if features.empty:
        print(f"오류: '{ticker}'에 대한 학습 데이터를 생성할 수 없습니다.")
        return

    # 데이터 분할
    X_train, X_test, y_train, y_test = train_test_split(features, target, test_size=0.2, random_state=42, shuffle=False)
    
    # Phase 1: 하이퍼파라미터 개선
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=15,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train, y_train)
    
    # 평가
    predictions = model.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)
    print(f"'{ticker}' 모델 테스트 정확도: {accuracy:.2f}")
    
    # 모델과 사용된 특성 목록 저장
    model_path = os.path.join(MODELS_DIR, f"{ticker}_model.joblib")
    joblib.dump({'model': model, 'features': list(features.columns)}, model_path)
    print(f"'{ticker}' 모델을 '{model_path}'에 저장했습니다.")

def predict_latest(ticker):
    """저장된 모델을 사용하여 최신 데이터에 대한 예측을 생성합니다."""
    model_path = os.path.join(MODELS_DIR, f"{ticker}_model.joblib")
    if not os.path.exists(model_path):
        print(f"경고: '{ticker}'에 대한 학습된 모델이 없습니다. 먼저 모델을 학습시키세요.")
        return "Not Trained"

    # 모델과 특성 목록 로드
    saved_model = joblib.load(model_path)
    model = saved_model['model']
    feature_columns = saved_model['features']

    # 최신 데이터 로드 및 특성 생성
    data = load_data(ticker)
    if data is None: return "Data Error"
    
    latest_data = data.tail(100) # 최근 100일 데이터로 특성 계산
    df = latest_data.copy()
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    df['Volatility'] = df['Close'].rolling(window=20).std()

    # 마지막 데이터 포인트 선택
    latest_features = df[feature_columns].tail(1)
    if latest_features.isnull().values.any():
        return "Insufficient Data"
        
    # 예측
    prediction = model.predict(latest_features)[0]
    decision = "UP" if prediction == 1 else "DOWN"
    
    print(f"'{ticker}' 최신 예측: {decision}")
    return decision

def update_all_models():
    """TICKERS 목록에 있는 모든 자산에 대해 모델을 학습/업데이트합니다."""
    print("--- 모든 모델 업데이트 시작 ---")
    tickers = get_all_tickers()
    if not tickers:
        print("오류: 모델을 학습할 티커 목록을 가져올 수 없습니다.")
        return
        
    for ticker in tickers:
        train_model(ticker)
    print("--- 모든 모델 업데이트 완료 ---")

if __name__ == '__main__':
    update_all_models()

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import os
from .data_handler import DATA_DIR
from .asset_screener import get_all_tickers

MODELS_DIR = "alpha_server/models"
os.makedirs(MODELS_DIR, exist_ok=True)

def load_data(ticker):
    """지정된 티커의 CSV 데이터를 로드하고, 데이터 타입을 검증 및 정리합니다."""
    filepath = os.path.join(DATA_DIR, f"{ticker}.csv")
    if not os.path.exists(filepath):
        print(f"오류: '{ticker}'에 대한 데이터 파일이 없습니다. 먼저 데이터를 다운로드하세요.")
        return None
    
    try:
        # 1. 파일 읽기 (기존 로직 유지)
        data = pd.read_csv(filepath, index_col=0, parse_dates=True)
        if 'Date' in data.columns:
             data = pd.read_csv(filepath, header=0, skiprows=[1, 2], index_col=0, parse_dates=True)
             data.index.name = 'Date'
             data.columns = [col.replace('Price.', '') for col in data.columns]
        
        # 2. 숫자형 데이터 타입 강제 변환 (오류 방지 핵심 로직)
        numeric_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in numeric_cols:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors='coerce')
        
        # 3. 변환 과정에서 오류가 발생한 행(NaN) 제거
        initial_rows = len(data)
        data.dropna(subset=numeric_cols, inplace=True)
        if len(data) < initial_rows:
            print(f"경고: '{ticker}' 파일에서 {initial_rows - len(data)}개의 유효하지 않은 행을 제거했습니다.")

    except Exception as e:
        print(f"오류: '{filepath}' 파일 처리 중 오류 발생: {e}")
        return None
        
    if data.empty:
        print(f"경고: '{ticker}' 처리 후 데이터가 비어있습니다.")
        return None

    return data

def create_features_and_target(data, target_days=7):
    """기술적 지표(feature)와 예측 목표(target)를 생성합니다."""
    df = data.copy()
    
    # --- 특성 공학 (Feature Engineering) ---
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    
    # RSI 계산
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))

    # 변동성
    df['Volatility'] = df['Close'].rolling(window=20).std()

    # --- 목표 변수 (Target) 생성 ---
    # target_days 이후의 가격이 현재보다 높으면 1, 아니면 0
    df['future_price'] = df['Close'].shift(-target_days)
    df['target'] = (df['future_price'] > df['Close']).astype(int)
    
    # 불필요한 열 및 결측값 제거
    df = df.dropna()
    
    feature_columns = ['SMA_20', 'SMA_50', 'RSI_14', 'Volatility']
    target_column = 'target'
    
    return df[feature_columns], df[target_column]

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
    
    # 모델 초기화 및 학습
    model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
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

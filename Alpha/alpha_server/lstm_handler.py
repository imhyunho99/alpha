"""
Phase 3: LSTM 딥러닝 모델
시계열 패턴 학습을 위한 LSTM 구현
"""
import os
import numpy as np
import joblib
from sklearn.preprocessing import MinMaxScaler
from sklearn.model_selection import train_test_split

try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout
    from tensorflow.keras.callbacks import EarlyStopping
    TENSORFLOW_AVAILABLE = True
except ImportError:
    TENSORFLOW_AVAILABLE = False
    print("경고: TensorFlow가 설치되지 않았습니다. pip install tensorflow")

from .data_handler import load_data
from .model_handler import create_features_and_target

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')

def create_sequences(data, lookback=60):
    """시계열 데이터를 LSTM 입력 형태로 변환"""
    X, y = [], []
    for i in range(lookback, len(data)):
        X.append(data[i-lookback:i])
        y.append(data[i, -1])  # 마지막 열이 target
    return np.array(X), np.array(y)

def train_lstm_model(ticker, lookback=60, epochs=50):
    """Phase 3: LSTM 모델 학습"""
    if not TENSORFLOW_AVAILABLE:
        print("TensorFlow가 필요합니다. Phase 2 앙상블 모델을 사용하세요.")
        return None
    
    print(f"--- '{ticker}' LSTM 모델 학습 시작 ---")
    data = load_data(ticker)
    if data is None:
        return None

    features, target = create_features_and_target(data)
    
    if features.empty or len(features) < lookback + 100:
        print(f"오류: '{ticker}'에 대한 충분한 학습 데이터가 없습니다.")
        return None
    
    # 데이터 정규화
    scaler = MinMaxScaler()
    features_scaled = scaler.fit_transform(features)
    
    # target 추가
    data_with_target = np.column_stack([features_scaled, target.values])
    
    # 시퀀스 생성
    X, y = create_sequences(data_with_target, lookback)
    
    # 분할
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    
    # LSTM 모델 구축
    model = Sequential([
        LSTM(128, return_sequences=True, input_shape=(lookback, features.shape[1])),
        Dropout(0.2),
        LSTM(64, return_sequences=False),
        Dropout(0.2),
        Dense(32, activation='relu'),
        Dense(1, activation='sigmoid')
    ])
    
    model.compile(
        optimizer='adam',
        loss='binary_crossentropy',
        metrics=['accuracy']
    )
    
    # Early Stopping
    early_stop = EarlyStopping(
        monitor='val_loss',
        patience=5,
        restore_best_weights=True
    )
    
    # 학습
    history = model.fit(
        X_train, y_train,
        epochs=epochs,
        batch_size=32,
        validation_split=0.2,
        callbacks=[early_stop],
        verbose=0
    )
    
    # 평가
    loss, accuracy = model.evaluate(X_test, y_test, verbose=0)
    print(f"'{ticker}' LSTM 모델 테스트 정확도: {accuracy:.2%}")
    
    # 저장
    model_path = os.path.join(MODELS_DIR, f"{ticker}_lstm_model.h5")
    scaler_path = os.path.join(MODELS_DIR, f"{ticker}_scaler.joblib")
    
    model.save(model_path)
    joblib.dump({
        'scaler': scaler,
        'features': list(features.columns),
        'lookback': lookback
    }, scaler_path)
    
    print(f"'{ticker}' LSTM 모델을 '{model_path}'에 저장했습니다.")
    
    return accuracy

def predict_lstm(ticker, data=None):
    """LSTM 모델로 예측"""
    if not TENSORFLOW_AVAILABLE:
        return None
    
    model_path = os.path.join(MODELS_DIR, f"{ticker}_lstm_model.h5")
    scaler_path = os.path.join(MODELS_DIR, f"{ticker}_scaler.joblib")
    
    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        return None
    
    # 모델 로드
    model = tf.keras.models.load_model(model_path)
    scaler_data = joblib.load(scaler_path)
    scaler = scaler_data['scaler']
    lookback = scaler_data['lookback']
    
    # 데이터 준비
    if data is None:
        data = load_data(ticker)
    
    features, _ = create_features_and_target(data)
    
    if len(features) < lookback:
        return None
    
    # 정규화
    features_scaled = scaler.transform(features)
    
    # 최근 lookback 기간 데이터
    X = features_scaled[-lookback:].reshape(1, lookback, -1)
    
    # 예측
    prediction = model.predict(X, verbose=0)[0][0]
    
    return float(prediction)

def update_all_lstm_models(sample_size=10):
    """샘플 자산에 대해 LSTM 모델 학습 (전체는 시간 소요)"""
    if not TENSORFLOW_AVAILABLE:
        print("TensorFlow가 설치되지 않았습니다.")
        return
    
    from .asset_screener import get_all_tickers
    
    tickers = get_all_tickers()[:sample_size]
    print(f"\n{len(tickers)}개 자산에 대한 LSTM 모델 학습을 시작합니다...\n")
    
    accuracies = {}
    for ticker in tickers:
        try:
            acc = train_lstm_model(ticker, epochs=30)
            if acc:
                accuracies[ticker] = acc
        except Exception as e:
            print(f"오류: '{ticker}' LSTM 학습 실패 - {e}")
    
    if accuracies:
        avg_acc = np.mean(list(accuracies.values()))
        print(f"\n평균 정확도: {avg_acc:.2%}")
    
    print("\nLSTM 모델 학습이 완료되었습니다!")

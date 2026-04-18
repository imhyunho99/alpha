import pandas as pd
import numpy as np
import os
import joblib
from .data_handler import load_data, get_db_connection
from .market_features import get_ticker_metadata
from .asset_screener import get_all_tickers
from sklearn.model_selection import train_test_split

MODELS_DIR = os.path.expanduser("~/AlphaModels")
os.makedirs(MODELS_DIR, exist_ok=True)

def create_global_features_and_target(ticker, data, metadata, target_days=7):
    """
    개별 종목의 기술적 지표 + 메타데이터를 결합하여 Feature 생성.
    """
    df = data.copy()
    
    # --- 기술적 지표 (Technical Indicators) ---
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    df['Volatility'] = df['Close'].rolling(window=20).std()
    
    df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA_12'] - df['EMA_26']
    
    df['ROC'] = df['Close'].pct_change(periods=10) * 100
    df['Return_5d'] = df['Close'].pct_change(5)
    
    # 분모가 0이 되는 것을 방지
    sma_20_safe = df['SMA_20'].replace(0, np.nan)
    df['BB_Width'] = (df['Volatility'] * 4) / sma_20_safe
    
    df['Volume_SMA'] = df['Volume'].rolling(window=20).mean()
    vol_sma_safe = df['Volume_SMA'].replace(0, np.nan)
    df['Volume_Ratio'] = df['Volume'] / vol_sma_safe
    
    low_safe = df['Low'].replace(0, np.nan)
    df['High_Low_Ratio'] = df['High'] / low_safe
    
    df['DayOfWeek'] = df.index.dayofweek
    df['Month'] = df.index.month
    
    # --- 메타데이터 지표 (Metadata Indicators) ---
    meta = metadata.get(ticker, {})
    df['Sector'] = meta.get('sector', 'Unknown')
    df['Industry'] = meta.get('industry', 'Unknown')
    # Market Cap은 로그 스케일링 (값의 범위가 너무 크기 때문)
    mcap = meta.get('marketCap', 0)
    df['Log_MarketCap'] = np.log1p(mcap) if mcap > 0 else 0
    df['Beta'] = meta.get('beta', 1.0)
    
    # 티커 문자열 보존 (디버깅/조회용)
    df['Ticker'] = ticker
    
    # --- 목표 변수 (Target) 생성 ---
    df['future_price'] = df['Close'].shift(-target_days)
    df['target'] = (df['future_price'] > df['Close']).astype(int)
    
    # 결측치 제거
    df = df.dropna()
    
    feature_columns = [
        'Ticker', 'Sector', 'Industry', 'Log_MarketCap', 'Beta',
        'SMA_20', 'SMA_50', 'RSI_14', 'Volatility',
        'EMA_12', 'MACD', 'ROC', 'Return_5d', 'BB_Width',
        'Volume_Ratio', 'High_Low_Ratio', 'DayOfWeek', 'Month'
    ]
    
    return df[feature_columns], df['target']

def build_global_dataset(tickers, target_days=7):
    """모든 티커의 데이터를 모아 하나의 거대한 데이터셋을 생성합니다."""
    print(f"[{target_days}일 모델] 글로벌 데이터셋 구축 중... (대상: {len(tickers)}개 종목)")
    
    # 메타데이터 한 번에 로드
    metadata = get_ticker_metadata(tickers)
    
    all_features = []
    all_targets = []
    
    # QuestDB 연결 공유로 네트워크 오버헤드 감소
    conn = get_db_connection()
    
    success_count = 0
    for i, ticker in enumerate(tickers):
        if i % 50 == 0 and i > 0:
            print(f"데이터셋 처리 진행률: {i}/{len(tickers)}")
            
        data = load_data(ticker, conn=conn)
        if data is None or data.empty or len(data) < 50:
            continue
            
        features, target = create_global_features_and_target(data=data, ticker=ticker, metadata=metadata, target_days=target_days)
        
        if not features.empty:
            all_features.append(features)
            all_targets.append(target)
            success_count += 1
            
    conn.close()
    
    if not all_features:
        print("오류: 글로벌 데이터셋 생성 실패 (사용 가능한 데이터 없음)")
        return pd.DataFrame(), pd.Series()
        
    global_X = pd.concat(all_features)
    global_y = pd.concat(all_targets)
    
    print(f"[{target_days}일 모델] 데이터셋 구축 완료! 총 {success_count}개 종목, {len(global_X):,}개 데이터 포인트 확보.")
    return global_X, global_y


from sklearn.ensemble import VotingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score
import xgboost as xgb
import lightgbm as lgb
from sklearn.preprocessing import OrdinalEncoder

def train_global_model(horizon_name="short", target_days=7):
    """
    모든 종목의 데이터를 사용하여 단일 글로벌 모델을 학습합니다.
    horizon_name: "short", "mid", "long" 중 하나
    """
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
    
    # 인덱스(날짜)에 중복이 있으므로(여러 종목이 같은 날짜를 가짐), reindex 대신 sort_index를 양쪽에 동일하게 적용
    # 먼저 인덱스를 초기화하여 정렬 후 순서가 맞도록 유지
    X_features = X_features.reset_index()
    y = y.reset_index(drop=True)
    
    # 날짜(Date) 기준으로 정렬
    X_features_sorted = X_features.sort_values(by='Date')
    y_sorted = y.iloc[X_features_sorted.index]
    
    # Date를 다시 인덱스로 세팅 (선택사항이지만 모델 훈련에서는 필요 없음, 드롭)
    X_features_sorted = X_features_sorted.drop(columns=['Date'])
    
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
    """단기, 중기, 장기 글로벌 모델을 모두 학습합니다."""
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
            
    print("\n✅ 글로벌 모델 학습 최종 결과:")
    for name, acc in results.items():
        print(f"- {name.capitalize()} (Target {horizons[name]}d): {acc:.2%}")
        
    return results

if __name__ == "__main__":
    update_all_global_models()

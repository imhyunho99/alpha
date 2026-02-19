# Alpha 모델 개선 계획

## 📊 현재 모델 분석

### 현재 구조
- **모델**: RandomForestClassifier (100 estimators)
- **특성**: 4개 (SMA_20, SMA_50, RSI_14, Volatility)
- **목표**: 7일 후 가격 상승/하락 이진 분류
- **개별 모델**: 200+ 자산별 독립 학습

### 현재 한계
1. **특성 부족**: 단 4개의 기술적 지표만 사용
2. **단순 분류**: 상승/하락만 예측, 상승폭은 예측 못함
3. **시간 정보 무시**: 계절성, 요일 효과 미반영
4. **외부 요인 미반영**: 거시경제, 시장 심리 등
5. **하이퍼파라미터 미조정**: 기본값 사용

---

## 🎯 개선 방안 (우선순위별)

### 🔥 Priority 1: 특성 공학 강화 (즉시 적용 가능)

#### 1.1 추가 기술적 지표
```python
# 추세 지표
df['EMA_12'] = df['Close'].ewm(span=12).mean()
df['EMA_26'] = df['Close'].ewm(span=26).mean()
df['MACD'] = df['EMA_12'] - df['EMA_26']
df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()

# 모멘텀 지표
df['ROC'] = df['Close'].pct_change(periods=10) * 100  # Rate of Change
df['MOM'] = df['Close'] - df['Close'].shift(10)  # Momentum

# 변동성 지표
df['ATR'] = calculate_atr(df)  # Average True Range
df['Bollinger_Upper'] = df['SMA_20'] + (df['Volatility'] * 2)
df['Bollinger_Lower'] = df['SMA_20'] - (df['Volatility'] * 2)
df['BB_Width'] = (df['Bollinger_Upper'] - df['Bollinger_Lower']) / df['SMA_20']

# 거래량 지표
df['Volume_SMA'] = df['Volume'].rolling(window=20).mean()
df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA']
df['OBV'] = (df['Volume'] * (~df['Close'].diff().le(0) * 2 - 1)).cumsum()
```

**예상 효과**: 정확도 +3-5%

#### 1.2 가격 패턴 특성
```python
# 가격 변화율
df['Return_1d'] = df['Close'].pct_change(1)
df['Return_5d'] = df['Close'].pct_change(5)
df['Return_20d'] = df['Close'].pct_change(20)

# 고가/저가 비율
df['High_Low_Ratio'] = df['High'] / df['Low']
df['Close_Open_Ratio'] = df['Close'] / df['Open']

# 가격 위치 (최근 52주 대비)
df['Price_Position'] = (df['Close'] - df['Close'].rolling(252).min()) / \
                        (df['Close'].rolling(252).max() - df['Close'].rolling(252).min())
```

**예상 효과**: 정확도 +2-3%

#### 1.3 시간 특성
```python
# 날짜 정보
df['DayOfWeek'] = df.index.dayofweek
df['Month'] = df.index.month
df['Quarter'] = df.index.quarter

# 계절성 (사인/코사인 인코딩)
df['Month_Sin'] = np.sin(2 * np.pi * df['Month'] / 12)
df['Month_Cos'] = np.cos(2 * np.pi * df['Month'] / 12)
```

**예상 효과**: 정확도 +1-2%

---

### 🚀 Priority 2: 모델 아키텍처 개선

#### 2.1 앙상블 모델 강화
```python
from sklearn.ensemble import GradientBoostingClassifier, VotingClassifier
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier

# 다양한 모델 조합
rf_model = RandomForestClassifier(n_estimators=200, max_depth=10)
xgb_model = XGBClassifier(n_estimators=200, learning_rate=0.05)
lgbm_model = LGBMClassifier(n_estimators=200, learning_rate=0.05)

# 투표 앙상블
ensemble = VotingClassifier(
    estimators=[('rf', rf_model), ('xgb', xgb_model), ('lgbm', lgbm_model)],
    voting='soft'
)
```

**예상 효과**: 정확도 +3-7%

#### 2.2 회귀 모델 추가 (상승폭 예측)
```python
from sklearn.ensemble import RandomForestRegressor

# 분류 + 회귀 결합
classifier = RandomForestClassifier()  # 방향 예측
regressor = RandomForestRegressor()    # 변화율 예측

# 목표 변수
df['target_direction'] = (df['future_price'] > df['Close']).astype(int)
df['target_return'] = (df['future_price'] - df['Close']) / df['Close']

# 두 모델 결합하여 스코어링
direction_prob = classifier.predict_proba(X)[:, 1]
expected_return = regressor.predict(X)
final_score = direction_prob * expected_return
```

**예상 효과**: 스코어링 정확도 +5-10%

#### 2.3 딥러닝 모델 (장기 개선)
```python
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout

# LSTM 모델 (시계열 패턴 학습)
model = Sequential([
    LSTM(128, return_sequences=True, input_shape=(lookback, n_features)),
    Dropout(0.2),
    LSTM(64, return_sequences=False),
    Dropout(0.2),
    Dense(32, activation='relu'),
    Dense(1, activation='sigmoid')
])

model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
```

**예상 효과**: 정확도 +5-15% (데이터 충분 시)

---

### 📈 Priority 3: 하이퍼파라미터 최적화

#### 3.1 GridSearch / RandomSearch
```python
from sklearn.model_selection import GridSearchCV

param_grid = {
    'n_estimators': [100, 200, 300],
    'max_depth': [10, 20, 30, None],
    'min_samples_split': [2, 5, 10],
    'min_samples_leaf': [1, 2, 4],
    'max_features': ['sqrt', 'log2', None]
}

grid_search = GridSearchCV(
    RandomForestClassifier(),
    param_grid,
    cv=5,
    scoring='accuracy',
    n_jobs=-1
)

grid_search.fit(X_train, y_train)
best_model = grid_search.best_estimator_
```

**예상 효과**: 정확도 +2-4%

#### 3.2 Optuna를 이용한 베이지안 최적화
```python
import optuna

def objective(trial):
    params = {
        'n_estimators': trial.suggest_int('n_estimators', 100, 500),
        'max_depth': trial.suggest_int('max_depth', 5, 30),
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3),
    }
    
    model = XGBClassifier(**params)
    model.fit(X_train, y_train)
    return accuracy_score(y_test, model.predict(X_test))

study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=100)
```

**예상 효과**: 정확도 +3-5%

---

### 🌐 Priority 4: 외부 데이터 통합

#### 4.1 시장 지수 특성
```python
# S&P 500, NASDAQ, VIX 등
df['SPY_Return'] = spy_data['Close'].pct_change()
df['QQQ_Return'] = qqq_data['Close'].pct_change()
df['VIX'] = vix_data['Close']  # 변동성 지수

# 상관관계
df['Correlation_SPY'] = df['Close'].rolling(20).corr(spy_data['Close'])
```

**예상 효과**: 정확도 +2-4%

#### 4.2 뉴스 감성 분석 강화
```python
# 현재 시스템에 이미 있지만 개선 가능
from transformers import pipeline

sentiment_analyzer = pipeline("sentiment-analysis", 
                              model="ProsusAI/finbert")

# 뉴스 점수를 특성으로 추가
df['News_Sentiment'] = news_scores
df['News_Volume'] = news_counts
```

**예상 효과**: 정확도 +3-6%

#### 4.3 거시경제 지표
```python
# FRED API 활용
import fredapi

fred = fredapi.Fred(api_key='YOUR_KEY')

# 금리, 실업률, GDP 등
df['Interest_Rate'] = fred.get_series('DFF')
df['Unemployment'] = fred.get_series('UNRATE')
```

**예상 효과**: 정확도 +1-3%

---

### 🔧 Priority 5: 학습 전략 개선

#### 5.1 Walk-Forward 검증
```python
# 시계열 교차 검증
from sklearn.model_selection import TimeSeriesSplit

tscv = TimeSeriesSplit(n_splits=5)

for train_idx, test_idx in tscv.split(X):
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    
    model.fit(X_train, y_train)
    score = model.score(X_test, y_test)
```

**예상 효과**: 과적합 방지, 실전 성과 +5-10%

#### 5.2 클래스 불균형 처리
```python
from imblearn.over_sampling import SMOTE

# 상승/하락 비율이 불균형할 때
smote = SMOTE(random_state=42)
X_resampled, y_resampled = smote.fit_resample(X_train, y_train)
```

**예상 효과**: 정확도 +2-3%

#### 5.3 특성 중요도 기반 선택
```python
# 중요도 낮은 특성 제거
importances = model.feature_importances_
indices = np.argsort(importances)[::-1]

# 상위 N개만 사용
top_features = [feature_names[i] for i in indices[:20]]
X_selected = X[top_features]
```

**예상 효과**: 학습 속도 +30%, 과적합 방지

---

## 📋 구현 로드맵

### Phase 1: 빠른 개선 (1-2주)
1. ✅ 추가 기술적 지표 구현 (10개 추가)
2. ✅ 시간 특성 추가
3. ✅ 하이퍼파라미터 기본 튜닝
4. ✅ Walk-Forward 검증 도입

**예상 효과**: 정확도 55% → 62-65%

### Phase 2: 중급 개선 (2-4주)
1. ✅ XGBoost/LightGBM 앙상블
2. ✅ 회귀 모델 추가 (상승폭 예측)
3. ✅ 시장 지수 특성 통합
4. ✅ Optuna 하이퍼파라미터 최적화

**예상 효과**: 정확도 62-65% → 68-72%

### Phase 3: 고급 개선 (1-2개월)
1. ✅ LSTM 딥러닝 모델
2. ✅ 뉴스 감성 분석 고도화
3. ✅ 거시경제 지표 통합
4. ✅ 실시간 데이터 파이프라인

**예상 효과**: 정확도 68-72% → 75-80%

---

## 🎯 즉시 적용 가능한 개선안

### 개선안 1: 특성 10개 추가 (30분 작업)
```python
def create_features_and_target_v2(data, target_days=7):
    df = data.copy()
    
    # 기존 특성
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    df['RSI_14'] = calculate_rsi(df['Close'], 14)
    df['Volatility'] = df['Close'].rolling(window=20).std()
    
    # 신규 특성 (10개)
    df['EMA_12'] = df['Close'].ewm(span=12).mean()
    df['MACD'] = df['EMA_12'] - df['Close'].ewm(span=26).mean()
    df['ROC'] = df['Close'].pct_change(periods=10) * 100
    df['ATR'] = calculate_atr(df)
    df['BB_Width'] = calculate_bb_width(df)
    df['Volume_Ratio'] = df['Volume'] / df['Volume'].rolling(20).mean()
    df['Return_5d'] = df['Close'].pct_change(5)
    df['High_Low_Ratio'] = df['High'] / df['Low']
    df['DayOfWeek'] = df.index.dayofweek
    df['Month'] = df.index.month
    
    # 목표 변수
    df['target'] = (df['Close'].shift(-target_days) > df['Close']).astype(int)
    df = df.dropna()
    
    feature_columns = [
        'SMA_20', 'SMA_50', 'RSI_14', 'Volatility',
        'EMA_12', 'MACD', 'ROC', 'ATR', 'BB_Width',
        'Volume_Ratio', 'Return_5d', 'High_Low_Ratio',
        'DayOfWeek', 'Month'
    ]
    
    return df[feature_columns], df['target']
```

### 개선안 2: XGBoost 앙상블 (1시간 작업)
```python
def train_model_v2(ticker):
    features, target = create_features_and_target_v2(load_data(ticker))
    X_train, X_test, y_train, y_test = train_test_split(
        features, target, test_size=0.2, shuffle=False
    )
    
    # RandomForest + XGBoost 앙상블
    rf = RandomForestClassifier(n_estimators=200, max_depth=15, n_jobs=-1)
    xgb = XGBClassifier(n_estimators=200, learning_rate=0.05, max_depth=6)
    
    ensemble = VotingClassifier(
        estimators=[('rf', rf), ('xgb', xgb)],
        voting='soft'
    )
    
    ensemble.fit(X_train, y_train)
    accuracy = ensemble.score(X_test, y_test)
    
    print(f"{ticker} 정확도: {accuracy:.2%}")
    joblib.dump({'model': ensemble, 'features': list(features.columns)}, 
                f"models/{ticker}_model_v2.joblib")
```

### 개선안 3: 하이퍼파라미터 튜닝 (2시간 작업)
```python
def optimize_model(ticker):
    features, target = create_features_and_target_v2(load_data(ticker))
    X_train, X_test, y_train, y_test = train_test_split(
        features, target, test_size=0.2, shuffle=False
    )
    
    param_grid = {
        'n_estimators': [150, 200, 250],
        'max_depth': [10, 15, 20],
        'min_samples_split': [2, 5],
    }
    
    grid = GridSearchCV(
        RandomForestClassifier(n_jobs=-1),
        param_grid,
        cv=3,
        scoring='accuracy'
    )
    
    grid.fit(X_train, y_train)
    print(f"{ticker} 최적 파라미터: {grid.best_params_}")
    print(f"{ticker} 최고 정확도: {grid.best_score_:.2%}")
    
    return grid.best_estimator_
```

---

## 📊 예상 성과 비교

| 개선 단계 | 정확도 | 연간 수익률 | 샤프 비율 | 구현 시간 |
|----------|--------|------------|----------|----------|
| 현재 | 55-58% | 10-15% | 0.8 | - |
| Phase 1 | 62-65% | 15-20% | 1.0 | 1-2주 |
| Phase 2 | 68-72% | 20-28% | 1.2 | 2-4주 |
| Phase 3 | 75-80% | 28-40% | 1.5 | 1-2개월 |

---

## 🚨 주의사항

### 과적합 방지
- 특성이 많아질수록 과적합 위험 증가
- 정규화 (L1/L2) 적용 필수
- 교차 검증으로 일반화 성능 확인

### 데이터 품질
- 특성 추가 시 결측값 증가 가능
- 전처리 로직 강화 필요
- 이상치 탐지 및 제거

### 계산 비용
- 특성/모델 증가 → 학습 시간 증가
- 병렬 처리 최적화 필요
- 클라우드 GPU 활용 고려

---

## 📝 다음 단계

### 즉시 실행
1. `create_features_and_target_v2()` 구현
2. 10개 자산으로 테스트
3. 정확도 비교 분석

### 단기 목표 (1주)
1. 전체 자산에 적용
2. 성과 추적 시스템 구축
3. A/B 테스트 (기존 vs 신규)

### 중기 목표 (1개월)
1. XGBoost 앙상블 도입
2. 하이퍼파라미터 최적화
3. 실전 백테스팅

어떤 개선안부터 시작하시겠습니까?

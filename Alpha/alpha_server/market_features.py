"""
Phase 3: 외부 데이터 통합
시장 지수 및 거시경제 지표 추가
"""
import pandas as pd
import numpy as np
from .data_handler import load_data

def add_market_features(df, ticker):
    """시장 지수 특성 추가"""
    try:
        # S&P 500
        spy_data = load_data('SPY')
        if spy_data is not None and len(spy_data) > 0:
            spy_returns = spy_data['Close'].pct_change()
            df['SPY_Return'] = spy_returns.reindex(df.index, method='ffill')
            df['Correlation_SPY'] = df['Close'].rolling(20).corr(spy_data['Close'].reindex(df.index, method='ffill'))
        
        # NASDAQ 100
        qqq_data = load_data('QQQ')
        if qqq_data is not None and len(qqq_data) > 0:
            qqq_returns = qqq_data['Close'].pct_change()
            df['QQQ_Return'] = qqq_returns.reindex(df.index, method='ffill')
        
        # 암호화폐는 BTC 기준
        if '-USD' in ticker:
            btc_data = load_data('BTC-USD')
            if btc_data is not None and len(btc_data) > 0:
                btc_returns = btc_data['Close'].pct_change()
                df['BTC_Return'] = btc_returns.reindex(df.index, method='ffill')
                df['Correlation_BTC'] = df['Close'].rolling(20).corr(btc_data['Close'].reindex(df.index, method='ffill'))
    
    except Exception as e:
        print(f"시장 특성 추가 중 오류: {e}")
    
    return df

def create_features_with_market(data, ticker, target_days=7):
    """Phase 3: 시장 지수 포함 특성 생성"""
    df = data.copy()
    
    # 기존 특성 (Phase 1)
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
    df['BB_Width'] = (df['Volatility'] * 4) / df['SMA_20']
    df['Volume_SMA'] = df['Volume'].rolling(window=20).mean()
    df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA']
    df['High_Low_Ratio'] = df['High'] / df['Low']
    df['DayOfWeek'] = df.index.dayofweek
    df['Month'] = df.index.month
    
    # Phase 3: 시장 특성 추가
    df = add_market_features(df, ticker)
    
    # 목표 변수
    df['future_price'] = df['Close'].shift(-target_days)
    df['target'] = (df['future_price'] > df['Close']).astype(int)
    
    df = df.dropna()
    
    # 특성 목록 (시장 특성 포함)
    feature_columns = [
        'SMA_20', 'SMA_50', 'RSI_14', 'Volatility',
        'EMA_12', 'MACD', 'ROC', 'Return_5d', 'BB_Width',
        'Volume_Ratio', 'High_Low_Ratio', 'DayOfWeek', 'Month'
    ]
    
    # 시장 특성이 있으면 추가
    market_features = ['SPY_Return', 'Correlation_SPY', 'QQQ_Return', 'BTC_Return', 'Correlation_BTC']
    for feat in market_features:
        if feat in df.columns:
            feature_columns.append(feat)
    
    # 존재하는 특성만 선택
    available_features = [f for f in feature_columns if f in df.columns]
    
    return df[available_features], df['target']

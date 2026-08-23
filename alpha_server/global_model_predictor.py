import os

import joblib
import numpy as np

from .data_handler import load_data
from .market_features import get_ticker_metadata
from .global_model_handler import create_global_features_and_target

MODELS_DIR = os.path.expanduser("~/AlphaModels")


def _load_model_and_features(horizon_name):
    """모델 번들을 로드한다. 없으면 None."""
    model_path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    if not os.path.exists(model_path):
        return None
    saved = joblib.load(model_path)
    return saved['model'], saved['features'], saved['encoder'], saved['cat_cols']


def _latest_feature_row(ticker, feature_columns, encoder, cat_cols):
    """최신 시점 피처 1행. 만들 수 없으면 None."""
    data = load_data(ticker)
    if data is None or len(data) < 50:
        return None

    metadata = get_ticker_metadata([ticker])
    # target_days는 예측 시점에서 실제로 쓰이지 않지만 시그니처를 맞추기 위해 넘긴다.
    features, _ = create_global_features_and_target(
        ticker, data.tail(100), metadata, target_days=1
    )
    if features.empty:
        return None

    row = features.tail(1).copy()
    # Ticker 컬럼은 학습에서 제외되었으므로 여기서도 제외한다.
    if 'Ticker' in row.columns:
        row = row.drop(columns=['Ticker'])
    if cat_cols:
        row[cat_cols] = encoder.transform(row[cat_cols])
    row = row[feature_columns]
    if row.isnull().values.any():
        return None
    return row


def predict_proba_with_global_model(ticker, horizon_name="short"):
    """상승(class 1) 확률을 0~1로 반환. 모델·데이터가 없으면 None."""
    bundle = _load_model_and_features(horizon_name)
    if bundle is None:
        return None
    model, feature_columns, encoder, cat_cols = bundle

    row = _latest_feature_row(ticker, feature_columns, encoder, cat_cols)
    if row is None:
        return None

    try:
        proba = model.predict_proba(row)
    except Exception:
        return None

    classes = list(getattr(model, "classes_", [0, 1]))
    if 1 not in classes:
        return None
    return float(np.asarray(proba)[0][classes.index(1)])


def predict_with_global_model(ticker, horizon_name="short"):
    """기존 호출부 호환용. 확률을 0.5 기준으로 라벨화한다.

    실패 사유별 문자열 반환은 유지한다 — scoring_engine이 이 문자열들을 0점 처리한다.
    """
    if _load_model_and_features(horizon_name) is None:
        print(f"경고: {horizon_name} 글로벌 모델이 없습니다. 먼저 모델을 학습시키세요.")
        return "Not Trained"

    proba = predict_proba_with_global_model(ticker, horizon_name)
    if proba is None:
        return "Insufficient Data"
    return "UP" if proba >= 0.5 else "DOWN"

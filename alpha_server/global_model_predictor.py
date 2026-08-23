import os

import joblib
import numpy as np

from .data_handler import load_data
from .market_features import get_ticker_metadata
from .global_model_handler import create_global_features_and_target

MODELS_DIR = os.path.expanduser("~/AlphaModels")


_MODEL_CACHE: dict = {}


def clear_model_cache():
    """재학습 직후처럼 강제로 다시 읽어야 할 때 쓴다."""
    _MODEL_CACHE.clear()


# 학습 코드가 'Date' 를 drop 한 뒤 학습하면서 features 목록에는 그대로 남겨두었다.
# 그 결과 저장된 목록(18개)이 모델이 기대하는 입력(17개)과 어긋나, 예측이
# KeyError 로 죽고 scoring_engine 이 그 예외를 삼켜 AI 점수를 0으로 대체해왔다.
# 재학습 없이 쓰기 위해 로드 시점에 목록을 모델 기준으로 맞춘다.
_NON_FEATURE_COLUMNS = ("Date", "Ticker", "future_price", "target")


def _reconcile_features(model, features):
    expected = getattr(model, "n_features_in_", None)
    if expected is None or len(features) == expected:
        return features

    trimmed = [f for f in features if f not in _NON_FEATURE_COLUMNS]
    if len(trimmed) == expected:
        return trimmed

    print(
        f"경고: 저장된 feature 목록({len(features)}개)이 모델 입력({expected}개)과 "
        f"맞지 않습니다. 재학습이 필요합니다."
    )
    return features


def _load_model_and_features(horizon_name):
    """모델 번들을 로드한다. 없으면 None."""
    model_path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    if not os.path.exists(model_path):
        return None
    # 11MB 모델을 호출마다 읽으면 루프가 디스크에 묶인다. mtime을 키로 캐시하되
    # 재학습으로 파일이 바뀌면 자동으로 다시 읽는다.
    mtime = os.path.getmtime(model_path)
    cached = _MODEL_CACHE.get(horizon_name)
    if cached and cached[0] == mtime:
        return cached[1]

    saved = joblib.load(model_path)
    model = saved['model']
    features = _reconcile_features(model, list(saved['features']))
    bundle = (model, features, saved['encoder'], saved['cat_cols'])
    _MODEL_CACHE[horizon_name] = (mtime, bundle)
    return bundle


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

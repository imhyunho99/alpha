"""Alpha158 방식 정규화 피처로 글로벌 모델을 학습한다.

벤치마크(2026-08-23, walk-forward 8폴드, 퍼지 20일)가 정한 구성이다.

    always_up            IC —        스프레드 -0.06%
    momentum             IC -0.016   스프레드 -1.10%
    norm13 (정규화 17→13) IC  0.049   스프레드 +0.67%
    voting76             IC  0.095   스프레드 +2.87%   ← 채택

기존 학습기(global_model_handler)와 다른 점 셋:

1. **정규화된 76피처.** 원시 가격 피처는 모델이 패턴 대신 종목을 식별하게 만든다.
   같은 17피처를 정규화하자 IC 가 0.102 → 0.049 로 떨어졌다. 사라진 절반은
   실력이 아니라 "학습 구간에서 오른 종목군을 외운 것"이었다.
2. **저장하는 피처 목록이 실제 학습 입력과 일치한다.** 기존 코드는 'Date' 를
   drop 한 뒤 학습하면서 목록에는 남겨두어, 예측이 KeyError 로 죽고
   scoring_engine 이 그 예외를 삼켜 AI 점수를 0 으로 대체해왔다.
3. **feature_set 을 기록한다.** 예측기가 어느 빌더를 써야 하는지 알 수 있다.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .data_quality import filter_frames
from .features_alpha158 import build_features
from .global_model_predictor import ALPHA158_FEATURE_SET, MODELS_DIR

# horizon 이름 → 예측 지평(영업일). 기존 규약을 유지한다.
HORIZONS = {"short": 5, "mid": 20, "long": 60}


def build_training_panel(
    frames: dict[str, pd.DataFrame], target_days: int
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """{ticker: OHLCV} → (X, y, feature_columns).

    마지막 target_days 행은 정답을 알 수 없으므로 버린다.
    """
    frames = filter_frames(frames)
    parts: list[pd.DataFrame] = []

    for ticker, frame in frames.items():
        try:
            feats = build_features(frame.sort_index())
        except Exception as exc:
            print(f"'{ticker}' 피처 실패: {exc}")
            continue
        if feats is None or feats.empty:
            continue

        close = frame["Close"]
        # 마지막 target_days 행은 미래가 없다. (NaN > close) 는 False 가 되어
        # astype(float) 하면 조용히 0(하락)으로 둔갑한다 — 반드시 NaN 으로 남겨
        # dropna 에서 버려지게 한다.
        future = close.shift(-target_days)
        target = (future > close).astype(float).where(future.notna())
        part = feats.copy()
        part["__target"] = target.reindex(part.index)
        parts.append(part)

    if not parts:
        raise RuntimeError("학습에 쓸 수 있는 종목이 없습니다")

    panel = pd.concat(parts)
    panel = panel.replace([np.inf, -np.inf], np.nan).dropna()
    if panel.empty:
        raise RuntimeError("결측 제거 후 남은 행이 없습니다")

    feature_columns = [c for c in panel.columns if c != "__target"]
    return panel[feature_columns], panel["__target"].astype(int), feature_columns


def _make_ensemble():
    from sklearn.ensemble import RandomForestClassifier, VotingClassifier

    estimators = [("rf", RandomForestClassifier(
        n_estimators=200, max_depth=10, min_samples_leaf=20,
        n_jobs=-1, random_state=42))]
    try:
        from xgboost import XGBClassifier
        estimators.append(("xgb", XGBClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, n_jobs=-1,
            random_state=42, eval_metric="logloss")))
    except ImportError:
        print("경고: XGBoost 없음 — 앙상블이 약해집니다")
    try:
        from lightgbm import LGBMClassifier
        estimators.append(("lgbm", LGBMClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, n_jobs=-1,
            random_state=42, verbose=-1)))
    except ImportError:
        print("경고: LightGBM 없음 — 앙상블이 약해집니다")

    return VotingClassifier(estimators=estimators, voting="soft", n_jobs=1)


def backup_existing(horizon_name: str) -> str | None:
    """기존 모델을 타임스탬프를 붙여 보관한다. 되돌릴 길을 남긴다."""
    path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    if not os.path.exists(path):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.{stamp}.bak.joblib")
    shutil.copy2(path, backup)
    return backup


def train(horizon_name: str, frames: dict[str, pd.DataFrame],
          holdout_days: int = 126) -> dict:
    """한 horizon 을 학습하고 저장한다. 마지막 holdout_days 는 검증용으로 남긴다."""
    import joblib

    target_days = HORIZONS[horizon_name]
    print(f"\n===== {horizon_name} ({target_days}일 지평) =====", flush=True)

    X, y, feature_columns = build_training_panel(frames, target_days)
    print(f"패널 {len(X):,}행 × 피처 {len(feature_columns)}개, 상승 비율 {y.mean():.3f}")

    # 시간순 홀드아웃. 셔플하지 않는다.
    order = X.index.argsort()
    X, y = X.iloc[order], y.iloc[order]
    cut = len(X) - holdout_days * max(1, len(frames) // 4)
    cut = max(int(len(X) * 0.8), min(cut, len(X) - 1))
    X_tr, y_tr = X.iloc[:cut], y.iloc[:cut]
    X_te, y_te = X.iloc[cut:], y.iloc[cut:]

    model = _make_ensemble()
    print(f"학습 {len(X_tr):,} / 검증 {len(X_te):,} — 시작", flush=True)
    model.fit(X_tr, y_tr)

    from sklearn.metrics import accuracy_score, roc_auc_score

    proba = model.predict_proba(X_te)[:, list(model.classes_).index(1)]
    acc = accuracy_score(y_te, (proba >= 0.5).astype(int))
    try:
        auc = roc_auc_score(y_te, proba)
    except ValueError:
        auc = float("nan")
    base = float(y_te.mean())
    print(f"홀드아웃 정확도 {acc:.3f} (기저율 {base:.3f}), AUC {auc:.3f}")
    print(f"확률 분포: 평균 {proba.mean():.3f}, 최대 {proba.max():.3f}, "
          f">=0.6 비율 {(proba >= 0.6).mean():.1%}")

    backup = backup_existing(horizon_name)
    if backup:
        print(f"기존 모델 보관: {os.path.basename(backup)}")

    path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    joblib.dump({
        "model": model,
        # 실제 학습 입력과 정확히 같은 목록. 여기가 어긋나 예측이 죽어 있었다.
        "features": feature_columns,
        "encoder": None,
        "cat_cols": [],
        "feature_set": ALPHA158_FEATURE_SET,
        "target_days": target_days,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "holdout_accuracy": float(acc),
        "holdout_auc": float(auc),
        "holdout_base_rate": base,
    }, path)
    print(f"저장 완료: {os.path.basename(path)}")

    return {"horizon": horizon_name, "accuracy": float(acc), "auc": float(auc),
            "base_rate": base, "n_features": len(feature_columns),
            "proba_mean": float(proba.mean()), "proba_max": float(proba.max())}

"""모델 후보를 정직하게 비교하기 위한 walk-forward 평가 하네스.

이 프로젝트의 기존 성능 수치가 전부 신뢰를 잃은 이유는 모델이 아니라 평가였다.
`performance_results_v2.csv` 의 "정확도 100% 43종목" 은 홀드아웃 구간이 통째로
상승장이라 "항상 오른다" 가 다 맞은 것이었다. 그래서 여기서는 두 가지를 강제한다.

1. **퍼지(purge).** 타깃이 20일 앞을 보므로 학습 구간의 마지막 20일은 시험 구간과
   겹친다. 잘라내지 않으면 답을 보고 학습한다.
2. **순위 지표.** 정확도는 장세를 재고 순위 상관(IC)은 실력을 잰다. 종목을 줄 세워
   상위 N개를 담는 전략에서 의미 있는 건 후자다.

비교 대상에 `always_up`(항상 상승)과 `momentum`(과거 수익률)을 반드시 포함한다.
ML 후보가 이 둘을 못 이기면 ML 을 쓸 이유가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

# 타깃 지평. global_mid 와 맞춘다 (20영업일 ≈ 1개월)
DEFAULT_HORIZON = 20


# --------------------------------------------------------------------------
# 패널 구성
# --------------------------------------------------------------------------

def build_panel(
    frames: dict[str, pd.DataFrame],
    feature_fn: Callable[[pd.DataFrame], pd.DataFrame],
    horizon: int = DEFAULT_HORIZON,
) -> pd.DataFrame:
    """{ticker: OHLCV} → 롱 패널 (date, ticker, 피처들, fwd_return, target).

    fwd_return 은 horizon 일 뒤 종가 대비 수익률이며, 마지막 horizon 일은
    답을 알 수 없으므로 버린다.
    """
    parts: list[pd.DataFrame] = []
    for ticker, raw in frames.items():
        if raw is None or raw.empty or "Close" not in raw.columns:
            continue
        frame = raw.sort_index()
        try:
            feats = feature_fn(frame)
        except Exception as exc:
            print(f"'{ticker}' 피처 생성 실패, 건너뜁니다: {exc}")
            continue
        if feats is None or feats.empty:
            continue

        close = frame["Close"]
        fwd = close.shift(-horizon) / close - 1.0

        part = feats.copy()
        part["ticker"] = ticker
        part["fwd_return"] = fwd.reindex(part.index)
        parts.append(part)

    if not parts:
        return pd.DataFrame()

    panel = pd.concat(parts)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel = panel.dropna(subset=["fwd_return"])
    panel["target"] = (panel["fwd_return"] > 0).astype(int)
    return panel.sort_values(["date", "ticker"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# 분할
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Fold:
    index: int
    train_end: pd.Timestamp     # 이 시점까지 학습 (퍼지 적용 전)
    purge_end: pd.Timestamp     # 실제로 학습에 쓰는 마지막 날
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_folds(
    dates: Iterable[pd.Timestamp],
    n_folds: int = 8,
    test_days: int = 63,
    horizon: int = DEFAULT_HORIZON,
    min_train_days: int = 252,
) -> list[Fold]:
    """확장 윈도우 walk-forward 분할. 학습 꼬리에서 horizon 일을 퍼지한다."""
    unique = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(dates)))))
    if len(unique) < min_train_days + test_days * n_folds:
        # 데이터가 짧으면 가능한 만큼만 만든다
        n_folds = max(1, (len(unique) - min_train_days) // max(test_days, 1))

    folds: list[Fold] = []
    total_test = test_days * n_folds
    first_test_pos = len(unique) - total_test
    if first_test_pos <= min_train_days:
        first_test_pos = min_train_days

    for i in range(n_folds):
        test_lo = first_test_pos + i * test_days
        test_hi = min(test_lo + test_days - 1, len(unique) - 1)
        if test_lo >= len(unique):
            break
        train_end_pos = test_lo - 1
        purge_pos = train_end_pos - horizon
        if purge_pos <= 0:
            continue
        folds.append(
            Fold(
                index=i,
                train_end=unique[train_end_pos],
                purge_end=unique[purge_pos],
                test_start=unique[test_lo],
                test_end=unique[test_hi],
            )
        )
    return folds


# --------------------------------------------------------------------------
# 지표
# --------------------------------------------------------------------------

def rank_ic(scores: pd.Series, forward: pd.Series, dates: pd.Series) -> pd.Series:
    """일자별 횡단면 순위 상관(Spearman). 순위 전략의 실질 알파 지표."""
    df = pd.DataFrame({"score": scores, "fwd": forward, "date": dates}).dropna()
    out: dict[pd.Timestamp, float] = {}
    for day, chunk in df.groupby("date"):
        if len(chunk) < 5 or chunk["score"].nunique() < 2:
            continue
        out[day] = chunk["score"].corr(chunk["fwd"], method="spearman")
    return pd.Series(out).dropna()


def decile_spread(scores: pd.Series, forward: pd.Series, dates: pd.Series) -> float:
    """상위 10분위 − 하위 10분위 수익률 차이. **중앙값** 기준.

    평균을 쓰면 깨진 시계열 하나가 지표를 통째로 뒤집는다. 실제로 USDE-USD 의
    20일 수익률 +4,799,376% 하나 때문에 스프레드가 -703% 로 찍혔다. 데이터
    위생 검사(alpha_server/data_quality.py)로 걸러내지만, 지표 자체도 이런
    꼬리에 흔들리지 않아야 한다.
    """
    df = pd.DataFrame({"score": scores, "fwd": forward, "date": dates}).dropna()
    tops: list[float] = []
    bots: list[float] = []
    for _, chunk in df.groupby("date"):
        if len(chunk) < 10:
            continue
        k = max(1, len(chunk) // 10)
        ranked = chunk.sort_values("score")
        bots.append(ranked["fwd"].iloc[:k].median())
        tops.append(ranked["fwd"].iloc[-k:].median())
    if not tops:
        return float("nan")
    return float(np.median(tops) - np.median(bots))


@dataclass
class Result:
    name: str
    accuracy: float = float("nan")
    auc: float = float("nan")
    ic_mean: float = float("nan")
    ic_std: float = float("nan")
    icir: float = float("nan")
    spread: float = float("nan")
    n_test: int = 0
    folds: int = 0
    notes: list[str] = field(default_factory=list)

    def row(self) -> str:
        return (
            f"{self.name:<16}{self.accuracy:>9.3f}{self.auc:>8.3f}"
            f"{self.ic_mean:>9.4f}{self.icir:>8.2f}{self.spread*100:>10.2f}%"
            f"{self.n_test:>10,}"
        )


HEADER = (
    f"{'후보':<16}{'정확도':>9}{'AUC':>8}{'IC평균':>9}{'ICIR':>8}"
    f"{'10분위스프레드':>11}{'시험표본':>10}"
)


def summarise(name: str, preds: pd.DataFrame, folds: int) -> Result:
    """preds: date, ticker, score, proba, target, fwd_return"""
    from sklearn.metrics import roc_auc_score

    res = Result(name=name, n_test=len(preds), folds=folds)
    if preds.empty:
        res.notes.append("예측 없음")
        return res

    hard = (preds["proba"] >= 0.5).astype(int)
    res.accuracy = float((hard == preds["target"]).mean())
    try:
        if preds["target"].nunique() > 1:
            res.auc = float(roc_auc_score(preds["target"], preds["proba"]))
    except Exception:
        pass

    ic = rank_ic(preds["score"], preds["fwd_return"], preds["date"])
    if len(ic):
        res.ic_mean = float(ic.mean())
        res.ic_std = float(ic.std())
        res.icir = float(ic.mean() / ic.std()) if ic.std() > 0 else float("nan")
    res.spread = decile_spread(preds["score"], preds["fwd_return"], preds["date"])
    return res


# --------------------------------------------------------------------------
# 후보 모델
# --------------------------------------------------------------------------

def _fit_predict_sklearn(make_model, train: pd.DataFrame, test: pd.DataFrame,
                         feature_cols: list[str]) -> pd.DataFrame | None:
    """학습 후 상승 확률을 낸다. 학습 불가면 None."""
    X_tr = train[feature_cols].replace([np.inf, -np.inf], np.nan).dropna()
    y_tr = train.loc[X_tr.index, "target"]
    if len(X_tr) < 100 or y_tr.nunique() < 2:
        return None

    X_te = test[feature_cols].replace([np.inf, -np.inf], np.nan)
    keep = X_te.dropna().index
    if len(keep) == 0:
        return None

    model = make_model()
    model.fit(X_tr, y_tr)
    proba = model.predict_proba(X_te.loc[keep])
    classes = list(model.classes_)
    up = np.asarray(proba)[:, classes.index(1)] if 1 in classes else np.full(len(keep), 0.5)

    out = test.loc[keep, ["date", "ticker", "target", "fwd_return"]].copy()
    out["proba"] = up
    out["score"] = up
    return out


def candidate_always_up(train, test, feature_cols):
    """항상 상승 예측. 기저율을 드러낸다 — 순위를 못 매기므로 IC 는 정의되지 않는다."""
    out = test[["date", "ticker", "target", "fwd_return"]].copy()
    out["proba"] = 0.6
    out["score"] = 0.6
    return out


def candidate_momentum(train, test, feature_cols):
    """20일 과거 수익률로 순위. ML 없는 고전 베이스라인."""
    col = next((c for c in ("ROC20", "ret20", "Return_20d", "MOM20") if c in test.columns), None)
    out = test[["date", "ticker", "target", "fwd_return"]].copy()
    if col is None:
        return None
    raw = test[col].astype(float)
    # ROC20 = close.shift(20)/close 이므로 값이 작을수록 최근 상승폭이 크다 → 부호 반전
    signal = -raw if col == "ROC20" else raw
    out["score"] = signal.values
    # 확률처럼 쓰기 위해 일자별 백분위로 환산
    out["proba"] = out.groupby("date")["score"].rank(pct=True).values
    return out


def candidate_voting(train, test, feature_cols):
    """현재 구조 — RandomForest + XGBoost + LightGBM 소프트 보팅."""
    def make():
        from sklearn.ensemble import RandomForestClassifier, VotingClassifier

        estimators = [("rf", RandomForestClassifier(
            n_estimators=200, max_depth=10, min_samples_leaf=20, n_jobs=-1, random_state=42))]
        try:
            from xgboost import XGBClassifier
            estimators.append(("xgb", XGBClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, n_jobs=-1,
                random_state=42, eval_metric="logloss")))
        except ImportError:
            pass
        try:
            from lightgbm import LGBMClassifier
            estimators.append(("lgbm", LGBMClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, n_jobs=-1,
                random_state=42, verbose=-1)))
        except ImportError:
            pass
        return VotingClassifier(estimators=estimators, voting="soft", n_jobs=1)

    return _fit_predict_sklearn(make, train, test, feature_cols)


def candidate_lgbm(train, test, feature_cols):
    """LightGBM 단독 — 앙상블이 값을 하는지 보는 대조군."""
    def make():
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, n_jobs=-1,
            random_state=42, verbose=-1)

    return _fit_predict_sklearn(make, train, test, feature_cols)


def run_walk_forward(
    panel: pd.DataFrame,
    candidates: dict[str, Callable],
    feature_cols: list[str],
    n_folds: int = 8,
    test_days: int = 63,
    horizon: int = DEFAULT_HORIZON,
    verbose: bool = True,
) -> dict[str, Result]:
    """모든 후보를 같은 폴드에서 돌려 비교 가능한 결과를 만든다."""
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    folds = make_folds(panel["date"].unique(), n_folds=n_folds,
                       test_days=test_days, horizon=horizon)
    if verbose:
        print(f"폴드 {len(folds)}개 — 시험 구간 "
              f"{folds[0].test_start.date()} ~ {folds[-1].test_end.date()}")

    collected: dict[str, list[pd.DataFrame]] = {name: [] for name in candidates}
    for fold in folds:
        train = panel[panel["date"] <= fold.purge_end]      # 퍼지 적용
        test = panel[(panel["date"] >= fold.test_start) & (panel["date"] <= fold.test_end)]
        if train.empty or test.empty:
            continue
        if verbose:
            print(f"  폴드 {fold.index}: 학습 {len(train):,} / 시험 {len(test):,} "
                  f"(~{fold.purge_end.date()} | {fold.test_start.date()}~)", flush=True)

        for name, fn in candidates.items():
            try:
                preds = fn(train, test, feature_cols)
            except Exception as exc:
                print(f"    {name} 실패: {exc}")
                continue
            if preds is not None and not preds.empty:
                collected[name].append(preds)

    results: dict[str, Result] = {}
    for name, parts in collected.items():
        merged = pd.concat(parts) if parts else pd.DataFrame()
        results[name] = summarise(name, merged, folds=len(parts))
    return results


# --------------------------------------------------------------------------
# 현재 구조가 쓰는 피처 재현
# --------------------------------------------------------------------------

def current_feature_fn(metadata: dict | None = None, ticker: str | None = None):
    """global_model_handler 가 만드는 17개 피처를 그대로 재현한다.

    비교의 기준선이므로 정의를 바꾸지 않는다. 메타데이터(섹터·산업·시총·베타)는
    종목마다 고정값이라 횡단면 타이밍에는 기여하지 못하지만, 현재 구조가 쓰고
    있으므로 그대로 포함한다.
    """
    meta = (metadata or {}).get(ticker or "", {})

    def _build(frame: pd.DataFrame) -> pd.DataFrame:
        df = frame.copy()
        close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

        out = pd.DataFrame(index=df.index)
        out["SMA_20"] = close.rolling(20).mean()
        out["SMA_50"] = close.rolling(50).mean()

        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        # 원본(global_model_handler)과 동일하게 둔다. 하락일이 없으면 loss=0 이라
        # rs 가 inf 가 되고 RSI 는 100 으로 수렴한다. 여기서 NaN 을 만들면 행이
        # 통째로 버려져 기준선이 원본과 달라진다.
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = gain / loss
        out["RSI_14"] = 100 - (100 / (1 + rs))

        out["Volatility"] = close.rolling(20).std()
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        out["EMA_12"] = ema12
        out["MACD"] = ema12 - ema26
        out["ROC"] = close.pct_change(10) * 100
        out["Return_5d"] = close.pct_change(5)
        out["BB_Width"] = (out["Volatility"] * 4) / out["SMA_20"].replace(0, np.nan)

        vol_sma = volume.rolling(20).mean()
        out["Volume_Ratio"] = volume / vol_sma.replace(0, np.nan)
        out["High_Low_Ratio"] = high / low.replace(0, np.nan)
        out["DayOfWeek"] = df.index.dayofweek
        out["Month"] = df.index.month

        mcap = meta.get("marketCap", 0) or 0
        out["Log_MarketCap"] = np.log1p(mcap) if mcap > 0 else 0.0
        out["Beta"] = meta.get("beta", 1.0) or 1.0
        # 문자열 범주는 안정적인 해시 코드로 — 원본은 OrdinalEncoder 를 쓰지만
        # 폴드마다 인코더를 새로 맞추면 비교가 흔들린다.
        out["Sector_code"] = float(abs(hash(meta.get("sector", "Unknown"))) % 1000)
        out["Industry_code"] = float(abs(hash(meta.get("industry", "Unknown"))) % 1000)

        # 모멘텀 베이스라인이 참조할 20일 변화율 (ROC20 규약과 맞춘다)
        out["ROC20"] = close.shift(20) / close

        return out.dropna()

    return _build


CURRENT_FEATURE_COLUMNS = [
    "SMA_20", "SMA_50", "RSI_14", "Volatility", "EMA_12", "MACD", "ROC",
    "Return_5d", "BB_Width", "Volume_Ratio", "High_Low_Ratio", "DayOfWeek",
    "Month", "Log_MarketCap", "Beta", "Sector_code", "Industry_code",
]


# --------------------------------------------------------------------------
# 정규화
# --------------------------------------------------------------------------

# 가격 단위를 그대로 갖는 피처들. 종가로 나누지 않으면 모델이 패턴이 아니라
# 종목을 식별한다 — SMA_20=500(애플)과 SMA_20=0.001(잡코인)은 지문이다.
PRICE_SCALED_COLUMNS = ("SMA_20", "SMA_50", "EMA_12", "MACD", "Volatility")

# 종목마다 고정된 값. 횡단면 타이밍에 기여하지 못하면서 식별 단서만 준다.
IDENTITY_COLUMNS = ("Log_MarketCap", "Beta", "Sector_code", "Industry_code")

NORMALIZED_FEATURE_COLUMNS = [
    c for c in CURRENT_FEATURE_COLUMNS if c not in IDENTITY_COLUMNS
]


def normalize_price_features(feats: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """가격 단위 피처를 종가로 나눠 무차원으로 만든다.

    2026-08-23 측정: 정규화하고 식별 컬럼을 빼자 IC 가 0.102 → 0.049,
    10분위 스프레드가 2.40% → 0.67% 로 떨어졌다. 사라진 절반은 실력이 아니라
    "학습 구간에서 오른 종목군을 외운 것"이었다.
    """
    out = feats.copy()
    denom = close.reindex(out.index).replace(0, np.nan)
    for col in PRICE_SCALED_COLUMNS:
        if col in out.columns:
            out[col] = out[col] / denom
    return out

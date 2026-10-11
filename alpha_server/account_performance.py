"""에이전트 성과판: 넣은 돈 대비 얼마 벌었나, 같은 돈을 그냥 샀으면 얼마였나 (조회 전용).

대상은 실계좌 '새 돈만'(sleeve) 그림자 계좌다. 원금(principal)은 입출금으로만 바뀐다.
  - 손익: 지금 평가액 − 넣은 돈(순입금 누계).
  - 시간가중수익률(TWR): 입출금 날을 경계로 기간을 끊어 수익률만 곱한다 — 언제 얼마를 넣었는지와 무관한 운용 실력.
  - 비교 기준: 같은 날 같은 금액을 ① SPY 100% ② SPY 60 / IEF 40 사서 그대로 들고 있었다면 ③ 연 3% 예금이었다면.
  - 비용: 체결된 주문 금액 × 모의계좌 수수료율(autopilot.account.FEE_RATE).

입출금 이력은 mirror 상태의 "flows"(연동이 입출금을 반영할 때마다 한 줄)에서 읽는다. 그 기록이 생기기 전의
입출금은 알 수 없으므로, 원금 − 기록된 입출금을 시작일에 한 번에 넣은 것으로 추정한다(notes 에 적는다).

계산 일부가 실패해도 예외를 올리지 않는다. 되는 데까지 채우고 이유를 notes 에 남긴다.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

KST = timezone(timedelta(hours=9))
DEPOSIT_APR = 0.03            # 비교용 예금 금리(연)
START_BAND = 0.3              # 시작일 추정: 평가액이 원금의 ±30% 안에 들어온 구간부터를 이 계좌로 본다
PRICE_CACHE_SEC = 600

BENCHMARKS = (
    ("spy", "SPY만 샀다면", {"SPY": 1.0}),
    ("balanced", "SPY 60 / IEF 40 사서 그대로", {"SPY": 0.6, "IEF": 0.4}),
)

_price_cache: dict[tuple, tuple[float, object]] = {}


# ---------- 순수 계산 ----------

def _day(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(KST).date()
    text = str(value)
    if len(text) == 10:
        return date.fromisoformat(text)
    return _day(datetime.fromisoformat(text))


def time_weighted_return(points: list[tuple[date, float]], flows: list[tuple[date, float]]) -> Optional[float]:
    """일별 평가액(그날 마감, 입출금 반영 후)과 입출금으로 시간가중수익률(비율). 계산할 수 없으면 None.

    입출금은 그날 시작에 들어온 것으로 본다: 기간 수익률 = 오늘 평가액 / (어제 평가액 + 오늘 입출금).
    첫 평가 이전·당일의 입출금은 출발 금액이 된다.
    """
    points = sorted(points)
    if not points:
        return None
    flows = sorted(flows)
    growth, prev, i = 1.0, None, 0
    for day, value in points:
        moved = 0.0
        while i < len(flows) and flows[i][0] <= day:
            moved += flows[i][1]
            i += 1
        base = moved if prev is None else prev + moved
        if base > 0:
            growth *= value / base
        prev = value
    return growth - 1.0


def deposit_value(flows: list[tuple[date, float]], end: date, apr: float = DEPOSIT_APR) -> float:
    """같은 입출금을 연 apr 예금(일복리)에 넣었다면 end 의 잔액."""
    total = 0.0
    for day, amount in flows:
        days = max(0, (end - day).days)
        total += amount * (1 + apr) ** (days / 365.0)
    return total


def buy_and_hold_value(flows: list[tuple[date, float]], weights: dict[str, float],
                       price_on: Callable[[date], dict[str, float]], end: date) -> float:
    """같은 날 같은 금액을 weights 대로 사서 들고 있었다면 end 의 평가액(원). 리밸런싱 없음.

    출금은 그날 평가액 대비 비율만큼 모든 종목을 판다. 가격이 없으면 KeyError.
    """
    units: dict[str, float] = {t: 0.0 for t in weights}
    for day, amount in sorted(flows):
        prices = price_on(day)
        if amount >= 0:
            for t, w in weights.items():
                units[t] += amount * w / prices[t]
        else:
            value = sum(units[t] * prices[t] for t in units)
            share = min(1.0, -amount / value) if value > 0 else 1.0
            for t in units:
                units[t] *= 1.0 - share
    final = price_on(end)
    return sum(units[t] * final[t] for t in units)


def estimate_start(series: dict[str, float], principal: float, flows: list[tuple[date, float]]) -> Optional[date]:
    """시작일 기록이 없을 때: 평가액이 그날까지의 넣은 돈과 ±START_BAND 안에 있는 마지막 연속 구간의 첫날.

    실측(10/11): 같은 my-kb 이름으로 예전 방식(보유 종목 통째 운용) 평가액 360만원대가 남아 있고,
    '다시 맞추기'는 seeded_at 을 매번 덮어써 시작일로 쓸 수 없다.
    """
    days = sorted(series)
    if not days or principal <= 0:
        return None
    start = None
    for d in reversed(days):
        day = date.fromisoformat(d)
        # 그날까지 넣은 돈 = 원금 − 그 뒤에 들어온 입출금
        invested = principal - sum(a for f, a in flows if f > day)
        if invested <= 0 or abs(series[d] / invested - 1) > START_BAND:
            break
        start = day
    return start


def order_costs(orders: list[dict], start: Optional[date], fee_rate: float) -> dict:
    """체결된 주문 금액 × 수수료율. 실주문과 '기록만' 주문을 따로 센다."""
    live = recorded = 0.0
    n_live = n_recorded = 0
    for o in orders:
        if o.get("status") != "success":
            continue
        try:
            if start and _day(o.get("at")) < start:
                continue
        except (TypeError, ValueError):
            continue
        amount = abs(float(o.get("amount_krw") or 0))
        if o.get("dry_run", True):
            recorded += amount
            n_recorded += 1
        else:
            live += amount
            n_live += 1
    return {"fee_rate": fee_rate,
            "live_orders": n_live, "live_amount": round(live), "live_cost": round(live * fee_rate),
            "recorded_orders": n_recorded, "recorded_amount": round(recorded),
            "recorded_cost": round(recorded * fee_rate)}


def _pct(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value * 100, 2)


# ---------- 가격 ----------

def _load_frames(tickers: list[str], start: date) -> dict:
    """SPY·IEF 일봉. 로컬 CSV 가 시작일부터 최근까지 덮으면 쓰고, 아니면 받는다(1년, 10분 캐시)."""
    from .data_handler import download_many, load_from_csv

    key = ("frames", tuple(sorted(tickers)), start.isoformat())
    hit = _price_cache.get(key)
    if hit and time.time() - hit[0] < PRICE_CACHE_SEC:
        return hit[1]
    today = datetime.now(KST).date()
    out, missing = {}, []
    for t in tickers:
        df = load_from_csv(t)
        ok = df is not None and not df.empty and "Close" in df.columns
        if ok:
            first, last = df.index.min(), df.index.max()
            ok = first.date() <= start - timedelta(days=5) and (today - last.date()).days <= 4
        if ok:
            out[t] = df
        else:
            missing.append(t)
    if missing:
        got = download_many(missing, period="1y" if (today - start).days < 300 else "5y")
        out.update({t: f for t, f in got.items() if f is not None and not f.empty})
    _price_cache[key] = (time.time(), out)
    return out


def _load_rates(start: date):
    from .autopilot import fx

    key = ("fx", start.isoformat())
    hit = _price_cache.get(key)
    if hit and time.time() - hit[0] < PRICE_CACHE_SEC:
        return hit[1]
    now = datetime.now(timezone.utc)
    rates = fx.usd_krw_series(datetime.combine(start - timedelta(days=10), datetime.min.time(), timezone.utc),
                              now + timedelta(days=1))
    if len(rates) == 0:   # 이력이 비면 고정값 대신 최신 환율 한 값
        rates = fx.usd_krw_rate(None)
    _price_cache[key] = (time.time(), rates)
    return rates


def price_source(tickers: list[str], start: date) -> Callable[[date], dict[str, float]]:
    """날짜 → {티커: 원화 가격}. 그 날(한국) 아침에 알 수 있는 마지막 종가(전날 미국 종가)와 그때 환율."""
    from .autopilot.prices import HistoricalPrices

    hp = HistoricalPrices(_load_frames(tickers, start), rates=_load_rates(start))

    def price_on(day: date) -> dict[str, float]:
        at = datetime.combine(day, datetime.min.time(), KST).astimezone(timezone.utc)
        got = hp.get_many(tickers, at)
        missing = [t for t in tickers if t not in got]
        if missing:
            raise KeyError(f"{day} {', '.join(missing)} 가격 없음")
        return got

    return price_on


# ---------- 조립 ----------

def _flows(cfg: dict, state: dict, start: Optional[date], notes: list[str]) -> tuple[list[tuple[date, float]], str]:
    """(입출금 목록, 출처). 기록된 입출금 + 원금에서 나머지를 시작일 입금으로 추정."""
    principal = float(cfg.get("principal") or 0)
    if principal <= 0:
        notes.append("넣은 돈(원금) 기록이 없습니다 — '새 돈만' 운용에서만 계산합니다")
        return [], "none"
    logged = []
    for f in state.get("flows") or []:
        try:
            logged.append((_day(f["at"]), float(f["amount"])))
        except (KeyError, TypeError, ValueError):
            continue
    if start:
        logged = [(d, a) for d, a in logged if d >= start]
    initial = principal - sum(a for _, a in logged)
    first = start or (min(d for d, _ in logged) if logged else None)
    flows = sorted(logged)
    if first is not None and abs(initial) >= 1:
        flows.insert(0, (first, initial))
    if not logged:
        notes.append("입출금 기록이 없어 원금 전체를 시작일에 넣은 것으로 계산했습니다")
        return flows, "estimated"
    return flows, "log"


def compute(username: str, portfolio: str, *, now: Optional[datetime] = None,
            value_fn: Optional[Callable[[object], Optional[float]]] = None,
            price_on: Optional[Callable[[date], dict[str, float]]] = None) -> dict:
    """성과판 한 장. value_fn(account) → 지금 평가액, price_on(date) → 비교 가격 — 둘 다 테스트 주입용."""
    from .autopilot import mirror
    from .autopilot import store as ap_store
    from .autopilot.account import FEE_RATE

    now = now or datetime.now(timezone.utc)
    today = _day(now)
    notes: list[str] = []
    cfg = ap_store.load_config(username, portfolio)
    out: dict = {"exists": bool(cfg.get("shadow_of")), "portfolio": portfolio, "as_of": now.isoformat(),
                 "notes": notes}
    if not out["exists"]:
        notes.append("에이전트가 아직 시작되지 않았습니다")
        return out

    principal = float(cfg.get("principal") or 0)
    state = mirror.load_state(username, portfolio)
    series = ap_store.load_equity(username, portfolio)
    out.update(principal=round(principal), sleeve=bool(cfg.get("sleeve")))

    # 시작일
    start = None
    if cfg.get("started_at"):
        try:
            start = _day(cfg["started_at"])
        except ValueError:
            pass
    if start is None:
        raw_flows = []
        for f in state.get("flows") or []:
            try:
                raw_flows.append((_day(f["at"]), float(f["amount"])))
            except (KeyError, TypeError, ValueError):
                continue
        start = estimate_start(series, principal, raw_flows)
        if start is None and cfg.get("seeded_at"):
            start = _day(cfg["seeded_at"])
        if start is not None:
            notes.append("시작일 기록이 없어 평가액 기록으로 추정했습니다")
    out["start_date"] = start.isoformat() if start else None

    flows, source = _flows(cfg, state, start, notes)
    out["flows"] = [{"date": d.isoformat(), "amount": round(a)} for d, a in flows]
    out["flows_source"] = source

    # 지금 평가액
    value = None
    account, _ = ap_store.load_account(username, portfolio)
    try:
        if account is not None:
            value = value_fn(account) if value_fn else _live_value(account)
    except Exception as exc:
        notes.append(f"현재 시세 조회 실패({type(exc).__name__}) — 마지막 기록 평가액을 씁니다")
    if value is None and series:
        value = float(series[max(series)])
    out["value"] = round(value) if value is not None else None
    if value is not None and principal:
        out["pnl"] = round(value - principal)
        out["pnl_pct"] = round((value / principal - 1) * 100, 2)
    else:
        out["pnl"] = out["pnl_pct"] = None
        if value is None:
            notes.append("평가액을 알 수 없습니다")

    # 시간가중수익률
    points = [(date.fromisoformat(d), float(v)) for d, v in series.items() if not start or date.fromisoformat(d) >= start]
    points = [p for p in points if p[0] < today]
    if value is not None:
        points.append((today, value))
    twr = time_weighted_return(points, flows) if points and flows else None
    out["twr_pct"] = _pct(twr)
    if twr is None:
        notes.append("시간가중수익률을 계산할 평가액 기록이 부족합니다")

    # 비교 기준
    benchmarks = []
    if flows:
        try:
            if price_on is None:
                price_on = price_source(sorted({t for _, _, w in BENCHMARKS for t in w}), flows[0][0])
        except Exception as exc:
            notes.append(f"비교용 시세를 불러오지 못했습니다({type(exc).__name__})")
            price_on = None
        for key, label, weights in BENCHMARKS:
            row = {"key": key, "label": label}
            if price_on is None:
                row["error"] = "시세 없음"
            else:
                try:
                    row.update(_bench_row(buy_and_hold_value(flows, weights, price_on, today), principal))
                except Exception as exc:
                    row["error"] = str(exc) if isinstance(exc, KeyError) else type(exc).__name__
                    notes.append(f"{label}: 계산 실패({row['error']})")
            benchmarks.append(row)
        benchmarks.append({"key": "deposit", "label": f"연 {DEPOSIT_APR:.0%} 예금이었다면",
                           **_bench_row(deposit_value(flows, today), principal)})
    elif source != "none":
        notes.append("넣은 돈이 없어 비교할 수 없습니다")
    out["benchmarks"] = benchmarks

    out["costs"] = order_costs(state.get("orders") or [], start, FEE_RATE)
    return out


def _bench_row(value: float, principal: float) -> dict:
    return {"value": round(value), "pnl": round(value - principal),
            "pnl_pct": round((value / principal - 1) * 100, 2) if principal else None}


def _live_value(account) -> float:
    from .autopilot.prices import LivePrices

    prices = LivePrices().get_many(list(account.positions), datetime.now(timezone.utc)) if account.positions else {}
    missing = [t for t in account.positions if t not in prices]
    if missing:
        raise LookupError(", ".join(missing))
    return account.equity(prices)


def performance(username: str, portfolio: str, **kwargs) -> dict:
    """API 진입점 — 무엇이 터져도 결과를 돌려준다."""
    try:
        return compute(username, portfolio, **kwargs)
    except Exception as exc:
        return {"exists": None, "portfolio": portfolio, "notes": [f"성과 계산 실패: {type(exc).__name__}: {exc}"]}

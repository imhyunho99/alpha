"""업비트 KRW 마켓 일봉 수집 — 알트코인 주간 전략 백테스트용(키 불필요, 공개 API).

- 현재 상장 종목: 업비트 공개 캔들 API(일봉, 거래대금 포함).
- 상장폐지 종목: 업비트는 과거 시세를 주지 않는다(404). 공지(거래지원 종료)에서 종목과 종료일을 뽑고,
  바이낸스 공개 아카이브(data.binance.vision, USDT 일봉) × 업비트 USDT/KRW 로 근사한다.
  바이낸스에 없는 국내 전용 코인은 'missing' 으로 남긴다 — 백테스트가 보수적으로 처리한다.

저장: ~/AlphaModels/upbit/{KRW-XXX}.csv, delisted.json
실행: python scripts/upbit_history.py [--since 2021-01-01]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

API = "https://api.upbit.com/v1"
NOTICES = "https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade"
BINANCE = "https://data.binance.vision/data/spot/monthly/klines/{s}USDT/1d/{s}USDT-1d-{ym}.zip"
OUT = os.path.expanduser("~/AlphaModels/upbit")
UA = {"User-Agent": "alpha-research/1.0"}
FIELDS = ["date", "open", "high", "low", "close", "value_krw", "value_binance_krw", "source"]


def _get(url: str, retries: int = 5):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.0 + i)
                continue
            raise
        except urllib.error.URLError:
            time.sleep(1.0 + i)
    raise RuntimeError(f"요청 실패: {url}")


def krw_markets() -> list[str]:
    data = json.loads(_get(f"{API}/market/all"))
    return sorted(m["market"] for m in data if m["market"].startswith("KRW-"))


def upbit_days(market: str, since: date) -> list[dict]:
    rows, to = [], None
    while True:
        url = f"{API}/candles/days?market={market}&count=200" + (f"&to={to}" if to else "")
        batch = json.loads(_get(url))
        time.sleep(0.12)                      # 시세 API 초당 10회
        if not batch:
            break
        for c in batch:
            rows.append({"date": c["candle_date_time_kst"][:10], "open": c["opening_price"],
                         "high": c["high_price"], "low": c["low_price"], "close": c["trade_price"],
                         "value_krw": round(c["candle_acc_trade_price"]), "value_binance_krw": "",
                         "source": "upbit"})
        oldest = batch[-1]["candle_date_time_utc"]
        if len(batch) < 200 or oldest[:10] <= since.isoformat():
            break
        to = oldest + "Z"
    return sorted((r for r in rows if r["date"] >= since.isoformat()), key=lambda r: r["date"])


def delistings() -> list[dict]:
    notices, p = [], 1
    while True:
        d = json.loads(_get(NOTICES.format(p=p)))["data"]
        notices += d["notices"]
        if p >= d["total_pages"]:
            break
        p += 1
        time.sleep(0.25)
    out = []
    for n in notices:
        t = n["title"]
        if "거래지원 종료" not in t or any(w in t for w in ("연기", "철회", "취소")):
            continue
        announced = n["first_listed_at"][:10]
        m = re.search(r"\((\d{1,2})/(\d{1,2})", t)                      # "(12/14 15:00)" — 종료일
        end = None
        if m:
            y = int(announced[:4])
            end = date(y, int(m.group(1)), int(m.group(2)))
            if end.isoformat() < announced:
                end = date(y + 1, end.month, end.day)
        for sym in re.findall(r"\(([A-Z0-9]{2,10})\)", t):
            out.append({"symbol": sym, "announced": announced, "end": end.isoformat() if end else None,
                        "title": t, "krw": "KRW" in t or "마켓" not in t})
    return out


def binance_days(sym: str, start: date, end: date) -> list[dict]:
    rows, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        ym = f"{cur.year}-{cur.month:02d}"
        try:
            raw = _get(BINANCE.format(s=sym, ym=ym), retries=2)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for line in z.read(z.namelist()[0]).decode().splitlines():
                    f = line.split(",")
                    ts = int(f[0]) // (1000 if len(f[0]) <= 13 else 1_000_000)
                    rows.append({"date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), "open": float(f[1]),
                                 "high": float(f[2]), "low": float(f[3]), "close": float(f[4]),
                                 "value_usdt": float(f[7])})
        except (urllib.error.HTTPError, RuntimeError, zipfile.BadZipFile):
            pass
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return rows


def _save(market: str, rows: list[dict]) -> None:
    with open(os.path.join(OUT, f"{market}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--delisted-only", action="store_true")
    args = ap.parse_args()
    since = date.fromisoformat(args.since)
    os.makedirs(OUT, exist_ok=True)

    markets = krw_markets()
    print(f"현재 KRW 마켓 {len(markets)}종목 수집…", flush=True)
    for i, m in enumerate([] if args.delisted_only else markets, 1):
        _save(m, upbit_days(m, since))
        if i % 25 == 0:
            print(f"  {i}/{len(markets)}", flush=True)

    # 업비트 USDT 마켓은 2023 하반기부터 — 그 전은 원/달러 환율(김치프리미엄 없는 근사)로 메운다
    import yfinance as yf

    fx_usd = yf.download("KRW=X", start=since.isoformat(), progress=False, auto_adjust=True)["Close"].squeeze()
    usdt = {d.date().isoformat(): float(v) for d, v in fx_usd.dropna().items()}
    usdt.update({r["date"]: r["close"] for r in upbit_days("KRW-USDT", since)})
    days = sorted(usdt)
    listed = {m.split("-")[1] for m in markets}
    events = [e for e in delistings() if e["krw"] and e["symbol"] not in listed and e["end"]
              and e["end"] >= since.isoformat()]
    print(f"상장폐지 {len(events)}종목 — 바이낸스 아카이브로 근사…", flush=True)
    for e in events:
        end = date.fromisoformat(e["end"])
        rows = binance_days(e["symbol"], max(since, end - timedelta(days=1100)), end)
        out = []
        for r in rows:
            fx = usdt.get(r["date"]) or next((usdt[d] for d in reversed(days) if d < r["date"]), None)
            if fx and r["date"] < e["end"]:
                out.append({"date": r["date"], **{k: r[k] * fx for k in ("open", "high", "low", "close")},
                            # 거래대금은 업비트가 아니라 바이낸스 값 — 유니버스 순위에 그대로 쓰면 안 된다(백테스트가 환산)
                            "value_krw": "", "value_binance_krw": round(r["value_usdt"] * fx),
                            "source": "binance_proxy"})
        e["rows"] = len(out)
        e["status"] = "proxy" if out else "missing"
        if out:
            _save(f"KRW-{e['symbol']}", out)
    with open(os.path.join(OUT, "delisted.json"), "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)
    got = sum(e["status"] == "proxy" for e in events)
    print(f"완료: 현재 {len(markets)} · 상장폐지 근사 {got}/{len(events)} (나머지는 missing)", flush=True)


if __name__ == "__main__":
    main()

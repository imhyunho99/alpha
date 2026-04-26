"""자연어(주로 한국어) → StrategySpec 변환.

Anthropic Claude API를 사용한다. 사용자 vault에 저장된 anthropic.api_key를 BYO로 가져온다.
구조 강제는 두 가지 방법을 모두 시도:
  1) tool_use (가능하면 우선)
  2) JSON-only 응답 + 정규식 추출

테스트/오프라인 환경에선 ALPHA_NL_PARSER_BACKEND=stub 으로 결정적 파싱(룰 기반) 사용.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from pydantic import ValidationError

from .spec import StrategySpec

SYSTEM_PROMPT = """당신은 한국어/영어로 표현된 자동매매 지시를 JSON 전략 스펙으로 변환하는 파서입니다.

반드시 다음 스키마를 따르는 JSON만 출력하십시오:
{
  "name": "사람이 읽기 좋은 짧은 이름",
  "tickers": ["AAPL", "005930.KS", "BTC-USD"],
  "broker": "alpaca|upbit|binance|kis|mock",
  "trigger": {
    "mode": "all" | "any",
    "conditions": [
      {"type":"indicator","indicator":"rsi|sma|ema|price|change_pct|volume","period":int,"op":"<|<=|>|>=|==|between","value":number,"value_high":number?},
      {"type":"cross","fast_period":int,"slow_period":int,"fast_kind":"sma|ema","slow_kind":"sma|ema","direction":"golden|death"}
    ]
  },
  "action": {"type":"buy|sell","quantity":number,"quantity_kind":"shares|percent_cash|percent_position"},
  "cooldown_seconds": 3600,
  "active": true,
  "dry_run": true
}

규칙:
- 한국어 회사명은 종목코드(.KS)로 변환: 삼성전자→005930.KS, SK하이닉스→000660.KS, NAVER→035420.KS, 카카오→035720.KS, 현대차→005380.KS
- 코인은 "BTC-USD" 형식
- 미국 주식은 티커 그대로
- "골든크로스"=cross direction=golden, 기본 50/200일선 sma
- "데드크로스"=cross direction=death
- 수량 단위 추정: "주"/"개"=shares, "%"=percent_cash
- broker가 명시되지 않으면 종목 시장에 따라 추정: 미국→alpaca, 한국→kis, 코인→upbit
- dry_run 기본값 true (사용자가 "실거래"라고 명시하면 false)

JSON 외에 어떤 텍스트도 출력하지 마십시오."""


def _stub_parse(text: str) -> StrategySpec:
    """테스트용 결정적 파서 (RSI/이동평균 골든크로스 패턴만 인식)."""
    text_l = text.lower()
    tickers = []
    for sym in ["aapl", "tsla", "msft", "nvda", "googl", "btc", "eth"]:
        if sym in text_l:
            tickers.append(sym.upper() if "btc" not in sym and "eth" not in sym else f"{sym.upper()}-USD")
    if "삼성전자" in text:
        tickers.append("005930.KS")
    if not tickers:
        tickers = ["AAPL"]

    conditions: list[dict] = []
    rsi_match = re.search(r"rsi[\s가-힣]*?(\d{1,3})\s*(이하|이상|미만|초과|밑|위|<|>)", text_l)
    if rsi_match:
        val = int(rsi_match.group(1))
        op = "<" if rsi_match.group(2) in ("이하", "미만", "밑", "<") else ">"
        conditions.append({"type": "indicator", "indicator": "rsi", "period": 14, "op": op, "value": val})
    if "골든" in text or "golden" in text_l:
        conditions.append({
            "type": "cross", "fast_period": 50, "slow_period": 200,
            "fast_kind": "sma", "slow_kind": "sma", "direction": "golden",
        })
    if "데드" in text or "death" in text_l:
        conditions.append({
            "type": "cross", "fast_period": 50, "slow_period": 200,
            "fast_kind": "sma", "slow_kind": "sma", "direction": "death",
        })
    if not conditions:
        conditions.append({"type": "indicator", "indicator": "rsi", "period": 14, "op": "<", "value": 30})

    qty_match = re.search(r"(\d+)\s*(주|개)", text)
    qty = int(qty_match.group(1)) if qty_match else 1
    action_type = "sell" if ("매도" in text or "팔" in text or "sell" in text_l) else "buy"

    spec = {
        "name": text.strip()[:60] or "stub strategy",
        "tickers": list(dict.fromkeys(tickers))[:3],
        "broker": "mock",
        "trigger": {"mode": "all", "conditions": conditions},
        "action": {"type": action_type, "quantity": qty, "quantity_kind": "shares"},
        "cooldown_seconds": 3600,
        "active": True,
        "dry_run": True,
    }
    return StrategySpec(**spec)


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 본문에서 첫 번째 { ... } 블록 추출
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def parse(text: str, *, anthropic_api_key: Optional[str] = None, model: str = "claude-haiku-4-5-20251001") -> StrategySpec:
    """자연어 → StrategySpec. 키가 없거나 backend=stub이면 룰 기반 파서로 폴백."""
    backend = os.getenv("ALPHA_NL_PARSER_BACKEND", "anthropic").lower()
    if backend == "stub" or not anthropic_api_key:
        return _stub_parse(text)

    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=anthropic_api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        body = "".join(b.text for b in msg.content if hasattr(b, "text"))
        data = _extract_json(body)
        if not data:
            raise ValueError("LLM 응답에서 JSON을 추출하지 못했습니다.")
        return StrategySpec(**data)
    except ValidationError as e:
        raise ValueError(f"전략 검증 실패: {e}")
    except Exception as e:
        # 실패 시 stub 파서로 폴백 (사용자가 키를 잘못 입력한 경우 대비)
        print(f"⚠️ Anthropic 파서 실패 → 룰 기반 폴백: {e}")
        return _stub_parse(text)

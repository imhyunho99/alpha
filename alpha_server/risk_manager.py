"""거래 위험 관리.

자동매매 루프에 통합되어 다음을 수행한다.
- 포지션 사이징: 자본의 X% 까지만 한 종목에 배치
- 손절매 / 익절: 평단 대비 -stop_loss% / +take_profit% 도달 시 강제 청산 신호
- 일일 한도: 하루 최대 N건 매수 / 손실 한도 초과 시 신규 진입 차단
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .brokers.base import BaseBroker

STATE_FILE = os.path.expanduser("~/AlphaModels/risk_state.json")


@dataclass
class RiskConfig:
    max_position_pct: float = float(os.getenv("ALPHA_RISK_MAX_POSITION_PCT", "0.10"))  # 10%
    stop_loss_pct: float = float(os.getenv("ALPHA_RISK_STOP_LOSS_PCT", "0.07"))  # -7%
    take_profit_pct: float = float(os.getenv("ALPHA_RISK_TAKE_PROFIT_PCT", "0.15"))  # +15%
    max_daily_buys: int = int(os.getenv("ALPHA_RISK_MAX_DAILY_BUYS", "10"))
    max_daily_loss_pct: float = float(os.getenv("ALPHA_RISK_MAX_DAILY_LOSS_PCT", "0.05"))  # -5%


@dataclass
class _DailyState:
    day: str = ""
    buys: int = 0
    realized_pnl: float = 0.0
    starting_equity: float = 0.0


@dataclass
class RiskManager:
    broker: BaseBroker
    config: RiskConfig = field(default_factory=RiskConfig)

    def __post_init__(self) -> None:
        self.state = self._load_state()

    # ---- state persistence ----
    def _load_state(self) -> _DailyState:
        today = date.today().isoformat()
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if raw.get("day") == today:
                    return _DailyState(**raw)
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        return _DailyState(day=today, starting_equity=self._equity())

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state.__dict__, f, indent=2)

    def _equity(self) -> float:
        snap = self.broker.get_portfolio()
        return float(snap.get("total_value") or snap.get("equity") or 0.0)

    # ---- decisions ----
    def position_size(self, ticker: str, price: float) -> int:
        """이 종목에 새로 매수할 수 있는 최대 수량(정수). 0이면 진입 금지."""
        if price <= 0:
            return 0
        equity = self._equity() or 1.0
        cap = equity * self.config.max_position_pct
        existing = self.broker.get_position(ticker)
        existing_value = (existing.quantity * price) if existing else 0.0
        remaining_cap = max(0.0, cap - existing_value)
        affordable = max(0.0, min(remaining_cap, self.broker.get_cash()))
        return int(affordable // price)

    def should_exit(self, ticker: str, current_price: float) -> Optional[str]:
        """보유 포지션이 stop-loss/take-profit 조건에 도달했는지 평가."""
        pos = self.broker.get_position(ticker)
        if not pos or pos.quantity <= 0 or pos.avg_price <= 0:
            return None
        ret = (current_price - pos.avg_price) / pos.avg_price
        if ret <= -self.config.stop_loss_pct:
            return "stop_loss"
        if ret >= self.config.take_profit_pct:
            return "take_profit"
        return None

    def can_buy(self) -> tuple[bool, str]:
        if self.state.buys >= self.config.max_daily_buys:
            return False, f"일일 매수 한도({self.config.max_daily_buys}건) 초과"
        if self.state.starting_equity > 0:
            equity = self._equity()
            day_return = (equity - self.state.starting_equity) / self.state.starting_equity
            if day_return <= -self.config.max_daily_loss_pct:
                return False, f"일일 손실 한도({self.config.max_daily_loss_pct:.0%}) 도달"
        return True, "ok"

    def record_buy(self) -> None:
        self.state.buys += 1
        self._save_state()

    def snapshot(self) -> dict:
        return {
            "config": self.config.__dict__,
            "state": self.state.__dict__,
            "current_equity": self._equity(),
        }

"""브로커 인터페이스 정의."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    ticker: str
    quantity: float
    avg_price: float


@dataclass
class OrderResult:
    status: str  # "success" | "error"
    message: str
    ticker: Optional[str] = None
    action: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class BaseBroker(ABC):
    """모든 브로커 어댑터가 구현해야 하는 표준 인터페이스."""

    @abstractmethod
    def get_current_price(self, ticker: str) -> Optional[float]: ...

    @abstractmethod
    def execute_order(self, ticker: str, action: str, quantity: float) -> dict: ...

    @abstractmethod
    def get_portfolio(self) -> dict: ...

    @abstractmethod
    def get_position(self, ticker: str) -> Optional[Position]: ...

    @abstractmethod
    def get_cash(self) -> float: ...

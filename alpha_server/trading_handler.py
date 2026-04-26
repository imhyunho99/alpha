"""호환성 유지용 얇은 래퍼.

기존 임포트 경로(`from .trading_handler import broker`)를 깨지 않으면서
실제 구현은 brokers 패키지에 위임한다.
"""
from .brokers import build_broker

broker = build_broker()

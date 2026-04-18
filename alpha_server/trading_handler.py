import json
import os
import yfinance as yf
from datetime import datetime

PORTFOLIO_FILE = os.path.expanduser("~/AlphaModels/paper_portfolio.json")

class MockBroker:
    """
    모의투자를 위한 가상 브로커 클래스.
    실제 Alpaca, 한국투자증권 등의 API를 연동할 수 있는 뼈대(Stub) 역할을 합니다.
    """
    def __init__(self):
        self.portfolio = None
        self.load_portfolio()

    def load_portfolio(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, 'r') as f:
                    self.portfolio = json.load(f)
            except:
                self._initialize_portfolio()
        else:
            self._initialize_portfolio()

    def _initialize_portfolio(self):
        self.portfolio = {
            "cash": 100000.0, # 초기 자본금 $100k
            "positions": {},  # ticker -> {'quantity': 0, 'avg_price': 0}
            "history": []
        }
        self.save_portfolio()

    def save_portfolio(self):
        os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(self.portfolio, f, indent=4)

    def get_current_price(self, ticker):
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
        except:
            pass
        return None

    def execute_order(self, ticker, action, quantity):
        """가상 주문 실행"""
        price = self.get_current_price(ticker)
        if not price:
            return {"status": "error", "message": f"{ticker}의 현재 가격을 가져올 수 없습니다."}

        if action == "buy":
            cost = price * quantity
            if self.portfolio["cash"] >= cost:
                self.portfolio["cash"] -= cost
                pos = self.portfolio["positions"].get(ticker, {"quantity": 0, "avg_price": 0.0})
                
                total_cost = (pos["quantity"] * pos["avg_price"]) + cost
                pos["quantity"] += quantity
                pos["avg_price"] = total_cost / pos["quantity"]
                
                self.portfolio["positions"][ticker] = pos
                self.portfolio["history"].append({
                    "date": datetime.now().isoformat(),
                    "ticker": ticker,
                    "action": "buy",
                    "quantity": quantity,
                    "price": price
                })
                self.save_portfolio()
                return {"status": "success", "message": f"{ticker} {quantity}주 매수 (체결가: {price:.2f})"}
            else:
                return {"status": "error", "message": "현금 잔고가 부족합니다."}

        elif action == "sell":
            pos = self.portfolio["positions"].get(ticker)
            if pos and pos["quantity"] >= quantity:
                revenue = price * quantity
                self.portfolio["cash"] += revenue
                pos["quantity"] -= quantity
                
                if pos["quantity"] == 0:
                    del self.portfolio["positions"][ticker]
                else:
                    self.portfolio["positions"][ticker] = pos
                    
                self.portfolio["history"].append({
                    "date": datetime.now().isoformat(),
                    "ticker": ticker,
                    "action": "sell",
                    "quantity": quantity,
                    "price": price
                })
                self.save_portfolio()
                return {"status": "success", "message": f"{ticker} {quantity}주 매도 (체결가: {price:.2f})"}
            else:
                return {"status": "error", "message": "보유 수량이 부족합니다."}
        return {"status": "error", "message": "잘못된 주문 유형입니다."}

    def get_portfolio(self):
        # 현재 가치 계산
        total_value = self.portfolio["cash"]
        positions_value = 0.0
        
        for ticker, pos in self.portfolio["positions"].items():
            price = self.get_current_price(ticker)
            if price:
                positions_value += price * pos["quantity"]
                
        total_value += positions_value
        
        # 포트폴리오 스냅샷에 현재 총 자산 가치 추가
        snapshot = dict(self.portfolio)
        snapshot["total_value"] = total_value
        snapshot["unrealized_pnl"] = total_value - 100000.0 # 초기 자본금 기준
        
        return snapshot

broker = MockBroker()

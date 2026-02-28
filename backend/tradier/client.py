"""
Tradier API wrapper — orders + market data + history.
Form-encoded POST, whole shares only, custom __repr__ redacts auth.
"""

import logging
import time
from typing import Optional, Dict, List, Any
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("glyphTrader.tradier")
ET = ZoneInfo("America/New_York")

TRADIER_URLS = {
    "sandbox": "https://sandbox.tradier.com/v1",
    "production": "https://api.tradier.com/v1",
}


def _validate_quantity(quantity: Any, context: str = "") -> int:
    try:
        whole_qty = int(quantity)
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid quantity '{quantity}' for {context}: {e}")
    if whole_qty <= 0:
        raise ValueError(f"Quantity must be positive, got {whole_qty} for {context}")
    if isinstance(quantity, float) and quantity != whole_qty:
        logger.warning(f"WHOLE SHARES: Truncated {quantity} -> {whole_qty} for {context}")
    return whole_qty


class TradierClient:
    def __init__(self, api_token: str, account_number: str = "", environment: str = "sandbox"):
        self.api_token = api_token
        self.account_number = account_number
        base_url = TRADIER_URLS.get(environment)
        if not base_url:
            raise ValueError(f"Invalid environment: {environment}")
        self.base_url = base_url
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )

    def __repr__(self):
        return f"TradierClient(account='{self.account_number}', url='{self.base_url}')"

    def _request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        if data:
            data = {k: str(v) if v is not None else "" for k, v in data.items()}
        try:
            if method == "GET":
                resp = self.client.get(url, params=params)
            elif method == "POST":
                resp = self.client.post(url, data=data)
            elif method == "PUT":
                resp = self.client.put(url, data=data)
            elif method == "DELETE":
                resp = self.client.delete(url)
            else:
                raise ValueError(f"Unsupported method: {method}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Tradier API error: {method} {endpoint} -> {e.response.status_code}: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Tradier request failed: {method} {endpoint} -> {e}")
            raise

    # === Account ===

    def get_profile(self) -> Dict:
        return self._request("GET", "/user/profile")

    def get_balances(self) -> Dict:
        resp = self._request("GET", f"/accounts/{self.account_number}/balances")
        return resp.get("balances", {})

    def get_positions(self) -> List[Dict]:
        resp = self._request("GET", f"/accounts/{self.account_number}/positions")
        positions = resp.get("positions", {})
        if not positions or positions == "null":
            return []
        pos_list = positions.get("position", [])
        return [pos_list] if isinstance(pos_list, dict) else pos_list

    def get_orders(self, status: str = None) -> List[Dict]:
        resp = self._request("GET", f"/accounts/{self.account_number}/orders")
        orders = resp.get("orders", {})
        if not orders or orders == "null":
            return []
        order_list = orders.get("order", [])
        if isinstance(order_list, dict):
            order_list = [order_list]
        if status:
            order_list = [o for o in order_list if o.get("status") == status]
        return order_list

    def get_order(self, order_id: int) -> Dict:
        resp = self._request("GET", f"/accounts/{self.account_number}/orders/{order_id}")
        return resp.get("order", {})

    # === Orders ===

    def place_market_order(self, symbol: str, side: str, quantity: int, duration: str = "day") -> Dict:
        qty = _validate_quantity(quantity, f"market {side} {symbol}")
        data = {
            "class": "equity", "symbol": symbol.upper(), "side": side.lower(),
            "quantity": qty, "type": "market", "duration": duration.lower(),
        }
        logger.info(f"Market order: {side} {qty} {symbol}")
        resp = self._request("POST", f"/accounts/{self.account_number}/orders", data=data)
        return resp.get("order", {})

    def place_limit_order(self, symbol: str, side: str, quantity: int, price: float, duration: str = "gtc") -> Dict:
        qty = _validate_quantity(quantity, f"limit {side} {symbol} @ {price:.2f}")
        data = {
            "class": "equity", "symbol": symbol.upper(), "side": side.lower(),
            "quantity": qty, "type": "limit", "price": f"{price:.2f}", "duration": duration.lower(),
        }
        logger.info(f"Limit order: {side} {qty} {symbol} @ ${price:.2f}")
        resp = self._request("POST", f"/accounts/{self.account_number}/orders", data=data)
        return resp.get("order", {})

    def place_stop_order(self, symbol: str, side: str, quantity: int, stop_price: float, duration: str = "gtc") -> Dict:
        qty = _validate_quantity(quantity, f"stop {side} {symbol} @ {stop_price:.2f}")
        data = {
            "class": "equity", "symbol": symbol.upper(), "side": side.lower(),
            "quantity": qty, "type": "stop", "stop": f"{stop_price:.2f}", "duration": duration.lower(),
        }
        logger.info(f"Stop order: {side} {qty} {symbol} stop @ ${stop_price:.2f}")
        resp = self._request("POST", f"/accounts/{self.account_number}/orders", data=data)
        return resp.get("order", {})

    def place_oco_order(self, symbol: str, quantity: int, limit_price: float, stop_price: float, duration: str = "gtc") -> Dict:
        qty = _validate_quantity(quantity, f"OCO {symbol}")
        data = {
            "class": "oco", "duration": duration.lower(),
            "side[0]": "sell", "quantity[0]": qty, "type[0]": "limit",
            "price[0]": f"{limit_price:.2f}", "symbol[0]": symbol.upper(),
            "side[1]": "sell", "quantity[1]": qty, "type[1]": "stop",
            "stop[1]": f"{stop_price:.2f}", "symbol[1]": symbol.upper(),
        }
        logger.info(f"OCO order: {symbol} {qty}sh limit ${limit_price:.2f} / stop ${stop_price:.2f}")
        resp = self._request("POST", f"/accounts/{self.account_number}/orders", data=data)
        return resp.get("order", {})

    def cancel_order(self, order_id) -> Dict:
        logger.info(f"Cancelling order {order_id}")
        resp = self._request("DELETE", f"/accounts/{self.account_number}/orders/{order_id}")
        return resp.get("order", {})

    def modify_order(self, order_id, order_type=None, price=None, stop=None, duration=None) -> Dict:
        data = {}
        if order_type:
            data["type"] = order_type
        if price is not None:
            data["price"] = f"{price:.2f}"
        if stop is not None:
            data["stop"] = f"{stop:.2f}"
        if duration:
            data["duration"] = duration
        logger.info(f"Modifying order {order_id}: {data}")
        resp = self._request("PUT", f"/accounts/{self.account_number}/orders/{order_id}", data=data)
        return resp.get("order", {})

    # === Market Data ===

    def get_quotes(self, symbols: List[str]) -> List[Dict]:
        symbols_str = ",".join(symbols)
        resp = self._request("GET", "/markets/quotes", params={"symbols": symbols_str})
        quotes = resp.get("quotes", {})
        if not quotes or quotes == "null":
            return []
        quote_list = quotes.get("quote", [])
        return [quote_list] if isinstance(quote_list, dict) else quote_list

    def get_quote(self, symbol: str) -> Optional[Dict]:
        quotes = self.get_quotes([symbol])
        return quotes[0] if quotes else None

    def get_market_history(self, symbol: str, interval: str = "daily", start: str = None, end: str = None) -> List[Dict]:
        params = {"symbol": symbol, "interval": interval}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        resp = self._request("GET", "/markets/history", params=params)
        history = resp.get("history", {})
        if not history or history == "null":
            return []
        days = history.get("day", [])
        return [days] if isinstance(days, dict) else days

    def is_market_open(self) -> bool:
        resp = self._request("GET", "/markets/clock")
        clock = resp.get("clock", {})
        return clock.get("state") == "open"

    def get_market_calendar(self, month: int = None, year: int = None) -> List[Dict]:
        params = {}
        if month:
            params["month"] = month
        if year:
            params["year"] = year
        resp = self._request("GET", "/markets/calendar", params=params)
        return resp.get("calendar", {}).get("days", {}).get("day", [])

    # === Wait for Cancel ===

    def wait_for_cancel(self, order_id, max_wait: float = 5.0, poll_interval: float = 0.5) -> bool:
        elapsed = 0.0
        while elapsed < max_wait:
            order = self.get_order(order_id)
            status = order.get("status", "")
            if status in ("cancelled", "rejected", "expired"):
                return True
            time.sleep(poll_interval)
            elapsed += poll_interval
        logger.warning(f"Order {order_id} not cancelled after {max_wait}s — status: {order.get('status')}")
        return False

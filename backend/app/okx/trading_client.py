"""
OKX Trading Client for executing spot trades
Supports both production and sandbox (demo) environments
"""
from __future__ import annotations
import hmac
import base64
import hashlib
import time
from typing import Dict, Any, Optional
import httpx
from ..config import get_settings


class OKXTradingClient:
    """Client for OKX private trading API (spot orders)"""
    
    def __init__(self, api_key: str, secret_key: str, passphrase: str, sandbox: bool = False):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.sandbox = sandbox
        
        # Use same base URL but add x-simulated-trading header for sandbox
        self.base_url = "https://www.okx.com"
        
        print(f"[OKX Trading] Initialized in {'SANDBOX' if sandbox else 'PRODUCTION'} mode")
    
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Generate OKX API signature"""
        message = timestamp + method + path + body
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()
    
    def _get_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Generate request headers with authentication"""
        # OKX requires ISO 8601 timestamp in UTC (e.g., "2024-10-25T10:30:45.123Z")
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"
        sign = self._sign(timestamp, method, path, body)
        
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        
        # Add sandbox header if in demo mode
        if self.sandbox:
            headers["x-simulated-trading"] = "1"
        
        return headers
    
    def get_account_balance(self, ccy: Optional[str] = None) -> Dict[str, Any]:
        """
        Get account balance
        https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-balance
        """
        path = "/api/v5/account/balance"
        if ccy:
            path += f"?ccy={ccy}"
        
        headers = self._get_headers("GET", path)
        
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(self.base_url + path, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get("code") != "0":
                raise Exception(f"OKX API error: {result.get('msg', 'Unknown error')}")
            
            return result
    
    def place_spot_order(
        self,
        inst_id: str,
        side: str,
        sz: str,
        ord_type: str = "market",
        px: Optional[str] = None,
        td_mode: str = "cash",  # "cash" (spot), "cross" or "isolated" for margin
    ) -> Dict[str, Any]:
        """
        Place a spot order
        
        Args:
            inst_id: Trading pair (e.g., "BTC-USDT")
            side: "buy" or "sell"
            sz: Size to buy/sell (in base currency for spot)
            ord_type: "market" or "limit"
            px: Price (required for limit orders)
        
        Returns:
            Order response
        """
        path = "/api/v5/trade/order"
        
        order = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": sz,
            "tgtCcy": "base_ccy",  # sz is in base currency (BTC, ETH, etc.)
        }
        
        if ord_type == "limit":
            if not px:
                raise ValueError("Price (px) required for limit orders")
            order["px"] = px
        
        # Properly encode body as JSON for signature
        import json as json_module
        body = json_module.dumps(order)
        headers = self._get_headers("POST", path, body)
        
        print(f"[OKX Trading] Placing {side.upper()} order: {sz} {inst_id} ({ord_type})")
        
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(self.base_url + path, headers=headers, json=order)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get("code") != "0":
                error_msg = result.get("msg", "Unknown error")
                print(f"[OKX Trading] ✗ Order failed: {error_msg}")
                raise Exception(f"OKX order error: {error_msg}")
                raise Exception(f"OKX order error: {error_msg}")
            
            order_id = result["data"][0]["ordId"]
            print(f"[OKX Trading] ✓ Order placed successfully: {order_id}")
            
            return result
    
    def get_order_details(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        """Get order details by order ID"""
        path = f"/api/v5/trade/order?instId={inst_id}&ordId={ord_id}"
        headers = self._get_headers("GET", path)
        
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(self.base_url + path, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get("code") != "0":
                raise Exception(f"OKX API error: {result.get('msg', 'Unknown error')}")
            
            return result
    
    def cancel_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        """Cancel an order"""
        path = "/api/v5/trade/cancel-order"
        
        body_dict = {
            "instId": inst_id,
            "ordId": ord_id,
        }
        
        import json as json_module
        body = json_module.dumps(body_dict)
        headers = self._get_headers("POST", path, body)
        
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(self.base_url + path, headers=headers, json=body_dict)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get("code") != "0":
                raise Exception(f"OKX API error: {result.get('msg', 'Unknown error')}")
            
            return result

    def get_recent_fills(self, inst_type: str = "SPOT", limit: int = 20) -> Dict[str, Any]:
        """Fetch recent trade fills history from OKX.
        https://www.okx.com/docs-v5/en/#rest-api-trade-get-fills-history
        """
        # Use fills-history to include both recent and historical fills
        path = f"/api/v5/trade/fills-history?instType={inst_type}&limit={int(limit)}"
        headers = self._get_headers("GET", path)

        with httpx.Client(timeout=20.0) as client:
            resp = client.get(self.base_url + path, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != "0":
                raise Exception(f"OKX API error: {result.get('msg', 'Unknown error')}")
            return result

    def get_asset_valuation(self, ccy: str = "USD") -> Dict[str, Any]:
        """
        Get total asset valuation in a target currency (e.g., USD or USDT).
        https://www.okx.com/docs-v5/en/#rest-api-asset-get-asset-valuation
        """
        path = f"/api/v5/asset/asset-valuation?ccy={ccy}"
        headers = self._get_headers("GET", path)

        with httpx.Client(timeout=20.0) as client:
            resp = client.get(self.base_url + path, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != "0":
                raise Exception(f"OKX API error: {result.get('msg', 'Unknown error')}")
            return result


def get_trading_client() -> OKXTradingClient:
    """Factory function to create trading client from settings"""
    s = get_settings()
    
    # Sandbox mode if mode is not 'live' or if explicitly set
    sandbox = (s.mode != "live") or s.okx_use_sandbox
    
    if not s.okx_api_key or not s.okx_api_secret or not s.okx_api_passphrase:
        raise ValueError("OKX API credentials not configured. Please set OKX_API_KEY, OKX_API_SECRET, and OKX_API_PASSPHRASE in .env")
    
    return OKXTradingClient(
        api_key=s.okx_api_key,
        secret_key=s.okx_api_secret,
        passphrase=s.okx_api_passphrase,
        sandbox=sandbox
    )

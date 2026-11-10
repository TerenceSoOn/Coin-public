from __future__ import annotations
import httpx
from typing import Dict, List, Any
import time
from ..config import get_settings

_public_client = httpx.Client(timeout=20.0)

def _get_with_retries(url: str, params: Dict[str, Any] | None = None, max_retries: int = 3) -> httpx.Response:
    """HTTP GET with simple retry on timeouts, SSL errors, and 5xx."""
    import ssl
    backoff = 0.5
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = _public_client.get(url, params=params)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)
            return resp
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPStatusError, ssl.SSLError) as e:
            last_exc = e
            if attempt == max_retries:
                break
            time.sleep(backoff)
            backoff *= 2
        except Exception as e:
            # Catch any other SSL-related exceptions (e.g., SSLEOFError)
            last_exc = e
            if attempt == max_retries:
                break
            time.sleep(backoff)
            backoff *= 2
    if last_exc:
        raise last_exc
    raise RuntimeError("Unknown HTTP error without exception")

# Supported timeframes for multi-timeframe analysis
TIMEFRAMES = ["1m", "15m", "1H", "4H", "1D"]  # OKX uses 1H/4H/1D format


def _market_candles_url(base: str) -> str:
    return f"{base}/api/v5/market/candles"


def _tickers_url(base: str) -> str:
    return f"{base}/api/v5/market/tickers"


def get_recent_candles(inst_id: str, bar: str = "1m", limit: int = 10) -> List[List[str]]:
    """
    Returns list of candles, each: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    OKX returns newest first; we reverse to oldest->newest.
    """
    s = get_settings()
    params = {"instId": inst_id, "bar": bar, "limit": str(limit)}
    r = _get_with_retries(_market_candles_url(s.okx_base_url), params=params)
    r.raise_for_status()
    data = r.json()
    arr = data.get("data", [])
    arr.reverse()
    return arr


def get_multi_timeframe_candles(inst_id: str, limits: Dict[str, int] = None) -> Dict[str, List[List[str]]]:
    """
    Fetch candles for multiple timeframes at once.
    
    Args:
        inst_id: Symbol (e.g., 'BTC-USDT')
        limits: Dict of timeframe -> limit. Defaults to reasonable values for AI analysis.
    
    Returns:
        Dict mapping timeframe to candle data
    """
    if limits is None:
        limits = {
            "1m": 60,    # Last hour of 1m bars
            "15m": 96,   # Last day of 15m bars
            "1H": 168,   # Last week of 1h bars
            "4H": 180,   # Last month of 4h bars
            "1D": 90,    # Last 3 months of daily bars
        }
    
    result = {}
    for tf in TIMEFRAMES:
        limit = limits.get(tf, 100)
        try:
            result[tf] = get_recent_candles(inst_id, bar=tf, limit=limit)
        except Exception:
            result[tf] = []
    
    return result


def get_last_prices(symbols: List[str]) -> Dict[str, float]:
    s = get_settings()
    # Fetch all tickers for category=spot and filter
    url = _tickers_url(s.okx_base_url)
    try:
        r = _get_with_retries(url, params={"instType": "SPOT"})
        r.raise_for_status()
        data = r.json().get("data", [])
        mp: Dict[str, float] = {}
        want = set(symbols)
        for row in data:
            inst_id = row.get("instId")
            if inst_id in want:
                last = float(row.get("last", 0.0))
                mp[inst_id] = last
        return mp
    except Exception as e:
        print(f"[OKX Client] Failed to fetch last prices: {e}")
        # Return empty dict so caller can handle gracefully
        return {}


def get_tickers_map(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return ticker info for the given symbols with last price and 24h change pct.

    Shape: { symbol: { last: float, open24h: float, change_24h_pct: float } }
    """
    s = get_settings()
    url = _tickers_url(s.okx_base_url)
    try:
        r = _public_client.get(url, params={"instType": "SPOT"})
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as e:
        # On network/connection errors, return empty map so frontend doesn't crash
        print(f"[OKX Client] Failed to fetch tickers: {e}")
        return {}
    
    want = set(symbols)
    out: Dict[str, Dict[str, Any]] = {}
    for row in data:
        inst_id = row.get("instId")
        if inst_id not in want:
            continue
        try:
            last = float(row.get("last") or 0.0)
            open24h = float(row.get("open24h") or 0.0)
            change = 0.0
            if open24h > 0:
                change = (last - open24h) / open24h
            out[inst_id] = {"last": last, "open24h": open24h, "change_24h_pct": change}
        except Exception:
            # Skip malformed rows
            continue
    return out

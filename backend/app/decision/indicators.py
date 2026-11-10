"""
Technical indicators and price level calculations
"""
from typing import List, Dict, Any


def calculate_support_resistance(candles: List[List[str]], lookback: int = 20) -> Dict[str, float]:
    """
    Calculate support and resistance levels from recent candles.
    
    Args:
        candles: List of OKX candles [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        lookback: Number of recent candles to analyze
    
    Returns:
        Dict with 'support' and 'resistance' levels
    """
    if not candles or len(candles) < 3:
        return {"support": 0, "resistance": 0}
    
    # Use last N candles
    recent = candles[-lookback:] if len(candles) > lookback else candles
    
    # Extract highs and lows
    highs = [float(c[2]) for c in recent]  # c[2] = high
    lows = [float(c[3]) for c in recent]   # c[3] = low
    
    # Simple method: Find pivot points (local extremes)
    support_levels = []
    resistance_levels = []
    
    for i in range(1, len(lows) - 1):
        # Support: Local low (lower than neighbors)
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            support_levels.append(lows[i])
        
        # Resistance: Local high (higher than neighbors)
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            resistance_levels.append(highs[i])
    
    # If no pivots found, use simple min/max
    if not support_levels:
        support_levels = [min(lows)]
    if not resistance_levels:
        resistance_levels = [max(highs)]
    
    # Use the most recent/relevant levels
    support = max(support_levels[-3:]) if support_levels else min(lows)
    resistance = min(resistance_levels[-3:]) if resistance_levels else max(highs)
    
    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2)
    }


def calculate_multi_timeframe_levels(timeframes: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """
    Calculate support/resistance for multiple timeframes.
    
    Args:
        timeframes: Dict of timeframe -> {bars: [...]}
    
    Returns:
        Dict of timeframe -> {support, resistance}
    """
    levels = {}
    
    for tf, tf_data in timeframes.items():
        bars = tf_data.get("bars", [])
        if bars:
            levels[tf] = calculate_support_resistance(bars, lookback=20)
    
    return levels

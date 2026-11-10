# AI I/O Contract (What we send to AI, and what we expect back)

The AI makes a single decision per run. It does not know the execution mode (paper/sandbox/live). We send only market/news context and risk constraints; we never send secrets or mode flags.

## Input JSON (to AI)
```jsonc
{
  "timestamp": "ISO-8601",
  "symbols": ["BTC-USDT", "ETH-USDT"],
  "market": {
    "BTC-USDT": {
      "ohlcv_1m": [{"t": "ISO", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0}],
      "last_price": 0,
      "change_1m_pct": 0,
      "change_10m_pct": 0
    },
    "ETH-USDT": { /* same shape */ }
  },
  "positions": {
    // For spot: quantity and average cost; leverage field appears only if spot margin is enabled
    "BTC": {"qty": 0.0, "avg_cost": 0.0},
    "ETH": {"qty": 0.0, "avg_cost": 0.0}
  },
  "balances": {
    "USDT": 0.0
  },
  "risk_constraints": {
    "max_leverage": 3,          // Only applicable if spot margin is enabled; otherwise assume 1x
    "max_position_pct": 0.10,   // per asset cap vs equity (e.g., 10%)
    "max_total_exposure_pct": 0.20,
    "daily_loss_cap_pct": 0.02
  },
  "news_digest": {},
  "recent_decisions": [
    {
      "symbol": "BTC-USDT",
      "action": "buy|sell|hold",
      "size_usdt": 0,
      "leverage": 1,
      "ts": "ISO-8601"
    }
  ],
  "defaults": {
    "order_type": "market",
    "sl_pct": 0.01,
    "tp_pct": 0.02
  },
  "hints": {
    "target_interval_minutes": 10
  }
}
```

Notes:
- News/search is deferred for MVP; `news_digest` is empty for now.
- We may omit leverage entirely from inputs if spot margin is disabled; the model should assume 1× in that case.
- We clamp AI outputs to constraints. If it requests more than allowed, we scale down or reject.

## Output JSON (expected from AI)
```jsonc
{
  "decisions": [
    {
      "symbol": "BTC-USDT",
      "action": "buy|sell|hold",
      "order_type": "market|limit",
      "limit_price": 0,          // optional if order_type == "limit"
      "size_usdt": 0,            // preferred: size in USDT (we'll convert to qty)
      "leverage": 1,             // optional, only used if spot margin is enabled
      "sl_pct": 0.01,            // optional
      "tp_pct": 0.02             // optional
    },
    {
      "symbol": "ETH-USDT",
      "action": "buy|sell|hold",
      "order_type": "market|limit",
      "size_usdt": 0
    }
  ],
  "next_decision_in_minutes": 10,
  "rationale": "Short explanation of the logic used",
  "confidence": 0.0
}
```

Validation rules we enforce:
- Unknown symbols, invalid order types, negative sizes → reject or correct to hold
- Size cannot exceed per-asset and total exposure caps
- Leverage is ignored (or set to 1) if margin is disabled; otherwise clamped ≤ max_leverage
- If malformed or timeout → hold and alert, no trade executed

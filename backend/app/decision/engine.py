from __future__ import annotations
from threading import Lock
from typing import Dict, Any, List
from datetime import datetime, timezone
import copy
from ..config import get_settings
from ..okx.client import get_multi_timeframe_candles, get_last_prices, TIMEFRAMES
from ..ai.factory import AIProviderFactory
from ..db.repository import save_candles, save_decision, get_recent_decisions, save_equity_snapshot, get_trades
from .executor import execute_decisions
from ..okx.trading_client import get_trading_client
from .indicators import calculate_support_resistance
from .pipeline import PipelineCoordinator


_PIPELINE_LOCK = Lock()
_PIPELINE: PipelineCoordinator | None = None
_STATE_LOCK = Lock()
_LAST_PIPELINE_RUN: Dict[str, Any] | None = None
_RUN_COUNTER = 0


def _refresh_account_state(symbols: List[str]) -> Dict[str, Any]:
    """
    Refresh account balances and positions from OKX.
    Called before each symbol decision to see the impact of previous trades.
    """
    balances = {}
    positions = {}
    
    try:
        client = get_trading_client()
        balance_result = client.get_account_balance()
        
        if balance_result.get('code') == '0' and balance_result.get('data'):
            for account in balance_result['data']:
                for detail in account.get('details', []):
                    ccy = detail.get('ccy')
                    avail = float(detail.get('availBal', 0) or 0.0)
                    cash = float(detail.get('cashBal', 0) or 0.0)
                    equity = float(detail.get('eq', 0) or 0.0)
                    total = max(avail, cash, equity)
                    
                    # Skip if balance is zero or negative
                    if total <= 0 and avail <= 0:
                        continue
                    
                    balances[ccy] = total
        
        # Get last prices for position valuation
        last_prices = get_last_prices(symbols)
        
        # Extract crypto positions (non-USDT)
        for ccy, amount in balances.items():
            if ccy != 'USDT' and amount > 0:
                symbol = f"{ccy}-USDT"
                if symbol in last_prices:
                    value_usdt = amount * last_prices[symbol]
                    positions[symbol] = {
                        "amount": amount,
                        "value_usdt": value_usdt,
                        "side": "long"
                    }
    
    except Exception as e:
        print(f"[Engine] Warning: Could not refresh account state: {e}")
    
    return {
        "balances": balances,
        "positions": positions
    }


def build_ai_input() -> Dict[str, Any]:
    s = get_settings()
    symbols = s.symbols
    
    print(f"\n[Engine] Building AI input for {len(symbols)} symbols: {', '.join(symbols)}")

    # Fetch multi-timeframe candles and last prices
    all_candles = {}
    for sym in symbols:
        print(f"[Engine] Fetching multi-timeframe data for {sym}...")
        all_candles[sym] = get_multi_timeframe_candles(sym)
    
    last_prices = get_last_prices(symbols)
    print(f"[Engine] Got last prices: {last_prices}")

    # Save all timeframes to database
    saved_count = 0
    for sym, tf_data in all_candles.items():
        for tf, candle_data in tf_data.items():
            try:
                save_candles(sym, tf, candle_data)
                saved_count += 1
            except Exception as e:
                print(f"[Engine] Warning: Failed to save {sym} {tf} candles: {e}")
    
    print(f"[Engine] Saved {saved_count} candle datasets to database")

    # Build market summary with multi-timeframe analysis
    market: Dict[str, Any] = {}
    for sym in symbols:
        tf_candles = all_candles.get(sym, {})
        
        # Get 1m data for short-term changes
        candles_1m = tf_candles.get("1m", [])
        change_1m_pct = 0.0
        if len(candles_1m) >= 2:
            prev = float(candles_1m[-2][4])
            now = float(candles_1m[-1][4])
            if prev:
                change_1m_pct = (now - prev) / prev
        
        # Build multi-timeframe summary for AI
        timeframe_data = {}
        for tf in TIMEFRAMES:
            candles = tf_candles.get(tf, [])
            if not candles:
                continue
            
            # Calculate change for this timeframe
            change_pct = 0.0
            if len(candles) >= 2:
                first = float(candles[0][4])
                last = float(candles[-1][4])
                if first:
                    change_pct = (last - first) / first
            
            # Calculate support/resistance levels
            levels = calculate_support_resistance(candles, lookback=20)
            
            # Get recent bars (reduced to minimize token usage)
            # Focus on most recent data - older bars have diminishing value
            recent_count = {"1m": 20, "15m": 12, "1H": 12, "4H": 12, "1D": 7}.get(tf, 10)
            recent = candles[-recent_count:]
            
            # Send only essential OHLC data (skip volume and timestamp for AI)
            timeframe_data[tf] = {
                "bars": [
                    {"o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])}
                    for c in recent
                ],
                "change_pct": change_pct,
                "count": len(candles),
                "support": levels["support"],
                "resistance": levels["resistance"],
            }
        
        market[sym] = {
            "last_price": float(last_prices.get(sym, 0.0)),
            "change_1m_pct": change_1m_pct,
            "timeframes": timeframe_data,
        }

    # Fetch real account balances and positions
    balances: dict[str, float] = {}
    available_balances: dict[str, float] = {}
    positions = {}
    
    try:
        print(f"[Engine] Fetching account balances...")
        client = get_trading_client()
        balance_result = client.get_account_balance()
        
        if balance_result.get('code') == '0' and balance_result.get('data'):
            for account in balance_result['data']:
                for detail in account.get('details', []):
                    ccy = detail.get('ccy')
                    available = float(detail.get('availBal', 0) or 0.0)
                    cash_bal = float(detail.get('cashBal', 0) or 0.0)
                    equity_total = float(detail.get('eq', 0) or 0.0)
                    total_amount = max(available, cash_bal, equity_total)

                    if total_amount <= 0 and available <= 0:
                        continue

                    balances[ccy] = total_amount
                    available_balances[ccy] = available

                    if ccy == "USDT":
                        print(f"[Engine]   {ccy}: total={total_amount:.8f}, available={available:.8f}")
                    else:
                        print(f"[Engine]   {ccy}: {total_amount:.8f}")
        
        # Extract crypto positions (non-USDT balances)
        for ccy, amount in balances.items():
            if ccy != 'USDT' and amount > 0:
                # Calculate position value in USDT
                symbol = f"{ccy}-USDT"
                if symbol in last_prices:
                    value_usdt = amount * last_prices[symbol]
                    positions[symbol] = {
                        "amount": amount,
                        "value_usdt": value_usdt,
                        "side": "long"  # Holding the asset = long position
                    }
                    print(f"[Engine]   Position: {amount:.8f} {ccy} = ${value_usdt:.2f}")
        
    except Exception as e:
        print(f"[Engine] Warning: Could not fetch balances: {e}")
        print(f"[Engine] Using default balance (USDT: 0)")

    usdt_total = balances.get("USDT", 0.0)
    usdt_available = available_balances.get("USDT", usdt_total)

    payload = {
        "symbols": symbols,
        "market": market,
        "positions": positions,
        "balances": balances,
        "available_balances": available_balances,
        "risk_constraints": {
            "margin_enabled": s.margin_enabled,
            "max_leverage": s.max_leverage if s.margin_enabled else 1,
            "max_position_usd": s.max_position_usd,
            "max_total_exposure_usd": s.max_total_exposure_usd,
            "daily_loss_cap_usd": s.daily_loss_cap_usd,
        },
        "news_digest": {},  # omitted for now
        "recent_decisions": [],
        "defaults": {"order_type": "market", "sl_pct": 0.01, "tp_pct": 0.02},
        "hints": {
            "target_interval_minutes": s.ai_cadence_minutes,
            "short_selling": "Short selling (selling without holding) is technically possible but requires VERY high confidence (80%+). Only recommend when you see extremely strong bearish signals across multiple timeframes. Be conservative with shorts - they carry higher risk."
        },
    }
    
    print(f"[Engine] AI input prepared: {len(market)} symbols, {len(positions)} positions, {len(balances)} balances")
    return payload


def _legacy_run_decision_once() -> Dict[str, Any]:
    s = get_settings()
    print(f"\n[Engine] ========== Running AI Decision (mode={s.mode}, provider={s.ai_provider}) ==========")
    
    # Get shared context (balances, positions, risk constraints)
    shared_input = build_ai_input()
    symbols = shared_input.get("symbols", [])
    
    print(f"[Engine] Processing {len(symbols)} symbols individually...")

    # Pull a batch of recent decisions to give the AI short-term memory
    recent_history = []
    try:
        recent_history = get_recent_decisions(limit=20)
        print(f"[Engine] Loaded recent decision history: {len(recent_history)} entries")
    except Exception as e:
        print(f"[Engine] Warning: Could not load recent decisions: {e}")
    
    # Fetch recent executed trades for AI context
    recent_trades = []
    try:
        all_trades = get_trades()  # Get all trades
        recent_trades = all_trades[:10] if all_trades else []  # Last 10 trades
        print(f"[Engine] Loaded recent trade history: {len(recent_trades)} executed trades")
    except Exception as e:
        print(f"[Engine] Warning: Could not load recent trades: {e}")
    
    # Make separate AI decisions for each symbol
    # IMPORTANT: Process symbols sequentially - each decision should see the updated balance after previous trade
    all_decisions = []
    all_execution_results = []
    
    for symbol in symbols:
        print(f"\n[Engine] --- Analyzing {symbol} ---")
        
        # REFRESH balances and positions before each symbol decision
        # This ensures the AI sees the impact of previous trades
        print(f"[Engine] Refreshing account state before {symbol} decision...")
        refreshed_state = _refresh_account_state(s.symbols)
        
        # Update shared input with fresh balances and positions
        shared_input["balances"] = refreshed_state["balances"]
        shared_input["positions"] = refreshed_state["positions"]
        
        print(f"[Engine] Current balance: USDT=${refreshed_state['balances'].get('USDT', 0):.2f}")
        if refreshed_state["positions"]:
            for pos_sym, pos in refreshed_state["positions"].items():
                print(f"[Engine]   Position: {pos['amount']:.8f} {pos_sym.split('-')[0]} = ${pos['value_usdt']:.2f}")
        
        # Build input for this specific symbol
        symbol_input = {
            **shared_input,
            "symbols": [symbol],  # Single symbol only
            "market": {symbol: shared_input["market"].get(symbol, {})},  # This symbol's market data
        }

        # Attach recent decisions for this symbol (last few entries)
        per_symbol_recent: list[dict[str, Any]] = []
        try:
            for d in recent_history:
                try:
                    ai_in = d.ai_input or {}
                    ai_out = d.ai_output or {}
                    syms = ai_in.get("symbols", [])
                    if symbol in syms:
                        ts = d.timestamp.isoformat() if d.timestamp else None
                        actions = ai_out.get("actions") or ai_out.get("decisions") or []
                        exec_results = d.execution_results or []
                        
                        # Only keep minimal info per action (support both formats)
                        per_symbol_recent.append({
                            "timestamp": ts,
                            "actions": [
                                {
                                    "symbol": a.get("symbol"),
                                    "action": a.get("action"),
                                    "size_usd": a.get("size_usd"),
                                    "size_pct": a.get("size_pct"),
                                    "leverage": a.get("leverage", 1)
                                } for a in actions
                            ],
                            "confidence": ai_out.get("confidence"),
                            "execution_results": exec_results,  # Include execution outcomes
                        })
                except Exception:
                    continue
            # Limit to most recent 5 to keep prompt compact
            per_symbol_recent = per_symbol_recent[:5]
        except Exception as e:
            print(f"[Engine] Warning: Could not attach recent decisions for {symbol}: {e}")

        symbol_input["recent_decisions"] = per_symbol_recent
        
        # Attach recent executed trades for this symbol
        per_symbol_trades = []
        try:
            for trade in recent_trades:
                if trade.symbol == symbol:
                    per_symbol_trades.append({
                        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
                        "side": trade.side,
                        "amount": float(trade.base_amount) if trade.base_amount else 0,
                        "price": float(trade.price_usdt) if trade.price_usdt else 0,
                        "notional_usdt": float(trade.notional_usdt) if trade.notional_usdt else 0,
                        "order_id": trade.order_id,
                        "status": trade.status,
                    })
            # Limit to most recent 5
            per_symbol_trades = per_symbol_trades[:5]
            if per_symbol_trades:
                print(f"[Engine] Attached {len(per_symbol_trades)} recent trades for {symbol}")
        except Exception as e:
            print(f"[Engine] Warning: Could not attach recent trades for {symbol}: {e}")
        
        symbol_input["recent_trades"] = per_symbol_trades
        
        # Use factory to create provider instance
        try:
            print(f"[Engine] Creating AI provider: {s.ai_provider}")
            provider_kwargs = {}
            if s.ai_provider == "deepseek":
                provider_kwargs.update({
                    "api_key": s.deepseek_api_key,
                    "model": s.deepseek_model,
                })
            elif s.ai_provider == "gemini":
                provider_kwargs.update({
                    "api_key": s.gemini_api_key,
                    "model": s.gemini_model,
                })
            provider = AIProviderFactory.create(
                provider_name=s.ai_provider,
                **provider_kwargs,
            )
            
            print(f"[Engine] Calling AI for {symbol}...")
            ai_output = provider.decide(symbol_input)
            provider_name = provider.get_provider_name()
            
            print(f"[Engine] ✓ AI decision received for {symbol}:")
            print(f"[Engine]   - Confidence: {ai_output.get('confidence', 0):.2f}")
            print(f"[Engine]   - Actions: {len(ai_output.get('actions', []))}")
            print(f"[Engine]   - Rationale: {ai_output.get('rationale', 'N/A')[:100]}...")
            
        except Exception as e:
            print(f"[Engine] ✗ AI provider error for {symbol}: {e}")
            # Fallback if provider creation fails
            ai_output = {
                "actions": [],
                "next_decision_in_minutes": s.ai_cadence_minutes,
                "rationale": f"Provider error: {str(e)}",
                "confidence": 0.0,
            }
            provider_name = "error"

        # Enforce basic clamps (no margin if disabled)
        for action in ai_output.get("actions", []):
            if not s.margin_enabled:
                action["leverage"] = 1
            # Clamp size_usd (new format) or size_pct (legacy format)
            if action.get("size_usd", 0) < 0:
                action["size_usd"] = 0
            if action.get("size_pct", 0) < 0:
                action["size_pct"] = 0

        # Execute trades based on AI decision IMMEDIATELY
        # This ensures the next symbol sees updated balances
        execution_results = []
        try:
            print(f"[Engine] Executing trades for {symbol} before next symbol decision...")
            execution_results = execute_decisions(ai_output, symbol_input.get("market", {}))
            if execution_results:
                print(f"[Engine] ✓ Executed {len(execution_results)} actions for {symbol}")
                for result in execution_results:
                    status = result.get("status", "unknown")
                    action = result.get("action", "?")
                    print(f"[Engine]   - {symbol}: {action.upper()} → {status}")
            else:
                print(f"[Engine] No trades executed for {symbol} (likely HOLD or failed validation)")
        except Exception as e:
            print(f"[Engine] ✗ Trade execution error for {symbol}: {e}")

        # Save decision to database WITH execution results
        try:
            save_decision(provider_name, symbol_input, ai_output, execution_results)
            print(f"[Engine] ✓ Decision saved for {symbol} with execution results")
        except Exception as e:
            print(f"[Engine] ✗ Failed to save decision for {symbol}: {e}")
        
        # Store results
        all_decisions.append({
            "symbol": symbol,
            "input": symbol_input,
            "output": ai_output,
            "execution": execution_results
        })
        all_execution_results.extend(execution_results)
        
        # Log that execution is complete and next symbol will see fresh state
        if len(symbols) > 1 and symbol != symbols[-1]:
            print(f"[Engine] ✓ {symbol} processing complete. Next symbol will see updated balances.")
    
    print(f"\n[Engine] ========== All Decisions Complete ({len(symbols)} symbols) ==========\n")
    # Snapshot equity after decisions execute
    try:
        from ..okx.trading_client import get_trading_client
        client = get_trading_client()
        total = 0.0
        source = "okx"
        try:
            val = client.get_asset_valuation(ccy="USDT")
            if val.get("code") == "0" and val.get("data"):
                total = float(val["data"][0].get("totalBal", 0.0) or 0.0)
            else:
                raise ValueError("Unexpected valuation response")
        except Exception:
            # Fallback: compute from balances and last prices
            from ..okx.client import get_tickers_map
            tickers = get_tickers_map(symbols)
            last_prices = {k: v.get("last", 0.0) for k, v in tickers.items()}
            bal = client.get_account_balance()
            source = "computed"
            if bal.get("code") == "0" and bal.get("data"):
                for d in bal["data"][0].get("details", []):
                    ccy = d.get("ccy")
                    avail = float(d.get("availBal", 0) or 0)
                    if avail <= 0:
                        continue
                    if ccy == "USDT":
                        total += avail
                    else:
                        sym = f"{ccy}-USDT"
                        if sym not in last_prices:
                            extra = get_tickers_map([sym])
                            last_prices[sym] = extra.get(sym, {}).get("last", 0.0)
                        total += avail * (last_prices.get(sym, 0.0) or 0.0)
        save_equity_snapshot(mode=s.mode, total_value_usdt=total, source=source)
        print(f"[Engine] ✓ Equity snapshot saved: ${total:.2f} ({source})")
    except Exception as e:
        print(f"[Engine] Warning: Failed to snapshot equity: {e}")

    # Return aggregated results
    return {
        "input": shared_input,  # Full context
        "decisions": all_decisions,  # Per-symbol decisions
        "execution": all_execution_results  # All executions
    }


def _get_pipeline() -> PipelineCoordinator:
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None:
            _PIPELINE = PipelineCoordinator()
        return _PIPELINE


def reset_pipeline() -> None:
    """Reset cached pipeline so future runs pick up new settings."""
    global _PIPELINE
    with _PIPELINE_LOCK:
        _PIPELINE = None


def _record_pipeline_run(payload: Dict[str, Any]) -> None:
    global _LAST_PIPELINE_RUN
    with _STATE_LOCK:
        _LAST_PIPELINE_RUN = copy.deepcopy(payload)


def get_last_pipeline_run() -> Dict[str, Any] | None:
    with _STATE_LOCK:
        if _LAST_PIPELINE_RUN is None:
            return None
        return copy.deepcopy(_LAST_PIPELINE_RUN)


def run_decision_once(
    *,
    symbols: List[str] | None = None,
    force_strategist: bool = False,
) -> Dict[str, Any]:
    """Entry point used by API and scheduler to run the multi-role pipeline."""

    coordinator = _get_pipeline()
    pipeline_result = coordinator.run_once(symbols=symbols, force_strategist=force_strategist)
    decisions = pipeline_result.get("results", [])

    flattened_execution: List[Dict[str, Any]] = []
    for entry in decisions:
        execution = entry.get("execution") or {}
        orders = execution.get("orders") or []
        for order in orders:
            enriched = dict(order)
            enriched.setdefault("symbol", entry.get("symbol"))
            enriched.setdefault("plan_id", execution.get("plan_id"))
            enriched.setdefault("step_id", execution.get("step_id"))
            flattened_execution.append(enriched)

    with _STATE_LOCK:
        global _RUN_COUNTER
        _RUN_COUNTER += 1
        run_id = _RUN_COUNTER

    finished_at = datetime.now(timezone.utc).isoformat()
    result_payload = {
        "meta": {
            "run_id": run_id,
            "started_at": pipeline_result.get("started_at"),
            "finished_at": finished_at,
            "duration_seconds": pipeline_result.get("duration_seconds"),
            "symbols": pipeline_result.get("symbols"),
            "force_strategist": force_strategist,
        },
        "input": None,
        "decisions": decisions,
        "execution": flattened_execution,
        "pipeline": pipeline_result,
    }
    _record_pipeline_run(result_payload)
    return result_payload

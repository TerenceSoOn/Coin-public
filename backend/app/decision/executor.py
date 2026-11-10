"""
Trade execution module - converts AI decisions into actual OKX orders
"""
from __future__ import annotations
from typing import Dict, Any, List
from ..config import get_settings
from ..db.repository import save_trade
from ..okx.trading_client import get_trading_client


def execute_decisions(ai_output: Dict[str, Any], market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Execute AI trading decisions on OKX
    
    Args:
        ai_output: AI decision output with actions
        market_data: Current market data with prices
        
    Returns:
        List of execution results
    """
    s = get_settings()
    actions = ai_output.get("actions", [])
    
    if not actions:
        print("[Executor] No actions to execute")
        return []
    
    # Filter out HOLD actions
    tradeable_actions = [a for a in actions if a.get("action", "").lower() != "hold"]
    
    if not tradeable_actions:
        print("[Executor] All actions are HOLD - nothing to execute")
        return []
    
    print(f"\n[Executor] ========== Executing {len(tradeable_actions)} trades ==========")
    print(f"[Executor] Mode: {s.mode}")
    
    # For sandbox/live mode, execute real trades via OKX
    try:
        client = get_trading_client()
        results = []
        
        for action in tradeable_actions:
            try:
                result = _execute_trade(client, action, market_data)
                results.append(result)
                # Persist executed trade (approx price)
                try:
                    if result.get("status") == "executed":
                        side = result.get("side")
                        base_amt = result.get("size")
                        price = result.get("price") or 0.0
                        if base_amt and price:
                            save_trade(
                                mode=s.mode,
                                provider="ai",
                                symbol=result.get("symbol"),
                                side=side,
                                base_amount=base_amt,
                                price_usdt=price,
                                notional_usdt=base_amt * price,
                                leverage=action.get("leverage", 1),
                                order_id=result.get("order_id"),
                                status="executed",
                            )
                except Exception as e:
                    print(f"[Executor] Warning: failed to save executed trade: {e}")
            except Exception as e:
                print(f"[Executor] ✗ Failed to execute {action.get('symbol')}: {e}")
                results.append({
                    "symbol": action.get("symbol"),
                    "action": action.get("action"),
                    "status": "failed",
                    "error": str(e)
                })
        
        return results
        
    except Exception as e:
        print(f"[Executor] ✗ Failed to create trading client: {e}")
        return [{
            "status": "failed",
            "error": f"Trading client error: {str(e)}"
        }]


    # No simulation helpers; only OKX execution in sandbox/live


def _execute_trade(client, action: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a real trade via OKX"""
    symbol = action.get("symbol", "")
    trade_action = action.get("action", "").lower()
    
    # Support both size_usd (new) and size_pct (legacy) formats
    size_usd = action.get("size_usd", 0)
    size_pct = action.get("size_pct", 0) if size_usd == 0 else 0
    
    leverage = max(1, int(action.get("leverage", 1) or 1))
    
    # Get current price for reference
    price = market_data.get(symbol, {}).get("last_price", 0)
    
    if not price or price <= 0:
        raise Exception(f"Invalid price for {symbol}: {price}")
    
    # Convert action to OKX side
    side = "buy" if trade_action == "buy" else "sell"
    
    try:
        # Get account balances
        print(f"[Executor] Fetching account balance...")
        balance_data = client.get_account_balance()
        if balance_data.get("code") != "0":
            raise Exception(f"Failed to get balance: {balance_data.get('msg')}")

        details = balance_data.get("data", [{}])[0].get("details", [])
        # Helper to get available by currency symbol
        def _avail(ccy: str) -> float:
            d = next((x for x in details if x.get("ccy") == ccy), None)
            return float((d or {}).get("availBal", 0) or 0)

        base_ccy = symbol.split("-")[0]
        available_usdt = _avail("USDT")
        available_base = _avail(base_ccy)
        print(f"[Executor] Available: USDT=${available_usdt:.2f}, {base_ccy}={available_base:.8f}")

        # Determine order size depending on side
        min_order_value = 10.0  # $10 minimum notional for safety
        is_margin = get_settings().margin_enabled and leverage > 1
        # OKX requires "isolated" for margin trading on SPOT pairs (BTC-USDT, ETH-USDT)
        # "cross" requires Multi-currency margin mode which needs account setup
        td_mode = "isolated" if is_margin else "cash"

        if side == "buy":
            if not is_margin and available_usdt <= 0:
                raise Exception(f"Insufficient USDT balance: ${available_usdt:.2f}")
            
            # Calculate position value
            if size_usd > 0:
                # New format: direct dollar amount
                position_value = size_usd * (leverage if is_margin else 1)
            else:
                # Legacy format: percentage of balance
                base_value = available_usdt * (size_pct / 100.0)
                position_value = base_value * (leverage if is_margin else 1)
            
            if position_value < min_order_value:
                raise Exception(f"Position value ${position_value:.2f} below minimum ${min_order_value}")
            size = round(position_value / price, 8)
            if size <= 0:
                raise Exception(f"Calculated size too small: {size}")
        else:  # sell
            if not is_margin and available_base <= 0:
                raise Exception(f"No {base_ccy} available to sell; short selling not supported in spot mode")
            
            # Calculate size
            if size_usd > 0:
                # New format: direct dollar amount
                position_value = size_usd * (leverage if is_margin else 1)
                size = round(position_value / price, 8)
            elif is_margin:
                # Legacy format with margin
                base_value = available_usdt * (size_pct / 100.0)
                position_value = base_value * leverage
                size = round(position_value / price, 8)
            else:
                # Legacy format spot
                size = round(available_base * (size_pct / 100.0), 8)
            
            if size * price < min_order_value:
                raise Exception(f"Sell notional ${size*price:.2f} below minimum ${min_order_value}")
            if size <= 0:
                raise Exception(f"Calculated size too small: {size}")

        print(f"[Executor] Placing {side.upper()} order: {size} {base_ccy} @ ~${price:.2f} (${size*price:.2f}, tdMode={td_mode}, leverage={leverage}x)")
        
        # Place market order
        result = client.place_spot_order(
            inst_id=symbol,
            side=side,
            sz=str(size),
            ord_type="market",
            td_mode=td_mode
        )
        
        order_id = result["data"][0]["ordId"]
        print(f"[Executor] ✓ Order executed successfully: {order_id}")
        
        return {
            "symbol": symbol,
            "action": trade_action,
            "side": side,
            "size": size,
            "size_pct": size_pct,
            "price": price,
            "position_value": position_value,
            "order_id": order_id,
            "status": "executed"
        }
        
    except Exception as e:
        print(f"[Executor] ✗ Execution failed: {str(e)}")
        raise Exception(f"Order execution failed: {str(e)}")

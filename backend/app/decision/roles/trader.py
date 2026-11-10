from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from ..data_services import DataCollector
from ...config import get_settings
from ...ai.factory import AIProviderFactory
from ...db.repository import (
    get_latest_predictor_insight,
    get_latest_trader_memory,
    save_trader_decision,
    save_trader_memory,
    update_trader_decision_execution,
    get_latest_trader_decision,
)
from ...db.models import TraderDecision as TraderDecisionModel
from ...okx.trading_client import get_trading_client


class TraderRole:
    """Trader agent: consumes Predictor insight, short-term market data, memory and produces execution decisions."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.collector = DataCollector()

    def run(self, symbol: str, predictor_insight: Any | None = None, force: bool = False) -> TraderDecisionModel:
        """Run Trader for a single symbol.

        Args:
            symbol: trading pair (e.g., 'BTC-USDT')
            predictor_insight: optional PredictorInsight data object; if None, the latest persisted insight will be used
            force: if True, bypass caches (not used here yet)
        """
        self.settings = get_settings()
        now = datetime.now(timezone.utc)

        if predictor_insight is None:
            predictor_insight = get_latest_predictor_insight(symbol)
            if not predictor_insight:
                raise ValueError("No Predictor insight available for symbol; run predictor first")

        # Build short-term market envelope and account snapshot
        account_state = self.collector.build_account_state([symbol])
        market_env = self.collector.build_market_envelope([symbol])
        snapshot = market_env.snapshots.get(symbol)
        last_price = snapshot.last_price if snapshot else None

        # Fetch last memory
        last_memory = get_latest_trader_memory(symbol)
        last_memory_summary = last_memory.memory_summary if last_memory else None

        # Build provider payload
        payload: Dict[str, Any] = {
            "role": "trader",
            "timestamp": market_env.retrieved_at.isoformat(),
            "symbol": symbol,
            "predictor": {
                "overall_trend": predictor_insight.overall_trend,
                "trend_strength": predictor_insight.trend_strength,
                "next_24h_bias": predictor_insight.next_24h_bias,
                "next_4h_bias": predictor_insight.next_4h_bias,
                "support_levels": predictor_insight.support_levels,
                "resistance_levels": predictor_insight.resistance_levels,
                "confidence": predictor_insight.confidence,
                "reasoning": predictor_insight.reasoning,
            },
            "market": {
                symbol: {
                    "last_price": snapshot.last_price if snapshot else None,
                    "timeframes": {
                        tf: {
                            "change_pct": data.change_pct,
                            "support": data.support,
                            "resistance": data.resistance,
                            "bars": data.bars,
                        }
                        for tf, data in snapshot.timeframes.items()
                        if tf in ("1H", "15m", "5m")
                    },
                }
            },
            "holdings": {
                "balances": account_state.balances,
                "available_balances": account_state.available_balances,
                "positions": {
                    sym: {
                        "amount": pos.amount,
                        "value_usdt": pos.value_usdt,
                        "side": pos.side,
                    }
                    for sym, pos in account_state.positions.items()
                },
            },
            "value_change": {
                # provide simple P&L summary for the targeted symbol if present
                "symbol_value_usdt": (account_state.positions.get(symbol) or {}).get("value_usdt") if account_state.positions else None,
            },
            "recent_trades": account_state.recent_trades,
            "last_memory": last_memory_summary,
            "hints": {
                "task": (
                    "You are the Trader agent. Use the Predictor insight and recent 1H/15m/5m market context,\n"
                    "account holdings and recent trades to produce a single execution decision.\n"
                    "Return strict JSON with fields: action (buy|sell|hold), side (long|short), entry_price (null for market),\n"
                    "size_usd, leverage, stop_loss, take_profit, reason (short), confidence (0-1), memory_summary (concise summary for next call), invalidation_condition.\n"
                    "If action is 'hold', size_usd should be 0 and memory_summary should explain why.\n"
                ),
                "min_order_usd": getattr(self.settings, "max_position_usd", 10.0),
            },
        }

        provider = AIProviderFactory.create(self.settings.executor_provider or self.settings.ai_provider, api_key=None, model=self.settings.executor_model)
        print(f"[Trader] ▶ Calling provider={provider.get_provider_name()} model={getattr(provider, 'model', None)} for {symbol}...")

        # Use provider.decide for uniform behavior across provider implementations
        try:
            decision_raw = provider.decide(payload)
        except Exception as e:
            print(f"[Trader] Provider decide() failed: {e}")
            decision_raw = {"action": "hold", "size_usd": 0.0, "reason": f"Provider error: {e}", "confidence": 0.0, "memory_summary": "hold: provider error"}

        print(f"[Trader] Raw decision keys: {list(decision_raw.keys())}")

        # Normalize outputs
        action = (decision_raw.get("action") or "hold").lower()
        side = (decision_raw.get("side") or "long").lower()
        entry_price = decision_raw.get("entry_price")
        size_usd = float(decision_raw.get("size_usd") or 0.0)
        leverage = int(decision_raw.get("leverage") or 1)
        stop_loss = decision_raw.get("stop_loss")
        take_profit = decision_raw.get("take_profit")
        confidence = float(decision_raw.get("confidence") or 0.0)
        reason = decision_raw.get("reason") or ""
        invalidation_condition = decision_raw.get("invalidation_condition")
        memory_summary = decision_raw.get("memory_summary") or (f"Last action: {action} size=${size_usd:.2f} conf={confidence:.2f}")

        # Persist decision (status pending initially)
        record = save_trader_decision(
            symbol=symbol,
            predictor_insight_id=predictor_insight.id,
            provider=provider.get_provider_name(),
            model=getattr(provider, "model", None),
            action=action,
            side=side,
            entry_price=entry_price,
            size_usd=size_usd,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reason=reason,
            invalidation_condition=invalidation_condition,
            memory_summary=memory_summary,
            status="pending",
            raw_output=decision_raw,
            context_snapshot=payload,
        )

        # Save memory for next time
        try:
            save_trader_memory(symbol, memory_summary, decision_id=record.id)
        except Exception as e:
            print(f"[Trader] Failed to save memory: {e}")

        # Execute if actionable
        executed = False
        exec_result = None
        if action in {"buy", "sell"} and size_usd and size_usd >= float(self.settings.max_position_usd or 0):
            # Only execute when size meets configured minimums and account has capacity
            try:
                client = get_trading_client()
                if not last_price or last_price <= 0:
                    raise ValueError("Invalid last price for execution")
                # Compute base amount from USD size
                base_amount = size_usd / last_price
                sz = f"{base_amount:.8f}".rstrip('0').rstrip('.')
                # Market order
                resp = client.place_spot_order(inst_id=symbol, side=action, sz=sz, ord_type="market")
                exec_result = resp
                update_trader_decision_execution(record.id, "executed", resp)
                executed = True
                print(f"[Trader] Executed {action} for {symbol} size_usd={size_usd} base={sz}")
            except Exception as e:
                exec_result = {"error": str(e)}
                update_trader_decision_execution(record.id, "failed", exec_result)
                print(f"[Trader] Execution failed: {e}")
        else:
            # Not executing now: mark pending -> pending (no-op) or set to 'skipped'
            update_trader_decision_execution(record.id, "pending", {})

        # Return the persisted decision record (fresh from DB)
        latest = get_latest_trader_decision(symbol)
        return latest
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Any

from ..data_services import DataCollector
from ..models import PredictorInsightData, TraderDecisionData, AccountState
from ...config import get_settings
from ...ai.factory import AIProviderFactory
from ...db.repository import (
    save_trader_decision,
    save_trader_memory,
    get_latest_trader_memory,
    get_latest_predictor_insight,
    update_trader_decision_execution,
    save_decision,
)
from ...okx.trading_client import get_trading_client


class TraderRole:
    """
    Trader Agent: Execution-focused agent that combines Predictor insights with
    real-time market data, account state, and its own memory to make trade decisions.
    
    The Trader:
    - Sees 1H/15m/5m candle data for precise timing
    - Knows current holdings and available capital
    - Remembers its previous actions via memory_summary
    - Executes trades directly on OKX
    - Updates its memory for the next call
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.collector = DataCollector()

    def run(
        self,
        symbol: str,
        predictor_insight: PredictorInsightData | None = None,
        account_state: AccountState | None = None,
        *,
        force: bool = False,
    ) -> TraderDecisionData:
        """
        Generate and optionally execute a trading decision for the given symbol.
        
        Args:
            symbol: Trading pair (e.g., 'BTC-USDT')
            predictor_insight: Latest Predictor insight (fetched if not provided)
            account_state: Current account state (fetched if not provided)
            force: If True, always generate fresh decision
            
        Returns:
            TraderDecisionData with action, size, prices, and memory
        """
        self.settings = get_settings()
        
        # Fetch Predictor insight if not provided
        if predictor_insight is None:
            predictor_record = get_latest_predictor_insight(symbol)
            if not predictor_record:
                raise ValueError(f"No Predictor insight available for {symbol}. Run Predictor first.")
            
            predictor_insight = PredictorInsightData(
                id=predictor_record.id,
                symbol=predictor_record.symbol,
                provider=predictor_record.provider,
                model=predictor_record.model,
                generated_at=predictor_record.generated_at,
                expires_at=predictor_record.expires_at,
                overall_trend=predictor_record.overall_trend,
                trend_strength=predictor_record.trend_strength,
                next_24h_bias=predictor_record.next_24h_bias,
                next_4h_bias=predictor_record.next_4h_bias,
                support_levels=predictor_record.support_levels or [],
                resistance_levels=predictor_record.resistance_levels or [],
                confidence=predictor_record.confidence,
                reasoning=predictor_record.reasoning,
                news_context=predictor_record.news_context,
                raw_output=predictor_record.raw_output,
            )

        # Fetch account state if not provided
        if account_state is None:
            account_state = self.collector.build_account_state([symbol])

        # Fetch last memory
        memory_record = get_latest_trader_memory(symbol)
        last_memory = memory_record.memory_summary if memory_record else None

        print(f"[Trader] ▶ run(symbol={symbol}, force={force})")
        print(f"[Trader] Predictor insight: trend={predictor_insight.overall_trend}, 24h={predictor_insight.next_24h_bias}, conf={predictor_insight.confidence:.2f}")
        print(f"[Trader] Account: USDT={account_state.available_balances.get('USDT', 0):.2f}, holdings={list(account_state.positions.keys())}")
        if last_memory:
            print(f"[Trader] Last memory: {last_memory[:100]}...")

        # Build market envelope with 1H/15m/5m data
        market_env = self.collector.build_market_envelope([symbol])
        
        # Build prompt payload
        prompt_payload = self._build_prompt_payload(
            symbol=symbol,
            predictor_insight=predictor_insight,
            market_env=market_env,
            account_state=account_state,
            last_memory=last_memory,
        )

        # Call AI provider
        provider = self._make_provider(
            self.settings.tactician_provider,
            self.settings.tactician_model
        )
        prompt = self._build_trading_prompt(prompt_payload)
        print(f"[Trader] Prompt length: {len(prompt)} chars")
        
        response = provider.invoke_json(prompt, schema_hint=self._schema())
        print(f"[Trader] Response keys: {list(response.keys())}")

        # Parse decision
        decision = self._parse_decision(symbol, predictor_insight, response)
        print(f"[Trader] Decision: action={decision.action}, size=${decision.size_usd}, conf={decision.confidence:.2f}")

        # Save decision to database
        record = save_trader_decision(
            symbol=symbol,
            predictor_insight_id=predictor_insight.id,
            provider=decision.provider,
            model=decision.model,
            action=decision.action,
            side=decision.side,
            entry_price=decision.entry_price,
            size_usd=decision.size_usd,
            leverage=decision.leverage,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            confidence=decision.confidence,
            reason=decision.reason,
            invalidation_condition=decision.invalidation_condition,
            memory_summary=decision.memory_summary,
            status="pending",
            raw_output=decision.raw_output,
            context_snapshot=prompt_payload,
        )
        decision.id = record.id

        # Execute trade if not hold
        if decision.action.lower() != "hold":
            execution_result = self._execute_trade(decision, account_state)
            decision.execution_result = execution_result
            decision.status = "executed" if execution_result.get("success") else "failed"
            decision.executed_at = datetime.now(timezone.utc)
            
            # Update decision with execution result
            update_trader_decision_execution(
                decision_id=record.id,
                status=decision.status,
                execution_result=execution_result,
            )
            print(f"[Trader] Execution: status={decision.status}, result={execution_result.get('message', '')[:100]}")
        else:
            print(f"[Trader] Hold decision - no execution")

        # Save memory for next call
        if decision.memory_summary:
            save_trader_memory(
                symbol=symbol,
                memory_summary=decision.memory_summary,
                decision_id=record.id,
            )
            print(f"[Trader] Memory saved: {decision.memory_summary[:80]}...")

        # Save to decisions table for audit trail
        save_decision(
            provider=decision.provider,
            ai_input=prompt_payload,
            ai_output=decision.raw_output,
            execution_results=decision.execution_result if decision.action != "hold" else None,
            role="trader",
        )

        return decision

    def _make_provider(self, provider_name: str, model: str | None):
        """Create AI provider instance."""
        api_key = self._resolve_api_key(provider_name)
        return AIProviderFactory.create(provider_name, api_key=api_key, model=model)

    def _resolve_api_key(self, provider: str) -> str | None:
        """Resolve API key for the given provider."""
        if provider == "deepseek":
            return self.settings.deepseek_api_key
        if provider == "gemini":
            return self.settings.gemini_api_key
        raise ValueError(f"Unsupported trader provider: {provider}")

    def _build_prompt_payload(
        self,
        symbol: str,
        predictor_insight: PredictorInsightData,
        market_env,
        account_state: AccountState,
        last_memory: str | None,
    ) -> Dict[str, Any]:
        """Build payload for Trader AI prompt."""
        snapshot = market_env.snapshots.get(symbol)
        if not snapshot:
            raise ValueError(f"No market data for {symbol}")

        # Focus on 1H/15m/5m timeframes for execution timing
        timeframes_data = {}
        for tf in ["1H", "15m", "5m"]:
            if tf in snapshot.timeframes:
                tf_data = snapshot.timeframes[tf]
                timeframes_data[tf] = {
                    "change_pct": tf_data.change_pct,
                    "support": tf_data.support,
                    "resistance": tf_data.resistance,
                    "bars": tf_data.bars[-10:],  # Last 10 candles
                }

        # Calculate current holdings value
        position = account_state.positions.get(symbol)
        current_position_value = position.value_usdt if position else 0.0
        
        # Calculate value change (compare to last known price)
        value_change_pct = 0.0
        if position and len(account_state.recent_trades) > 0:
            # Find last trade for this symbol
            last_trade = next(
                (t for t in account_state.recent_trades if t.get("symbol") == symbol),
                None
            )
            if last_trade:
                last_price = last_trade.get("price_usdt", 0)
                if last_price > 0:
                    current_price = snapshot.last_price
                    value_change_pct = ((current_price - last_price) / last_price) * 100

        return {
            "role": "trader",
            "timestamp": market_env.retrieved_at.isoformat(),
            "symbol": symbol,
            "last_price": snapshot.last_price,
            "timeframes": timeframes_data,
            
            # Predictor insight
            "predictor": {
                "overall_trend": predictor_insight.overall_trend,
                "trend_strength": predictor_insight.trend_strength,
                "next_24h_bias": predictor_insight.next_24h_bias,
                "next_4h_bias": predictor_insight.next_4h_bias,
                "support_levels": predictor_insight.support_levels,
                "resistance_levels": predictor_insight.resistance_levels,
                "confidence": predictor_insight.confidence,
                "reasoning": predictor_insight.reasoning,
            },
            
            # Account state
            "available_usdt": account_state.available_balances.get("USDT", 0),
            "current_position": {
                "amount": position.amount if position else 0,
                "value_usdt": current_position_value,
                "value_change_pct": value_change_pct,
            },
            "recent_trades": account_state.recent_trades[-5:],  # Last 5 trades
            
            # Memory
            "last_memory": last_memory,
            
            # Risk constraints
            "risk_constraints": {
                "max_position_usd": self.settings.max_position_usd,
                "max_leverage": self.settings.max_leverage if self.settings.margin_enabled else 1,
                "min_order_usd": 10.0,
            },
        }

    def _build_trading_prompt(self, payload: Dict[str, Any]) -> str:
        """
        Build comprehensive trading prompt for the Trader agent.
        
        This prompt guides the AI to act as a professional cryptocurrency trader
        who combines macro trends with micro execution timing.
        """
        symbol = payload.get("symbol", "?")
        last_price = payload.get("last_price", 0.0)
        predictor = payload.get("predictor", {})
        available_usdt = payload.get("available_usdt", 0)
        current_position = payload.get("current_position", {})
        recent_trades = payload.get("recent_trades", [])
        last_memory = payload.get("last_memory")
        risk = payload.get("risk_constraints", {})
        timeframes = payload.get("timeframes", {})

        # Format timeframes
        tf_sections = []
        for tf, data in timeframes.items():
            change_pct = data.get("change_pct", 0.0) * 100
            support = data.get("support", 0.0)
            resistance = data.get("resistance", 0.0)
            bars = data.get("bars", [])
            
            recent_closes = [bar['c'] for bar in bars[-3:]] if bars else []
            tf_sections.append(f"""
{tf}:
- Change: {change_pct:+.2f}%
- Support: ${support:.2f} | Resistance: ${resistance:.2f}
- Recent closes: {recent_closes}
            """.strip())

        timeframes_str = "\n".join(tf_sections)

        # Format recent trades
        trades_str = "None"
        if recent_trades:
            trades_list = []
            for t in recent_trades[-3:]:
                side = t.get("side", "?").upper()
                price = t.get("price_usdt", 0)
                notional = t.get("notional_usdt", 0)
                trades_list.append(f"  - {side} @ ${price:.2f} (${notional:.2f})")
            trades_str = "\n".join(trades_list)

        # Format memory
        memory_str = last_memory if last_memory else "No previous actions recorded"

        # Format Predictor insight
        predictor_str = f"""
- Overall Trend: {predictor.get('overall_trend', '?')} (strength: {predictor.get('trend_strength', 0):.2f})
- Next 24H Bias: {predictor.get('next_24h_bias', '?')}
- Next 4H Bias: {predictor.get('next_4h_bias', '?')}
- Confidence: {predictor.get('confidence', 0):.2f}
- Key Support: {predictor.get('support_levels', [])[:2]}
- Key Resistance: {predictor.get('resistance_levels', [])[:2]}
- Reasoning: {predictor.get('reasoning', 'N/A')[:200]}
        """.strip()

        prompt = f"""You are a professional cryptocurrency trader executing real trades on OKX.

CURRENT SITUATION:
Symbol: {symbol}
Current Price: ${last_price:.2f}
Timestamp: {payload.get('timestamp', 'N/A')}

YOUR ACCOUNT:
- Available Capital: ${available_usdt:.2f} USDT
- Current Position: {current_position.get('amount', 0):.6f} units (${current_position.get('value_usdt', 0):.2f})
- Position P&L: {current_position.get('value_change_pct', 0):+.2f}%

MACRO ANALYSIS (From Predictor Agent):
{predictor_str}

MICRO TIMING (Real-time Price Action):
{timeframes_str}

RECENT TRADING ACTIVITY:
{trades_str}

YOUR MEMORY (From Last Decision):
{memory_str}

RISK CONSTRAINTS:
- Min Order: ${risk.get('min_order_usd', 10):.2f}
- Max Position: ${risk.get('max_position_usd', 100):.2f}
- Max Leverage: {risk.get('max_leverage', 1)}x
- Trading Mode: {'Spot Margin' if risk.get('max_leverage', 1) > 1 else 'Spot Only'}

YOUR TASK:
Decide whether to BUY, SELL, or HOLD {symbol} right now.

DECISION FRAMEWORK:
1. **Alignment Check**: Does short-term price action (1H/15m/5m) align with Predictor's macro trend?
2. **Entry/Exit Timing**: Are we near key support (for buy) or resistance (for sell)?
3. **Risk Assessment**: Given available capital and current position, what's the optimal size?
4. **Memory Integration**: Learn from your past decisions - did they work? Why or why not?
5. **Invalidation**: Define clear exit condition if trade goes wrong

DECISION RULES:
- **BUY**: Predictor bullish + price near support + 15m/5m showing strength
- **SELL**: Predictor bearish OR price hit resistance + have open position
- **HOLD**: No clear setup, low confidence, or waiting for better entry/exit

POSITION SIZING:
- New position: Start with ${risk.get('min_order_usd', 10):.2f} to ${risk.get('min_order_usd', 10) * 2:.2f}
- Add to winner: Scale up if position is profitable
- Cut loser: Exit quickly if invalidation triggered

OUTPUT FORMAT (strict JSON):
{{
  "action": "buy|sell|hold",
  "entry_price": null,
  "size_usd": 10.0,
  "leverage": 1,
  "stop_loss": 3900.0,
  "take_profit": 4200.0,
  "confidence": 0.75,
  "reason": "Clear bullish setup - 15m broke above resistance at $4100, Predictor confirms macro uptrend. Entering small position with tight stop.",
  "invalidation_condition": "Exit if price breaks below $4050 (recent support)",
  "memory_summary": "Bought BTC at $4100 on bullish breakout. Risk $50, targeting $4200. Will exit if support fails."
}}

CRITICAL NOTES:
- entry_price: null means market order (execute immediately at current price)
- stop_loss & take_profit: Can be null if you'll monitor manually
- memory_summary: Write THIS decision in 1-2 sentences for your FUTURE self
- confidence: 0.0-1.0 (only trade if ≥0.60, hold otherwise)
- reason: Explain your decision with specific price levels and logic

This is REAL MONEY. Be disciplined, follow the Predictor's macro view, and manage risk carefully."""

        return prompt

    def _schema(self) -> Dict[str, Any]:
        """JSON schema for Trader output."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["buy", "sell", "hold"],
                    "description": "Trading action to take"
                },
                "entry_price": {
                    "type": ["number", "null"],
                    "description": "Limit price (null = market order)"
                },
                "size_usd": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Position size in USD"
                },
                "leverage": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Leverage to use (1=spot)"
                },
                "stop_loss": {
                    "type": ["number", "null"],
                    "description": "Stop loss price level"
                },
                "take_profit": {
                    "type": ["number", "null"],
                    "description": "Take profit price level"
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Confidence in this decision"
                },
                "reason": {
                    "type": "string",
                    "description": "Explanation of the decision"
                },
                "invalidation_condition": {
                    "type": "string",
                    "description": "Condition to exit trade"
                },
                "memory_summary": {
                    "type": "string",
                    "description": "Summary for next decision"
                }
            },
            "required": [
                "action",
                "size_usd",
                "leverage",
                "confidence",
                "reason",
                "memory_summary"
            ]
        }

    def _parse_decision(
        self,
        symbol: str,
        predictor_insight: PredictorInsightData,
        response: Dict[str, Any],
    ) -> TraderDecisionData:
        """Parse AI response into TraderDecisionData."""
        now = datetime.now(timezone.utc)
        
        return TraderDecisionData(
            symbol=symbol,
            predictor_insight_id=predictor_insight.id,
            provider=self.settings.tactician_provider,
            model=self.settings.tactician_model,
            action=response.get("action", "hold").lower(),
            side="long",  # Spot trading (no shorts)
            entry_price=response.get("entry_price"),
            size_usd=float(response.get("size_usd", 0)),
            leverage=int(response.get("leverage", 1)),
            stop_loss=response.get("stop_loss"),
            take_profit=response.get("take_profit"),
            confidence=float(response.get("confidence", 0)),
            reason=response.get("reason"),
            invalidation_condition=response.get("invalidation_condition"),
            memory_summary=response.get("memory_summary"),
            status="pending",
            created_at=now,
            raw_output=response,
        )

    def _execute_trade(
        self,
        decision: TraderDecisionData,
        account_state: AccountState,
    ) -> Dict[str, Any]:
        """
        Execute the trade on OKX exchange.
        
        Returns execution result with success status and details.
        """
        try:
            client = get_trading_client()
            symbol = decision.symbol
            action = decision.action.lower()
            size_usd = decision.size_usd or 0
            
            if size_usd < 10.0:
                return {
                    "success": False,
                    "message": f"Order size ${size_usd:.2f} below minimum $10",
                }

            # Determine side and quantity
            if action == "buy":
                side = "buy"
                # Calculate quantity in base currency
                position = account_state.positions.get(symbol)
                last_price = decision.entry_price or account_state.positions.get(symbol, {}).get("last_price", 0)
                if not last_price:
                    return {"success": False, "message": "Cannot determine current price"}
                
                quantity = size_usd / last_price
                
            elif action == "sell":
                side = "sell"
                # Sell current position
                position = account_state.positions.get(symbol)
                if not position or position.amount <= 0:
                    return {"success": False, "message": "No position to sell"}
                
                quantity = position.amount
            else:
                return {"success": False, "message": f"Unknown action: {action}"}

            # Place market order
            order_type = "market"  # Use market orders for now
            result = client.place_spot_order(
                inst_id=symbol,
                side=side,
                ord_type=order_type,
                sz=str(quantity),
            )

            if result.get("code") == "0":
                return {
                    "success": True,
                    "message": f"{side.upper()} order placed successfully",
                    "order_id": result.get("data", [{}])[0].get("ordId"),
                    "quantity": quantity,
                    "okx_response": result,
                }
            else:
                return {
                    "success": False,
                    "message": f"OKX error: {result.get('msg', 'Unknown error')}",
                    "okx_response": result,
                }

        except Exception as e:
            import traceback
            return {
                "success": False,
                "message": f"Execution failed: {str(e)}",
                "traceback": traceback.format_exc(),
            }

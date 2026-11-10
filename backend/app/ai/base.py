from __future__ import annotations


from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Mapping


class BaseAIProvider(ABC):
    """Shared helpers for AI trading providers."""

    # Narrow context so prompts stay within model token limits.
    MAX_MARKET_SYMBOLS = 3
    MAX_TIMEFRAMES_DETAIL = 3
    TIMEFRAME_ORDER = ("1D", "4H", "1H", "15m", "5m", "1m")
    MIN_BASE_SIZE = {
        "BTC": 0.0001,
        "ETH": 0.001,
    }

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        self.api_key = api_key
        # Persist arbitrary provider configuration for subclasses.
        self.config: Dict[str, Any] = dict(kwargs)

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider identifier (e.g. 'gemini')."""

    @abstractmethod
    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a trading decision from the raw payload."""

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------
    def build_market_analysis_prompt(self, payload: Dict[str, Any]) -> str:
        """
        Render a prompt for pure market analysis WITHOUT any account information.
        Used by Strategist role to analyze market trends only.
        """
        market: Mapping[str, Any] = payload.get("market") or {}
        hints: Mapping[str, Any] = payload.get("hints") or {}
        symbols: list[str] = list(payload.get("symbols") or [])
        role = payload.get("role", "analyst")
        
        primary_symbol = symbols[0] if symbols else "?"
        task = hints.get("task", "Analyze the market trend.")
        
        # Format market data (only technical analysis, no account data)
        market_detail, _ = self._format_market_section(market)
        
        parts = [
            f"You are a {role}. {task}",
            "",
            "MARKET DATA:",
            market_detail,
            "",
            "Return your analysis in strict JSON format with these EXACT field names:",
            "{",
            '  "overall_trend": "up|down|neutral|bullish|bearish|mixed",',
            '  "next_days_trend": "up|down|neutral|bullish|bearish|mixed",',
            '  "next_24h_trend": "up|down|neutral|bullish|bearish|mixed",',
            '  "next_few_hours_trend": "up|down|neutral|bullish|bearish|mixed",',
            '  "confidence": 0.0-1.0,',
            '  "rationale": "brief explanation"',
            "}",
            "",
            "Focus ONLY on technical analysis - do NOT make trading decisions or consider account balance.",
        ]
        
        return "\n".join(parts)
    
    def build_trading_prompt(self, payload: Dict[str, Any], compact: bool = False) -> str:
        """Render a prompt that stays within model token limits."""

        market: Mapping[str, Any] = payload.get("market") or {}
        positions: Mapping[str, Any] = payload.get("positions") or {}
        balances: Mapping[str, Any] = payload.get("balances") or {}
        available_balances: Mapping[str, Any] = payload.get("available_balances") or {}
        risk: Mapping[str, Any] = payload.get("risk_constraints") or {}
        recent_decisions: Iterable[Dict[str, Any]] = payload.get("recent_decisions") or []
        recent_trades: Iterable[Dict[str, Any]] = payload.get("recent_trades") or []
        hints: Mapping[str, Any] = payload.get("hints") or {}
        symbols: list[str] = list(payload.get("symbols") or [])

        primary_symbol = symbols[0] if symbols else "?"
        target_interval = int(self._safe_float(hints.get("target_interval_minutes"), 10) or 10)

        usdt_total = self._safe_float(balances.get("USDT"), 0.0)
        usdt_available = self._safe_float(available_balances.get("USDT"), usdt_total)

        position_values = {
            symbol: self._safe_float(data.get("value_usdt"), 0.0)
            for symbol, data in positions.items()
        }
        current_exposure = sum(position_values.values())
        total_value = usdt_total + current_exposure

        max_position_usd = self._safe_float(risk.get("max_position_usd"), 100.0) or 100.0
        max_total_exposure_usd = self._safe_float(
            risk.get("max_total_exposure_usd"), max_position_usd * 2
        ) or (max_position_usd * 2)
        remaining_exposure = max_total_exposure_usd - current_exposure
        available_to_trade = max(0.0, min(usdt_available, remaining_exposure, max_position_usd))
        max_trade_now = max(0.0, min(max_position_usd, available_to_trade))

        max_leverage = int(self._safe_float(risk.get("max_leverage"), 1) or 1)
        margin_enabled = bool(risk.get("margin_enabled", False))

        holdings_str = self._format_holdings(position_values)
        market_detail, market_compact = self._format_market_section(market)
        trades_block = self._format_trades(recent_trades, compact)
        decisions_block = self._format_decisions(recent_decisions, compact)

        primary_market = market.get(primary_symbol, {}) if market else {}
        last_price = self._safe_float(primary_market.get("last_price"), 0.0)
        min_base_units = self.MIN_BASE_SIZE.get(primary_symbol.split("-")[0], 0.0)
        min_trade_usd = max(10.0, last_price * min_base_units) if last_price else 10.0
        min_trade_usd = round(min_trade_usd, 2)

        compact_prompt = self._render_compact_prompt(
            primary_symbol=primary_symbol,
            market_compact=market_compact,
            trades_block=trades_block,
            decisions_block=decisions_block,
            usdt_total=usdt_total,
            usdt_available=usdt_available,
            current_exposure=current_exposure,
            holdings_str=holdings_str,
            available_to_trade=available_to_trade,
            max_position_usd=max_position_usd,
            max_total_exposure_usd=max_total_exposure_usd,
            max_trade_now=max_trade_now,
            target_interval=target_interval,
            max_leverage=max_leverage,
            margin_enabled=margin_enabled,
            min_trade_usd=min_trade_usd,
        )

        detailed_prompt = self._render_detailed_prompt(
            primary_symbol=primary_symbol,
            market_detail=market_detail,
            trades_block=trades_block,
            decisions_block=decisions_block,
            usdt_total=usdt_total,
            usdt_available=usdt_available,
            current_exposure=current_exposure,
            total_value=total_value,
            holdings_str=holdings_str,
            available_to_trade=available_to_trade,
            max_position_usd=max_position_usd,
            max_total_exposure_usd=max_total_exposure_usd,
            max_trade_now=max_trade_now,
            target_interval=target_interval,
            max_leverage=max_leverage,
            margin_enabled=margin_enabled,
            min_trade_usd=min_trade_usd,
        )

        if compact:
            return compact_prompt

        # Gemini flash variants have much smaller context windows; auto fall back
        # to the compact prompt when the detailed prompt grows too large.
        if len(detailed_prompt) > 5500:
            return compact_prompt
        return detailed_prompt

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _format_holdings(position_values: Mapping[str, float]) -> str:
        if not position_values:
            return "None"
        sorted_positions = sorted(position_values.items(), key=lambda item: item[1], reverse=True)
        display = [f"{sym}: ${value:.2f}" for sym, value in sorted_positions[:5]]
        if len(sorted_positions) > 5:
            display.append("…")
        return ", ".join(display)

    def _format_market_section(self, market: Mapping[str, Any]) -> tuple[str, str]:
        if not market:
            return "- No market data", "- No market data"

        def pct(value: float) -> str:
            return f"{value:+.2f}%"

        detail_lines: list[str] = []
        compact_lines: list[str] = []

        market_items = list(market.items())
        trimmed_items = market_items[: self.MAX_MARKET_SYMBOLS]
        truncated = len(market_items) > self.MAX_MARKET_SYMBOLS

        for symbol, data in trimmed_items:
            last_price = self._safe_float(data.get("last_price"), 0.0)
            timeframes = data.get("timeframes") or {}

            detail_lines.append(f"{symbol} @ ${last_price:.2f}")
            timeframe_keys = [
                tf for tf in self.TIMEFRAME_ORDER[: self.MAX_TIMEFRAMES_DETAIL]
                if tf in timeframes
            ]
            for tf in timeframe_keys:
                tf_data = timeframes.get(tf) or {}
                change_pct = self._safe_float(tf_data.get("change_pct"), 0.0) * 100
                support = self._safe_float(tf_data.get("support"), 0.0)
                resistance = self._safe_float(tf_data.get("resistance"), 0.0)
                detail_lines.append(
                    f"  {tf}: {pct(change_pct)} | S:{support:.0f} R:{resistance:.0f}"
                )
            detail_lines.append("")

            change_1h = self._safe_float((timeframes.get("1H") or {}).get("change_pct"), 0.0) * 100
            change_1d = self._safe_float((timeframes.get("1D") or {}).get("change_pct"), 0.0) * 100
            compact_lines.append(
                f"- {symbol}: ${last_price:.2f} | 1H {pct(change_1h)} | 1D {pct(change_1d)}"
            )

        if truncated:
            detail_lines.append("…")
            compact_lines.append("- …")

        detail = "\n".join(line for line in detail_lines if line.strip())
        compact = "\n".join(compact_lines)
        return detail or "- No market data", compact or "- No market data"

    def _format_trades(self, trades: Iterable[Dict[str, Any]], compact: bool) -> str:
        trade_list = list(trades)
        limit = 2 if compact else 5
        lines = ["Recent Trades:"]

        if not trade_list:
            lines.append("- None")
            return "\n".join(lines)

        for trade in trade_list[:limit]:
            side = str(trade.get("side", "?")).upper()
            status = trade.get("status", "?")
            price = self._safe_float(trade.get("price"), 0.0)
            notional = self._safe_float(trade.get("notional_usdt"), 0.0)
            lines.append(f"- {side} @ ${price:.2f} (${notional:.2f}) [{status}]")
        if len(trade_list) > limit:
            lines.append("- …")
        return "\n".join(lines)

    def _format_decisions(self, decisions: Iterable[Dict[str, Any]], compact: bool) -> str:
        decision_list = list(decisions)
        limit = 2 if compact else 5
        lines = ["Recent Decisions:"]

        if not decision_list:
            lines.append("- None")
            return "\n".join(lines)

        for decision in decision_list[:limit]:
            confidence = self._safe_float(decision.get("confidence"), 0.0)
            actions = decision.get("actions") or []
            execution_results = decision.get("execution_results") or []

            for action in actions:
                symbol = action.get("symbol", "?")
                act = str(action.get("action", "?")).upper()
                size_usd = self._safe_float(action.get("size_usd"), 0.0)
                size_pct = self._safe_float(action.get("size_pct"), 0.0)
                size_display = (
                    f"${size_usd:.2f}" if size_usd > 0
                    else (f"{size_pct:.0f}%" if size_pct > 0 else "N/A")
                )

                match = next(
                    (
                        result
                        for result in execution_results
                        if result.get("symbol") == symbol
                        and str(result.get("action", "")).lower()
                        == str(action.get("action", "")).lower()
                    ),
                    None,
                )

                if match:
                    status = str(match.get("status", "unknown")).lower()
                    if status == "success":
                        price = self._safe_float(match.get("avg_price"), 0.0)
                        lines.append(
                            f"- {act} {size_display} @ ${price:.2f} → ✓ (conf {confidence:.0%})"
                        )
                    else:
                        reason = match.get("message", "Failed")
                        lines.append(
                            f"- {act} {size_display} → ✗ {reason} (conf {confidence:.0%})"
                        )
                else:
                    lines.append(
                        f"- {act} {size_display} → {'OK' if act == 'HOLD' else '?'} (conf {confidence:.0%})"
                    )

        if len(decision_list) > limit:
            lines.append("- …")
        return "\n".join(lines)

    def _render_compact_prompt(
        self,
        *,
        primary_symbol: str,
        market_compact: str,
        trades_block: str,
        decisions_block: str,
        usdt_total: float,
        usdt_available: float,
        current_exposure: float,
        holdings_str: str,
        available_to_trade: float,
        max_position_usd: float,
        max_total_exposure_usd: float,
        max_trade_now: float,
        target_interval: int,
        max_leverage: int,
        margin_enabled: bool,
        min_trade_usd: float,
    ) -> str:
        leverage_hint = (
            "spot only"
            if (not margin_enabled or max_leverage <= 1)
            else f"up to {max_leverage}x"
        )
        json_template = (
            "{\n"
            '  "actions": [{\n'
            f'    "symbol": "{primary_symbol}",\n'
            '    "action": "buy|sell|hold",\n'
            '    "size_usd": 0.0,\n'
            f'    "leverage": {max(1, max_leverage)},\n'
            '    "reason": ""\n'
            '  }],\n'
            '  "confidence": 0.0,\n'
            '  "rationale": "",\n'
            f'  "next_decision_in_minutes": {target_interval}\n'
            "}"
        )

        if max_trade_now >= min_trade_usd:
            size_range = f"${min_trade_usd:.2f} to ${max_trade_now:.2f} (0 when holding)"
        else:
            size_range = "Hold (insufficient tradable balance)"

        parts = [
            "You are a crypto trading AI. Return strict JSON only (no prose).",
            "",
            "ACCOUNT:",
            f"- Free USDT: ${usdt_available:.2f}",
            f"- Total USDT: ${usdt_total:.2f}",
            f"- Current exposure: ${current_exposure:.2f}",
            f"- Holdings: {holdings_str}",
            "",
            "MARKET SNAPSHOT:",
            market_compact,
            "",
            "CONTEXT:",
            trades_block,
            decisions_block,
            "",
            "CONSTRAINTS:",
            f"- Minimum order size ${min_trade_usd:.2f}",
            f"- Max per trade ${max_position_usd:.2f}",
            f"- Available to trade now ${available_to_trade:.2f}",
            f"- Stay within exposure cap ${max_total_exposure_usd:.2f}",
            f"- Leverage {leverage_hint}",
            "",
            "OUTPUT JSON TEMPLATE:",
            json_template,
            "",
            "Rules:",
            f"- size_usd: {size_range}",
            f"- confidence: 0.0-1.0 (≥0.55 to trade)",
            "- reason: brief justification",
        ]
        return "\n".join(parts)

    def _render_detailed_prompt(
        self,
        *,
        primary_symbol: str,
        market_detail: str,
        trades_block: str,
        decisions_block: str,
        usdt_total: float,
        usdt_available: float,
        current_exposure: float,
        total_value: float,
        holdings_str: str,
        available_to_trade: float,
        max_position_usd: float,
        max_total_exposure_usd: float,
        max_trade_now: float,
        target_interval: int,
        max_leverage: int,
        margin_enabled: bool,
        min_trade_usd: float,
    ) -> str:
        leverage_hint = (
            "spot only"
            if (not margin_enabled or max_leverage <= 1)
            else f"margin allowed up to {max_leverage}x"
        )
        trade_band = (
            f"${min_trade_usd:.2f} - ${max_trade_now:.2f}"
            if max_trade_now >= min_trade_usd
            else "Trade blocked (hold unless confidence very high)"
        )
        size_instruction = (
            f"${min_trade_usd:.2f} to ${max_trade_now:.2f} (0 when holding)"
            if max_trade_now >= min_trade_usd
            else "Hold (insufficient tradable balance)"
        )
        json_template = (
            "{\n"
            '  "actions": [{\n'
            f'    "symbol": "{primary_symbol}",\n'
            '    "action": "buy|sell|hold",\n'
            '    "size_usd": 0.0,\n'
            f'    "leverage": {max(1, max_leverage)},\n'
            '    "reason": ""\n'
            '  }],\n'
            '  "confidence": 0.0,\n'
            '  "rationale": "",\n'
            f'  "next_decision_in_minutes": {target_interval}\n'
            "}"
        )

        parts = [
            "You are a crypto trading AI. Make one decision for the highlighted symbol.",
            "",
            "ACCOUNT OVERVIEW:",
            f"- Free USDT: ${usdt_available:.2f}",
            f"- Total USDT (wallet + frozen): ${usdt_total:.2f}",
            f"- Current exposure: ${current_exposure:.2f}",
            f"- Portfolio value: ${total_value:.2f}",
            f"- Holdings: {holdings_str}",
            "",
            "RISK LIMITS:",
            f"- Minimum order size ${min_trade_usd:.2f} (OKX)",
            f"- Max per trade {max_position_usd:.2f}",
            f"- Exposure cap {max_total_exposure_usd:.2f}",
            f"- Available to deploy now {available_to_trade:.2f}",
            f"- Leverage: {leverage_hint}",
            f"- Target next decision in {target_interval} minutes",
            "",
            "TRADE SIZE GUIDE:",
            f"- Recommended band: {trade_band}",
            "- Confidence ≥0.80 → size near upper band",
            "- Confidence 0.60-0.79 → mid band",
            "- Confidence <0.55 → hold",
            "",
            "MARKET DETAIL:",
            market_detail,
            "",
            "RECENT CONTEXT:",
            trades_block,
            decisions_block,
            "",
            "OUTPUT JSON TEMPLATE:",
            json_template,
            "",
            "Instructions:",
            f"- size_usd: {size_instruction}",
            "- Provide a short reason referencing price action/levels",
            "- Output must be valid JSON, no markdown or prose",
        ]
        return "\n".join(parts)

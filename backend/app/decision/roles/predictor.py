from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from ..data_services import DataCollector
from ..models import PredictorInsightData
from ...config import get_settings
from ...ai.factory import AIProviderFactory
from ...db.repository import (
    save_predictor_insight,
    get_latest_predictor_insight,
    save_decision,
)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (UTC if naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class PredictorRole:
    """
    Predictor Agent: Macro trend analysis using 1D and 4H timeframes.
    
    The Predictor analyzes long-term price action, identifies key support/resistance levels,
    and provides directional bias for the next 24 hours and 4 hours. This insight guides
    the Trader agent's execution decisions.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.collector = DataCollector()

    def run(self, symbol: str, *, force: bool = False) -> PredictorInsightData:
        """
        Generate a market prediction for the given symbol.
        
        Args:
            symbol: Trading pair (e.g., 'BTC-USDT', 'ETH-USDT')
            force: If True, bypass cache and generate fresh insight
            
        Returns:
            PredictorInsightData with trend analysis and key levels
        """
        self.settings = get_settings()
        existing = get_latest_predictor_insight(symbol)
        now = datetime.now(timezone.utc)
        
        # Return cached insight if still valid
        if existing and not force:
            expires_at = _ensure_aware(existing.expires_at)
            if expires_at and expires_at > now:
                print(f"[Predictor] Using cached insight for {symbol} (expires {expires_at.isoformat()})")
                return PredictorInsightData(
                    id=existing.id,
                    symbol=existing.symbol,
                    provider=existing.provider,
                    model=existing.model,
                    generated_at=existing.generated_at,
                    expires_at=existing.expires_at,
                    overall_trend=existing.overall_trend,
                    trend_strength=existing.trend_strength,
                    next_24h_bias=existing.next_24h_bias,
                    next_4h_bias=existing.next_4h_bias,
                    support_levels=existing.support_levels or [],
                    resistance_levels=existing.resistance_levels or [],
                    confidence=existing.confidence,
                    reasoning=existing.reasoning,
                    news_context=existing.news_context,
                    raw_output=existing.raw_output,
                )

        print(f"[Predictor] ▶ Generating fresh insight for {symbol} (provider={self.settings.strategist_provider}, model={self.settings.strategist_model})")
        
        # Collect market data (1D and 4H focus)
        market_env = self.collector.build_market_envelope([symbol])
        prompt_payload = self._build_prompt_payload(symbol, market_env)

        # Call AI provider
        provider = self._make_provider(
            self.settings.strategist_provider,
            self.settings.strategist_model
        )
        prompt = self._build_prediction_prompt(prompt_payload)
        print(f"[Predictor] Prompt length: {len(prompt)} chars")
        
        response = provider.invoke_json(prompt, schema_hint=self._schema())
        print(f"[Predictor] Response keys: {list(response.keys())}")

        # Parse and save insight
        insight = self._parse_insight(symbol, response)
        expires_at = now + timedelta(hours=4)  # Cache for 4 hours

        record = save_predictor_insight(
            symbol=symbol,
            provider=insight.provider,
            model=insight.model,
            overall_trend=insight.overall_trend,
            trend_strength=insight.trend_strength,
            next_24h_bias=insight.next_24h_bias,
            next_4h_bias=insight.next_4h_bias,
            support_levels=insight.support_levels,
            resistance_levels=insight.resistance_levels,
            confidence=insight.confidence,
            reasoning=insight.reasoning,
            news_context=insight.news_context,
            raw_output=insight.raw_output,
            context_snapshot=prompt_payload,
            expires_at=expires_at,
        )

        print(f"[Predictor] ✓ Insight saved: trend={insight.overall_trend}, strength={insight.trend_strength:.2f}, 24h={insight.next_24h_bias}, 4h={insight.next_4h_bias}, conf={insight.confidence:.2f}")
        
        # Save decision for audit trail
        save_decision(
            provider=insight.provider,
            ai_input=prompt_payload,
            ai_output=insight.raw_output,
            execution_results=None,
            role="predictor",
        )

        insight.id = record.id
        return insight

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
        raise ValueError(f"Unsupported predictor provider: {provider}")

    def _build_prompt_payload(self, symbol: str, market_env) -> Dict[str, Any]:
        """Build payload for AI prompt with 1D/4H market data."""
        snapshot = market_env.snapshots.get(symbol)
        if not snapshot:
            raise ValueError(f"No market data for {symbol}")

        # Focus on 1D and 4H timeframes for macro analysis
        timeframes_data = {}
        for tf in ["1D", "4H"]:
            if tf in snapshot.timeframes:
                tf_data = snapshot.timeframes[tf]
                timeframes_data[tf] = {
                    "change_pct": tf_data.change_pct,
                    "support": tf_data.support,
                    "resistance": tf_data.resistance,
                    "bars": tf_data.bars[-20:],  # Last 20 candles for trend analysis
                }

        return {
            "role": "predictor",
            "timestamp": market_env.retrieved_at.isoformat(),
            "symbol": symbol,
            "last_price": snapshot.last_price,
            "timeframes": timeframes_data,
        }

    def _build_prediction_prompt(self, payload: Dict[str, Any]) -> str:
        """
        Build a professional market analysis prompt for cryptocurrency prediction.
        
        The prompt guides the AI to act as an expert cryptocurrency analyst,
        analyzing price action, market structure, and trend momentum.
        """
        symbol = payload.get("symbol", "?")
        last_price = payload.get("last_price", 0.0)
        timeframes = payload.get("timeframes", {})

        # Format timeframe data
        tf_sections = []
        for tf, data in timeframes.items():
            change_pct = data.get("change_pct", 0.0) * 100
            support = data.get("support", 0.0)
            resistance = data.get("resistance", 0.0)
            bars = data.get("bars", [])
            
            tf_sections.append(f"""
{tf} Timeframe:
- Price Change: {change_pct:+.2f}%
- Support Level: ${support:.2f}
- Resistance Level: ${resistance:.2f}
- Recent Price Action: {len(bars)} candles available
  Last 3 closes: {[bar['c'] for bar in bars[-3:]]}
            """.strip())

        timeframes_str = "\n\n".join(tf_sections)

        prompt = f"""You are a professional cryptocurrency market analyst specializing in technical analysis.

Your task is to analyze {symbol} and provide a comprehensive market outlook.

CURRENT MARKET DATA:
Symbol: {symbol}
Current Price: ${last_price:.2f}
Timestamp: {payload.get('timestamp', 'N/A')}

TIMEFRAME ANALYSIS:
{timeframes_str}

YOUR ANALYSIS MUST INCLUDE:

1. **Overall Trend Assessment** (bullish/bearish/neutral):
   - Analyze the daily (1D) price structure
   - Identify if we're in an uptrend, downtrend, or range
   - Consider trend strength based on momentum and price action

2. **Short-term Bias** (next 24 hours and next 4 hours):
   - Direction: up/down/sideways
   - Based on 4H timeframe momentum
   - Consider recent price rejections at key levels

3. **Key Levels Identification**:
   - List 2-3 critical support levels below current price
   - List 2-3 critical resistance levels above current price
   - These levels should guide entry/exit decisions

4. **Confidence Assessment** (0.0 to 1.0):
   - How certain are you about this analysis?
   - Higher confidence when trend is clear and levels are well-defined
   - Lower confidence in choppy/ranging markets

5. **Reasoning**:
   - Explain WHY you reached these conclusions
   - Reference specific price action, breakouts, or rejections
   - Be concise but specific (3-5 sentences)

IMPORTANT GUIDELINES:
- Focus on PRICE ACTION and MARKET STRUCTURE, not external news
- Be objective - avoid emotional language like "moon" or "crash"
- Support/resistance levels should be actionable (not too far from current price)
- Trend strength: 0.0-0.3 (weak), 0.4-0.6 (moderate), 0.7-1.0 (strong)
- If market is ranging/choppy, use "neutral" trend and "sideways" bias

OUTPUT FORMAT (strict JSON only):
{{
  "overall_trend": "bullish|bearish|neutral",
  "trend_strength": 0.75,
  "next_24h_bias": "up|down|sideways",
  "next_4h_bias": "up|down|sideways",
  "support_levels": [4000.0, 3950.0, 3900.0],
  "resistance_levels": [4200.0, 4250.0, 4300.0],
  "confidence": 0.80,
  "reasoning": "BTC broke above $4100 resistance on strong volume in the 4H timeframe, now testing $4200. Daily trend remains bullish with higher highs and higher lows. Momentum favors continuation toward $4250-4300 resistance zone."
}}

Remember: Your analysis will guide real trading decisions. Be precise, objective, and professional."""

        return prompt

    def _schema(self) -> Dict[str, Any]:
        """JSON schema for Predictor output."""
        return {
            "type": "object",
            "properties": {
                "overall_trend": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                    "description": "Overall market trend direction"
                },
                "trend_strength": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Strength of the trend (0.0=weak, 1.0=very strong)"
                },
                "next_24h_bias": {
                    "type": "string",
                    "enum": ["up", "down", "sideways"],
                    "description": "Expected price direction in next 24 hours"
                },
                "next_4h_bias": {
                    "type": "string",
                    "enum": ["up", "down", "sideways"],
                    "description": "Expected price direction in next 4 hours"
                },
                "support_levels": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Key support price levels"
                },
                "resistance_levels": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Key resistance price levels"
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Confidence in this analysis"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Explanation of the analysis"
                }
            },
            "required": [
                "overall_trend",
                "trend_strength",
                "next_24h_bias",
                "next_4h_bias",
                "support_levels",
                "resistance_levels",
                "confidence",
                "reasoning"
            ]
        }

    def _parse_insight(self, symbol: str, response: Dict[str, Any]) -> PredictorInsightData:
        """Parse AI response into PredictorInsightData."""
        now = datetime.now(timezone.utc)
        
        return PredictorInsightData(
            symbol=symbol,
            provider=self.settings.strategist_provider,
            model=self.settings.strategist_model,
            generated_at=now,
            overall_trend=response.get("overall_trend", "neutral"),
            trend_strength=float(response.get("trend_strength", 0.5)),
            next_24h_bias=response.get("next_24h_bias", "sideways"),
            next_4h_bias=response.get("next_4h_bias", "sideways"),
            support_levels=response.get("support_levels", []),
            resistance_levels=response.get("resistance_levels", []),
            confidence=float(response.get("confidence", 0.5)),
            reasoning=response.get("reasoning"),
            news_context=response.get("news_context"),  # Future: add news
            raw_output=response,
        )

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from ..data_services import DataCollector
from ..models import StrategistContextData, StrategistInsightData
from ...config import get_settings
from ...ai.factory import AIProviderFactory
from ...db.repository import (
    save_strategist_insight,
    get_latest_strategist_insight,
    save_decision,
)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (UTC if naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class StrategistRole:
    """Generates high-level market insights per symbol using long-term data."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.collector = DataCollector()

    def run(self, symbol: str, *, force: bool = False) -> StrategistInsightData:
        self.settings = get_settings()
        existing = get_latest_strategist_insight(symbol)
        now = datetime.now(timezone.utc)
        if existing and not force:
            # Ensure expires_at is timezone-aware for comparison
            expires_at = _ensure_aware(existing.expires_at)
            
            if expires_at and expires_at > now:
                return StrategistInsightData(
                    id=existing.id,
                    symbol=existing.symbol,
                    provider=existing.provider,
                    model=existing.model,
                    generated_at=existing.generated_at,
                    overall_trend=existing.overall_trend,
                    next_days_trend=existing.next_days_trend,
                    next_24h_trend=existing.next_24h_trend,
                    next_few_hours_trend=existing.next_few_hours_trend,
                    confidence=existing.confidence,
                    rationale=existing.rationale,
                    expires_at=existing.expires_at,
                    raw_output=existing.raw_output,
                )

        print(f"[Strategist] ▶ run(symbol={symbol}, force={force}) provider={self.settings.strategist_provider}, model={self.settings.strategist_model}")
        context = self.collector.build_strategist_context(
            self.settings.symbols,
            force_refresh=force,
        )
        prompt_payload = self._build_prompt_payload(symbol, context)

        provider = self._make_provider(
            self.settings.strategist_provider, self.settings.strategist_model
        )
        prompt = provider.build_market_analysis_prompt(prompt_payload)
        print(f"[Strategist] Built prompt for {symbol}, length={len(prompt)}")
        response = provider.invoke_json(prompt, schema_hint=self._schema(), compact=True)
        print(f"[Strategist] Raw response keys: {list(response.keys())}")

        insight = self._parse_insight(symbol, response)
        
        # Ensure generated_at is timezone-aware before arithmetic
        generated_at = _ensure_aware(insight.generated_at)
        expires_at = generated_at + timedelta(hours=4)

        record = save_strategist_insight(
            symbol=symbol,
            provider=insight.provider,
            model=insight.model,
            overall_trend=insight.overall_trend,
            next_days_trend=insight.next_days_trend,
            next_24h_trend=insight.next_24h_trend,
            next_few_hours_trend=insight.next_few_hours_trend,
            confidence=insight.confidence,
            rationale=insight.rationale,
            raw_output=insight.raw_output,
            context_snapshot=prompt_payload,
            expires_at=expires_at,
        )

        print(f"[Strategist] ✓ Insight for {symbol}: overall={insight.overall_trend}, 24h={insight.next_24h_trend}, few_hours={insight.next_few_hours_trend}, conf={insight.confidence:.2f}")
        save_decision(
            provider=insight.provider,
            ai_input=prompt_payload,
            ai_output=insight.raw_output,
            execution_results=None,
            role="strategist",
        )

        return StrategistInsightData(
            id=record.id,
            symbol=symbol,
            provider=record.provider,
            model=record.model,
            generated_at=record.generated_at,
            overall_trend=record.overall_trend,
            next_days_trend=record.next_days_trend,
            next_24h_trend=record.next_24h_trend,
            next_few_hours_trend=record.next_few_hours_trend,
            confidence=record.confidence,
            rationale=record.rationale,
            expires_at=record.expires_at,
            raw_output=record.raw_output,
        )

    def _make_provider(self, provider_name: str, model: str | None):
        api_key = self._resolve_api_key(provider_name)
        return AIProviderFactory.create(provider_name, api_key=api_key, model=model)

    def _resolve_api_key(self, provider: str) -> str | None:
        if provider == "deepseek":
            return self.settings.deepseek_api_key
        if provider == "gemini":
            return self.settings.gemini_api_key
        raise ValueError(f"Unsupported strategist provider: {provider}")

    def _build_prompt_payload(
        self, symbol: str, context: StrategistContextData
    ) -> Dict[str, Any]:
        snapshot = context.market.snapshots.get(symbol)
        if snapshot is None:
            raise ValueError(f"No market snapshot available for symbol {symbol}")
        
        # Strategist should ONLY see market data (1D and 4H timeframes)
        # NO balance, NO positions, NO trading decisions
        return {
            "role": "strategist",
            "timestamp": context.collected_at.isoformat(),
            "symbols": [symbol],
            "market": {
                symbol: {
                    "last_price": snapshot.last_price,
                    "change_1m_pct": snapshot.change_1m_pct,
                    "timeframes": {
                        tf: {
                            "change_pct": data.change_pct,
                            "support": data.support,
                            "resistance": data.resistance,
                            "bars": data.bars,
                        }
                        for tf, data in snapshot.timeframes.items()
                        if tf in ("1D", "4H")  # Only 1D and 4H data for strategist
                    },
                }
            },
            "hints": {
                "task": "You are a market analyst. Analyze ONLY the market trend using 1D and 4H candlestick data. Do NOT make trading decisions or consider account balance. Provide trend analysis for: overall, next few days, next 24 hours, and next few hours. Each trend should be a simple string: 'up', 'down', 'neutral', 'bullish', 'bearish', or 'mixed'.",
            },
        }

    def _schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "overall_trend": {
                    "type": "string",
                    "description": "Overall market trend: 'up', 'down', 'neutral', 'bullish', 'bearish', or 'mixed'",
                },
                "next_days_trend": {
                    "type": "string",
                    "description": "Trend for next few days: 'up', 'down', 'neutral', 'bullish', 'bearish', or 'mixed'",
                },
                "next_24h_trend": {
                    "type": "string",
                    "description": "Trend for next 24 hours: 'up', 'down', 'neutral', 'bullish', 'bearish', or 'mixed'",
                },
                "next_few_hours_trend": {
                    "type": "string",
                    "description": "Trend for next few hours: 'up', 'down', 'neutral', 'bullish', 'bearish', or 'mixed'",
                },
                "confidence": {"type": "number", "description": "Confidence level (0-1)"},
                "rationale": {
                    "type": "string",
                    "description": "Brief explanation of the trend analysis",
                },
            },
            "required": [
                "overall_trend",
                "next_days_trend",
                "next_24h_trend",
                "next_few_hours_trend",
                "confidence",
            ],
        }

    def _parse_insight(self, symbol: str, response: Dict[str, Any]) -> StrategistInsightData:
        now = datetime.now(timezone.utc)
        provider_name = self.settings.strategist_provider
        model = self.settings.strategist_model
        return StrategistInsightData(
            id=None,
            symbol=symbol,
            provider=provider_name,
            model=model,
            generated_at=now,
            overall_trend=response.get("overall_trend", "unknown"),
            next_days_trend=response.get("next_days_trend", "unknown"),
            next_24h_trend=response.get("next_24h_trend", "unknown"),
            next_few_hours_trend=response.get("next_few_hours_trend", "unknown"),
            confidence=float(response.get("confidence", 0) or 0.0),
            rationale=response.get("rationale"),
            expires_at=now + timedelta(hours=4),
            raw_output=response,
        )

